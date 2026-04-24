"""Tests for progress_tracker.bot factories."""

from __future__ import annotations

from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from progress_tracker.bot import build_bot, build_dispatcher
from progress_tracker.config import Settings


def _fake_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        bot_token="1234:FAKE-TOKEN",
        database_url="postgresql+asyncpg://u:p@h/db",
        media_dir=Path("/tmp/media"),
        log_level="INFO",
        _env_file=None,
    )


async def test_build_bot_returns_bot_with_token() -> None:
    bot = build_bot(_fake_settings())
    try:
        assert isinstance(bot, Bot)
        assert bot.token == "1234:FAKE-TOKEN"
    finally:
        await bot.session.close()


def test_build_dispatcher_returns_dispatcher() -> None:
    dp = build_dispatcher()
    assert isinstance(dp, Dispatcher)


def test_build_dispatcher_uses_memory_storage() -> None:
    dp = build_dispatcher()
    assert isinstance(dp.fsm.storage, MemoryStorage)


def test_build_dispatcher_includes_root_router() -> None:
    dp = build_dispatcher()
    names = [r.name for r in dp.sub_routers]
    assert "root" in names
