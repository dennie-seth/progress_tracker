"""Bot + Dispatcher factory."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from progress_tracker.config import Settings
from progress_tracker.handlers import build_root_router


def build_bot(settings: Settings) -> Bot:
    """Create the aiogram Bot instance with sensible defaults."""
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=None),
    )


def build_dispatcher() -> Dispatcher:
    """Create the Dispatcher wired with all feature routers.

    MemoryStorage is fine for a single-instance bot. Switch to Redis-backed
    storage when running multiple replicas.
    """
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(build_root_router())
    return dp
