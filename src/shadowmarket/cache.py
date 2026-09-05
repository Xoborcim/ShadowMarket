"""In-memory cache so on_message never touches SQLite."""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, datetime, timezone

from shadowmarket.config import VOLUME_COOLDOWN_SECONDS
from shadowmarket.models import BountyRecord


class MarketCache:
    """Nested lookup structure loaded on startup and mutated by commands/tasks."""

    def __init__(self) -> None:
        self.stocks: dict[str, set[str]] = defaultdict(set)
        self.bounties: dict[str, dict[int, BountyRecord]] = defaultdict(dict)
        self.bounties_by_keyword: dict[str, dict[str, list[int]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self.usage_buffer: dict[tuple[str, str], int] = defaultdict(int)
        self.period_volume: dict[tuple[str, str], int] = defaultdict(int)
        self.volume_cooldown: dict[tuple[str, str, str], float] = {}
        self.daily_messages: dict[str, int] = defaultdict(int)
        self.daily_date: dict[str, date] = {}

    def load_stocks(self, guild_id: str, keywords: list[str]) -> None:
        self.stocks[guild_id] = set(keywords)

    def add_stock(self, guild_id: str, keyword: str) -> None:
        self.stocks[guild_id].add(keyword)

    def load_bounties(self, records: list[BountyRecord]) -> None:
        self.bounties.clear()
        self.bounties_by_keyword.clear()
        for record in records:
            self.add_bounty(record)

    def add_bounty(self, record: BountyRecord) -> None:
        self.bounties[record.guild_id][record.bounty_id] = record
        ids = self.bounties_by_keyword[record.guild_id][record.keyword]
        if record.bounty_id not in ids:
            ids.append(record.bounty_id)

    def remove_bounty(self, guild_id: str, bounty_id: int) -> BountyRecord | None:
        record = self.bounties[guild_id].pop(bounty_id, None)
        if record is None:
            return None
        ids = self.bounties_by_keyword[guild_id][record.keyword]
        if bounty_id in ids:
            ids.remove(bounty_id)
        if not ids:
            self.bounties_by_keyword[guild_id].pop(record.keyword, None)
        return record

    def active_bounties_for_keyword(self, guild_id: str, keyword: str) -> list[BountyRecord]:
        ids = self.bounties_by_keyword[guild_id].get(keyword, [])
        return [self.bounties[guild_id][bounty_id] for bounty_id in ids if bounty_id in self.bounties[guild_id]]

    def public_bounty_count(self, guild_id: str) -> int:
        return sum(1 for b in self.bounties[guild_id].values() if not b.is_stealth)

    def stealth_bounties_from(self, guild_id: str, placer_id: str, target_id: str) -> list[BountyRecord]:
        return [
            bounty
            for bounty in self.bounties[guild_id].values()
            if bounty.is_stealth and bounty.placer_id == placer_id and bounty.target_id == target_id
        ]

    def matching_claimable(
        self, guild_id: str, keyword: str, speaker_id: str
    ) -> list[BountyRecord]:
        now = datetime.now(timezone.utc)
        matched: list[BountyRecord] = []
        for bounty in self.active_bounties_for_keyword(guild_id, keyword):
            if bounty.placer_id == speaker_id:
                continue
            if bounty.expires_at <= now:
                continue
            if bounty.target_id is None or bounty.target_id == speaker_id:
                matched.append(bounty)
        return matched

    def hit_stock(self, guild_id: str, user_id: str, keyword: str, now: float | None = None) -> bool:
        """Count at most one volume impact per user/keyword every 60 seconds."""
        if keyword not in self.stocks.get(guild_id, ()):
            return False
        stamp = now if now is not None else time.monotonic()
        key = (guild_id, user_id, keyword)
        last = self.volume_cooldown.get(key)
        if last is not None and stamp - last < VOLUME_COOLDOWN_SECONDS:
            return False
        self.volume_cooldown[key] = stamp
        self.usage_buffer[(guild_id, keyword)] += 1
        self.period_volume[(guild_id, keyword)] += 1
        return True

    def drain_usage_buffer(self) -> dict[tuple[str, str], int]:
        pending = dict(self.usage_buffer)
        self.usage_buffer.clear()
        return pending

    def drain_period_volume(self) -> dict[tuple[str, str], int]:
        pending = dict(self.period_volume)
        self.period_volume.clear()
        return pending

    def record_message(self, guild_id: str, today: date | None = None) -> None:
        today = today or date.today()
        if self.daily_date.get(guild_id) != today:
            self.daily_date[guild_id] = today
            self.daily_messages[guild_id] = 0
        self.daily_messages[guild_id] += 1

    def is_dead_server(self, guild_id: str, threshold: int) -> bool:
        return self.daily_messages.get(guild_id, 0) < threshold

    def prune_cooldowns(self, now: float | None = None) -> None:
        stamp = now if now is not None else time.monotonic()
        stale = [
            key
            for key, last in self.volume_cooldown.items()
            if stamp - last > VOLUME_COOLDOWN_SECONDS * 5
        ]
        for key in stale:
            del self.volume_cooldown[key]
