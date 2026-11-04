"""Original HTTPX send intercepted offline; this fixture is not live qualification."""

import hashlib
import importlib
import json
from collections.abc import AsyncIterator
from copy import copy
from decimal import Decimal
from types import ModuleType

import httpx
import pytest

from multi_exchange_trader.account.bybit_public_mark_source_v1 import BybitPublicMarkSessionV1
from multi_exchange_trader.account.bybit_readonly import BybitBlocked, BybitReadError
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget


def api() -> ModuleType:
    return importlib.import_module("multi_exchange_trader.account.bybit_public_mark_source_v1")


def body(symbol: str = "BTCUSDT") -> bytes:
    return json.dumps({"retCode": 0, "retMsg": "OK", "time": 1767225600000,
        "result": {"category": "linear", "list": [{"symbol": symbol,
            "markPrice": "91234.5000", "lastPrice": "99999.0"}]}}, separators=(",", ":")).encode()


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks
        self.reads = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_original_public_httpx_captures_exact_raw_and_projects_after_receipt_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = api().BybitPublicMarkSessionV1(source_environment="PUBLIC_MAINNET", symbols=("BTCUSDT",))
    raw = body()
    content = Stream((raw[:7], raw[7:]))
    calls: list[tuple[httpx.AsyncClient, httpx.Request]] = []
    responses: list[httpx.Response] = []
    async def send(client: httpx.AsyncClient, request: httpx.Request, *, stream: bool) -> httpx.Response:
        assert type(client) is httpx.AsyncClient and stream
        calls.append((client, request))
        response = httpx.Response(200, request=request, stream=content,
            headers={"x-bapi-limit-status": "9", "content-length": str(len(raw))})
        responses.append(response)
        return response
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    budget = MonitoringReadBudget()
    try:
        ref = await session.capture_mark_v1("BTCUSDT", budget=budget, owner_guard=lambda: None)
        assert len(calls) == budget.physical_attempts == 1
        client, request = calls[0]
        assert client.trust_env is False and client.follow_redirects is False
        assert str(request.url) == "https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT"
        assert request.method == "GET" and request.content == b""
        assert not any(key.lower().startswith("x-bapi") for key in request.headers)
        assert request.headers["accept-encoding"] == "identity"
        assert ref.raw_body_sha256 == hashlib.sha256(raw).hexdigest()
        assert ref.account_scope is None and ref.source_environment == "PUBLIC_MAINNET"
        assert ref.envelope_time_ms == 1767225600000 and ref.accepted_attempt == ref.page_ordinal == 1
        assert content.closed and responses[0].is_closed and content.reads == 2
        session.require_mark_v1(ref, symbol="BTCUSDT")
        mark = session.project_mark_v1(ref, symbol="BTCUSDT")
        assert mark.reference.source is ref and mark.reference.json_pointer == "/result/list/0/markPrice"
        assert mark.reference.wire_spelling == "91234.5000"
        assert mark.value.as_tuple() == Decimal("91234.5000").as_tuple()
    finally:
        await session.aclose()


def intercepted(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[BybitPublicMarkSessionV1, list[httpx.Request], list[Stream]]:
    session = BybitPublicMarkSessionV1(source_environment="PUBLIC_TESTNET", symbols=("BTCUSDT", "ETHUSDT"))
    requests: list[httpx.Request] = []
    streams: list[Stream] = []
    async def send(client: httpx.AsyncClient, request: httpx.Request, *, stream: bool) -> httpx.Response:
        assert type(client) is httpx.AsyncClient and stream
        assert request.url.host == "api-testnet.bybit.com"
        requests.append(request)
        content = Stream((body(request.url.params["symbol"]),))
        streams.append(content)
        return httpx.Response(200, request=request, stream=content)
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return session, requests, streams


@pytest.mark.asyncio
async def test_symbol_slots_coexist_replace_independently_and_reject_copies_foreign_and_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, requests, streams = intercepted(monkeypatch)
    foreign = BybitPublicMarkSessionV1(source_environment="PUBLIC_TESTNET", symbols=("BTCUSDT",))
    try:
        first = await session.capture_mark_v1("BTCUSDT", budget=MonitoringReadBudget(), owner_guard=lambda: None)
        other = await session.capture_mark_v1("ETHUSDT", budget=MonitoringReadBudget(), owner_guard=lambda: None)
        session.require_mark_v1(first, symbol="BTCUSDT")
        session.require_mark_v1(other, symbol="ETHUSDT")
        for invalid in (copy(first), other):
            with pytest.raises(BybitReadError):
                session.require_mark_v1(invalid, symbol="BTCUSDT")
        with pytest.raises(BybitReadError):
            foreign.require_mark_v1(first, symbol="BTCUSDT")
        fresh = await session.capture_mark_v1("BTCUSDT", budget=MonitoringReadBudget(), owner_guard=lambda: None)
        with pytest.raises(BybitReadError):
            session.require_mark_v1(first, symbol="BTCUSDT")
        session.require_mark_v1(fresh, symbol="BTCUSDT")
        session.require_mark_v1(other, symbol="ETHUSDT")
        assert len(requests) == 3 and all(s.closed for s in streams)
        await session.aclose()
        for ref, symbol in ((fresh, "BTCUSDT"), (other, "ETHUSDT")):
            with pytest.raises(BybitReadError):
                session.require_mark_v1(ref, symbol=symbol)
    finally:
        await session.aclose()
        await foreign.aclose()


@pytest.mark.parametrize("symbols", [(), ("BTCUSDT", "BTCUSDT"), tuple("S" + str(i) for i in range(101))])
def test_symbols_are_closed_unique_and_bounded_to_catalog_capacity(symbols: tuple[str, ...]) -> None:
    with pytest.raises(BybitReadError):
        BybitPublicMarkSessionV1(source_environment="PUBLIC_MAINNET", symbols=symbols)


@pytest.mark.asyncio
async def test_operator_blocked_host_refuses_before_any_physical_send(monkeypatch: pytest.MonkeyPatch) -> None:
    session, requests, _ = intercepted(monkeypatch)
    monkeypatch.setenv("MARKET_DATA_BLOCKED_HOSTS", "api.bybit.com")
    mainnet = BybitPublicMarkSessionV1(source_environment="PUBLIC_MAINNET", symbols=("BTCUSDT",))
    budget = MonitoringReadBudget()
    try:
        with pytest.raises(BybitBlocked):
            await mainnet.capture_mark_v1("BTCUSDT", budget=budget, owner_guard=lambda: None)
        assert not requests and budget.physical_attempts == 0
    finally:
        await mainnet.aclose()
        await session.aclose()


@pytest.mark.parametrize("fault", ["redirect", "gzip", "origin", "header-size", "body-size",
    "length", "time", "mark", "quota", "region"])
@pytest.mark.asyncio
async def test_fixed_original_response_body_and_budget_failures_never_issue_or_retry(
    monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    session, requests, _ = intercepted(monkeypatch)
    raw = body()
    if fault == "time":
        decoded = json.loads(raw)
        decoded["time"] = True
        raw = json.dumps(decoded).encode()
    elif fault == "mark":
        decoded = json.loads(raw)
        decoded["result"]["list"][0].pop("markPrice")
        raw = json.dumps(decoded).encode()
    elif fault == "region":
        decoded = json.loads(raw)
        decoded["retMsg"] = "restricted location"
        raw = json.dumps(decoded).encode()
    content = Stream((b"x" * 65537 if fault == "body-size" else raw,))
    headers = {"content-encoding": "gzip"} if fault == "gzip" else {}
    if fault == "header-size":
        headers["content-length"] = "65537"
    elif fault == "length":
        headers["content-length"] = "1"
    elif fault == "quota":
        headers["x-bapi-limit-status"] = "0"
    async def send(client: httpx.AsyncClient, request: httpx.Request, *, stream: bool) -> httpx.Response:
        requests.append(request)
        actual = httpx.Request("GET", "https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT") if fault == "origin" else request
        return httpx.Response(302 if fault == "redirect" else 200, request=actual,
            headers=headers, stream=content)
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    budget = MonitoringReadBudget()
    try:
        with pytest.raises((BybitReadError, BybitBlocked)):
            await session.capture_mark_v1("BTCUSDT", budget=budget, owner_guard=lambda: None)
        assert len(requests) == budget.physical_attempts == 1 and content.closed
        if fault in ("redirect", "gzip", "origin", "header-size", "quota"):
            assert content.reads == 0
        assert not session._slots
    finally:
        await session.aclose()


@pytest.mark.parametrize("fault", ["deadline", "physical", "unknown-symbol"])
@pytest.mark.asyncio
async def test_exhausted_budget_or_unselected_symbol_refuses_without_send(
    monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    session, requests, _ = intercepted(monkeypatch)
    budget = MonitoringReadBudget()
    if fault == "deadline":
        budget.deadline = 0.0
    elif fault == "physical":
        budget.physical_attempts = 200
    try:
        with pytest.raises(BybitReadError):
            await session.capture_mark_v1("SOLUSDT" if fault == "unknown-symbol" else "BTCUSDT",
                budget=budget, owner_guard=lambda: None)
        assert not requests
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_caught_capture_reentry_taints_outer_before_send(monkeypatch: pytest.MonkeyPatch) -> None:
    session, requests, _ = intercepted(monkeypatch)
    def guard() -> None:
        coroutine = session.capture_mark_v1("ETHUSDT", budget=MonitoringReadBudget(), owner_guard=lambda: None)
        with pytest.raises(BybitReadError):
            coroutine.send(None)
    try:
        with pytest.raises(BybitReadError):
            await session.capture_mark_v1("BTCUSDT", budget=MonitoringReadBudget(), owner_guard=guard)
        assert not requests and not session._slots
    finally:
        await session.aclose()


@pytest.mark.parametrize("change", ["close", "owner-copy"])
@pytest.mark.asyncio
async def test_original_shared_client_owner_or_close_retires_all_sources(
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    session, requests, _ = intercepted(monkeypatch)
    original = session._owner
    try:
        ref = await session.capture_mark_v1("BTCUSDT", budget=MonitoringReadBudget(), owner_guard=lambda: None)
        if change == "close":
            await session._owner.aclose()
        else:
            session._owner = copy(original)
        with pytest.raises(BybitReadError):
            session.require_mark_v1(ref, symbol="BTCUSDT")
        assert len(requests) == 1 and not session._slots
    finally:
        session._owner = original
        await session.aclose()


@pytest.mark.parametrize("field", ["header", "source"])
@pytest.mark.asyncio
async def test_final_owner_callback_cannot_change_source_or_original_header_in_place(
    monkeypatch: pytest.MonkeyPatch, field: str,
) -> None:
    session, _, _ = intercepted(monkeypatch)
    changed = False
    def guard() -> None:
        if changed:
            cap = session._slots["BTCUSDT"]
            if field == "header":
                cap.headers["x-bapi-limit-status"] = "8"
            else:
                object.__setattr__(cap.source, "observation_id", "changed")
    try:
        ref = await session.capture_mark_v1("BTCUSDT", budget=MonitoringReadBudget(), owner_guard=guard)
        changed = True
        with pytest.raises(BybitReadError):
            session.require_mark_v1(ref, symbol="BTCUSDT")
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_caught_require_reentry_retires_same_original_source_and_denies_outer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, requests, _ = intercepted(monkeypatch)
    retained: list[object] = []
    nested: list[str] = []
    armed = False
    entering = False
    def guard() -> None:
        nonlocal entering
        if armed and not entering:
            entering = True
            try:
                session.require_mark_v1(ref, symbol="BTCUSDT")
            except BybitReadError:
                nested.append("denied")
            else:
                nested.append("returned")
    try:
        ref = await session.capture_mark_v1("BTCUSDT", budget=MonitoringReadBudget(), owner_guard=guard)
        retained.append(ref)
        session.require_mark_v1(ref, symbol="BTCUSDT")  # Original source remains usable before reentry.
        armed = True
        with pytest.raises(BybitReadError):
            session.require_mark_v1(ref, symbol="BTCUSDT")
        assert nested == ["denied"] and retained[0] is ref
        assert not session._slots and len(requests) == 1
        with pytest.raises(BybitReadError):
            session.require_mark_v1(ref, symbol="BTCUSDT")
    finally:
        await session.aclose()
