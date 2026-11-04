"""Original bounded signed HTTPX capture intercepted offline."""
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from multi_exchange_trader.account.bybit_readonly import (
    BybitReadError,
    BybitReadOnlyClient,
    _private_account_endpoint_v1,
    sign_v5,
)
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget


def client() -> BybitReadOnlyClient:
    result = BybitReadOnlyClient(api_key="FLAT-OFFLINE-KEY", api_secret="FLAT-OFFLINE-SECRET",
        base_url="https://api-demo.bybit.com", environment="demo")
    result.anchor_server_time(int(datetime.now(UTC).timestamp() * 1000))
    return result


async def test_original_flat_capture_preserves_fixed_symbol_wire_and_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = client()
    raw = json.dumps({"retCode": 0, "result": {"category": "linear", "list": []},
        "time": int(datetime.now(UTC).timestamp() * 1000)}).encode()
    requests: list[httpx.Request] = []

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/v5/position/list"
        assert request.url.query == b"category=linear&symbol=BTCUSDT"
        assert request.headers["X-BAPI-SIGN"] == sign_v5("FLAT-OFFLINE-SECRET", "FLAT-OFFLINE-KEY",
            request.headers["X-BAPI-TIMESTAMP"], request.headers["X-BAPI-RECV-WINDOW"], request.url.query.decode())
        return httpx.Response(200, content=raw, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        method = getattr(owner, "_capture_account_flat_leverage_v1", None)
        assert callable(method), "Selected-symbol original capture API absent"
        budget = MonitoringReadBudget()
        with owner._monitoring_operation(budget), budget.reuse_or_open_receipt() as group:
            capture = await method(symbol="BTCUSDT", budget=budget, group=group, owner_guard=lambda: None)
        assert capture.raw_body == raw and capture.request is requests[0]
        assert capture.query == (("category", "linear"), ("symbol", "BTCUSDT"))
        assert len(requests) == budget.physical_attempts == capture.accepted_attempt == 1
    finally:
        await owner.aclose()


@pytest.mark.parametrize("symbol", ["", "btcUSDT", "BTC-USDT", "BTC USDT", "B", "A" * 49])
async def test_flat_symbol_is_closed_before_any_http_send(
    monkeypatch: pytest.MonkeyPatch, symbol: str,
) -> None:
    owner = client()
    sent: list[httpx.Request] = []

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        sent.append(request)
        raise AssertionError("Invalid closed symbol must not be sent")

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    budget = MonitoringReadBudget()
    try:
        with (
            owner._monitoring_operation(budget), budget.reuse_or_open_receipt() as group,
            pytest.raises(BybitReadError),
        ):
            await owner._capture_account_flat_leverage_v1(symbol=symbol,
                budget=budget, group=group, owner_guard=lambda: None)
        assert sent == [] and budget.physical_attempts == 0
    finally:
        await owner.aclose()


@pytest.mark.parametrize("change", ["query", "origin", "time"])
async def test_flat_capture_reuses_original_wire_origin_and_envelope_guards(
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    owner = client()
    sent: list[httpx.Request] = []
    build = owner._build_request

    def request(path: str, params: list[tuple[str, str]], *, signed: bool) -> httpx.Request:
        value = build(path, params, signed=signed)
        if change != "time":
            value.url = value.url.copy_with(**({"query": b"category=linear&settleCoin=USDT"}
                if change == "query" else {"host": "api.bybit.com"}))
        return value

    async def send(_self: httpx.AsyncClient, value: httpx.Request, **_kwargs: Any) -> httpx.Response:
        sent.append(value)
        return httpx.Response(200, json={"retCode": 0, "result": {}}, request=value)

    monkeypatch.setattr(owner, "_build_request", request)
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    budget = MonitoringReadBudget()
    try:
        with (
            owner._monitoring_operation(budget), budget.reuse_or_open_receipt() as group,
            pytest.raises(BybitReadError),
        ):
            await owner._capture_account_flat_leverage_v1(symbol="BTCUSDT",
                budget=budget, group=group, owner_guard=lambda: None)
        assert len(sent) == (1 if change == "time" else 0)
        assert owner._account_info_pending is None
    finally:
        await owner.aclose()


def test_same_position_path_has_two_closed_incompatible_query_families() -> None:
    assert _private_account_endpoint_v1("/v5/position/list", (("category", "linear"), ("symbol", "BTCUSDT"))) == "FLAT"
    assert _private_account_endpoint_v1("/v5/position/list", (("category", "linear"), ("settleCoin", "USDT"))) == "POSITIONS"
    with pytest.raises(BybitReadError):
        _private_account_endpoint_v1("/v5/position/list", (
            ("category", "linear"), ("symbol", "BTCUSDT"), ("settleCoin", "USDT")))
