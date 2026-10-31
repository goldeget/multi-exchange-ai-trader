"""Signed Bybit V5 reads for USDT linear accounts.

This is not an order sender. NautilusTrader's Bybit execution client can submit
orders and, on connect, apply leverage, position mode, and margin mode. The
HTTP client behind that execution path is the same order path. This module
therefore does not construct either client. It allows only the GET paths in
``READ_ONLY_PATHS``.

``LIVE_TRADING_ENABLED`` defaults to false. The API refuses order and leverage
routes on every call. Turning the flag on does not add an order method here.
A future order must go through one NautilusTrader execution path, which this
milestone does not start.
"""

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Mapping
from typing import TypeGuard, cast
from urllib.parse import urlencode

import httpx

from .config import (
    EnvironmentName,
    MissingBybitCredentials,
    allowed_host,
    require_bybit_credentials,
    resolve_environment,
)

READ_ONLY_PATHS = frozenset(
    {
        "/v5/market/time",
        "/v5/market/instruments-info",
        "/v5/account/wallet-balance",
        "/v5/position/list",
        "/v5/order/realtime",
    }
)
RECV_WINDOW = "10000"
_MAX_BYTES = 1_000_000
_PAGE_LIMIT = 5
_REGION_PHRASES = (
    "restricted location",
    "block access from your country",
    "your region",
    "your country",
    "not available in your",
)
_LOGGERS = ("httpx", "httpcore")


class ReadOnlyViolation(RuntimeError):
    def __str__(self) -> str:
        return "Bybit read-only client refused a request that is not an allowlisted GET"


class BybitBlocked(RuntimeError):
    def __str__(self) -> str:
        return "BLOCKED"


class BybitReadError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _DropCredentialLogs(logging.Filter):
    """Drop log records that contain the key or secret. The repr hides both."""

    def __init__(self, secrets: tuple[str, ...]) -> None:
        super().__init__()
        self._secrets = tuple(item for item in secrets if len(item) >= 8)

    def filter(self, record: logging.LogRecord) -> bool:
        parts: list[str] = []
        if isinstance(record.msg, str):
            parts.append(record.msg)
        args = record.args
        if isinstance(args, Mapping):
            parts.extend(str(value) for value in args.values())
        elif isinstance(args, tuple):
            parts.extend(str(value) for value in args)
        blob = " ".join(parts)
        return not any(secret in blob for secret in self._secrets)

    def __repr__(self) -> str:
        return "_DropCredentialLogs(active=True)"


def sign_v5(secret: str, api_key: str, timestamp: str, recv_window: str, query: str) -> str:
    """Bybit V5 HMAC. The hex digest is not the secret, and the secret is not appended."""
    payload = f"{timestamp}{api_key}{recv_window}{query}"
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _query(params: list[tuple[str, str]]) -> str:
    items = sorted((key, value) for key, value in params if value != "")
    return urlencode(items)


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_json(raw: bytes) -> object:
    if len(raw) > _MAX_BYTES:
        raise BybitReadError("response_too_large")
    try:
        return cast(object, json.loads(raw))
    except json.JSONDecodeError:
        raise BybitReadError("invalid_json") from None


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise BybitReadError("invalid_json")
    mapped: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise BybitReadError("invalid_json")
        mapped[key] = item
    return mapped


def _region_message(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = value.lower()
    return any(phrase in text for phrase in _REGION_PHRASES)


class BybitReadOnlyClient:
    """GET-only Bybit client. It cannot place orders or change leverage."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str,
        environment: EnvironmentName,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        key = api_key.strip()
        secret = api_secret.strip()
        if not secret:
            raise MissingBybitCredentials("BYBIT_API_SECRET is not set")
        if not key:
            raise MissingBybitCredentials("BYBIT_API_KEY is not set")
        if base_url != allowed_host(environment):
            raise ValueError("Bybit host does not match the selected environment")
        self.environment: EnvironmentName = environment
        self.base_url = base_url
        self._api_key = key
        self._api_secret = secret
        self._server_ms: int | None = None
        self._mono: float | None = None
        self._log_filter = _DropCredentialLogs((key, secret))
        self._loggers = [logging.getLogger(name) for name in _LOGGERS]
        for logger in self._loggers:
            logger.addFilter(self._log_filter)
        # trust_env stays at the library default so an existing proxy is used
        # unchanged. This client does not set or remove a proxy.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
            transport=transport,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

    @classmethod
    def from_environment(
        cls, transport: httpx.AsyncBaseTransport | None = None
    ) -> "BybitReadOnlyClient":
        key, secret = require_bybit_credentials()
        environment, base_url = resolve_environment()
        return cls(
            api_key=key,
            api_secret=secret,
            base_url=base_url,
            environment=environment,
            transport=transport,
        )

    def __repr__(self) -> str:
        return f"BybitReadOnlyClient(environment={self.environment}, base_url={self.base_url})"

    async def aclose(self) -> None:
        for logger in self._loggers:
            try:
                logger.removeFilter(self._log_filter)
            except ValueError:
                continue
        await self._client.aclose()

    def anchor_server_time(self, server_ms: int) -> None:
        if not _is_int(server_ms):
            raise BybitReadError("invalid_json")
        if server_ms <= 0:
            raise BybitReadError("invalid_json")
        self._server_ms = server_ms
        self._mono = time.monotonic()

    def _timestamp(self) -> str:
        if self._server_ms is None or self._mono is None:
            raise BybitReadError("invalid_json")
        elapsed = int((time.monotonic() - self._mono) * 1000)
        return str(self._server_ms + max(elapsed, 0))

    async def read(
        self, path: str, params: list[tuple[str, str]], *, signed: bool
    ) -> tuple[dict[str, object], int]:
        if path not in READ_ONLY_PATHS:
            raise ReadOnlyViolation()
        query = _query(params)
        headers: dict[str, str] = {}
        if signed:
            timestamp = self._timestamp()
            headers = {
                "X-BAPI-API-KEY": self._api_key,
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-RECV-WINDOW": RECV_WINDOW,
                "X-BAPI-SIGN": sign_v5(
                    self._api_secret, self._api_key, timestamp, RECV_WINDOW, query
                ),
            }
        target = f"{self.base_url}{path}"
        if query:
            target = f"{target}?{query}"
        request = httpx.Request("GET", target, headers=headers)
        try:
            response = await self._client.send(request)
        except httpx.HTTPError as exc:
            # Classify from the exception type and a few fixed phrases.
            # Do not copy the exception text forward; it can contain request details.
            detail = str(exc).lower()
            if "403" in detail or "451" in detail:
                raise BybitBlocked() from None
            if "name resolution" in detail or "getaddrinfo" in detail:
                raise BybitReadError("dns") from None
            raise BybitReadError("network") from None
        return self._accepted(response)

    async def read_pages(self, path: str, params: list[tuple[str, str]]) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        cursor = ""
        for _ in range(_PAGE_LIMIT):
            page = list(params)
            page.append(("limit", "50"))
            if cursor:
                page.append(("cursor", cursor))
            result, _server_ms = await self.read(path, page, signed=True)
            for item in _as_list(result.get("list")):
                rows.append(_mapping(item))
            cursor = _next_cursor(result)
            if not cursor:
                return rows
        raise BybitReadError("truncated")

    def _accepted(self, response: httpx.Response) -> tuple[dict[str, object], int]:
        if response.status_code in {403, 451}:
            raise BybitBlocked()
        if response.status_code == 429:
            raise BybitReadError("ret_10006")
        if 300 <= response.status_code < 400:
            raise BybitReadError("redirect")
        if response.status_code != 200:
            raise BybitReadError("http_status")
        body = _mapping(_parse_json(response.content))
        if _region_message(body.get("retMsg")):
            raise BybitBlocked()
        ret = body.get("retCode")
        if not _is_int(ret):
            raise BybitReadError("invalid_json")
        if ret != 0:
            raise BybitReadError(f"ret_{ret}")
        server_ms = body.get("time")
        if not _is_int(server_ms):
            raise BybitReadError("invalid_json")
        if server_ms <= 0:
            raise BybitReadError("invalid_json")
        return _mapping(body.get("result")), server_ms


def _as_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise BybitReadError("invalid_json")
    return list(value)


def _next_cursor(result: dict[str, object]) -> str:
    raw = result.get("nextPageCursor", "")
    if raw in (None, ""):
        return ""
    if isinstance(raw, str) and 1 <= len(raw) <= 512 and raw.isascii() and " " not in raw:
        return raw
    raise BybitReadError("invalid_json")
