"""Tests for SocksAiohttpSession — the SOCKS5-capable aiogram session.

Structural assertions only: we verify the connector factory is wired up to
produce a `ProxyConnector`, without opening a socket.
"""

from __future__ import annotations

from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp_socks import ProxyConnector

from progress_tracker.bot_api.session import SocksAiohttpSession


def test_inherits_aiohttp_session() -> None:
    session = SocksAiohttpSession(socks_proxy_url="socks5://u:p@proxy:1080")
    try:
        assert isinstance(session, AiohttpSession)
    finally:
        # no-op close; no connections opened
        pass


async def test_connector_factory_produces_proxy_connector() -> None:
    """Invoke the factory under a running loop (aiohttp needs it)."""
    session = SocksAiohttpSession(socks_proxy_url="socks5://u:p@proxy:1080")
    # aiogram stores the connector factory as `_connector_type` and any
    # kwargs (limit, ssl) in `_connector_init`. Invoking the factory must
    # yield a ProxyConnector pre-configured with our SOCKS URL.
    connector = session._connector_type(**session._connector_init)
    try:
        assert isinstance(connector, ProxyConnector)
    finally:
        await connector.close()


def test_accepts_custom_api_server() -> None:
    """A custom TelegramAPIServer should be honored alongside the SOCKS connector."""
    from aiogram.client.telegram import TelegramAPIServer

    api = TelegramAPIServer.from_base("http://my-bot-api:8081", is_local=False)
    session = SocksAiohttpSession(
        socks_proxy_url="socks5://u:p@proxy:1080",
        api=api,
    )
    assert session.api is api
