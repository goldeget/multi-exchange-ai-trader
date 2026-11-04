"""Genuine Session auth and fixed HTTPX page fixtures, without actual account access."""
import asyncio
import copy
import json
from dataclasses import replace
from typing import Any

import httpx
import pytest
from test_bybit_linear_model_inputs_v1 import a1_pin
from test_bybit_position_order_projections_v1 import ORDER, POSITION
from test_private_account_sources_bundle_v1 import (
    SCOPE,
    authenticate,
    capture_all,
    intercepted_session,
    require_one,
)

from multi_exchange_trader.execution.account_binding import AccountBindingError
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
from multi_exchange_trader.execution.protection import ProtectionScope


def paged_session(monkeypatch: pytest.MonkeyPatch, kind: str, *, empty: bool = False) -> tuple[Any, list[httpx.Request]]:
    owner, requests = intercepted_session(monkeypatch)
    original = httpx.AsyncClient.send
    counts: dict[str, int] = {}

    async def send(client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        if request.url.path not in ("/v5/position/list", "/v5/order/realtime"):
            return await original(client, request, **kwargs)
        requests.append(request)
        count = counts.get(request.url.path, 0) + 1
        counts[request.url.path] = count
        ordinal = (count - 1) % 2 + 1
        row = ({**POSITION, "positionIdx": ordinal - 1} if request.url.path == "/v5/position/list" else
               {**ORDER, "orderId": f"order-{ordinal}"})
        return httpx.Response(200, content=json.dumps({"retCode": 0, "time": int(owner._clock().timestamp() * 1000),
            "result": {"category": "linear", "list": [] if empty else [row],
                       "nextPageCursor": "" if empty or ordinal == 2 else "next-page"}}).encode(), request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return owner, requests


@pytest.mark.parametrize("kind", ["positions", "orders"])
async def test_original_session_requires_and_projects_whole_current_collection(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    owner, requests = paged_session(monkeypatch, kind)
    try:
        binding = await authenticate(owner)
        method = getattr(owner, f"_capture_linear_{kind}_v1", None)
        assert callable(method), "Whole-page Session API absent"
        sources = await method(binding, scope=SCOPE, budget=MonitoringReadBudget(), owner_guard=lambda: None)
        assert len(sources) == 2 and len(requests) == 4
        assert [s.page_ordinal for s in sources] == [1, 2]
        assert sources[0].source_generation == sources[1].source_generation
        getattr(owner, f"_require_linear_{kind}_v1")((sources[0], sources[1]), binding=binding, scope=SCOPE)
        rows = getattr(owner, f"_project_linear_{kind}_v1")(
            sources, instruments=(a1_pin(),), binding=binding, scope=SCOPE)
        assert len(rows) == 2 and rows[0].source is sources[0] and rows[1].source is sources[1]
    finally:
        await owner.aclose()


async def collect(owner: Any, binding: Any, kind: str, **kwargs: Any) -> Any:
    return await getattr(owner, f"_capture_linear_{kind}_v1")(binding, scope=SCOPE,
        budget=MonitoringReadBudget(), owner_guard=lambda: None, **kwargs)


def require(owner: Any, binding: Any, kind: str, sources: Any, *, scope: ProtectionScope = SCOPE) -> None:
    getattr(owner, f"_require_linear_{kind}_v1")(sources, binding=binding, scope=scope)


@pytest.mark.parametrize("kind", ["positions", "orders"])
async def test_empty_completed_collection_is_one_source_without_invented_rows(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    owner, requests = paged_session(monkeypatch, kind, empty=True)
    try:
        binding = await authenticate(owner)
        sources = await collect(owner, binding, kind)
        require(owner, binding, kind, sources)
        assert len(sources) == 1 and len(requests) == 3
        assert getattr(owner, f"_project_linear_{kind}_v1")(
            sources, instruments=(), binding=binding, scope=SCOPE) == ()
    finally:
        await owner.aclose()


@pytest.mark.parametrize("kind", ["positions", "orders"])
@pytest.mark.parametrize("wrong", ["missing", "reordered", "duplicate", "copy", "scope"])
async def test_require_accepts_only_all_original_page_objects_in_order_and_scope(
    monkeypatch: pytest.MonkeyPatch, kind: str, wrong: str,
) -> None:
    owner, _requests = paged_session(monkeypatch, kind)
    try:
        binding = await authenticate(owner)
        sources = await collect(owner, binding, kind)
        invalid = sources[:1] if wrong == "missing" else sources[::-1] if wrong == "reordered" else (
            (sources[0], sources[0]) if wrong == "duplicate" else (replace(sources[0]), sources[1]) if wrong == "copy" else sources)
        with pytest.raises(AccountBindingError):
            require(owner, binding, kind, invalid,
                scope=ProtectionScope("67890", "DEMO") if wrong == "scope" else SCOPE)
    finally:
        await owner.aclose()


@pytest.mark.parametrize("kind", ["positions", "orders"])
async def test_refresh_only_its_family_while_all_six_current_sources_coexist(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    owner, requests = paged_session(monkeypatch, kind)
    try:
        binding = await authenticate(owner)
        old_four = await capture_all(owner, binding)
        positions, orders = await collect(owner, binding, "positions"), await collect(owner, binding, "orders")
        old = positions if kind == "positions" else orders
        fresh = await collect(owner, binding, kind)
        with pytest.raises(AccountBindingError):
            require(owner, binding, kind, old)
        require(owner, binding, kind, fresh)
        other = "orders" if kind == "positions" else "positions"
        require(owner, binding, other, orders if other == "orders" else positions)
        for endpoint, source in old_four.items():
            require_one(owner, binding, endpoint, source)
        assert owner._project_linear_account_mode_v1(old_four["info"], binding=binding, scope=SCOPE).updated_time_ms == 1
        assert owner._project_linear_wallet_v1(old_four["wallet"], binding=binding, scope=SCOPE).source is old_four["wallet"]
        assert len(requests) == 12
    finally:
        await owner.aclose()


@pytest.mark.parametrize("kind", ["positions", "orders"])
@pytest.mark.parametrize("change", ["reauth", "session-close", "client-close", "owner"])
async def test_collection_withdraws_for_authentication_close_or_shared_owner_change(
    monkeypatch: pytest.MonkeyPatch, kind: str, change: str,
) -> None:
    owner, _requests = paged_session(monkeypatch, kind)
    task: asyncio.Task[Any] | None = None
    original = owner._client
    try:
        binding = await authenticate(owner)
        sources = await collect(owner, binding, kind)
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
            require(owner, binding, kind, sources)
    finally:
        owner._client = original
        if owner._refresh_lock.locked():
            owner._refresh_lock.release()
        if task is not None:
            await task
        await owner.aclose()


@pytest.mark.parametrize("kind", ["positions", "orders"])
async def test_aggregate_refuses_duplicate_identity_across_pages(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    owner, _requests = paged_session(monkeypatch, kind)
    original = httpx.AsyncClient.send

    async def send(client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        response = await original(client, request, **kwargs)
        if request.url.path in ("/v5/position/list", "/v5/order/realtime"):
            body = json.loads(response.content)
            body["result"]["list"] = [POSITION if kind == "positions" else ORDER]
            return httpx.Response(200, json=body, request=request)
        return response

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        binding = await authenticate(owner)
        sources = await collect(owner, binding, kind)
        require(owner, binding, kind, sources)
        with pytest.raises(AccountBindingError, match="ACCOUNT_HELD_PROJECTION_REQUIRED"):
            getattr(owner, f"_project_linear_{kind}_v1")(
                sources, instruments=(a1_pin(),), binding=binding, scope=SCOPE)
    finally:
        await owner.aclose()


@pytest.mark.parametrize("kind", ["positions", "orders"])
async def test_whole_collection_excludes_cross_family_overlap_even_when_inner_error_caught(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    owner, requests = paged_session(monkeypatch, kind)
    original = httpx.AsyncClient.send
    caught = False
    binding: Any = None

    async def send(client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        nonlocal caught
        response = await original(client, request, **kwargs)
        if request.url.params.get("cursor") and not caught:
            with pytest.raises(AccountBindingError):
                await owner._capture_linear_wallet_v1(binding, scope=SCOPE,
                    budget=MonitoringReadBudget(), owner_guard=lambda: None)
            caught = True
        return response

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        binding = await authenticate(owner)
        with pytest.raises(AccountBindingError):
            await collect(owner, binding, kind)
        assert caught and len(requests) == 4
        assert owner._linear_info_pending is None
        assert owner._client._account_info_pending is None
    finally:
        await owner.aclose()


@pytest.mark.parametrize("kind", ["positions", "orders"])
async def test_malformed_later_page_retires_old_collection_without_partial_publication(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    owner, _requests = paged_session(monkeypatch, kind)
    original = httpx.AsyncClient.send
    broken = False

    async def send(client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        response = await original(client, request, **kwargs)
        if broken and request.url.params.get("cursor"):
            body = json.loads(response.content)
            body["result"]["list"] = None
            return httpx.Response(200, json=body, request=request)
        return response

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        binding = await authenticate(owner)
        old = await collect(owner, binding, kind)
        broken = True
        with pytest.raises(AccountBindingError):
            await collect(owner, binding, kind)
        with pytest.raises(AccountBindingError):
            require(owner, binding, kind, old)
        assert owner._linear_private_state_v1(kind.upper())[1] is None
        assert owner._linear_info_pending is None
    finally:
        await owner.aclose()
