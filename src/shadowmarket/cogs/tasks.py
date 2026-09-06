"""Background loops: volume flush, hourly prices, bounty expiry, ticker edits."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord.ext import commands, tasks

from shadowmarket import config
from shadowmarket.analytics import should_post_daily_report
from shadowmarket.cogs.analytics import build_server_report
from shadowmarket.economy import next_price
from shadowmarket.embeds import ticker_embed

log = logging.getLogger("shadowmarket.tasks")


class TaskCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.flush_usage.start()
        self.expire_bounties.start()
        self.tick_prices.start()
        self.update_tickers.start()
        self.daily_reports.start()

    async def cog_unload(self) -> None:
        self.flush_usage.cancel()
        self.expire_bounties.cancel()
        self.tick_prices.cancel()
        self.update_tickers.cancel()
        self.daily_reports.cancel()

    @tasks.loop(seconds=config.FLUSH_INTERVAL_SECONDS)
    async def flush_usage(self) -> None:
        pending = self.bot.cache.drain_usage_buffer()
        try:
            await self.bot.db.apply_volume_flush(pending)
        except Exception:
            log.exception("Failed to flush usage buffer")
            for key, value in pending.items():
                self.bot.cache.usage_buffer[key] += value
        counts = {
            guild_id: (self.bot.cache.daily_date[guild_id], self.bot.cache.daily_messages[guild_id])
            for guild_id in self.bot.cache.daily_messages
            if guild_id in self.bot.cache.daily_date
        }
        try:
            await self.bot.db.save_daily_counts(counts)
        except Exception:
            log.exception("Failed to persist daily message counts")
        analytics = self.bot.analytics.drain()
        try:
            await self.bot.db.apply_analytics_flush(analytics)
            for guild_id, (_user, _word, n) in analytics.longest.items():
                floor = self.bot.analytics.record_floor.get(guild_id, 0)
                self.bot.analytics.record_floor[guild_id] = max(floor, n)
        except Exception:
            log.exception("Failed to flush analytics")
            self.bot.analytics.restore(analytics)
        self.bot.cache.prune_cooldowns()

    @flush_usage.before_loop
    async def before_flush(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=config.EXPIRY_CHECK_SECONDS)
    async def expire_bounties(self) -> None:
        now = datetime.now(timezone.utc)
        try:
            expired = await self.bot.db.expired_active_bounties(now)
        except Exception:
            log.exception("Failed to load expired bounties")
            return
        for bounty in expired:
            refund = bounty.reward_amount * config.BOUNTY_EXPIRE_REFUND_RATE
            try:
                ok = await self.bot.db.expire_bounty(bounty.bounty_id, refund)
            except Exception:
                log.exception("Failed to expire bounty %s", bounty.bounty_id)
                continue
            if ok:
                self.bot.cache.remove_bounty(bounty.guild_id, bounty.bounty_id)

    @expire_bounties.before_loop
    async def before_expire(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(hours=config.PRICE_UPDATE_HOURS)
    async def tick_prices(self) -> None:
        volumes = self.bot.cache.drain_period_volume()
        keywords = await self.bot.db.all_active_stock_keywords()
        seen: set[tuple[str, str]] = set()
        for guild_id, keyword in keywords:
            seen.add((guild_id, keyword))
            volume = volumes.get((guild_id, keyword), 0)
            stock = await self.bot.db.get_stock(guild_id, keyword)
            if stock is None:
                continue
            decay_paused = self.bot.cache.is_dead_server(
                guild_id, config.DEAD_SERVER_DAILY_MESSAGES
            )
            new_price = next_price(
                float(stock["current_price"]),
                volume,
                decay_paused=decay_paused,
            )
            await self.bot.db.apply_price_tick(guild_id, keyword, new_price, volume)

        for key, volume in volumes.items():
            if key not in seen:
                self.bot.cache.period_volume[key] += volume

    @tick_prices.before_loop
    async def before_prices(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=config.TICKER_UPDATE_MINUTES)
    async def update_tickers(self) -> None:
        configs = await self.bot.db.all_server_configs()
        for row in configs:
            channel_id = row["ticker_channel_id"]
            message_id = row["ticker_message_id"]
            if not channel_id or not message_id:
                continue
            channel = self.bot.get_channel(int(channel_id))
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                message = await channel.fetch_message(int(message_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                log.warning("Ticker message missing for guild %s", row["guild_id"])
                continue

            guild_id = row["guild_id"]
            stocks = await self.bot.db.list_active_stocks(guild_id)
            stock_dicts = [
                {
                    "keyword": s["keyword"],
                    "current_price": float(s["current_price"]),
                    "previous_price": float(s["previous_price"]),
                    "pending_volume": self.bot.cache.period_volume.get(
                        (guild_id, s["keyword"]), 0
                    ),
                }
                for s in stocks
            ]
            richest = await self.bot.db.net_worths(guild_id, 3)
            guild = self.bot.get_guild(int(guild_id))

            embed = ticker_embed(
                stock_dicts,
                self.bot.cache.public_bounty_count(guild_id),
                richest,
                lambda uid: self.bot.trader_name(guild, uid),
            )
            try:
                await message.edit(embed=embed)
            except discord.HTTPException:
                log.warning("Could not edit ticker for guild %s", guild_id)

        today = date.today().isoformat()
        for guild in self.bot.guilds:
            try:
                await self.bot.db.snapshot_members(
                    str(guild.id),
                    today,
                    guild.member_count or len(guild.members),
                )
            except Exception:
                log.exception("Member snapshot failed for %s", guild.id)

    @update_tickers.before_loop
    async def before_tickers(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def daily_reports(self) -> None:
        try:
            configs = await self.bot.db.all_server_configs()
        except Exception:
            log.exception("Failed to load server configs for daily reports")
            return
        for row in configs:
            channel_id = row["report_channel_id"]
            if not channel_id:
                continue
            tz_name = row["report_timezone"] or config.REPORT_TZ
            try:
                tz = ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                tz = ZoneInfo(config.REPORT_TZ)
                tz_name = config.REPORT_TZ
            now = datetime.now(tz)
            if not should_post_daily_report(
                now,
                row["last_report_date"],
                hour=config.REPORT_HOUR,
                minute=config.REPORT_MINUTE,
            ):
                continue
            channel = self.bot.get_channel(int(channel_id))
            if not isinstance(channel, discord.TextChannel):
                continue
            guild = channel.guild
            try:
                embed = await build_server_report(self.bot, guild)
                extra = f"Daily auto-report · {config.REPORT_HOUR:02d}:{config.REPORT_MINUTE:02d} {tz_name}"
                footer = embed.footer.text
                embed.set_footer(text=f"{footer} · {extra}" if footer else extra)
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                await self.bot.db.mark_daily_report_sent(str(guild.id), now.date().isoformat())
                log.info("Posted daily analytics report for guild %s", guild.id)
            except discord.Forbidden:
                log.warning("Cannot post daily report in channel %s", channel_id)
            except Exception:
                log.exception("Daily report failed for guild %s", row["guild_id"])

    @daily_reports.before_loop
    async def before_daily_reports(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TaskCog(bot))
