"""Public market reads through an injected set of venue adapters."""

import asyncio
import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType

from .adapters.base import INTERVAL_SECONDS, MarketAdapter, MarketDataUnavailable, validate_candle_selection
from .adapters.bybit import BybitAdapter
from .closed_bars import (
    ClosedBarRejected,
    ClosedBarScope,
    ClosedBarSnapshot,
    ClosedBarSnapshotStore,
)
from .models import Candle, MarketType, Ticker
from ..account.instrument_envelope import CurrentCatalogReceipt
from .instrument_producer import BybitInstrumentCatalogProducer
from .public_observations_v1 import (
    BybitPublicQuoteSnapshotV1,
    PublicObservationRejected,
    bind_bybit_quote_snapshot_v1,
    validate_candle_source_binding_v1,
)


class MarketDataService:
    __slots__ = ("_adapters", "_bybit_public_owner_v1", "_sealed")

    def __init__(self, adapters: Mapping[str, MarketAdapter]):
        self._adapters = MappingProxyType(dict(adapters))
        candidate = self._adapters.get("bybit")
        self._bybit_public_owner_v1 = candidate if type(candidate) is BybitAdapter else None
        self._sealed = True

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("MarketDataService installation is immutable")
        object.__setattr__(self, name, value)

    def _require_bybit_public_owner_v1(self) -> BybitAdapter:
        owner = self._bybit_public_owner_v1
        if (type(owner) is not BybitAdapter or self._adapters.get("bybit") is not owner):
            raise MarketDataUnavailable("public_source_required", "A pinned Bybit public adapter is required")
        return owner

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

    async def get_closed_snapshot_v1(
        self,
        symbol: str,
        interval: str,
        limit: int,
        *,
        path: Path,
        catalog_producer: BybitInstrumentCatalogProducer,
        catalog_receipt: CurrentCatalogReceipt,
        expected_catalog_revision: str,
        source_ttl: timedelta,
        max_bar_age: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> ClosedBarSnapshot:
        """Persist LINEAR candles with the exact public source and current A02 catalog pin."""
        validate_candle_selection(symbol, "perpetual", interval, limit)
        if (type(source_ttl) is not timedelta or not timedelta(0) < source_ttl <= timedelta(minutes=5)
                or type(max_bar_age) is not timedelta or not timedelta(0) < max_bar_age <= timedelta(days=7)):
            raise MarketDataUnavailable("closed_bar_ttl_invalid", "Closed candle lifetime is invalid")
        adapter = self._require_bybit_public_owner_v1()
        current = clock or (lambda: datetime.now(UTC))
        try:
            read = await adapter.get_candle_read_v1(symbol, interval, limit)
            now = current()
            catalog_revision, instrument_revision, source_expiry = validate_candle_source_binding_v1(
                read, catalog_producer, catalog_receipt, expected_catalog_revision, adapter=adapter,
                now=now, max_age=source_ttl)
            source = read.source
            if (source.received_at - source.request_started_at > timedelta(seconds=10)
                    or source.endpoint != "https://api.bybit.com/v5/market/kline"):
                raise PublicObservationRejected("public_source_window_invalid")
            step = timedelta(seconds=INTERVAL_SECONDS[interval])
            closed = tuple(item for item in read.candles if item.is_closed)
            if not closed:
                raise PublicObservationRejected("closed_bar_unclosed")
            watermark = closed[-1].timestamp + step
            if watermark > source.server_observed_at or now < watermark:
                raise PublicObservationRejected("closed_bar_future")
            if now - watermark > max_bar_age:
                raise PublicObservationRejected("closed_bar_stale")
            valid_until = min(source_expiry, watermark + max_bar_age)
            if now >= valid_until:
                raise PublicObservationRejected("closed_bar_stale")
            result = await asyncio.to_thread(
                ClosedBarSnapshotStore(path).capture,
                ClosedBarScope(symbol, interval),
                read.candles,
                collected_at=source.received_at,
                cutoff=source.server_observed_at,
                source=source,
                catalog_revision=catalog_revision,
                instrument_revision=instrument_revision,
                valid_until=valid_until,
            )
            completed_at = current()
            if (type(completed_at) is not datetime or completed_at.tzinfo is None
                    or completed_at.utcoffset() is None
                    or completed_at.astimezone(UTC) >= result.valid_until):
                raise PublicObservationRejected("closed_bar_stale")
            catalog_producer.require_current(catalog_receipt, expected_revision=expected_catalog_revision)
            return result
        except PublicObservationRejected as error:
            raise MarketDataUnavailable(error.reason, "Closed candle source was rejected") from None
        except ClosedBarRejected as error:
            raise MarketDataUnavailable(error.reason, "Closed candle input was rejected") from None
        except (ValueError, TypeError, KeyError, sqlite3.Error, OSError):
            raise MarketDataUnavailable("closed_bar_storage", "Closed candle storage is unavailable") from None

    async def get_public_quote_snapshot_v1(
        self,
        symbol: str,
        *,
        catalog_producer: BybitInstrumentCatalogProducer,
        catalog_receipt: CurrentCatalogReceipt,
        expected_catalog_revision: str,
        max_age: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> BybitPublicQuoteSnapshotV1:
        """Capture a fresh, catalog-bound public best bid/ask without account access."""
        if symbol not in ("BTCUSDT", "ETHUSDT"):
            raise MarketDataUnavailable("unsupported", "Unsupported public quote symbol")
        adapter = self._require_bybit_public_owner_v1()
        current = clock or (lambda: datetime.now(UTC))
        try:
            read = await adapter.get_quote_read_v1(symbol)
            result = bind_bybit_quote_snapshot_v1(
                read, catalog_producer, catalog_receipt, expected_catalog_revision,
                adapter=adapter, now=current(), max_age=max_age)
            completed_at = current()
            if (type(completed_at) is not datetime or completed_at.tzinfo is None
                    or completed_at.utcoffset() is None
                    or completed_at.astimezone(UTC) >= result.valid_until):
                raise PublicObservationRejected("public_source_stale")
            catalog_producer.require_current(catalog_receipt, expected_revision=expected_catalog_revision)
            return result
        except PublicObservationRejected as error:
            raise MarketDataUnavailable(error.reason, "Public quote source was rejected") from None
        except (ValueError, TypeError, KeyError):
            raise MarketDataUnavailable("public_quote_invalid", "Public quote source was rejected") from None
