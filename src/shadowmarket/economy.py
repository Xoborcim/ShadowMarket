"""Economy formulas from the specification."""

from __future__ import annotations

from shadowmarket.config import (
    DECAY_RATE,
    INITIAL_PRICE,
    IPO_BASE_COST,
    IPO_MULTIPLIER,
    PRICE_FLOOR,
    VOLATILITY_ALPHA,
)


def ipo_cost(active_stock_count: int) -> float:
    """C_ipo = B + (N × M)."""
    return IPO_BASE_COST + (active_stock_count * IPO_MULTIPLIER)


def next_price(
    previous_price: float,
    period_volume: int,
    *,
    decay_paused: bool = False,
    alpha: float = VOLATILITY_ALPHA,
    decay_rate: float = DECAY_RATE,
    floor: float = PRICE_FLOOR,
) -> float:
    """P_t = P_{t-1} + (V × α) − (D × P_{t-1}), clamped to the price floor."""
    decay = 0.0 if decay_paused else decay_rate
    raw = previous_price + (period_volume * alpha) - (decay * previous_price)
    return max(floor, raw)


def weighted_average_buy_price(
    existing_shares: int,
    existing_avg: float,
    bought_shares: int,
    buy_price: float,
) -> float:
    total = existing_shares + bought_shares
    if total <= 0:
        return 0.0
    return ((existing_shares * existing_avg) + (bought_shares * buy_price)) / total


def percent_change(current: float, previous: float) -> float:
    if previous <= 0:
        return 0.0
    return ((current - previous) / previous) * 100.0


def initial_listing_price() -> float:
    return INITIAL_PRICE
