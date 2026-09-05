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


async def test_analytics_flush_and_overview(db: Database):
    from shadowmarket.analytics import AnalyticsFlush

    flush = AnalyticsFlush(
        messages={("g1", "u1"): 10, ("g1", "u2"): 5},
        channels={("g1", "c1"): 15},
        ping_sent={("g1", "u1"): 3},
        ping_received={("g1", "u2"): 3},
        everyone={("g1", "u1"): 2},
        hours={("g1", 1, 18): 15},
        words={("g1", "minecraft"): 8, ("g1", "raid"): 2},
        last_message={("g1", "u1"): "2026-01-27T18:00:00"},
        longest={"g1": ("u1", "a" * 50, 50)},
        voice_minutes={("g1", "u2"): 12.5},
        devices={("g1", "u1"): (1, 0, 0)},
    )
    await db.apply_analytics_flush(flush)
    overview = await db.analytics_overview("g1")
    assert overview["messages"] == 15
    assert overview["pings"] == 3
    assert overview["peak_hour"] == 18
    words = await db.top_words("g1", 2)
    assert words[0]["word"] == "minecraft"
    longest = await db.longest_word("g1")
    assert int(longest["char_count"]) == 50
    voice = await db.top_voice("g1")
    assert float(voice[0]["minutes"]) == 12.5
    everyone = await db.top_everyone("g1")
    assert int(everyone[0]["n"]) == 2


async def test_daily_report_config(db: Database):
    await db.upsert_daily_report("g1", "111", "America/New_York", "2026-01-27")
    row = await db.get_server_config("g1")
    assert row["report_channel_id"] == "111"
    assert row["report_timezone"] == "America/New_York"
    await db.mark_daily_report_sent("g1", "2026-01-28")
    row = await db.get_server_config("g1")
    assert row["last_report_date"] == "2026-01-28"
    await db.upsert_daily_report("g1", None, "America/New_York", None)
    row = await db.get_server_config("g1")
    assert row["report_channel_id"] is None
