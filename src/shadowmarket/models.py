"""In-memory records used by the message listener and background tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class BountyRecord:
    bounty_id: int
    guild_id: str
    placer_id: str
    target_id: str | None
    keyword: str
    reward_amount: float
    is_stealth: bool
    status: str
    expires_at: datetime


@dataclass(slots=True)
class Holding:
    keyword: str
    shares: int
    average_buy_price: float
    current_price: float

    @property
    def market_value(self) -> float:
        return self.shares * self.current_price

    @property
    def cost_basis(self) -> float:
        return self.shares * self.average_buy_price

    @property
    def roi_pct(self) -> float:
        if self.average_buy_price <= 0:
            return 0.0
        return ((self.current_price - self.average_buy_price) / self.average_buy_price) * 100.0


@dataclass(slots=True)
class PortfolioSnapshot:
    cash: float
    holdings: list[Holding] = field(default_factory=list)

    @property
    def holdings_value(self) -> float:
        return sum(h.market_value for h in self.holdings)

    @property
    def net_worth(self) -> float:
        return self.cash + self.holdings_value

    @property
    def cost_basis(self) -> float:
        return sum(h.cost_basis for h in self.holdings)

    @property
    def roi_pct(self) -> float:
        basis = self.cost_basis
        if basis <= 0:
            return 0.0
        return ((self.holdings_value - basis) / basis) * 100.0
