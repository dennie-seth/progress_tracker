"""Bot + Dispatcher factory."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import PRODUCTION, TelegramAPIServer
from aiogram.fsm.storage.memory import MemoryStorage

from progress_tracker.bot_api.session import SocksAiohttpSession
from progress_tracker.config import Settings
from progress_tracker.handlers import build_root_router


def build_bot(settings: Settings) -> Bot:
    """Create the aiogram Bot, honoring BOT_API_URL and SOCKS_PROXY_URL.

    Four cases:
      - neither set → default cloud API, direct.
      - BOT_API_URL only → custom server, direct.
      - SOCKS_PROXY_URL only → cloud API through the SOCKS tunnel.
      - both set → custom server through the SOCKS tunnel.
    """
    api = (
        TelegramAPIServer.from_base(settings.bot_api_url, is_local=False)
        if settings.bot_api_url
        else PRODUCTION
    )

    session: AiohttpSession
    if settings.socks_proxy_url:
        session = SocksAiohttpSession(
            socks_proxy_url=settings.socks_proxy_url,
            api=api,
        )
    else:
        session = AiohttpSession(api=api)

    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=None),
        session=session,
    )


def build_dispatcher() -> Dispatcher:
    """Create the Dispatcher wired with all feature routers.

    MemoryStorage is fine for a single-instance bot. Switch to Redis-backed
    storage when running multiple replicas.
    """
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(build_root_router())
    return dp
