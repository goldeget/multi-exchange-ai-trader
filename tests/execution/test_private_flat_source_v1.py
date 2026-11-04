"""Selected flat data from genuine Session auth and original intercepted HTTPX."""
import asyncio
import copy
import json
from typing import Any

import httpx
import pytest
from test_bybit_entry_market_projections_v1 import FLAT
from test_private_account_sources_bundle_v1 import SCOPE, authenticate, capture_all, require_one
from test_private_held_sources_pages_v1 import paged_session

from multi_exchange_trader.execution.account_binding import AccountBindingError
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget


def flat_session(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[httpx.Request]]:
    owner, requests = paged_session(monkeypatch, "positions")
    original = httpx.AsyncClient.send

    async def send(client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        if request.url.path != "/v5/position/list" or "symbol" not in request.url.params:
            return await original(client, request, **kwargs)
        requests.append(request)
        return httpx.Response(200, json={"retCode": 0, "time": int(owner._clock().timestamp() * 1000),
            "result": {"category": "linear", "list": [{**FLAT, "symbol": request.url.params["symbol"],
                                                       "autoAddMargin": 1}]}}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return owner, requests


async def test_original_flat_source_preserves_zero_size_auto_one_and_exact_field_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, requests = flat_session(monkeypatch)
    try:
        binding = await authenticate(owner)
        method = getattr(owner, "_capture_linear_flat_leverage_v1", None)
        assert callable(method), "Selected-symbol original source API absent"
        source = await method(binding, symbol="BTCUSDT", scope=SCOPE,
            budget=MonitoringReadBudget(), owner_guard=lambda: None)
        owner._require_linear_flat_leverage_v1(source, symbol="BTCUSDT", binding=binding, scope=SCOPE)
        flat, size, auto = owner._project_linear_flat_leverage_v1(
            source, symbol="BTCUSDT", binding=binding, scope=SCOPE)
        assert flat.source is size.reference.source is auto.source is source
        assert flat.leverage.reference.source is source
        assert flat.leverage.reference.wire_spelling == "7.500"
        assert size.reference.wire_spelling == "0.000" and size.value == 0
        assert auto.wire_spelling == "1" and auto.json_pointer == "/result/list/0/autoAddMargin"
        assert source.query == (("category", "linear"), ("symbol", "BTCUSDT"))
        assert len(requests) == 3
    finally:
        await owner.aclose()


async def capture(owner: Any, binding: Any, symbol: str = "BTCUSDT") -> Any:
    return await owner._capture_linear_flat_leverage_v1(binding, symbol=symbol, scope=SCOPE,
        budget=MonitoringReadBudget(), owner_guard=lambda: None)


@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT"])
async def test_flat_republication_retires_only_flat_while_other_six_sources_survive(
    monkeypatch: pytest.MonkeyPatch, symbol: str,
) -> None:
    owner, requests = flat_session(monkeypatch)
    try:
        binding = await authenticate(owner)
        others = await capture_all(owner, binding)
        positions = await owner._capture_linear_positions_v1(binding, scope=SCOPE,
            budget=MonitoringReadBudget(), owner_guard=lambda: None)
        orders = await owner._capture_linear_orders_v1(binding, scope=SCOPE,
            budget=MonitoringReadBudget(), owner_guard=lambda: None)
        old = await capture(owner, binding)
        fresh = await capture(owner, binding, symbol)
        with pytest.raises(AccountBindingError):
            owner._require_linear_flat_leverage_v1(old, symbol="BTCUSDT", binding=binding, scope=SCOPE)
        owner._require_linear_flat_leverage_v1(fresh, symbol=symbol, binding=binding, scope=SCOPE)
        owner._require_linear_positions_v1(positions, binding=binding, scope=SCOPE)
        owner._require_linear_orders_v1(orders, binding=binding, scope=SCOPE)
        for kind, source in others.items():
            require_one(owner, binding, kind, source)
        assert len(requests) == 12
    finally:
        await owner.aclose()


async def test_flat_and_paged_position_sources_cannot_substitute_for_each_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, _requests = flat_session(monkeypatch)
    try:
        binding = await authenticate(owner)
        pages = await owner._capture_linear_positions_v1(binding, scope=SCOPE,
            budget=MonitoringReadBudget(), owner_guard=lambda: None)
        flat = await capture(owner, binding)
        with pytest.raises(AccountBindingError):
            owner._require_linear_flat_leverage_v1(pages[0], symbol="BTCUSDT", binding=binding, scope=SCOPE)
        with pytest.raises(AccountBindingError):
            owner._project_linear_flat_leverage_v1(pages[0], symbol="BTCUSDT", binding=binding, scope=SCOPE)
        with pytest.raises(AccountBindingError):
            owner._require_linear_positions_v1((flat,), binding=binding, scope=SCOPE)
        with pytest.raises(AccountBindingError):
            owner._require_linear_flat_leverage_v1(flat, symbol="ETHUSDT", binding=binding, scope=SCOPE)
        owner._require_linear_flat_leverage_v1(flat, symbol="BTCUSDT", binding=binding, scope=SCOPE)
        owner._require_linear_positions_v1(pages, binding=binding, scope=SCOPE)
    finally:
        await owner.aclose()


@pytest.mark.parametrize("change", ["reauth", "session-close", "client-close", "owner"])
async def test_flat_source_withdraws_before_auth_wait_or_after_close_and_owner_change(
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    owner, _requests = flat_session(monkeypatch)
    original = owner._client
    task: asyncio.Task[Any] | None = None
    try:
        binding = await authenticate(owner)
        source = await capture(owner, binding)
        if change == "reauth":
            await owner._refresh_lock.acquire()
            task = asyncio.create_task(authenticate(owner))
            await asyncio.sleep(0)
        elif change == "session-close":
            await owner.aclose()
        elif change == "client-close":
            await owner._client.aclose()
        else:
            owner._client = copy.copy(original)
        with pytest.raises(AccountBindingError):
            owner._require_linear_flat_leverage_v1(source, symbol="BTCUSDT", binding=binding, scope=SCOPE)
    finally:
        owner._client = original
        if owner._refresh_lock.locked():
            owner._refresh_lock.release()
        if task is not None:
            await task
        await owner.aclose()


@pytest.mark.parametrize("bad", ["empty", "missing-size", "held", "missing-leverage"])
async def test_projection_never_invents_flat_size_or_leverage_defaults(
    monkeypatch: pytest.MonkeyPatch, bad: str,
) -> None:
    owner, _requests = flat_session(monkeypatch)
    original = httpx.AsyncClient.send

    async def send(client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        response = await original(client, request, **kwargs)
        if request.url.path == "/v5/position/list" and "symbol" in request.url.params:
            body = json.loads(response.content)
            if bad == "empty":
                body["result"]["list"] = []
            elif bad == "missing-size":
                del body["result"]["list"][0]["size"]
            elif bad == "held":
                body["result"]["list"][0]["size"] = "1"
            else:
                del body["result"]["list"][0]["leverage"]
            return httpx.Response(200, json=body, request=request)
        return response

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        binding = await authenticate(owner)
        source = await capture(owner, binding)
        owner._require_linear_flat_leverage_v1(source, symbol="BTCUSDT", binding=binding, scope=SCOPE)
        with pytest.raises(AccountBindingError, match="ACCOUNT_DATA_PROJECTION_REQUIRED"):
            owner._project_linear_flat_leverage_v1(source, symbol="BTCUSDT", binding=binding, scope=SCOPE)
    finally:
        await owner.aclose()


async def test_inner_caught_overlap_withdraws_flat_and_publishes_no_partial_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, requests = flat_session(monkeypatch)
    original = httpx.AsyncClient.send
    caught = False
    binding: Any = None

    async def send(client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        nonlocal caught
        response = await original(client, request, **kwargs)
        if request.url.path == "/v5/position/list" and "symbol" in request.url.params:
            with pytest.raises(AccountBindingError):
                await owner._capture_linear_positions_v1(binding, scope=SCOPE,
                    budget=MonitoringReadBudget(), owner_guard=lambda: None)
            caught = True
        return response

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        binding = await authenticate(owner)
        with pytest.raises(AccountBindingError):
            await capture(owner, binding)
        assert caught and len(requests) == 3
        assert owner._linear_flat_current is owner._linear_info_pending is None
    finally:
        await owner.aclose()
