import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

import pytest

from multi_exchange_trader.execution.history import (
    MAX_WINDOW_MS,
    ExecutionRecord,
    HistoryError,
    HistorySnapshot,
    HistoryWindow,
    OrderHistoryRecord,
    TransactionRecord,
)
from multi_exchange_trader.execution.history_coverage import HistoryCoverage
from multi_exchange_trader.execution.history_store import HistoryStore
from multi_exchange_trader.execution.protection import ProtectionScope

SCOPE = ProtectionScope("12345", "DEMO")
NOW = datetime(2026, 11, 2, tzinfo=UTC)
D = Decimal


def snapshot(name: str = "s1", start: int = 1000, end: int = 3000,
             **changes: Any) -> HistorySnapshot:
    return replace(HistorySnapshot(SCOPE, "BTCUSDT", name, HistoryWindow(start, end),
        NOW, NOW - timedelta(seconds=1), (), (), (), True), **changes)


def execution(**changes: Any) -> ExecutionRecord:
    return replace(ExecutionRecord("e1", "o1", "c1", "BTCUSDT", "Buy", "Trade", 2000,
        D("0.4"), D("100"), D("40"), D("0.01"), "USDT", D("0.001"), D(0)), **changes)


def order(**changes: Any) -> OrderHistoryRecord:
    return replace(OrderHistoryRecord("o1", "c1", "BTCUSDT", "Buy", 0, "PartiallyFilled",
        D(1), D("0.4"), D("0.6"), False, 1000, 2000), **changes)


def transaction(**changes: Any) -> TransactionRecord:
    value = D("12345678901234567890.123456789012345678")
    return replace(TransactionRecord("t1", "", "", "", "", "USDT", "TRANSFER_IN", 2000,
        value, D(0), D(0), value, D("999")), **changes)


def assert_no_authority(coverage: HistoryCoverage) -> None:
    assert coverage.venue_event_complete is False
    assert coverage.flat_verified is False


def test_inclusive_boundaries_adjacency_and_one_ms_gap(tmp_path: Path) -> None:
    with HistoryStore(tmp_path / "history.sqlite3", SCOPE) as store:
        store.ingest(snapshot("a", 1000, 2000))
        store.ingest(snapshot("b", 2001, 3000))
        complete = store.coverage("BTCUSDT", start_ms=1000, end_ms=3000)
        for stream in complete.streams:
            assert stream.covered == (HistoryWindow(1000, 3000),)
            assert stream.gaps == ()
            assert stream.last_query_end_ms == stream.contiguous_through_ms == 3000
        clipped = store.coverage("BTCUSDT", start_ms=1500, end_ms=2500)
        assert clipped.streams[0].covered == (HistoryWindow(1500, 2500),)
        assert {p.window for p in clipped.provenance} == {
            HistoryWindow(1000, 2000), HistoryWindow(2001, 3000)}
    with HistoryStore(tmp_path / "gap.sqlite3", SCOPE) as store:
        store.ingest(snapshot("a", 1000, 1999))
        store.ingest(snapshot("b", 2001, 3000))
        coverage = store.coverage("BTCUSDT", start_ms=1000, end_ms=3000)
        for stream in coverage.streams:
            assert stream.gaps == (HistoryWindow(2000, 2000),)
            assert stream.covered == (HistoryWindow(1000, 1999), HistoryWindow(2001, 3000))
            assert stream.contiguous_through_ms == 1999
            assert stream.last_query_end_ms == 3000
        assert coverage.query_complete is False
        assert coverage.conflict_free is True
        missing_start = store.coverage("BTCUSDT", start_ms=2000, end_ms=3001)
        assert missing_start.streams[0].contiguous_through_ms is None
        assert missing_start.streams[0].gaps == (
            HistoryWindow(2000, 2000), HistoryWindow(3001, 3001))


def test_empty_complete_queries_do_not_prove_venue_completeness(tmp_path: Path) -> None:
    with HistoryStore(tmp_path / "history.sqlite3", SCOPE) as store:
        empty = store.load_window("BTCUSDT", start_ms=1000, end_ms=3000)
        assert empty.variants == empty.coverage.provenance == empty.coverage.conflicts == ()
        assert empty.coverage.oldest_observed_at is None
        assert empty.coverage.oldest_server_observed_at is None
        assert not empty.coverage.query_complete and empty.coverage.conflict_free
        assert all(s.gaps == (HistoryWindow(1000, 3000),) and s.last_query_end_ms is None
                   and s.contiguous_through_ms is None for s in empty.coverage.streams)
        store.ingest(snapshot())
        view = store.load_window("BTCUSDT", start_ms=1000, end_ms=3000)
        assert view.coverage.query_complete and view.coverage.conflict_free
        assert view.variants == ()
        assert [s.kind for s in view.coverage.streams] == ["EXECUTION", "ORDER", "TRANSACTION"]
        assert_no_authority(empty.coverage)
        assert_no_authority(view.coverage)


def test_transaction_coverage_is_account_wide_but_order_execution_coverage_is_symbol_bound(
    tmp_path: Path,
) -> None:
    with HistoryStore(tmp_path / "history.sqlite3", SCOPE) as store:
        first = store.ingest(snapshot(transactions=(transaction(),)))
        coverage = store.coverage("ETHUSDT", start_ms=1000, end_ms=3000)
        assert not coverage.query_complete
        assert all(s.gaps == (HistoryWindow(1000, 3000),) for s in coverage.streams[:2])
        assert coverage.streams[2].gaps == ()
        second = store.ingest(snapshot("eth", symbol="ETHUSDT", transactions=(transaction(),)))
        view = store.load_window("ETHUSDT", start_ms=1000, end_ms=3000)
        assert view.coverage.query_complete
        assert len(view.variants) == 1
        assert view.variants[0].record == transaction()
        assert view.variants[0].observation_keys == tuple(sorted((
            first.observation_key, second.observation_key)))


def test_late_events_extend_old_window_without_refreshing_provenance(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    with HistoryStore(path, SCOPE) as store:
        first = store.ingest(snapshot())
        second = store.ingest(snapshot("late", executions=(execution(),),
            observed_at=NOW + timedelta(hours=1), server_observed_at=NOW + timedelta(minutes=59),
            transport_injected=False))
        view = store.load_window("BTCUSDT", start_ms=2000, end_ms=2000)
        assert len(view.variants) == 1
        assert view.variants[0].observation_keys == (second.observation_key,)
        assert {p.observation_key for p in view.coverage.provenance} == {
            first.observation_key, second.observation_key}
        assert view.coverage.oldest_observed_at == NOW
        assert view.coverage.oldest_server_observed_at == NOW - timedelta(seconds=1)
        assert {p.transport_injected for p in view.coverage.provenance} == {False, True}
    with localcontext() as ctx:
        ctx.prec = 2
        with HistoryStore(path, SCOPE) as store:
            assert store.load_window("BTCUSDT", start_ms=2000, end_ms=2000) == view
            store.ingest(snapshot("again", executions=(execution(quantity=D("0.4000")),)))
            assert store.load_window("BTCUSDT", start_ms=2000, end_ms=2000).variants[
                0].variant_hash == view.variants[0].variant_hash


def test_new_conflict_taints_previous_and_other_symbol_transaction_views(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    with HistoryStore(path, SCOPE) as store:
        store.ingest(snapshot(transactions=(transaction(),)))
        store.ingest(snapshot("eth", 1500, 2500, symbol="ETHUSDT"))
        clean = store.coverage("ETHUSDT", start_ms=1500, end_ms=2500)
        assert clean.query_complete and clean.conflict_free
        # A corrected timestamp moves the contradictory variant to a disjoint query.
        store.ingest(snapshot("late", 4000, 5000, symbol="SOLUSDT",
            transactions=(transaction(time_ms=4500),)))
        tainted = store.load_window("ETHUSDT", start_ms=1500, end_ms=2500)
        assert tainted.coverage.query_complete and not tainted.coverage.conflict_free
        assert len(tainted.coverage.conflicts) == 1
        assert tainted.coverage.conflicts[0].reason == "EVENT_CONTENT_CHANGED"
        assert len(tainted.variants) == 1
        assert tainted.coverage.provenance == clean.provenance
        assert clean.conflict_free  # Previously returned immutable value stays unchanged.
    with HistoryStore(path, SCOPE) as store:
        assert store.load_window("ETHUSDT", start_ms=1500, end_ms=2500) == tainted


def test_union_view_retains_order_creation_scope_and_every_variant(tmp_path: Path) -> None:
    with HistoryStore(tmp_path / "history.sqlite3", SCOPE) as store:
        store.ingest(snapshot(orders=(order(),), executions=(execution(),)))
        store.ingest(snapshot("changed", orders=(order(status="Cancelled"),),
            executions=(execution(fee=D("0.02")),)))
        view = store.load_window("BTCUSDT", start_ms=1000, end_ms=3000)
        assert len(view.variants) == 4
        assert {v.kind for v in view.variants} == {"EXECUTION", "ORDER"}
        assert not view.coverage.conflict_free and view.coverage.query_complete
        later = store.load_window("BTCUSDT", start_ms=2000, end_ms=3000)
        assert len(later.variants) == 2
        assert all(v.kind == "EXECUTION" for v in later.variants)
        assert_no_authority(later.coverage)


def test_order_conflict_taints_earlier_nonconflicting_update(tmp_path: Path) -> None:
    with HistoryStore(tmp_path / "history.sqlite3", SCOPE) as store:
        first = store.ingest(snapshot(orders=(order(),)))
        store.ingest(snapshot("later", orders=(order(updated_ms=3000,
            cumulative_filled=D("0.6"), leaves_quantity=D("0.4")),)))
        store.ingest(snapshot("regressed", orders=(order(updated_ms=4000,
            cumulative_filled=D("0.5"), leaves_quantity=D("0.5")),)))
        coverage = store.coverage("BTCUSDT", start_ms=1000, end_ms=1000)
        assert not coverage.conflict_free
        assert len(coverage.conflicts) == 1
        conflict = coverage.conflicts[0]
        assert first.observation_key not in (
            conflict.left_observation_key, conflict.right_observation_key)


def test_reused_snapshot_id_taints_disjoint_original_window(tmp_path: Path) -> None:
    with HistoryStore(tmp_path / "history.sqlite3", SCOPE) as store:
        store.ingest(snapshot())
        store.ingest(snapshot(start=4000, end=5000))
        coverage = store.coverage("BTCUSDT", start_ms=1000, end_ms=3000)
        assert coverage.query_complete and not coverage.conflict_free
        assert coverage.conflicts[0].kind == "SNAPSHOT"
        assert len(coverage.provenance) == 1


@pytest.mark.parametrize("method", ["coverage", "load_window"])
@pytest.mark.parametrize("start,end", [(0, 1), (2000, 1000), (True, 3000),
                                     (1000, 1001 + MAX_WINDOW_MS)])
def test_query_rejects_invalid_or_unbounded_windows(
    tmp_path: Path, method: str, start: int, end: int,
) -> None:
    with (HistoryStore(tmp_path / "history.sqlite3", SCOPE) as store,
          pytest.raises(HistoryError, match="HISTORY_WINDOW_INVALID")):
        getattr(store, method)("BTCUSDT", start_ms=start, end_ms=end)


@pytest.mark.parametrize("method", ["coverage", "load_window"])
def test_query_validates_symbol_and_store_lifecycle(tmp_path: Path, method: str) -> None:
    store = HistoryStore(tmp_path / "history.sqlite3", SCOPE)
    query = getattr(store, method)
    with pytest.raises(HistoryError, match="HISTORY_SYMBOL_INVALID"):
        query("btc", start_ms=1000, end_ms=3000)
    with (ThreadPoolExecutor(max_workers=1) as pool,
          pytest.raises(HistoryError, match="HISTORY_OWNER_THREAD_REQUIRED")):
        pool.submit(query, "BTCUSDT", start_ms=1000, end_ms=3000).result()
    store.close()
    with pytest.raises(HistoryError, match="HISTORY_STORE_CLOSED"):
        query("BTCUSDT", start_ms=1000, end_ms=3000)


def test_views_filter_symbol_keep_account_economics_and_all_original_memberships(
    tmp_path: Path,
) -> None:
    with HistoryStore(tmp_path / "history.sqlite3", SCOPE) as store:
        first = store.ingest(snapshot("broad", 1000, 5000, executions=(execution(),),
            transactions=(transaction(),)))
        second = store.ingest(snapshot("narrow", 2000, 2000, executions=(execution(),)))
        third = store.ingest(snapshot("eth", symbol="ETHUSDT",
            orders=(order(venue_order_id="eth-o", symbol="ETHUSDT"),),
            executions=(execution(execution_id="eth-e", venue_order_id="eth-o", symbol="ETHUSDT"),),
            transactions=(transaction(transaction_id="funding", symbol="ETHUSDT", category="linear",
                transaction_type="SETTLEMENT", cash_flow=D(0), funding=D("0.25"),
                change=D("0.25")),)))
        with localcontext() as ctx:
            ctx.prec = 2
            view = store.load_window("BTCUSDT", start_ms=2000, end_ms=2000)
        assert len(view.variants) == 3
        assert {v.record_id for v in view.variants} == {"e1", "t1", "funding"}
        fill = next(v for v in view.variants if v.kind == "EXECUTION")
        assert fill.observation_keys == tuple(sorted((
            first.observation_key, second.observation_key)))
        transfer = next(v for v in view.variants if v.record_id == "t1")
        assert transfer.record == transaction()
        assert {p.observation_key for p in view.coverage.provenance} == {
            first.observation_key, second.observation_key, third.observation_key}
        assert {p.window for p in view.coverage.provenance} == {
            HistoryWindow(1000, 5000), HistoryWindow(2000, 2000), HistoryWindow(1000, 3000)}


def test_query_reads_one_consistent_snapshot_during_another_owner_commit(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    with HistoryStore(path, SCOPE) as reader, HistoryStore(path, SCOPE) as writer:
        reader.ingest(snapshot(executions=(execution(),)))
        committed = False

        def append_during_query(statement: str) -> None:
            nonlocal committed
            if statement.startswith("WITH contributing") and not committed:
                writer.ingest(snapshot("changed", executions=(execution(fee=D("0.02")),)))
                committed = True

        reader._db.set_trace_callback(append_during_query)
        before = reader.load_window("BTCUSDT", start_ms=1000, end_ms=3000)
        reader._db.set_trace_callback(None)
        assert committed
        assert before.coverage.conflict_free and len(before.variants) == 1
        assert len(before.coverage.provenance) == 1
        after = reader.load_window("BTCUSDT", start_ms=1000, end_ms=3000)
        assert not after.coverage.conflict_free and len(after.variants) == 2
        assert len(after.coverage.provenance) == 2


def test_query_is_read_only_and_failed_query_leaves_connection_usable(tmp_path: Path) -> None:
    with HistoryStore(tmp_path / "history.sqlite3", SCOPE) as store:
        store.ingest(snapshot(executions=(execution(),)))
        # Enforce read-only through SQLite itself, not a mock of the query API.
        store._db.execute("PRAGMA query_only=ON")
        expected = store.load_window("BTCUSDT", start_ms=1000, end_ms=3000)
        assert store.coverage("BTCUSDT", start_ms=1000, end_ms=3000) == expected.coverage
        store._db.set_authorizer(lambda action, name, _c, _d, _s:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and name == "history_variants"
            else sqlite3.SQLITE_OK)
        with pytest.raises(HistoryError, match="^HISTORY_QUERY_FAILED$"):
            store.load_window("BTCUSDT", start_ms=1000, end_ms=3000)
        assert not store._db.in_transaction
        store._db.set_authorizer(None)
        assert store.load_window("BTCUSDT", start_ms=1000, end_ms=3000) == expected
