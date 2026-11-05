import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from conftest import bybit_row, payload

from multi_exchange_trader.market_data.adapters.base import MarketDataUnavailable
from multi_exchange_trader.market_data.adapters.bybit import BybitAdapter


@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT"])
@pytest.mark.parametrize("market,category", [("spot", "spot"), ("perpetual", "linear")])
async def test_ticker_mapping_and_response_timestamp(symbol, market, category):
    def respond(request):
        assert request.method == "GET"
        assert request.url.host == "api.bybit.com"
        assert request.url.path == "/v5/market/tickers"
        assert dict(request.url.params) == {"symbol": symbol, "category": category}
        return httpx.Response(200, json=payload(BybitAdapter, symbol=symbol, market=market))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        before = datetime.now(UTC)
        item = await BybitAdapter(client).get_ticker(symbol, market)
        assert item.exchange == "bybit" and item.symbol == symbol and item.market_type == market
        assert item.last_price == Decimal("10.1234567890123456789")
        assert item.observed_at == datetime(2024, 1, 1, 0, 1, tzinfo=UTC)
        assert before <= item.fetched_at <= datetime.now(UTC)
        assert item.source == "https://api.bybit.com/v5/market/tickers"


@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT"])
@pytest.mark.parametrize("market,category", [("spot", "spot"), ("perpetual", "linear")])
@pytest.mark.parametrize(
    "interval,wire",
    [("1m", "1"), ("5m", "5"), ("15m", "15"), ("1h", "60"), ("4h", "240"), ("1d", "D")],
)
async def test_candle_mapping_order_and_closure(symbol, market, category, interval, wire):
    def respond(request):
        assert request.url.host == "api.bybit.com"
        assert request.url.path == "/v5/market/kline"
        assert dict(request.url.params) == {
            "symbol": symbol,
            "category": category,
            "interval": wire,
            "limit": "500",
        }
        body = payload(BybitAdapter, "candles", symbol, market)
        body["result"]["list"] = [bybit_row("1704153600000"), bybit_row()]
        body["time"] = 1704153600000
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        items = await BybitAdapter(client).get_candles(symbol, market, interval, 500)
        assert items[0].timestamp == datetime(2024, 1, 1, tzinfo=UTC)
        assert items[1].timestamp == datetime(2024, 1, 2, tzinfo=UTC)
        assert [item.is_closed for item in items] == [True, False]
        assert items[0].volume == Decimal("2.5")


@pytest.mark.parametrize("kind", ["ticker", "candles"])
@pytest.mark.parametrize("mutation", ["retcode", "category", "symbol", "time", "rows"])
async def test_wrong_envelopes_are_rejected(kind, mutation, make_client):
    body = payload(BybitAdapter, kind)
    match mutation:
        case "retcode":
            body["retCode"] = 10006
        case "category":
            body["result"]["category"] = "inverse"
        case "symbol":
            if kind == "ticker":
                body["result"]["list"][0]["symbol"] = "ETHUSDT"
            else:
                body["result"]["symbol"] = "ETHUSDT"
        case "time":
            body["time"] = True
        case "rows":
            body["result"]["list"] = {}
    async with make_client(body) as client:
        with pytest.raises(MarketDataUnavailable):
            if kind == "ticker":
                await BybitAdapter(client).get_ticker("BTCUSDT", "spot")
            else:
                await BybitAdapter(client).get_candles("BTCUSDT", "spot", "1m", 1)


async def test_source_bound_linear_candle_and_quote_keep_exact_public_response_custody() -> None:
    from multi_exchange_trader.market_data.bybit_public_contracts import CANDLE_URL, TICKER_URL

    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    now_ms = int(now.timestamp() * 1000)
    start_ms = now_ms - 60_000
    captured: list[tuple[str, tuple[tuple[str, str], ...], bytes]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        query = tuple(sorted((key, value) for key, value in request.url.params.multi_items()))
        if request.url.path == "/v5/market/kline":
            body = {"retCode": 0, "result": {"category": "linear", "symbol": "BTCUSDT",
                "list": [[str(start_ms), "100", "102", "99", "101", "4", "400"]]}, "time": now_ms}
        else:
            assert request.url.path == "/v5/market/tickers"
            body = {"retCode": 0, "result": {"category": "linear", "list": [{"symbol": "BTCUSDT",
                "bid1Price": "100", "bid1Size": "2", "ask1Price": "101", "ask1Size": "3"}]}, "time": now_ms}
        raw = json.dumps(body, separators=(",", ":")).encode()
        captured.append((request.url.path, query, raw))
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = BybitAdapter(client, clock=lambda: now)
        candles = await adapter.get_candle_read_v1("BTCUSDT", "1m", 1)
        quote = await adapter.get_quote_read_v1("BTCUSDT")

    assert captured[0][:2] == ("/v5/market/kline", (("category", "linear"), ("interval", "1"),
        ("limit", "1"), ("symbol", "BTCUSDT")))
    assert captured[1][:2] == ("/v5/market/tickers", (("category", "linear"), ("symbol", "BTCUSDT")))
    assert candles.source.endpoint == CANDLE_URL and candles.source.query == captured[0][1]
    assert candles.source.raw_body_sha256 == hashlib.sha256(captured[0][2]).hexdigest()
    assert candles.source.server_observed_at == now and candles.candles[0].is_closed
    assert quote.source.endpoint == TICKER_URL and quote.source.query == captured[1][1]
    assert quote.source.raw_body_sha256 == hashlib.sha256(captured[1][2]).hexdigest()
    assert (quote.bid_price, quote.bid_size, quote.ask_price, quote.ask_size) == (
        Decimal("100"), Decimal("2"), Decimal("101"), Decimal("3"))


async def test_source_bound_reader_rejects_wrong_route_and_product() -> None:
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    now_ms = int(now.timestamp() * 1000)
    body = {"retCode": 0, "result": {"category": "linear", "symbol": "BTCUSDT",
        "list": [[str(now_ms - 60_000), "100", "102", "99", "101", "4", "400"]]}, "time": now_ms}
    raw = json.dumps(body, separators=(",", ":")).encode()

    def wrong_route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"},
            request=httpx.Request("GET", "https://api-testnet.bybit.com/v5/market/kline?category=linear&interval=1&limit=1&symbol=BTCUSDT"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(wrong_route)) as client:
        with pytest.raises(MarketDataUnavailable, match="source did not match"):
            await BybitAdapter(client, clock=lambda: now).get_candle_read_v1("BTCUSDT", "1m", 1)

    body["result"]["category"] = "spot"
    raw = json.dumps(body, separators=(",", ":")).encode()

    def wrong_product(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(wrong_product)) as client:
        with pytest.raises(MarketDataUnavailable):
            await BybitAdapter(client, clock=lambda: now).get_candle_read_v1("BTCUSDT", "1m", 1)
