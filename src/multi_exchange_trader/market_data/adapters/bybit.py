"""Bybit V5 spot/linear public reads; envelope time is server response time.

It is not a last-trade timestamp. See the task report for official source contracts.
"""

from datetime import datetime, timedelta
from typing import Any

import httpx

from ..models import Candle, MarketType, Ticker
from .base import (
    INTERVAL_SECONDS,
    MarketAdapter,
    MarketDataUnavailable,
    ordered_candles,
    parsing,
    request_json,
    timestamp_ms,
    validate_candle_selection,
    validate_selection,
)

TICKER_URL = "https://api.bybit.com/v5/market/tickers"
CANDLE_URL = "https://api.bybit.com/v5/market/kline"
CATEGORIES = {"spot": "spot", "perpetual": "linear"}
INTERVALS = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}


def _envelope(body: Any, category: str) -> tuple[dict[str, Any], datetime]:
    if not isinstance(body, dict) or type(body["retCode"]) is not int:
        raise ValueError("Invalid envelope")
    if body["retCode"] != 0:
        reason = "rate_limited" if body["retCode"] == 10006 else "upstream"
        raise MarketDataUnavailable(reason, "Provider returned an error")
    result = body["result"]
    if not isinstance(result, dict) or result["category"] != category:
        raise ValueError("Unexpected category")
    if not isinstance(result["list"], list):
        raise TypeError("Expected result list")
    return result, timestamp_ms(body["time"])


class BybitAdapter(MarketAdapter):
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def get_ticker(self, symbol: str, market_type: MarketType) -> Ticker:
        validate_selection(symbol, market_type)
        category = CATEGORIES[market_type]
        body, fetched_at = await request_json(
            self.client, TICKER_URL, {"symbol": symbol, "category": category}
        )
        with parsing():
            result, observed_at = _envelope(body, category)
            rows = result["list"]
            if len(rows) != 1 or not isinstance(rows[0], dict) or rows[0]["symbol"] != symbol:
                raise ValueError("Unexpected ticker symbol")
            return Ticker(
                exchange="bybit",
                symbol=symbol,
                market_type=market_type,
                last_price=rows[0]["lastPrice"],
                observed_at=observed_at,
                fetched_at=fetched_at,
                source=TICKER_URL,
            )

    async def get_candles(
        self, symbol: str, market_type: MarketType, interval: str, limit: int
    ) -> list[Candle]:
        validate_candle_selection(symbol, market_type, interval, limit)
        category = CATEGORIES[market_type]
        body, _ = await request_json(
            self.client,
            CANDLE_URL,
            {
                "symbol": symbol,
                "category": category,
                "interval": INTERVALS[interval],
                "limit": limit,
            },
        )
        with parsing():
            result, observed_at = _envelope(body, category)
            if result["symbol"] != symbol:
                raise ValueError("Unexpected candle symbol")
            items = []
            for row in result["list"]:
                if not isinstance(row, list) or len(row) != 7:
                    raise ValueError("Invalid kline schema")
                start = timestamp_ms(row[0])
                if start > observed_at:
                    raise ValueError("Candle starts after provider timestamp")
                end = start + timedelta(seconds=INTERVAL_SECONDS[interval])
                items.append(
                    Candle(
                        timestamp=start,
                        open=row[1],
                        high=row[2],
                        low=row[3],
                        close=row[4],
                        volume=row[5],
                        is_closed=end <= observed_at,
                    )
                )
            return ordered_candles(items, limit)
