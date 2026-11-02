"""Synthetic private-core GET fixtures with real temporary SQLite; no transport proof."""

import asyncio
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_risk import NOW
from test_runtime import SCOPE
from test_worker import fill, fresh_assembly, harness, quarantines, stage
from worker_receipt_support import synthetic_session

from multi_exchange_trader.execution import codec
from multi_exchange_trader.execution.execution_credit import (
    CorroboratedExecutionCredit,
    ExecutionCorroborationConflict,
    candidate_identity,
    execution_guard,
)
from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.execution.runtime import RuntimeBlocked
from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook
from multi_exchange_trader.execution.worker import ExecutionWorker

MS = int(NOW.timestamp()) * 1000


def history_row(client_id: str, **changes: Any) -> dict[str, Any]:
    return {"execId": "fill-1", "orderId": "venue-1", "orderLinkId": client_id,
        "symbol": "BTCUSDT", "side": "Buy", "execType": "Trade", "execTime": str(MS),
        "execQty": "0.4", "execPrice": "100", "execValue": "40", "execFee": "0.01",
        "feeCurrency": "USDT", "feeRate": "0.00025", "closedSize": "0"} | changes


async def prepared(tmp_path: Path, **fill_changes: Any) -> Any:
    locks = tmp_path.parent / (tmp_path.name + "-locks")
    locks.mkdir()
    h = harness(tmp_path, lock_directory=locks)
    await h.worker.start_passive()
    record = await stage(h)
    h.worker.runtime.journal.claim("entry")
    event = fill(record.client_order_id)
    for name, value in fill_changes.items():
        setattr(event, name, value)
    h.worker.post_native(h.assembly.channel, event)
    await asyncio.sleep(0)
    assert len(quarantines(h.worker)) == 1
    assert h.worker.runtime.projection("position-1").owned_quantity == 0
    assert h.worker.callback_failures == 0
    h.core.configured = True
    h.core.rows = [history_row(record.client_order_id)]
    return h


async def corroborate(h: Any, *, start_ms: int = MS - 1000, end_ms: int = MS) -> Any:
    return await h.worker.corroborate_native_fills("BTCUSDT", start_ms=start_ms, end_ms=end_ms)


def credits(h: Any) -> tuple[Any, ...]:
    return tuple(item for item in h.worker.runtime.store.list_position_events("position-1")
        if type(codec.decode(item.payload_json)) is CorroboratedExecutionCredit)


def decisions(h: Any) -> tuple[Any, ...]:
    return tuple(item for item in h.worker.runtime.store.list_position_events("position-1")
        if type(codec.decode(item.payload_json)) is ExecutionCorroborationConflict)


def no_effects(h: Any) -> None:
    assert not h.assembly.strategy.commands
    assert not h.assembly.bridge._submissions
    assert not h.assembly.bridge._cancellations
    assert h.worker.runtime.status.state == "BLOCKED"
    assert h.book.read()["desired_state"] == "STOPPED"


async def test_worker_injected_session_never_credits(tmp_path: Path) -> None:
    calls: list[str] = []
    transport = httpx.MockTransport(lambda request: calls.append(str(request.url)))
    core = synthetic_session(SCOPE, NOW, transport=transport)
    h = harness(tmp_path, core=core)
    await h.worker.start_passive()
    try:
        with pytest.raises(ValueError, match="ACCOUNT_NETWORK_TRANSPORT_REQUIRED"):
            await corroborate(h)
        assert not calls and not core.reads
        assert h.worker._history_store._db.execute("SELECT count(*) FROM history_snapshots").fetchone() == (0,)
        no_effects(h)
    finally:
        await h.worker.stop()


async def test_stop_during_read_prevents_ingest_and_credit(tmp_path: Path) -> None:
    h = await prepared(tmp_path)
    h.core.block = True
    task = asyncio.create_task(corroborate(h))
    await asyncio.wait_for(h.core.entered.wait(), 1)
    accesses: list[str] = []
    h.worker._history_store._db.set_trace_callback(accesses.append)
    with pytest.raises(RuntimeBlocked, match="AWAIT_INFLIGHT_OPERATION_BEFORE_STOP"):
        await h.worker.stop()
    assert h.worker._stopping and h.worker._lock.acquired
    h.core.release.set()
    with pytest.raises(RuntimeBlocked, match="WORKER_STOPPING"):
        await task
    assert accesses == [] and h.worker._operations == 0
    h.worker._history_store._db.set_trace_callback(None)
    assert not credits(h)
    assert not h.session._history_receipts
    await h.worker.stop()


@pytest.mark.parametrize("change", ["binding", "session", "runtime", "history", "pinned_history",
                                   "pinned_session", "lease", "scope", "symbols"])
async def test_lease_reacquire_binding_or_worker_change_rejects_result(
    tmp_path: Path, change: str,
) -> None:
    h = await prepared(tmp_path)
    h.core.block = True
    worker, runtime, history = h.worker, h.worker.runtime, h.worker._history_store
    task = asyncio.create_task(corroborate(h))
    await asyncio.wait_for(h.core.entered.wait(), 1)
    if change == "binding":
        worker.binding = object()
    elif change == "session":
        worker.session = object()
    elif change == "runtime":
        worker._runtime = object()
    elif change == "history":
        worker._history_store = object()
    elif change == "pinned_history":
        runtime._history_store = object()
    elif change == "pinned_session":
        runtime._receipt_session = object()
    elif change == "lease":
        worker._lock.release()
        worker._lock.acquire()
    elif change == "scope":
        worker.scope = ProtectionScope("other", "DEMO")
    elif change == "symbols":
        worker.symbols = ()
    accesses: list[str] = []
    history._db.set_trace_callback(accesses.append)
    h.core.release.set()
    try:
        with pytest.raises((RuntimeBlocked, ValueError)):
            await task
        assert accesses == [] and worker._operations == 0
        assert not h.session._history_receipts
    finally:
        worker.session, worker.binding, worker._runtime = h.session, h.binding, runtime
        worker._history_store, worker.scope, worker.symbols = history, SCOPE, ("BTCUSDT",)
        runtime._receipt_session, runtime._history_store = h.session, history
        history._db.set_trace_callback(None)
        if change == "lease":
            worker._close_stores()
        else:
            await worker.stop()


async def test_cancel_releases_receipt_capacity(tmp_path: Path) -> None:
    h = await prepared(tmp_path)
    h.core.block = True
    task = asyncio.create_task(corroborate(h))
    await asyncio.wait_for(h.core.entered.wait(), 1)
    assert len(h.session._history_receipts) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not h.session._history_receipts and h.worker._operations == 0
    assert not credits(h) and h.worker._lock.acquired
    assert h.worker._history_store._db.execute("SELECT count(*) FROM history_snapshots").fetchone() == (0,)
    await h.worker.stop()


async def test_requery_duplicate_keeps_quantity_and_first_metadata(tmp_path: Path) -> None:
    h = await prepared(tmp_path)
    try:
        journal, audit = h.worker.runtime.journal.list_commands(), h.worker.runtime.journal.audit_events()
        result = await corroborate(h)
        assert [item.status for item in result] == ["CREDITED"]
        candidate = h.worker.runtime.persisted_candidates()[0]
        assert result[0].candidate_id == candidate_identity(candidate)
        first = credits(h)
        assert len(first) == 1
        assert h.worker.runtime.store.get_inbox_event(execution_guard(SCOPE,
            candidate.fill.instrument_id, candidate.fill.execution_id)) == first[0]
        h.core.mono[0] += 1
        assert [item.status for item in await corroborate(h)] == ["DUPLICATE"]
        assert credits(h) == first
        assert h.worker.runtime.projection("position-1").owned_quantity == Decimal("0.4")
        assert len(quarantines(h.worker)) == 1
        assert h.worker.runtime.journal.list_commands() == journal
        assert h.worker.runtime.journal.audit_events() == audit
        assert h.worker._history_store._db.execute("SELECT count(*) FROM history_snapshots").fetchone() == (2,)
        assert not h.session._history_receipts
        no_effects(h)
        with pytest.raises(ValueError, match="ACCOUNT_LINEAR_PERMISSIONS_REQUIRED"):
            await h.worker.connect_monitoring()
    finally:
        await h.worker.stop()


async def test_credited_candidate_checked_for_later_history_conflict(tmp_path: Path) -> None:
    h = await prepared(tmp_path)
    try:
        assert (await corroborate(h))[0].status == "CREDITED"
        first = credits(h)
        h.core.rows[0]["execFee"] = "0.02"
        result = await corroborate(h)
        assert result[0].status == "CONFLICT"
        assert "HISTORY_EXECUTION_CONFLICT" in result[0].reason_codes
        assert credits(h) == first and len(decisions(h)) == 1
        assert h.worker._history_store._db.execute("SELECT count(*) FROM history_conflicts").fetchone() == (1,)
        assert h.worker.runtime.projection("position-1").owned_quantity == Decimal("0.4")
        no_effects(h)
    finally:
        await h.worker.stop()


async def test_history_commit_then_credit_failure_is_retryable(tmp_path: Path, monkeypatch: Any) -> None:
    h = await prepared(tmp_path)
    store = h.worker.runtime.store
    original = store.apply_position_event
    before = store.get_position("position-1")

    def failed(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeBlocked("SYNTHETIC_CREDIT_FAILURE")

    monkeypatch.setattr(store, "apply_position_event", failed)
    try:
        with pytest.raises(RuntimeBlocked, match="SYNTHETIC_CREDIT_FAILURE"):
            await corroborate(h)
        assert store.get_position("position-1") == before and not credits(h)
        assert h.worker.runtime.projection("position-1").owned_quantity == 0
        assert h.worker._history_store._db.execute("SELECT count(*) FROM history_snapshots").fetchone() == (1,)
        assert not h.session._history_receipts
        monkeypatch.setattr(store, "apply_position_event", original)
        assert (await corroborate(h))[0].status == "CREDITED"
        assert len(credits(h)) == 1
        assert h.worker._history_store._db.execute("SELECT count(*) FROM history_snapshots").fetchone() == (2,)
        no_effects(h)
    finally:
        await h.worker.stop()


async def test_history_only_rows_unmatched_not_credited(tmp_path: Path) -> None:
    h = harness(tmp_path)
    await h.worker.start_passive()
    h.core.configured = True
    h.core.rows = [history_row("manual")]
    try:
        assert await corroborate(h) == ()
        assert h.worker.runtime.store.list_positions() == ()
        view = h.worker._history_store.load_window("BTCUSDT", start_ms=MS - 1000, end_ms=MS)
        assert len(view.variants) == 1 and not view.coverage.flat_verified
        no_effects(h)
    finally:
        await h.worker.stop()


async def test_batch_partial_commit_never_claims_atomic_completion(tmp_path: Path, monkeypatch: Any) -> None:
    h = await prepared(tmp_path)
    second = fill(h.core.rows[0]["orderLinkId"])
    second.trade_id = "fill-2"
    h.worker.post_native(h.assembly.channel, second)
    await asyncio.sleep(0)
    h.core.rows.append({**h.core.rows[0], "execId": "fill-2"})
    candidates = sorted(h.worker.runtime.persisted_candidates(), key=candidate_identity)
    original = h.worker.runtime.store.apply_position_event
    calls = 0

    def failed_second(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeBlocked("SYNTHETIC_SECOND_CREDIT_FAILURE")
        return original(*args, **kwargs)

    monkeypatch.setattr(h.worker.runtime.store, "apply_position_event", failed_second)
    try:
        with pytest.raises(RuntimeBlocked, match="SYNTHETIC_SECOND_CREDIT_FAILURE"):
            await corroborate(h)
        assert len(credits(h)) == 1
        assert codec.decode(credits(h)[0].payload_json).candidate_id == candidate_identity(candidates[0])
        assert h.worker.runtime.projection("position-1").owned_quantity == Decimal("0.4")
        assert h.worker._history_store._db.execute("SELECT count(*) FROM history_members WHERE kind='EXECUTION'").fetchone() == (2,)
        monkeypatch.setattr(h.worker.runtime.store, "apply_position_event", original)
        results = await corroborate(h)
        assert [item.candidate_id for item in results] == sorted(candidate_identity(c) for c in candidates)
        assert [item.status for item in results] == ["DUPLICATE", "CREDITED"]
        assert len(credits(h)) == 2
        assert h.worker.runtime.projection("position-1").owned_quantity == Decimal("0.8")
        no_effects(h)
    finally:
        await h.worker.stop()


async def test_changed_history_time_outside_native_window_still_conflicts(tmp_path: Path) -> None:
    h = await prepared(tmp_path)
    try:
        assert (await corroborate(h))[0].status == "CREDITED"
        h.core.rows[0]["execTime"] = str(MS + 5000)
        h.core.wall[0] += timedelta(seconds=6)
        h.core.mono[0] += 6
        result = await corroborate(h, start_ms=MS + 4000, end_ms=MS + 6000)
        assert result[0].status == "CONFLICT"
        assert "TS_EVENT_NS_MISMATCH" in result[0].reason_codes
        assert h.worker.runtime.projection("position-1").owned_quantity == Decimal("0.4")
    finally:
        await h.worker.stop()


async def test_native_one_ns_after_end_ms_selected_then_rejected(tmp_path: Path) -> None:
    h = await prepared(tmp_path, ts_event=MS * 1_000_000 + 1)
    try:
        row = h.core.rows.pop()
        missing = await corroborate(h)
        assert missing[0].status == "UNRESOLVED"
        assert missing[0].reason_codes == ("HISTORY_EXECUTION_REQUIRED",)
        h.core.rows = [row]
        result = await corroborate(h)
        assert result[0].status == "CONFLICT"
        assert result[0].reason_codes == ("TS_EVENT_NS_MISMATCH",)
        assert not credits(h)
    finally:
        await h.worker.stop()


async def test_unrelated_past_candidate_is_not_selected(tmp_path: Path) -> None:
    h = await prepared(tmp_path)
    h.core.rows = []
    h.core.wall[0] += timedelta(seconds=2)
    h.core.mono[0] += 2
    try:
        assert await corroborate(h, start_ms=MS + 1000, end_ms=MS + 2000) == ()
        assert not credits(h) and len(quarantines(h.worker)) == 1
        no_effects(h)
    finally:
        await h.worker.stop()


@pytest.mark.parametrize("change", ["stop", "binding", "session", "lease", "history", "expired"])
async def test_each_candidate_rechecks_owner_and_receipt_before_commit(
    tmp_path: Path, monkeypatch: Any, change: str,
) -> None:
    h = await prepared(tmp_path)
    second = fill(h.core.rows[0]["orderLinkId"])
    second.trade_id = "fill-2"
    h.worker.post_native(h.assembly.channel, second)
    await asyncio.sleep(0)
    h.core.rows.append({**h.core.rows[0], "execId": "fill-2"})
    runtime, history = h.worker.runtime, h.worker._history_store
    original = runtime.corroborate_execution

    def first_then_change(**kwargs: Any) -> Any:
        result = original(**kwargs)
        if change == "stop":
            h.worker._stopping = True
        elif change == "binding":
            h.worker.binding = object()
        elif change == "session":
            h.worker.session = object()
        elif change == "lease":
            h.worker._lock.release()
            h.worker._lock.acquire()
        elif change == "history":
            h.worker._history_store = object()
        elif change == "expired":
            h.core.mono[0] += 31
        return result

    monkeypatch.setattr(runtime, "corroborate_execution", first_then_change)
    try:
        with pytest.raises((RuntimeBlocked, ValueError)):
            await corroborate(h)
        # The first candidate committed; stale continuation cannot commit the second.
        rows = runtime.store.list_position_events("position-1")
        assert sum(type(codec.decode(item.payload_json)) is CorroboratedExecutionCredit
                   for item in rows) == 1
        assert not h.session._history_receipts and h.worker._operations == 0
        assert history._db.execute("SELECT count(*) FROM history_members WHERE kind='EXECUTION'").fetchone() == (2,)
    finally:
        h.worker.session, h.worker.binding, h.worker._history_store = h.session, h.binding, history
        if change == "lease":
            h.worker._close_stores()
        else:
            await h.worker.stop()


async def test_unconfigured_read_fails_closed_without_ingestion(tmp_path: Path) -> None:
    h = harness(tmp_path)
    await h.worker.start_passive()
    try:
        with pytest.raises(ValueError, match="AUTHENTICATED_HISTORY_READ_FAILED"):
            await corroborate(h)
        assert h.core.reads == ["/v5/execution/list"]
        assert not h.session._history_receipts
        assert h.worker._history_store._db.execute("SELECT count(*) FROM history_snapshots").fetchone() == (0,)
        no_effects(h)
    finally:
        await h.worker.stop()


def initialize_base_stores(root: Path) -> None:
    from multi_exchange_trader.ai.budget import BudgetBook
    from multi_exchange_trader.paper.order_preview import OrderReviews
    from multi_exchange_trader.paper.spot_store import SpotPaperStore
    from multi_exchange_trader.paper.store import PaperStore
    from multi_exchange_trader.recommendations.book import RecommendationBook
    from multi_exchange_trader.research.storage import ResearchStore

    paper = PaperStore(root / "paper.sqlite3")
    spot = SpotPaperStore(root / "spot-paper.sqlite3")
    OrderReviews(root / "order-reviews.sqlite3", paper, spot=spot)
    BudgetBook(root / "ai.sqlite3")
    RecommendationBook(root / "recommendations.sqlite3")
    ResearchStore(root / "research.sqlite3", root / "datasets")
    with closing(sqlite3.connect(root / "ai-dispatch.sqlite3")) as db:
        db.execute("PRAGMA user_version=1")
        db.execute("CREATE TABLE ai_dispatches(request_id TEXT PRIMARY KEY,binding_hash TEXT,"
                   "maximum TEXT,status TEXT,failure TEXT,exact_cost TEXT,created_at TEXT,"
                   "updated_at TEXT)")
        db.commit()


def snapshot_command(*args: object) -> None:
    tool = Path(__file__).resolve().parents[2] / "scripts/operations/state_snapshot.py"
    result = subprocess.run([sys.executable, str(tool), *map(str, args)],
        text=True, capture_output=True, timeout=35, check=False)
    assert result.returncode == 0, result.stderr


async def test_backup_restore_credit_next_day_keeps_original_times_and_blockers(tmp_path: Path) -> None:
    root, backup, restored = (tmp_path / name for name in ("state", "backup", "restored"))
    root.mkdir()
    initialize_base_stores(root)
    h = await prepared(root)
    runtime = h.worker.runtime
    assert runtime.receipt_session is h.session and runtime.history_store is h.worker._history_store
    try:
        assert (await corroborate(h))[0].status == "CREDITED"
        first_credit = credits(h)
        credit = codec.decode(first_credit[0].payload_json)
        assert credit.source.observed_at == credit.source.server_observed_at == NOW
        h.core.rows[0]["execFee"] = "0.02"
        assert (await corroborate(h))[0].status == "CONFLICT"
        first_decisions = decisions(h)
        assert first_decisions and codec.decode(first_decisions[0].payload_json).history_conflict_keys
        history_view = h.worker._history_store.load_window("BTCUSDT", start_ms=MS - 1000, end_ms=MS)
        original_summary = runtime.store.get_position("position-1").payload_json
        original_events = runtime.store.list_position_events("position-1")
        assert codec.decode(original_summary)[0] == "RUNTIME_POSITION_V3"
        original_projection = runtime.projection("position-1")
        original_journal = runtime.journal.get("entry")
        old_handle = await h.session.read_history_receipt(h.binding, scope=SCOPE, symbol="BTCUSDT",
            start_ms=MS - 1000, end_ms=MS)
        old_view = h.session.require_history_receipt(old_handle, binding=h.binding, scope=SCOPE)
        no_effects(h)
    finally:
        await h.worker.stop()
        await h.session.aclose()
    assert h.worker._operations == 0 and not h.worker._lock.acquired
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    snapshot_command("snapshot", "--source", root, "--target", backup,
        "--writers-stopped", "--require-history", "--require-runtime")
    snapshot_command("verify", "--source", backup, "--require-history", "--require-runtime")
    snapshot_command("restore", "--source", backup, "--target", restored,
        "--require-history", "--require-runtime")
    manifest = json.loads((backup / "manifest.json").read_text())
    assert manifest["format_version"] == 3 and manifest["history_files"] == ["history.sqlite3"]
    assert manifest["runtime_files"] == ["ai-dispatch.sqlite3", "execution.sqlite3",
                                        "runtime-policy.sqlite3", "runtime-state.sqlite3"]
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert {p.relative_to(restored): p.read_bytes() for p in restored.rglob("*") if p.is_file()} == before
    core = synthetic_session(SCOPE, NOW + timedelta(days=1))
    assembly = fresh_assembly()
    book = RuntimePolicyBook(restored / "runtime-policy.sqlite3")
    worker = ExecutionWorker(directory=restored, scope=SCOPE, session=core.session,
        binding=core.binding, symbols=("BTCUSDT",), trader_id="WORKER-001", policy_book=book,
        registry=h.registry, entry_risk=h.worker.entry_risk, clock=lambda: core.wall[0],
        max_age=timedelta(seconds=10), assembly_factory=lambda _sink: assembly,
        lock_directory=h.worker.lock_directory)
    assert worker is not h.worker and assembly.bridge is not h.assembly.bridge
    assert assembly.strategy is not h.assembly.strategy and assembly.channel is not h.assembly.channel
    await worker.start_passive()
    try:
        fresh = worker.runtime
        assert not core.reads
        assert fresh.projection("position-1") == original_projection
        assert fresh.store.get_position("position-1").payload_json == original_summary
        assert fresh.store.get_position("position-1").reconciliation_required
        assert fresh.store.list_position_events("position-1") == original_events
        assert tuple(item for item in fresh.store.list_position_events("position-1")
            if type(codec.decode(item.payload_json)) is CorroboratedExecutionCredit) == first_credit
        assert tuple(item for item in fresh.store.list_position_events("position-1")
            if type(codec.decode(item.payload_json)) is ExecutionCorroborationConflict) == first_decisions
        assert len(quarantines(worker)) == 1
        assert worker._history_store.load_window("BTCUSDT", start_ms=MS - 1000, end_ms=MS) == history_view
        recovered = fresh.journal.get("entry")
        assert recovered.reservation == original_journal.reservation and not recovered.terminal_confirmed
        assert fresh.status.state == "BLOCKED" and fresh.status.entries_paused
        assert book.read()["desired_state"] == "STOPPED"
        with pytest.raises(RuntimeBlocked, match="QUALIFICATION"):
            fresh.start()
        for owner, binding in ((h.session, h.binding), (core.session, core.binding)):
            with pytest.raises(ValueError):
                owner.require_history_receipt(old_handle, binding=binding, scope=SCOPE)
        events = fresh.store.list_position_events("position-1")
        for invalid in (old_handle, old_view, credit, credit.source, credit.observation):
            with pytest.raises(ValueError):
                fresh.corroborate_execution(receipt=invalid, binding=core.binding,
                    observation_key=credit.source.observation_key, candidate_id=credit.candidate_id)
        assert fresh.store.list_position_events("position-1") == events
        assert fresh.projection("position-1").owned_quantity == Decimal("0.4")
        assert not assembly.strategy.commands and not assembly.bridge._submissions and not core.reads
    finally:
        await worker.stop()
        await core.session.aclose()
