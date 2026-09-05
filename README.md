# ShadowMarket

A zero-spam Discord bot: a stock market for your server's inside jokes, plus a stealth bounty board.

The bot never DMs, never pings, and never posts unsolicited chat messages. Trading happens through ephemeral slash commands (only you see them). The only public surface is a single ticker embed that is **edited in place**.

## Game loop

1. `/ipo pineapple` lists a word. Everyone starts with $1,000.
2. `/buy` shares of that word. Price moves with how often people say it.
3. Place a `/bounty place` on someone to say the word. When they do, they silently collect the escrow — and the usage spike pumps the stock.
4. `/sell` into the pump. `/suspect` if you think someone put a stealth bounty on you.

## Setup

### 1. Create a Discord application

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and create an application.
2. Bot → Add Bot. Copy the token.
3. Bot → Privileged Gateway Intents → enable **Message Content Intent** (required so the bot can count keyword usage).
4. OAuth2 → URL Generator:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: `View Channels`, `Send Messages`, `Embed Links`, `Read Message History`, `Manage Messages` (optional, only used if you re-run `/setup_ticker` in a new channel)
5. Invite the bot with the generated URL.

### 2. Run locally

```bash
cd ShadowMarket
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -r requirements-dev.txt
cp .env.example .env
# paste DISCORD_TOKEN into .env
# optional while iterating: DEV_GUILD_ID=<your server id> so slash commands appear instantly
python -m shadowmarket
```

### 3. Run with Docker

```bash
cp .env.example .env
# paste DISCORD_TOKEN into .env
docker compose up --build -d
```

SQLite lives in `./data/shadowmarket.db`.

## Commands

All replies are ephemeral except the one-time ticker post.

| Command | Who | What |
|---|---|---|
| `/ipo keyword` | Anyone | List a word/emoji. Cost = `$500 + ($50 × active listings)`. Opens at $100. |
| `/buy stock amount` | Anyone | Buy shares at the current price. |
| `/sell stock amount` | Anyone | Sell shares at the current price. |
| `/portfolio` | Anyone | Cash, positions, net worth, ROI. |
| `/bounty place target keyword reward stealth` | Anyone | Escrow `reward`. Stealth costs **2×** (extra is burned) and can be slashed. Lasts 24h. |
| `/suspect user` | Anyone | If they had a stealth bounty on you, you steal the escrow. If not, you are fined $100. |
| `/setup_ticker` | Manage Server | Posts the single public ticker and saves its message ID. Later updates only **edit** that message. |

## Economy (from the spec)

Hourly price tick:

```
P_t = P_{t-1} + (V × 0.5) − (0.02 × P_{t-1})
```

- `V` is unique usage that hour (one hit per user per keyword per 60 seconds; repeats in one message do not stack).
- Prices cannot fall below **$10**.
- If the server sends fewer than **50 messages/day**, decay pauses so inactive servers do not wipe the book.
- Bounties are zero-sum (or negative-sum with stealth fees). New money only comes from price appreciation.

Bounty states: `ACTIVE` → `CLAIMED` (target said the word; silent payout), `EXPIRED` (80% refund, 20% burned after 24h), `SLASHED` (correct `/suspect`; target takes 100% of escrow).

## Layout

```
src/shadowmarket/
  bot.py          # listener + command tree
  cache.py        # in-memory stocks/bounties/volume (no SQL on every message)
  database.py     # SQLite schema and transactions
  economy.py      # IPO cost + price formula
  tokenizer.py    # unique tokens, custom emoji
  cogs/market.py  # /ipo /buy /sell /portfolio
  cogs/bounty.py  # /bounty place /suspect
  cogs/admin.py   # /setup_ticker
  cogs/tasks.py   # 60s flush, hourly prices, 15m ticker edit, expiry
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
