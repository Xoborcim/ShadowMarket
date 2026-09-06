"""discord.py bot: silent listener, cache hydrate, slash command tree."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord.ext import commands

from shadowmarket.analytics import AnalyticsBuffer
from shadowmarket.cache import MarketCache
from shadowmarket.cogs.analytics import event_from_message
from shadowmarket.config import DATABASE_PATH, DEV_GUILD_ID
from shadowmarket.database import Database
from shadowmarket.tokenizer import keyword_hits

log = logging.getLogger("shadowmarket")

EXTENSIONS = (
    "shadowmarket.cogs.market",
    "shadowmarket.cogs.bounty",
    "shadowmarket.cogs.admin",
    "shadowmarket.cogs.analytics",
    "shadowmarket.cogs.tasks",
)


class ShadowMarketBot(commands.Bot):
    def __init__(self, db_path: Path | None = None) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.voice_states = True
        intents.presences = os.getenv("PRESENCE_INTENT", "").lower() in {"1", "true", "yes"}
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, help_command=None)
        self.db = Database(db_path or DATABASE_PATH)
        self.cache = MarketCache()
        self.analytics = AnalyticsBuffer()
        self.started_at = datetime.now(timezone.utc)
        self._synced_commands = False

    async def setup_hook(self) -> None:
        await self.db.connect()
        await self._hydrate_cache()
        for extension in EXTENSIONS:
            await self.load_extension(extension)

        names = [cmd.name for cmd in self.tree.get_commands()]
        log.info("Command tree loaded: %s", ", ".join(names) or "(empty)")
        self.tree.error(self.on_app_command_error)

    async def _sync_app_commands(self) -> None:
        """Guild sync is instant; global sync can take up to an hour."""
        guilds = list(self.guilds)
        if DEV_GUILD_ID and not any(g.id == DEV_GUILD_ID for g in guilds):
            guilds.append(discord.Object(id=DEV_GUILD_ID))  # type: ignore[arg-type]

        for guild in guilds:
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info(
                    "Synced %s commands instantly to guild %s: %s",
                    len(synced),
                    getattr(guild, "id", guild),
                    ", ".join(c.name for c in synced),
                )
            except discord.HTTPException:
                log.exception("Guild command sync failed for %s", getattr(guild, "id", guild))

        try:
            synced = await self.tree.sync()
            log.info("Synced %s global commands (may take up to an hour to appear)", len(synced))
        except discord.HTTPException:
            log.exception("Global command sync failed")

    async def _hydrate_cache(self) -> None:
        pairs = await self.db.all_active_stock_keywords()
        by_guild: dict[str, list[str]] = {}
        for guild_id, keyword in pairs:
            by_guild.setdefault(guild_id, []).append(keyword)
        for guild_id, keywords in by_guild.items():
            self.cache.load_stocks(guild_id, keywords)
        bounties = await self.db.list_active_bounties()
        self.cache.load_bounties(bounties)
        for guild_id, (day, count) in (await self.db.load_daily_counts()).items():
            self.cache.daily_date[guild_id] = day
            self.cache.daily_messages[guild_id] = count
        log.info(
            "Cache ready: %s stocks across %s guilds, %s active bounties",
            sum(len(v) for v in self.cache.stocks.values()),
            len(self.cache.stocks),
            sum(len(v) for v in self.cache.bounties.values()),
        )

    async def close(self) -> None:
        import time

        now = time.monotonic()
        for guild_id, user_id in list(self.analytics.voice_joined):
            self.analytics.stop_voice(guild_id, user_id, now)
        usage = self.cache.drain_usage_buffer()
        analytics = self.analytics.drain()
        try:
            await self.db.apply_volume_flush(usage)
            await self.db.apply_analytics_flush(analytics)
        except Exception:
            log.exception("Failed to flush buffers on shutdown")
        await self.db.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "?")
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(type=discord.ActivityType.watching, name="the ticker"),
        )
        if not self._synced_commands:
            self._synced_commands = True
            await self._sync_app_commands()

    async def on_guild_join(self, guild: discord.Guild) -> None:
        try:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %s commands to new guild %s", len(synced), guild.id)
        except discord.HTTPException:
            log.exception("Command sync failed for new guild %s", guild.id)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.webhook_id is not None:
            return
        if message.guild is None:
            return

        guild_id = str(message.guild.id)
        user_id = str(message.author.id)
        self.cache.record_message(guild_id)
        self.analytics.ingest(event_from_message(message))

        stock_set = self.cache.stocks.get(guild_id) or set()
        bounty_index = self.cache.bounties_by_keyword.get(guild_id) or {}
        universe = stock_set | set(bounty_index.keys())
        if not universe:
            return

        for keyword in keyword_hits(message.content, universe):
            self.cache.hit_stock(guild_id, user_id, keyword)
            await self._try_claim_bounties(guild_id, user_id, keyword)

    async def _try_claim_bounties(self, guild_id: str, speaker_id: str, keyword: str) -> None:
        claims = self.cache.matching_claimable(guild_id, keyword, speaker_id)
        for bounty in claims:
            try:
                payout = await self.db.claim_bounty(bounty.bounty_id, speaker_id, guild_id)
            except Exception:
                log.exception("Failed claiming bounty %s", bounty.bounty_id)
                continue
            if payout is not None:
                self.cache.remove_bounty(guild_id, bounty.bounty_id)
                log.info(
                    "Silent claim: bounty %s keyword=%s guild=%s amount=%.2f",
                    bounty.bounty_id,
                    keyword,
                    guild_id,
                    payout,
                )

    def trader_name(self, guild: discord.Guild | None, user_id: str) -> str:
        """Resolve a display name without mentioning (zero-spam)."""
        uid = int(user_id)
        if guild is not None:
            member = guild.get_member(uid)
            if member is not None:
                return member.display_name
        user = self.get_user(uid)
        if user is not None:
            return user.display_name
        return f"Trader-{str(uid)[-4:]}"

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError
    ) -> None:
        log.warning("Command error: %s", error)
        if interaction.response.is_done():
            return
        await interaction.response.send_message(
            "That command could not be completed.",
            ephemeral=True,
        )
