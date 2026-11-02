"""Native callbacks are durable observations, never authenticated executions."""

import json
import sqlite3
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from test_runtime import NS, SCOPE, consume, queue
from test_runtime import setup as setup  # noqa: PLC0414 - export shared pytest fixture

from multi_exchange_trader.execution import codec
from multi_exchange_trader.execution import protection as p
from multi_exchange_trader.execution.execution_provenance import (
    FillQuarantineRecord,
    VenueExecutionObservation,
    compare_execution_candidate,
)
from multi_exchange_trader.execution.journal import CommandState
from multi_exchange_trader.execution.native_events import NativeEventContext, NativeEventDecoder
from multi_exchange_trader.execution.state_store import RuntimeStateStore


def candidates(store: Any) -> list[FillQuarantineRecord]:
    return [value for item in store.list_position_events("position-1")
            if type(value := codec.decode(item.payload_json)) is FillQuarantineRecord]


@pytest.mark.parametrize("kind,reconciliation", [
    ("OrderFilled", False), ("OrderFilled", True), ("FillReport", None),
])
def test_native_fill_candidate_does_not_change_owned_quantity_or_ack(
    setup: Any, monkeypatch: pytest.MonkeyPatch, kind: str, reconciliation: bool | None,
) -> None:
    worker, journal, store, strategy, _, _ = setup
    record = queue(worker)
    journal.claim("entry")

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("native candidate reached accounting or journal authorization")

    monkeypatch.setattr(p, "apply_fill", forbidden)
    monkeypatch.setattr(journal, "acknowledge", forbidden)
    monkeypatch.setattr(journal, "release_reservation", forbidden)
    evidence = consume(worker, record.client_order_id, kind, reconciliation=reconciliation)
    assert evidence.fill is not None
    state = worker.projection("position-1")
    assert state.owned_quantity == 0 and state.executions == ()
    assert state.entries_blocked
    assert any(c.reason == "AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED"
               for c in state.conflicts)
    assert journal.get("entry").state is CommandState.SENDING
    assert journal.get("entry").reservation == record.reservation
    assert not journal.get("entry").terminal_confirmed
    assert candidates(store)[0].candidate.fill == evidence.fill
    assert not strategy.commands


def test_quarantine_invalidates_readbacks_and_pauses(setup: Any) -> None:
    worker, journal, store, _, _, module = setup
    record = queue(worker)
    journal.claim("entry")
    worker._active = True
    worker._readbacks["position-1"] = object()
    worker._leverage_readbacks["BTCUSDT"] = object()
    consume(worker, record.client_order_id, "OrderFilled", reconciliation=False)
    assert worker.status.entries_paused
    assert not worker._readbacks and not worker._leverage_readbacks
    assert len(candidates(store)) == 1
    with pytest.raises(module.RuntimeBlocked):
        worker.queue_exit("exit", "position-1", Decimal("0.4"), Decimal(100))


def test_quarantine_survives_restart_without_credit(setup: Any) -> None:
    worker, journal, store, _, kwargs, module = setup
    record = queue(worker)
    journal.claim("entry")
    consume(worker, record.client_order_id, "OrderFilled", reconciliation=True)
    before = worker.projection("position-1")
    saved = store.get_position("position-1").payload_json
    events = store.list_position_events("position-1")
    worker.shutdown()
    with RuntimeStateStore(kwargs["lock_directory"] / "runtime-state.sqlite3", store.scope) as opened:
        restarted = module.NativeExecutionRuntime(**{**kwargs, "store": opened})
        assert restarted.projection("position-1") == before
        assert before.owned_quantity == 0 and before.executions == () and before.entries_blocked
        assert opened.get_position("position-1").payload_json == saved
        assert opened.list_position_events("position-1") == events
        assert journal.get("entry").state is CommandState.UNKNOWN
        assert journal.get("entry").reservation == record.reservation
        assert restarted.status.entries_paused
        restarted.shutdown()


def test_quarantine_duplicate_keeps_same_candidate_identity(setup: Any) -> None:
    worker, journal, store, _, kwargs, module = setup
    record = queue(worker)
    journal.claim("entry")
    consume(worker, record.client_order_id, "OrderFilled", reconciliation=False)
    first = store.list_position_events("position-1")
    worker.shutdown()
    restarted = module.NativeExecutionRuntime(**kwargs)
    before = store.get_position("position-1")
    event = SimpleNamespace(account_id="BYBIT-UNIFIED", client_order_id=record.client_order_id,
        instrument_id="BTCUSDT-LINEAR.BYBIT", venue_order_id="venue-1", ts_event=NS,
        ts_init=NS + 10, order_side="BUY", trade_id="fill-1", last_qty=Decimal("0.40"),
        last_px=Decimal("100.0"), reconciliation=False,
        commission=SimpleNamespace(as_decimal=lambda: Decimal("0.010"), currency="USDT"))
    restarted.consume_native(event, NativeEventContext(SCOPE, "BYBIT-UNIFIED", NS + 20),
        decoder=NativeEventDecoder(event_types={SimpleNamespace: "OrderFilled"}))
    assert store.get_position("position-1") == before
    assert store.list_position_events("position-1") == first
    assert len(candidates(store)) == 1


@pytest.mark.parametrize("change", [
    {"venue_order_id": "venue-changed"}, {"last_px": Decimal(101)},
    {"last_qty": Decimal("0.3")}, {"ts_event": NS + 1},
    {"commission": SimpleNamespace(as_decimal=lambda: Decimal("-0.02"), currency="USDT")},
    {"commission": SimpleNamespace(as_decimal=lambda: Decimal("0.01"), currency="BTC")},
])
def test_same_exec_id_changed_candidate_is_preserved_as_conflict(setup: Any, change: Any) -> None:
    worker, journal, store, _, kwargs, module = setup
    record = queue(worker)
    journal.claim("entry")
    consume(worker, record.client_order_id, "OrderFilled", reconciliation=False)
    worker.shutdown()
    restarted = module.NativeExecutionRuntime(**kwargs)
    consume(restarted, record.client_order_id, "OrderFilled", reconciliation=False, **change)
    state = restarted.projection("position-1")
    assert state.owned_quantity == 0 and state.executions == ()
    assert any(c.reason == "EXECUTION_ID_CONFLICT" for c in state.conflicts)
    assert len(candidates(store)) == 2
    assert candidates(store)[0] != candidates(store)[1]
    saved = store.get_position("position-1").payload_json
    restarted.shutdown()
    again = module.NativeExecutionRuntime(**kwargs)
    assert again.projection("position-1") == state
    assert store.get_position("position-1").payload_json == saved


def test_quarantine_persistence_failure_cannot_credit_or_resume(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, journal, store, strategy, kwargs, _ = setup
    record = queue(worker)
    journal.claim("entry")
    before = store.get_position("position-1")
    worker._active = True
    worker._readbacks["position-1"] = object()
    worker._leverage_readbacks["BTCUSDT"] = object()
    apply = store.apply_position_event

    def check_pause(*args: Any, **kw: Any) -> Any:
        assert not worker._active and not worker._readbacks and not worker._leverage_readbacks
        return apply(*args, **kw)

    monkeypatch.setattr(store, "apply_position_event", check_pause)
    with sqlite3.connect(kwargs["lock_directory"] / "runtime-state.sqlite3") as conn:
        conn.execute("CREATE TRIGGER fail_quarantine BEFORE INSERT ON runtime_inbox "
                     "BEGIN SELECT RAISE(ABORT,'storage failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        consume(worker, record.client_order_id, "OrderFilled", reconciliation=False)
    assert store.get_position("position-1") == before
    assert not candidates(store)
    assert worker.status.entries_paused
    assert worker.projection("position-1").executions == ()
    assert journal.get("entry").state is CommandState.SENDING
    assert journal.get("entry").reservation == record.reservation
    assert not journal.get("entry").terminal_confirmed
    assert not strategy.commands


@pytest.mark.parametrize("aggregate", [False, True])
def test_legacy_native_fill_restore_requires_provenance(setup: Any, aggregate: bool) -> None:
    worker, journal, store, strategy, kwargs, module = setup
    record = queue(worker)
    journal.claim("entry")
    state = worker.projection("position-1")
    fill = p.FillEvent(SCOPE, "legacy-execution", record.client_order_id, "position-1",
                      state.instrument_id, "BUY", Decimal("0.4"), kwargs["clock"]())
    credited = p.apply_fill(state, fill)
    event = ("FILL", record.client_order_id, "venue-1", NS, None,
             (fill, Decimal(100), Decimal("0.01"), "USDT", NS))
    summary = ("RUNTIME_POSITION_V1", SCOPE, "position-1", state.instrument_id, "BUY",
               Decimal("0.4"), fill.occurred_at, 1, 1, 0, 0)
    current = store.get_position("position-1")
    store.apply_position_event("legacy-fill", json.loads(codec.encode(event)), "position-1",
        current.revision, json.loads(codec.encode(credited if aggregate else summary)))
    before = store.get_position("position-1"), store.list_position_events("position-1")
    command = journal.get("entry")
    worker.shutdown()
    with pytest.raises(module.RuntimeBlocked, match="LEGACY_NATIVE_FILL_PROVENANCE_REQUIRED"):
        module.NativeExecutionRuntime(**kwargs)
    assert (store.get_position("position-1"), store.list_position_events("position-1")) == before
    assert journal.get("entry") == command
    assert not strategy.commands


def test_matching_observation_does_not_release_native_quarantine(setup: Any) -> None:
    worker, journal, store, _, _, _ = setup
    record = queue(worker)
    journal.claim("entry")
    consume(worker, record.client_order_id, "OrderFilled", reconciliation=False)
    candidate = candidates(store)[0].candidate
    observation = VenueExecutionObservation(SCOPE, "caller-binding", "caller-batch", "fill-1",
        record.client_order_id, "venue-1", "BTCUSDT-LINEAR.BYBIT", "BUY", Decimal("0.4"),
        Decimal(100), Decimal("0.01"), "USDT", NS)
    assert compare_execution_candidate(candidate, observation).state == "MATCH"
    assert not compare_execution_candidate(candidate, observation).credit_authorized
    assert worker.projection("position-1").executions == ()
    assert worker.projection("position-1").entries_blocked
    assert journal.get("entry").state is CommandState.SENDING


def test_quarantine_replay_rejects_wrong_owned_scope(setup: Any) -> None:
    worker, journal, store, _, kwargs, module = setup
    record = queue(worker)
    journal.claim("entry")
    consume(worker, record.client_order_id, "OrderFilled", reconciliation=False)
    quarantined = candidates(store)[0]
    changed = replace(quarantined, candidate=replace(quarantined.candidate,
        fill=replace(quarantined.candidate.fill, scope=p.ProtectionScope("other", "DEMO"))))
    current = store.get_position("position-1")
    store.apply_position_event("forged-quarantine", json.loads(codec.encode(changed)), "position-1",
        current.revision, json.loads(current.payload_json))
    worker.shutdown()
    with pytest.raises(module.RuntimeBlocked, match="QUARANTINE_OWNERSHIP"):
        module.NativeExecutionRuntime(**kwargs)


def test_unsent_owned_command_fill_is_quarantined_without_changing_command(setup: Any) -> None:
    worker, journal, store, strategy, _, _ = setup
    record = queue(worker)
    consume(worker, record.client_order_id, "OrderFilled", reconciliation=False)
    assert journal.get("entry") == record
    assert len(candidates(store)) == 1
    assert worker.projection("position-1").entries_blocked
    assert worker.projection("position-1").executions == ()
    assert not strategy.commands


def test_missing_venue_identity_fails_after_revoking_readbacks(setup: Any) -> None:
    worker, journal, store, _, _, module = setup
    record = queue(worker)
    journal.claim("entry")
    worker._active = True
    worker._readbacks["position-1"] = object()
    worker._leverage_readbacks["BTCUSDT"] = object()
    with pytest.raises(module.RuntimeBlocked, match="NATIVE_FILL_CANDIDATE_REQUIRED"):
        consume(worker, record.client_order_id, "OrderFilled", reconciliation=False,
                venue_order_id=None)
    assert not worker._active and not worker._readbacks and not worker._leverage_readbacks
    assert not candidates(store)
    assert journal.get("entry").state is CommandState.SENDING
    assert worker.projection("position-1").executions == ()


def test_changed_quarantine_content_cannot_match_saved_summary(setup: Any) -> None:
    worker, journal, store, _, kwargs, module = setup
    record = queue(worker)
    journal.claim("entry")
    consume(worker, record.client_order_id, "OrderFilled", reconciliation=False)
    original = candidates(store)[0]
    changed = replace(original, candidate=replace(original.candidate,
        fill=replace(original.candidate.fill, price=Decimal(101))))
    row = store.list_position_events("position-1")[-1]
    worker.shutdown()
    with sqlite3.connect(kwargs["lock_directory"] / "runtime-state.sqlite3") as conn:
        conn.execute("UPDATE runtime_inbox SET event_payload=? WHERE event_id=?",
                     (codec.encode(changed), row.event_id))
    with pytest.raises(module.RuntimeBlocked, match="PROJECTION_HISTORY"):
        module.NativeExecutionRuntime(**kwargs)
    assert journal.get("entry").state is CommandState.SENDING
