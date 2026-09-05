"""Package entrypoint: python -m shadowmarket"""

from __future__ import annotations

import logging
import sys

from shadowmarket.bot import ShadowMarketBot
from shadowmarket.config import DISCORD_TOKEN


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not DISCORD_TOKEN:
        sys.exit("DISCORD_TOKEN is not set. Copy .env.example to .env and add your bot token.")
    bot = ShadowMarketBot()
    bot.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
