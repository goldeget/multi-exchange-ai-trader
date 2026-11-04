"""Public market reads through an injected set of venue adapters."""

import asyncio
import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .adapters.base import MarketAdapter, MarketDataUnavailable, validate_candle_selection
from .closed_bars import (
    ClosedBarRejected,
    ClosedBarScope,
    ClosedBarSnapshot,
    ClosedBarSnapshotStore,
)
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

    async def get_closed_snapshot(self, exchange: str, symbol: str, market_type: MarketType,
                                  interval: str, limit: int, *, path: Path,
                                  clock: Callable[[], datetime] | None = None) -> ClosedBarSnapshot:
        """Collect public Bybit linear bars into an observation-only durable identity."""
        if exchange != "bybit" or market_type != "perpetual":
            raise MarketDataUnavailable("unsupported", "Closed snapshots require Bybit linear")
        validate_candle_selection(symbol, market_type, interval, limit)
        current = clock or (lambda: datetime.now(UTC))
        start = current()
        values = await self.get_candles(exchange, symbol, market_type, interval, limit)
        end = current()
        try:
            if start.tzinfo is None or end.tzinfo is None or not timedelta(0) <= end - start <= timedelta(seconds=10):
                raise ClosedBarRejected("closed_bar_clock")
            return await asyncio.to_thread(ClosedBarSnapshotStore(path).capture,
                ClosedBarScope(symbol, interval), values, collected_at=end, cutoff=end)
        except ClosedBarRejected as error:
            raise MarketDataUnavailable(error.reason, "Closed candle input was rejected") from None
        except (ValueError, TypeError, KeyError, sqlite3.Error, OSError):
            raise MarketDataUnavailable("closed_bar_storage", "Closed candle storage is unavailable") from None
