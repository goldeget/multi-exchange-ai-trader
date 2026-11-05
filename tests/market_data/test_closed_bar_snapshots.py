"""Durable source observations, never financial or order authority."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from multi_exchange_trader.market_data.adapters.base import MarketDataUnavailable
from multi_exchange_trader.market_data.closed_bars import ClosedBarSnapshot
from multi_exchange_trader.market_data.models import Candle, Ticker
from multi_exchange_trader.market_data.service import MarketDataService

NOW = datetime(2026, 11, 4, 12, tzinfo=UTC)
STEP = timedelta(minutes=15)


def bar(stamp: datetime, price: int = 100, *, closed: bool = True) -> Candle:
    return Candle(timestamp=stamp, open=Decimal(price), high=Decimal(price + 1),
        low=Decimal(price - 1), close=Decimal(price), volume=Decimal(1), is_closed=closed)


class Source:
    def __init__(self, rows: list[Candle]) -> None:
        self.rows = rows
        self.calls = 0

    async def get_candles(self, *_args: object, **_kwargs: object) -> list[Candle]:
        self.calls += 1
        return list(self.rows)

    async def get_ticker(self, *_args: object, **_kwargs: object) -> Ticker:
        raise AssertionError("Candle collection does not request a ticker")


async def collect(market: MarketDataService, path: Path, now: datetime, *, symbol: str = "BTCUSDT", interval: str = "15m") -> ClosedBarSnapshot:
    return await market.get_closed_snapshot("bybit", symbol, "perpetual", interval, 80,
        path=path, clock=lambda: now)


async def test_first_collection_and_input_identity_survive_restart_and_active_tail(tmp_path: Path) -> None:
    path = tmp_path / "bars.sqlite3"
    source = Source([bar(NOW - STEP * index) for index in (3, 2, 1)])
    first = await collect(MarketDataService({"bybit": source}), path, NOW)
    assert first.watermark == NOW and first.collected_at == NOW
    assert first.is_new_input is True and len(first.candles) == 3
    assert first.financial_authority is False and first.trading_enabled is False
    source.rows.append(bar(NOW, closed=False))
    repeated = await collect(MarketDataService({"bybit": source}), path, NOW + timedelta(minutes=5))
    assert repeated.snapshot_id == first.snapshot_id
    assert repeated.collected_at == first.collected_at and repeated.cutoff == first.cutoff
    assert repeated.is_new_input is False and repeated.candles == first.candles
    assert all(candle.is_closed for candle in repeated.candles)


async def test_gap_keeps_durable_watermark_and_only_complete_backfill_advances(tmp_path: Path) -> None:
    from multi_exchange_trader.market_data.closed_bars import ClosedBarScope, ClosedBarSnapshotStore

    path = tmp_path / "bars.sqlite3"
    source = Source([bar(NOW - STEP * index) for index in (3, 2, 1)])
    await collect(MarketDataService({"bybit": source}), path, NOW)
    source.rows = [bar(NOW + STEP), bar(NOW + STEP * 2)]
    with pytest.raises(MarketDataUnavailable) as caught:
        await collect(MarketDataService({"bybit": source}), path, NOW + STEP * 3)
    assert caught.value.reason == "closed_bar_gap"
    store = ClosedBarSnapshotStore(path)
    scope = ClosedBarScope("BTCUSDT", "15m")
    assert store.watermark(scope) == NOW
    with pytest.raises(ValueError):
        store.latest(scope)
    source.rows = [bar(NOW - STEP * index) for index in (3, 2, 1)]
    with pytest.raises(MarketDataUnavailable) as replay:
        await collect(MarketDataService({"bybit": source}), path, NOW + STEP * 3)
    assert replay.value.reason == "closed_bar_gap"
    with pytest.raises(ValueError):
        store.latest(scope)
    source.rows = [bar(NOW + STEP * index) for index in (0, 1, 2)]
    recovered = await collect(MarketDataService({"bybit": source}), path, NOW + STEP * 3)
    assert recovered.watermark == NOW + STEP * 3 and recovered.is_new_input is True
    assert ClosedBarSnapshotStore(path).latest(scope).snapshot_id == recovered.snapshot_id


async def test_prior_closed_bar_revision_is_rejected_after_restart(tmp_path: Path) -> None:
    from multi_exchange_trader.market_data.closed_bars import ClosedBarScope, ClosedBarSnapshotStore

    path = tmp_path / "bars.sqlite3"
    source = Source([bar(NOW - STEP * index) for index in (3, 2, 1)])
    first = await collect(MarketDataService({"bybit": source}), path, NOW)
    source.rows[0] = bar(NOW - STEP * 3, 99)
    with pytest.raises(MarketDataUnavailable) as caught:
        await collect(MarketDataService({"bybit": source}), path, NOW + timedelta(minutes=1))
    assert caught.value.reason == "closed_bar_conflict"
    assert ClosedBarSnapshotStore(path).latest(ClosedBarScope("BTCUSDT", "15m")).snapshot_id == first.snapshot_id


@pytest.mark.parametrize("rows,reason", [
    ([bar(NOW - STEP, closed=False)], "closed_bar_unclosed"),
    ([bar(NOW)], "closed_bar_future"),
    ([bar(NOW - STEP + timedelta(seconds=1))], "closed_bar_alignment"),
    ([bar(NOW - STEP), bar(NOW - STEP)], "closed_bar_duplicate"),
    ([bar(NOW - STEP * 3), bar(NOW - STEP)], "closed_bar_gap"),
])
async def test_invalid_input_never_produces_a_snapshot(tmp_path: Path, rows: list[Candle], reason: str) -> None:
    source = Source(rows)
    with pytest.raises(MarketDataUnavailable) as caught:
        await collect(MarketDataService({"bybit": source}), tmp_path / "bars.sqlite3", NOW)
    assert caught.value.reason == reason


async def test_regressed_window_cannot_replace_a_later_watermark(tmp_path: Path) -> None:
    path = tmp_path / "bars.sqlite3"
    source = Source([bar(NOW - STEP * index) for index in (3, 2, 1)])
    await collect(MarketDataService({"bybit": source}), path, NOW)
    source.rows = [bar(NOW - STEP * index) for index in (4, 3, 2)]
    with pytest.raises(MarketDataUnavailable) as caught:
        await collect(MarketDataService({"bybit": source}), path, NOW + timedelta(minutes=1))
    assert caught.value.reason == "closed_bar_regression"


async def test_symbol_timeframe_and_product_are_not_interchangeable(tmp_path: Path) -> None:
    path = tmp_path / "bars.sqlite3"
    source = Source([bar(NOW - STEP * index) for index in (3, 2, 1)])
    market = MarketDataService({"bybit": source})
    btc = await collect(market, path, NOW)
    eth = await collect(market, path, NOW, symbol="ETHUSDT")
    source.rows = [bar(NOW - timedelta(minutes=5) * index) for index in (3, 2, 1)]
    five = await collect(market, path, NOW, interval="5m")
    assert len({btc.snapshot_id, eth.snapshot_id, five.snapshot_id}) == 3
    before = source.calls
    with pytest.raises(MarketDataUnavailable):
        await market.get_closed_snapshot("bybit", "BTCUSDT", "spot", "15m", 80,
            path=path, clock=lambda: NOW)
    assert source.calls == before


async def test_real_recommendation_consumer_persists_input_and_refuses_revised_bar(tmp_path: Path) -> None:
    import asyncio

    from multi_exchange_trader.paper.store import PaperStore
    from multi_exchange_trader.recommendations.book import RecommendationBook
    from multi_exchange_trader.recommendations.service import RecommendationService, Refusal

    source = Source([bar(NOW - STEP * (30 - index), 100 + index) for index in range(30)])
    paper = PaperStore(tmp_path / "paper.sqlite3")
    service = RecommendationService(RecommendationBook(tmp_path / "recommendations.sqlite3"),
        paper, MarketDataService({"bybit": source}), asyncio.Lock(), clock=lambda: NOW)
    plan = await service._plan("bybit", "BTCUSDT", "perpetual")
    assert plan.fresh is True and plan.side == "long"
    assert (tmp_path / "recommendations-market.sqlite3").is_file()
    source.rows[0] = bar(source.rows[0].timestamp, 99)
    with pytest.raises(Refusal) as caught:
        await service._plan("bybit", "BTCUSDT", "perpetual")
    assert caught.value.status_code == 409 and paper.scenarios() == []


@pytest.mark.parametrize("first_count,second_count", [(79, 80), (80, 79)])
async def test_same_watermark_allows_identical_overlap_with_a_different_closed_window(
    tmp_path: Path, first_count: int, second_count: int,
) -> None:
    from multi_exchange_trader.market_data.closed_bars import ClosedBarScope, ClosedBarSnapshotStore

    def window(count: int) -> list[Candle]:
        closed = [bar(NOW - STEP * index) for index in range(count, 0, -1)]
        return closed + ([bar(NOW, closed=False)] if count == 79 else [])

    path = tmp_path / "bars.sqlite3"
    source = Source(window(first_count))
    first = await collect(MarketDataService({"bybit": source}), path, NOW)
    source.rows = window(second_count)
    second = await collect(MarketDataService({"bybit": source}), path, NOW + timedelta(minutes=2))
    assert len(second.candles) == second_count and second.snapshot_id != first.snapshot_id
    assert second.watermark == first.watermark == NOW
    assert second.collected_at == first.collected_at == NOW and second.cutoff == first.cutoff
    assert second.is_new_input is False
    assert second.financial_authority is False and second.trading_enabled is False
    source.rows = window(first_count)
    replay = await collect(MarketDataService({"bybit": source}), path, NOW + timedelta(minutes=3))
    assert replay.snapshot_id == first.snapshot_id and replay.candles == first.candles
    assert replay.collected_at == first.collected_at and replay.is_new_input is False
    store = ClosedBarSnapshotStore(path)
    assert store.watermark(ClosedBarScope("BTCUSDT", "15m")) == NOW
    assert store.latest(ClosedBarScope("BTCUSDT", "15m")).collected_at == NOW
    source.rows = window(second_count)
    variant = await collect(MarketDataService({"bybit": source}), path, NOW + timedelta(minutes=4))
    assert variant.snapshot_id == second.snapshot_id and variant.candles == second.candles
    assert variant.collected_at == first.collected_at and variant.cutoff == first.cutoff
    assert variant.is_new_input is False


async def test_added_history_prefix_stays_immutable_after_same_watermark_capture(tmp_path: Path) -> None:
    path = tmp_path / "bars.sqlite3"
    source = Source([bar(NOW - STEP * index) for index in (3, 2, 1)])
    await collect(MarketDataService({"bybit": source}), path, NOW)
    source.rows.insert(0, bar(NOW - STEP * 4))
    expanded = await collect(MarketDataService({"bybit": source}), path, NOW + timedelta(minutes=1))
    assert len(expanded.candles) == 4 and expanded.is_new_input is False
    source.rows[0] = bar(NOW - STEP * 4, 99)
    with pytest.raises(MarketDataUnavailable) as caught:
        await collect(MarketDataService({"bybit": source}), path, NOW + timedelta(minutes=2))
    assert caught.value.reason == "closed_bar_conflict"


def source_ref(stamp: datetime, *, source_generation: str = "a03-fixture-v1",
               body_hash: str = "a" * 64, limit: int = 3):
    from multi_exchange_trader.market_data.bybit_public_contracts import CANDLE_URL
    from multi_exchange_trader.market_data.public_observations_v1 import make_source_ref_v1

    query = tuple(sorted((("category", "linear"), ("interval", "15"),
        ("limit", str(limit)), ("symbol", "BTCUSDT"))))
    return make_source_ref_v1(source_generation=source_generation, endpoint=CANDLE_URL, query=query,
        request_started_at=stamp - timedelta(milliseconds=10), received_at=stamp,
        server_observed_at=stamp, raw_body_sha256=body_hash)


def capture_source_bound(store, values: list[Candle], stamp: datetime, *, body_hash: str = "a" * 64,
                         source_generation: str = "a03-fixture-v1", catalog_revision: str = "c" * 64,
                         cutoff: datetime | None = None):
    from multi_exchange_trader.market_data.closed_bars import ClosedBarScope

    observed = cutoff or stamp
    return store.capture(ClosedBarScope("BTCUSDT", "15m"), values, collected_at=stamp,
        cutoff=observed, source=source_ref(stamp, source_generation=source_generation, body_hash=body_hash,
            limit=len(values)), catalog_revision=catalog_revision, instrument_revision="d" * 64,
        valid_until=stamp + timedelta(seconds=30))


async def test_source_bound_same_watermark_updates_durable_latest_after_restart(tmp_path: Path) -> None:
    from multi_exchange_trader.market_data.closed_bars import ClosedBarScope, ClosedBarSnapshotStore

    path = tmp_path / "source-bars.sqlite3"
    scope = ClosedBarScope("BTCUSDT", "15m")
    values = [bar(NOW - STEP * index) for index in (3, 2, 1)]
    first = capture_source_bound(ClosedBarSnapshotStore(path), values, NOW)
    assert first.is_new_input is True
    second_time = NOW + timedelta(seconds=1)
    second = capture_source_bound(ClosedBarSnapshotStore(path), values, second_time,
        body_hash="b" * 64, source_generation="a03-fixture-v2")
    assert second.is_new_input is False and second.watermark == first.watermark
    assert second.snapshot_id != first.snapshot_id

    restarted = ClosedBarSnapshotStore(path)
    latest = restarted.latest_source_v1(scope, catalog_revision="c" * 64,
        now=NOW + timedelta(seconds=2), max_bar_age=timedelta(hours=1))
    assert latest.snapshot_id == second.snapshot_id
    assert latest.source is not None and latest.source.raw_body_sha256 == "b" * 64
    assert latest.source.source_generation == "a03-fixture-v2"
    with pytest.raises(ValueError):
        restarted.latest_source_v1(scope, catalog_revision="e" * 64,
            now=NOW + timedelta(seconds=2), max_bar_age=timedelta(hours=1))
    with pytest.raises(ValueError):
        restarted.latest_source_v1(scope, catalog_revision="c" * 64,
            now=NOW + timedelta(seconds=31), max_bar_age=timedelta(hours=1))


async def test_source_less_legacy_rows_cannot_be_promoted_by_signal_consumer_or_overwrite(tmp_path: Path) -> None:
    from multi_exchange_trader.market_data.closed_bars import (
        ClosedBarRejected,
        ClosedBarScope,
        ClosedBarSnapshotStore,
    )

    path = tmp_path / "legacy-bars.sqlite3"
    scope = ClosedBarScope("BTCUSDT", "15m")
    values = [bar(NOW - STEP * index) for index in (3, 2, 1)]
    legacy = ClosedBarSnapshotStore(path)
    legacy.capture(scope, values, collected_at=NOW, cutoff=NOW)
    with pytest.raises(ClosedBarRejected):
        legacy.latest_source_v1(scope, catalog_revision="c" * 64,
            now=NOW + timedelta(seconds=1), max_bar_age=timedelta(hours=1))

    source_bound = capture_source_bound(legacy, values, NOW + timedelta(seconds=1))
    assert source_bound.source is not None
    with pytest.raises(ClosedBarRejected) as caught:
        legacy.capture(scope, values, collected_at=NOW + timedelta(seconds=2),
            cutoff=NOW + timedelta(seconds=2))
    assert caught.value.reason == "closed_bar_source_required"


async def test_source_bound_cutoff_future_and_gap_replay_reject_after_restart(tmp_path: Path) -> None:
    from multi_exchange_trader.market_data.closed_bars import (
        ClosedBarRejected,
        ClosedBarScope,
        ClosedBarSnapshotStore,
    )

    path = tmp_path / "gap-bars.sqlite3"
    scope = ClosedBarScope("BTCUSDT", "15m")
    rows = [bar(NOW - STEP * index) for index in (3, 2, 1)]
    store = ClosedBarSnapshotStore(path)
    first = capture_source_bound(store, rows, NOW)
    with pytest.raises(ClosedBarRejected) as cutoff_error:
        capture_source_bound(store, rows, NOW + timedelta(seconds=1), cutoff=NOW + timedelta(seconds=2))
    assert cutoff_error.value.reason == "closed_bar_source"
    future = [bar(NOW + STEP)]
    with pytest.raises(ClosedBarRejected) as future_error:
        capture_source_bound(ClosedBarSnapshotStore(tmp_path / "future.sqlite3"), future, NOW)
    assert future_error.value.reason == "closed_bar_future"

    gap_rows = [bar(NOW + STEP), bar(NOW + STEP * 2)]
    with pytest.raises(ClosedBarRejected) as gap_error:
        capture_source_bound(store, gap_rows, NOW + STEP * 3)
    assert gap_error.value.reason == "closed_bar_gap"
    restarted = ClosedBarSnapshotStore(path)
    assert restarted.watermark(scope) == first.watermark
    with pytest.raises(ClosedBarRejected):
        restarted.latest_source_v1(scope, catalog_revision="c" * 64,
            now=NOW + STEP * 3, max_bar_age=timedelta(hours=1))
    with pytest.raises(ClosedBarRejected) as replay:
        capture_source_bound(restarted, rows, NOW + STEP * 3)
    assert replay.value.reason == "closed_bar_gap"
