"""Binance public spot/USD-M reads. Source contracts are cited in the task report."""

import httpx

from ..models import Candle, MarketType, Ticker
from .base import (
    MarketAdapter,
    ordered_candles,
    parsing,
    request_json,
    timestamp_ms,
    validate_candle_selection,
    validate_selection,
)

SPOT_HOST = "https://data-api.binance.vision"
PERPETUAL_HOST = "https://fapi.binance.com"
TICKER_URLS = {
    "spot": SPOT_HOST + "/api/v3/ticker/price",
    "perpetual": PERPETUAL_HOST + "/fapi/v2/ticker/price",
}
CANDLE_URLS = {
    "spot": SPOT_HOST + "/api/v3/klines",
    "perpetual": PERPETUAL_HOST + "/fapi/v1/klines",
}
INTERVALS = {key: key for key in ("1m", "5m", "15m", "1h", "4h", "1d")}


class BinanceAdapter(MarketAdapter):
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def get_ticker(self, symbol: str, market_type: MarketType) -> Ticker:
        validate_selection(symbol, market_type)
        url = TICKER_URLS[market_type]
        body, fetched_at = await request_json(self.client, url, {"symbol": symbol})
        with parsing():
            if not isinstance(body, dict) or body["symbol"] != symbol:
                raise ValueError("Unexpected ticker symbol")
            observed_at = timestamp_ms(body["time"]) if market_type == "perpetual" else None
            return Ticker(
                exchange="binance",
                symbol=symbol,
                market_type=market_type,
                last_price=body["price"],
                observed_at=observed_at,
                fetched_at=fetched_at,
                source=url,
            )

    async def get_candles(
        self, symbol: str, market_type: MarketType, interval: str, limit: int
    ) -> list[Candle]:
        validate_candle_selection(symbol, market_type, interval, limit)
        body, fetched_at = await request_json(
            self.client,
            CANDLE_URLS[market_type],
            {"symbol": symbol, "interval": INTERVALS[interval], "limit": limit},
        )
        with parsing():
            if not isinstance(body, list):
                raise TypeError("Expected candle list")
            items = []
            for row in body:
                if not isinstance(row, list) or len(row) != 12:
                    raise ValueError("Invalid kline schema")
                start, end = timestamp_ms(row[0]), timestamp_ms(row[6])
                if start > fetched_at or end < start:
                    raise ValueError("Invalid candle time range")
                items.append(
                    Candle(
                        timestamp=start,
                        open=row[1],
                        high=row[2],
                        low=row[3],
                        close=row[4],
                        volume=row[5],
                        is_closed=end < fetched_at,
                    )
                )
            return ordered_candles(items, limit)
