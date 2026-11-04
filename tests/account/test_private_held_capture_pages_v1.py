"""Offline original HTTPX page capture; no venue or financial authority."""
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest

from multi_exchange_trader.account.bybit_readonly import BybitReadError, BybitReadOnlyClient
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget


def original_client() -> BybitReadOnlyClient:
    client = BybitReadOnlyClient(api_key="HELD-OFFLINE-KEY", api_secret="HELD-OFFLINE-SECRET",
        base_url="https://api-demo.bybit.com", environment="demo")
    client.anchor_server_time(int(datetime.now(UTC).timestamp() * 1000))
    return client


@pytest.mark.parametrize("kind", ["positions", "orders"])
async def test_original_transport_retains_two_pages_one_generation(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    client = original_client()
    sent: list[httpx.Request] = []
    stamp = int(datetime.now(UTC).timestamp() * 1000)

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        sent.append(request)
        assert request.url.path == ("/v5/position/list" if kind == "positions" else "/v5/order/realtime")
        expected = [("category", "linear"), ("settleCoin", "USDT")]
        if kind == "orders":
            expected.append(("openOnly", "0"))
        if len(sent) == 2:
            expected.append(("cursor", "next/page"))
        assert request.url.query == urlencode(sorted(expected)).encode()
        raw = json.dumps({"retCode": 0, "result": {"category": "linear", "list": [],
            "nextPageCursor": "next/page" if len(sent) == 1 else ""}, "time": stamp}).encode()
        return httpx.Response(200, content=raw, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        method = getattr(client, f"_capture_account_{kind}_v1", None)
        assert callable(method), "Whole-page capture API absent"
        budget = MonitoringReadBudget()
        pages = await method(budget=budget, owner_guard=lambda: None)
        assert len(pages) == len(sent) == budget.physical_attempts == 2
        assert pages[0].generation is pages[1].generation
        assert pages[0].request is sent[0] and pages[1].request is sent[1]
        assert pages[0].raw_body != pages[1].raw_body
        assert client._account_info_pending is None
    finally:
        await client.aclose()


@pytest.mark.parametrize("kind", ["positions", "orders"])
@pytest.mark.parametrize("failure", ["cursor-null", "cursor-missing", "category", "list", "row", "count", "time", "header"])
async def test_malformed_later_page_never_returns_a_partial_collection(
    monkeypatch: pytest.MonkeyPatch, kind: str, failure: str,
) -> None:
    client = original_client()
    calls = 0
    stamp = int(datetime.now(UTC).timestamp() * 1000)

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        result: dict[str, Any] = {"category": "linear", "list": [], "nextPageCursor": "more" if calls == 1 else ""}
        envelope: dict[str, Any] = {"retCode": 0, "result": result, "time": stamp}
        headers = {}
        if calls == 2:
            if failure == "cursor-null":
                result["nextPageCursor"] = None
            elif failure == "cursor-missing":
                del result["nextPageCursor"]
            elif failure == "category":
                result["category"] = "inverse"
            elif failure == "list":
                result["list"] = {}
            elif failure == "row":
                result["list"] = [False]
            elif failure == "count":
                result["list"] = [{}] * 21
            elif failure == "time":
                del envelope["time"]
            else:
                headers = {"X-Bapi-Limit-Status": "0"}
        return httpx.Response(200, json=envelope, headers=headers, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        with pytest.raises(BybitReadError):
            await getattr(client, f"_capture_account_{kind}_v1")(budget=MonitoringReadBudget(), owner_guard=lambda: None)
        assert calls == 2 and client._account_info_pending is None
        assert client._private_account_collection_pending_v1 is None
    finally:
        await client.aclose()


@pytest.mark.parametrize("kind", ["positions", "orders"])
@pytest.mark.parametrize("mode,expected", [("cycle", 2), ("overflow", 64), ("complete", 64), ("budget", 1)])
async def test_cursor_cycle_page_ceiling_and_original_physical_budget(
    monkeypatch: pytest.MonkeyPatch, kind: str, mode: str, expected: int,
) -> None:
    client = original_client()
    calls = 0
    budget = MonitoringReadBudget()
    if mode == "budget":
        budget.physical_attempts = 199
    stamp = int(datetime.now(UTC).timestamp() * 1000)

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        cursor = "same" if mode == "cycle" else "" if mode == "complete" and calls == 64 else f"page-{calls}"
        return httpx.Response(200, json={"retCode": 0, "time": stamp, "result": {
            "category": "linear", "list": [], "nextPageCursor": cursor}}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        method = getattr(client, f"_capture_account_{kind}_v1")
        if mode == "complete":
            pages = await method(budget=budget, owner_guard=lambda: None)
            assert len(pages) == 64 and all(page.generation is pages[0].generation for page in pages)
        else:
            with pytest.raises(BybitReadError):
                await method(budget=budget, owner_guard=lambda: None)
        assert calls == expected and client._account_info_pending is None
    finally:
        await client.aclose()


@pytest.mark.parametrize("kind", ["positions", "orders"])
@pytest.mark.parametrize("change", ["query", "origin"])
async def test_later_page_cannot_change_fixed_wire_before_send(
    monkeypatch: pytest.MonkeyPatch, kind: str, change: str,
) -> None:
    client = original_client()
    original = client._build_request
    sent = 0
    stamp = int(datetime.now(UTC).timestamp() * 1000)

    def build(path: str, params: list[tuple[str, str]], *, signed: bool) -> httpx.Request:
        request = original(path, params, signed=signed)
        if any(key == "cursor" for key, _value in params):
            request.url = request.url.copy_with(**({"query": b"category=linear&settleCoin=USDC"}
                if change == "query" else {"host": "api.bybit.com"}))
        return request

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(200, json={"retCode": 0, "time": stamp, "result": {
            "category": "linear", "list": [], "nextPageCursor": "more"}}, request=request)

    monkeypatch.setattr(client, "_build_request", build)
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        with pytest.raises(BybitReadError):
            await getattr(client, f"_capture_account_{kind}_v1")(budget=MonitoringReadBudget(), owner_guard=lambda: None)
        assert sent == 1
    finally:
        await client.aclose()


@pytest.mark.parametrize("kind", ["positions", "orders"])
async def test_later_page_retry_keeps_accepted_attempt_and_original_receipt_limit(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    client = original_client()
    calls = 0
    stamp = int(datetime.now(UTC).timestamp() * 1000)

    async def send(_self: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 2:
            return httpx.Response(500, content=b"{}", request=request)
        return httpx.Response(200, json={"retCode": 0, "time": stamp, "result": {
            "category": "linear", "list": [], "nextPageCursor": "more" if calls == 1 else ""}}, request=request)

    async def wait(_delay: float) -> None:
        return None

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    client._sleeper = wait
    budget = MonitoringReadBudget()
    try:
        pages = await getattr(client, f"_capture_account_{kind}_v1")(budget=budget, owner_guard=lambda: None)
        assert [page.accepted_attempt for page in pages] == [1, 2]
        assert calls == budget.physical_attempts == 3
        assert [page.logical_ordinal for page in pages] == [1, 1]
    finally:
        await client.aclose()
