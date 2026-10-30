"""Read API contracts exercised in process with local exchange transports only."""

from contextlib import asynccontextmanager

import httpx
import pytest

from multi_exchange_trader.market_data.adapters.binance import BinanceAdapter
from multi_exchange_trader.market_data.adapters.bybit import BybitAdapter
from multi_exchange_trader.market_data.adapters.okx import OKXAdapter


@asynccontextmanager
async def api_client(handler):
    from multi_exchange_trader.api import create_app
    from multi_exchange_trader.market_data.service import MarketDataService

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as provider:
        service = MarketDataService({
            "binance": BinanceAdapter(provider),
            "bybit": BybitAdapter(provider),
            "okx": OKXAdapter(provider),
        })
        app = create_app(service)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local.test"
        ) as client:
            yield client, app


def forbidden_transport(request):
    pytest.fail(f"Unexpected exchange transport: {request.url.host}")


async def test_health_and_route_inventory_have_no_exchange_access():
    async with api_client(forbidden_transport) as (client, app):
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert {(route.path, tuple(sorted(route.methods))) for route in app.routes} == {
            ("/api/v1/health", ("GET",)),
            ("/api/v1/market/ticker", ("GET",)),
            ("/api/v1/market/candles", ("GET",)),
        }


@pytest.mark.parametrize("market", ["spot", "perpetual"])
async def test_ticker_preserves_decimal_and_instrument_metadata(market):
    def respond(request):
        assert request.url.path == (
            "/api/v3/ticker/price" if market == "spot" else "/fapi/v2/ticker/price"
        )
        assert dict(request.url.params) == {"symbol": "ETHUSDT"}
        body = {"symbol": "ETHUSDT", "price": "10.1234567890123456789"}
        if market == "perpetual":
            body["time"] = 1704067260000
        return httpx.Response(200, json=body)

    async with api_client(respond) as (client, _):
        response = await client.get("/api/v1/market/ticker", params={
            "exchange": "binance", "symbol": "ETHUSDT", "market_type": market,
        })
    assert response.status_code == 200
    body = response.json()
    assert body["last_price"] == "10.1234567890123456789"
    assert (body["exchange"], body["symbol"], body["market_type"]) == (
        "binance", "ETHUSDT", market,
    )
    assert body["fetched_at"].endswith("Z")
    assert body["observed_at"] == (None if market == "spot" else "2024-01-01T00:01:00Z")


@pytest.mark.parametrize("limit", [None, 1, 500])
async def test_candles_are_ascending_and_forward_interval_and_limit(limit):
    def respond(request):
        assert request.url.path == "/v5/market/kline"
        assert dict(request.url.params) == {
            "category": "linear", "symbol": "BTCUSDT", "interval": "60",
            "limit": str(200 if limit is None else limit),
        }
        rows = [["1704070800000", "10", "12", "9", "11", "2.5", "26"]]
        if limit != 1:
            rows.append(["1704067200000", "9", "11", "8", "10", "1", "10"])
        return httpx.Response(200, json={
            "retCode": 0, "retMsg": "OK", "retExtInfo": {}, "time": 1704074400000,
            "result": {"category": "linear", "symbol": "BTCUSDT", "list": rows},
        })

    params = {"exchange": "bybit", "symbol": "BTCUSDT", "market_type": "perpetual",
              "interval": "1h"}
    if limit is not None:
        params["limit"] = limit
    async with api_client(respond) as (client, _):
        response = await client.get("/api/v1/market/candles", params=params)
    assert response.status_code == 200
    body = response.json()
    assert [row["timestamp"] for row in body] == (
        ["2024-01-01T01:00:00Z"] if limit == 1 else
        ["2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"]
    )
    assert body[-1] == {"timestamp": "2024-01-01T01:00:00Z", "open": "10", "high": "12",
                        "low": "9", "close": "11", "volume": "2.5", "is_closed": True}


@pytest.mark.parametrize("route", ["ticker", "candles"])
@pytest.mark.parametrize("field,value", [
    ("exchange", "unknown"), ("exchange", "BINANCE"), ("symbol", "SOLUSDT"),
    ("market_type", "futures"), ("symbol", ""), ("exchange", None),
    ("symbol", None), ("market_type", None),
])
async def test_invalid_selection_returns_422_before_transport(route, field, value):
    params = {"exchange": "binance", "symbol": "BTCUSDT", "market_type": "spot",
              "interval": "1m"}
    if value is None:
        params.pop(field)
    else:
        params[field] = value
    async with api_client(forbidden_transport) as (client, _):
        response = await client.get(f"/api/v1/market/{route}", params=params)
    assert response.status_code == 422


@pytest.mark.parametrize("field,value", [
    ("interval", "2m"), ("interval", None), ("limit", "0"), ("limit", "501"),
    ("limit", "1.5"), ("limit", "true"),
])
async def test_invalid_candle_query_returns_422_before_transport(field, value):
    params = {"exchange": "binance", "symbol": "BTCUSDT", "market_type": "spot",
              "interval": "1m"}
    if value is None:
        params.pop(field)
    else:
        params[field] = value
    async with api_client(forbidden_transport) as (client, _):
        response = await client.get("/api/v1/market/candles", params=params)
    assert response.status_code == 422


@pytest.mark.parametrize("route", ["ticker", "candles"])
@pytest.mark.parametrize("failure,reason", [
    ("timeout", "timeout"), ("http", "upstream"), ("rate", "rate_limited"),
    ("malformed", "malformed"),
])
async def test_provider_failures_return_safe_503(route, failure, reason):
    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("secret-header https://private.example", request=request)
        if failure == "malformed":
            return httpx.Response(200, text="secret-header https://private.example")
        return httpx.Response(429 if failure == "rate" else 500,
                              text="secret-header https://private.example")

    async with api_client(respond) as (client, _):
        response = await client.get(f"/api/v1/market/{route}", params={
            "exchange": "binance", "symbol": "BTCUSDT", "market_type": "spot",
            "interval": "1m",
        })
    assert response.status_code == 503
    assert response.json() == {"reason": reason, "message": "Market data is unavailable."}


@pytest.mark.parametrize("route", ["ticker", "candles"])
async def test_okx_is_unverified_without_any_transport_call(route):
    async with api_client(forbidden_transport) as (client, _):
        response = await client.get(f"/api/v1/market/{route}", params={
            "exchange": "okx", "symbol": "BTCUSDT", "market_type": "spot", "interval": "1m",
        })
    assert response.status_code == 503
    assert response.json() == {"reason": "unverified", "message": "Market data is unavailable."}


async def test_main_exports_the_api_app_without_provider_access():
    from multi_exchange_trader.api import app
    from multi_exchange_trader.main import app as main_app

    assert main_app is app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main_app), base_url="http://local.test"
    ) as client:
        assert (await client.get("/api/v1/health")).json() == {"status": "ok"}


async def test_default_lifespan_shares_and_closes_provider_client(monkeypatch):
    from multi_exchange_trader import api

    exchange_requests = []

    def record_exchange_request(request):
        exchange_requests.append(request)
        return httpx.Response(200)

    provider_client = api.PublicMarketClient(
        transport=httpx.MockTransport(record_exchange_request)
    )
    client_constructions = 0

    def make_provider_client(**kwargs):
        nonlocal client_constructions
        client_constructions += 1
        return provider_client

    monkeypatch.setattr(api, "PublicMarketClient", make_provider_client)

    async with api.app.router.lifespan_context(api.app):
        service = api.app.state.market_data_service
        assert isinstance(service, api.MarketDataService)
        assert client_constructions == 1
        assert set(service._adapters) == {"binance", "bybit", "okx"}
        assert all(
            adapter.client is provider_client
            for adapter in service._adapters.values()
        )
        assert not provider_client.is_closed
        assert exchange_requests == []

    assert provider_client.is_closed
    assert exchange_requests == []
