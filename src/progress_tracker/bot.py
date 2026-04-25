"""Bot + Dispatcher factory."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import PRODUCTION, TelegramAPIServer
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from progress_tracker.bot_api.session import CustomAiohttpSession
from progress_tracker.config import Settings
from progress_tracker.handlers import build_root_router
from progress_tracker.middlewares.db import DependenciesMiddleware
from progress_tracker.storage.base import Storage


def build_bot(settings: Settings) -> Bot:
    """Create the aiogram Bot, honoring BOT_API_URL, SOCKS_PROXY_URL, and
    Basic Auth credentials.

    The four-by-two routing matrix collapses to: any custom-session feature
    set (SOCKS or Basic Auth) → `CustomAiohttpSession`; otherwise the stock
    `AiohttpSession`. The endpoint is `BOT_API_URL` if set, else cloud.
    """
    api = (
        TelegramAPIServer.from_base(settings.bot_api_url, is_local=False)
        if settings.bot_api_url
        else PRODUCTION
    )

    # Only treat creds as set when *both* are present — half-set is almost
    # always a misconfiguration and shouldn't produce a malformed header.
    basic_auth: tuple[str, str] | None = (
        (settings.bot_api_username, settings.bot_api_password)
        if settings.bot_api_username and settings.bot_api_password
        else None
    )

    session: AiohttpSession
    if settings.socks_proxy_url or basic_auth:
        session = CustomAiohttpSession(
            api=api,
            socks_proxy_url=settings.socks_proxy_url or None,
            basic_auth=basic_auth,
        )
    else:
        session = AiohttpSession(api=api)

    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=None),
        session=session,
    )


def build_dispatcher(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    storage: Storage | None = None,
) -> Dispatcher:
    """Create the Dispatcher wired with all feature routers.

    MemoryStorage is fine for a single-instance bot. Switch to Redis-backed
    storage when running multiple replicas.

    When `session_factory` and `storage` are both supplied, a
    `DependenciesMiddleware` is attached so handlers can request `session`
    and `storage` as kwargs. They're optional so unit tests can build a
    minimal dispatcher without a database.
    """
    dp = Dispatcher(storage=MemoryStorage())
    if session_factory is not None and storage is not None:
        dp.update.outer_middleware(
            DependenciesMiddleware(session_factory=session_factory, storage=storage)
        )
    dp.include_router(build_root_router())
    return dp
