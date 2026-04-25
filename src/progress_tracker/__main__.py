"""Entry point: `python -m progress_tracker`."""

from __future__ import annotations

import asyncio
import signal
from typing import Any

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

    # Install our own SIGINT/SIGTERM handlers so `docker compose stop` and
    # Ctrl+C land in `dp.stop_polling` cleanly and our `finally` block runs
    # with visible log lines. aiogram's built-in `handle_signals=True` was
    # observed to skip our cleanup logs in the docker stack, hence this
    # explicit setup.
    loop = asyncio.get_running_loop()

    def _request_stop(sig: signal.Signals) -> None:
        log.info("signal received, stopping polling", signal=sig.name)
        # `stop_polling` is a coroutine; schedule it on the loop.
        loop.create_task(dp.stop_polling())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop, sig)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler; fall back to default
            # SIG behavior. We never run on bare Windows in production (only
            # inside Linux containers via Rancher Desktop), so this is just a
            # safety net for ad-hoc local runs.
            log.debug("loop.add_signal_handler unsupported", signal=sig.name)

    try:
        # `handle_signals=False`: we drive the shutdown ourselves above.
        # `close_bot_session=False`: we close it in the finally block so the
        # log lines come out in a predictable order.
        await dp.start_polling(
            bot,
            handle_signals=False,
            close_bot_session=False,
        )
    finally:
        log.info("shutting down")
        try:
            await bot.session.close()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            log.warning("failed to close bot session", exc_info=True)
        try:
            await engine.dispose()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            log.warning("failed to dispose db engine", exc_info=True)
        log.info("bot stopped")


def main() -> None:
    """Synchronous entry used by the `progress-tracker` console script."""
    try:
        asyncio.run(_run())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
