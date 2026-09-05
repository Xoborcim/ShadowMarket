from datetime import datetime, timezone

from shadowmarket.analytics import AnalyticsBuffer, ChatEvent, lexical_words, longest_raw_token
from shadowmarket.stopwords import STOP_WORDS


def test_stop_words_drop_filler():
    words = lexical_words("I just like yeah playing minecraft on the server")
    assert "just" not in words
    assert "yeah" not in words
    assert "like" not in words
    assert "playing" in words
    assert "minecraft" in words
    assert "server" in words


def test_stop_words_include_nltk_core():
    assert "the" in STOP_WORDS
    assert "and" in STOP_WORDS
    assert "minecraft" not in STOP_WORDS


def test_lexical_ignores_urls_and_mentions():
    words = lexical_words("check https://example.com <@123> pokemon raid")
    assert words == ["check", "pokemon", "raid"]


def test_longest_word_record():
    smash = "a" * 288
    token = longest_raw_token(f"hello {smash} world")
    assert token == smash
    assert len(token) == 288


def test_ingest_counts_unique_chat_and_pings():
    buf = AnalyticsBuffer()
    now = datetime(2026, 1, 27, 18, 0, tzinfo=timezone.utc)
    buf.ingest(
        ChatEvent(
            guild_id="g",
            channel_id="c",
            user_id="alice",
            content="hey @bob playing minecraft",
            created_at=now,
            mentioned_ids=["bob"],
            mention_everyone=False,
        )
    )
    buf.ingest(
        ChatEvent(
            guild_id="g",
            channel_id="c",
            user_id="alice",
            content="@everyone raid tonight",
            created_at=now,
            mentioned_ids=[],
            mention_everyone=True,
        )
    )
    assert buf.messages[("g", "alice")] == 2
    assert buf.channels[("g", "c")] == 2
    assert buf.ping_sent[("g", "alice")] == 1
    assert buf.ping_received[("g", "bob")] == 1
    assert buf.everyone[("g", "alice")] == 1
    assert buf.hours[("g", 1, 18)] == 2  # 2026-01-27 is a Tuesday
    assert buf.words[("g", "minecraft")] == 1
    assert buf.words[("g", "raid")] == 1
    assert buf.words[("g", "hey")] == 0


def test_voice_xp_is_minutes():
    buf = AnalyticsBuffer()
    buf.start_voice("g", "u", now=0)
    minutes = buf.stop_voice("g", "u", now=180)
    assert minutes == 3
    assert buf.voice_minutes[("g", "u")] == 3


def test_daily_report_posts_once_after_3am():
    from shadowmarket.analytics import last_report_date_after_setup, should_post_daily_report

    before = datetime(2026, 1, 27, 2, 59, tzinfo=timezone.utc)
    at = datetime(2026, 1, 27, 3, 0, tzinfo=timezone.utc)
    later = datetime(2026, 1, 27, 15, 0, tzinfo=timezone.utc)
    assert should_post_daily_report(before, None) is False
    assert should_post_daily_report(at, None) is True
    assert should_post_daily_report(later, "2026-01-27") is False
    assert last_report_date_after_setup(before) is None
    assert last_report_date_after_setup(later) == "2026-01-27"
