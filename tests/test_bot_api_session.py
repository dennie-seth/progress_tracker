"""Tests for CustomAiohttpSession — optional SOCKS5 and Basic Auth."""

from __future__ import annotations

import base64

from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp_socks import ProxyConnector

from progress_tracker.bot_api.session import CustomAiohttpSession


def _expected_basic_header(user: str, pw: str) -> str:
    encoded = base64.b64encode(f"{user}:{pw}".encode()).decode("ascii")
    return f"Basic {encoded}"


def test_inherits_aiohttp_session() -> None:
    s = CustomAiohttpSession(socks_proxy_url="socks5://u:p@proxy:1080")
    assert isinstance(s, AiohttpSession)


# ---- SOCKS only (regression) ----


async def test_socks_only_uses_proxy_connector() -> None:
    s = CustomAiohttpSession(socks_proxy_url="socks5://u:p@proxy:1080")
    connector = s._connector_type(**s._connector_init)
    try:
        assert isinstance(connector, ProxyConnector)
    finally:
        await connector.close()


def test_socks_only_does_not_set_auth_header() -> None:
    s = CustomAiohttpSession(socks_proxy_url="socks5://u:p@proxy:1080")
    assert s._auth_header is None


# ---- BasicAuth only ----


def test_basic_auth_only_emits_authorization_header() -> None:
    s = CustomAiohttpSession(basic_auth=("alice", "wonderland"))
    assert s._auth_header == _expected_basic_header("alice", "wonderland")


async def test_basic_auth_only_keeps_default_tcp_connector() -> None:
    """Without socks_proxy_url, the connector factory must remain a stock
    aiohttp TCPConnector so requests don't accidentally route through SOCKS."""
    s = CustomAiohttpSession(basic_auth=("u", "p"))
    connector = s._connector_type(**s._connector_init)
    try:
        assert not isinstance(connector, ProxyConnector)
    finally:
        await connector.close()


# ---- Both SOCKS and BasicAuth ----


async def test_socks_plus_basic_auth_combine() -> None:
    s = CustomAiohttpSession(
        socks_proxy_url="socks5://u:p@proxy:1080",
        basic_auth=("alice", "wonderland"),
    )
    assert s._auth_header == _expected_basic_header("alice", "wonderland")
    connector = s._connector_type(**s._connector_init)
    try:
        assert isinstance(connector, ProxyConnector)
    finally:
        await connector.close()


# ---- Wiring: header is actually attached to the underlying ClientSession ----


async def test_underlying_client_session_carries_authorization_header() -> None:
    s = CustomAiohttpSession(basic_auth=("alice", "wonderland"))
    try:
        client = await s.create_session()
        assert (
            client._default_headers.get("Authorization")
            == _expected_basic_header("alice", "wonderland")
        )
    finally:
        await s.close()
