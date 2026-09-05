"""In-memory server analytics. on_message never waits on SQLite."""

from __future__ import annotations

import re
import string
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from shadowmarket.stopwords import STOP_WORDS
from shadowmarket.tokenizer import CUSTOM_EMOJI_RE

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
MENTION_RE = re.compile(r"<@!?\d+>|<@&\d+>|<#\d+>")
EMOJI_MARKUP_RE = re.compile(r"<a?:[A-Za-z0-9_]+:\d+>")
WORD_RE = re.compile(r"[a-z0-9][a-z0-9'_-]{1,}", re.IGNORECASE)


@dataclass(slots=True)
class ChatEvent:
    guild_id: str
    channel_id: str
    user_id: str
    content: str
    created_at: datetime
    mentioned_ids: list[str]
    mention_everyone: bool
    desktop: bool = False
    mobile: bool = False
    web: bool = False


@dataclass
class AnalyticsFlush:
    messages: dict[tuple[str, str], int]
    channels: dict[tuple[str, str], int]
    ping_sent: dict[tuple[str, str], int]
    ping_received: dict[tuple[str, str], int]
    everyone: dict[tuple[str, str], int]
    hours: dict[tuple[str, int, int], int]
    words: dict[tuple[str, str], int]
    last_message: dict[tuple[str, str], str]
    longest: dict[str, tuple[str, str, int]]
    voice_minutes: dict[tuple[str, str], float]
    devices: dict[tuple[str, str], tuple[int, int, int]]


def lexical_words(content: str) -> list[str]:
    """Meaningful word occurrences with stop words and markup stripped."""
    text = URL_RE.sub(" ", content)
    text = EMOJI_MARKUP_RE.sub(" ", text)
    text = MENTION_RE.sub(" ", text)
    text = CUSTOM_EMOJI_RE.sub(" ", text)
    words: list[str] = []
    for match in WORD_RE.finditer(text.lower()):
        token = match.group(0).strip("'_-")
        if len(token) < 3 or token in STOP_WORDS or token.isdigit():
            continue
        words.append(token)
    return words


def longest_raw_token(content: str) -> str | None:
    """Longest whitespace token (keyboard-smash record), ignoring URLs and mentions."""
    text = URL_RE.sub(" ", content)
    text = MENTION_RE.sub(" ", text)
    text = EMOJI_MARKUP_RE.sub(" ", text)
    best: str | None = None
    for raw in text.split():
        token = raw.strip(string.punctuation + "“”‘’\"'")
        if len(token) < 8:
            continue
        if best is None or len(token) > len(best):
            best = token
    return best


class AnalyticsBuffer:
    def __init__(self) -> None:
        self.messages: dict[tuple[str, str], int] = defaultdict(int)
        self.channels: dict[tuple[str, str], int] = defaultdict(int)
        self.ping_sent: dict[tuple[str, str], int] = defaultdict(int)
        self.ping_received: dict[tuple[str, str], int] = defaultdict(int)
        self.everyone: dict[tuple[str, str], int] = defaultdict(int)
        self.hours: dict[tuple[str, int, int], int] = defaultdict(int)
        self.words: dict[tuple[str, str], int] = defaultdict(int)
        self.last_message: dict[tuple[str, str], str] = {}
        self.longest: dict[str, tuple[str, str, int]] = {}
        self.voice_minutes: dict[tuple[str, str], float] = defaultdict(float)
        self.devices: dict[tuple[str, str], tuple[int, int, int]] = {}
        self.voice_joined: dict[tuple[str, str], float] = {}
        self.invite_uses: dict[str, dict[str, tuple[str | None, int]]] = {}
        self.record_floor: dict[str, int] = {}

    def ingest(self, event: ChatEvent) -> None:
        g, u, c = event.guild_id, event.user_id, event.channel_id
        self.messages[(g, u)] += 1
        self.channels[(g, c)] += 1
        stamp = event.created_at.astimezone(timezone.utc).replace(tzinfo=None).isoformat(
            timespec="seconds"
        )
        previous = self.last_message.get((g, u))
        if previous is None or stamp > previous:
            self.last_message[(g, u)] = stamp

        weekday = event.created_at.astimezone(timezone.utc).weekday()
        hour = event.created_at.astimezone(timezone.utc).hour
        self.hours[(g, weekday, hour)] += 1

        mentions = [mid for mid in event.mentioned_ids if mid != u]
        if mentions:
            self.ping_sent[(g, u)] += len(mentions)
            for mid in mentions:
                self.ping_received[(g, mid)] += 1
        if event.mention_everyone:
            self.everyone[(g, u)] += 1

        for word in lexical_words(event.content):
            self.words[(g, word)] += 1

        token = longest_raw_token(event.content)
        if token:
            current = self.longest.get(g)
            best_len = current[2] if current else self.record_floor.get(g, 0)
            if len(token) > best_len:
                self.longest[g] = (u, token, len(token))

        if event.desktop or event.mobile or event.web:
            self.devices[(g, u)] = (
                int(event.desktop),
                int(event.mobile),
                int(event.web),
            )

    def start_voice(self, guild_id: str, user_id: str, now: float | None = None) -> None:
        key = (guild_id, user_id)
        if key not in self.voice_joined:
            self.voice_joined[key] = now if now is not None else time.monotonic()

    def stop_voice(self, guild_id: str, user_id: str, now: float | None = None) -> float:
        started = self.voice_joined.pop((guild_id, user_id), None)
        if started is None:
            return 0.0
        elapsed = (now if now is not None else time.monotonic()) - started
        minutes = max(0.0, elapsed / 60.0)
        if minutes > 0:
            self.voice_minutes[(guild_id, user_id)] += minutes
        return minutes

    def drain(self) -> AnalyticsFlush:
        flush = AnalyticsFlush(
            messages=dict(self.messages),
            channels=dict(self.channels),
            ping_sent=dict(self.ping_sent),
            ping_received=dict(self.ping_received),
            everyone=dict(self.everyone),
            hours=dict(self.hours),
            words=dict(self.words),
            last_message=dict(self.last_message),
            longest=dict(self.longest),
            voice_minutes=dict(self.voice_minutes),
            devices=dict(self.devices),
        )
        self.messages.clear()
        self.channels.clear()
        self.ping_sent.clear()
        self.ping_received.clear()
        self.everyone.clear()
        self.hours.clear()
        self.words.clear()
        self.last_message.clear()
        self.longest.clear()
        self.voice_minutes.clear()
        self.devices.clear()
        return flush

    def restore(self, flush: AnalyticsFlush) -> None:
        for key, value in flush.messages.items():
            self.messages[key] += value
        for key, value in flush.channels.items():
            self.channels[key] += value
        for key, value in flush.ping_sent.items():
            self.ping_sent[key] += value
        for key, value in flush.ping_received.items():
            self.ping_received[key] += value
        for key, value in flush.everyone.items():
            self.everyone[key] += value
        for key, value in flush.hours.items():
            self.hours[key] += value
        for key, value in flush.words.items():
            self.words[key] += value
        self.last_message.update(flush.last_message)
        for guild_id, record in flush.longest.items():
            current = self.longest.get(guild_id)
            if current is None or record[2] > current[2]:
                self.longest[guild_id] = record
        for key, value in flush.voice_minutes.items():
            self.voice_minutes[key] += value
        self.devices.update(flush.devices)
