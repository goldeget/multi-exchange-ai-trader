"""Original bounded HTTPX capture with offline response bytes, never live authority."""
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from multi_exchange_trader.account.bybit_readonly import (
    BybitReadError,
    BybitReadOnlyClient,
    ReadOnlyViolation,
    sign_v5,
)
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget

KEY, SECRET = "BUNDLE-OFFLINE-KEY", "BUNDLE-OFFLINE-SECRET"
ENDPOINTS = [
    ("wallet", "/v5/account/wallet-balance", b"accountType=UNIFIED"),
    ("fee_rate", "/v5/account/fee-rate", b"category=linear&symbol=BTCUSDT"),
    ("settings", "/v5/account/user-setting-config", b""),
]


@pytest.mark.parametrize("kind,path,query", ENDPOINTS)
async def test_original_capture_keeps_fixed_endpoint_query_and_accepted_raw(
    monkeypatch: pytest.MonkeyPatch, kind: str, path: str, query: bytes,
) -> None:
    stamp = int(datetime.now(UTC).timestamp() * 1000)
    raw = json.dumps({"retCode": 0, "result": {}, "time": stamp}).encode()
    requests: list[httpx.Request] = []

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET" and request.url.path == path and request.url.query == query
        assert request.headers["X-BAPI-SIGN"] == sign_v5(SECRET, KEY,
            request.headers["X-BAPI-TIMESTAMP"], request.headers["X-BAPI-RECV-WINDOW"], query.decode())
        return httpx.Response(200, content=raw, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    client = BybitReadOnlyClient(api_key=KEY, api_secret=SECRET, environment="demo",
                                base_url="https://api-demo.bybit.com")
    client.anchor_server_time(stamp)
    budget = MonitoringReadBudget()
    try:
        method = getattr(client, "_capture_account_" + kind + "_v1", None)
        assert callable(method), "Fixed private account capture API is absent"
        with client._monitoring_operation(budget), budget.reuse_or_open_receipt() as group:
            captured = await method(budget=budget, group=group, owner_guard=lambda: None,
                **({"symbol": "BTCUSDT"} if kind == "fee_rate" else {}))
        assert captured.raw_body == raw and captured.raw_body_sha256 == hashlib.sha256(raw).hexdigest()
        assert captured.request is requests[0] and captured.accepted_attempt == 1
        assert captured.path == path and len(requests) == budget.physical_attempts == 1
        assert KEY not in repr(captured) and SECRET not in repr(captured)
    finally:
        await client.aclose()


def original_client() -> BybitReadOnlyClient:
    client = BybitReadOnlyClient(api_key=KEY, api_secret=SECRET, environment="demo",
                                base_url="https://api-demo.bybit.com")
    client.anchor_server_time(int(datetime.now(UTC).timestamp() * 1000))
    return client


async def capture(client: BybitReadOnlyClient, kind: str, budget: MonitoringReadBudget) -> Any:
    with client._monitoring_operation(budget), budget.reuse_or_open_receipt() as group:
        return await getattr(client, "_capture_account_" + kind + "_v1")(
            budget=budget, group=group, owner_guard=lambda: None,
            **({"symbol": "BTCUSDT"} if kind == "fee_rate" else {}))


async def test_settings_capture_does_not_open_the_legacy_public_read_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = original_client()
    sent: list[httpx.Request] = []
    raw = json.dumps({"retCode": 0, "result": {}, "time": int(datetime.now(UTC).timestamp() * 1000)}).encode()

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, content=raw, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        with pytest.raises(ReadOnlyViolation):
            await client.read("/v5/account/user-setting-config", [], signed=True)
        assert sent == []
        captured = await capture(client, "settings", MonitoringReadBudget())
        assert captured.raw_body == raw and len(sent) == 1
    finally:
        await client.aclose()


@pytest.mark.parametrize("kind", ["wallet", "fee_rate", "settings"])
@pytest.mark.parametrize("change", ["query", "origin"])
async def test_fixed_wire_refuses_wrong_query_or_origin_before_http_send(
    monkeypatch: pytest.MonkeyPatch, kind: str, change: str,
) -> None:
    client = original_client()
    sent: list[httpx.Request] = []
    original = client._build_request

    def build(path: str, params: list[tuple[str, str]], *, signed: bool) -> httpx.Request:
        request = original(path, params, signed=signed)
        request.url = request.url.copy_with(**({"query": b"unknown=value"} if change == "query"
                                               else {"host": "api.bybit.com"}))
        return request

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        sent.append(request)
        raise AssertionError("Rejected wire must not be sent")

    monkeypatch.setattr(client, "_build_request", build)
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        with pytest.raises(BybitReadError):
            await capture(client, kind, MonitoringReadBudget())
        assert sent == [] and client._account_info_pending is None
    finally:
        await client.aclose()


@pytest.mark.parametrize("kind", ["wallet", "fee_rate", "settings"])
@pytest.mark.parametrize("failure", ["boolean-retcode", "duplicate", "missing-time"])
async def test_new_capture_reuses_original_strict_wire_acceptance(
    monkeypatch: pytest.MonkeyPatch, kind: str, failure: str,
) -> None:
    client = original_client()
    stamp = int(datetime.now(UTC).timestamp() * 1000)
    raw = (b'{"retCode":true,"result":{}}' if failure == "boolean-retcode" else
           b'{"retCode":0,"result":{},"extra":1,"extra":2}' if failure == "duplicate" else
           b'{"retCode":0,"result":{}}')
    if failure != "missing-time":
        raw = raw[:-1] + b',"time":' + str(stamp).encode() + b'}'

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(200, content=raw, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        with pytest.raises(BybitReadError):
            await capture(client, kind, MonitoringReadBudget())
        assert client._account_info_pending is None
    finally:
        await client.aclose()


@pytest.mark.parametrize("kind", ["wallet", "fee_rate", "settings"])
async def test_new_endpoint_retry_keeps_one_logical_receipt_and_actual_attempt(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    client = original_client()
    requests: list[httpx.Request] = []
    raw = json.dumps({"retCode": 0, "result": {}, "time": int(datetime.now(UTC).timestamp() * 1000)}).encode()

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500 if len(requests) == 1 else 200,
                              content=b'{}' if len(requests) == 1 else raw, request=request)

    async def wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    client._sleeper = wait
    budget = MonitoringReadBudget()
    try:
        captured = await capture(client, kind, budget)
        assert captured.request is requests[1] and captured.accepted_attempt == 2
        assert captured.logical_ordinal == 1 and captured.raw_body == raw
        assert len(requests) == budget.physical_attempts == 2
    finally:
        await client.aclose()
