"""Tests for progress_tracker.bot factories."""

from __future__ import annotations

from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage

from progress_tracker.bot import build_bot, build_dispatcher, build_fetcher
from progress_tracker.bot_api.fetcher import LocalFileFetcher, RemoteFileFetcher
from progress_tracker.bot_api.session import CustomAiohttpSession
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
    """No custom URL, no proxy, no auth -> plain AiohttpSession on api.telegram.org."""
    bot = build_bot(_fake_settings())
    try:
        assert isinstance(bot.session, AiohttpSession)
        assert not isinstance(bot.session, CustomAiohttpSession)
        assert "api.telegram.org" in bot.session.api.base
    finally:
        await bot.session.close()


async def test_build_bot_with_custom_api_url_no_proxy() -> None:
    bot = build_bot(_fake_settings(bot_api_url="http://my-bot-api:8081"))
    try:
        assert isinstance(bot.session, AiohttpSession)
        assert not isinstance(bot.session, CustomAiohttpSession)
        assert bot.session.api.base.startswith("http://my-bot-api:8081")
    finally:
        await bot.session.close()


async def test_build_bot_with_socks_only_uses_cloud_via_proxy() -> None:
    """SOCKS without custom URL -> CustomAiohttpSession still pointed at cloud."""
    bot = build_bot(_fake_settings(socks_proxy_url="socks5://u:p@proxy:1080"))
    try:
        assert isinstance(bot.session, CustomAiohttpSession)
        assert bot.session._auth_header is None
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
        assert isinstance(bot.session, CustomAiohttpSession)
        assert bot.session._auth_header is None
        assert bot.session.api.base.startswith("http://my-bot-api:8081")
    finally:
        await bot.session.close()


async def test_build_bot_with_basic_auth_only() -> None:
    bot = build_bot(
        _fake_settings(
            bot_api_url="http://my-bot-api:8081",
            bot_api_username="alice",
            bot_api_password="secret",
        )
    )
    try:
        assert isinstance(bot.session, CustomAiohttpSession)
        assert bot.session._auth_header is not None
        assert bot.session._auth_header.startswith("Basic ")
        assert bot.session.api.base.startswith("http://my-bot-api:8081")
    finally:
        await bot.session.close()


async def test_build_bot_with_basic_auth_and_socks() -> None:
    bot = build_bot(
        _fake_settings(
            bot_api_url="http://my-bot-api:8081",
            bot_api_username="alice",
            bot_api_password="secret",
            socks_proxy_url="socks5://u:p@proxy:1080",
        )
    )
    try:
        assert isinstance(bot.session, CustomAiohttpSession)
        assert bot.session._auth_header is not None
        assert bot.session.api.base.startswith("http://my-bot-api:8081")
    finally:
        await bot.session.close()


async def test_build_bot_partial_basic_auth_is_ignored() -> None:
    """Setting only the username (no password) must not produce an Authorization
    header — that's almost always a misconfiguration."""
    bot = build_bot(
        _fake_settings(
            bot_api_url="http://my-bot-api:8081",
            bot_api_username="alice",
            # bot_api_password left empty
        )
    )
    try:
        # No proxy + no full creds -> plain session
        assert not isinstance(bot.session, CustomAiohttpSession)
    finally:
        await bot.session.close()


# ---------- VDS co-location: build_fetcher + dispatcher wiring ----------


def test_build_fetcher_default_is_remote() -> None:
    """Dev-from-home: HTTP-download path, with DeleteFile cleanup."""
    fetcher = build_fetcher(_fake_settings())
    assert isinstance(fetcher, RemoteFileFetcher)


def test_build_fetcher_returns_local_when_local_files_enabled() -> None:
    """Co-located VDS: read directly off the shared filesystem, no cleanup."""
    fetcher = build_fetcher(
        _fake_settings(
            bot_api_local_files=True,
            bot_api_local_root="/var/lib/telegram-bot-api",
        )
    )
    assert isinstance(fetcher, LocalFileFetcher)


def test_build_dispatcher_attaches_fetcher_to_middleware() -> None:
    """If session_factory + storage + fetcher are all provided, the
    DependenciesMiddleware exposes the fetcher to handlers via `data`."""
    from unittest.mock import MagicMock

    from progress_tracker.middlewares.db import DependenciesMiddleware

    factory = MagicMock()
    storage = MagicMock()
    fetcher = RemoteFileFetcher()
    dp = build_dispatcher(
        session_factory=factory, storage=storage, fetcher=fetcher
    )
    deps_mws = [
        mw
        for mw in dp.update.outer_middleware
        if isinstance(mw, DependenciesMiddleware)
    ]
    assert len(deps_mws) == 1
    assert deps_mws[0]._fetcher is fetcher
