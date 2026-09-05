"""Market slash commands: /ipo /buy /sell /portfolio. All ephemeral."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from shadowmarket import config
from shadowmarket.database import InsufficientFunds, InsufficientShares, StockExists, UnknownStock
from shadowmarket.economy import initial_listing_price, ipo_cost
from shadowmarket.embeds import error, money, portfolio_embed, success
from shadowmarket.tokenizer import normalize_keyword


class MarketCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_app_command_error(
        self, interaction: discord.Interaction, err: app_commands.AppCommandError
    ) -> None:
        original = err.original if isinstance(err, app_commands.CommandInvokeError) else err
        message = str(original) if isinstance(original, (InsufficientFunds, InsufficientShares, StockExists, UnknownStock)) else "Something went wrong."
        embed = error("Trade failed", message)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="ipo", description="List a word or emoji as a new stock")
    @app_commands.describe(keyword="The inside joke, word, or emoji to list")
    @app_commands.guild_only()
    async def ipo(self, interaction: discord.Interaction, keyword: str) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id)
        user_id = str(interaction.user.id)
        normalized = normalize_keyword(keyword)
        if not normalized or len(normalized) > config.KEYWORD_MAX_LENGTH:
            await interaction.followup.send(
                embed=error("Invalid keyword", "Use a word, emoji, or :custom_emoji: up to 64 characters."),
                ephemeral=True,
            )
            return

        await self.bot.db.ensure_user(user_id, guild_id)
        n = await self.bot.db.active_stock_count(guild_id)
        cost = ipo_cost(n)
        price = initial_listing_price()
        await self.bot.db.create_stock(guild_id, normalized, user_id, price, cost)
        self.bot.cache.add_stock(guild_id, normalized)
        await interaction.followup.send(
            embed=success(
                "IPO complete",
                f"`{normalized}` is now trading at **{money(price)}**.\n"
                f"Listing fee: **{money(cost)}**",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="buy", description="Buy shares of a listed keyword")
    @app_commands.describe(stock="Listed keyword", amount="Number of shares")
    @app_commands.guild_only()
    async def buy(self, interaction: discord.Interaction, stock: str, amount: int) -> None:
        await interaction.response.defer(ephemeral=True)
        if amount < config.MIN_TRADE_SHARES or amount > config.MAX_TRADE_SHARES:
            await interaction.followup.send(
                embed=error("Invalid amount", f"Buy between {config.MIN_TRADE_SHARES} and {config.MAX_TRADE_SHARES} shares."),
                ephemeral=True,
            )
            return
        guild_id = str(interaction.guild_id)
        user_id = str(interaction.user.id)
        keyword = normalize_keyword(stock)
        await self.bot.db.ensure_user(user_id, guild_id)
        price, balance = await self.bot.db.buy_shares(user_id, guild_id, keyword, amount)
        await interaction.followup.send(
            embed=success(
                "Bought",
                f"Purchased **{amount}** share(s) of `{keyword}` at **{money(price)}**.\n"
                f"Cash remaining: **{money(balance)}**",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="sell", description="Sell shares of a listed keyword")
    @app_commands.describe(stock="A stock you own", amount="Number of shares to sell")
    @app_commands.guild_only()
    async def sell(self, interaction: discord.Interaction, stock: str, amount: int) -> None:
        await interaction.response.defer(ephemeral=True)
        if amount < config.MIN_TRADE_SHARES or amount > config.MAX_TRADE_SHARES:
            await interaction.followup.send(
                embed=error("Invalid amount", f"Sell between {config.MIN_TRADE_SHARES} and {config.MAX_TRADE_SHARES} shares."),
                ephemeral=True,
            )
            return
        guild_id = str(interaction.guild_id)
        user_id = str(interaction.user.id)
        keyword = normalize_keyword(stock)
        await self.bot.db.ensure_user(user_id, guild_id)
        price, balance = await self.bot.db.sell_shares(user_id, guild_id, keyword, amount)
        await interaction.followup.send(
            embed=success(
                "Sold",
                f"Sold **{amount}** share(s) of `{keyword}` at **{money(price)}**.\n"
                f"Cash: **{money(balance)}**",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="portfolio", description="View your cash, holdings, net worth, and ROI")
    @app_commands.guild_only()
    async def portfolio(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id)
        user_id = str(interaction.user.id)
        await self.bot.db.ensure_user(user_id, guild_id)
        snapshot = await self.bot.db.portfolio(user_id, guild_id)
        await interaction.followup.send(embed=portfolio_embed(snapshot), ephemeral=True)

    @buy.autocomplete("stock")
    async def buy_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._stock_choices(interaction, current, owned_only=False)

    @sell.autocomplete("stock")
    async def sell_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._stock_choices(interaction, current, owned_only=True)

    async def _stock_choices(
        self, interaction: discord.Interaction, current: str, *, owned_only: bool
    ) -> list[app_commands.Choice[str]]:
        if not interaction.guild_id:
            return []
        guild_id = str(interaction.guild_id)
        needle = current.lower()
        if owned_only:
            snapshot = await self.bot.db.portfolio(str(interaction.user.id), guild_id)
            keywords = [h.keyword for h in snapshot.holdings]
        else:
            keywords = sorted(self.bot.cache.stocks.get(guild_id, set()))
        matches = [k for k in keywords if needle in k.lower()][:25]
        return [app_commands.Choice(name=k, value=k) for k in matches]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MarketCog(bot))
