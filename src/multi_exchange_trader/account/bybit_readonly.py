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

import asyncio
import hashlib
import hmac
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, Protocol, TypeGuard, cast
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from httpx._client import ClientState
from httpx._urlparse import ParseResult

from ..credential_resolution import CredentialResolver
from ..trading_product import InstrumentKey, bybit_category
from .config import (
    EnvironmentName,
    MissingBybitCredentials,
    allowed_host,
    require_bybit_credentials,
    require_demo_credentials,
    resolve_environment,
)
from .instrument_envelope import (
    _REGION_PHRASES as _REGION_PHRASES,  # noqa: PLC0414 - preserve legacy re-export
)
from .instrument_envelope import (
    HttpsOrigin,
    InstrumentEnvelopeRead,
    InstrumentSourceError,
    _accepted_envelope_read,
)
from .instrument_envelope import (
    _region_message as _region_message,  # noqa: PLC0414 - preserve legacy re-export
)

if TYPE_CHECKING:
    from ..execution.monitoring_budget import MonitoringReadBudget, ReadGroupToken, _ReceiptBudget
    from .risk_limit_envelope import BybitRiskLimitEnvelopeReadV1

READ_ONLY_PATHS = frozenset(
    {
        "/v5/market/time",
        "/v5/market/instruments-info",
        "/v5/account/wallet-balance",
        "/v5/account/fee-rate",
        "/v5/account/info",
        "/v5/user/query-api",
        "/v5/position/list",
        "/v5/order/realtime",
        "/v5/execution/list",
        "/v5/order/history",
        "/v5/account/transaction-log",
        "/v5/market/risk-limit",
    }
)
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RECV_WINDOW = "10000"
_MAX_BYTES = 1_000_000
_PAGE_LIMIT = 5
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


def _positive_time(value: object) -> int | None:
    """Return a Bybit envelope time, or None when that field cannot be used."""
    if _is_int(value) and value > 0:
        return value
    return None


def _parse_json(raw: bytes) -> object:
    if len(raw) > _MAX_BYTES:
        raise BybitReadError("response_too_large")
    try:
        def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate JSON field")
                result[key] = value
            return result

        def constant(_: str) -> object:
            raise ValueError("nonfinite JSON")

        return cast(object, json.loads(raw, object_pairs_hook=pairs, parse_constant=constant))
    except (ValueError, UnicodeError, RecursionError):
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


class _ReadOperation(Protocol):
    def check(self) -> None: ...
    def begin_read(self) -> float: ...
    def before_attempt(self, attempt: int) -> None: ...
    def response(self, status: int, headers: Mapping[str, str]) -> None: ...
    def read_failed(self) -> None: ...
    def validate_response(self, result: dict[str, object], stamp: int | None) -> None: ...


@dataclass
class _InstrumentRoute:
    key: InstrumentKey
    budget: "MonitoringReadBudget"
    group: "ReadGroupToken"
    started_at: datetime
    started_mono: float
    physical_start: int
    attempt: int = 0
    logical_ordinal: int = 0
    sent_origin: HttpsOrigin | None = None
    response_origin: HttpsOrigin | None = None
    received_at: datetime | None = None
    received_mono: float | None = None
    envelope: InstrumentEnvelopeRead | None = None

    def check(self) -> None:
        self.budget.require_active_group(self.group)


type _RiskWire = tuple[
    httpx.URL, httpx.Headers, str, ParseResult, str | None,
    tuple[tuple[bytes, bytes, bytes], ...],
]


def _risk_request_wire(request: httpx.Request, expected: _RiskWire | None = None) -> _RiskWire:
    """Pinned HTTPX 0.28 wire fields only; no virtual request/header/URL calls."""
    if type(request) is not httpx.Request:
        raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
    try:
        url, headers, method = request.url, request.headers, request.method
    except AttributeError:
        raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH") from None
    if type(url) is not httpx.URL or type(headers) is not httpx.Headers or type(method) is not str:
        raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
    try:
        reference = url._uri_reference
    except AttributeError:
        raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH") from None
    if type(reference) is not ParseResult or len(reference) != 7:
        raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
    size = 0
    for value in (method, reference.scheme, reference.userinfo, reference.host, reference.path):
        if type(value) is not str:
            raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
        size += len(value)
        if size > 65536 or not value.isascii():
            raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
    port = reference.port
    if port is not None and (type(port) is not int or not 0 <= port <= 65535):
        raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
    size += 0 if port is None else 5
    if size > 65536:
        raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
    try:
        encoding, entries = headers._encoding, headers._list
    except AttributeError:
        raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH") from None
    for optional in (reference.query, reference.fragment, encoding):
        if optional is not None:
            if type(optional) is not str:
                raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
            size += len(optional)
            if size > 65536 or not optional.isascii():
                raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
    if type(entries) is not list or len(entries) > 64:
        raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
    frozen: list[tuple[bytes, bytes, bytes]] = []
    for entry in entries:
        if type(entry) is not tuple or len(entry) != 3:
            raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
        for leaf in entry:
            if type(leaf) is not bytes:
                raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
            size += len(leaf)
            if size > 65536:
                raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
        # HTTPX semantic lookup uses slot1, while the wire uses slot0.
        if (not entry[0] or not entry[0].isascii() or entry[0].lower() != entry[1]
                or entry[1].startswith(b"x-bapi")):
            raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
        frozen.append(entry)
    packed = tuple(frozen)
    if expected is not None and (
            url is not expected[0] or headers is not expected[1] or method != expected[2]
            or reference != expected[3] or encoding != expected[4] or packed != expected[5]):
        raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
    return url, headers, method, reference, encoding, packed


@dataclass
class _RiskRoute(_InstrumentRoute):
    owner: "BybitReadOnlyClient | None" = None
    client: httpx.AsyncClient | None = None
    source_environment: Literal["PUBLIC_MAINNET", "PUBLIC_TESTNET"] = "PUBLIC_MAINNET"
    source_id: str = ""
    generation: str = ""
    observation_id: str = ""
    params: tuple[tuple[str, str], ...] = ()
    deadline: float | None = None
    last_mono: float = 0.0
    risk_envelope: "BybitRiskLimitEnvelopeReadV1 | None" = None
    owner_guard: Callable[[], None] | None = None
    owner_failure: BaseException | None = None
    operation_context: ContextVar[_ReadOperation | None] | None = None
    operation_entered: bool = False
    receipt_context: "ContextVar[_ReceiptBudget | None] | None" = None
    receipt: "_ReceiptBudget | None" = None

    def _group_check(self) -> None:
        from ..execution.monitoring_budget import (
            MonitoringReadBudget,
            ReadGroupToken,
            _ReceiptBudget,
        )

        # Callback-free proof of the originally selected receipt. A guard may
        # end it or replace the ContextVar/receipt while retaining the token.
        context, receipt = self.receipt_context, self.receipt
        if (type(self.budget) is not MonitoringReadBudget
                or type(context) is not ContextVar or self.budget._receipt is not context
                or type(receipt) is not _ReceiptBudget or context.get() is not receipt
                or type(self.group) is not ReadGroupToken or receipt.token is not self.group
                or receipt.active is not True):
            raise InstrumentSourceError("INSTRUMENT_SOURCE_GROUP_REQUIRED")

    def _owner_check(self) -> None:
        assert self.owner is not None and self.client is not None
        if (type(self.owner) is not BybitReadOnlyClient
                or type(self.client) is not httpx.AsyncClient):
            raise InstrumentSourceError("INSTRUMENT_SOURCE_RETIRED")
        if self.owner._risk_generation is None:
            raise InstrumentSourceError("INSTRUMENT_SOURCE_RETIRED")
        if (type(self.generation) is not str or type(self.source_id) is not str
                or type(self.owner._risk_generation) is not str
                or type(self.owner._risk_source_id) is not str):
            raise InstrumentSourceError("INSTRUMENT_SOURCE_RETIRED")
        state = self.client._state
        if type(state) is not ClientState:
            raise InstrumentSourceError("INSTRUMENT_SOURCE_RETIRED")
        if state is ClientState.CLOSED:
            raise InstrumentSourceError("INSTRUMENT_SOURCE_CLOSED")
        if state is not ClientState.UNOPENED and state is not ClientState.OPENED:
            raise InstrumentSourceError("INSTRUMENT_SOURCE_RETIRED")
        if (self.owner._client is not self.client
                or self.owner._risk_http_client is not self.client
                or self.owner._risk_generation != self.generation
                or self.owner._risk_source_id != self.source_id):
            raise InstrumentSourceError("INSTRUMENT_SOURCE_RETIRED")
        # Closed built-in fields keep these final equalities callback-free.
        if (type(self.source_environment) is not str
                or type(self.owner._public_source_environment) is not str
                or type(self.owner.environment) is not str
                or type(self.owner.base_url) is not str
                or type(self.owner._api_key) is not str
                or type(self.owner._api_secret) is not str):
            raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
        environment = "mainnet" if self.source_environment == "PUBLIC_MAINNET" else "testnet"
        if (self.owner._public_source_environment != self.source_environment
                or self.owner.environment != environment
                or self.owner.base_url != allowed_host(environment)
                or self.owner._api_key != "" or self.owner._api_secret != ""
                or self.owner._credential_check is not None):
            raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
        context = self.operation_context
        if type(context) is not ContextVar or self.owner._operation is not context:
            raise InstrumentSourceError("INSTRUMENT_SOURCE_OVERLAP")
        operation = context.get()
        if (operation is not self.budget if self.operation_entered else
                operation is not None and operation is not self.budget):
            raise InstrumentSourceError("INSTRUMENT_SOURCE_OVERLAP")

    def _clock_check(self) -> None:
        now = time.monotonic()
        if type(now) is not float or not math.isfinite(now) or now < self.last_mono:
            raise InstrumentSourceError("INSTRUMENT_SOURCE_CLOCK_INVALID")
        self.last_mono = now
        if self.deadline is not None and now >= self.deadline:
            raise InstrumentSourceError("INSTRUMENT_SOURCE_TIMEOUT")

    def check(self, request: httpx.Request | None = None) -> None:
        self._owner_check()
        self._group_check()
        self.budget.require_active_group(self.group)
        # Semantic library observations and clocks precede the higher-owner
        # guard. Only closed wire fields and raw owner/context proofs follow it.
        self._clock_check()
        wire: _RiskWire | None = None
        if request is not None:
            wire = _risk_request_wire(request)
            origin = self.request_origin(request)
            # Bracket semantic observations with the same closed wire snapshot.
            # Observation-time owner loss retains its ordinary lifetime error.
            self._owner_check()
            self._group_check()
            _risk_request_wire(request, wire)
            self.sent_origin = origin
        if self.owner_guard is not None:
            try:
                self.owner_guard()
            except BaseException as error:
                self.owner_failure = error
                raise
        if request is not None:
            assert wire is not None
            _risk_request_wire(request, wire)
        self._owner_check()
        self._group_check()

    def request_origin(self, request: httpx.Request) -> HttpsOrigin:
        origin = _exact_origin(request)
        host = "api.bybit.com" if self.source_environment == "PUBLIC_MAINNET" else "api-testnet.bybit.com"
        if (origin != HttpsOrigin("https", host, 443) or request.method != "GET"
                or request.url.raw_path.split(b"?", 1)[0] != b"/v5/market/risk-limit"
                or request.url.query != _query(list(self.params)).encode("ascii")
                or request.url.fragment
                or request.headers.get_list("accept-encoding") != ["identity"]
                or any(name.lower().startswith("x-bapi") for name in request.headers)):
            raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
        return origin


def _exact_origin(request: httpx.Request) -> HttpsOrigin:
    url = request.url
    if url.userinfo or (url.port is not None and url.port != 443):
        raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
    return HttpsOrigin(url.scheme, url.host, 443 if url.port is None else url.port)


def _check_exact_request(request: httpx.Request, key: InstrumentKey) -> HttpsOrigin:
    expected_query = _query([("category", bybit_category(key.product)), ("symbol", key.symbol)])
    if (request.method != "GET" or request.url.raw_path.split(b"?", 1)[0]
            != b"/v5/market/instruments-info" or request.url.query != expected_query.encode("ascii")
            or request.url.fragment):
        raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
    return _exact_origin(request)


class BybitReadOnlyClient:
    """GET-only Bybit client. It cannot place orders or change leverage."""

    _risk_generation: str | None
    _risk_source_id: str
    _risk_http_client: httpx.AsyncClient

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
        self._credential_check: Callable[[str], bool] | None = None
        self._public_source_environment: Literal["PUBLIC_MAINNET", "PUBLIC_TESTNET"] | None = None
        self._injected_transport = transport is not None
        self._server_ms: int | None = None
        self._mono: float | None = None
        self.retry_waits = (0.4, 0.8)
        self._operation: ContextVar[_ReadOperation | None] = ContextVar("bybit_read_operation", default=None)
        self._sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep
        self._log_filter = _DropCredentialLogs((key, secret))
        self._loggers = [logging.getLogger(name) for name in _LOGGERS]
        for logger in self._loggers:
            logger.addFilter(self._log_filter)
        # trust_env stays at the library default so an existing proxy is used
        # unchanged. This client does not set or remove a proxy.
        self._initialize_http(transport=transport, public=False)

    def _initialize_http(self, *, transport: httpx.AsyncBaseTransport | None,
                         public: bool) -> None:
        options: dict[str, object] = {"trust_env": False, "verify": True} if public else {}
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
            transport=transport,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            **options,  # type: ignore[arg-type]
        )

    @classmethod
    def for_public_instruments(
        cls, *, source_environment: Literal["PUBLIC_MAINNET", "PUBLIC_TESTNET"],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "BybitReadOnlyClient":
        if (cls is not BybitReadOnlyClient or type(source_environment) is not str
                or source_environment not in ("PUBLIC_MAINNET", "PUBLIC_TESTNET")):
            raise InstrumentSourceError("INSTRUMENT_SOURCE_SOURCE_UNSUPPORTED")
        client = object.__new__(cls)
        client.environment = "mainnet" if source_environment == "PUBLIC_MAINNET" else "testnet"
        client.base_url = allowed_host(client.environment)
        client._public_source_environment = source_environment
        client._injected_transport = transport is not None
        client._api_key = ""
        client._api_secret = ""
        client._credential_check = None
        client._server_ms = None
        client._mono = None
        client.retry_waits = (0.4, 0.8)
        client._operation = ContextVar("bybit_read_operation", default=None)
        client._sleeper = asyncio.sleep
        client._log_filter = _DropCredentialLogs(())
        client._loggers = []
        client._initialize_http(transport=transport, public=True)
        client._risk_source_id = uuid4().hex
        client._risk_generation = uuid4().hex
        client._risk_http_client = client._client
        return client

    @classmethod
    def from_environment(
        cls, transport: httpx.AsyncBaseTransport | None = None, *,
        resolver: CredentialResolver | None = None,
    ) -> "BybitReadOnlyClient":
        context = resolver if resolver is not None else CredentialResolver.from_environment()
        key, secret = require_bybit_credentials(resolver=context)
        environment, base_url = resolve_environment(resolver=context)
        client = cls(
            api_key=key,
            api_secret=secret,
            base_url=base_url,
            environment=environment,
            transport=transport,
        )
        client._credential_check = context._bybit_publication_check()
        return client

    @classmethod
    def for_demo(cls, transport: httpx.AsyncBaseTransport | None = None, *,
                 resolver: CredentialResolver | None = None) -> "BybitReadOnlyClient":
        """Practice-account reads. The host is fixed and the live key is refused."""
        context = resolver if resolver is not None else CredentialResolver.from_environment()
        key, secret = require_demo_credentials(resolver=context)
        client = cls(
            api_key=key,
            api_secret=secret,
            base_url=allowed_host("demo"),
            environment="demo",
            transport=transport,
        )
        client._credential_check = context._bybit_publication_check()
        return client

    def __repr__(self) -> str:
        return f"BybitReadOnlyClient(environment={self.environment}, base_url={self.base_url})"

    def _contains_credential(self, text: str) -> bool:
        """Private guard for this pair and all startup-selected Bybit namespaces."""
        return any(value and value in text for value in (self._api_key, self._api_secret)) or (
            self._credential_check is not None and self._credential_check(text)
        )

    async def aclose(self) -> None:
        if self._public_source_environment is not None:
            # Revoke in-flight raw risk reads before awaiting transport cleanup.
            self._risk_generation = None
        for logger in self._loggers:
            try:
                logger.removeFilter(self._log_filter)
            except ValueError:
                continue
        await self._client.aclose()

    def anchor_server_time(self, server_ms: int) -> None:
        if self._public_source_environment is not None:
            raise InstrumentSourceError("INSTRUMENT_SOURCE_INPUT_INVALID")
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

    def _raise_if_deadline(self, deadline: float | None) -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise BybitReadError("timeout")

    def _build_request(
        self, path: str, params: list[tuple[str, str]], *, signed: bool
    ) -> httpx.Request:
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
        return httpx.Request("GET", target, headers=headers)

    @contextmanager
    def _monitoring_operation(self, operation: _ReadOperation) -> Iterator[None]:
        context = self._operation
        current = context.get()
        if current is not None and current is not operation:
            raise BybitReadError("monitoring_operation_overlap")
        token = context.set(operation)
        try:
            yield
        finally:
            context.reset(token)

    async def read(
        self,
        path: str,
        params: list[tuple[str, str]],
        *,
        signed: bool,
        deadline: float | None = None,
    ) -> tuple[dict[str, object], int | None]:
        if self._public_source_environment is not None:
            raise InstrumentSourceError("INSTRUMENT_SOURCE_INPUT_INVALID")
        return await self._read(path, params, signed=signed, deadline=deadline)

    async def read_instrument_envelope(
        self, key: InstrumentKey, *, budget: "MonitoringReadBudget", group: "ReadGroupToken",
    ) -> InstrumentEnvelopeRead:
        # Runtime budget import stays local: importing account does not load execution/native.
        from ..execution.monitoring_budget import MonitoringBudgetError, MonitoringReadBudget
        from ..market_data.instrument_definition import CatalogRejected, _key_payload

        if type(budget) is not MonitoringReadBudget:
            raise InstrumentSourceError("INSTRUMENT_SOURCE_BUDGET_REQUIRED")
        code: str | None = None
        try:
            budget.require_active_group(group)
        except MonitoringBudgetError as error:
            code = ("INSTRUMENT_SOURCE_GROUP_REQUIRED" if error.args == ("MONITORING_GROUP_REQUIRED",)
                    else "INSTRUMENT_SOURCE_BUDGET_EXHAUSTED")
        if code is not None:
            raise InstrumentSourceError(code)
        try:
            _key_payload(key)
        except CatalogRejected:
            code = "INSTRUMENT_SOURCE_INPUT_INVALID"
        if code is not None:
            raise InstrumentSourceError(code)
        route = _InstrumentRoute(key, budget, group, datetime.now(UTC), time.monotonic(),
                                 budget.physical_attempts)
        try:
            with self._monitoring_operation(budget):
                await self._read("/v5/market/instruments-info",
                                 [("category", bybit_category(key.product)), ("symbol", key.symbol)],
                                 signed=False, route=route)
        except InstrumentSourceError as error:
            code = error.code
        except BybitBlocked:
            code = "INSTRUMENT_SOURCE_ACCESS_RESTRICTED"
        except BybitReadError as error:
            code = {
                "timeout": "TIMEOUT", "proxy": "NETWORK", "dns": "NETWORK", "network": "NETWORK",
                "response_too_large": "RESOURCE_LIMIT", "invalid_json": "JSON_INVALID",
                "redirect": "ORIGIN_MISMATCH", "http_403": "ACCESS_RESTRICTED",
                "http_451": "ACCESS_RESTRICTED", "ret_10006": "RATE_LIMITED",
                "http_status": "HTTP_REJECTED", "monitoring_operation_overlap": "OVERLAP",
            }.get(error.code, "RESPONSE_INVALID")
            code = "INSTRUMENT_SOURCE_" + code
        except MonitoringBudgetError as error:
            code = ("INSTRUMENT_SOURCE_GROUP_REQUIRED" if error.args == ("MONITORING_GROUP_REQUIRED",)
                    else "INSTRUMENT_SOURCE_BUDGET_EXHAUSTED")
        if code is not None:
            # Preserve the original safe classification while terminalizing the shared parent.
            try:
                budget.read_failed()
            except MonitoringBudgetError:
                pass
            raise InstrumentSourceError(code)
        assert route.envelope is not None
        return route.envelope

    async def read_risk_limit_envelope(
        self, key: InstrumentKey, *, cursor: str,
        budget: "MonitoringReadBudget", group: "ReadGroupToken", deadline: float | None,
        owner_guard: Callable[[], None] | None = None,
    ) -> "BybitRiskLimitEnvelopeReadV1":
        """Capture one public raw page; client lifetime IDs confer no source authority."""
        from ..execution.monitoring_budget import MonitoringBudgetError, MonitoringReadBudget
        from ..execution.risk_v2_contracts import _key_payload_v2
        from ..execution.risk_v2_math import RiskV2Rejected
        from .risk_limit_envelope import _reject_v1, _text_v1

        if type(budget) is not MonitoringReadBudget:
            raise InstrumentSourceError("INSTRUMENT_SOURCE_BUDGET_REQUIRED")
        code: str | None = None
        route: _RiskRoute | None = None
        source_failure: InstrumentSourceError | None = None
        try:
            if (type(self._public_source_environment) is not str
                    or self._public_source_environment not in ("PUBLIC_MAINNET", "PUBLIC_TESTNET")):
                raise InstrumentSourceError("INSTRUMENT_SOURCE_SOURCE_UNSUPPORTED")
            payload = _key_payload_v2(key)
            if payload["product"] != "LINEAR":
                _reject_v1("RISK_V2_UNSUPPORTED_PRODUCT")
            _text_v1(cursor, 1024)
            if deadline is not None and (type(deadline) is not float
                    or not math.isfinite(deadline) or deadline < 0):
                raise InstrumentSourceError("INSTRUMENT_SOURCE_INPUT_INVALID")
            if owner_guard is not None and not callable(owner_guard):
                raise InstrumentSourceError("INSTRUMENT_SOURCE_INPUT_INVALID")
            if self._risk_generation is None:
                raise InstrumentSourceError("INSTRUMENT_SOURCE_RETIRED")
            # Snapshot every original identity before the first route clock or guard.
            client, generation = self._risk_http_client, self._risk_generation
            source_id, source_environment = self._risk_source_id, self._public_source_environment
            operation_context, receipt_context = self._operation, budget._receipt
            receipt = receipt_context.get()
            params: tuple[tuple[str, str], ...] = (("category", "linear"), ("symbol", key.symbol))
            if cursor:
                params += (("cursor", cursor),)
            route = _RiskRoute(
                key, budget, group, datetime.now(UTC), 0.0, budget.physical_attempts,
                owner=self, client=client, source_environment=source_environment,
                source_id=source_id, generation=generation,
                observation_id=uuid4().hex, params=params, deadline=deadline,
                owner_guard=owner_guard, operation_context=operation_context,
                receipt_context=receipt_context, receipt=receipt,
            )
            started = time.monotonic()
            if type(started) is not float or not math.isfinite(started) or started < 0:
                raise InstrumentSourceError("INSTRUMENT_SOURCE_CLOCK_INVALID")
            route.started_mono = route.last_mono = started
            route.check()
            with self._monitoring_operation(budget):
                route.operation_entered = True
                await self._read("/v5/market/risk-limit", list(params), signed=False,
                                 deadline=deadline, route=route)
                route.check()
            assert route.risk_envelope is not None
            return route.risk_envelope
        except InstrumentSourceError as error:
            if route is not None and route.owner_failure is error:
                raise
            source_failure, code = error, error.code
        except BybitBlocked as error:
            if route is not None and route.owner_failure is error:
                raise
            code = "INSTRUMENT_SOURCE_ACCESS_RESTRICTED"
        except BybitReadError as error:
            if route is not None and route.owner_failure is error:
                raise
            translations = {
                "timeout": "TIMEOUT", "proxy": "NETWORK", "dns": "NETWORK", "network": "NETWORK",
                "response_too_large": "RESOURCE_LIMIT", "invalid_json": "JSON_INVALID",
                "redirect": "ORIGIN_MISMATCH", "http_403": "ACCESS_RESTRICTED",
                "http_451": "ACCESS_RESTRICTED", "ret_10006": "RATE_LIMITED",
                "http_status": "HTTP_REJECTED", "monitoring_operation_overlap": "OVERLAP",
            }
            if error.code not in translations:
                raise
            code = "INSTRUMENT_SOURCE_" + translations[error.code]
        except MonitoringBudgetError as error:
            if route is not None and route.owner_failure is error:
                raise
            translations = {
                "MONITORING_GROUP_REQUIRED": "GROUP_REQUIRED",
                "MONITORING_DEADLINE_OR_CLOCK": "BUDGET_EXHAUSTED",
                "MONITORING_LOGICAL_GET_LIMIT": "BUDGET_EXHAUSTED",
                "MONITORING_PHYSICAL_ATTEMPTS": "BUDGET_EXHAUSTED",
                "MONITORING_READ_FAILED": "BUDGET_EXHAUSTED",
            }
            if len(error.args) != 1 or error.args[0] not in translations:
                raise
            code = "INSTRUMENT_SOURCE_" + translations[error.args[0]]
        except RiskV2Rejected as error:
            if route is not None and route.owner_failure is error:
                raise
            try:
                budget.read_failed()
            except MonitoringBudgetError as error:
                if route is not None and route.owner_failure is error:
                    raise
            raise
        # Keep the primary finite error even when terminalizing an already failed parent.
        try:
            budget.read_failed()
        except MonitoringBudgetError as error:
            if route is not None and route.owner_failure is error:
                raise
        assert code is not None
        if source_failure is not None:
            raise source_failure from None
        raise InstrumentSourceError(code) from None

    async def _read(
        self, path: str, params: list[tuple[str, str]], *, signed: bool,
        deadline: float | None = None, route: _InstrumentRoute | None = None,
    ) -> tuple[dict[str, object], int | None]:
        if path not in READ_ONLY_PATHS:
            raise ReadOnlyViolation()
        operation = self._operation.get()
        if operation is not None:
            bounded = operation.begin_read()
            deadline = bounded if deadline is None else min(deadline, bounded)
        if isinstance(route, _RiskRoute):
            route.deadline = deadline
            route.check()
        if isinstance(route, _RiskRoute):
            assert route.receipt is not None
            route.logical_ordinal = route.receipt.logical
        elif route is not None:
            route.logical_ordinal = route.budget.require_active_group(route.group).logical_gets
        attempt = 0
        while True:
            self._raise_if_deadline(deadline)
            request = self._build_request(path, params, signed=signed)
            if route is not None:
                request.headers["Accept-Encoding"] = "identity"
                route.check()
                route.attempt = attempt
            if operation is not None and route is None:
                operation.before_attempt(attempt)
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise BybitReadError("timeout")
            try:
                send = self._bounded_response(request, route=route)
                if remaining is None:
                    response = await asyncio.wait_for(send, timeout=10.0)
                else:
                    response = await asyncio.wait_for(send, timeout=min(10.0, remaining))
            except TimeoutError as error:
                if isinstance(route, _RiskRoute) and route.owner_failure is error:
                    raise
                raise BybitReadError("timeout") from None
            except httpx.TimeoutException as error:
                if isinstance(route, _RiskRoute) and route.owner_failure is error:
                    raise
                raise BybitReadError("timeout") from None
            except httpx.ProxyError as error:
                if isinstance(route, _RiskRoute) and route.owner_failure is error:
                    raise
                raise BybitReadError("proxy") from None
            except httpx.HTTPError as exc:
                if isinstance(route, _RiskRoute) and route.owner_failure is exc:
                    raise
                # Classify from the exception type and a few fixed phrases.
                # Do not copy the exception text forward; it can contain request details.
                detail = str(exc).lower()
                if (
                    "name resolution" in detail
                    or "getaddrinfo" in detail
                    or "nodename nor servname" in detail
                ):
                    raise BybitReadError("dns") from None
                raise BybitReadError("network") from None
            if route is not None:
                route.check()
            if operation is not None and route is None:
                operation.response(response.status_code, response.headers)
            if response.status_code in _RETRY_STATUSES and attempt < len(self.retry_waits):
                await response.aclose()
                if isinstance(route, _RiskRoute):
                    route.check()
                self._raise_if_deadline(deadline)
                await self._sleeper(self.retry_waits[attempt])
                if route is not None:
                    route.check()
                attempt += 1
                continue
            # Account info can return retCode 0 with no envelope time.
            # Wallet, positions, and orders still require a positive server time.
            try:
                if route is None:
                    accepted = self._accepted(response, require_time=path != "/v5/account/info")
                elif isinstance(route, _RiskRoute):
                    accepted = self._accepted_risk(response, route)
                else:
                    if 300 <= response.status_code < 400:
                        raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
                    if response.status_code != 200:
                        raise InstrumentSourceError("INSTRUMENT_SOURCE_HTTP_REJECTED")
                    assert route.sent_origin is not None and route.response_origin is not None
                    assert route.received_at is not None and route.received_mono is not None
                    envelope, result, stamp = _accepted_envelope_read(
                        key=route.key, sent_origin=route.sent_origin,
                        response_origin=route.response_origin, request_started_at=route.started_at,
                        received_at=route.received_at, started_mono=route.started_mono,
                        received_mono=route.received_mono, raw=response.content,
                        accepted_attempt=attempt, physical_start=route.physical_start,
                        physical_end=route.budget.physical_attempts,
                        logical_ordinal=route.logical_ordinal)
                    route.envelope = envelope
                    accepted = result, stamp
            except (BybitReadError, BybitBlocked) as error:
                if isinstance(route, _RiskRoute) and route.owner_failure is error:
                    raise
                if operation is not None:
                    operation.read_failed()
                raise
            if operation is not None:
                operation.validate_response(*accepted)
                if isinstance(route, _RiskRoute):
                    route.check()
                operation.check()
            if isinstance(route, _RiskRoute):
                route.check()
            return accepted

    def _accepted_risk(self, response: httpx.Response,
                       route: _RiskRoute) -> tuple[dict[str, object], int]:
        from .risk_limit_envelope import (
            BybitRiskLimitEnvelopeReadV1,
            _decode_risk_limit_json_v1,
            _integer_token_v1,
            _reject_v1,
        )

        route.check()
        if 300 <= response.status_code < 400:
            raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
        if response.status_code != 200:
            raise InstrumentSourceError("INSTRUMENT_SOURCE_HTTP_REJECTED")
        body = _decode_risk_limit_json_v1(response.content)
        route.check()
        if _region_message(body.get("retMsg")):
            raise InstrumentSourceError("INSTRUMENT_SOURCE_ACCESS_RESTRICTED")
        ret = _integer_token_v1(body.get("retCode"), -(2**31), 2**31 - 1)
        if ret != 0:
            raise InstrumentSourceError("INSTRUMENT_SOURCE_RATE_LIMITED" if ret == 10006
                                        else "INSTRUMENT_SOURCE_PROVIDER_REJECTED")
        stamp = _integer_token_v1(body.get("time"), 1, 253402300799999)
        result = body.get("result")
        if type(result) is not dict:
            _reject_v1()
        assert route.sent_origin is not None and route.response_origin is not None
        assert route.received_at is not None and route.received_mono is not None
        # Constructor independently revalidates the same original bytes. See root ruling.
        route.risk_envelope = BybitRiskLimitEnvelopeReadV1(
            key=route.key, method="GET", endpoint="/v5/market/risk-limit", params=route.params,
            sent_origin=route.sent_origin, response_origin=route.response_origin,
            response_status=200, request_started_at=route.started_at,
            received_at=route.received_at, started_mono=route.started_mono,
            received_mono=route.received_mono,
            response_time=datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=stamp),
            decoded_body_bytes=response.content,
            decoded_body_sha256=hashlib.sha256(response.content).hexdigest(),
            accepted_attempt=route.attempt + 1, physical_start=route.physical_start,
            physical_end=route.budget.physical_attempts, logical_ordinal=route.logical_ordinal,
            observation_id=route.observation_id, source_id=route.source_id,
            source_generation=route.generation, source_environment=route.source_environment,
        )
        route.check()
        return cast(dict[str, object], result), stamp

    async def _bounded_response(self, request: httpx.Request, *,
                                route: _InstrumentRoute | None = None) -> httpx.Response:
        if route is not None:
            route.check()
            route.sent_origin = (route.request_origin(request) if isinstance(route, _RiskRoute)
                                 else _check_exact_request(request, route.key))
            # wait_for schedules this coroutine. Charge before the final
            # callbacks/proof; rejection at that boundary is still charged.
            route.budget.before_attempt(route.attempt)
            if isinstance(route, _RiskRoute):
                route.check(request)
        response = await self._client.send(request, stream=True)
        primary: BaseException | None = None
        try:
            if route is not None:
                # Quota/ban observation must precede any encoding/body/origin rejection.
                from ..execution.monitoring_budget import MonitoringBudgetError

                quota_code: str | None = None
                quota = response.headers.get("x-bapi-limit-status")
                unparseable_quota = False
                if quota is not None and quota.isdigit():
                    try:
                        int(quota)
                    except ValueError:
                        unparseable_quota = True
                try:
                    route.budget.response(response.status_code, response.headers)
                except MonitoringBudgetError as error:
                    if isinstance(route, _RiskRoute) and route.owner_failure is error:
                        raise
                    if error.args == ("MONITORING_QUOTA_OR_BAN",):
                        quota_code = ("INSTRUMENT_SOURCE_ACCESS_RESTRICTED"
                                      if response.status_code in (403, 451)
                                      else "INSTRUMENT_SOURCE_RATE_LIMITED")
                    elif (not isinstance(route, _RiskRoute) or error.args in (
                        ("MONITORING_DEADLINE_OR_CLOCK",), ("MONITORING_LOGICAL_GET_LIMIT",),
                        ("MONITORING_PHYSICAL_ATTEMPTS",), ("MONITORING_READ_FAILED",),
                    )):
                        quota_code = "INSTRUMENT_SOURCE_BUDGET_EXHAUSTED"
                    else:
                        raise
                except ValueError as error:
                    if isinstance(route, _RiskRoute) and route.owner_failure is error:
                        raise
                    # Only a proven failure in that hook's integer conversion
                    # is expected here. A later owner's programming error must
                    # propagate even when the header is long or non-ASCII.
                    if not unparseable_quota:
                        raise
                    quota_code = "INSTRUMENT_SOURCE_RATE_LIMITED"
                if quota_code is not None:
                    raise InstrumentSourceError(quota_code)
                route.check()
                actual_request: httpx.Request | None = None
                try:
                    # HTTPX's application association on the ORIGINAL response;
                    # it is not independently supplied transport/server metadata.
                    actual_request = response.request
                except RuntimeError:
                    pass
                if actual_request is None or response.history:
                    raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
                route.response_origin = (route.request_origin(actual_request)
                                         if isinstance(route, _RiskRoute)
                                         else _check_exact_request(actual_request, route.key))
                if route.response_origin != route.sent_origin:
                    raise InstrumentSourceError("INSTRUMENT_SOURCE_ORIGIN_MISMATCH")
                encodings = response.headers.get_list("content-encoding")
                if len(encodings) > 1 or (encodings and encodings[0] not in ("", "identity")):
                    raise InstrumentSourceError("INSTRUMENT_SOURCE_ENCODING_UNSUPPORTED")
                lengths = response.headers.get_list("content-length")
                if lengths:
                    length = lengths[0]
                    if (len(lengths) != 1 or not length or not length.isascii()
                            or not length.isdecimal()):
                        raise InstrumentSourceError("INSTRUMENT_SOURCE_RESPONSE_INVALID")
                    significant = length.lstrip("0") or "0"
                    if len(significant) > 5 or int(significant) > 65536:
                        raise InstrumentSourceError("INSTRUMENT_SOURCE_RESOURCE_LIMIT")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                if route is not None:
                    route.check()
                if len(data) + len(chunk) > (_MAX_BYTES if route is None else 65536):
                    raise BybitReadError("response_too_large")
                data.extend(chunk)
            if route is not None:
                route.received_at = datetime.now(UTC)
                route.received_mono = time.monotonic()
                route.check()
            # aiter_bytes() already decodes content. The wire encoding/length
            # headers no longer describe this body and must not trigger decoding again.
            return httpx.Response(response.status_code, content=bytes(data), request=request,
                headers={key: value for key, value in response.headers.items()
                         if key.lower() in {"x-bapi-limit", "x-bapi-limit-status",
                                            "x-bapi-limit-reset-timestamp"}})
        except BaseException as error:
            primary = error
            raise
        finally:
            if isinstance(route, _RiskRoute):
                try:
                    await response.aclose()
                except Exception:
                    # Ordinary cleanup faults cannot replace an earlier primary.
                    # Fresh cancellation/process-control BaseExceptions still win.
                    if primary is None:
                        raise
                if primary is None:
                    route.check()
            else:
                await response.aclose()

    async def read_pages(
        self,
        path: str,
        params: list[tuple[str, str]],
        *,
        deadline: float | None = None,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        cursor = ""
        for _ in range(_PAGE_LIMIT):
            page = list(params)
            page.append(("limit", "50"))
            if cursor:
                page.append(("cursor", cursor))
            result, _server_ms = await self.read(path, page, signed=True, deadline=deadline)
            for item in _as_list(result.get("list")):
                rows.append(_mapping(item))
            cursor = _next_cursor(result)
            if not cursor:
                return rows
        raise BybitReadError("truncated")

    def _accepted(
        self, response: httpx.Response, *, require_time: bool
    ) -> tuple[dict[str, object], int | None]:
        if response.status_code in {403, 451}:
            snippet = ""
            if len(response.content) <= _MAX_BYTES:
                snippet = response.content.decode("utf-8", errors="ignore")
            if _region_message(snippet):
                raise BybitBlocked()
            code = "http_403" if response.status_code == 403 else "http_451"
            raise BybitReadError(code)
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
        result = _mapping(body.get("result"))
        server_ms = _positive_time(body.get("time"))
        if server_ms is None:
            if require_time:
                raise BybitReadError("invalid_json")
            return result, None
        return result, server_ms


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
