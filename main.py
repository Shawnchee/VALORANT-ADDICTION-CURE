"""Entry point for the Valorant Session Agent.

Run it:  python main.py

On launch it elevates via UAC (so it can manage processes and firewall rules),
then starts the tray agent. The tray icon shows games remaining and turns red
when you hit your limit.
"""

import logging
import os

from valorant_limiter import config
from valorant_limiter.app import run
from valorant_limiter.elevation import ensure_admin


def _setup_logging() -> None:
    os.makedirs(config.app_data_dir(), exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(
                os.path.join(config.app_data_dir(), "agent.log"), encoding="utf-8"
            ),
        ],
    )


def main() -> None:
    # Elevate FIRST — before touching state or the tray — so the elevated
    # instance is the only one that sets anything up.
    ensure_admin()
    _setup_logging()
    logging.getLogger(__name__).info("starting %s", config.APP_NAME)
    run()


if __name__ == "__main__":
    main()
