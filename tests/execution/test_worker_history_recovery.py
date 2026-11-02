"""Explicit worker recovery with synthetic GETs, real journals and temporary SQLite."""

import asyncio
import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from test_risk import NOW
from test_runtime import SCOPE
from test_worker import fill, fresh_assembly, harness, quarantines, stage
from test_worker_corroboration import (
    MS,
    history_row,
    initialize_base_stores,
    no_effects,
    snapshot_command,
)
from worker_receipt_support import synthetic_session

from multi_exchange_trader.execution import codec
from multi_exchange_trader.execution.history_recovery import (
    HistoryExecutionCredit,
    HistoryRecoveryConflict,
    NativeHistoryResolution,
)
from multi_exchange_trader.execution.native_events import NativeEventDecoder
from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.execution.runtime import RuntimeBlocked
from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook
from multi_exchange_trader.execution.worker import ExecutionWorker


def order_row(client: str, **changes: Any) -> dict[str, Any]:
    return {"orderId": "venue-1", "orderLinkId": client, "symbol": "BTCUSDT", "side": "Buy",
        "positionIdx": 0, "reduceOnly": False, "orderStatus": "PartiallyFilled", "qty": "1",
        "cumExecQty": "0.8", "leavesQty": "0.2", "createdTime": str(MS - 1000),
        "updatedTime": str(MS)} | changes


async def prepared(tmp_path: Path) -> Any:
    locks = tmp_path.parent / (tmp_path.name + "-locks")
    locks.mkdir()
    h = harness(tmp_path, lock_directory=locks)
    await h.worker.start_passive()
    record = await stage(h)
    h.worker.runtime.journal.claim("entry")
    assert not quarantines(h.worker) and h.worker.runtime.persisted_candidates() == ()
    h.core.configured = True
    h.core.rows = [history_row(record.client_order_id)]
    h.core.order_rows = [order_row(record.client_order_id)]
    return h


async def recover(h: Any) -> Any:
    route = getattr(h.worker, "recover_history_fills", None)
    assert route is not None, "explicit worker history recovery required"
    return await route("BTCUSDT", start_ms=MS - 1000, end_ms=MS)


def events(h: Any, kind: type[Any]) -> tuple[Any, ...]:
    return tuple(row for row in h.worker.runtime.store.list_position_events("position-1")
                 if type(codec.decode(row.payload_json)) is kind)


async def test_no_candidate_credit_and_duplicate_preserve_first_metadata(tmp_path: Path) -> None:
    h = await prepared(tmp_path)
    try:
        before = h.worker.runtime.journal.list_commands(), h.worker.runtime.journal.audit_events()
        assert not h.core.reads
        assert [item.status for item in await recover(h)] == ["CREDITED"]
        first = events(h, HistoryExecutionCredit)
        credit = codec.decode(first[0].payload_json)
        assert credit.execution.execution.time_ms == MS
        assert credit.execution.execution.fee == Decimal("0.01")
        assert credit.execution.source.observed_at == NOW
        saved = h.worker.runtime.store.get_position("position-1")
        assert [item.status for item in await recover(h)] == ["DUPLICATE"]
        assert events(h, HistoryExecutionCredit) == first
        assert h.worker.runtime.store.get_position("position-1") == saved
        assert h.worker.runtime.projection("position-1").owned_quantity == Decimal("0.4")
        assert not h.worker.runtime.persisted_candidates() and not quarantines(h.worker)
        assert (h.worker.runtime.journal.list_commands(), h.worker.runtime.journal.audit_events()) == before
        assert not h.session._history_receipts and h.worker._operations == 0
        no_effects(h)
    finally:
        await h.worker.stop()


@pytest.mark.parametrize("rows", [[], ["manual"], ["missing_client"], ["missing_venue"], ["non_trade"]])
async def test_empty_or_ineligible_current_rows_are_preserved_without_credit(tmp_path: Path, rows: Any) -> None:
    h = await prepared(tmp_path)
    if not rows:
        h.core.rows = []
    elif rows == ["manual"]:
        h.core.rows[0]["orderLinkId"] = "manual"
        h.core.order_rows = []
    elif rows == ["missing_client"]:
        h.core.rows[0]["orderLinkId"] = ""
        h.core.order_rows = []
    elif rows == ["missing_venue"]:
        h.core.rows[0].update(execType="Funding", orderId="", execQty="0", execPrice="0", execValue="0")
    else:
        h.core.rows[0]["execType"] = "AdlTrade"
    try:
        result = await recover(h)
        assert [item.status for item in result] == ([] if not rows else ["UNRESOLVED"])
        assert not events(h, HistoryExecutionCredit)
        assert h.worker.runtime.projection("position-1").owned_quantity == 0
        assert h.worker._history_store._db.execute("SELECT count(*) FROM history_snapshots").fetchone() == (1,)
        assert not h.session._history_receipts
        no_effects(h)
    finally:
        await h.worker.stop()


async def test_batch_uses_chronology_then_execution_identity(tmp_path: Path) -> None:
    h = await prepared(tmp_path)
    row = h.core.rows[0]
    h.core.rows = [{**row, "execId": "z", "execTime": str(MS), "execQty": "0.2", "execValue": "20"},
                   {**row, "execId": "b", "execTime": str(MS - 100), "execQty": "0.2", "execValue": "20"},
                   {**row, "execId": "a", "execTime": str(MS - 100), "execQty": "0.2", "execValue": "20"}]
    try:
        result = await recover(h)
        assert [(r.execution_id, r.status) for r in result] == [("a", "CREDITED"), ("b", "CREDITED"), ("z", "CREDITED")]
        assert [codec.decode(r.payload_json).execution.execution.execution_id
                for r in events(h, HistoryExecutionCredit)] == ["a", "b", "z"]
        assert h.worker.runtime.projection("position-1").owned_quantity == Decimal("0.6")
        no_effects(h)
    finally:
        await h.worker.stop()


@pytest.mark.parametrize("native_first", [False, True])
async def test_both_arrival_orders_share_one_guard(tmp_path: Path, native_first: bool) -> None:
    h = await prepared(tmp_path)
    event = fill(h.core.rows[0]["orderLinkId"])
    try:
        if not native_first:
            assert (await recover(h))[0].status == "CREDITED"
        h.worker.post_native(h.assembly.channel, event)
        await asyncio.sleep(0)
        assert len(quarantines(h.worker)) == 1
        if native_first:
            assert (await recover(h))[0].reason_codes == ("NATIVE_CORROBORATION_REQUIRED",)
        result = await h.worker.corroborate_native_fills("BTCUSDT", start_ms=MS - 1000, end_ms=MS)
        assert result[0].status == ("CREDITED" if native_first else "DUPLICATE")
        assert (await recover(h))[0].status == "DUPLICATE"
        assert len(events(h, HistoryExecutionCredit)) == (0 if native_first else 1)
        assert len(events(h, NativeHistoryResolution)) == (0 if native_first else 1)
        assert h.worker.runtime.projection("position-1").owned_quantity == Decimal("0.4")
        no_effects(h)
    finally:
        await h.worker.stop()


async def test_stop_during_get_rejects_before_any_history_sql(tmp_path: Path) -> None:
    h = await prepared(tmp_path)
    h.core.block = True
    task = asyncio.create_task(recover(h))
    # Check the missing API before waiting for the deliberately blocked GET.
    await asyncio.sleep(0)
    if task.done():
        await task
    await asyncio.wait_for(h.core.entered.wait(), 1)
    accesses: list[str] = []
    h.worker._history_store._db.set_trace_callback(accesses.append)
    with pytest.raises(RuntimeBlocked, match="AWAIT_INFLIGHT_OPERATION_BEFORE_STOP"):
        await h.worker.stop()
    h.core.release.set()
    with pytest.raises(RuntimeBlocked, match="WORKER_STOPPING"):
        await task
    assert accesses == [] and not h.session._history_receipts and h.worker._operations == 0
    h.worker._history_store._db.set_trace_callback(None)
    assert not events(h, HistoryExecutionCredit)
    await h.worker.stop()


@pytest.mark.parametrize("change", ["binding", "session", "runtime", "history", "pinned_history",
    "pinned_session", "lease", "scope", "symbols", "expired"])
async def test_changed_owner_after_get_rejects_before_history_sql(tmp_path: Path, change: str) -> None:
    h = await prepared(tmp_path)
    worker, runtime, history = h.worker, h.worker.runtime, h.worker._history_store
    h.core.block = True
    task = asyncio.create_task(recover(h))
    await asyncio.sleep(0)
    if task.done():
        await task
    await asyncio.wait_for(h.core.entered.wait(), 1)
    mutate(h, change)
    accesses: list[str] = []
    history._db.set_trace_callback(accesses.append)
    h.core.release.set()
    try:
        with pytest.raises((RuntimeBlocked, ValueError)):
            await task
        assert accesses == [] and worker._operations == 0 and not h.session._history_receipts
    finally:
        restore_owner(h, runtime, history)
        history._db.set_trace_callback(None)
        if change == "lease":
            worker._close_stores()
        else:
            await worker.stop()


def mutate(h: Any, change: str) -> None:
    worker = h.worker
    if change == "binding":
        worker.binding = object()
    elif change == "session":
        worker.session = object()
    elif change == "runtime":
        worker._runtime = object()
    elif change == "history":
        worker._history_store = object()
    elif change == "pinned_history":
        worker.runtime._history_store = object()
    elif change == "pinned_session":
        worker.runtime._receipt_session = object()
    elif change == "lease":
        worker._lock.release()
        worker._lock.acquire()
    elif change == "scope":
        worker.scope = ProtectionScope("other", "DEMO")
    elif change == "symbols":
        worker.symbols = ()
    elif change == "expired":
        h.core.mono[0] += 31
    elif change == "stop":
        worker._stopping = True


def restore_owner(h: Any, runtime: Any, history: Any) -> None:
    h.worker.session, h.worker.binding, h.worker._runtime = h.session, h.binding, runtime
    h.worker._history_store, h.worker.scope, h.worker.symbols = history, SCOPE, ("BTCUSDT",)
    runtime._receipt_session, runtime._history_store = h.session, history


@pytest.mark.parametrize("change", ["stop", "binding", "session", "runtime", "history", "pinned_history",
    "pinned_session", "lease", "scope", "symbols", "expired"])
async def test_each_row_rechecks_authority_and_keeps_partial_commits(tmp_path: Path, monkeypatch: Any, change: str) -> None:
    h = await prepared(tmp_path)
    h.core.rows.append({**h.core.rows[0], "execId": "fill-2"})
    runtime, history = h.worker.runtime, h.worker._history_store
    original = runtime.recover_history_execution

    def first_then_change(**kwargs: Any) -> Any:
        result = original(**kwargs)
        mutate(h, change)
        return result

    monkeypatch.setattr(runtime, "recover_history_execution", first_then_change)
    try:
        with pytest.raises((RuntimeBlocked, ValueError)):
            await recover(h)
        assert sum(type(codec.decode(r.payload_json)) is HistoryExecutionCredit
                   for r in runtime.store.list_position_events("position-1")) == 1
        if change == "lease":
            with pytest.raises(RuntimeBlocked, match="ACCOUNT_WORKER_LEASE_REQUIRED"):
                runtime.projection("position-1")
        else:
            assert runtime.projection("position-1").owned_quantity == Decimal("0.4")
        assert not h.session._history_receipts and h.worker._operations == 0
    finally:
        restore_owner(h, runtime, history)
        if change == "lease":
            h.worker._close_stores()
        else:
            await h.worker.stop()


@pytest.mark.parametrize("second", [False, True])
async def test_committed_history_and_partial_accounting_retry_without_double_credit(tmp_path: Path, monkeypatch: Any, second: bool) -> None:
    h = await prepared(tmp_path)
    if second:
        h.core.rows.append({**h.core.rows[0], "execId": "fill-2"})
    store = h.worker.runtime.store
    original = store.apply_position_event
    calls = 0

    def fail(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == (2 if second else 1):
            raise RuntimeBlocked("SYNTHETIC_CREDIT_FAILURE")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "apply_position_event", fail)
    try:
        with pytest.raises(RuntimeBlocked, match="SYNTHETIC_CREDIT_FAILURE"):
            await recover(h)
        assert len(events(h, HistoryExecutionCredit)) == (1 if second else 0)
        assert h.worker._history_store._db.execute("SELECT count(*) FROM history_snapshots").fetchone() == (1,)
        assert not h.session._history_receipts and h.worker._operations == 0
        monkeypatch.setattr(store, "apply_position_event", original)
        assert [r.status for r in await recover(h)] == (["DUPLICATE", "CREDITED"] if second else ["CREDITED"])
        assert h.worker.runtime.projection("position-1").owned_quantity == Decimal("0.8" if second else "0.4")
        no_effects(h)
    finally:
        await h.worker.stop()


async def test_cancel_during_get_discards_pending_receipt(tmp_path: Path) -> None:
    h = await prepared(tmp_path)
    h.core.block = True
    task = asyncio.create_task(recover(h))
    await asyncio.sleep(0)
    if task.done():
        await task
    await asyncio.wait_for(h.core.entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not h.session._history_receipts and h.worker._operations == 0
    assert h.worker._history_store._db.execute("SELECT count(*) FROM history_snapshots").fetchone() == (0,)
    await h.worker.stop()


async def test_injected_get_fails_without_history_sql(tmp_path: Path) -> None:
    core = synthetic_session(SCOPE, NOW, transport=httpx.MockTransport(lambda _request: None))
    h = harness(tmp_path, core=core)
    await h.worker.start_passive()
    try:
        with pytest.raises(ValueError, match="ACCOUNT_NETWORK_TRANSPORT_REQUIRED"):
            await recover(h)
        assert not core.reads and not core.session._history_receipts
        assert h.worker._history_store._db.execute("SELECT count(*) FROM history_snapshots").fetchone() == (0,)
    finally:
        await h.worker.stop()


async def test_actual_v3_backup_restores_v4_history_credit_next_day_without_authority(tmp_path: Path) -> None:
    root, backup, restored = (tmp_path / name for name in ("state", "backup", "restored"))
    root.mkdir()
    initialize_base_stores(root)
    h = await prepared(root)
    runtime = h.worker.runtime
    clean = dict(h.core.rows[0])
    conflicted = {**clean, "execId": "fill-2", "execFee": "-0.01", "feeRate": "-0.00025"}
    h.core.rows = [clean, conflicted]
    try:
        assert [r.status for r in await recover(h)] == ["CREDITED", "CREDITED"]
        original_credits = events(h, HistoryExecutionCredit)
        assert len(original_credits) == 2 and not quarantines(h.worker)
        h.core.rows = [{**conflicted, "execFee": "-0.02"}]
        assert (await recover(h))[0].status == "CONFLICT"
        original_decisions = events(h, HistoryRecoveryConflict)
        decision = codec.decode(original_decisions[0].payload_json)
        assert decision.prior_credit_event_id == original_credits[1].event_id
        assert decision.history_conflict_keys and decision.execution.execution.fee == Decimal("-0.02")
        original_summary = runtime.store.get_position("position-1").payload_json
        assert codec.decode(original_summary)[0] == "RUNTIME_POSITION_V4"
        assert len(codec.decode(original_summary)) == 23
        original_events = runtime.store.list_position_events("position-1")
        original_projection = runtime.projection("position-1")
        assert original_projection.owned_quantity == Decimal("0.8") and original_projection.conflicts
        original_command = runtime.journal.get("entry")
        original_history = h.worker._history_store.load_window("BTCUSDT", start_ms=MS - 1000, end_ms=MS)
        old_handle = await h.session.read_history_receipt(h.binding, scope=SCOPE, symbol="BTCUSDT",
            start_ms=MS - 1000, end_ms=MS)
        old_view = h.session.require_history_receipt(old_handle, binding=h.binding, scope=SCOPE)
        no_effects(h)
    finally:
        await h.worker.stop()
        await h.session.aclose()
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    snapshot_command("snapshot", "--source", root, "--target", backup,
        "--writers-stopped", "--require-history", "--require-runtime")
    snapshot_command("verify", "--source", backup, "--require-history", "--require-runtime")
    snapshot_command("restore", "--source", backup, "--target", restored,
        "--require-history", "--require-runtime")
    manifest = json.loads((backup / "manifest.json").read_text())
    assert manifest["format_version"] == 3
    assert manifest["history_files"] == ["history.sqlite3"]
    assert manifest["runtime_files"] == ["ai-dispatch.sqlite3", "execution.sqlite3",
                                         "runtime-policy.sqlite3", "runtime-state.sqlite3"]
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert {p.relative_to(restored): p.read_bytes() for p in restored.rglob("*") if p.is_file()} == before
    core = synthetic_session(SCOPE, NOW + timedelta(days=1))
    assembly = fresh_assembly()
    book = RuntimePolicyBook(restored / "runtime-policy.sqlite3")
    worker = ExecutionWorker(directory=restored, scope=SCOPE, session=core.session, binding=core.binding,
        symbols=("BTCUSDT",), trader_id="WORKER-001", policy_book=book, registry=h.registry,
        entry_risk=h.worker.entry_risk, clock=lambda: core.wall[0], max_age=timedelta(seconds=10),
        assembly_factory=lambda _sink: assembly, lock_directory=h.worker.lock_directory,
        decoder=NativeEventDecoder(event_types={SimpleNamespace: "OrderFilled"}))
    fresh = SimpleNamespace(worker=worker, core=core, session=core.session, binding=core.binding,
                            assembly=assembly, book=book)
    assert worker is not h.worker and assembly.bridge is not h.assembly.bridge
    assert assembly.channel is not h.assembly.channel and core.session is not h.session
    await worker.start_passive()
    try:
        runtime = worker.runtime
        assert not core.reads and not core.session._history_receipts
        assert runtime.projection("position-1") == original_projection
        assert runtime.store.get_position("position-1").payload_json == original_summary
        assert runtime.store.list_position_events("position-1") == original_events
        assert events(fresh, HistoryExecutionCredit) == original_credits
        assert events(fresh, HistoryRecoveryConflict) == original_decisions
        assert worker._history_store.load_window("BTCUSDT", start_ms=MS - 1000, end_ms=MS) == original_history
        recovered = runtime.journal.get("entry")
        assert recovered.reservation == original_command.reservation and not recovered.terminal_confirmed
        assert runtime.store.get_position("position-1").reconciliation_required
        assert runtime.status.state == "BLOCKED" and book.read()["desired_state"] == "STOPPED"
        with pytest.raises(RuntimeBlocked, match="QUALIFICATION"):
            runtime.start()
        for owner, binding in ((h.session, h.binding), (core.session, core.binding)):
            with pytest.raises(ValueError):
                owner.require_history_receipt(old_handle, binding=binding, scope=SCOPE)
        credit = codec.decode(original_credits[0].payload_json)
        before_invalid = runtime.store.list_position_events("position-1")
        for invalid in (old_handle, old_view, credit, credit.execution, credit.execution.source):
            with pytest.raises(ValueError):
                runtime.recover_history_execution(receipt=invalid, binding=core.binding,
                    observation_key=credit.execution.source.observation_key, execution_id="fill-1")
        assert runtime.store.list_position_events("position-1") == before_invalid
        core.configured = True
        core.rows, core.order_rows = [clean], [order_row(clean["orderLinkId"])]
        assert (await recover(fresh))[0].status == "DUPLICATE"
        assert runtime.store.get_position("position-1").payload_json == original_summary
        worker.post_native(h.assembly.channel, fill(clean["orderLinkId"]))
        await asyncio.sleep(0)
        assert worker.rejected_channels == 1 and not quarantines(worker)
        worker.post_native(assembly.channel, fill(clean["orderLinkId"]))
        await asyncio.sleep(0)
        assert len(quarantines(worker)) == 1 and runtime.projection("position-1").owned_quantity == Decimal("0.8")
        result = await worker.corroborate_native_fills("BTCUSDT", start_ms=MS - 1000, end_ms=MS)
        assert result[0].status == "DUPLICATE"
        resolution = events(fresh, NativeHistoryResolution)
        assert len(resolution) == 1
        resolved = codec.decode(resolution[0].payload_json)
        assert resolved.prior_credit_event_id == original_credits[0].event_id
        assert resolved.credit_digest == hashlib.sha256(
            original_credits[0].payload_json.encode()).hexdigest()
        assert resolved.execution.source.observed_at == NOW + timedelta(days=1)
        assert resolved.execution.execution.time_ms == MS
        assert events(fresh, HistoryExecutionCredit) == original_credits
        assert events(fresh, HistoryRecoveryConflict) == original_decisions
        assert runtime.projection("position-1").conflicts
        assert runtime.projection("position-1").owned_quantity == Decimal("0.8")
        saved = runtime.store.get_position("position-1")
        assert (await worker.corroborate_native_fills("BTCUSDT", start_ms=MS - 1000, end_ms=MS))[0].status == "DUPLICATE"
        assert runtime.store.get_position("position-1") == saved
        core.rows = [conflicted]
        assert (await recover(fresh))[0].status == "CONFLICT"
        assert events(fresh, HistoryExecutionCredit) == original_credits
        assert events(fresh, NativeHistoryResolution) == resolution
        runtime._projections.clear()
        assert runtime.projection("position-1").owned_quantity == Decimal("0.8")
        assert runtime.projection("position-1").conflicts
        assert not core.session._history_receipts
        no_effects(fresh)
    finally:
        await worker.stop()
        await core.session.aclose()
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("change", ["missing_client", "funding_empty_ids", "non_trade"])
async def test_late_ineligible_rows_still_reach_prior_credit_inspection(tmp_path: Path, change: str) -> None:
    h = await prepared(tmp_path)
    try:
        assert (await recover(h))[0].status == "CREDITED"
        original = events(h, HistoryExecutionCredit)
        h.core.order_rows = []
        if change == "missing_client":
            h.core.rows[0]["orderLinkId"] = ""
        elif change == "funding_empty_ids":
            h.core.rows[0].update(execType="Funding", orderLinkId="", orderId="",
                                  execQty="0", execPrice="0", execValue="0")
        else:
            h.core.rows[0]["execType"] = "AdlTrade"
        assert (await recover(h))[0].status == "CONFLICT"
        decision = codec.decode(events(h, HistoryRecoveryConflict)[0].payload_json)
        assert decision.execution.execution.client_order_id == h.core.rows[0]["orderLinkId"]
        assert decision.execution.execution.venue_order_id == h.core.rows[0]["orderId"]
        assert decision.prior_credit_event_id == original[0].event_id
        assert events(h, HistoryExecutionCredit) == original
        assert h.worker.runtime.projection("position-1").owned_quantity == Decimal("0.4")
        assert not h.session._history_receipts
        no_effects(h)
    finally:
        await h.worker.stop()


async def test_collect_history_remains_observation_only_without_recovery(tmp_path: Path) -> None:
    h = await prepared(tmp_path)
    try:
        assert not h.core.reads
        await h.worker.collect_history("BTCUSDT", start_ms=MS - 1000, end_ms=MS)
        assert not events(h, HistoryExecutionCredit)
        assert h.worker.runtime.projection("position-1").owned_quantity == 0
        assert not h.worker.runtime.persisted_candidates() and not h.session._history_receipts
        no_effects(h)
    finally:
        await h.worker.stop()


async def test_ingest_failure_discards_issued_receipt_without_accounting(tmp_path: Path) -> None:
    h = await prepared(tmp_path)
    history = h.worker._history_store
    history._db.execute("CREATE TEMP TRIGGER fail_history BEFORE INSERT ON history_snapshots "
                        "BEGIN SELECT RAISE(ABORT,'synthetic history failure'); END")
    try:
        with pytest.raises(ValueError, match="HISTORY_INGEST_FAILED"):
            await recover(h)
        assert not h.session._history_receipts and h.worker._operations == 0
        assert not events(h, HistoryExecutionCredit)
        assert history._db.execute("SELECT count(*) FROM history_snapshots").fetchone() == (0,)
        assert h.worker.runtime.projection("position-1").owned_quantity == 0
        no_effects(h)
    finally:
        await h.worker.stop()


async def test_unconfigured_get_fails_without_ingestion(tmp_path: Path) -> None:
    h = harness(tmp_path)
    await h.worker.start_passive()
    try:
        with pytest.raises(ValueError, match="AUTHENTICATED_HISTORY_READ_FAILED"):
            await recover(h)
        assert h.core.reads == ["/v5/execution/list"]
        assert not h.session._history_receipts and h.worker._operations == 0
        assert h.worker._history_store._db.execute("SELECT count(*) FROM history_snapshots").fetchone() == (0,)
        no_effects(h)
    finally:
        await h.worker.stop()
