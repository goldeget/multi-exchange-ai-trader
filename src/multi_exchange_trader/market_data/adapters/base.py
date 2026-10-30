"""Public read contract, input allowlists and transport error boundary."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

from multi_exchange_trader.market_data.models import Candle, MarketType, Ticker

SYMBOLS = {"BTCUSDT": "BTCUSDT", "ETHUSDT": "ETHUSDT"}
INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


class MarketDataUnavailable(Exception):
    """A safe, typed failure for the API/UI; messages contain no provider payloads."""

    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(message)


class UnsupportedMarketData(MarketDataUnavailable):
    def __init__(self, message: str):
        super().__init__("unsupported", message)


class MarketAdapter(Protocol):
    async def get_ticker(self, symbol: str, market_type: MarketType) -> Ticker: ...
    async def get_candles(
        self, symbol: str, market_type: MarketType, interval: str, limit: int
    ) -> list[Candle]: ...


def validate_selection(symbol: str, market_type: MarketType) -> None:
    if symbol not in SYMBOLS or market_type not in ("spot", "perpetual"):
        raise UnsupportedMarketData("Unsupported symbol or market type")


def validate_candle_selection(
    symbol: str, market_type: MarketType, interval: str, limit: int
) -> None:
    validate_selection(symbol, market_type)
    if interval not in INTERVAL_SECONDS:
        raise UnsupportedMarketData("Unsupported candle interval")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise UnsupportedMarketData("Candle limit must be an integer from 1 to 500")


async def request_json(
    client: httpx.AsyncClient, url: str, params: dict[str, str | int]
) -> tuple[Any, datetime]:
    try:
        response = await client.get(
            url, params=params, timeout=10, follow_redirects=False, auth=None
        )
        if response.status_code == 429:
            raise MarketDataUnavailable("rate_limited", "Provider rate limit reached")
        if not 200 <= response.status_code < 300:
            raise MarketDataUnavailable("upstream", "Provider request failed")
        return response.json(), datetime.now(UTC)
    except httpx.TimeoutException as exc:
        raise MarketDataUnavailable("timeout", "Provider request timed out") from exc
    except httpx.HTTPError as exc:
        raise MarketDataUnavailable("upstream", "Provider request failed") from exc
    except ValueError as exc:
        raise MarketDataUnavailable("malformed", "Malformed provider response") from exc


@contextmanager
def parsing() -> Iterator[None]:
    try:
        yield
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        raise MarketDataUnavailable("malformed", "Malformed provider response") from exc


def timestamp_ms(value: object) -> datetime:
    if type(value) is int:
        number = value
    elif isinstance(value, str) and value.isascii() and value.isdigit():
        number = int(value)
    else:
        raise ValueError("Invalid millisecond timestamp")
    if number < 0:
        raise ValueError("Negative timestamp")
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=number)


def ordered_candles(items: list[Candle], limit: int) -> list[Candle]:
    if len(items) > limit or len({item.timestamp for item in items}) != len(items):
        raise ValueError("Oversized or duplicate candle batch")
    return sorted(items, key=lambda item: item.timestamp)
