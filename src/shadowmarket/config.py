"""Tunable economy constants from the ShadowMarket specification."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DEV_GUILD_ID = int(os.getenv("DEV_GUILD_ID")) if os.getenv("DEV_GUILD_ID") else None
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "data/shadowmarket.db"))

STARTING_BALANCE = 1000.0
IPO_BASE_COST = 500.0
IPO_MULTIPLIER = 50.0
INITIAL_PRICE = 100.0
PRICE_FLOOR = 10.0
VOLATILITY_ALPHA = 0.5
DECAY_RATE = 0.02

VOLUME_COOLDOWN_SECONDS = 60
FLUSH_INTERVAL_SECONDS = 60
PRICE_UPDATE_HOURS = 1
TICKER_UPDATE_MINUTES = 15
EXPIRY_CHECK_SECONDS = 60

BOUNTY_DURATION_HOURS = 24
BOUNTY_EXPIRE_REFUND_RATE = 0.80
STEALTH_COST_MULTIPLIER = 2.0
SUSPECT_FAIL_FINE = 100.0

DEAD_SERVER_DAILY_MESSAGES = 50

KEYWORD_MAX_LENGTH = 64
MAX_TRADE_SHARES = 10_000
MAX_BOUNTY_REWARD = 1_000_000
MIN_TRADE_SHARES = 1
MIN_BOUNTY_REWARD = 1
REPORT_HOUR = 3
REPORT_MINUTE = 0
REPORT_TZ = os.getenv("REPORT_TZ", "America/New_York")

VOICE_LISTEN = os.getenv("VOICE_LISTEN", "true").lower() not in {"0", "false", "no"}
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base.en")
_DEFAULT_WHISPER_HINTS = "rizz, skibidi, skibidi toilet, tung tung tung sahur, gyatt, sigma, ohio"
WHISPER_HINTS = [
    part.strip().lower()
    for part in os.getenv("WHISPER_HINTS", _DEFAULT_WHISPER_HINTS).split(",")
    if part.strip()
]
WHISPER_HINT_MAX_CHARS = 350
VOICE_POLL_SECONDS = 20
VOICE_CONSENT_SECONDS = 45
VOICE_PROMPT_COOLDOWN = 600
VOICE_SWITCH_MARGIN = 2
CONSENT_YES = "✅"
CONSENT_NO = "❌"

# "back" soundboard: download a YouTube playlist and play a short clip in VC.
BACK_JAM_ENABLED = os.getenv("BACK_JAM_ENABLED", "true").lower() not in {"0", "false", "no"}
BACK_JAM_PLAYLIST_URL = os.getenv(
    "BACK_JAM_PLAYLIST_URL",
    "https://www.youtube.com/playlist?list=PLWVz9oaYquijWE-lRZxFJd0op8D8qgo2H",
)
BACK_JAM_DIR = Path(os.getenv("BACK_JAM_DIR", str(DATABASE_PATH.parent / "back_jams")))
BACK_JAM_CLIP_SECONDS = int(os.getenv("BACK_JAM_CLIP_SECONDS", "30"))
BACK_JAM_COOLDOWN_SECONDS = float(os.getenv("BACK_JAM_COOLDOWN_SECONDS", "15"))
BACK_JAM_TRIGGER = os.getenv("BACK_JAM_TRIGGER", "back").strip().lower() or "back"
