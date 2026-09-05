from shadowmarket.cache import MarketCache
from shadowmarket.config import VOLUME_COOLDOWN_SECONDS
from shadowmarket.models import BountyRecord
from datetime import datetime, timedelta, timezone


def test_one_volume_hit_per_user_per_minute():
    cache = MarketCache()
    cache.add_stock("1", "pineapple")

    assert cache.hit_stock("1", "user", "pineapple", now=0) is True
    assert cache.hit_stock("1", "user", "pineapple", now=30) is False
    assert cache.hit_stock("1", "user", "pineapple", now=VOLUME_COOLDOWN_SECONDS) is True
    assert cache.period_volume[("1", "pineapple")] == 2


def test_different_users_count_separately():
    cache = MarketCache()
    cache.add_stock("1", "pineapple")
    assert cache.hit_stock("1", "a", "pineapple", now=0)
    assert cache.hit_stock("1", "b", "pineapple", now=0)
    assert cache.period_volume[("1", "pineapple")] == 2


def test_unknown_keyword_is_ignored():
    cache = MarketCache()
    cache.add_stock("1", "pineapple")
    assert cache.hit_stock("1", "user", "lmao", now=0) is False
    assert cache.period_volume[("1", "lmao")] == 0


def test_cannot_claim_own_bounty():
    cache = MarketCache()
    cache.add_bounty(
        BountyRecord(
            bounty_id=1,
            guild_id="g",
            placer_id="alice",
            target_id="bob",
            keyword="pineapple",
            reward_amount=50,
            is_stealth=True,
            status="ACTIVE",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    assert cache.matching_claimable("g", "pineapple", "alice") == []
    claims = cache.matching_claimable("g", "pineapple", "bob")
    assert len(claims) == 1


def test_public_bounty_count_excludes_stealth():
    cache = MarketCache()
    now = datetime.now(timezone.utc) + timedelta(hours=1)
    cache.add_bounty(
        BountyRecord(1, "g", "a", "b", "x", 10, True, "ACTIVE", now)
    )
    cache.add_bounty(
        BountyRecord(2, "g", "a", "c", "y", 10, False, "ACTIVE", now)
    )
    assert cache.public_bounty_count("g") == 1
