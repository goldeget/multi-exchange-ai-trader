"""Bybit V5 spot/linear public reads; envelope time is server response time.

It is not a last-trade timestamp. See the task report for official source contracts.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx

from ..models import Candle, MarketType, Ticker
from .base import (
    INTERVAL_SECONDS,
    MarketAdapter,
    MarketDataUnavailable,
    ordered_candles,
    parsing,
    request_json,
    request_json_observation_v1,
    timestamp_ms,
    validate_candle_selection,
    validate_selection,
)

from ..bybit_public_contracts import CATEGORIES, CANDLE_URL, INTERVALS, TICKER_URL
from ..public_observations_v1 import (
    BybitCandleReadV1,
    BybitTickerQuoteReadV1,
    make_source_ref_v1,
)


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
    def __init__(self, client: httpx.AsyncClient, *, clock: Any = None):
        self.client = client
        self._clock = clock or (lambda: datetime.now(UTC))
        self._source_generation = uuid4().hex

    async def get_candle_read_v1(self, symbol: str, interval: str, limit: int) -> BybitCandleReadV1:
        """Capture linear candles with the exact public response custody fields."""
        validate_candle_selection(symbol, "perpetual", interval, limit)
        params = {
            "symbol": symbol,
            "category": "linear",
            "interval": INTERVALS[interval],
            "limit": limit,
        }
        read = await request_json_observation_v1(self.client, CANDLE_URL, params, clock=self._clock)
        expected_query = tuple(sorted((key, str(value)) for key, value in params.items()))
        if read.endpoint != CANDLE_URL or read.query != expected_query:
            raise MarketDataUnavailable("source_mismatch", "Public candle source did not match request")
        with parsing():
            result, observed_at = _envelope(read.body, "linear")
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
                items.append(Candle(
                    timestamp=start,
                    open=row[1],
                    high=row[2],
                    low=row[3],
                    close=row[4],
                    volume=row[5],
                    is_closed=end <= observed_at,
                ))
            candles = tuple(ordered_candles(items, limit))
            source = make_source_ref_v1(
                source_generation=self._source_generation,
                endpoint=read.endpoint,
                query=read.query,
                request_started_at=read.request_started_at,
                received_at=read.received_at,
                server_observed_at=observed_at,
                raw_body_sha256=read.raw_body_sha256,
            )
            return BybitCandleReadV1(symbol, interval, limit, candles, source)

    async def get_quote_read_v1(self, symbol: str) -> BybitTickerQuoteReadV1:
        """Capture a linear best bid/ask with source timestamps and response hash."""
        validate_selection(symbol, "perpetual")
        params = {"symbol": symbol, "category": "linear"}
        read = await request_json_observation_v1(self.client, TICKER_URL, params, clock=self._clock)
        expected_query = tuple(sorted((key, str(value)) for key, value in params.items()))
        if read.endpoint != TICKER_URL or read.query != expected_query:
            raise MarketDataUnavailable("source_mismatch", "Public quote source did not match request")
        with parsing():
            result, observed_at = _envelope(read.body, "linear")
            rows = result["list"]
            if len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get("symbol") != symbol:
                raise ValueError("Unexpected ticker symbol")
            row = rows[0]
            source = make_source_ref_v1(
                source_generation=self._source_generation,
                endpoint=read.endpoint,
                query=read.query,
                request_started_at=read.request_started_at,
                received_at=read.received_at,
                server_observed_at=observed_at,
                raw_body_sha256=read.raw_body_sha256,
            )
            return BybitTickerQuoteReadV1(
                symbol, Decimal(row["bid1Price"]), Decimal(row["bid1Size"]),
                Decimal(row["ask1Price"]), Decimal(row["ask1Size"]), source,
            )

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
