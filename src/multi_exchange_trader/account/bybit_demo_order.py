"""Smallest Bybit demo order, then one reduce-only close.

Recommendation approval is the only caller. The close is attempted once.
This module never calls a leverage endpoint and refuses every host except
the demo trading API.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import cast

import httpx

from .bybit_readonly import sign_v5
from .config import allowed_host, require_demo_credentials

DEMO_HOST = allowed_host("demo")
_TIME_PATH = "/v5/market/time"
_ORDER_PATH = "/v5/order/create"
_FIELDS = frozenset(
    {"category", "symbol", "side", "orderType", "qty", "reduceOnly", "positionIdx"}
)
_RECV_WINDOW = "10000"


class DemoOrderRefused(RuntimeError):
    def __init__(self, code: int | None = None) -> None:
        super().__init__("Bybit demo order was refused")
        self.code = code

    def __str__(self) -> str:
        return "Bybit demo order was refused"


class DemoCloseFailed(DemoOrderRefused):
    """The open was accepted and the single reduce-only close was not."""

    def __str__(self) -> str:
        return "Bybit demo close was refused"


class BybitDemoOrderClient:
    """POST only ``/v5/order/create`` on the demo host, for one tiny round trip."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        key = api_key.strip()
        secret = api_secret.strip()
        if not key or not secret:
            raise DemoOrderRefused()
        self.base_url = DEMO_HOST
        self._api_key = key
        self._api_secret = secret
        self._server_ms: int | None = None
        self._mono: float | None = None
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
            transport=transport,
        )

    @classmethod
    def from_environment(
        cls, transport: httpx.AsyncBaseTransport | None = None
    ) -> BybitDemoOrderClient:
        key, secret = require_demo_credentials()
        return cls(api_key=key, api_secret=secret, transport=transport)

    def __repr__(self) -> str:
        return f"BybitDemoOrderClient(base_url={self.base_url})"

    async def aclose(self) -> None:
        await self._client.aclose()

    def open_body(self) -> dict[str, object]:
        return _order("Buy", reduce_only=False)

    def close_body(self) -> dict[str, object]:
        return _order("Sell", reduce_only=True)

    async def submit(self, body: Mapping[str, object]) -> None:
        payload = _checked_body(body)
        await self._sync_time()
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        timestamp = self._timestamp()
        headers = {
            "Content-Type": "application/json",
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": _RECV_WINDOW,
            "X-BAPI-SIGN": sign_v5(
                self._api_secret, self._api_key, timestamp, _RECV_WINDOW, raw
            ),
        }
        request = httpx.Request("POST", f"{self.base_url}{_ORDER_PATH}", headers=headers, content=raw)
        if request.url.host != "api-demo.bybit.com" or request.url.path != _ORDER_PATH:
            raise DemoOrderRefused()
        response = await self._client.send(request)
        if response.status_code != 200:
            raise DemoOrderRefused()
        try:
            parsed = cast(object, json.loads(response.content))
        except json.JSONDecodeError as exc:
            raise DemoOrderRefused() from exc
        ret = parsed.get("retCode") if isinstance(parsed, dict) else None
        if not isinstance(parsed, dict) or ret != 0:
            code = ret if isinstance(ret, int) and not isinstance(ret, bool) else None
            raise DemoOrderRefused(code)

    async def round_trip(self) -> None:
        """Open the smallest demo order, then close it reduce-only once."""
        await self.submit(self.open_body())
        try:
            await self.submit(self.close_body())
        except DemoOrderRefused as exc:
            raise DemoCloseFailed(exc.code) from exc

    async def _sync_time(self) -> None:
        if self._server_ms is not None:
            return
        request = httpx.Request("GET", f"{self.base_url}{_TIME_PATH}")
        response = await self._client.send(request)
        if response.status_code != 200:
            raise DemoOrderRefused()
        try:
            parsed = cast(object, json.loads(response.content))
        except json.JSONDecodeError as exc:
            raise DemoOrderRefused() from exc
        if not isinstance(parsed, dict):
            raise DemoOrderRefused()
        server_ms = parsed.get("time")
        if not isinstance(server_ms, int) or isinstance(server_ms, bool) or server_ms <= 0:
            raise DemoOrderRefused()
        self._server_ms = server_ms
        self._mono = time.monotonic()

    def _timestamp(self) -> str:
        if self._server_ms is None or self._mono is None:
            raise DemoOrderRefused()
        elapsed = int((time.monotonic() - self._mono) * 1000)
        return str(self._server_ms + max(elapsed, 0))


def _order(side: str, *, reduce_only: bool) -> dict[str, object]:
    return {
        "category": "linear",
        "symbol": "BTCUSDT",
        "side": side,
        "orderType": "Market",
        "qty": "0.001",
        "reduceOnly": reduce_only,
        "positionIdx": 0,
    }


def _checked_body(body: Mapping[str, object]) -> dict[str, object]:
    if set(body) != _FIELDS or "leverage" in body:
        raise DemoOrderRefused()
    side = body.get("side")
    reduce_only = body.get("reduceOnly")
    allowed = (side == "Buy" and reduce_only is False) or (side == "Sell" and reduce_only is True)
    if not allowed:
        raise DemoOrderRefused()
    if (
        body.get("category") != "linear"
        or body.get("symbol") != "BTCUSDT"
        or body.get("orderType") != "Market"
        or body.get("qty") != "0.001"
        or body.get("positionIdx") != 0
    ):
        raise DemoOrderRefused()
    return _order(str(side), reduce_only=bool(reduce_only))
