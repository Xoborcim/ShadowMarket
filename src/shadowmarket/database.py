"""Async SQLite access. Commands and tasks talk to this; on_message never does."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
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
    daily_count_date TEXT,
    report_channel_id TEXT,
    last_report_date TEXT,
    report_timezone TEXT
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

ANALYTICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS UserActivity (
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    message_count INTEGER DEFAULT 0,
    ping_sent INTEGER DEFAULT 0,
    ping_received INTEGER DEFAULT 0,
    everyone_sent INTEGER DEFAULT 0,
    last_message_at TEXT,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS ChannelActivity (
    guild_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    message_count INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS HourlyActivity (
    guild_id TEXT NOT NULL,
    weekday INTEGER NOT NULL,
    hour INTEGER NOT NULL,
    message_count INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, weekday, hour)
);

CREATE TABLE IF NOT EXISTS WordStats (
    guild_id TEXT NOT NULL,
    word TEXT NOT NULL COLLATE NOCASE,
    count INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, word)
);

CREATE TABLE IF NOT EXISTS LongestWord (
    guild_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    word TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS VoiceStats (
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    minutes REAL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS MemberEvents (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    account_created_at TEXT,
    invite_code TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS InviteStats (
    guild_id TEXT NOT NULL,
    code TEXT NOT NULL,
    inviter_id TEXT,
    uses INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, code)
);

CREATE TABLE IF NOT EXISTS DailyMembers (
    guild_id TEXT NOT NULL,
    day TEXT NOT NULL,
    member_count INTEGER NOT NULL,
    PRIMARY KEY (guild_id, day)
);

CREATE TABLE IF NOT EXISTS DeviceStats (
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    desktop INTEGER DEFAULT 0,
    mobile INTEGER DEFAULT 0,
    web INTEGER DEFAULT 0,
    last_seen TEXT,
    PRIMARY KEY (guild_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_user_activity_guild ON UserActivity(guild_id);
CREATE INDEX IF NOT EXISTS idx_word_stats_guild ON WordStats(guild_id, count);
CREATE INDEX IF NOT EXISTS idx_member_events_guild ON MemberEvents(guild_id, event_type);
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
        await self._conn.executescript(ANALYTICS_SCHEMA)
        await self._migrate()
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

    async def _migrate(self) -> None:
        async with self.conn.execute("PRAGMA table_info(ServerConfig)") as cursor:
            existing = {row[1] for row in await cursor.fetchall()}
        for column, spec in (
            ("report_channel_id", "TEXT"),
            ("last_report_date", "TEXT"),
            ("report_timezone", "TEXT"),
        ):
            if column not in existing:
                await self.conn.execute(f"ALTER TABLE ServerConfig ADD COLUMN {column} {spec}")

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

    async def upsert_daily_report(
        self,
        guild_id: str,
        channel_id: str | None,
        timezone_name: str | None,
        last_report_date: str | None,
    ) -> None:
        async with self._lock:
            await self.conn.execute(
                """
                INSERT INTO ServerConfig (guild_id, report_channel_id, report_timezone, last_report_date)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    report_channel_id = excluded.report_channel_id,
                    report_timezone = COALESCE(excluded.report_timezone, ServerConfig.report_timezone),
                    last_report_date = excluded.last_report_date
                """,
                (guild_id, channel_id, timezone_name, last_report_date),
            )
            await self.conn.commit()

    async def mark_daily_report_sent(self, guild_id: str, day: str) -> None:
        async with self._lock:
            await self.conn.execute(
                """
                INSERT INTO ServerConfig (guild_id, last_report_date)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET last_report_date = excluded.last_report_date
                """,
                (guild_id, day),
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

    async def apply_analytics_flush(self, flush) -> None:
        from shadowmarket.analytics import AnalyticsFlush

        if not isinstance(flush, AnalyticsFlush):
            return
        async with self._lock:
            for (guild_id, user_id), count in flush.messages.items():
                last = flush.last_message.get((guild_id, user_id))
                await self.conn.execute(
                    """
                    INSERT INTO UserActivity (guild_id, user_id, message_count, last_message_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        message_count = message_count + excluded.message_count,
                        last_message_at = CASE
                            WHEN excluded.last_message_at IS NOT NULL
                             AND (UserActivity.last_message_at IS NULL
                                  OR excluded.last_message_at > UserActivity.last_message_at)
                            THEN excluded.last_message_at
                            ELSE UserActivity.last_message_at
                        END
                    """,
                    (guild_id, user_id, count, last),
                )
            for (guild_id, user_id), count in flush.ping_sent.items():
                await self.conn.execute(
                    """
                    INSERT INTO UserActivity (guild_id, user_id, ping_sent)
                    VALUES (?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        ping_sent = ping_sent + excluded.ping_sent
                    """,
                    (guild_id, user_id, count),
                )
            for (guild_id, user_id), count in flush.ping_received.items():
                await self.conn.execute(
                    """
                    INSERT INTO UserActivity (guild_id, user_id, ping_received)
                    VALUES (?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        ping_received = ping_received + excluded.ping_received
                    """,
                    (guild_id, user_id, count),
                )
            for (guild_id, user_id), count in flush.everyone.items():
                await self.conn.execute(
                    """
                    INSERT INTO UserActivity (guild_id, user_id, everyone_sent)
                    VALUES (?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        everyone_sent = everyone_sent + excluded.everyone_sent
                    """,
                    (guild_id, user_id, count),
                )
            for (guild_id, channel_id), count in flush.channels.items():
                await self.conn.execute(
                    """
                    INSERT INTO ChannelActivity (guild_id, channel_id, message_count)
                    VALUES (?, ?, ?)
                    ON CONFLICT(guild_id, channel_id) DO UPDATE SET
                        message_count = message_count + excluded.message_count
                    """,
                    (guild_id, channel_id, count),
                )
            for (guild_id, weekday, hour), count in flush.hours.items():
                await self.conn.execute(
                    """
                    INSERT INTO HourlyActivity (guild_id, weekday, hour, message_count)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(guild_id, weekday, hour) DO UPDATE SET
                        message_count = message_count + excluded.message_count
                    """,
                    (guild_id, weekday, hour, count),
                )
            for (guild_id, word), count in flush.words.items():
                await self.conn.execute(
                    """
                    INSERT INTO WordStats (guild_id, word, count)
                    VALUES (?, ?, ?)
                    ON CONFLICT(guild_id, word) DO UPDATE SET
                        count = count + excluded.count
                    """,
                    (guild_id, word, count),
                )
            for guild_id, (user_id, word, char_count) in flush.longest.items():
                existing = await self._fetchone(
                    "SELECT char_count FROM LongestWord WHERE guild_id = ?",
                    (guild_id,),
                )
                if existing is None or char_count > int(existing["char_count"]):
                    await self.conn.execute(
                        """
                        INSERT INTO LongestWord (guild_id, user_id, word, char_count, recorded_at)
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(guild_id) DO UPDATE SET
                            user_id = excluded.user_id,
                            word = excluded.word,
                            char_count = excluded.char_count,
                            recorded_at = CURRENT_TIMESTAMP
                        """,
                        (guild_id, user_id, word, char_count),
                    )
            for (guild_id, user_id), minutes in flush.voice_minutes.items():
                await self.conn.execute(
                    """
                    INSERT INTO VoiceStats (guild_id, user_id, minutes)
                    VALUES (?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        minutes = minutes + excluded.minutes
                    """,
                    (guild_id, user_id, minutes),
                )
            now = _iso(_utc_now())
            for (guild_id, user_id), (desktop, mobile, web) in flush.devices.items():
                await self.conn.execute(
                    """
                    INSERT INTO DeviceStats (guild_id, user_id, desktop, mobile, web, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        desktop = excluded.desktop,
                        mobile = excluded.mobile,
                        web = excluded.web,
                        last_seen = excluded.last_seen
                    """,
                    (guild_id, user_id, desktop, mobile, web, now),
                )
            await self.conn.commit()

    async def analytics_overview(self, guild_id: str) -> dict:
        total = await self._fetchone(
            "SELECT COALESCE(SUM(message_count), 0) AS n FROM UserActivity WHERE guild_id = ?",
            (guild_id,),
        )
        pings = await self._fetchone(
            "SELECT COALESCE(SUM(ping_sent), 0) AS n FROM UserActivity WHERE guild_id = ?",
            (guild_id,),
        )
        speakers = await self._fetchone(
            "SELECT COUNT(*) AS n FROM UserActivity WHERE guild_id = ? AND message_count > 0",
            (guild_id,),
        )
        peak = await self._fetchone(
            """
            SELECT hour, SUM(message_count) AS n
            FROM HourlyActivity
            WHERE guild_id = ?
            GROUP BY hour
            ORDER BY n DESC
            LIMIT 1
            """,
            (guild_id,),
        )
        return {
            "messages": int(total["n"]) if total else 0,
            "pings": int(pings["n"]) if pings else 0,
            "speakers": int(speakers["n"]) if speakers else 0,
            "peak_hour": int(peak["hour"]) if peak else None,
            "peak_volume": int(peak["n"]) if peak else 0,
        }

    async def top_chatters(self, guild_id: str, limit: int = 5) -> list[aiosqlite.Row]:
        return await self._fetchall(
            """
            SELECT user_id, message_count
            FROM UserActivity
            WHERE guild_id = ? AND message_count > 0
            ORDER BY message_count DESC
            LIMIT ?
            """,
            (guild_id, limit),
        )

    async def top_words(self, guild_id: str, limit: int = 10) -> list[aiosqlite.Row]:
        return await self._fetchall(
            """
            SELECT word, count FROM WordStats
            WHERE guild_id = ?
            ORDER BY count DESC
            LIMIT ?
            """,
            (guild_id, limit),
        )

    async def top_pingers(self, guild_id: str, limit: int = 5) -> list[aiosqlite.Row]:
        return await self._fetchall(
            """
            SELECT user_id, ping_sent AS n FROM UserActivity
            WHERE guild_id = ? AND ping_sent > 0
            ORDER BY ping_sent DESC LIMIT ?
            """,
            (guild_id, limit),
        )

    async def top_ping_victims(self, guild_id: str, limit: int = 5) -> list[aiosqlite.Row]:
        return await self._fetchall(
            """
            SELECT user_id, ping_received AS n FROM UserActivity
            WHERE guild_id = ? AND ping_received > 0
            ORDER BY ping_received DESC LIMIT ?
            """,
            (guild_id, limit),
        )

    async def top_everyone(self, guild_id: str, limit: int = 5) -> list[aiosqlite.Row]:
        return await self._fetchall(
            """
            SELECT user_id, everyone_sent AS n FROM UserActivity
            WHERE guild_id = ? AND everyone_sent > 0
            ORDER BY everyone_sent DESC LIMIT ?
            """,
            (guild_id, limit),
        )

    async def top_channels(self, guild_id: str, limit: int = 10) -> list[aiosqlite.Row]:
        return await self._fetchall(
            """
            SELECT channel_id, message_count FROM ChannelActivity
            WHERE guild_id = ? AND message_count > 0
            ORDER BY message_count DESC LIMIT ?
            """,
            (guild_id, limit),
        )

    async def hourly_totals(self, guild_id: str) -> list[aiosqlite.Row]:
        return await self._fetchall(
            """
            SELECT hour, SUM(message_count) AS n
            FROM HourlyActivity WHERE guild_id = ?
            GROUP BY hour ORDER BY hour
            """,
            (guild_id,),
        )

    async def weekday_totals(self, guild_id: str) -> list[aiosqlite.Row]:
        return await self._fetchall(
            """
            SELECT weekday, SUM(message_count) AS n
            FROM HourlyActivity WHERE guild_id = ?
            GROUP BY weekday ORDER BY weekday
            """,
            (guild_id,),
        )

    async def longest_word(self, guild_id: str) -> aiosqlite.Row | None:
        return await self._fetchone(
            "SELECT user_id, word, char_count, recorded_at FROM LongestWord WHERE guild_id = ?",
            (guild_id,),
        )

    async def all_longest_floors(self) -> dict[str, int]:
        rows = await self._fetchall("SELECT guild_id, char_count FROM LongestWord")
        return {row["guild_id"]: int(row["char_count"]) for row in rows}

    async def top_voice(self, guild_id: str, limit: int = 10) -> list[aiosqlite.Row]:
        return await self._fetchall(
            """
            SELECT user_id, minutes FROM VoiceStats
            WHERE guild_id = ? AND minutes > 0
            ORDER BY minutes DESC LIMIT ?
            """,
            (guild_id, limit),
        )

    async def record_member_event(
        self,
        guild_id: str,
        user_id: str,
        event_type: str,
        account_created_at: str | None = None,
        invite_code: str | None = None,
    ) -> None:
        async with self._lock:
            await self.conn.execute(
                """
                INSERT INTO MemberEvents (guild_id, user_id, event_type, account_created_at, invite_code)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, event_type, account_created_at, invite_code),
            )
            await self.conn.commit()

    async def member_event_counts(self, guild_id: str) -> dict[str, int]:
        rows = await self._fetchall(
            """
            SELECT event_type, COUNT(*) AS n
            FROM MemberEvents WHERE guild_id = ?
            GROUP BY event_type
            """,
            (guild_id,),
        )
        return {row["event_type"]: int(row["n"]) for row in rows}

    async def bump_invite(self, guild_id: str, code: str, inviter_id: str | None, delta: int = 1) -> None:
        async with self._lock:
            await self.conn.execute(
                """
                INSERT INTO InviteStats (guild_id, code, inviter_id, uses)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, code) DO UPDATE SET
                    uses = uses + excluded.uses,
                    inviter_id = COALESCE(excluded.inviter_id, InviteStats.inviter_id)
                """,
                (guild_id, code, inviter_id, delta),
            )
            await self.conn.commit()

    async def top_invites(self, guild_id: str, limit: int = 10) -> list[aiosqlite.Row]:
        return await self._fetchall(
            """
            SELECT code, inviter_id, uses FROM InviteStats
            WHERE guild_id = ? AND uses > 0
            ORDER BY uses DESC LIMIT ?
            """,
            (guild_id, limit),
        )

    async def snapshot_members(self, guild_id: str, day: str, count: int) -> None:
        async with self._lock:
            await self.conn.execute(
                """
                INSERT INTO DailyMembers (guild_id, day, member_count)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, day) DO UPDATE SET member_count = excluded.member_count
                """,
                (guild_id, day, count),
            )
            await self.conn.commit()

    async def member_history(self, guild_id: str, limit: int = 30) -> list[aiosqlite.Row]:
        return await self._fetchall(
            """
            SELECT day, member_count FROM DailyMembers
            WHERE guild_id = ?
            ORDER BY day DESC LIMIT ?
            """,
            (guild_id, limit),
        )

    async def retention(self, guild_id: str, days: int = 30) -> tuple[int, int]:
        horizon = _utc_now() - timedelta(days=days)
        joined = await self._fetchall(
            """
            SELECT user_id, created_at FROM MemberEvents
            WHERE guild_id = ? AND event_type = 'JOIN'
            ORDER BY created_at DESC
            """,
            (guild_id,),
        )
        recent = []
        for row in joined:
            created = _parse_ts(row["created_at"])
            if created >= horizon:
                recent.append(str(row["user_id"]))
        if not recent:
            return 0, 0
        placeholders = ",".join("?" * len(recent))
        active = await self._fetchone(
            f"""
            SELECT COUNT(*) AS n FROM UserActivity
            WHERE guild_id = ? AND message_count > 0 AND user_id IN ({placeholders})
            """,
            (guild_id, *recent),
        )
        return len(recent), int(active["n"]) if active else 0

    async def active_since(self, guild_id: str, since_iso: str) -> int:
        row = await self._fetchone(
            """
            SELECT COUNT(*) AS n FROM UserActivity
            WHERE guild_id = ? AND last_message_at IS NOT NULL AND last_message_at >= ?
            """,
            (guild_id, since_iso),
        )
        return int(row["n"]) if row else 0

    async def device_totals(self, guild_id: str) -> tuple[int, int, int, int]:
        row = await self._fetchone(
            """
            SELECT
                COALESCE(SUM(desktop), 0) AS desktop,
                COALESCE(SUM(mobile), 0) AS mobile,
                COALESCE(SUM(web), 0) AS web,
                COUNT(*) AS n
            FROM DeviceStats WHERE guild_id = ?
            """,
            (guild_id,),
        )
        if not row:
            return 0, 0, 0, 0
        return int(row["desktop"]), int(row["mobile"]), int(row["web"]), int(row["n"])

    async def account_age_buckets(self, guild_id: str) -> dict[str, int]:
        rows = await self._fetchall(
            """
            SELECT account_created_at FROM MemberEvents
            WHERE guild_id = ? AND event_type = 'JOIN' AND account_created_at IS NOT NULL
            """,
            (guild_id,),
        )
        buckets = {"< 7 days": 0, "7-30 days": 0, "1-12 months": 0, "1+ years": 0}
        now = _utc_now()
        for row in rows:
            created = _parse_ts(row["account_created_at"])
            age = (now - created).days
            if age < 7:
                buckets["< 7 days"] += 1
            elif age < 30:
                buckets["7-30 days"] += 1
            elif age < 365:
                buckets["1-12 months"] += 1
            else:
                buckets["1+ years"] += 1
        return buckets


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
