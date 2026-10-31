"""Offline production lifespan/API wire tests; no real exchange traffic."""

import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from multi_exchange_trader.api import create_app


@asynccontextmanager
async def production(monkeypatch, handler, blocked=""):
    monkeypatch.setenv("MARKET_DATA_BLOCKED_HOSTS", blocked)
    mock = httpx.MockTransport(handler)

    async def wire(self, request):
        return await mock.handle_async_request(request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", wire)
    app = create_app()
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local.test"
    ) as client:
        yield client, app


def selection(exchange="binance", market="perpetual", symbol="BTCUSDT"):
    return {"exchange": exchange, "market_type": market, "symbol": symbol, "interval": "1m"}


@pytest.mark.parametrize("status", [403, 451])
async def test_production_concurrent_denial_serializes_host_and_keeps_other_host(monkeypatch, status):
    attempts = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def wire(request):
        attempts.append(request.url.host)
        assert request.method == "GET" and request.content == b""
        if request.url.host == "fapi.binance.com":
            entered.set()
            await release.wait()
            return httpx.Response(status, text="identifier=PRIVATE unclassified denial")
        return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "10"})

    async with production(monkeypatch, wire) as (client, _):
        first = asyncio.create_task(client.get("/api/v1/market/ticker", params=selection()))
        await entered.wait()
        queued = [asyncio.create_task(client.get(
            f"/api/v1/market/{kind}", params=selection(symbol=symbol)
        )) for kind in ["ticker", "candles"] for symbol in ["BTCUSDT", "ETHUSDT"]]
        # Other host must progress while this host remains held in its network response.
        other = await asyncio.wait_for(client.get(
            "/api/v1/market/ticker", params=selection(market="spot")
        ), 2)
        assert other.status_code == 200
        release.set()
        replies = await asyncio.gather(first, *queued)
    assert attempts.count("fapi.binance.com") == 1
    assert attempts.count("data-api.binance.vision") == 1
    assert all(r.status_code == 503 and r.json()["reason"] == "access_restricted" for r in replies)
    assert all("PRIVATE" not in r.text for r in replies)


async def test_production_preblocked_first_send_zero_across_selections_and_workers(monkeypatch):
    attempts = []

    def wire(request):
        attempts.append(request.url.host)
        return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "10"})

    for _ in range(2):  # Independent app lifespans emulate workers/restart; no shared memory.
        async with production(monkeypatch, wire, "fapi.binance.com,api.bybit.com") as (client, _):
            for exchange, market in [("binance", "perpetual"), ("bybit", "spot"),
                                     ("bybit", "perpetual")]:
                for symbol in ["BTCUSDT", "ETHUSDT"]:
                    for kind in ["ticker", "candles"]:
                        reply = await client.get(f"/api/v1/market/{kind}",
                                                 params=selection(exchange, market, symbol))
                        assert reply.json()["reason"] == "operator_disabled"
            assert (await client.get("/api/v1/market/ticker",
                                     params=selection(market="spot"))).status_code == 200
    assert attempts == ["data-api.binance.vision", "data-api.binance.vision"]


@pytest.mark.parametrize("bad", ["https://api.bybit.com", "api.bybit.com:443", "evil.test",
                                  "api.bybit.com,", "API.BYBIT.COM", "api.bybit.com/path"])
async def test_production_invalid_block_setting_fails_startup_without_echo(monkeypatch, bad):
    monkeypatch.setenv("MARKET_DATA_BLOCKED_HOSTS", bad)
    app = create_app()
    with pytest.raises(ValueError, match="MARKET_DATA_BLOCKED_HOSTS") as exc:
        async with app.router.lifespan_context(app):
            pytest.fail("Invalid setting did not fail startup")
    assert bad not in str(exc.value)


@pytest.mark.parametrize("body,reason", [
    ("restricted location; secret=PRIVATE 192.0.2.1", "geographic_restriction"),
    ("block access from your country; Request ID=PRIVATE", "geographic_restriction"),
    ("403 Forbidden US IP? secret=PRIVATE", "access_restricted"),
])
async def test_production_safe_classification_and_no_raw_payload(monkeypatch, body, reason):
    attempts = []

    def wire(request):
        attempts.append(request.url.host)
        return httpx.Response(403, text=body)

    async with production(monkeypatch, wire) as (client, _):
        for market in ["spot", "perpetual"]:
            reply = await client.get("/api/v1/market/ticker", params=selection("bybit", market))
            assert reply.json() == {"reason": reason, "message": "Market data is unavailable."}
            assert "PRIVATE" not in reply.text and "192.0.2.1" not in reply.text
    assert attempts == ["api.bybit.com"]


async def test_production_transient_timeout_keeps_recovery_possible(monkeypatch):
    attempts = []

    def wire(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.ReadTimeout("PRIVATE")
        return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "10"})

    async with production(monkeypatch, wire) as (client, _):
        assert (await client.get("/api/v1/market/ticker", params=selection(market="spot"))).json()[
            "reason"] == "timeout"
        assert (await client.get("/api/v1/market/ticker", params=selection(market="spot"))).status_code == 200
    assert len(attempts) == 2
