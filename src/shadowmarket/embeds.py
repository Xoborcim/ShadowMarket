"""Ephemeral embed helpers. Public output is limited to the ticker edit loop."""

from __future__ import annotations

from datetime import datetime, timezone

import discord

from shadowmarket.economy import percent_change
from shadowmarket.models import PortfolioSnapshot


GREEN = discord.Color.from_rgb(46, 204, 113)
RED = discord.Color.from_rgb(231, 76, 60)
GOLD = discord.Color.from_rgb(241, 196, 15)
BLURPLE = discord.Color.blurple()


def money(value: float) -> str:
    return f"${value:,.2f}"


def signed_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def success(title: str, body: str) -> discord.Embed:
    return discord.Embed(title=title, description=body, color=GREEN)


def error(title: str, body: str) -> discord.Embed:
    return discord.Embed(title=title, description=body, color=RED)


def info(title: str, body: str = "") -> discord.Embed:
    return discord.Embed(title=title, description=body, color=BLURPLE)


def portfolio_embed(snapshot: PortfolioSnapshot) -> discord.Embed:
    embed = discord.Embed(
        title="Your Portfolio",
        color=GOLD,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Cash", value=money(snapshot.cash), inline=True)
    embed.add_field(name="Holdings", value=money(snapshot.holdings_value), inline=True)
    embed.add_field(name="Net Worth", value=money(snapshot.net_worth), inline=True)
    embed.add_field(name="ROI", value=signed_pct(snapshot.roi_pct), inline=True)

    if snapshot.holdings:
        lines = []
        for holding in snapshot.holdings[:15]:
            lines.append(
                f"`{holding.keyword}` ×{holding.shares} @ {money(holding.current_price)}  "
                f"({signed_pct(holding.roi_pct)})"
            )
        embed.add_field(name="Positions", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Positions", value="No shares yet. Try `/buy`.", inline=False)

    embed.set_footer(text="Visible only to you")
    return embed


def ticker_embed(
    stocks: list[dict],
    public_bounties: int,
    richest: list[tuple[str, float]],
    user_resolver,
) -> discord.Embed:
    """Build the single public ticker message."""
    changes = [percent_change(s["current_price"], s["previous_price"]) for s in stocks]
    market_delta = sum(changes) if changes else 0.0
    color = GREEN if market_delta > 0 else RED if market_delta < 0 else GOLD

    embed = discord.Embed(
        title="📈 ShadowMarket Ticker",
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    if not stocks:
        embed.add_field(name="🚀 Top Gainers", value="No listings yet. `/ipo` a word.", inline=False)
        embed.add_field(name="📉 Top Losers", value="—", inline=False)
    else:
        ranked = sorted(
            stocks,
            key=lambda s: percent_change(s["current_price"], s["previous_price"]),
            reverse=True,
        )
        gainers = [s for s in ranked if percent_change(s["current_price"], s["previous_price"]) >= 0][:5]
        losers = [s for s in ranked if percent_change(s["current_price"], s["previous_price"]) < 0]
        losers = list(reversed(losers[-5:]))

        embed.add_field(
            name="🚀 Top Gainers",
            value=_stock_lines(gainers) or "No gainers this hour.",
            inline=False,
        )
        embed.add_field(
            name="📉 Top Losers",
            value=_stock_lines(losers) or "No losers this hour.",
            inline=False,
        )

    embed.add_field(
        name="🎯 Public Bounties Active",
        value=str(public_bounties),
        inline=True,
    )

    if richest:
        rich_lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, (user_id, worth) in enumerate(richest):
            name = user_resolver(user_id)
            rich_lines.append(f"{medals[i]} {name} — {money(worth)}")
        embed.add_field(name="💰 Server Richest", value="\n".join(rich_lines), inline=False)
    else:
        embed.add_field(name="💰 Server Richest", value="No traders yet.", inline=False)

    embed.set_footer(text="Prices recast hourly from unique uses · Last updated")
    return embed


def _pending_suffix(stock: dict) -> str:
    pending = int(stock.get("pending_volume") or 0)
    if pending <= 0:
        return ""
    noun = "use" if pending == 1 else "uses"
    return f"  · {pending} {noun} this hour"


def _stock_lines(stocks: list[dict]) -> str:
    if not stocks:
        return ""
    lines = []
    for stock in stocks:
        pct = percent_change(stock["current_price"], stock["previous_price"])
        lines.append(
            f"`{stock['keyword']}`  {money(stock['current_price'])}  {signed_pct(pct)}"
            + _pending_suffix(stock)
        )
    return "\n".join(lines)
