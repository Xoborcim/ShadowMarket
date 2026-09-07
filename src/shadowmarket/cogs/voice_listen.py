"""Join the busiest VC after reaction consent, transcribe speech into stocks/analytics."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands, tasks

from shadowmarket import config
from shadowmarket.embeds import GOLD, info, success
from shadowmarket.voice_audio import UtteranceAssembler, busiest_channel_id, should_switch_channel
from shadowmarket.voice_sink import DavePcmSink

log = logging.getLogger("shadowmarket.voice")

try:
    from discord.ext import voice_recv
except ImportError:  # pragma: no cover
    voice_recv = None


@dataclass
class GuildVoiceState:
    target_id: int | None = None
    consent_message_id: int | None = None
    consent_channel_id: int | None = None
    consented: set[int] = field(default_factory=set)
    declined: set[int] = field(default_factory=set)
    prompted: set[int] = field(default_factory=set)
    prompt_started: float = 0.0
    last_denied_prompt: float = 0.0
    waiting: bool = False
    paused: bool = False


class VoiceListenCog(commands.Cog):
    listen = app_commands.Group(
        name="listen",
        description="Voice transcription for stocks and analytics",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.states: dict[int, GuildVoiceState] = {}
        self._packets: asyncio.Queue = asyncio.Queue()
        self._assemblers = {}
        self._worker: asyncio.Task | None = None

    async def cog_load(self) -> None:
        if config.VOICE_LISTEN:
            self.watch_voice.start()
            self._worker = asyncio.create_task(self._transcribe_worker(), name="voice-stt")

    async def cog_unload(self) -> None:
        self.watch_voice.cancel()
        if self._worker:
            self._worker.cancel()
        for guild in list(self.bot.guilds):
            vc = guild.voice_client
            if vc:
                await vc.disconnect(force=True)

    def _state(self, guild_id: int) -> GuildVoiceState:
        return self.states.setdefault(guild_id, GuildVoiceState())

    @listen.command(name="pause", description="Stop joining voice and prompting for consent")
    @app_commands.guild_only()
    async def pause(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        state = self._state(interaction.guild_id)
        state.paused = True
        if interaction.guild and interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect()
        await interaction.followup.send(
            embed=success("Voice listen paused", "I will stay out of voice until `/listen resume`."),
            ephemeral=True,
        )

    @listen.command(name="resume", description="Resume joining the busiest voice channel")
    @app_commands.guild_only()
    async def resume(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        self._state(interaction.guild_id).paused = False
        await interaction.followup.send(
            embed=success("Voice listen on", "I will ask the busiest VC for consent, then join."),
            ephemeral=True,
        )

    @listen.command(name="status", description="Who is being transcribed right now")
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        state = self._state(guild.id)
        vc = guild.voice_client
        channel = vc.channel if vc else None
        names = [self.bot.trader_name(guild, str(uid)) for uid in state.consented]
        body = (
            f"Paused: **{state.paused}**\n"
            f"Channel: {channel.mention if channel else 'not connected'}\n"
            f"Consented: {', '.join(names) if names else 'nobody yet'}"
        )
        await interaction.followup.send(embed=info("Voice listen", body), ephemeral=True)

    @tasks.loop(seconds=config.VOICE_POLL_SECONDS)
    async def watch_voice(self) -> None:
        if voice_recv is None or not config.VOICE_LISTEN:
            return
        for guild in self.bot.guilds:
            try:
                await self._tick_guild(guild)
            except Exception:
                log.exception("Voice watch failed for guild %s", guild.id)

    @watch_voice.before_loop
    async def before_watch(self) -> None:
        await self.bot.wait_until_ready()

    async def _tick_guild(self, guild: discord.Guild) -> None:
        state = self._state(guild.id)
        if state.paused:
            return
        counts = [
            (ch.id, sum(1 for m in ch.members if not m.bot))
            for ch in guild.voice_channels
        ]
        candidate_id = busiest_channel_id(counts)
        count_map = dict(counts)
        current = guild.voice_client.channel if guild.voice_client else None
        current_id = current.id if current else None
        current_n = count_map.get(current_id, 0) if current_id else 0
        candidate_n = count_map.get(candidate_id, 0) if candidate_id else 0

        if candidate_id is None:
            if guild.voice_client:
                await self._leave(guild, "Voice channels are empty")
            return

        if current_id == candidate_id:
            await self._prompt_late_joiners(guild, current)
            return

        if current_id and not should_switch_channel(
            current_id,
            current_n,
            candidate_id,
            candidate_n,
            margin=config.VOICE_SWITCH_MARGIN,
        ):
            await self._prompt_late_joiners(guild, current)
            return

        target = guild.get_channel(candidate_id)
        if not isinstance(target, discord.VoiceChannel):
            return

        if state.waiting and state.target_id == candidate_id:
            await self._maybe_join_after_consent(guild, target)
            return

        now = time.monotonic()
        if now - state.last_denied_prompt < config.VOICE_PROMPT_COOLDOWN and state.target_id == candidate_id:
            return

        if current_id:
            await self._leave(guild, "Moving to a busier voice channel")
        await self._prompt_consent(guild, target)

    async def _prompt_consent(self, guild: discord.Guild, channel: discord.VoiceChannel) -> None:
        humans = [m for m in channel.members if not m.bot]
        if not humans:
            return
        state = self._state(guild.id)
        mentions = " ".join(m.mention for m in humans)
        embed = discord.Embed(
            title="Join voice for ShadowMarket?",
            description=(
                "I want to join **this** voice channel — it's the busiest one — to transcribe speech "
                "for stock prices **and** server analytics.\n\n"
                f"{config.CONSENT_YES} Consent to **your** voice being transcribed\n"
                f"{config.CONSENT_NO} Decline (I will ignore your audio)\n\n"
                f"I join after someone consents, or after {config.VOICE_CONSENT_SECONDS}s if anyone has. "
                "Only consenting users are transcribed."
            ),
            color=GOLD,
        )
        try:
            message = await channel.send(
                content=mentions,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True, everyone=False, roles=False),
            )
            await message.add_reaction(config.CONSENT_YES)
            await message.add_reaction(config.CONSENT_NO)
        except discord.HTTPException:
            log.warning("Cannot send consent prompt in %s", channel.id)
            return
        state.target_id = channel.id
        state.consent_message_id = message.id
        state.consent_channel_id = channel.id
        state.consented.clear()
        state.declined.clear()
        state.prompted = {m.id for m in humans}
        state.prompt_started = time.monotonic()
        state.waiting = True

    async def _prompt_late_joiners(self, guild: discord.Guild, channel: discord.VoiceChannel | None) -> None:
        if channel is None:
            return
        state = self._state(guild.id)
        if not state.consent_message_id:
            return
        newcomers = [
            m
            for m in channel.members
            if not m.bot
            and m.id not in state.prompted
            and m.id not in state.consented
            and m.id not in state.declined
        ]
        if not newcomers:
            return
        jump = f"https://discord.com/channels/{guild.id}/{channel.id}/{state.consent_message_id}"
        mentions = " ".join(m.mention for m in newcomers)
        try:
            await channel.send(
                content=(
                    f"{mentions} ShadowMarket is transcribing this VC for stocks and analytics. "
                    f"React {config.CONSENT_YES} on {jump} to opt in, or {config.CONSENT_NO} to stay out."
                ),
                allowed_mentions=discord.AllowedMentions(users=True, everyone=False, roles=False),
            )
        except discord.HTTPException:
            return
        state.prompted.update(m.id for m in newcomers)

    async def _maybe_join_after_consent(self, guild: discord.Guild, channel: discord.VoiceChannel) -> None:
        state = self._state(guild.id)
        elapsed = time.monotonic() - state.prompt_started
        humans = [m for m in channel.members if not m.bot]
        pending = [m for m in humans if m.id not in state.consented and m.id not in state.declined]
        if state.consented and not pending:
            await self._join(guild, channel)
            return
        if elapsed < config.VOICE_CONSENT_SECONDS:
            return
        if state.consented:
            await self._join(guild, channel)
            return
        state.waiting = False
        state.last_denied_prompt = time.monotonic()
        try:
            await channel.send(
                embed=info("Staying out", "Nobody consented, so I will not join or transcribe this session."),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass

    async def _join(self, guild: discord.Guild, channel: discord.VoiceChannel) -> None:
        if voice_recv is None:
            return
        state = self._state(guild.id)
        try:
            if guild.voice_client:
                await guild.voice_client.disconnect()
            vc = await channel.connect(cls=voice_recv.VoiceRecvClient, self_mute=True, self_deaf=False)
        except discord.HTTPException:
            log.exception("Failed to join voice %s", channel.id)
            return
        if not isinstance(vc, voice_recv.VoiceRecvClient):
            return
        if vc.is_listening():
            vc.stop_listening()
        vc.listen(DavePcmSink(self._on_packet))
        state.waiting = False
        state.target_id = channel.id
        try:
            await channel.send(
                embed=info(
                    "Listening",
                    f"Transcribing **{len(state.consented)}** consenting member(s) for stocks and analytics. "
                    f"New joiners: react {config.CONSENT_YES} on the consent message.",
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass
        log.info("Joined VC %s in guild %s (%s consented)", channel.id, guild.id, len(state.consented))

    async def _leave(self, guild: discord.Guild, reason: str) -> None:
        state = self._state(guild.id)
        if guild.voice_client:
            try:
                await guild.voice_client.disconnect()
            except discord.HTTPException:
                pass
        state.waiting = False
        state.consent_message_id = None
        state.consented.clear()
        state.declined.clear()
        state.prompted.clear()
        self._assemblers.pop(guild.id, None)
        log.info("Left voice in guild %s (%s)", guild.id, reason)

    def _on_packet(self, user, data) -> None:
        if user is None or getattr(user, "bot", False):
            return
        pcm = getattr(data, "pcm", None)
        if not pcm:
            return
        guild = getattr(user, "guild", None)
        if guild is None:
            return
        state = self.states.get(guild.id)
        if state is None or user.id not in state.consented:
            return
        buffers = self._assemblers.setdefault(guild.id, UtteranceAssembler())
        complete = buffers.push(user.id, pcm)
        if complete:
            try:
                self._packets.put_nowait((guild, user.id, complete))
            except asyncio.QueueFull:
                pass

    async def _transcribe_worker(self) -> None:
        from shadowmarket.stt import transcribe_pcm48

        while True:
            guild, user_id, pcm = await self._packets.get()
            try:
                text = await asyncio.to_thread(transcribe_pcm48, pcm)
            except Exception:
                log.exception("Transcription failed")
                continue
            if not text:
                continue
            member = guild.get_member(user_id) if hasattr(guild, "get_member") else None
            if member is None:
                continue
            channel = guild.voice_client.channel if guild.voice_client else None
            channel_id = str(channel.id) if channel else str(guild.id)
            await self.bot.ingest_spoken_text(guild, member, channel_id, text)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None or payload.user_id == (self.bot.user.id if self.bot.user else 0):
            return
        state = self.states.get(payload.guild_id)
        if state is None or payload.message_id != state.consent_message_id:
            return
        emoji = str(payload.emoji)
        if emoji == config.CONSENT_YES:
            state.consented.add(payload.user_id)
            state.declined.discard(payload.user_id)
        elif emoji == config.CONSENT_NO:
            state.declined.add(payload.user_id)
            state.consented.discard(payload.user_id)
        else:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        target = guild.get_channel(state.target_id) if state.target_id else None
        if isinstance(target, discord.VoiceChannel) and state.waiting:
            await self._maybe_join_after_consent(guild, target)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None:
            return
        state = self.states.get(payload.guild_id)
        if state is None or payload.message_id != state.consent_message_id:
            return
        if str(payload.emoji) == config.CONSENT_YES:
            state.consented.discard(payload.user_id)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot or member.guild is None:
            return
        state = self._state(member.guild.id)
        current = member.guild.voice_client.channel if member.guild.voice_client else None
        if current and after.channel == current and before.channel != current:
            await self._prompt_late_joiners(member.guild, current)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceListenCog(bot))
