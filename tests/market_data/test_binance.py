from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from conftest import binance_row, payload

from multi_exchange_trader.market_data.adapters.binance import BinanceAdapter


@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT"])
@pytest.mark.parametrize(
    "market,host,path,observed",
    [
        ("spot", "data-api.binance.vision", "/api/v3/ticker/price", None),
        (
            "perpetual",
            "fapi.binance.com",
            "/fapi/v2/ticker/price",
            datetime(2024, 1, 1, 0, 1, tzinfo=UTC),
        ),
    ],
)
async def test_ticker_mapping_and_timestamp_semantics(symbol, market, host, path, observed):
    def respond(request):
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == host
        assert request.url.path == path
        assert dict(request.url.params) == {"symbol": symbol}
        assert request.extensions["timeout"]["read"] == 10
        return httpx.Response(200, json=payload(BinanceAdapter, symbol=symbol, market=market))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        before = datetime.now(UTC)
        item = await BinanceAdapter(client).get_ticker(symbol, market)
        assert item.exchange == "binance"
        assert item.symbol == symbol and item.market_type == market
        assert item.last_price == Decimal("10.1234567890123456789")
        assert item.observed_at == observed
        assert before <= item.fetched_at <= datetime.now(UTC)
        assert item.source == f"https://{host}{path}"


@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT"])
@pytest.mark.parametrize(
    "market,host,path",
    [
        ("spot", "data-api.binance.vision", "/api/v3/klines"),
        ("perpetual", "fapi.binance.com", "/fapi/v1/klines"),
    ],
)
@pytest.mark.parametrize("interval", ["1m", "5m", "15m", "1h", "4h", "1d"])
async def test_candle_dispatch(symbol, market, host, path, interval):
    def respond(request):
        assert request.url.host == host and request.url.path == path
        assert dict(request.url.params) == {"symbol": symbol, "interval": interval, "limit": "500"}
        return httpx.Response(200, json=[binance_row()])

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        items = await BinanceAdapter(client).get_candles(symbol, market, interval, 500)
        assert items[0].timestamp == datetime(2024, 1, 1, tzinfo=UTC)
        assert items[0].volume == Decimal("2.5") and items[0].is_closed is True


async def test_candles_sorted_with_unfinished_latest(make_client, monkeypatch):
    fetched_at = datetime(2024, 1, 1, 0, 0, 59, 999000, tzinfo=UTC)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fetched_at

    monkeypatch.setattr(
        "multi_exchange_trader.market_data.adapters.base.datetime", FixedDatetime
    )
    async with make_client(
        [binance_row(1704067200000), binance_row(1704067140000)]
    ) as client:
        items = await BinanceAdapter(client).get_candles("BTCUSDT", "spot", "1m", 2)
        assert [(item.timestamp, item.is_closed) for item in items] == [
            (datetime(2023, 12, 31, 23, 59, tzinfo=UTC), True),
            (datetime(2024, 1, 1, 0, 0, tzinfo=UTC), False),
        ]


@pytest.mark.parametrize("change", [{"time": True}, {"time": "bad"}, {"symbol": "ETHUSDT"}])
async def test_perpetual_ticker_rejects_bad_time_or_symbol(change, make_client):
    from multi_exchange_trader.market_data.adapters.base import MarketDataUnavailable

    body = payload(BinanceAdapter, market="perpetual") | change
    async with make_client(body) as client:
        with pytest.raises(MarketDataUnavailable):
            await BinanceAdapter(client).get_ticker("BTCUSDT", "perpetual")
