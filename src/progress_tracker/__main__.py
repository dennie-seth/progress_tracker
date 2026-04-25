"""Entry point: `python -m progress_tracker`."""

from __future__ import annotations

import asyncio

import structlog

from progress_tracker.bot import build_bot, build_dispatcher
from progress_tracker.config import load_settings
from progress_tracker.db.session import create_engine, create_session_factory
from progress_tracker.logging_setup import configure_logging
from progress_tracker.storage.local import LocalStorage


async def _run() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)

    log = structlog.get_logger("progress_tracker")
    log.info("starting bot", media_dir=str(settings.media_dir))

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    storage = LocalStorage(root=settings.media_dir)
    settings.media_dir.mkdir(parents=True, exist_ok=True)

    bot = build_bot(settings)
    dp = build_dispatcher(session_factory=session_factory, storage=storage)

    try:
        await dp.start_polling(bot, handle_signals=True)
    finally:
        # Run each cleanup independently so a failure in one doesn't skip the
        # others — `engine.dispose()` is the most important to reach because
        # it returns DB connections to the pool.
        try:
            await bot.session.close()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            log.warning("failed to close bot session", exc_info=True)
        try:
            await engine.dispose()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            log.warning("failed to dispose db engine", exc_info=True)


def main() -> None:
    """Synchronous entry used by the `progress-tracker` console script."""
    try:
        asyncio.run(_run())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
