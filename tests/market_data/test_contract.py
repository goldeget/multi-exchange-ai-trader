from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from conftest import payload
from pydantic import ValidationError

from multi_exchange_trader.market_data.adapters.base import (
    MarketAdapter,
    MarketDataUnavailable,
    UnsupportedMarketData,
)
from multi_exchange_trader.market_data.models import Candle, Ticker

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def candle(**changes):
    return Candle(
        **{
            "timestamp": NOW,
            "open": "10.1234567890123456789",
            "high": "12",
            "low": "9",
            "close": "11",
            "volume": "0",
            "is_closed": True,
        }
        | changes
    )


def test_decimal_precision_and_utc_normalization():
    item = candle(timestamp=NOW.astimezone(timezone(timedelta(hours=9))))
    assert item.open == Decimal("10.1234567890123456789")
    assert item.timestamp.utcoffset() == timedelta(0)
    assert item.volume == Decimal(0)
    with pytest.raises(ValidationError):
        item.volume = Decimal(-1)


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "-Infinity", True, 1.1])
def test_candle_rejects_unsafe_numeric_values(field, value):
    with pytest.raises(ValidationError):
        candle(**{field: value})


@pytest.mark.parametrize(
    "changes",
    [
        {"high": "9"},
        {"low": "11"},
        {"close": "13"},
        {"timestamp": NOW.replace(tzinfo=None)},
        {"is_closed": "true"},
    ],
)
def test_candle_rejects_invalid_relationships_and_types(changes):
    with pytest.raises(ValidationError):
        candle(**changes)


def ticker(**changes):
    return Ticker(
        **{
            "exchange": "binance",
            "symbol": "BTCUSDT",
            "market_type": "spot",
            "last_price": "10.1234567890123456789",
            "observed_at": None,
            "fetched_at": NOW,
            "source": "https://data-api.binance.vision/api/v3/ticker/price",
        }
        | changes
    )


def test_ticker_preserves_missing_observation_and_exact_price():
    item = ticker()
    assert item.observed_at is None
    assert item.last_price == Decimal("10.1234567890123456789")
    assert item.fetched_at == NOW


@pytest.mark.parametrize(
    "changes",
    [
        {"last_price": "-1"},
        {"last_price": "NaN"},
        {"last_price": float("inf")},
        {"last_price": True},
        {"market_type": "inverse"},
        {"fetched_at": NOW.replace(tzinfo=None)},
        {"observed_at": NOW.replace(tzinfo=None)},
    ],
)
def test_ticker_rejects_invalid_values(changes):
    with pytest.raises(ValidationError):
        ticker(**changes)


# Shared adapter behaviors catch missing validation, unsafe dispatch and leaked transport errors.


@pytest.mark.parametrize(
    "kind,symbol,market,interval,limit",
    [
        ("ticker", "https://evil.example", "spot", "1m", 1),
        ("ticker", "BTCUSDT", "inverse", "1m", 1),
        ("candles", "https://evil.example", "spot", "1m", 1),
        ("candles", "BTCUSDT", "inverse", "1m", 1),
        ("candles", "BTCUSDT", "spot", "../../private", 1),
        ("candles", "BTCUSDT", "spot", "1m", 0),
        ("candles", "BTCUSDT", "spot", "1m", 501),
        ("candles", "BTCUSDT", "spot", "1m", True),
        ("candles", "BTCUSDT", "spot", "1m", None),
        ("candles", "BTCUSDT", "spot", None, 1),
        ("candles", "BTCUSDT", "spot", "1m", 1.5),
        ("candles", "BTCUSDT", "spot", "1m", "1"),
    ],
)
async def test_unsupported_inputs_never_reach_transport(
    adapter_class,
    kind,
    symbol,
    market,
    interval,
    limit,
):
    def fail(request):
        pytest.fail("Unsupported input reached upstream")

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        adapter: MarketAdapter = adapter_class(client)
        with pytest.raises(UnsupportedMarketData):
            if kind == "ticker":
                await adapter.get_ticker(symbol, market)
            else:
                await adapter.get_candles(symbol, market, interval, limit)


@pytest.mark.parametrize("kind", ["ticker", "candles"])
@pytest.mark.parametrize("failure", ["timeout", "429", "500", "redirect", "json"])
async def test_provider_failures_are_typed(adapter_class, kind, failure):
    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if failure == "json":
            return httpx.Response(200, text="broken JSON")
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://evil.example/private"})
        return httpx.Response(int(failure), json={"error": "failed"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=True
    ) as client:
        with pytest.raises(MarketDataUnavailable) as caught:
            adapter = adapter_class(client)
            if kind == "ticker":
                await adapter.get_ticker("BTCUSDT", "spot")
            else:
                await adapter.get_candles("BTCUSDT", "spot", "1m", 1)
        assert caught.value.reason in {"timeout", "rate_limited", "upstream", "malformed"}


@pytest.mark.parametrize("bad", ["-1", "NaN", "Infinity", True, 1.1, None])
async def test_bad_ticker_prices_are_typed(adapter_class, bad, make_client):
    body = payload(adapter_class)
    if adapter_class.__name__ == "BinanceAdapter":
        body["price"] = bad
    else:
        body["result"]["list"][0]["lastPrice"] = bad
    async with make_client(body) as client:
        with pytest.raises(MarketDataUnavailable, match="Malformed"):
            await adapter_class(client).get_ticker("BTCUSDT", "spot")


@pytest.mark.parametrize("bad", [None, [], {}, {"symbol": "ETHUSDT", "price": "10"}])
async def test_malformed_ticker_schema_is_typed(adapter_class, bad, make_client):
    async with make_client(bad) as client:
        with pytest.raises(MarketDataUnavailable):
            await adapter_class(client).get_ticker("BTCUSDT", "spot")


@pytest.mark.parametrize(
    "mutation",
    ["negative_volume", "nan", "ohlc", "duplicate", "short_row", "timestamp", "future", "too_many"],
)
async def test_invalid_candle_batches_are_rejected(adapter_class, mutation, make_client):
    body = payload(adapter_class, "candles")
    rows = body if isinstance(body, list) else body["result"]["list"]
    match mutation:
        case "negative_volume":
            rows[0][5] = "-1"
        case "nan":
            rows[0][4] = "NaN"
        case "ohlc":
            rows[0][2] = "8"
        case "duplicate":
            rows.append(rows[0].copy())
        case "short_row":
            rows[0] = rows[0][:5]
        case "timestamp":
            rows[0][0] = True
        case "future":
            rows[0][0] = "4102444800000"
        case "too_many":
            rows.append(rows[0].copy())
            rows[1][0] = "1704067260000"
    async with make_client(body) as client:
        with pytest.raises(MarketDataUnavailable):
            await adapter_class(client).get_candles(
                "BTCUSDT", "spot", "1m", 2 if mutation == "duplicate" else 1
            )
