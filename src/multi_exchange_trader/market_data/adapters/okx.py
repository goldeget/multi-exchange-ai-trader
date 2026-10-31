"""Disabled pending authoritative V5 contract verification. Never calls transport."""

import httpx

from ..models import Candle, MarketType, Ticker
from .base import MarketAdapter, MarketDataUnavailable


class OKXAdapter(MarketAdapter):
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def get_ticker(self, symbol: str, market_type: MarketType) -> Ticker:
        raise MarketDataUnavailable("unverified", "OKX public market contract is unverified")

    async def get_candles(
        self, symbol: str, market_type: MarketType, interval: str, limit: int
    ) -> list[Candle]:
        raise MarketDataUnavailable("unverified", "OKX public market contract is unverified")
