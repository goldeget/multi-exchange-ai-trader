"""Per-process, app-lifespan denial guard for fixed public market endpoints."""

import asyncio
from typing import Any

import httpx

from .adapters.base import MarketDataUnavailable
from .adapters.binance import CANDLE_URLS, TICKER_URLS
from .adapters.bybit import CANDLE_URL, TICKER_URL
from .denials import safe_denial_evidence

PUBLIC_URLS = frozenset([*CANDLE_URLS.values(), *TICKER_URLS.values(), CANDLE_URL, TICKER_URL])
PUBLIC_HOSTS = frozenset(httpx.URL(url).host for url in PUBLIC_URLS)


def parse_blocked_hosts(value: str) -> frozenset[str]:
    if not value.strip():
        return frozenset()
    hosts = frozenset(part.strip() for part in value.split(","))
    if not hosts <= PUBLIC_HOSTS:
        # Never echo arbitrary configuration (which could accidentally contain a secret).
        raise ValueError("Invalid MARKET_DATA_BLOCKED_HOSTS: use supported public host names only")
    return hosts


class PublicMarketClient(httpx.AsyncClient):
    """Serialize each host through response classification; never auto-clear denial."""

    def __init__(self, *, blocked_hosts: frozenset[str] = frozenset(), **kwargs: Any) -> None:
        if not blocked_hosts <= PUBLIC_HOSTS:
            raise ValueError("Invalid MARKET_DATA_BLOCKED_HOSTS: unsupported public host")
        super().__init__(**kwargs)
        self._blocked = dict.fromkeys(blocked_hosts, "operator_disabled")
        self._locks = {host: asyncio.Lock() for host in PUBLIC_HOSTS}

    async def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        target = request.url.copy_with(query=None)
        if str(target) not in PUBLIC_URLS or request.method != "GET" or request.content:
            raise MarketDataUnavailable("unsupported", "Unsupported public market request")
        host = request.url.host
        async with self._locks[host]:
            if host in self._blocked:
                raise MarketDataUnavailable(self._blocked[host], "Public market host is disabled")
            kwargs["follow_redirects"] = False
            try:
                response = await super().send(request, **kwargs)
            except httpx.ProxyError as exc:
                if "403" not in str(exc) and "451" not in str(exc):
                    raise
                self._blocked[host] = "access_restricted"
                raise MarketDataUnavailable("access_restricted", "Public proxy denied access") from None
            if response.status_code in (403, 451):
                reason = (
                    "geographic_restriction"
                    if safe_denial_evidence(response)["denial_category"] == "geographic_restriction"
                    else "access_restricted"
                )
                self._blocked[host] = reason
                await response.aclose()
                raise MarketDataUnavailable(reason, "Public market access is restricted")
            return response
