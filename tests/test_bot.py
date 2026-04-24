"""Tests for progress_tracker.bot factories."""

from __future__ import annotations

from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage

from progress_tracker.bot import build_bot, build_dispatcher
from progress_tracker.bot_api.session import SocksAiohttpSession
from progress_tracker.config import Settings


def _fake_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "bot_token": "1234:FAKE-TOKEN",
        "database_url": "postgresql+asyncpg://u:p@h/db",
        "media_dir": Path("/tmp/media"),
        "log_level": "INFO",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


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


# ---------- Milestone 2.5: BOT_API_URL + SOCKS5 routing ----------


async def test_build_bot_default_uses_cloud_direct() -> None:
    """No custom URL, no proxy -> plain AiohttpSession on api.telegram.org."""
    bot = build_bot(_fake_settings())
    try:
        assert isinstance(bot.session, AiohttpSession)
        assert not isinstance(bot.session, SocksAiohttpSession)
        assert "api.telegram.org" in bot.session.api.base
    finally:
        await bot.session.close()


async def test_build_bot_with_custom_api_url_no_proxy() -> None:
    bot = build_bot(_fake_settings(bot_api_url="http://my-bot-api:8081"))
    try:
        assert isinstance(bot.session, AiohttpSession)
        assert not isinstance(bot.session, SocksAiohttpSession)
        assert bot.session.api.base.startswith("http://my-bot-api:8081")
    finally:
        await bot.session.close()


async def test_build_bot_with_socks_only_uses_cloud_via_proxy() -> None:
    """SOCKS without custom URL -> SocksAiohttpSession still pointed at cloud."""
    bot = build_bot(_fake_settings(socks_proxy_url="socks5://u:p@proxy:1080"))
    try:
        assert isinstance(bot.session, SocksAiohttpSession)
        assert "api.telegram.org" in bot.session.api.base
    finally:
        await bot.session.close()


async def test_build_bot_with_custom_api_and_socks() -> None:
    bot = build_bot(
        _fake_settings(
            bot_api_url="http://my-bot-api:8081",
            socks_proxy_url="socks5://u:p@proxy:1080",
        )
    )
    try:
        assert isinstance(bot.session, SocksAiohttpSession)
        assert bot.session.api.base.startswith("http://my-bot-api:8081")
    finally:
        await bot.session.close()
