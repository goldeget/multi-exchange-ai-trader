"""Product selection reaches the original authenticated account route, never an order sender."""

import json
from typing import Any

import httpx
import pytest

from . import test_demo_view_api_v1 as api_fixtures

app = api_fixtures.app
setup = api_fixtures.setup


async def test_spot_selection_reads_spot_orders_without_derivative_positions(
    app: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, wire, _service = app
    original = wire.send
    queries: list[tuple[str, dict[str, str]]] = []

    async def spot_response(client: httpx.AsyncClient, request: httpx.Request,
                            **kwargs: Any) -> httpx.Response:
        params = dict(request.url.params)
        queries.append((request.url.path, params))
        if request.url.path == "/v5/order/realtime" and params.get("category") == "spot":
            return httpx.Response(200, request=request, content=json.dumps({
                "retCode": 0, "time": int(wire.now.timestamp() * 1000), "result": {
                    "category": "spot", "nextPageCursor": "", "list": [{
                        "orderId": "external-spot-order", "orderLinkId": "", "symbol": "ETHUSDC",
                        "side": "Buy", "orderType": "Limit", "qty": "0.25", "price": "2000.5",
                        "orderStatus": "New", "isLeverage": "0",
                    }],
                },
            }).encode())
        response = await original(client, request, **kwargs)
        assert isinstance(response, httpx.Response)
        return response

    monkeypatch.setattr(wire, "send", spot_response)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                base_url="https://dashboard.example") as client:
        response = await client.get("/api/v1/account/bybit/demo/view?product=SPOT")
    assert response.status_code == 200
    body = response.json()
    assert body["상태코드"] == "ok"
    assert body["미체결주문"][0]["종목"] == "ETHUSDC"
    assert body["거래상품"] == "SPOT" and body["포지션"] == [] and body["최대레버리지"] is None
    assert not any(path == "/v5/position/list" for path, _params in queries)
    assert not any(path == "/v5/market/instruments-info" for path, _params in queries)
    assert any(path == "/v5/order/realtime" and params.get("category") == "spot"
               and "settleCoin" not in params for path, params in queries)
    assert body["주문보냄"] is False and body["주문판단에사용"] is False
    assert all(owner.is_closed for owner in wire.clients)


@pytest.mark.parametrize("product", ["spot", "INVERSE", "SPOT "])
async def test_invalid_product_stops_before_account_authentication(app: Any, product: str) -> None:
    application, wire, _service = app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                base_url="https://dashboard.example") as client:
        response = await client.get("/api/v1/account/bybit/demo/view", params={"product": product})
    assert response.status_code == 422 and not wire.paths


async def test_spot_category_mismatch_on_a_later_page_discards_all_values(
    app: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, wire, _service = app
    original = wire.send
    pages: list[str] = []

    async def wrong_page(client: httpx.AsyncClient, request: httpx.Request,
                         **kwargs: Any) -> httpx.Response:
        if request.url.path != "/v5/order/realtime" or request.url.params.get("category") != "spot":
            response = await original(client, request, **kwargs)
            assert isinstance(response, httpx.Response)
            return response
        cursor = request.url.params.get("cursor", "")
        pages.append(cursor)
        result: dict[str, object] = {"category": "linear" if cursor else "spot", "list": [],
                  "nextPageCursor": "next" if not cursor else ""}
        return httpx.Response(200, request=request, json={
            "retCode": 0, "time": int(wire.now.timestamp() * 1000), "result": result})

    monkeypatch.setattr(wire, "send", wrong_page)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                base_url="https://dashboard.example") as client:
        response = await client.get("/api/v1/account/bybit/demo/view?product=SPOT")
    body = response.json()
    assert pages == ["", "next"]
    assert body["거래상품"] == "SPOT" and body["상태코드"] == "error"
    assert body["잔고"] is None and body["포지션"] is None and body["미체결주문"] is None
    assert body["오류코드"] == "invalid_json" and all(owner.is_closed for owner in wire.clients)


async def test_spot_uid_mismatch_preserves_selected_product_without_publishing_balances(app: Any) -> None:
    application, wire, _service = app
    wire.uid = 67890
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                base_url="https://dashboard.example") as client:
        response = await client.get("/api/v1/account/bybit/demo/view?product=SPOT")
    body = response.json()
    assert body["거래상품"] == "SPOT" and body["오류코드"] == "DEMO_AUTHENTICATION_REQUIRED"
    assert body["잔고"] is None and wire.paths == ["/v5/market/time", "/v5/user/query-api"]
    assert all(owner.is_closed for owner in wire.clients)


async def test_mainnet_saved_read_cannot_mix_spot_and_linear_results(app: Any) -> None:
    from datetime import UTC, datetime

    from multi_exchange_trader.account.bybit_readonly import BybitReadOnlyClient

    application, _wire, _service = app
    reads: list[tuple[str, str | None]] = []

    def source(request: httpx.Request) -> httpx.Response:
        path, category = request.url.path, request.url.params.get("category")
        reads.append((path, category))
        result: dict[str, object]
        if path == "/v5/market/time":
            result = {}
        elif path == "/v5/market/instruments-info":
            result = {"category": "linear", "list": [{"symbol": "BTCUSDT",
                "leverageFilter": {"maxLeverage": "10", "minLeverage": "1", "leverageStep": "1"}}]}
        elif path == "/v5/account/wallet-balance":
            result = {"list": [{"accountType": "UNIFIED", "totalEquity": "10", "totalWalletBalance": "10",
                "totalAvailableBalance": "8", "coin": [{"coin": "USDT", "walletBalance": "10", "equity": "10"}]}]}
        elif path == "/v5/account/info":
            result = {"unifiedMarginStatus": 3, "marginMode": "REGULAR_MARGIN"}
        elif path == "/v5/position/list":
            result = {"category": "linear", "list": [], "nextPageCursor": ""}
        else:
            assert path == "/v5/order/realtime"
            result = {"category": category, "nextPageCursor": "", "list": [{
                "symbol": "ETHUSDC" if category == "spot" else "BTCUSDT", "side": "Buy",
                "orderType": "Limit", "qty": "1", "price": "100", "orderStatus": "New", "reduceOnly": False}]}
        return httpx.Response(200, request=request, json={
            "retCode": 0, "time": int(datetime.now(UTC).timestamp() * 1000), "result": result})

    original = BybitReadOnlyClient(api_key="mainnet-fixture-key", api_secret="mainnet-fixture-secret",
        base_url="https://api.bybit.com", environment="mainnet", transport=httpx.MockTransport(source))
    application.state.bybit_account = original
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                    base_url="https://dashboard.example") as client:
            linear = (await client.get("/api/v1/account/bybit/view")).json()
            spot = (await client.get("/api/v1/account/bybit/view?product=SPOT")).json()
            before = len(reads)
            saved = (await client.get("/api/v1/account/bybit/view")).json()
        assert linear["미체결주문"][0]["종목"] == saved["미체결주문"][0]["종목"] == "BTCUSDT"
        assert spot["미체결주문"][0]["종목"] == "ETHUSDC"
        assert saved["거래상품"] == "LINEAR" and saved["캐시사용"] is True and len(reads) == before
        assert spot["거래상품"] == "SPOT" and spot["포지션"] == [] and spot["최대레버리지"] is None
    finally:
        await original.aclose()


@pytest.mark.parametrize("symbol,market_unit,expected", [
    ("BTCUSDT", "quoteCoin", "quoteCoin"),
    ("BTCUSDT", "baseCoin", "baseCoin"),
    ("BTCUSDT", None, "unknown"),
    ("BTCUSDT", "invalid", "unknown"),
    ("ETHUSDC", "quoteCoin", "quoteCoin"),
    ("ETHUSDC", "baseCoin", "baseCoin"),
])
async def test_spot_quantity_keeps_original_market_unit_without_symbol_inference(
    app: Any, monkeypatch: pytest.MonkeyPatch, symbol: str,
    market_unit: str | None, expected: str,
) -> None:
    application, wire, _service = app
    original = wire.send

    async def unit_response(client: httpx.AsyncClient, request: httpx.Request,
                            **kwargs: Any) -> httpx.Response:
        if request.url.path == "/v5/order/realtime" and request.url.params.get("category") == "spot":
            row: dict[str, object] = {"orderId": "external-market-order", "orderLinkId": "",
                "symbol": symbol, "side": "Buy", "orderType": "Market", "qty": "100",
                "price": "0", "orderStatus": "New", "isLeverage": "0"}
            if market_unit is not None:
                row["marketUnit"] = market_unit
            return httpx.Response(200, request=request, json={"retCode": 0,
                "time": int(wire.now.timestamp() * 1000), "result": {
                    "category": "spot", "nextPageCursor": "", "list": [row]}})
        response = await original(client, request, **kwargs)
        assert isinstance(response, httpx.Response)
        return response

    monkeypatch.setattr(wire, "send", unit_response)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                base_url="https://dashboard.example") as client:
        response = await client.get("/api/v1/account/bybit/demo/view?product=SPOT")
    body = response.json()
    assert body["상태코드"] == "ok" and body["거래상품"] == "SPOT"
    assert body["미체결주문"][0]["수량"] == "100"
    assert body["미체결주문"][0]["수량기준"] == expected
    assert body["주문보냄"] is False and body["주문판단에사용"] is False
    assert all(owner.is_closed for owner in wire.clients)
