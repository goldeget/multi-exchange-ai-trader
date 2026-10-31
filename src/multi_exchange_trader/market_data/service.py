"""Public market reads through an injected set of venue adapters."""

from collections.abc import Mapping

from .adapters.base import MarketAdapter
from .models import Candle, MarketType, Ticker


class MarketDataService:
    def __init__(self, adapters: Mapping[str, MarketAdapter]):
        self._adapters = dict(adapters)

    async def get_ticker(self, exchange: str, symbol: str, market_type: MarketType) -> Ticker:
        return await self._adapters[exchange].get_ticker(symbol, market_type)

    async def get_candles(
        self, exchange: str, symbol: str, market_type: MarketType, interval: str, limit: int
    ) -> list[Candle]:
        return await self._adapters[exchange].get_candles(symbol, market_type, interval, limit)
