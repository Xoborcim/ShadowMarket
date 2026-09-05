"""Bounty board: /bounty place and /suspect. Claims are silent (zero-spam)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from shadowmarket import config
from shadowmarket.database import InsufficientFunds
from shadowmarket.embeds import error, money, success
from shadowmarket.models import BountyRecord
from shadowmarket.tokenizer import normalize_keyword


class BountyCog(commands.Cog):
    bounty = app_commands.Group(name="bounty", description="Place stealth or public bounties on keywords")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_app_command_error(
        self, interaction: discord.Interaction, err: app_commands.AppCommandError
    ) -> None:
        original = err.original if isinstance(err, app_commands.CommandInvokeError) else err
        message = str(original) if isinstance(original, InsufficientFunds) else "Something went wrong."
        embed = error("Bounty failed", message)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @bounty.command(name="place", description="Escrow a reward if the target says the keyword")
    @app_commands.describe(
        target="Who must say the keyword to claim",
        keyword="Word or emoji they have to say",
        reward="Escrowed payout (stealth costs 2× this)",
        stealth="Hide the bounty; costs 2× and can be slashed with /suspect",
    )
    @app_commands.guild_only()
    async def place(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        keyword: str,
        reward: int,
        stealth: bool = False,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id)
        placer_id = str(interaction.user.id)
        target_id = str(target.id)

        if target_id == placer_id:
            await interaction.followup.send(
                embed=error("Invalid target", "You cannot place a bounty on yourself."),
                ephemeral=True,
            )
            return
        if target.bot:
            await interaction.followup.send(
                embed=error("Invalid target", "Bounties cannot be placed on bots."),
                ephemeral=True,
            )
            return
        if reward < config.MIN_BOUNTY_REWARD or reward > config.MAX_BOUNTY_REWARD:
            await interaction.followup.send(
                embed=error("Invalid reward", "Reward is outside the allowed range."),
                ephemeral=True,
            )
            return

        normalized = normalize_keyword(keyword)
        if not normalized or len(normalized) > config.KEYWORD_MAX_LENGTH:
            await interaction.followup.send(
                embed=error("Invalid keyword", "Use a word, emoji, or :custom_emoji: up to 64 characters."),
                ephemeral=True,
            )
            return

        cost = float(reward) * (config.STEALTH_COST_MULTIPLIER if stealth else 1.0)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=config.BOUNTY_DURATION_HOURS)

        await self.bot.db.ensure_user(placer_id, guild_id)
        bounty_id = await self.bot.db.place_bounty(
            guild_id=guild_id,
            placer_id=placer_id,
            target_id=target_id,
            keyword=normalized,
            reward=float(reward),
            cost=cost,
            is_stealth=stealth,
            expires_at=expires_at,
        )
        record = BountyRecord(
            bounty_id=bounty_id,
            guild_id=guild_id,
            placer_id=placer_id,
            target_id=target_id,
            keyword=normalized,
            reward_amount=float(reward),
            is_stealth=stealth,
            status="ACTIVE",
            expires_at=expires_at,
        )
        self.bot.cache.add_bounty(record)

        extra = ""
        if stealth:
            extra = (
                f"\nStealth fee burned: **{money(cost - reward)}**. "
                "If they `/suspect` you correctly, they steal the escrow."
            )
        await interaction.followup.send(
            embed=success(
                "Bounty armed",
                f"{'Stealth' if stealth else 'Public'} bounty on `{normalized}` for **{target.display_name}**.\n"
                f"Escrow: **{money(reward)}** · Paid: **{money(cost)}** · Expires in 24h."
                f"{extra}",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="suspect", description="Accuse someone of placing a stealth bounty on you")
    @app_commands.describe(user="The person you think is hunting you")
    @app_commands.guild_only()
    async def suspect(self, interaction: discord.Interaction, user: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id)
        target_id = str(interaction.user.id)
        accused_id = str(user.id)

        if accused_id == target_id:
            await interaction.followup.send(
                embed=error("Nice try", "You cannot suspect yourself."),
                ephemeral=True,
            )
            return

        await self.bot.db.ensure_user(target_id, guild_id)
        hits = self.bot.cache.stealth_bounties_from(guild_id, accused_id, target_id)
        if not hits:
            taken = await self.bot.db.fine_user(target_id, guild_id, config.SUSPECT_FAIL_FINE)
            await interaction.followup.send(
                embed=error(
                    "Wrong suspect",
                    f"No stealth bounty from **{user.display_name}** on you. "
                    f"Fined **{money(taken)}**.",
                ),
                ephemeral=True,
            )
            return

        stolen = 0.0
        for bounty in hits:
            payout = await self.bot.db.slash_bounty(bounty.bounty_id, target_id, guild_id)
            if payout is not None:
                self.bot.cache.remove_bounty(guild_id, bounty.bounty_id)
                stolen += payout

        await interaction.followup.send(
            embed=success(
                "Bounty slashed",
                f"You caught **{user.display_name}**. Escrow stolen: **{money(stolen)}**.",
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BountyCog(bot))
