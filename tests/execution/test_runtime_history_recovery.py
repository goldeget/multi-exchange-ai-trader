"""History-only accounting uses real temporary stores and synthetic receipt slots."""

import json
import sqlite3
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
from test_runtime import NS, SCOPE, consume, queue
from test_runtime import setup as setup  # noqa: PLC0414
from test_runtime_credit import OBSERVED, credit, issue, native, snapshot
from test_runtime_credit import live as live  # noqa: PLC0414

from multi_exchange_trader.execution import codec
from multi_exchange_trader.execution.execution_credit import candidate_identity, execution_guard
from multi_exchange_trader.execution.history import (
    ExecutionRecord,
    HistorySnapshot,
    HistoryWindow,
    OrderHistoryRecord,
)
from multi_exchange_trader.execution.history_recovery import (
    HistoryExecutionCredit,
    NativeHistoryResolution,
)


def owned(core: Any, state: str = "SENDING") -> Any:
    record = queue(core.runtime)
    if state != "PENDING":
        core.journal.claim("entry")
    if state in ("ACKNOWLEDGED", "TERMINAL"):
        core.journal.acknowledge("entry", "synthetic-ack")
    if state == "UNKNOWN":
        core.journal.mark_unknown("entry", "synthetic-unknown")
    if state == "REJECTED":
        core.journal.reject("entry", "synthetic-rejection")
    if state == "TERMINAL":
        core.journal.release_reservation("entry", confirmed_terminal=True, terminal_evidence="synthetic-terminal")
    return record


def observed(record: Any, **changes: Any) -> HistorySnapshot:
    row = ExecutionRecord("fill-1", "venue-1", record.client_order_id, "BTCUSDT", "Buy", "Trade",
        NS // 1_000_000, Decimal("0.4"), Decimal(100), Decimal(40), Decimal("-0.01"),
        "USDT", Decimal("-0.00025"), Decimal(0))
    order = OrderHistoryRecord("venue-1", record.client_order_id, "BTCUSDT", "Buy", 0,
        "PartiallyFilled", Decimal(1), Decimal("0.4"), Decimal("0.6"), False,
        NS // 1_000_000, NS // 1_000_000)
    return replace(HistorySnapshot(SCOPE, "BTCUSDT", "history-1", HistoryWindow(
        NS // 1_000_000 - 1000, NS // 1_000_000 + 1000), OBSERVED, OBSERVED,
        (row,), (order,), (), False), **changes)


def recover(core: Any, snapshot: HistorySnapshot, execution_id: str = "fill-1") -> Any:
    receipt, key = issue(core, snapshot)
    route = getattr(core.runtime, "recover_history_execution", None)
    assert route is not None, "receipt-gated history recovery required"
    return route(receipt=receipt, binding=core.binding, observation_key=key, execution_id=execution_id)


@pytest.mark.parametrize("state", ["SENDING", "UNKNOWN", "ACKNOWLEDGED"])
def test_history_credit_preserves_first_metadata_command_and_native_absence(live: Any, state: str) -> None:
    record = owned(live, state)
    before = live.journal.list_commands(), live.journal.audit_events()
    snap = observed(record)
    assert recover(live, snap).status == "CREDITED"
    guard = execution_guard(SCOPE, "BTCUSDT-LINEAR.BYBIT", "fill-1")
    first = live.store.get_inbox_event(guard)
    event = codec.decode(first.payload_json)
    assert type(event) is HistoryExecutionCredit and event.execution.execution.fee == Decimal("-0.01")
    assert event.command_key == record.command_key and event.command_payload_hash == record.payload_hash
    position = live.store.get_position("position-1")
    assert recover(live, replace(snap, snapshot_id="fresh")).status == "DUPLICATE"
    assert live.store.get_position("position-1") == position
    assert live.store.get_inbox_event(guard) == first
    assert live.runtime.persisted_candidates() == () and not live.strategy.commands
    assert (live.journal.list_commands(), live.journal.audit_events()) == before
    assert live.runtime.projection("position-1").owned_quantity == Decimal("0.4")
    live.runtime.shutdown()
    live.runtime = live.module.NativeExecutionRuntime(**live.kwargs)
    assert recover(live, replace(snap, snapshot_id="restart")).status == "DUPLICATE"


@pytest.mark.parametrize("state", ["PENDING", "REJECTED", "TERMINAL"])
def test_new_ineligible_commands_never_credit(live: Any, state: str) -> None:
    record = owned(live, state)
    assert recover(live, observed(record)).reason_codes == ("OWNED_COMMAND_INELIGIBLE",)
    assert live.runtime.projection("position-1").owned_quantity == 0


def test_same_fresh_receipt_order_required_even_with_old_order_or_native_ack(live: Any) -> None:
    record = owned(live)
    snap = observed(record)
    live.history.ingest(replace(snap, snapshot_id="old", executions=()))
    consume(live.runtime, record.client_order_id, "OrderAccepted")
    assert recover(live, replace(snap, orders=())).reason_codes == ("HISTORY_CURRENT_ORDER_REQUIRED",)
    assert live.runtime.projection("position-1").owned_quantity == 0


@pytest.mark.parametrize("changes", [
    {"quantity": Decimal("0.3")}, {"price": Decimal(101)}, {"fee": Decimal("0.02")},
    {"time_ms": NS // 1_000_000 + 1}, {"client_order_id": ""}, {"venue_order_id": "other"},
    {"value": Decimal(41)}, {"fee_rate": Decimal("0.001")}, {"closed_quantity": Decimal("0.1")},
    {"execution_type": "Funding", "client_order_id": "", "venue_order_id": "",
     "quantity": Decimal(0), "price": Decimal(0), "value": Decimal(0)},
])
def test_late_contradictions_persist_after_terminal_and_keep_first_quantity(live: Any, changes: Any) -> None:
    record = owned(live)
    snap = observed(record)
    assert recover(live, snap).status == "CREDITED"
    live.journal.acknowledge("entry", "synthetic-ack")
    live.journal.release_reservation("entry", confirmed_terminal=True, terminal_evidence="synthetic-terminal")
    assert recover(live, replace(snap, snapshot_id="changed", orders=(),
        executions=(replace(snap.executions[0], **changes),))).status == "CONFLICT"
    assert recover(live, replace(snap, snapshot_id="matching-again")).status == "CONFLICT"
    assert live.runtime.projection("position-1").owned_quantity == Decimal("0.4")
    live.runtime.shutdown()
    live.runtime = live.module.NativeExecutionRuntime(**live.kwargs)
    assert live.runtime.projection("position-1").owned_quantity == Decimal("0.4")


def test_native_candidate_cannot_be_bypassed_by_history_first_credit(live: Any) -> None:
    candidate = native(live)
    record = live.journal.get("entry")
    snap = replace(snapshot(candidate), orders=observed(record).orders)
    assert recover(live, snap).reason_codes == ("NATIVE_CORROBORATION_REQUIRED",)
    assert live.runtime.projection("position-1").owned_quantity == 0
    result = credit(live, candidate, snap)
    assert result.status == "CREDITED", result


def test_history_first_native_late_exact_resolution_does_not_credit_twice(live: Any) -> None:
    record = owned(live)
    snap = observed(record)
    assert recover(live, snap).status == "CREDITED"
    live.journal.acknowledge("entry", "synthetic-ack")
    live.journal.release_reservation("entry", confirmed_terminal=True, terminal_evidence="synthetic-terminal")
    consume(live.runtime, record.client_order_id, "OrderFilled", commission=type("Fee", (), {
        "as_decimal": lambda self: Decimal("-0.01"), "currency": "USDT"})())
    candidate = live.runtime.persisted_candidates()[0]
    assert live.runtime.projection("position-1").owned_quantity == Decimal("0.4")
    receipt, key = issue(live, replace(snap, snapshot_id="late-native"))
    result = live.runtime.corroborate_execution(receipt=receipt, binding=live.binding,
        observation_key=key, candidate_id=candidate_identity(candidate))
    assert result.status == "DUPLICATE"
    events = [codec.decode(item.payload_json) for item in live.store.list_position_events("position-1")]
    assert sum(type(item) is NativeHistoryResolution for item in events) == 1
    position = live.store.get_position("position-1")
    receipt, key = issue(live, replace(snap, snapshot_id="another-fresh-native"))
    assert live.runtime.corroborate_execution(receipt=receipt, binding=live.binding,
        observation_key=key, candidate_id=candidate_identity(candidate)).status == "DUPLICATE"
    assert live.store.get_position("position-1") == position
    assert not any(item.reason == "AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED"
        for item in live.runtime.projection("position-1").conflicts)
    live.runtime.shutdown()
    live.runtime = live.module.NativeExecutionRuntime(**live.kwargs)
    assert live.runtime.projection("position-1").owned_quantity == Decimal("0.4")


def test_native_first_recovery_uses_real_prior_and_retains_native_credit(live: Any) -> None:
    candidate = native(live)
    assert credit(live, candidate).status == "CREDITED"
    snap = replace(snapshot(candidate), snapshot_id="fresh-history", orders=observed(live.journal.get("entry")).orders)
    result = recover(live, snap)
    assert result.status == "DUPLICATE", result
    assert recover(live, replace(snap, snapshot_id="changed", orders=(),
        executions=(replace(snap.executions[0], client_order_id=""),))).status == "CONFLICT"
    assert live.runtime.projection("position-1").owned_quantity == Decimal("0.4")


def test_native_first_recovery_still_checks_full_immutable_order_quantity(live: Any) -> None:
    candidate = native(live)
    assert credit(live, candidate).status == "CREDITED"
    order = replace(observed(live.journal.get("entry")).orders[0], quantity=Decimal(2),
                    leaves_quantity=Decimal("1.6"))
    snap = replace(snapshot(candidate), snapshot_id="wrong-order-quantity", orders=(order,))
    result = recover(live, snap)
    assert result.status == "CONFLICT" and "HISTORY_ORDER_CONFLICT" in result.reason_codes
    assert live.runtime.projection("position-1").owned_quantity == Decimal("0.4")


@pytest.mark.parametrize("route", ["native", "history"])
def test_history_first_wrong_symbol_remains_conflict_after_terminal(live: Any, route: str) -> None:
    record = owned(live)
    snap = observed(record)
    snap = replace(snap, executions=(replace(snap.executions[0], fee=Decimal("0.01")),))
    assert recover(live, snap).status == "CREDITED"
    live.journal.acknowledge("entry", "synthetic-ack")
    live.journal.release_reservation("entry", confirmed_terminal=True, terminal_evidence="synthetic-terminal")
    wrong = replace(snap, snapshot_id="wrong-symbol", symbol="ETHUSDT", orders=(),
        executions=(replace(snap.executions[0], symbol="ETHUSDT"),))
    if route == "native":
        consume(live.runtime, record.client_order_id, "OrderFilled")
        candidate = live.runtime.persisted_candidates()[0]
        receipt, key = issue(live, wrong)
        result = live.runtime.corroborate_execution(receipt=receipt, binding=live.binding,
            observation_key=key, candidate_id=candidate_identity(candidate))
    else:
        result = recover(live, wrong)
    assert result.status == "CONFLICT" and "INSTRUMENT_ID_MISMATCH" in result.reason_codes
    assert live.runtime.projection("position-1").owned_quantity == Decimal("0.4")


@pytest.mark.parametrize("route", ["history", "native", "new_history_replay"])
def test_malformed_legacy_command_linkage_cannot_authorize_new_history_or_live_prior(live: Any, route: str) -> None:
    from test_native_quarantine import seed_credit

    from multi_exchange_trader.execution.execution_credit import ExecutionObservationReference
    from multi_exchange_trader.execution.history_recovery import (
        HistoryExecutionEvidence,
        HistoryRecoveryConflict,
        history_decision_event_id,
    )
    from multi_exchange_trader.execution.history_store import _variant

    candidate = native(live)
    legacy = seed_credit(live.runtime, live.store, candidate)
    assert legacy.command_key == "command" and live.journal.get("command") is None
    snap = snapshot(candidate)
    receipt, key = issue(live, snap)
    if route != "new_history_replay":
        with pytest.raises(ValueError, match="INVALID_DURABLE_PRIOR_CREDIT"):
            if route == "history":
                live.runtime.recover_history_execution(receipt=receipt, binding=live.binding,
                    observation_key=key, execution_id="fill-1")
            else:
                live.runtime.corroborate_execution(receipt=receipt, binding=live.binding,
                    observation_key=key, candidate_id=candidate_identity(candidate))
        return
    state = live.runtime.projection("position-1")
    command = live.journal.get("entry")
    source = ExecutionObservationReference(SCOPE, "synthetic-binding", key, snap.snapshot_id,
        snap.symbol, snap.window.start_ms, snap.window.end_ms, snap.observed_at, snap.server_observed_at)
    event = HistoryRecoveryConflict(HistoryExecutionEvidence(source, snap.executions[0],
        _variant(snap.executions[0])[2]), state.orders[0], command.command_key, command.payload_hash,
        ("HISTORY_EXECUTION_CONFLICT",), (), (),
        execution_guard(SCOPE, state.instrument_id, "fill-1"), None)
    cached = live.runtime._projections[state.position_id]
    context = replace(cached[3])
    reduced = live.runtime._reduce_event(state, event, dict(cached[2]), context)
    live.store.apply_position_event(history_decision_event_id(event), json.loads(codec.encode(event)),
        state.position_id, cached[0], json.loads(codec.encode(live.runtime._summary(reduced,
            cached[2], context.credit, context.history))))
    live.runtime.shutdown()
    with pytest.raises(ValueError, match="INVALID_DURABLE_PRIOR_CREDIT"):
        live.module.NativeExecutionRuntime(**live.kwargs)


@pytest.mark.parametrize("field", ["command_key", "command_payload_hash", "owned_order"])
def test_restore_rejects_tampered_immutable_history_linkage_before_recovery(live: Any, field: str) -> None:
    record = owned(live)
    assert recover(live, observed(record)).status == "CREDITED"
    guard = execution_guard(SCOPE, "BTCUSDT-LINEAR.BYBIT", "fill-1")
    event = codec.decode(live.store.get_inbox_event(guard).payload_json)
    changes = {"command_key": "missing", "command_payload_hash": "c" * 64,
        "owned_order": replace(event.owned_order, policy_revision="other")}
    bad = replace(event, **{field: changes[field]})
    with sqlite3.connect(live.kwargs["lock_directory"] / "runtime-state.sqlite3") as db:
        db.execute("UPDATE runtime_inbox SET event_payload=? WHERE event_id=?",
                   (json.dumps(json.loads(codec.encode(bad)), sort_keys=True, separators=(",", ":")), guard))
    live.runtime.shutdown()
    with pytest.raises(ValueError, match="DURABLE_IMMUTABLE_MAPPING_REQUIRED"):
        live.module.NativeExecutionRuntime(**live.kwargs)


@pytest.mark.parametrize("misuse", ["copied", "discarded", "expired", "whole_snapshot", "foreign_binding"])
def test_recovery_requires_exact_current_receipt_authority(live: Any, misuse: str) -> None:
    import copy

    record = owned(live)
    snap = observed(record)
    receipt, key = issue(live, snap)
    binding = live.binding
    if misuse == "copied":
        with pytest.raises(ValueError, match="AUTHENTICATED_HISTORY_RECEIPT_REQUIRED"):
            copy.copy(receipt)
        receipt = object.__new__(type(receipt))
    elif misuse == "discarded":
        live.owner.discard_history_receipt(receipt)
    elif misuse == "expired":
        live.owner._monotonic = lambda: 1000.0
    elif misuse == "whole_snapshot":
        receipt, _ = issue(live, replace(snap, snapshot_id="other"))
    else:
        binding = object.__new__(type(binding))
    before = live.store.get_position("position-1")
    with pytest.raises(ValueError):
        live.runtime.recover_history_execution(receipt=receipt, binding=binding,
            observation_key=key, execution_id="fill-1")
    assert live.store.get_position("position-1") == before


@pytest.mark.parametrize("failure", ["authority", "sql", "cas"])
def test_history_accounting_failure_preserves_cache_and_atomic_quantity(live: Any, monkeypatch: Any, failure: str) -> None:
    record = owned(live)
    snap = observed(record)
    before = live.store.get_position("position-1")
    cache = dict(live.runtime._projections)
    if failure == "authority":
        require = live.owner.require_history_receipt
        calls = 0
        def expire(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 2:
                live.owner.discard_history_receipt(args[0])
            return require(*args, **kwargs)
        monkeypatch.setattr(live.owner, "require_history_receipt", expire)
    elif failure == "sql":
        with sqlite3.connect(live.kwargs["lock_directory"] / "runtime-state.sqlite3") as db:
            db.execute("CREATE TRIGGER fail_inbox BEFORE INSERT ON runtime_inbox BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    else:
        apply = live.store.apply_position_event
        def concurrent(*args: Any, **kwargs: Any) -> Any:
            monkeypatch.setattr(live.store, "apply_position_event", apply)
            live.runtime._persist(("READBACK", "concurrent", ()), live.runtime.projection("position-1"))
            return apply(*args, **kwargs)
        monkeypatch.setattr(live.store, "apply_position_event", concurrent)
    with pytest.raises((ValueError, sqlite3.Error)):
        recover(live, snap)
    assert live.store.get_inbox_event(execution_guard(SCOPE, "BTCUSDT-LINEAR.BYBIT", "fill-1")) is None
    if failure != "cas":
        assert live.store.get_position("position-1") == before and live.runtime._projections == cache
    assert live.runtime.projection("position-1").owned_quantity == 0
    monkeypatch.undo()
    if failure == "sql":
        with sqlite3.connect(live.kwargs["lock_directory"] / "runtime-state.sqlite3") as db:
            db.execute("DROP TRIGGER fail_inbox")
    assert recover(live, replace(snap, snapshot_id="retry")).status == "CREDITED"
    assert live.runtime.projection("position-1").owned_quantity == Decimal("0.4")


def test_old_order_immutable_quantity_contradiction_keeps_original_source(live: Any) -> None:
    record = owned(live)
    snap = observed(record)
    old = replace(snap, snapshot_id="old-order", executions=(), orders=(replace(snap.orders[0],
        quantity=Decimal(2), leaves_quantity=Decimal("1.6")),), transport_injected=True)
    old_key = live.history.ingest(old).observation_key
    assert recover(live, snap).status == "CONFLICT"
    decision = codec.decode(live.store.list_position_events("position-1")[-1].payload_json)
    assert any(item.observation_key == old_key and item.transport_injected for item in decision.order_sources)
    assert live.runtime.projection("position-1").owned_quantity == 0


def test_partial_fills_and_explicit_exit_respect_cumulative_exposure(live: Any) -> None:
    from multi_exchange_trader.execution.journal import CommandKind
    from multi_exchange_trader.execution.protection import OwnedOrder

    record = owned(live)
    snap = observed(record)
    assert recover(live, snap).status == "CREDITED"
    second = replace(snap, snapshot_id="second-fill", executions=(replace(snap.executions[0],
        execution_id="fill-2", quantity=Decimal("0.1"), value=Decimal(10), time_ms=NS // 1_000_000 + 1),),
        orders=(replace(snap.orders[0], updated_ms=NS // 1_000_000 + 1,
            cumulative_filled=Decimal("0.5"), leaves_quantity=Decimal("0.5")),))
    assert recover(live, second, "fill-2").status == "CREDITED"
    state = live.runtime.projection("position-1")
    exit_command = live.journal.register("exit", CommandKind.EXIT, {"position_id": "position-1",
        "quantity": "0.5", "reference_price": "100", "policy_revision": state.orders[0].policy_revision})
    exit_owned = OwnedOrder(exit_command.client_order_id, "position-1", state.instrument_id,
        "SELL", "EXIT", Decimal("0.5"), True, 0, state.orders[0].policy_revision)
    live.runtime._register(state, exit_owned)
    live.journal.claim("exit")
    exit_snap = replace(snap, snapshot_id="exit", executions=(replace(snap.executions[0],
        execution_id="exit-fill", client_order_id=exit_command.client_order_id, venue_order_id="exit-venue",
        side="Sell", quantity=Decimal("0.5"), value=Decimal(50)),), orders=(replace(snap.orders[0],
        venue_order_id="exit-venue", client_order_id=exit_command.client_order_id, side="Sell",
        quantity=Decimal("0.5"), cumulative_filled=Decimal("0.5"), leaves_quantity=Decimal(0), reduce_only=True),))
    assert recover(live, exit_snap, "exit-fill").status == "CREDITED"
    assert live.runtime.projection("position-1").owned_quantity == 0
    overfill = replace(exit_snap, snapshot_id="exit-overfill", executions=(replace(exit_snap.executions[0],
        execution_id="exit-extra", quantity=Decimal("0.1"), value=Decimal(10)),))
    assert recover(live, overfill, "exit-extra").status == "CONFLICT"
    assert live.store.get_inbox_event(execution_guard(SCOPE, state.instrument_id, "exit-extra")) is None


def test_history_decision_cannot_be_bypassed_by_later_native_candidate(live: Any) -> None:
    record = owned(live)
    snap = observed(record)
    bad = replace(snap, orders=(replace(snap.orders[0], position_idx=1),))
    assert recover(live, bad).status == "CONFLICT"
    consume(live.runtime, record.client_order_id, "OrderFilled")
    candidate = live.runtime.persisted_candidates()[0]
    result = credit(live, candidate, replace(snapshot(candidate), snapshot_id="native-after-decision"))
    assert result.status == "CONFLICT" and "EXECUTION_DECISION_CONFLICT" in result.reason_codes
    assert live.runtime.projection("position-1").owned_quantity == 0


def test_global_history_guard_refuses_other_owned_position(live: Any) -> None:
    record = owned(live)
    snap = observed(record)
    assert recover(live, snap).status == "CREDITED"
    command = live.journal.register("second-entry", record.kind,
        {**json.loads(record.payload_json), "position_id": "a-position"})
    live.runtime._recover_entry_mapping(command)
    live.journal.claim(command.command_key)
    second = replace(snap, snapshot_id="other-position", executions=(replace(snap.executions[0],
        client_order_id=command.client_order_id, venue_order_id="venue-2"),), orders=(replace(snap.orders[0],
        client_order_id=command.client_order_id, venue_order_id="venue-2"),))
    assert recover(live, second).status == "CONFLICT"
    assert live.runtime.projection("a-position").owned_quantity == 0
    assert live.runtime.projection("position-1").owned_quantity == Decimal("0.4")
    live.runtime.shutdown()
    live.runtime = live.module.NativeExecutionRuntime(**live.kwargs)
    assert live.runtime.projection("a-position").owned_quantity == 0


@pytest.mark.parametrize("change", ["price", "quantity", "time", "client", "venue", "variant"])
def test_history_first_native_mismatch_and_variant_never_resolve(live: Any, change: str) -> None:
    record = owned(live)
    snap = observed(record)
    # Native's ordinary fee matches this baseline; variants differ only in their claimed source.
    snap = replace(snap, executions=(replace(snap.executions[0], fee=Decimal("0.01")),))
    assert recover(live, snap).status == "CREDITED"
    changes = {"price": {"last_px": Decimal(101)}, "quantity": {"last_qty": Decimal("0.3")},
        "time": {"ts_event": NS + 1}, "client": {}, "venue": {"venue_order_id": "other"},
        "variant": {}}[change]
    consume(live.runtime, record.client_order_id, "OrderFilled", **changes)
    if change == "variant":
        consume(live.runtime, record.client_order_id, "OrderFilled", reconciliation=True, last_qty=Decimal("0.3"))
    candidate = live.runtime.persisted_candidates()[0]
    current = replace(snap, snapshot_id="late-mismatch", orders=())
    if change == "client":
        current = replace(current, executions=(replace(current.executions[0], client_order_id=""),))
    receipt, key = issue(live, current)
    result = live.runtime.corroborate_execution(receipt=receipt, binding=live.binding,
        observation_key=key, candidate_id=candidate_identity(candidate))
    assert result.status == "CONFLICT"
    assert live.runtime.projection("position-1").owned_quantity == Decimal("0.4")
    assert not live.runtime._projections["position-1"][3].history.resolutions


def test_exact_source_distinct_native_variants_resolve_individually(live: Any) -> None:
    record = owned(live)
    snap = observed(record)
    snap = replace(snap, executions=(replace(snap.executions[0], fee=Decimal("0.01")),))
    assert recover(live, snap).status == "CREDITED"
    consume(live.runtime, record.client_order_id, "OrderFilled")
    consume(live.runtime, record.client_order_id, "OrderFilled", reconciliation=True)
    candidates = live.runtime.persisted_candidates()
    for index, candidate in enumerate(candidates):
        receipt, key = issue(live, replace(snap, snapshot_id=f"variant-{index}"))
        assert live.runtime.corroborate_execution(receipt=receipt, binding=live.binding,
            observation_key=key, candidate_id=candidate_identity(candidate)).status == "DUPLICATE"
        marked = any(item.reason == "AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED"
            for item in live.runtime.projection("position-1").conflicts)
        assert marked == (index == 0)
    assert live.runtime.projection("position-1").owned_quantity == Decimal("0.4")


def test_final_lease_revocation_prevents_history_commit(live: Any, monkeypatch: Any) -> None:
    record = owned(live)
    before = live.store.get_position("position-1")
    require = live.owner.require_history_receipt
    calls = 0
    def revoke(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        view = require(*args, **kwargs)
        if calls == 2:
            live.runtime.worker_lock.release()
        return view
    monkeypatch.setattr(live.owner, "require_history_receipt", revoke)
    with pytest.raises(ValueError, match="ACCOUNT_WORKER_LEASE_REQUIRED"):
        recover(live, observed(record))
    assert live.store.get_position("position-1") == before


@pytest.mark.parametrize("changes,reason", [
    ({"client_order_id": ""}, "HISTORY_CLIENT_ORDER_ID_REQUIRED"),
    ({"execution_type": "Funding", "venue_order_id": ""}, "HISTORY_TRADE_REQUIRED"),
    ({"execution_type": "AdlTrade"}, "HISTORY_TRADE_REQUIRED"),
    ({"client_order_id": "unowned"}, "OWNED_COMMAND_INELIGIBLE"),
])
def test_unowned_or_unsupported_new_history_never_changes_projection(live: Any, changes: Any, reason: str) -> None:
    record = owned(live)
    snap = observed(record)
    before = live.store.get_position("position-1")
    result = recover(live, replace(snap, orders=(), executions=(replace(snap.executions[0], **changes),)))
    assert result.status == "UNRESOLVED" and reason in result.reason_codes
    assert live.store.get_position("position-1") == before


@pytest.mark.parametrize("change", ["digest", "summary", "prior_type"])
def test_tampered_resolution_or_prior_fails_restore_without_writes(live: Any, change: str) -> None:
    from multi_exchange_trader.execution.history_recovery import history_resolution_event_id

    record = owned(live)
    snap = observed(record)
    snap = replace(snap, executions=(replace(snap.executions[0], fee=Decimal("0.01")),))
    assert recover(live, snap).status == "CREDITED"
    consume(live.runtime, record.client_order_id, "OrderFilled")
    candidate = live.runtime.persisted_candidates()[0]
    receipt, key = issue(live, replace(snap, snapshot_id="late"))
    assert live.runtime.corroborate_execution(receipt=receipt, binding=live.binding,
        observation_key=key, candidate_id=candidate_identity(candidate)).status == "DUPLICATE"
    with sqlite3.connect(live.kwargs["lock_directory"] / "runtime-state.sqlite3") as db:
        if change == "digest":
            item = next(item for item in live.store.list_position_events("position-1")
                if item.event_id.startswith("history-resolution:"))
            bad = replace(codec.decode(item.payload_json), credit_digest="f" * 64)
            db.execute("UPDATE runtime_inbox SET event_id=?,event_payload=? WHERE event_id=?",
                (history_resolution_event_id(bad), json.dumps(json.loads(codec.encode(bad)),
                    sort_keys=True, separators=(",", ":")), item.event_id))
        elif change == "summary":
            summary = codec.decode(live.store.get_position("position-1").payload_json)
            bad_summary = (*summary[:22], "f" * 64)
            db.execute("UPDATE runtime_positions SET payload=?", (json.dumps(json.loads(codec.encode(bad_summary)),
                sort_keys=True, separators=(",", ":")),))
        else:
            guard = execution_guard(SCOPE, "BTCUSDT-LINEAR.BYBIT", "fill-1")
            db.execute("UPDATE runtime_inbox SET event_payload=? WHERE event_id=?",
                (json.dumps(json.loads(codec.encode(live.runtime.projection("position-1").orders[0])),
                    sort_keys=True, separators=(",", ":")), guard))
    before = live.store.list_position_events("position-1")
    live.runtime.shutdown()
    with pytest.raises(ValueError):
        live.module.NativeExecutionRuntime(**live.kwargs)
    assert live.store.list_position_events("position-1") == before
