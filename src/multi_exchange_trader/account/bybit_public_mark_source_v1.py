"""Original public ticker issuance only; no freshness or financial qualification."""

import asyncio
import hashlib
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, NoReturn
from uuid import uuid4

import httpx
from httpx._client import ClientState

from ..execution.bybit_entry_market_projections_v1 import project_bybit_mark_price_v1
from ..execution.bybit_linear_model_inputs_v1 import (
    BybitDecimalFieldV1,
    BybitModelSourceRefV1,
    _symbol_v1,
    bybit_source_ref_sha256_v1,
)
from ..execution.monitoring_budget import MonitoringBudgetError, MonitoringReadBudget
from ..market_data.public_client import parse_blocked_hosts
from .bybit_readonly import (
    BybitBlocked,
    BybitReadError,
    BybitReadOnlyClient,
    _account_info_headers,
    _AccountInfoHeaderWire,
    _parse_json,
    _region_message,
    _risk_request_wire,
    _RiskWire,
)
from .instrument_envelope import HttpsOrigin
from .risk_limit_envelope import _count_v1


def _fail() -> NoReturn:
    raise BybitReadError("public_mark_source_invalid")


def _source_values(source: BybitModelSourceRefV1) -> tuple[object, ...]:
    """Finite closed source14 leaves, compared after the last caller callback."""
    if type(source) is not BybitModelSourceRefV1 or source.account_scope is not None:
        _fail()
    text = (source.observation_id, source.source_id, source.source_generation,
        source.source_environment, source.path, source.raw_body_sha256)
    origin = source.origin
    if (any(type(v) is not str for v in text) or type(origin) is not HttpsOrigin
            or type(origin.scheme) is not str or type(origin.host) is not str
            or type(origin.port) is not int or type(source.query) is not tuple
            or len(source.query) != 2):
        _fail()
    query: list[str] = []
    for pair in source.query:
        if type(pair) is not tuple or len(pair) != 2 or any(type(v) is not str for v in pair):
            _fail()
        query.extend(pair)
    for stamp in (source.request_started_at, source.received_at):
        if type(stamp) is not datetime or stamp.tzinfo is not UTC:
            _fail()
    for count in (source.envelope_time_ms, source.page_ordinal, source.accepted_attempt):
        if type(count) is not int:
            _fail()
    return (*text, origin.scheme, origin.host, origin.port, *query,
        source.request_started_at, source.received_at, source.envelope_time_ms,
        source.page_ordinal, source.accepted_attempt)


@dataclass(frozen=True, repr=False)
class _MarkCapture:
    source: BybitModelSourceRefV1
    source_values: tuple[object, ...]
    raw: bytes
    request: httpx.Request
    wire: _RiskWire
    response: httpx.Response
    headers: httpx.Headers
    header_wire: _AccountInfoHeaderWire
    owner_guard: Callable[[], None]
    budget: MonitoringReadBudget
    physical_start: int
    physical_end: int
    started_mono: float
    received_mono: float


class BybitPublicMarkSessionV1:
    """Keyless fixed-route issuer, with at most the catalog's 100 symbol slots.

    Successful capture proves original public transport/raw custody only. A later
    consumer must establish current-time/model/financial eligibility separately.
    """

    def __init__(self, *, source_environment: Literal["PUBLIC_MAINNET", "PUBLIC_TESTNET"],
                 symbols: tuple[str, ...]) -> None:
        if type(symbols) is not tuple or not 1 <= len(symbols) <= 100:
            _fail()
        for symbol in symbols:
            _symbol_v1(symbol)
        if len(set(symbols)) != len(symbols):
            _fail()
        self._owner = BybitReadOnlyClient.for_public_instruments(source_environment=source_environment)
        self._original_owner = self._owner
        self._self = self
        self._client = self._owner._client
        self._transport = self._client._transport
        self._mounts = self._client._mounts
        self._environment = source_environment
        self._host = "api.bybit.com" if source_environment == "PUBLIC_MAINNET" else "api-testnet.bybit.com"
        self._owner_generation = self._owner._risk_generation
        self._symbols = frozenset(symbols)
        self._source_id, self._generation = uuid4().hex, uuid4().hex
        self._slots: dict[str, _MarkCapture] = {}
        self._busy: object | None = None
        self._requiring: object | None = None
        self._epoch = object()
        self._closed = False
        self._denied = False

    def _retire(self) -> None:
        self._slots.clear()
        self._epoch = object()

    def _owner_current(self) -> None:
        owner, client = self._owner, self._client
        if (self._self is not self or self._closed is not False
                or owner is not self._original_owner
                or type(owner) is not BybitReadOnlyClient or type(client) is not httpx.AsyncClient
                or owner._client is not client or owner._risk_http_client is not client
                or type(owner._public_source_environment) is not str
                or owner._public_source_environment != self._environment
                or type(owner.environment) is not str
                or owner.environment != ("mainnet" if self._environment == "PUBLIC_MAINNET" else "testnet")
                or type(owner.base_url) is not str or owner.base_url != "https://" + self._host
                or owner._risk_generation is not self._owner_generation
                or owner._injected_transport is not False or owner._credential_check is not None
                or type(owner._api_key) is not str or owner._api_key != ""
                or type(owner._api_secret) is not str or owner._api_secret != ""
                or client._transport is not self._transport or client._mounts is not self._mounts
                or type(client._mounts) is not dict or len(client._mounts) != 0
                or client.trust_env is not False or client.follow_redirects is not False
                or type(client._state) is not ClientState
                or client._state not in (ClientState.UNOPENED, ClientState.OPENED)):
            self._retire()
            _fail()
        try:
            blocked = parse_blocked_hosts(os.environ.get("MARKET_DATA_BLOCKED_HOSTS", ""))
        except ValueError:
            self._denied = True
            self._retire()
            _fail()
        if self._denied or self._host in blocked:
            self._denied = True
            self._retire()
            raise BybitBlocked()

    def _request(self, request: httpx.Request, symbol: str, expected: _RiskWire | None = None) -> _RiskWire:
        if type(request) is not httpx.Request:
            _fail()
        _encoding = request.headers.encoding
        wire = _risk_request_wire(request, expected)
        parsed = wire[3]
        query = "category=linear&symbol=" + symbol
        if (wire[2] != "GET" or parsed.scheme != "https" or parsed.host != self._host
                or parsed.port not in (None, 443) or parsed.userinfo or parsed.fragment
                or parsed.path != "/v5/market/tickers" or parsed.query != query
                or request.url.raw_path != ("/v5/market/tickers?" + query).encode("ascii")
                or request.content != b"" or request.headers.get_list("accept-encoding") != ["identity"]):
            _fail()
        return wire

    def _response(self, cap: _MarkCapture) -> None:
        response = cap.response
        if (type(response) is not httpx.Response or response.request is not cap.request
                or response.headers is not cap.headers or response.history
                or type(response.status_code) is not int or response.status_code != 200
                or response.is_closed is not True
                or _account_info_headers(cap.headers) != cap.header_wire):
            _fail()

    async def capture_mark_v1(self, symbol: str, *, budget: MonitoringReadBudget,
                              owner_guard: Callable[[], None]) -> BybitModelSourceRefV1:
        _symbol_v1(symbol)
        if symbol not in self._symbols or type(budget) is not MonitoringReadBudget or not callable(owner_guard):
            _fail()
        if self._busy is not None or self._requiring is not None:
            self._retire()
            _fail()
        self._owner_current()
        self._slots.pop(symbol, None)
        token, epoch = object(), self._epoch
        self._busy = token
        response: httpx.Response | None = None
        failed = True
        blocked = False
        try:
            with budget.reuse_or_open_receipt() as group:
                deadline = budget.begin_read()
                started_at, started = datetime.now(UTC), time.monotonic()
                last = started
                request = self._client.build_request("GET", "https://" + self._host + "/v5/market/tickers",
                    params=(("category", "linear"), ("symbol", symbol)), headers={"Accept-Encoding": "identity"})
                wire = self._request(request, symbol)
                def check() -> None:
                    nonlocal last
                    budget.require_active_group(group)
                    now = time.monotonic()
                    if type(now) is not float or not math.isfinite(now) or now < last or now >= deadline:
                        _fail()
                    last = now
                    owner_guard()
                    self._owner_current()
                    if self._busy is not token or self._epoch is not epoch:
                        _fail()
                    self._request(request, symbol, wire)
                check()
                physical_start = budget.physical_attempts
                budget.before_attempt(0)
                check()
                remaining = min(10.0, deadline - time.monotonic())
                if remaining <= 0:
                    _fail()
                async with asyncio.timeout(remaining):
                    response = await httpx.AsyncClient.send(self._client, request, stream=True)
                    primary: BaseException | None = None
                    try:
                        if type(response) is not httpx.Response:
                            _fail()
                        headers = response.headers
                        status = response.status_code
                        _encoding = headers.encoding
                        header_wire: _AccountInfoHeaderWire | None = None
                        try:
                            header_wire = _account_info_headers(headers)
                        except BybitReadError:
                            pass  # Original quota/ban observation has priority over header rejection.
                        if status in (403, 451):
                            self._denied = blocked = True
                            self._retire()
                        budget.response(status, headers)
                        check()
                        if (header_wire is None or type(status) is not int or status != 200
                                or response.request is not request or response.history
                                or type(response.status_code) is not int or response.status_code != status):
                            _fail()
                        encodings, lengths = headers.get_list("content-encoding"), headers.get_list("content-length")
                        if len(encodings) > 1 or encodings and encodings[0] not in ("", "identity"):
                            _fail()
                        expected_length: int | None = None
                        if lengths:
                            length = lengths[0]
                            if (len(lengths) != 1 or not length or not length.isascii() or not length.isdecimal()
                                    or len(length.lstrip("0") or "0") > 5):
                                _fail()
                            expected_length = int(length.lstrip("0") or "0")
                            if expected_length > 65536:
                                _fail()
                        data = bytearray()
                        async for chunk in response.aiter_raw():
                            check()
                            if type(chunk) is not bytes or len(data) + len(chunk) > 65536:
                                _fail()
                            data.extend(chunk)
                        if expected_length is not None and len(data) != expected_length:
                            _fail()
                        raw = bytes(data)
                        received_at, received = datetime.now(UTC), time.monotonic()
                        check()
                    except BaseException as error:
                        primary = error
                        raise
                    finally:
                        try:
                            await response.aclose()
                        except Exception:
                            if primary is None:
                                raise
                check()
                envelope = _parse_json(raw)
                if type(envelope) is not dict:
                    _fail()
                if _region_message(envelope.get("retMsg")):
                    self._denied = blocked = True
                    self._retire()
                    raise BybitBlocked()
                stamp = _count_v1(envelope.get("time"), 1, 253402300799999)
                source = BybitModelSourceRefV1(uuid4().hex, self._source_id, self._generation, None,
                    self._environment, HttpsOrigin("https", self._host, 443), "/v5/market/tickers",
                    (("category", "linear"), ("symbol", symbol)), started_at, received_at, stamp,
                    hashlib.sha256(raw).hexdigest(), 1, 1)
                project_bybit_mark_price_v1(source, raw, symbol=symbol)
                cap = _MarkCapture(source, _source_values(source), raw, request, wire, response,
                    headers, header_wire, owner_guard, budget, physical_start, budget.physical_attempts,
                    started, received)
                self._response(cap)
                check()
                self._response(cap)
                if (_source_values(source) != cap.source_values
                        or type(budget.physical_attempts) is not int
                        or budget.physical_attempts != cap.physical_end
                        or cap.physical_end != physical_start + 1):
                    _fail()
            # Receipt has closed normally. No budget or clock is needed to require this source later.
            self._owner_current()
            if self._busy is not token or self._epoch is not epoch:
                _fail()
            self._slots[symbol] = cap
            failed = False
            return source
        except Exception:  # noqa: BLE001 - capture failures become a fixed public code.
            try:
                budget.read_failed()
            except MonitoringBudgetError:
                pass
        finally:
            if failed:
                self._slots.pop(symbol, None)
            if self._busy is token:
                self._busy = None
        if blocked:
            raise BybitBlocked() from None
        raise BybitReadError("public_mark_capture_failed") from None

    def _current(self, source: BybitModelSourceRefV1, symbol: str) -> _MarkCapture:
        self._owner_current()
        _symbol_v1(symbol)
        cap = self._slots.get(symbol)
        if (self._busy is not None or cap is None or cap.source is not source
                or _source_values(source) != cap.source_values):
            _fail()
        self._request(cap.request, symbol, cap.wire)
        self._response(cap)
        return cap

    def require_mark_v1(self, source: BybitModelSourceRefV1, *, symbol: str) -> None:
        if self._busy is not None or self._requiring is not None:
            self._retire()
            _fail()
        token, epoch = object(), self._epoch
        self._requiring = token
        try:
            cap = self._current(source, symbol)
            bybit_source_ref_sha256_v1(source)
            raw = cap.raw
            if type(raw) is not bytes or hashlib.sha256(raw).hexdigest() != source.raw_body_sha256:
                _fail()
            try:
                cap.owner_guard()
            except Exception:  # noqa: BLE001 - any owner callback failure retires sources.
                self._retire()
                raise BybitReadError("public_mark_source_invalid") from None
            if (self._requiring is not token or self._epoch is not epoch
                    or self._current(source, symbol) is not cap or cap.raw is not raw):
                _fail()
        finally:
            if self._requiring is token:
                self._requiring = None

    def project_mark_v1(self, source: BybitModelSourceRefV1, *, symbol: str) -> BybitDecimalFieldV1:
        self.require_mark_v1(source, symbol=symbol)
        cap = self._current(source, symbol)
        return project_bybit_mark_price_v1(source, cap.raw, symbol=symbol)

    async def aclose(self) -> None:
        self._closed = True
        self._retire()
        await self._owner.aclose()
