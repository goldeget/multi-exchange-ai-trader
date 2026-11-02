"""Synthetic truthful history evidence and pure accounting replay; no live authority."""

import hashlib
import importlib
import importlib.util
import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any

import pytest
from test_runtime import queue
from test_runtime import setup as setup  # noqa: PLC0414

from multi_exchange_trader.execution import codec
from multi_exchange_trader.execution import protection as p
from multi_exchange_trader.execution.execution_credit import (
    CreditReplayState,
    ExecutionObservationReference,
    candidate_identity,
    execution_guard,
)
from multi_exchange_trader.execution.history import ExecutionRecord, OrderHistoryRecord
from multi_exchange_trader.execution.history_store import _variant
from multi_exchange_trader.execution.runtime import NativeExecutionRuntime, _ReplayContext


def api() -> Any:
    name = "multi_exchange_trader.execution.history_recovery"
    assert importlib.util.find_spec(name) is not None, "truthful historical replay module required"
    return importlib.import_module(name)


def source() -> ExecutionObservationReference:
    return ExecutionObservationReference(p.ProtectionScope("fixture-account", "DEMO"),
        "binding", "a" * 64, "history", "BTCUSDT", 1, 2000,
        datetime(2026, 11, 2, tzinfo=UTC), datetime(2026, 11, 2, tzinfo=UTC))


def execution(**changes: Any) -> ExecutionRecord:
    return replace(ExecutionRecord("exec-1", "venue-1", "client-1", "BTCUSDT", "Buy", "Trade",
        1000, Decimal("0.00400"), Decimal("50001.500"), Decimal("200.006"),
        Decimal("-0.01000"), "USDT", Decimal("-0.00005"), Decimal(0)), **changes)


def order(**changes: Any) -> OrderHistoryRecord:
    return replace(OrderHistoryRecord("venue-1", "client-1", "BTCUSDT", "Buy", 0,
        "PartiallyFilled", Decimal("0.020"), Decimal("0.004"), Decimal("0.016"),
        False, 500, 1000), **changes)


def evidence(row: ExecutionRecord | None = None, ref: Any = None) -> Any:
    row = execution() if row is None else row
    return api().HistoryExecutionEvidence(source() if ref is None else ref, row, _variant(row)[2])


def order_evidence(row: OrderHistoryRecord | None = None, ref: Any = None) -> Any:
    row = order() if row is None else row
    return api().HistoryOrderEvidence(source() if ref is None else ref, row, _variant(row)[2])


def credit(**changes: Any) -> Any:
    owned = p.OwnedOrder("client-1", "logical-position", "BTCUSDT-LINEAR.BYBIT", "BUY", "ENTRY",
        Decimal("0.020"), False, 0, "policy")
    return replace(api().HistoryExecutionCredit(evidence(), order_evidence(), owned,
        "command", "b" * 64), **changes)


def projection(event: Any = None) -> p.ProtectionProjection:
    event = credit() if event is None else event
    owned = event.owned_order
    return p.ProtectionProjection(source().scope, owned.position_id, owned.instrument_id,
        "BUY", (owned,))


def reduce(state: Any, event: Any, replay: Any = None, candidates: Any = (),
           native: Any = None) -> Any:
    return api().reduce_history_event(state, event, candidates=candidates,
        native_replay=CreditReplayState() if native is None else native,
        replay=api().HistoryRecoveryReplayState() if replay is None else replay)


def decision(event: Any = None, **changes: Any) -> Any:
    event = credit() if event is None else event
    return replace(api().HistoryRecoveryConflict(event.execution, event.owned_order,
        event.command_key, event.command_payload_hash, ("PRICE_MISMATCH",), (), (), None, None),
        **changes)


def late_candidate() -> Any:
    from test_execution_provenance import candidate

    original = candidate()
    return replace(original, venue_order_id="venue-1", fill=replace(original.fill,
        execution_id="exec-1", ts_event_ns=1000000000, instrument_id="BTCUSDT-LINEAR.BYBIT"))


@pytest.mark.parametrize("stage", ["construction", "reducer"])
def test_positive_credit_rejects_historical_symbol_different_from_owned_instrument(stage: str) -> None:
    from dataclasses import fields

    event = credit()
    wrong_source = replace(source(), symbol="ETHUSDT")
    wrong_execution = evidence(execution(symbol="ETHUSDT"), wrong_source)
    wrong_order = order_evidence(order(symbol="ETHUSDT"), wrong_source)
    with pytest.raises(ValueError, match="HISTORY_RECOVERY_CREDIT_INVALID"):
        if stage == "construction":
            replace(event, execution=wrong_execution, order=wrong_order)
        else:
            # A reflected object must still fail the reducer's independent invariant check.
            malformed = object.__new__(type(event))
            for field in fields(event):
                object.__setattr__(malformed, field.name, getattr(event, field.name))
            object.__setattr__(malformed, "execution", wrong_execution)
            object.__setattr__(malformed, "order", wrong_order)
            reduce(projection(event), malformed)


def test_conflict_keeps_contrary_observed_symbol_without_changing_owned_instrument() -> None:
    event = credit()
    contrary = evidence(execution(symbol="ETHUSDT"), replace(source(), symbol="ETHUSDT"))
    record = decision(event, execution=contrary, mismatch_codes=("INSTRUMENT_ID_MISMATCH",))
    state, _, replay = reduce(projection(event), record)
    assert state.owned_quantity == 0 and replay.decisions[0].execution.execution.symbol == "ETHUSDT"
    assert replay.decisions[0].owned_order.instrument_id == "BTCUSDT-LINEAR.BYBIT"


def resolution(event: Any = None, candidate: Any = None) -> Any:
    event = credit() if event is None else event
    candidate = late_candidate() if candidate is None else candidate
    return api().NativeHistoryResolution(candidate_identity(candidate), execution_guard(
        source().scope, event.owned_order.instrument_id, event.execution.execution.execution_id),
        api().history_credit_digest(event), event.execution)


def test_records_roundtrip_closed_fields_and_no_authority() -> None:
    records = (evidence(), order_evidence(), credit(), decision(), resolution(),
               api().HistoryRecoveryResult("exec-1", "CREDITED", ()))
    for record in records:
        assert codec.decode(codec.encode(record)) == record
        assert not hasattr(record, "credit_authorized")
        with pytest.raises(FrozenInstanceError):
            record.authority = True
        raw = json.loads(codec.encode(record))
        for name in tuple(raw["fields"]):
            missing = json.loads(codec.encode(record))
            del missing["fields"][name]
            with pytest.raises(ValueError, match="missing or extra record fields"):
                codec.decode(json.dumps(missing))
        raw["fields"]["authority"] = True
        with pytest.raises(ValueError, match="missing or extra record fields"):
            codec.decode(json.dumps(raw))
    with pytest.raises(TypeError):
        codec.encode(api().HistoryRecoveryReplayState())


@pytest.mark.parametrize("change", ["digest", "row", "window", "symbol", "currency", "kind"])
def test_wrapper_binds_full_normalized_row_and_existing_parser(change: str) -> None:
    with pytest.raises(ValueError):
        if change == "digest":
            replace(evidence(), execution_variant_hash="f" * 64)
        elif change == "row":
            replace(evidence(), execution=execution(value=Decimal(201)))
        elif change == "window":
            replace(evidence(), source=replace(source(), end_ms=999))
        elif change == "symbol":
            replace(evidence(), source=replace(source(), symbol="ETHUSDT"))
        elif change == "currency":
            evidence(execution(fee_currency="USDC"))
        else:
            evidence(execution(execution_type="Delivery"))


@pytest.mark.parametrize("change", ["source", "client", "venue", "quantity", "side", "idx",
                                   "reduce", "role", "payload", "command", "trade", "blank"])
def test_positive_credit_requires_exact_same_source_owned_order_proof(change: str) -> None:
    event = credit()
    with pytest.raises(ValueError):
        if change == "source":
            replace(event, order=order_evidence(ref=replace(source(), snapshot_id="old")))
        elif change in ("client", "venue", "quantity", "side", "idx", "reduce"):
            mapping = {"client": {"client_order_id": "other"},
                "venue": {"venue_order_id": "other"}, "quantity": {"quantity": Decimal(1)},
                "side": {"side": "Sell"}, "idx": {"position_idx": 1}, "reduce": {"reduce_only": True}}
            replace(event, order=order_evidence(order(**mapping[change])))
        elif change == "role":
            replace(event, owned_order=replace(event.owned_order, role="STOP",
                trigger_price=Decimal(1), trigger_source="MARK"))
        elif change == "payload":
            replace(event, command_payload_hash="B" * 64)
        elif change == "command":
            replace(event, command_key="")
        elif change == "trade":
            replace(event, execution=evidence(execution(execution_type="AdlTrade")))
        else:
            replace(event, execution=evidence(execution(client_order_id="")))


def test_history_credit_preserves_exact_source_time_and_economics_without_native() -> None:
    event = credit()
    with localcontext() as ctx:
        ctx.prec = 2
        state, native, replay = reduce(projection(), event)
    assert state.owned_quantity == Decimal("0.004")
    assert state.executions[0].occurred_at == datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)
    assert native == CreditReplayState()
    assert replay.credits == (event,)
    assert str(replay.credits[0].execution.execution.fee) == "-0.01000"
    assert str(replay.credits[0].execution.execution.price) == "50001.500"
    assert not hasattr(replay.credits[0], "candidate")
    assert not hasattr(replay.credits[0], "ts_init_ns")


def test_duplicate_preserves_first_source_and_all_normalized_fields_constrain_equality() -> None:
    event = credit()
    state, native, replay = reduce(projection(), event)
    fresh_source = replace(source(), snapshot_id="fresh", observed_at=source().observed_at + timedelta(seconds=1))
    fresh = replace(event, execution=evidence(ref=fresh_source), order=order_evidence(ref=fresh_source))
    assert reduce(state, fresh, replay, native=native) == (state, native, replay)
    for name, value in (("value", Decimal(201)), ("fee_rate", Decimal("0.1")),
                        ("closed_quantity", Decimal("0.001"))):
        changed = replace(event, execution=evidence(execution(**{name: value})))
        reduced, _, observed = reduce(state, changed, replay)
        assert reduced.executions == state.executions and observed.credits == (event,)
        assert observed.decisions[0].mismatch_codes == ("HISTORY_EXECUTION_CONFLICT",)


@pytest.mark.parametrize("mode,code", [("native", "NATIVE_CORROBORATION_REQUIRED"),
    ("overfill", "FILL_EXCEEDS_ORDER_INTENT"), ("exit", "EXIT_EXCEEDS_OWNED_EXPOSURE"),
    ("decision", "EXECUTION_DECISION_CONFLICT")])
def test_refusals_retain_sourced_decision_and_no_credit(mode: str, code: str) -> None:
    event = credit()
    candidates: tuple[Any, ...] = ()
    previous = api().HistoryRecoveryReplayState()
    if mode == "native":
        candidates = (late_candidate(),)
    elif mode == "overfill":
        event = replace(event, execution=evidence(execution(quantity=Decimal("0.021"))))
    elif mode == "exit":
        event = replace(event, owned_order=replace(event.owned_order, role="EXIT", side="SELL", reduce_only=True),
            execution=evidence(execution(side="Sell")), order=order_evidence(order(side="Sell", reduce_only=True)))
    else:
        previous = replace(previous, decisions=(decision(),))
    state, native, replay = reduce(projection(event), event, previous, candidates)
    assert state.executions == () and replay.credits == () and native == CreditReplayState()
    assert replay.decisions[-1].mismatch_codes == (code,)
    assert replay.decisions[-1].execution == event.execution


def test_registered_exact_owned_mapping_is_required() -> None:
    event = credit()
    for state in (replace(projection(), orders=()), replace(projection(),
        orders=(replace(event.owned_order, policy_revision="other"),))):
        with pytest.raises(ValueError, match="HISTORY_RECOVERY_OWNERSHIP_REQUIRED"):
            reduce(state, event)


def test_conflict_retains_missing_observed_ids_and_immutable_target() -> None:
    event = credit()
    original, native, prior = reduce(projection(), event)
    observed = evidence(execution(execution_type="Funding", client_order_id="", venue_order_id="",
        quantity=Decimal(0), price=Decimal(0), value=Decimal(0)))
    record = decision(event, execution=observed, prior_credit_event_id=execution_guard(
        source().scope, event.owned_order.instrument_id, "exec-1"))
    state, _, replay = reduce(original, record, prior, native=native)
    assert state.executions == original.executions and replay.decisions == (record,)
    assert state.owned_quantity == Decimal("0.004") and replay.credits == (event,)
    assert record.execution.execution.quantity == 0
    assert record.execution.execution.client_order_id == ""
    assert record.owned_order.client_order_id == "client-1"
    assert reduce(state, record, replay)[2] == replay


@pytest.mark.parametrize("change", ["codes", "duplicate", "keys", "candidate", "prior", "execution"])
def test_conflict_and_result_fixed_bounds(change: str) -> None:
    with pytest.raises(ValueError):
        if change == "codes":
            decision(mismatch_codes=("provider-secret",))
        elif change == "duplicate":
            decision(mismatch_codes=("PRICE_MISMATCH",) * 2)
        elif change == "keys":
            decision(history_conflict_keys=("a" * 64,) * 257)
        elif change == "candidate":
            decision(candidate_id="BAD")
        elif change == "prior":
            decision(prior_credit_event_id="credit:" + "f" * 64)
        else:
            api().HistoryRecoveryResult("", "CREDITED", ())
    with pytest.raises(ValueError):
        api().HistoryRecoveryResult("exec-1", "READY", ())


def test_late_resolution_uses_real_exact_candidate_and_keeps_original_accounting() -> None:
    event = credit()
    state, native, replay = reduce(projection(), event)
    candidate = late_candidate()
    state = replace(state, conflicts=(p.ProjectionConflict("UNRELATED"), p.ProjectionConflict(
        "AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED", client_order_id="client-1")))
    result = resolution(event, candidate)
    reduced, native, replay = reduce(state, result, replay, (candidate,), native)
    assert reduced.executions == state.executions and replay.credits == (event,)
    assert tuple(item.reason for item in reduced.conflicts) == ("UNRELATED",)
    assert native.credits == () and native.resolved_candidate_ids == (candidate_identity(candidate),)
    assert replay.resolutions == (result,)
    assert reduce(reduced, result, replay, (candidate,), native) == (reduced, native, replay)


@pytest.mark.parametrize("change", ["missing", "digest", "prior", "price", "nanoseconds", "observed"])
def test_late_resolution_rejects_wrong_prior_digest_candidate_or_shared_values(change: str) -> None:
    event = credit()
    state, native, replay = reduce(projection(), event)
    candidate = late_candidate()
    record = resolution(event, candidate)
    candidates = (candidate,)
    if change == "missing":
        candidates = ()
    elif change == "digest":
        record = replace(record, credit_digest="f" * 64)
    elif change == "prior":
        record = replace(record, prior_credit_event_id="credit:" + "f" * 64)
    elif change in ("price", "nanoseconds"):
        candidate = replace(candidate, fill=replace(candidate.fill,
            **({"price": Decimal("50001.6")} if change == "price" else {"ts_event_ns": 1000000001})))
        candidates = (candidate,)
        record = replace(record, candidate_id=candidate_identity(candidate))
    else:
        record = replace(record, execution=evidence(execution(value=Decimal(201))))
    with pytest.raises(ValueError, match="HISTORY_RECOVERY_RESOLUTION_INVALID"):
        reduce(state, record, replay, candidates, native)


def test_resolution_only_clears_order_marker_after_all_candidate_variants_resolved() -> None:
    event = credit()
    state, native, replay = reduce(projection(), event)
    candidate = late_candidate()
    other = replace(candidate, fill=replace(candidate.fill, native_reconciliation=True))
    marker = p.ProjectionConflict("AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED", client_order_id="client-1")
    state = replace(state, conflicts=(marker,))
    state, native, replay = reduce(state, resolution(), replay, (candidate, other), native)
    assert marker in state.conflicts
    state, native, replay = reduce(state, resolution(candidate=other), replay, (candidate, other), native)
    assert state.conflicts == () and len(native.resolved_candidate_ids) == 2


def test_v4_has_exact_23_items_complete_record_digests_and_pure_dispatch() -> None:
    event = credit()
    context = _ReplayContext()
    state = NativeExecutionRuntime._reduce_event(projection(), event, {}, context)
    summary = NativeExecutionRuntime._summary(state, {}, context.credit, context.history)
    assert len(summary) == 23 and summary[0] == "RUNTIME_POSITION_V4"
    assert summary[11:14] == (0, hashlib.sha256(b"").hexdigest(), 0)
    assert summary[17] == 1 and summary[19] == summary[21] == 0
    altered = replace(context.history, credits=(replace(event, command_payload_hash="c" * 64),))
    assert NativeExecutionRuntime._summary(state, {}, context.credit, altered)[18] != summary[18]
    decision_record = decision()
    decided = replace(context.history, decisions=(decision_record,))
    changed = replace(decided, decisions=(replace(decision_record, history_conflict_keys=("d" * 64,)),))
    assert NativeExecutionRuntime._summary(state, {}, context.credit, decided)[20] != NativeExecutionRuntime._summary(state, {}, context.credit, changed)[20]
    resolved = replace(context.history, resolutions=(resolution(),))
    changed_resolution = replace(resolved, resolutions=(replace(resolution(), execution=evidence(
        ref=replace(source(), snapshot_id="fresh"))),))
    assert NativeExecutionRuntime._summary(state, {}, context.credit, resolved)[22] != NativeExecutionRuntime._summary(state, {}, context.credit, changed_resolution)[22]


def test_generic_persist_rejects_history_authority_records_before_writes() -> None:
    from multi_exchange_trader.execution.runtime import RuntimeBlocked

    runtime = object.__new__(NativeExecutionRuntime)
    runtime._guard = lambda: None
    for event in (credit(), decision(), resolution()):
        with pytest.raises(RuntimeBlocked, match="AUTHENTICATED_EXECUTION_RECEIPT_REQUIRED"):
            runtime._persist(event, projection())


def test_content_address_helpers_cover_full_original_records() -> None:
    event = credit()
    assert api().history_credit_digest(event) == hashlib.sha256(codec.encode(event).encode()).hexdigest()
    assert api().history_credit_digest(replace(event, command_payload_hash="c" * 64)) != api().history_credit_digest(event)
    conflict = decision()
    assert api().history_decision_event_id(conflict) == "history-decision:" + hashlib.sha256(codec.encode(conflict).encode()).hexdigest()
    assert api().history_decision_event_id(replace(conflict, execution=evidence(
        ref=replace(source(), snapshot_id="other")))) != api().history_decision_event_id(conflict)
    resolved = resolution()
    assert api().history_resolution_event_id(resolved) == "history-resolution:" + hashlib.sha256(codec.encode(resolved).encode()).hexdigest()
    assert api().history_resolution_event_id(replace(resolved, credit_digest="f" * 64)) != api().history_resolution_event_id(resolved)


@pytest.mark.parametrize("target", ["source", "execution", "owned_order", "codes", "digest"])
def test_codec_rejects_wrong_field_types(target: str) -> None:
    record = {"source": evidence(), "execution": credit(), "owned_order": credit(),
              "codes": decision(), "digest": resolution()}[target]
    raw = json.loads(codec.encode(record))
    field = {"codes": "mismatch_codes", "digest": "credit_digest"}.get(target, target)
    raw["fields"][field] = True
    with pytest.raises(ValueError, match="explicit schema"):
        codec.decode(json.dumps(raw))


def test_native_prior_guard_deduplicates_only_same_owned_target_and_real_candidate() -> None:
    from test_execution_credit import replay_credit

    event = credit()
    candidate = late_candidate()
    native_credit = replay_credit(candidate)
    native = CreditReplayState(credits=(native_credit,))
    state, native_result, replay = reduce(projection(), event, candidates=(candidate,), native=native)
    assert state.executions == () and replay == api().HistoryRecoveryReplayState()
    assert native_result == native
    other = replace(candidate, fill=replace(candidate.fill, position_id="other-position"))
    native = CreditReplayState(credits=(replay_credit(other),))
    _, _, replay = reduce(projection(), event, candidates=(other,), native=native)
    assert not replay.credits and replay.decisions[0].mismatch_codes == ("EXECUTION_GUARD_CONFLICT",)
    with pytest.raises(ValueError, match="HISTORY_RECOVERY_EXACT_CANDIDATE_REQUIRED"):
        reduce(projection(), event, native=CreditReplayState(credits=(native_credit,)))


def test_native_credit_cannot_replay_after_history_guard() -> None:
    from test_execution_credit import replay_credit

    from multi_exchange_trader.execution.runtime import RuntimeBlocked

    context = _ReplayContext()
    state = NativeExecutionRuntime._reduce_event(projection(), credit(), {}, context)
    candidate = late_candidate()
    context.candidates = (candidate,)
    with pytest.raises(RuntimeBlocked, match="INVALID_DURABLE_CREDIT_GUARD"):
        NativeExecutionRuntime._reduce_event(state, replay_credit(candidate), {}, context)


def test_v4_reopen_recomputes_and_later_generic_writes_preserve_history(setup: Any) -> None:
    runtime, _, store, _, _, _ = setup
    command = queue(runtime)
    state = runtime.projection("position-1")
    owned = state.orders[0]
    ref = replace(source(), scope=state.scope)
    event = credit(owned_order=owned, command_key=command.command_key,
        command_payload_hash=command.payload_hash,
        execution=evidence(execution(client_order_id=owned.client_order_id), ref),
        order=order_evidence(order(client_order_id=owned.client_order_id, quantity=owned.quantity,
            leaves_quantity=owned.quantity - Decimal("0.004")), ref))
    reduced, native, replay = reduce(state, event)
    guard = execution_guard(state.scope, state.instrument_id, "exec-1")
    store.apply_position_event(guard, json.loads(codec.encode(event)), state.position_id, 1,
        json.loads(codec.encode(NativeExecutionRuntime._summary(reduced, {}, native, replay))))
    runtime._projections.clear()
    assert runtime._states() == (reduced,)
    runtime._persist(("READBACK", "later", ()), reduced)
    runtime._projections.clear()
    assert runtime._states() == (reduced,)
    summary = codec.decode(store.get_position(state.position_id).payload_json)
    assert summary[0] == "RUNTIME_POSITION_V4" and summary[17] == 1
