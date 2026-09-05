"""Async SQLite access. Commands and tasks talk to this; on_message never does."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path

import aiosqlite

from shadowmarket.config import STARTING_BALANCE
from shadowmarket.models import BountyRecord, Holding, PortfolioSnapshot

SCHEMA = """
CREATE TABLE IF NOT EXISTS Users (
    user_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    balance REAL DEFAULT 1000.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, guild_id)
);

CREATE TABLE IF NOT EXISTS Stocks (
    stock_id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    keyword TEXT NOT NULL COLLATE NOCASE,
    creator_id TEXT,
    current_price REAL DEFAULT 100.0,
    previous_price REAL DEFAULT 100.0,
    total_volume INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(guild_id, keyword)
);

CREATE TABLE IF NOT EXISTS Portfolios (
    portfolio_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    stock_id INTEGER NOT NULL,
    shares_owned INTEGER DEFAULT 0,
    average_buy_price REAL DEFAULT 0.0,
    FOREIGN KEY(stock_id) REFERENCES Stocks(stock_id),
    UNIQUE(user_id, guild_id, stock_id)
);

CREATE TABLE IF NOT EXISTS Bounties (
    bounty_id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    placer_id TEXT NOT NULL,
    target_id TEXT,
    keyword TEXT NOT NULL,
    reward_amount REAL NOT NULL,
    is_stealth BOOLEAN DEFAULT FALSE,
    status TEXT DEFAULT 'ACTIVE',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS ServerConfig (
    guild_id TEXT PRIMARY KEY,
    ticker_channel_id TEXT,
    ticker_message_id TEXT,
    daily_message_count INTEGER DEFAULT 0,
    daily_count_date TEXT
);

CREATE TABLE IF NOT EXISTS PriceHistory (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id INTEGER NOT NULL,
    price REAL NOT NULL,
    volume INTEGER NOT NULL,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(stock_id) REFERENCES Stocks(stock_id)
);

CREATE INDEX IF NOT EXISTS idx_stocks_guild ON Stocks(guild_id, is_active);
CREATE INDEX IF NOT EXISTS idx_bounties_guild_status ON Bounties(guild_id, status);
CREATE INDEX IF NOT EXISTS idx_portfolios_user ON Portfolios(user_id, guild_id);
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _parse_ts(value: str | datetime | None) -> datetime:
    if value is None:
        return _utc_now()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA busy_timeout = 5000")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def ensure_user(self, user_id: str, guild_id: str) -> float:
        async with self._lock:
            await self.conn.execute(
                """
                INSERT OR IGNORE INTO Users (user_id, guild_id, balance)
                VALUES (?, ?, ?)
                """,
                (user_id, guild_id, STARTING_BALANCE),
            )
            await self.conn.commit()
            row = await self._fetchone(
                "SELECT balance FROM Users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            return float(row["balance"]) if row else STARTING_BALANCE

    async def get_balance(self, user_id: str, guild_id: str) -> float:
        row = await self._fetchone(
            "SELECT balance FROM Users WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        return float(row["balance"]) if row else 0.0

    async def list_active_stocks(self, guild_id: str) -> list[aiosqlite.Row]:
        return await self._fetchall(
            """
            SELECT stock_id, keyword, current_price, previous_price, total_volume, creator_id
            FROM Stocks
            WHERE guild_id = ? AND is_active = 1
            ORDER BY keyword COLLATE NOCASE
            """,
            (guild_id,),
        )

    async def all_active_stock_keywords(self) -> list[tuple[str, str]]:
        rows = await self._fetchall(
            "SELECT guild_id, keyword FROM Stocks WHERE is_active = 1"
        )
        return [(row["guild_id"], row["keyword"]) for row in rows]

    async def active_stock_count(self, guild_id: str) -> int:
        row = await self._fetchone(
            "SELECT COUNT(*) AS n FROM Stocks WHERE guild_id = ? AND is_active = 1",
            (guild_id,),
        )
        return int(row["n"]) if row else 0

    async def get_stock(self, guild_id: str, keyword: str) -> aiosqlite.Row | None:
        return await self._fetchone(
            """
            SELECT * FROM Stocks
            WHERE guild_id = ? AND keyword = ? COLLATE NOCASE AND is_active = 1
            """,
            (guild_id, keyword),
        )

    async def create_stock(
        self, guild_id: str, keyword: str, creator_id: str, price: float, cost: float
    ) -> None:
        async with self._lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                user = await self._fetchone(
                    "SELECT balance FROM Users WHERE user_id = ? AND guild_id = ?",
                    (creator_id, guild_id),
                )
                if user is None or float(user["balance"]) < cost:
                    raise InsufficientFunds(cost, float(user["balance"]) if user else 0.0)
                existing = await self._fetchone(
                    "SELECT stock_id FROM Stocks WHERE guild_id = ? AND keyword = ? COLLATE NOCASE",
                    (guild_id, keyword),
                )
                if existing:
                    raise StockExists(keyword)
                await self.conn.execute(
                    """
                    INSERT INTO Stocks (guild_id, keyword, creator_id, current_price, previous_price)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (guild_id, keyword, creator_id, price, price),
                )
                await self.conn.execute(
                    "UPDATE Users SET balance = balance - ? WHERE user_id = ? AND guild_id = ?",
                    (cost, creator_id, guild_id),
                )
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise

    async def buy_shares(
        self,
        user_id: str,
        guild_id: str,
        keyword: str,
        amount: int,
    ) -> tuple[float, float]:
        async with self._lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                stock = await self._fetchone(
                    """
                    SELECT stock_id, current_price FROM Stocks
                    WHERE guild_id = ? AND keyword = ? COLLATE NOCASE AND is_active = 1
                    """,
                    (guild_id, keyword),
                )
                if stock is None:
                    raise UnknownStock(keyword)
                price = float(stock["current_price"])
                cost = price * amount
                user = await self._fetchone(
                    "SELECT balance FROM Users WHERE user_id = ? AND guild_id = ?",
                    (user_id, guild_id),
                )
                if user is None or float(user["balance"]) < cost:
                    raise InsufficientFunds(cost, float(user["balance"]) if user else 0.0)
                holding = await self._fetchone(
                    """
                    SELECT shares_owned, average_buy_price FROM Portfolios
                    WHERE user_id = ? AND guild_id = ? AND stock_id = ?
                    """,
                    (user_id, guild_id, stock["stock_id"]),
                )
                existing_shares = int(holding["shares_owned"]) if holding else 0
                existing_avg = float(holding["average_buy_price"]) if holding else 0.0
                total_shares = existing_shares + amount
                new_avg = (
                    ((existing_shares * existing_avg) + (amount * price)) / total_shares
                    if total_shares
                    else price
                )
                await self.conn.execute(
                    "UPDATE Users SET balance = balance - ? WHERE user_id = ? AND guild_id = ?",
                    (cost, user_id, guild_id),
                )
                await self.conn.execute(
                    """
                    INSERT INTO Portfolios (user_id, guild_id, stock_id, shares_owned, average_buy_price)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, guild_id, stock_id) DO UPDATE SET
                        shares_owned = shares_owned + excluded.shares_owned,
                        average_buy_price = excluded.average_buy_price
                    """,
                    (user_id, guild_id, stock["stock_id"], amount, new_avg),
                )
                await self.conn.commit()
                new_balance = float(user["balance"]) - cost
                return price, new_balance
            except Exception:
                await self.conn.rollback()
                raise

    async def get_holding(
        self, user_id: str, guild_id: str, keyword: str
    ) -> aiosqlite.Row | None:
        return await self._fetchone(
            """
            SELECT p.shares_owned, p.average_buy_price, s.stock_id, s.current_price
            FROM Portfolios p
            JOIN Stocks s ON s.stock_id = p.stock_id
            WHERE p.user_id = ? AND p.guild_id = ? AND s.keyword = ? COLLATE NOCASE
            """,
            (user_id, guild_id, keyword),
        )

    async def sell_shares(
        self, user_id: str, guild_id: str, keyword: str, amount: int
    ) -> tuple[float, float]:
        async with self._lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                holding = await self._fetchone(
                    """
                    SELECT p.portfolio_id, p.shares_owned, s.current_price
                    FROM Portfolios p
                    JOIN Stocks s ON s.stock_id = p.stock_id
                    WHERE p.user_id = ? AND p.guild_id = ?
                      AND s.keyword = ? COLLATE NOCASE
                    """,
                    (user_id, guild_id, keyword),
                )
                if holding is None or int(holding["shares_owned"]) < amount:
                    owned = int(holding["shares_owned"]) if holding else 0
                    raise InsufficientShares(keyword, amount, owned)
                price = float(holding["current_price"])
                proceeds = price * amount
                remaining = int(holding["shares_owned"]) - amount
                if remaining == 0:
                    await self.conn.execute(
                        "DELETE FROM Portfolios WHERE portfolio_id = ?",
                        (holding["portfolio_id"],),
                    )
                else:
                    await self.conn.execute(
                        "UPDATE Portfolios SET shares_owned = ? WHERE portfolio_id = ?",
                        (remaining, holding["portfolio_id"]),
                    )
                await self.conn.execute(
                    "UPDATE Users SET balance = balance + ? WHERE user_id = ? AND guild_id = ?",
                    (proceeds, user_id, guild_id),
                )
                await self.conn.commit()
                row = await self._fetchone(
                    "SELECT balance FROM Users WHERE user_id = ? AND guild_id = ?",
                    (user_id, guild_id),
                )
                return price, float(row["balance"]) if row else proceeds
            except Exception:
                await self.conn.rollback()
                raise

    async def portfolio(self, user_id: str, guild_id: str) -> PortfolioSnapshot:
        cash = await self.get_balance(user_id, guild_id)
        rows = await self._fetchall(
            """
            SELECT s.keyword, p.shares_owned, p.average_buy_price, s.current_price
            FROM Portfolios p
            JOIN Stocks s ON s.stock_id = p.stock_id
            WHERE p.user_id = ? AND p.guild_id = ? AND p.shares_owned > 0
            ORDER BY s.keyword COLLATE NOCASE
            """,
            (user_id, guild_id),
        )
        holdings = [
            Holding(
                keyword=row["keyword"],
                shares=int(row["shares_owned"]),
                average_buy_price=float(row["average_buy_price"]),
                current_price=float(row["current_price"]),
            )
            for row in rows
        ]
        return PortfolioSnapshot(cash=cash, holdings=holdings)

    async def net_worths(self, guild_id: str, limit: int = 3) -> list[tuple[str, float]]:
        rows = await self._fetchall(
            """
            SELECT u.user_id,
                   u.balance + COALESCE(SUM(p.shares_owned * s.current_price), 0) AS net_worth
            FROM Users u
            LEFT JOIN Portfolios p ON p.user_id = u.user_id AND p.guild_id = u.guild_id
            LEFT JOIN Stocks s ON s.stock_id = p.stock_id
            WHERE u.guild_id = ?
            GROUP BY u.user_id
            ORDER BY net_worth DESC
            LIMIT ?
            """,
            (guild_id, limit),
        )
        return [(row["user_id"], float(row["net_worth"])) for row in rows]

    async def apply_volume_flush(self, updates: dict[tuple[str, str], int]) -> None:
        if not updates:
            return
        async with self._lock:
            for (guild_id, keyword), volume in updates.items():
                await self.conn.execute(
                    """
                    UPDATE Stocks
                    SET total_volume = total_volume + ?
                    WHERE guild_id = ? AND keyword = ? COLLATE NOCASE
                    """,
                    (volume, guild_id, keyword),
                )
            await self.conn.commit()

    async def apply_price_tick(
        self, guild_id: str, keyword: str, new_price: float, volume: int
    ) -> None:
        async with self._lock:
            stock = await self._fetchone(
                "SELECT stock_id, current_price FROM Stocks WHERE guild_id = ? AND keyword = ? COLLATE NOCASE",
                (guild_id, keyword),
            )
            if stock is None:
                return
            await self.conn.execute(
                """
                UPDATE Stocks
                SET previous_price = current_price, current_price = ?
                WHERE stock_id = ?
                """,
                (new_price, stock["stock_id"]),
            )
            await self.conn.execute(
                "INSERT INTO PriceHistory (stock_id, price, volume) VALUES (?, ?, ?)",
                (stock["stock_id"], new_price, volume),
            )
            await self.conn.commit()

    async def place_bounty(
        self,
        guild_id: str,
        placer_id: str,
        target_id: str | None,
        keyword: str,
        reward: float,
        cost: float,
        is_stealth: bool,
        expires_at: datetime,
    ) -> int:
        async with self._lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                user = await self._fetchone(
                    "SELECT balance FROM Users WHERE user_id = ? AND guild_id = ?",
                    (placer_id, guild_id),
                )
                if user is None or float(user["balance"]) < cost:
                    raise InsufficientFunds(cost, float(user["balance"]) if user else 0.0)
                await self.conn.execute(
                    "UPDATE Users SET balance = balance - ? WHERE user_id = ? AND guild_id = ?",
                    (cost, placer_id, guild_id),
                )
                cursor = await self.conn.execute(
                    """
                    INSERT INTO Bounties (
                        guild_id, placer_id, target_id, keyword, reward_amount,
                        is_stealth, status, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
                    """,
                    (
                        guild_id,
                        placer_id,
                        target_id,
                        keyword,
                        reward,
                        int(is_stealth),
                        _iso(expires_at),
                    ),
                )
                await self.conn.commit()
                return int(cursor.lastrowid)
            except Exception:
                await self.conn.rollback()
                raise

    async def list_active_bounties(self) -> list[BountyRecord]:
        rows = await self._fetchall("SELECT * FROM Bounties WHERE status = 'ACTIVE'")
        return [self._bounty_from_row(row) for row in rows]

    async def claim_bounty(self, bounty_id: int, claimant_id: str, guild_id: str) -> float | None:
        async with self._lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = await self._fetchone(
                    "SELECT * FROM Bounties WHERE bounty_id = ? AND status = 'ACTIVE'",
                    (bounty_id,),
                )
                if row is None:
                    await self.conn.rollback()
                    return None
                reward = float(row["reward_amount"])
                await self.conn.execute(
                    "UPDATE Bounties SET status = 'CLAIMED' WHERE bounty_id = ?",
                    (bounty_id,),
                )
                await self.conn.execute(
                    """
                    INSERT OR IGNORE INTO Users (user_id, guild_id, balance)
                    VALUES (?, ?, ?)
                    """,
                    (claimant_id, guild_id, STARTING_BALANCE),
                )
                await self.conn.execute(
                    "UPDATE Users SET balance = balance + ? WHERE user_id = ? AND guild_id = ?",
                    (reward, claimant_id, guild_id),
                )
                await self.conn.commit()
                return reward
            except Exception:
                await self.conn.rollback()
                raise

    async def slash_bounty(self, bounty_id: int, target_id: str, guild_id: str) -> float | None:
        async with self._lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = await self._fetchone(
                    "SELECT * FROM Bounties WHERE bounty_id = ? AND status = 'ACTIVE'",
                    (bounty_id,),
                )
                if row is None:
                    await self.conn.rollback()
                    return None
                reward = float(row["reward_amount"])
                await self.conn.execute(
                    "UPDATE Bounties SET status = 'SLASHED' WHERE bounty_id = ?",
                    (bounty_id,),
                )
                await self.conn.execute(
                    """
                    INSERT OR IGNORE INTO Users (user_id, guild_id, balance)
                    VALUES (?, ?, ?)
                    """,
                    (target_id, guild_id, STARTING_BALANCE),
                )
                await self.conn.execute(
                    "UPDATE Users SET balance = balance + ? WHERE user_id = ? AND guild_id = ?",
                    (reward, target_id, guild_id),
                )
                await self.conn.commit()
                return reward
            except Exception:
                await self.conn.rollback()
                raise

    async def expire_bounty(self, bounty_id: int, refund: float) -> bool:
        async with self._lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = await self._fetchone(
                    "SELECT * FROM Bounties WHERE bounty_id = ? AND status = 'ACTIVE'",
                    (bounty_id,),
                )
                if row is None:
                    await self.conn.rollback()
                    return False
                await self.conn.execute(
                    "UPDATE Bounties SET status = 'EXPIRED' WHERE bounty_id = ?",
                    (bounty_id,),
                )
                if refund > 0:
                    await self.conn.execute(
                        "UPDATE Users SET balance = balance + ? WHERE user_id = ? AND guild_id = ?",
                        (refund, row["placer_id"], row["guild_id"]),
                    )
                await self.conn.commit()
                return True
            except Exception:
                await self.conn.rollback()
                raise

    async def fine_user(self, user_id: str, guild_id: str, amount: float) -> float:
        async with self._lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                await self.conn.execute(
                    """
                    INSERT OR IGNORE INTO Users (user_id, guild_id, balance)
                    VALUES (?, ?, ?)
                    """,
                    (user_id, guild_id, STARTING_BALANCE),
                )
                row = await self._fetchone(
                    "SELECT balance FROM Users WHERE user_id = ? AND guild_id = ?",
                    (user_id, guild_id),
                )
                balance = float(row["balance"]) if row else 0.0
                taken = min(balance, amount)
                await self.conn.execute(
                    "UPDATE Users SET balance = balance - ? WHERE user_id = ? AND guild_id = ?",
                    (taken, user_id, guild_id),
                )
                await self.conn.commit()
                return taken
            except Exception:
                await self.conn.rollback()
                raise

    async def expired_active_bounties(self, now: datetime) -> list[BountyRecord]:
        rows = await self._fetchall(
            "SELECT * FROM Bounties WHERE status = 'ACTIVE' AND expires_at <= ?",
            (_iso(now),),
        )
        return [self._bounty_from_row(row) for row in rows]

    async def get_server_config(self, guild_id: str) -> aiosqlite.Row | None:
        return await self._fetchone(
            "SELECT * FROM ServerConfig WHERE guild_id = ?",
            (guild_id,),
        )

    async def all_server_configs(self) -> list[aiosqlite.Row]:
        return await self._fetchall("SELECT * FROM ServerConfig")

    async def upsert_ticker(self, guild_id: str, channel_id: str, message_id: str) -> None:
        async with self._lock:
            await self.conn.execute(
                """
                INSERT INTO ServerConfig (guild_id, ticker_channel_id, ticker_message_id)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    ticker_channel_id = excluded.ticker_channel_id,
                    ticker_message_id = excluded.ticker_message_id
                """,
                (guild_id, channel_id, message_id),
            )
            await self.conn.commit()

    async def save_daily_counts(self, counts: dict[str, tuple[date, int]]) -> None:
        if not counts:
            return
        async with self._lock:
            for guild_id, (day, count) in counts.items():
                await self.conn.execute(
                    """
                    INSERT INTO ServerConfig (guild_id, daily_message_count, daily_count_date)
                    VALUES (?, ?, ?)
                    ON CONFLICT(guild_id) DO UPDATE SET
                        daily_message_count = excluded.daily_message_count,
                        daily_count_date = excluded.daily_count_date
                    """,
                    (guild_id, count, day.isoformat()),
                )
            await self.conn.commit()

    async def load_daily_counts(self) -> dict[str, tuple[date, int]]:
        rows = await self._fetchall(
            "SELECT guild_id, daily_message_count, daily_count_date FROM ServerConfig"
        )
        result: dict[str, tuple[date, int]] = {}
        today = date.today()
        for row in rows:
            if not row["daily_count_date"]:
                continue
            day = date.fromisoformat(row["daily_count_date"])
            count = int(row["daily_message_count"] or 0)
            if day != today:
                count = 0
                day = today
            result[row["guild_id"]] = (day, count)
        return result

    def _bounty_from_row(self, row: aiosqlite.Row) -> BountyRecord:
        return BountyRecord(
            bounty_id=int(row["bounty_id"]),
            guild_id=str(row["guild_id"]),
            placer_id=str(row["placer_id"]),
            target_id=str(row["target_id"]) if row["target_id"] else None,
            keyword=str(row["keyword"]),
            reward_amount=float(row["reward_amount"]),
            is_stealth=bool(row["is_stealth"]),
            status=str(row["status"]),
            expires_at=_parse_ts(row["expires_at"]),
        )

    async def _fetchone(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def _fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cursor:
            return await cursor.fetchall()


class EconomyError(Exception):
    pass


class InsufficientFunds(EconomyError):
    def __init__(self, required: float, available: float) -> None:
        super().__init__(f"Need ${required:,.2f}, have ${available:,.2f}")
        self.required = required
        self.available = available


class InsufficientShares(EconomyError):
    def __init__(self, keyword: str, requested: int, owned: int) -> None:
        super().__init__(f"You own {owned} share(s) of {keyword}, not {requested}")
        self.keyword = keyword
        self.requested = requested
        self.owned = owned


class StockExists(EconomyError):
    def __init__(self, keyword: str) -> None:
        super().__init__(f"`{keyword}` is already listed")
        self.keyword = keyword


class UnknownStock(EconomyError):
    def __init__(self, keyword: str) -> None:
        super().__init__(f"`{keyword}` is not listed")
        self.keyword = keyword
