"""SOCKS5-aware aiogram session.

aiogram's stock `AiohttpSession` passes a `proxy=` kwarg to aiohttp, which only
supports HTTP proxies. For SOCKS5 we need `aiohttp-socks` and must substitute
the TCP connector at session construction time — aiogram builds its
`ClientSession` from `self._connector_type(**self._connector_init)`, so we
replace `_connector_type` with a partial that routes through
`ProxyConnector.from_url`. That keeps aiogram's existing `limit` / `ssl`
kwargs flowing into the underlying TCP connector.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp_socks import ProxyConnector


class SocksAiohttpSession(AiohttpSession):
    """AiohttpSession that tunnels every request through a SOCKS5 proxy.

    Pass `socks_proxy_url` in classic form: ``socks5://user:pass@host:port``.
    Remaining kwargs (notably `api=...`) are forwarded to `AiohttpSession`.
    """

    def __init__(self, *, socks_proxy_url: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._socks_proxy_url = socks_proxy_url
        # aiogram's AiohttpSession uses self._connector_type(**self._connector_init)
        # to build the TCPConnector. Swap the factory for ProxyConnector.from_url
        # with the SOCKS URL baked in; kwargs (limit, ssl, ...) still pass through.
        self._connector_type = partial(ProxyConnector.from_url, socks_proxy_url)
