from pathlib import Path

import pytest

from shadowmarket.config import STARTING_BALANCE
from shadowmarket.database import Database, InsufficientFunds, StockExists
from shadowmarket.economy import ipo_cost


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()


async def test_starting_balance(db: Database):
    balance = await db.ensure_user("u1", "g1")
    assert balance == STARTING_BALANCE


async def test_ipo_buy_sell_roundtrip(db: Database):
    await db.ensure_user("u1", "g1")
    await db.create_stock("g1", "pineapple", "u1", 100.0, ipo_cost(0))
    balance = await db.get_balance("u1", "g1")
    assert balance == STARTING_BALANCE - 500

    price, balance = await db.buy_shares("u1", "g1", "pineapple", 2)
    assert price == 100
    assert balance == STARTING_BALANCE - 500 - 200

    price, balance = await db.sell_shares("u1", "g1", "pineapple", 2)
    assert price == 100
    assert balance == STARTING_BALANCE - 500


async def test_duplicate_ipo_rejected(db: Database):
    await db.ensure_user("u1", "g1")
    await db.create_stock("g1", "pineapple", "u1", 100.0, 500)
    with pytest.raises(StockExists):
        await db.create_stock("g1", "pineapple", "u1", 100.0, 50)


async def test_cannot_ipo_without_funds(db: Database):
    await db.ensure_user("u1", "g1")
    with pytest.raises(InsufficientFunds):
        await db.create_stock("g1", "word", "u1", 100.0, 50_000)


async def test_bounty_claim_is_zero_sum(db: Database):
    await db.ensure_user("placer", "g1")
    await db.ensure_user("target", "g1")
    from datetime import datetime, timedelta, timezone

    bounty_id = await db.place_bounty(
        "g1",
        "placer",
        "target",
        "pineapple",
        reward=100,
        cost=200,
        is_stealth=True,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    placer = await db.get_balance("placer", "g1")
    target = await db.get_balance("target", "g1")
    assert placer == STARTING_BALANCE - 200
    assert target == STARTING_BALANCE

    await db.claim_bounty(bounty_id, "target", "g1")
    placer = await db.get_balance("placer", "g1")
    target = await db.get_balance("target", "g1")
    assert placer == STARTING_BALANCE - 200
    assert target == STARTING_BALANCE + 100
    # 100 stealth fee burned — no new money minted
    assert placer + target == (STARTING_BALANCE * 2) - 100


async def test_wrong_suspect_fine(db: Database):
    await db.ensure_user("u1", "g1")
    taken = await db.fine_user("u1", "g1", 100)
    assert taken == 100
    assert await db.get_balance("u1", "g1") == STARTING_BALANCE - 100
