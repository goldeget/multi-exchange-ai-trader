"""Intercepted original HTTPX: structural RAW checks, never live source authority."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from multi_exchange_trader.account.bybit_readonly import BybitReadOnlyClient
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget

KEY, SECRET = "M3-OFFLINE-KEY-DO-NOT-USE", "M3-OFFLINE-SECRET-DO-NOT-USE"
RAW = b'{"retCode":0, "result":{"marginMode":"ISOLATED_MARGIN"}}'


async def test_actual_private_capture_keeps_accepted_bytes_without_server_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    async def intercepted_send(_self: httpx.AsyncClient, request: httpx.Request,
                               **_kwargs: Any) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=RAW, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", intercepted_send)
    client = BybitReadOnlyClient(api_key=KEY, api_secret=SECRET, environment="demo",
                                base_url="https://api-demo.bybit.com")
    client.anchor_server_time(int(datetime.now(UTC).timestamp() * 1000))
    budget = MonitoringReadBudget()
    try:
        assert client._injected_transport is False  # Original flag, never changed by this fixture.
        method = getattr(client, "_capture_account_info_v1", None)
        assert callable(method), "M3-A endpoint-specific original-response capture is absent"
        with client._monitoring_operation(budget), budget.reuse_or_open_receipt() as group:
            capture = await method(budget=budget, group=group, owner_guard=lambda: None)
        assert capture.raw_body == RAW
        assert capture.raw_body_sha256 == hashlib.sha256(RAW).hexdigest()
        assert capture.envelope_time_ms is None and capture.accepted_attempt == 1
        assert capture.request_started_at <= capture.received_at
        assert capture.started_mono <= capture.received_mono
        assert requests[0].url.path == "/v5/account/info" and not requests[0].url.query
        assert len(requests) == budget.physical_attempts == 1
        assert KEY not in repr(capture) and SECRET not in repr(capture)
        assert RAW.decode() not in repr(capture)
    finally:
        await client.aclose()


async def test_capture_reuses_one_receipt_across_actual_retry_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    async def intercepted_send(_self: httpx.AsyncClient, request: httpx.Request,
                               **_kwargs: Any) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500 if len(requests) == 1 else 200,
                              content=b'{}' if len(requests) == 1 else RAW, request=request)

    async def offline_wait(_delay: float) -> None:
        return None

    monkeypatch.setattr(httpx.AsyncClient, "send", intercepted_send)
    client = BybitReadOnlyClient(api_key=KEY, api_secret=SECRET, environment="demo",
                                base_url="https://api-demo.bybit.com")
    client.anchor_server_time(int(datetime.now(UTC).timestamp() * 1000))
    client._sleeper = offline_wait  # Synthetic retry pacing; no actual provider qualification.
    budget = MonitoringReadBudget()
    try:
        with client._monitoring_operation(budget), budget.reuse_or_open_receipt() as group:
            captured = await client._capture_account_info_v1(
                budget=budget, group=group, owner_guard=lambda: None)
            assert budget.require_active_group(group).logical_gets == 1
        assert captured.raw_body == RAW and captured.accepted_attempt == 2
        assert captured.physical_end - captured.physical_start == budget.physical_attempts == 2
        assert len(requests) == 2 and captured.request is requests[1]
    finally:
        await client.aclose()


class Body(httpx.AsyncByteStream):
    def __init__(self, raw: bytes, *, partial: bool = False, close_error: bool = False) -> None:
        self.raw, self.partial, self.close_error = raw, partial, close_error
        self.closed = False

    async def __aiter__(self) -> Any:
        yield self.raw
        if self.partial:
            raise httpx.RemoteProtocolError("OFFLINE partial response")

    async def aclose(self) -> None:
        self.closed = True
        if self.close_error:
            raise RuntimeError(KEY + SECRET + self.raw.decode(errors="replace"))


def low_client() -> BybitReadOnlyClient:
    client = BybitReadOnlyClient(api_key=KEY, api_secret=SECRET, environment="demo",
                                base_url="https://api-demo.bybit.com")
    client.anchor_server_time(int(datetime.now(UTC).timestamp() * 1000))
    return client


async def low_capture(client: Any, budget: Any = None, guard: Any = lambda: None) -> Any:
    budget = MonitoringReadBudget() if budget is None else budget
    with client._monitoring_operation(budget), budget.reuse_or_open_receipt() as group:
        return await client._capture_account_info_v1(budget=budget, group=group, owner_guard=guard)


@pytest.mark.parametrize("kind", ["association", "missing-association", "foreign-origin", "redirect",
    "gzip", "duplicate-encoding", "negative-length", "oversize-length", "length-mismatch",
    "oversize-body", "duplicate-json", "bad-retcode", "partial", "close-error"])
async def test_original_response_and_body_fail_closed(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    from multi_exchange_trader.account.bybit_readonly import BybitReadError

    bodies: list[Body] = []

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        raw, headers = RAW, []
        if kind == "oversize-body": raw = RAW + b" " * 65536
        if kind == "duplicate-json": raw = b'{"retCode":0,"retCode":0,"result":{}}'
        if kind == "bad-retcode": raw = b'{"retCode":true,"result":{}}'
        if kind == "gzip": headers = [("content-encoding", "gzip")]
        if kind == "duplicate-encoding":
            headers = [("content-encoding", "identity"), ("content-encoding", "identity")]
        if kind.endswith("length") or kind == "length-mismatch":
            length = "-1" if kind == "negative-length" else "65537" if kind == "oversize-length" else str(len(raw) + 1)
            headers = [("content-length", length)]
        body = Body(raw, partial=kind == "partial", close_error=kind == "close-error")
        bodies.append(body)
        associated = request
        if kind == "association": associated = httpx.Request("GET", request.url, headers=request.headers)
        if kind == "foreign-origin": associated = httpx.Request("GET", "https://example.invalid/v5/account/info")
        return httpx.Response(302 if kind == "redirect" else 200, stream=body, headers=headers,
                              request=None if kind == "missing-association" else associated)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    client = low_client()
    try:
        with pytest.raises(BybitReadError) as error:
            await low_capture(client)
        assert client._account_info_pending is None and bodies[0].closed
        assert KEY not in str(error.value) and SECRET not in str(error.value)
        assert error.value.__context__ is error.value.__cause__ is None
    finally:
        await client.aclose()


@pytest.mark.parametrize("status", [403, 429, 451])
async def test_quota_or_ban_observation_precedes_bad_body(
    monkeypatch: pytest.MonkeyPatch, status: int,
) -> None:
    from multi_exchange_trader.account.bybit_readonly import BybitReadError

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(status, stream=Body(b"not JSON"), request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    client, budget = low_client(), MonitoringReadBudget()
    try:
        with pytest.raises(BybitReadError): await low_capture(client, budget)
        assert budget._terminal_failure == "MONITORING_QUOTA_OR_BAN"
        assert budget.physical_attempts == 1 and client._account_info_pending is None
    finally:
        await client.aclose()


@pytest.mark.parametrize("change", ["path", "query", "host", "fragment", "method", "signature"])
async def test_only_exact_signed_account_info_request_can_reach_send(
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from multi_exchange_trader.account.bybit_readonly import BybitReadError

    client = low_client()
    original = client._build_request
    sends: list[httpx.Request] = []

    def request(*args: Any, **kwargs: Any) -> httpx.Request:
        result = original(*args, **kwargs)
        if change == "path": result.url = result.url.copy_with(path="/v5/user/query-api")
        if change == "query": result.url = result.url.copy_with(query=b"uid=999")
        if change == "host": result.url = result.url.copy_with(host="api.bybit.com")
        if change == "fragment": result.url = result.url.copy_with(fragment="unexpected")
        if change == "method": result.method = "POST"
        if change == "signature": result.headers["X-BAPI-SIGN"] = "f" * 64
        return result

    async def send(_self: httpx.AsyncClient, req: httpx.Request, **_kwargs: Any) -> httpx.Response:
        sends.append(req)
        return httpx.Response(200, content=RAW, request=req)

    monkeypatch.setattr(client, "_build_request", request)
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        with pytest.raises(BybitReadError): await low_capture(client)
        assert not sends and client._account_info_pending is None
    finally:
        await client.aclose()


async def test_legacy_read_tuple_and_injected_transport_refusal_remain_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.account.bybit_readonly import BybitReadError

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(200, content=RAW, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    client = low_client()
    injected = BybitReadOnlyClient(api_key=KEY, api_secret=SECRET, environment="demo",
        base_url="https://api-demo.bybit.com", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=RAW, request=request)))
    try:
        result, stamp = await client.read("/v5/account/info", [], signed=True)
        assert result == {"marginMode": "ISOLATED_MARGIN"} and stamp is None
        with pytest.raises(BybitReadError): await low_capture(injected)
        method: Any = client._capture_account_info_v1
        with pytest.raises(TypeError): await method(budget=MonitoringReadBudget(), group=object())
    finally:
        await client.aclose()
        await injected.aclose()


async def test_original_response_headers_cannot_change_after_body_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.account.bybit_readonly import BybitReadError

    client = low_client()
    fired = False
    bodies: list[Body] = []

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        body = Body(RAW)
        bodies.append(body)
        return httpx.Response(200, stream=body, request=request)

    def guard() -> None:
        nonlocal fired
        route = client._account_info_pending
        assert route is not None
        if not fired and route.raw_body is not None:
            assert route.response is not None
            route.response.headers["content-encoding"] = "gzip"
            fired = True

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        with pytest.raises(BybitReadError): await low_capture(client, guard=guard)
        assert fired and bodies[0].closed and client._account_info_pending is None
    finally:
        await client.aclose()


@pytest.mark.parametrize("change", ["deadline", "receipt-context", "operation-context", "wire"])
async def test_original_budget_context_and_request_survive_owner_callback(
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from contextvars import ContextVar

    from multi_exchange_trader.account.bybit_readonly import BybitReadError

    client, budget = low_client(), MonitoringReadBudget()
    sends: list[httpx.Request] = []
    old_receipt, old_operation = budget._receipt, client._operation
    fired = False

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        sends.append(request)
        return httpx.Response(200, content=RAW, request=request)

    def guard() -> None:
        nonlocal fired
        route = client._account_info_pending
        assert route is not None
        if fired or change == "wire" and route.request is None: return
        fired = True
        if change == "deadline": budget.deadline += 60
        if change == "receipt-context":
            replacement: Any = ContextVar("REPLACED_RECEIPT", default=None)
            replacement.set(budget._receipt.get())
            budget._receipt = replacement
        if change == "operation-context":
            replacement = ContextVar("REPLACED_OPERATION", default=budget)
            client._operation = replacement
        if change == "wire":
            assert route.request is not None
            route.request.url = route.request.url.copy_with(query=b"unexpected=1")

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        with pytest.raises((BybitReadError, ValueError)): await low_capture(client, budget, guard)
        assert fired and not sends and client._account_info_pending is None
    finally:
        budget._receipt, client._operation = old_receipt, old_operation
        await client.aclose()


async def test_copied_client_cannot_issue_or_retire_original_client_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import copy

    from multi_exchange_trader.account.bybit_readonly import BybitReadError

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(200, content=RAW, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    original = low_client()
    try:
        copied = copy.copy(original)
        generation = original._account_info_generation
        with pytest.raises(BybitReadError): await low_capture(copied)
        assert original._account_info_generation is generation
        captured = await low_capture(original)
        assert captured.owner is original and captured.raw_body == RAW
    finally:
        await original.aclose()
