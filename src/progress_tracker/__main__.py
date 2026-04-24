"""Entry point: `python -m progress_tracker`."""

from __future__ import annotations

import asyncio

import structlog

from progress_tracker.bot import build_bot, build_dispatcher
from progress_tracker.config import load_settings
from progress_tracker.logging_setup import configure_logging


async def _run() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)

    log = structlog.get_logger("progress_tracker")
    log.info("starting bot", media_dir=str(settings.media_dir))

    bot = build_bot(settings)
    dp = build_dispatcher()

    try:
        await dp.start_polling(bot, handle_signals=True)
    finally:
        await bot.session.close()


def main() -> None:
    """Synchronous entry used by the `progress-tracker` console script."""
    try:
        asyncio.run(_run())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
