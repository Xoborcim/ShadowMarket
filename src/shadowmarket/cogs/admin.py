"""Admin-only ticker bootstrap. The only command allowed to post a public message."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from shadowmarket.embeds import error, success, ticker_embed


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="setup_ticker",
        description="Post the single public ticker message in this channel and keep editing it",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_ticker(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                embed=error("Wrong channel", "Run this in a text channel."),
                ephemeral=True,
            )
            return

        guild_id = str(interaction.guild_id)
        stocks = await self.bot.db.list_active_stocks(guild_id)
        stock_dicts = [
            {
                "keyword": row["keyword"],
                "current_price": float(row["current_price"]),
                "previous_price": float(row["previous_price"]),
                "pending_volume": self.bot.cache.period_volume.get(
                    (guild_id, row["keyword"]), 0
                ),
            }
            for row in stocks
        ]
        richest = await self.bot.db.net_worths(guild_id, 3)
        guild = interaction.guild
        embed = ticker_embed(
            stock_dicts,
            self.bot.cache.public_bounty_count(guild_id),
            richest,
            lambda uid: self.bot.trader_name(guild, uid),
        )

        existing = await self.bot.db.get_server_config(guild_id)
        message: discord.Message | None = None
        if existing and existing["ticker_channel_id"] and existing["ticker_message_id"]:
            try:
                old_channel = self.bot.get_channel(int(existing["ticker_channel_id"]))
                if isinstance(old_channel, discord.TextChannel):
                    old_message = await old_channel.fetch_message(int(existing["ticker_message_id"]))
                    if old_channel.id == channel.id:
                        await old_message.edit(embed=embed)
                        message = old_message
                    else:
                        await old_message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None

        if message is None:
            message = await channel.send(embed=embed)

        await self.bot.db.upsert_ticker(guild_id, str(channel.id), str(message.id))
        await interaction.followup.send(
            embed=success(
                "Ticker online",
                f"The market board is {message.jump_url}. It will be edited in place — no extra messages.",
            ),
            ephemeral=True,
        )

    @setup_ticker.error
    async def setup_ticker_error(
        self, interaction: discord.Interaction, err: app_commands.AppCommandError
    ) -> None:
        if isinstance(err, app_commands.MissingPermissions):
            embed = error("Denied", "You need **Manage Server** to set up the ticker.")
        else:
            embed = error("Setup failed", "Could not post or edit the ticker message.")
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
