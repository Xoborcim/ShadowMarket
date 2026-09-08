# ShadowMarket

A zero-spam Discord bot: a stock market for your server's inside jokes, plus a stealth bounty board.

The bot never DMs, never pings for trading, and never posts unsolicited chat messages except: the ticker (edited in place), the optional daily analytics post, and **voice consent pings** in a VC before recording.

## Game loop

1. `/ipo pineapple` lists a word. Everyone starts with $1,000.
2. `/buy` shares of that word. Price moves with how often people say it.
3. Place a `/bounty place` on someone to say the word. When they do, they silently collect the escrow — and the usage spike pumps the stock.
4. `/sell` into the pump. `/suspect` if you think someone put a stealth bounty on you.

## Setup

### 1. Create a Discord application

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and create an application.
2. Bot → Add Bot. Copy the token.
3. Bot → Privileged Gateway Intents → enable **Message Content Intent** (keyword volume + word analytics) and **Server Members Intent** (joins, retention, invite tracking).
4. Optional: enable **Presence Intent** and set `PRESENCE_INTENT=true` in `.env` to sample desktop/mobile/web.
5. OAuth2 → URL Generator:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: `View Channels`, `Send Messages`, `Embed Links`, `Read Message History`, `Connect`, `Speak`, `Manage Guild` (invite stats), `View Audit Log` (kick vs leave), `Manage Messages` (optional, only used if you re-run `/setup_ticker` in a new channel)
6. Invite the bot with the generated URL.

Slash commands are synced to each server on startup (they should appear within a few seconds). `/analytics` only shows for members with **Manage Server**. If the menu is empty or commands are duplicated, fully restart the bot, then restart the Discord client (Ctrl/Cmd+R). Re-invite with the `applications.commands` scope if the bot was added without it.

### 2. Run locally

```bash
cd ShadowMarket
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .   # re-run after pulling; voice needs extra packages
pip install -r requirements-dev.txt
cp .env.example .env
# paste DISCORD_TOKEN into .env
python -m shadowmarket
# first VC listen downloads the Whisper model (~150 MB for base.en)
```

### 3. Run with Docker

```bash
cp .env.example .env
# paste DISCORD_TOKEN into .env
docker compose up --build -d
```

SQLite lives in `./data/shadowmarket.db`.

## Commands

All replies are ephemeral except the ticker (edited in place), the optional daily analytics post, and voice consent prompts.

| Command | Who | What |
|---|---|---|
| `/ipo keyword` | Anyone | List a word/emoji. Cost = `$500 + ($50 × active listings)`. Opens at $100. |
| `/buy stock amount` | Anyone | Buy shares at the current price. |
| `/sell stock amount` | Anyone | Sell shares at the current price. |
| `/portfolio` | Anyone | Cash, positions, net worth, ROI. |
| `/bounty place target keyword reward stealth` | Anyone | Escrow `reward`. Stealth costs **2×** (extra is burned) and can be slashed. Lasts 24h. |
| `/suspect user` | Anyone | If they had a stealth bounty on you, you steal the escrow. If not, you are fined $100. |
| `/setup_ticker` | Manage Server | Posts the single public ticker and saves its message ID. Later updates only **edit** that message. |
| `/analytics report` | Manage Server | Full engagement report: volume, peak hour, top chatters, filtered vocabulary, pings, @everyone. |
| `/analytics setup` | Manage Server | Post that report in a channel every day at 3:00 AM (default `America/New_York`). |
| `/analytics chatters` | Manage Server | Who generates the conversation (counts + %). |
| `/analytics words` | Manage Server | Top meaningful words (NLTK-style stop words stripped) and longest-word record. |
| `/analytics pings` | Manage Server | Top pingers and most-mentioned members. |
| `/analytics massping` | Manage Server | Who uses `@everyone`. |
| `/analytics channels` | Manage Server | Most active text channels. |
| `/analytics schedule` | Manage Server | Busiest hours (UTC) and weekdays. |
| `/analytics voice` | Manage Server | Voice minutes and Voice XP (1 XP / minute). |
| `/analytics growth` | Manage Server | Member trend, 30-day newcomer retention, invite sources, account age at join. |
| `/analytics health` | Manage Server | 7-day engagement rate, boosts, bans/kicks, device sample. |
| `/analytics backfill` | Manage Server | Ingest historical messages from before this bot session (once per catch-up). |
| `/listen pause` | Manage Server | Stop joining VCs and asking for consent. |
| `/listen resume` | Manage Server | Resume joining the busiest VC after consent. |
| `/listen status` | Manage Server | Who is currently being transcribed. |

## Economy (from the spec)

Hourly price tick:

```
P_t = P_{t-1} + (V × 0.5) − (0.02 × P_{t-1})
```

- `V` is unique usage that hour (one hit per user per keyword per 60 seconds; repeats in one message do not stack). Multi-word listings like `hi back` match the whole phrase, not the separate words.
- Prices recast **once an hour**. The ticker shows pending uses this hour until that recast.
- Prices cannot fall below **$10**.
- If the server sends fewer than **50 messages/day**, decay pauses so inactive servers do not wipe the book.
- Bounties are zero-sum (or negative-sum with stealth fees). New money only comes from price appreciation.

Bounty states: `ACTIVE` → `CLAIMED` (target said the word; silent payout), `EXPIRED` (80% refund, 20% burned after 24h), `SLASHED` (correct `/suspect`; target takes 100% of escrow).

## Server analytics

Tracking is silent (zero-spam) until you opt into `/analytics setup`. Admins can still pull reports with `/analytics *`. Counters live in memory and flush to SQLite every 60 seconds.

Daily auto-report: `/analytics setup` in the target channel. At **3:00 AM** local (default Eastern), the bot posts one public embed with no pings. If the bot is offline at 3:00, it posts once after it comes back that day. Override the timezone with `REPORT_TZ` or the `timezone` argument (IANA names).

- **Chat:** messages, peak hour, top chatters, per-channel volume
- **Language:** stop-word filtered vocabulary (`just`, `yeah`, `the`, … dropped) plus longest-token record
- **Pings:** sent, received, `@everyone`
- **Voice:** minutes in voice channels → Voice XP; consented speech is transcribed into word stats and stock volume

Historical catch-up: `/analytics backfill` reads channel history from *before this process started* so it does not double-count live traffic.

## Voice transcription

The bot watches for the **busiest voice channel** (most non-bot members). Before joining it pings the people in that VC and asks them to react:

- ✅ — transcribe my voice for stocks and analytics
- ❌ — do not transcribe me

It joins after someone consents (or after 45s if anyone has). Only consenting users are sent through Whisper. Late joiners get their own ping pointing at the same consent message. Switch to a different VC only if it is clearly busier (2+ more people). First Whisper run downloads the `base.en` model. Disable with `VOICE_LISTEN=false` or `/listen pause`.

Meme phrases work the same as other stocks: `/ipo skibidi toilet` (or `rizz`, `tung tung tung sahur`, …). Typed chat matches the whole phrase. In VC, Whisper is biased toward **listed stocks** plus `WHISPER_HINTS` so those names are less likely to come out as gibberish.

### "back" jams

On startup the bot downloads [this playlist](https://www.youtube.com/playlist?list=PLWVz9oaYquijWE-lRZxFJd0op8D8qgo2H) into `data/back_jams/` (Docker: `/data/back_jams` on the homelab host). When someone says the whole word **back** in chat or consented VC speech, and the bot is already in a voice channel, it plays **30 seconds** of a random track. Cooldown is 15s; disable with `BACK_JAM_ENABLED=false`.

## Layout

```
src/shadowmarket/
  bot.py          # listener + command tree
  cache.py        # in-memory stocks/bounties/volume (no SQL on every message)
  database.py     # SQLite schema and transactions
  economy.py      # IPO cost + price formula
  tokenizer.py    # unique tokens, custom emoji
  analytics.py    # in-memory chat/voice/ping/word buffers
  stopwords.py    # NLTK English list + chat filler
  cogs/market.py  # /ipo /buy /sell /portfolio
  cogs/bounty.py  # /bounty place /suspect
  cogs/admin.py   # /setup_ticker
  cogs/analytics.py # /analytics * + voice/join listeners
  cogs/voice_listen.py # busiest-VC consent + Whisper
  cogs/tasks.py   # 60s flush, hourly prices, 15m ticker edit, expiry
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
