"""Custom aiogram session that layers optional SOCKS5 and HTTP Basic Auth.

aiogram's stock `AiohttpSession` only knows about HTTP proxies (the `proxy=`
kwarg on aiohttp's request methods). For SOCKS5 we need `aiohttp-socks`'
`ProxyConnector`; for Basic Auth we need to inject an `Authorization` header
into the underlying `ClientSession`. This class supports both, independently.

Usage:

    # SOCKS only — same shape as the previous SocksAiohttpSession.
    session = CustomAiohttpSession(socks_proxy_url="socks5://u:p@host:1080")

    # Basic Auth only — for a server fronted by caddy/nginx with auth.
    session = CustomAiohttpSession(basic_auth=("user", "password"))

    # Both — useful while transitioning to a public server still behind SOCKS.
    session = CustomAiohttpSession(
        socks_proxy_url="socks5://u:p@host:1080",
        basic_auth=("user", "password"),
    )
"""

from __future__ import annotations

import base64
from functools import partial
from typing import Any

import aiohttp
from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp_socks import ProxyConnector


class CustomAiohttpSession(AiohttpSession):
    def __init__(
        self,
        *,
        socks_proxy_url: str | None = None,
        basic_auth: tuple[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if socks_proxy_url:
            # Replace aiogram's TCPConnector factory with one that builds a
            # SOCKS-aware connector from the proxy URL. The remaining kwargs
            # (limit, ssl, ...) still flow through `_connector_init`.
            self._connector_type = partial(ProxyConnector.from_url, socks_proxy_url)

        self._auth_header: str | None = None
        if basic_auth:
            user, pw = basic_auth
            encoded = base64.b64encode(f"{user}:{pw}".encode()).decode("ascii")
            self._auth_header = f"Basic {encoded}"

    async def create_session(self) -> aiohttp.ClientSession:
        """Build the underlying ClientSession, attaching Basic Auth if configured.

        We can't override `super().create_session()` cleanly because aiogram
        builds the connector inside it; instead, recreate the session here
        ourselves when an auth header is needed, mirroring the parent's
        construction.
        """
        if self._auth_header is None:
            return await super().create_session()

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=self._connector_type(**self._connector_init),
                headers={"Authorization": self._auth_header},
            )
            self._should_reset_connector = False
        return self._session
