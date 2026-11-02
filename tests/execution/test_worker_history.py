"""Explicit history ingestion uses the worker's owner loop and existing stop fence."""

import asyncio
import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from threading import get_ident
from typing import Any

import pytest
from test_risk import NOW
from test_runtime import SCOPE
from test_worker import fill, harness, harness_existing, stage

from multi_exchange_trader.execution import worker as worker_module
from multi_exchange_trader.execution.history import (
    HistoryError,
    HistorySnapshot,
    HistoryWindow,
    TransactionRecord,
)
from multi_exchange_trader.execution.history_store import HistoryStore
from multi_exchange_trader.execution.ownership import AccountWorkerLock
from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.execution.runtime import RuntimeBlocked

WINDOW = HistoryWindow(1000, 10000)


def observation(**changes: Any) -> HistorySnapshot:
    transfer = TransactionRecord("transfer-1", "", "", "", "", "USDT", "TRANSFER_IN", 2000,
        Decimal(10), Decimal(0), Decimal(0), Decimal(10), Decimal(100))
    return replace(HistorySnapshot(SCOPE, "BTCUSDT", "worker-history", WINDOW, NOW, NOW,
                                  (), (), (transfer,), True), **changes)


def reader(monkeypatch: pytest.MonkeyPatch, action: Any) -> None:
    class Reader:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def read_history(self, symbol: str, *, start_ms: int, end_ms: int) -> HistorySnapshot:
            assert (symbol, start_ms, end_ms) == ("BTCUSDT", WINDOW.start_ms, WINDOW.end_ms)
            return await action()

    monkeypatch.setattr(worker_module, "BybitHistoryReader", Reader)


async def collect(worker: Any) -> Any:
    return await worker.collect_history("BTCUSDT", start_ms=WINDOW.start_ms, end_ms=WINDOW.end_ms)


def coverage(worker: Any) -> Any:
    return worker._history_store.coverage("BTCUSDT", start_ms=1000, end_ms=10000)


async def test_collect_history_persists_on_owner_loop_and_reopens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = harness(tmp_path)
    assert not (tmp_path / "history.sqlite3").exists()
    await h.worker.start_passive()
    owner = get_ident()
    calls: list[int] = []
    original = h.worker._history_store.ingest

    def ingest(snapshot: HistorySnapshot) -> Any:
        calls.append(get_ident())
        assert get_ident() == owner
        return original(snapshot)

    monkeypatch.setattr(h.worker._history_store, "ingest", ingest)

    async def success() -> HistorySnapshot:
        return observation()

    reader(monkeypatch, success)
    result = await h.worker.read_history("BTCUSDT", start_ms=1000, end_ms=10000)
    assert result == observation() and not coverage(h.worker).query_complete
    receipt = await collect(h.worker)
    assert not receipt.duplicate and receipt.new_variant_count == 1 and calls == [owner]
    assert (await collect(h.worker)).duplicate
    assert h.worker.runtime.status.state == "BLOCKED"
    assert h.book.read()["desired_state"] == "STOPPED"
    assert not h.assembly.strategy.commands and not h.assembly.bridge._submissions
    await h.worker.stop()
    restarted = harness_existing(tmp_path, h)
    await restarted.start_passive()
    try:
        view = restarted._history_store.load_window("BTCUSDT", start_ms=1000, end_ms=10000)
        assert view.coverage.query_complete and len(view.variants) == 1
        assert view.variants[0].record == observation().transactions[0]
        assert view.coverage.oldest_observed_at == NOW
        assert view.coverage.oldest_server_observed_at == NOW
        assert not view.coverage.venue_event_complete and not view.coverage.flat_verified
        assert restarted.runtime.status.state == "BLOCKED"
        assert h.book.read()["desired_state"] == "STOPPED"
        assert not restarted.runtime.bridge._submissions
    finally:
        await restarted.stop()


@pytest.mark.parametrize("change", ["scope", "symbol", "window", "binding", "lease", "epoch"])
async def test_collect_history_rejects_before_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    h = harness(tmp_path)
    await h.worker.start_passive()

    async def forbidden() -> HistorySnapshot:
        pytest.fail("invalid collection must not request history")

    reader(monkeypatch, forbidden)
    if change == "scope":
        h.worker.scope = ProtectionScope("other", "DEMO")
    if change == "binding":
        def stale(*_args: Any, **_kwargs: Any) -> None:
            raise ValueError("ACCOUNT_BINDING_STALE")
        h.session.require_binding = stale
    if change in {"lease", "epoch"}:
        h.worker._lock.release()
        if change == "epoch":
            h.worker._lock.acquire()
    try:
        with pytest.raises((RuntimeBlocked, ValueError)):
            await h.worker.collect_history("ETHUSDT" if change == "symbol" else "BTCUSDT",
                start_ms=0 if change == "window" else 1000, end_ms=10000)
        assert not coverage(h.worker).query_complete
    finally:
        h.worker._close_stores()


@pytest.mark.parametrize("change", ["binding", "binding_identity", "session", "scope", "symbols",
                                   "lease", "epoch", "runtime", "result_scope", "result_symbol",
                                   "result_window"])
async def test_collect_history_rejects_changes_after_await(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    h = harness(tmp_path)
    await h.worker.start_passive()
    started, release = asyncio.Event(), asyncio.Event()
    result = observation()

    async def slow() -> HistorySnapshot:
        started.set()
        await release.wait()
        return result

    reader(monkeypatch, slow)
    task = asyncio.create_task(collect(h.worker))
    await asyncio.wait_for(started.wait(), 1)
    if change == "binding":
        def stale(*_args: Any, **_kwargs: Any) -> None:
            raise ValueError("ACCOUNT_BINDING_STALE")
        h.session.require_binding = stale
    elif change == "binding_identity":
        h.worker.binding = object()
    elif change == "session":
        h.worker.session = object()
    elif change == "scope":
        h.worker.scope = ProtectionScope("other", "DEMO")
    elif change == "symbols":
        h.worker.symbols = ("ETHUSDT",)
    elif change in {"lease", "epoch"}:
        h.worker._lock.release()
        if change == "epoch":
            h.worker._lock.acquire()
    elif change == "runtime":
        h.worker._runtime = object()
    elif change == "result_scope":
        result = observation(scope=ProtectionScope("other", "DEMO"))
    elif change == "result_symbol":
        result = observation(symbol="ETHUSDT")
    elif change == "result_window":
        result = observation(window=HistoryWindow(1000, 9999))
    accesses: list[str] = []
    h.worker._history_store._db.set_trace_callback(accesses.append)
    release.set()
    try:
        with pytest.raises((RuntimeBlocked, ValueError)):
            await task
        assert accesses == []
        assert h.worker._operations == 0
        assert not coverage(h.worker).query_complete
    finally:
        h.worker._close_stores()


async def test_stop_during_history_await_never_accesses_history_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = harness(tmp_path)
    await h.worker.start_passive()
    started, release = asyncio.Event(), asyncio.Event()

    async def slow() -> HistorySnapshot:
        started.set()
        await release.wait()
        return observation()

    reader(monkeypatch, slow)
    task = asyncio.create_task(collect(h.worker))
    await asyncio.wait_for(started.wait(), 1)
    accesses: list[str] = []
    h.worker._history_store._db.set_trace_callback(accesses.append)
    with pytest.raises(RuntimeBlocked, match="AWAIT_INFLIGHT_OPERATION_BEFORE_STOP"):
        await h.worker.stop()
    assert h.worker._stopping and h.worker._lock.acquired
    with pytest.raises(RuntimeBlocked, match="WORKER_STOPPING"):
        await collect(h.worker)
    release.set()
    with pytest.raises(RuntimeBlocked, match="WORKER_STOPPING"):
        await task
    assert accesses == [] and h.worker._operations == 0
    assert not coverage(h.worker).query_complete
    await h.worker.stop()
    assert not h.worker._lock.acquired
    with HistoryStore(tmp_path / "history.sqlite3", SCOPE) as store:
        assert not store.coverage("BTCUSDT", start_ms=1000, end_ms=10000).query_complete


@pytest.mark.parametrize("cancel", [False, True])
async def test_history_failure_and_cancellation_do_not_advance_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool,
) -> None:
    h = harness(tmp_path)
    await h.worker.start_passive()
    started, release = asyncio.Event(), asyncio.Event()

    async def failed() -> HistorySnapshot:
        started.set()
        await release.wait()
        raise HistoryError("HISTORY_PAGE_INCOMPLETE")

    reader(monkeypatch, failed)
    task = asyncio.create_task(collect(h.worker))
    await asyncio.wait_for(started.wait(), 1)
    if cancel:
        task.cancel()
    else:
        release.set()
    with pytest.raises(asyncio.CancelledError if cancel else HistoryError):
        await task
    assert h.worker._operations == 0 and not coverage(h.worker).query_complete
    assert not coverage(h.worker).provenance
    await h.worker.stop()


async def test_native_fill_progresses_during_slow_history_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = harness(tmp_path)
    await h.worker.start_passive()
    record = await stage(h)
    h.worker.runtime.journal.claim("entry")
    started, release = asyncio.Event(), asyncio.Event()

    async def slow() -> HistorySnapshot:
        started.set()
        await release.wait()
        return observation()

    reader(monkeypatch, slow)
    task = asyncio.create_task(collect(h.worker))
    await asyncio.wait_for(started.wait(), 1)
    h.worker.post_native(h.assembly.channel, fill(record.client_order_id))
    await asyncio.sleep(0)
    assert h.worker.runtime.projection("position-1").owned_quantity > 0
    assert h.worker.callback_failures == 0 and not task.done()
    release.set()
    await task
    await h.worker.stop()


@pytest.mark.parametrize("wrong_scope", [False, True])
async def test_history_open_failure_releases_all_worker_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wrong_scope: bool,
) -> None:
    locks = tmp_path / "locks"
    locks.mkdir()
    h = harness(tmp_path, lock_directory=locks)
    if wrong_scope:
        with HistoryStore(tmp_path / "history.sqlite3", ProtectionScope("other", "DEMO")):
            pass
    else:
        def failed(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("HISTORY_OPEN_FAILURE")
        monkeypatch.setattr(worker_module, "HistoryStore", failed)
    with pytest.raises((RuntimeError, HistoryError)):
        await h.worker.start_passive()
    assert h.worker._stopped and not h.worker._lock.acquired
    for store in (h.worker._store, h.worker._journal):
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            store._db.execute("SELECT 1")
    with AccountWorkerLock(locks, account_id=SCOPE.account_id, environment=SCOPE.environment):
        pass


async def test_collect_history_rejects_foreign_owner_loop_before_database_access(
    tmp_path: Path,
) -> None:
    h = harness(tmp_path)
    await h.worker.start_passive()
    accesses: list[str] = []
    h.worker._history_store._db.set_trace_callback(accesses.append)
    try:
        with pytest.raises(RuntimeBlocked, match="WORKER_OWNER_LOOP_REQUIRED"):
            await asyncio.to_thread(lambda: asyncio.run(collect(h.worker)))
        assert accesses == [] and h.worker._operations == 0
    finally:
        await h.worker.stop()


async def test_startup_failure_closes_history_and_releases_lease_even_if_close_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    locks = tmp_path / "locks"
    locks.mkdir()
    h = harness(tmp_path, lock_directory=locks)
    closed: list[str] = []
    for cls in (worker_module.HistoryStore, worker_module.RuntimeStateStore,
                worker_module.CommandJournal):
        original = cls.close

        def close(self: Any, original: Any = original, name: str = cls.__name__) -> None:
            original(self)
            closed.append(name)
            if name == "HistoryStore":
                raise RuntimeError("HISTORY_CLOSE_FAILED")

        monkeypatch.setattr(cls, "close", close)

    def failed_runtime(**_kwargs: Any) -> None:
        raise RuntimeError("RUNTIME_OPEN_FAILED")

    monkeypatch.setattr(worker_module, "NativeExecutionRuntime", failed_runtime)
    with pytest.raises(RuntimeError, match="HISTORY_CLOSE_FAILED"):
        await h.worker.start_passive()
    assert closed == ["HistoryStore", "RuntimeStateStore", "CommandJournal"]
    assert h.worker._stopped and not h.worker._lock.acquired
    for store in (h.worker._store, h.worker._journal):
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            store._db.execute("SELECT 1")
    with AccountWorkerLock(locks, account_id=SCOPE.account_id, environment=SCOPE.environment):
        pass


@pytest.mark.parametrize("failed_store", ["_history_store", "_store", "_journal"])
async def test_store_close_failure_still_closes_all_stores_and_releases_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_store: str,
) -> None:
    h = harness(tmp_path)
    await h.worker.start_passive()
    calls: list[str] = []
    for name in ("_history_store", "_store", "_journal"):
        store = getattr(h.worker, name)
        original = store.close

        def close(name: str = name, original: Any = original) -> None:
            calls.append(name)
            original()
            if name == failed_store:
                raise RuntimeError("CLOSE_FAILED")

        monkeypatch.setattr(store, "close", close)
    with pytest.raises(RuntimeError, match="CLOSE_FAILED"):
        await h.worker.stop()
    assert set(calls) == {"_history_store", "_store", "_journal"}
    assert h.worker._stopped and not h.worker._lock.acquired
