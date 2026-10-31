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
