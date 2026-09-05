"""Ephemeral server analytics. Tracking is silent; only slash replies are sent."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from shadowmarket.analytics import ChatEvent
from shadowmarket.embeds import BLURPLE, GOLD, GREEN, error, info

log = logging.getLogger("shadowmarket.analytics")

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
BARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[int]) -> str:
    if not values:
        return "—"
    hi = max(values)
    if hi <= 0:
        return BARS[0] * len(values)
    last = len(BARS) - 1
    return "".join(BARS[min(last, int(v / hi * last))] for v in values)


def rank_lines(rows, namer, value_key: str, formatter=str) -> str:
    if not rows:
        return "No data yet."
    lines = []
    for i, row in enumerate(rows, start=1):
        lines.append(f"`{i}.` **{namer(row['user_id'])}** — {formatter(row[value_key])}")
    return "\n".join(lines)


def event_from_message(message: discord.Message) -> ChatEvent:
    member = message.author if isinstance(message.author, discord.Member) else None
    everyone = bool(message.mention_everyone and "@everyone" in message.content)
    return ChatEvent(
        guild_id=str(message.guild.id) if message.guild else "",
        channel_id=str(message.channel.id),
        user_id=str(message.author.id),
        content=message.content or "",
        created_at=message.created_at,
        mentioned_ids=[str(user.id) for user in message.mentions],
        mention_everyone=everyone,
        desktop=bool(member and member.desktop_status != discord.Status.offline),
        mobile=bool(member and member.mobile_status != discord.Status.offline),
        web=bool(member and member.web_status != discord.Status.offline),
    )


class AnalyticsCog(commands.Cog):
    analytics = app_commands.Group(
        name="analytics",
        description="Server health and engagement reports (visible only to you)",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.analytics.record_floor.update(await self.bot.db.all_longest_floors())

    async def _flush(self) -> None:
        pending = self.bot.analytics.drain()
        try:
            await self.bot.db.apply_analytics_flush(pending)
            for guild_id, (_u, _w, n) in pending.longest.items():
                floor = self.bot.analytics.record_floor.get(guild_id, 0)
                self.bot.analytics.record_floor[guild_id] = max(floor, n)
        except Exception:
            log.exception("Analytics flush failed")
            self.bot.analytics.restore(pending)
            raise

    def _namer(self, guild: discord.Guild | None):
        return lambda uid: self.bot.trader_name(guild, str(uid))

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            await self._cache_invites(guild)
            for channel in guild.voice_channels:
                for member in channel.members:
                    if not member.bot:
                        self.bot.analytics.start_voice(str(guild.id), str(member.id))
            try:
                await self.bot.db.snapshot_members(
                    str(guild.id),
                    datetime.now(timezone.utc).date().isoformat(),
                    guild.member_count or len(guild.members),
                )
            except Exception:
                log.exception("Member snapshot failed for %s", guild.id)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot or member.guild is None:
            return
        guild_id = str(member.guild.id)
        user_id = str(member.id)
        was_in = before.channel is not None
        now_in = after.channel is not None
        if was_in and not now_in:
            self.bot.analytics.stop_voice(guild_id, user_id)
        elif not was_in and now_in:
            self.bot.analytics.start_voice(guild_id, user_id)
        elif was_in and now_in and before.channel != after.channel:
            self.bot.analytics.stop_voice(guild_id, user_id)
            self.bot.analytics.start_voice(guild_id, user_id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        invite = await self._used_invite(member.guild)
        created = member.created_at.astimezone(timezone.utc).replace(tzinfo=None).isoformat(
            timespec="seconds"
        )
        await self.bot.db.record_member_event(
            str(member.guild.id),
            str(member.id),
            "JOIN",
            account_created_at=created,
            invite_code=invite,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return
        kind = await self._leave_kind(member)
        await self.bot.db.record_member_event(str(member.guild.id), str(member.id), kind)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        if invite.guild is None:
            return
        await self._cache_invites(invite.guild)

    @analytics.command(name="report", description="Full server analytics report")
    @app_commands.guild_only()
    async def report(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._flush()
        guild = interaction.guild
        assert guild is not None
        gid = str(guild.id)
        namer = self._namer(guild)
        overview = await self.bot.db.analytics_overview(gid)
        chatters = await self.bot.db.top_chatters(gid)
        words = await self.bot.db.top_words(gid)
        pingers = await self.bot.db.top_pingers(gid)
        victims = await self.bot.db.top_ping_victims(gid)
        everyone = await self.bot.db.top_everyone(gid)
        longest = await self.bot.db.longest_word(gid)
        hours = await self.bot.db.hourly_totals(gid)

        total = overview["messages"] or 1
        peak = overview["peak_hour"]
        peak_txt = "No traffic yet"
        if peak is not None and overview["peak_volume"]:
            share = overview["peak_volume"] / total * 100
            peak_txt = f"{peak:02d}:00 UTC ({overview['peak_volume']:,} msgs, {share:.1f}%)"

        embed = discord.Embed(
            title=f"Server analytics — {guild.name}",
            color=BLURPLE,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Messages tracked", value=f"{overview['messages']:,}", inline=True)
        embed.add_field(name="Direct pings", value=f"{overview['pings']:,}", inline=True)
        embed.add_field(name="Chatters", value=f"{overview['speakers']:,}", inline=True)
        embed.add_field(name="Peak hour", value=peak_txt, inline=False)

        chatter_lines = []
        chatter_sum = 0
        for i, row in enumerate(chatters, start=1):
            n = int(row["message_count"])
            chatter_sum += n
            pct = n / total * 100
            chatter_lines.append(f"`{i}.` **{namer(row['user_id'])}** — {n:,} ({pct:.1f}%)")
        embed.add_field(
            name="Top chatters",
            value="\n".join(chatter_lines) or "No messages yet.",
            inline=False,
        )
        if chatters:
            embed.set_footer(
                text=f"Top {len(chatters)} generated {chatter_sum / total * 100:.1f}% of tracked chat · UTC"
            )

        word_lines = [
            f"`{i}.` **{row['word']}** — {int(row['count']):,}"
            for i, row in enumerate(words, start=1)
        ]
        embed.add_field(
            name="Top meaningful words (stop words filtered)",
            value="\n".join(word_lines) or "No lexical data yet.",
            inline=False,
        )
        if longest:
            preview = longest["word"]
            if len(preview) > 80:
                preview = preview[:80] + "…"
            embed.add_field(
                name="Longest word record",
                value=f"**{namer(longest['user_id'])}** — {int(longest['char_count'])} chars\n`{preview}`",
                inline=False,
            )

        ping_block = "**Pingers**\n" + rank_lines(pingers, namer, "n", lambda v: f"{int(v):,}")
        ping_block += "\n**Most mentioned**\n" + rank_lines(victims, namer, "n", lambda v: f"{int(v):,}")
        embed.add_field(name="Ping economy", value=ping_block, inline=False)
        embed.add_field(
            name="@everyone",
            value=rank_lines(everyone, namer, "n", lambda v: f"{int(v):,}"),
            inline=False,
        )

        hour_map = {int(r["hour"]): int(r["n"]) for r in hours}
        embed.add_field(
            name="Activity by hour (UTC)",
            value=sparkline([hour_map.get(h, 0) for h in range(24)]),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @analytics.command(name="chatters", description="Who drives the conversation")
    @app_commands.guild_only()
    async def chatters(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._flush()
        guild = interaction.guild
        gid = str(guild.id)
        overview = await self.bot.db.analytics_overview(gid)
        rows = await self.bot.db.top_chatters(gid, 15)
        total = overview["messages"] or 1
        namer = self._namer(guild)
        lines = []
        for i, row in enumerate(rows, start=1):
            n = int(row["message_count"])
            lines.append(f"`{i}.` **{namer(row['user_id'])}** — {n:,} ({n / total * 100:.1f}%)")
        embed = info("Top chatters", "\n".join(lines) or "No messages tracked yet.")
        embed.set_footer(text=f"{overview['messages']:,} messages · {overview['speakers']:,} speakers")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @analytics.command(name="words", description="Meaningful vocabulary after stop-word filtering")
    @app_commands.guild_only()
    async def words(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._flush()
        guild = interaction.guild
        rows = await self.bot.db.top_words(str(guild.id), 15)
        longest = await self.bot.db.longest_word(str(guild.id))
        lines = [f"`{i}.` **{row['word']}** — {int(row['count']):,}" for i, row in enumerate(rows, start=1)]
        embed = info("Lexical breakdown", "\n".join(lines) or "No words tracked yet.")
        if longest:
            preview = longest["word"]
            if len(preview) > 120:
                preview = preview[:120] + "…"
            embed.add_field(
                name="Longest word",
                value=f"{self.bot.trader_name(guild, longest['user_id'])} · {int(longest['char_count'])} chars\n`{preview}`",
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @analytics.command(name="pings", description="Who pings, and who gets pinged")
    @app_commands.guild_only()
    async def pings(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._flush()
        guild = interaction.guild
        namer = self._namer(guild)
        pingers = await self.bot.db.top_pingers(str(guild.id))
        victims = await self.bot.db.top_ping_victims(str(guild.id))
        embed = info("Ping analysis")
        embed.add_field(name="Top pingers", value=rank_lines(pingers, namer, "n", lambda v: f"{int(v):,}"), inline=False)
        embed.add_field(name="Most mentioned", value=rank_lines(victims, namer, "n", lambda v: f"{int(v):,}"), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @analytics.command(name="everyone", description="@everyone usage")
    @app_commands.guild_only()
    async def everyone(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._flush()
        guild = interaction.guild
        rows = await self.bot.db.top_everyone(str(guild.id), 10)
        embed = info(
            "@everyone usage",
            rank_lines(rows, self._namer(guild), "n", lambda v: f"{int(v):,}"),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @analytics.command(name="channels", description="Most active text channels")
    @app_commands.guild_only()
    async def channels(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._flush()
        rows = await self.bot.db.top_channels(str(interaction.guild_id), 15)
        lines = []
        for i, row in enumerate(rows, start=1):
            lines.append(f"`{i}.` <#{row['channel_id']}> — {int(row['message_count']):,}")
        await interaction.followup.send(
            embed=info("Popular text channels", "\n".join(lines) or "No channel data yet."),
            ephemeral=True,
        )

    @analytics.command(name="schedule", description="Busiest hours and days of the week")
    @app_commands.guild_only()
    async def schedule(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._flush()
        gid = str(interaction.guild_id)
        hours = {int(r["hour"]): int(r["n"]) for r in await self.bot.db.hourly_totals(gid)}
        days = {int(r["weekday"]): int(r["n"]) for r in await self.bot.db.weekday_totals(gid)}
        hour_vals = [hours.get(h, 0) for h in range(24)]
        day_lines = [f"`{WEEKDAYS[d]}` — {days.get(d, 0):,}" for d in range(7)]
        peak_hour = max(range(24), key=lambda h: hours.get(h, 0)) if any(hour_vals) else None
        embed = info("When the server is awake")
        embed.add_field(
            name="Hourly (UTC)",
            value=sparkline(hour_vals) + (f"\nPeak `{peak_hour:02d}:00`" if peak_hour is not None else ""),
            inline=False,
        )
        embed.add_field(name="Weekdays", value="\n".join(day_lines), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @analytics.command(name="voice", description="Voice minutes and Voice XP")
    @app_commands.guild_only()
    async def voice(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._flush()
        rows = await self.bot.db.top_voice(str(interaction.guild_id), 15)
        namer = self._namer(interaction.guild)
        lines = []
        for i, row in enumerate(rows, start=1):
            minutes = float(row["minutes"])
            xp = int(minutes)
            hours, mins = divmod(int(minutes), 60)
            lines.append(f"`{i}.` **{namer(row['user_id'])}** — {hours}h {mins}m · {xp:,} Voice XP")
        embed = info(
            "Voice tracking",
            "\n".join(lines) or "No voice time recorded yet. Time starts when someone joins a voice channel.",
        )
        embed.set_footer(text="1 Voice XP per minute in voice")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @analytics.command(name="growth", description="Member count, retention, and invite performance")
    @app_commands.guild_only()
    async def growth(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._flush()
        guild = interaction.guild
        gid = str(guild.id)
        history = list(reversed(await self.bot.db.member_history(gid, 30)))
        joined, retained = await self.bot.db.retention(gid, 30)
        invites = await self.bot.db.top_invites(gid)
        events = await self.bot.db.member_event_counts(gid)
        ages = await self.bot.db.account_age_buckets(gid)

        counts = [int(r["member_count"]) for r in history]
        embed = discord.Embed(title="Growth metrics", color=GREEN, timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Members now", value=str(guild.member_count or len(guild.members)), inline=True)
        embed.add_field(name="Joins tracked", value=str(events.get("JOIN", 0)), inline=True)
        embed.add_field(name="Leaves tracked", value=str(events.get("LEAVE", 0)), inline=True)
        if counts:
            embed.add_field(
                name="30-day member trend",
                value=sparkline(counts) + f"\n{counts[0]:,} → {counts[-1]:,}",
                inline=False,
            )
        rate = (retained / joined * 100) if joined else 0
        embed.add_field(
            name="New member retention (30d)",
            value=f"{retained}/{joined} newcomers have chatted ({rate:.0f}%)",
            inline=False,
        )
        namer = self._namer(guild)
        inv_lines = []
        for row in invites:
            who = namer(row["inviter_id"]) if row["inviter_id"] else "unknown"
            inv_lines.append(f"`{row['code']}` · {who} · {int(row['uses']):,} joins")
        embed.add_field(name="Invite sources", value="\n".join(inv_lines) or "Need **Manage Server** to track invites.", inline=False)
        age_lines = [f"**{label}** — {n}" for label, n in ages.items() if n]
        embed.add_field(
            name="Account age at join",
            value="\n".join(age_lines) or "No join ages yet.",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @analytics.command(name="health", description="Engagement rate, boosts, devices, and mod actions")
    @app_commands.guild_only()
    async def health(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._flush()
        guild = interaction.guild
        gid = str(guild.id)
        overview = await self.bot.db.analytics_overview(gid)
        since = (datetime.now(timezone.utc) - timedelta(days=7)).replace(tzinfo=None).isoformat(
            timespec="seconds"
        )
        active = await self.bot.db.active_since(gid, since)
        members = guild.member_count or len(guild.members) or 1
        events = await self.bot.db.member_event_counts(gid)
        desktop, mobile, web, device_n = await self.bot.db.device_totals(gid)

        embed = discord.Embed(title="Server health", color=GOLD, timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Members", value=str(members), inline=True)
        embed.add_field(name="Active (7d chat)", value=str(active), inline=True)
        embed.add_field(name="Engagement", value=f"{active / members * 100:.1f}%", inline=True)
        embed.add_field(name="Boosts", value=str(guild.premium_subscription_count or 0), inline=True)
        embed.add_field(name="Boost tier", value=str(guild.premium_tier), inline=True)
        embed.add_field(name="Tracked messages", value=f"{overview['messages']:,}", inline=True)
        embed.add_field(
            name="Moderation",
            value=f"Bans {events.get('BAN', 0)} · Kicks {events.get('KICK', 0)} · Leaves {events.get('LEAVE', 0)}",
            inline=False,
        )
        if device_n:
            embed.add_field(
                name="Device presence (sampled)",
                value=f"Desktop {desktop} · Mobile {mobile} · Web {web}\nRequires the Presence intent.",
                inline=False,
            )
        else:
            embed.add_field(
                name="Device presence",
                value="Enable **Presence Intent** in the Developer Portal to sample desktop/mobile/web.",
                inline=False,
            )
        embed.add_field(
            name="Country breakdown",
            value="Discord does not expose member countries or IPs to bots.",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @analytics.command(name="backfill", description="Ingest historical messages from before this bot session")
    @app_commands.describe(channel="Channel to scan (defaults to here)", limit="Max messages (capped at 10,000)")
    @app_commands.guild_only()
    async def backfill(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        limit: app_commands.Range[int, 1, 10000] = 2000,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.followup.send(embed=error("Wrong channel", "Pick a text channel."), ephemeral=True)
            return
        cutoff = getattr(self.bot, "started_at", datetime.now(timezone.utc))
        scanned = 0
        ingested = 0
        try:
            async for message in target.history(limit=int(limit)):
                scanned += 1
                if message.created_at >= cutoff:
                    continue
                if message.author.bot or message.webhook_id is not None:
                    continue
                if message.guild is None:
                    continue
                self.bot.analytics.ingest(event_from_message(message))
                ingested += 1
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error("Missing access", "I need **Read Message History** in that channel."),
                ephemeral=True,
            )
            return
        await self._flush()
        await interaction.followup.send(
            embed=info(
                "Backfill complete",
                f"Scanned **{scanned:,}** messages in {target.mention}, ingested **{ingested:,}** "
                f"from before this session. Live tracking is unchanged (zero-spam).",
            ),
            ephemeral=True,
        )

    async def _cache_invites(self, guild: discord.Guild) -> None:
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return
        snapshot = {}
        for invite in invites:
            inviter = str(invite.inviter.id) if invite.inviter else None
            snapshot[invite.code] = (inviter, invite.uses or 0)
        self.bot.analytics.invite_uses[str(guild.id)] = snapshot

    async def _used_invite(self, guild: discord.Guild) -> str | None:
        previous = self.bot.analytics.invite_uses.get(str(guild.id), {})
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return None
        used = None
        snapshot = {}
        for invite in invites:
            inviter = str(invite.inviter.id) if invite.inviter else None
            uses = invite.uses or 0
            snapshot[invite.code] = (inviter, uses)
            old = previous.get(invite.code)
            old_uses = old[1] if old else 0
            if uses > old_uses:
                used = invite.code
                await self.bot.db.bump_invite(str(guild.id), invite.code, inviter, uses - old_uses)
        self.bot.analytics.invite_uses[str(guild.id)] = snapshot
        return used

    async def _leave_kind(self, member: discord.Member) -> str:
        guild = member.guild
        now = datetime.now(timezone.utc)
        try:
            async for entry in guild.audit_logs(limit=6):
                if entry.target is None or getattr(entry.target, "id", None) != member.id:
                    continue
                if (now - entry.created_at).total_seconds() > 30:
                    continue
                if entry.action == discord.AuditLogAction.ban:
                    return "BAN"
                if entry.action == discord.AuditLogAction.kick:
                    return "KICK"
        except (discord.Forbidden, discord.HTTPException):
            return "LEAVE"
        return "LEAVE"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AnalyticsCog(bot))
