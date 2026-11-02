"""Synthetic private-registry credit fixtures: actual authenticated transport is unverified.

Only tests seed private session slots. Public injected transport remains forbidden;
no provider/native effects, real stores, authority callback or production test issuer.
"""

import json
import sqlite3
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from test_runtime import NOW, NS, SCOPE, consume, queue
from test_runtime import setup as setup  # noqa: PLC0414

from multi_exchange_trader.execution import codec
from multi_exchange_trader.execution.account_binding import (
    AuthenticatedAccountBinding,
    BybitAccountIdentity,
    BybitDemoAccountSession,
    _HistoryReceiptSlot,
)
from multi_exchange_trader.execution.execution_credit import candidate_identity, execution_guard
from multi_exchange_trader.execution.history import ExecutionRecord, HistorySnapshot, HistoryWindow
from multi_exchange_trader.execution.history_receipts import (
    AuthenticatedHistoryReceipt,
    HistoryReceiptView,
)
from multi_exchange_trader.execution.history_store import HistoryStore
from multi_exchange_trader.execution.protection import ProtectionScope

OBSERVED = NOW + timedelta(seconds=2)


@pytest.fixture
async def live(setup: Any) -> Any:
    old, journal, store, strategy, kwargs, module = setup
    owner = BybitDemoAccountSession(api_key="synthetic-key", api_secret="synthetic-secret",
        clock=lambda: OBSERVED, monotonic=lambda: 10.0)
    binding = object.__new__(AuthenticatedAccountBinding)
    owner._binding = binding
    owner._identity = BybitAccountIdentity(SCOPE.account_id, "0", False, True,
        (("Wallet", ()),), NOW, NOW, False)
    owner._issued_mono = 10.0
    history = HistoryStore(kwargs["lock_directory"] / "history.sqlite3", SCOPE)
    old.shutdown()
    kwargs = {**kwargs, "receipt_session": owner, "history_store": history}
    runtime = module.NativeExecutionRuntime(**kwargs)
    core = SimpleNamespace(runtime=runtime, journal=journal, store=store, strategy=strategy,
        owner=owner, binding=binding, history=history, kwargs=kwargs, module=module)
    try:
        yield core
    finally:
        history.close()
        await owner.aclose()


def native(core: Any, **changes: Any) -> Any:
    record = queue(core.runtime)
    core.journal.claim(record.command_key)
    consume(core.runtime, record.client_order_id, "OrderFilled", **changes)
    return core.runtime.persisted_candidates()[0]


def snapshot(candidate: Any, **changes: Any) -> HistorySnapshot:
    fill = candidate.fill
    row = ExecutionRecord(fill.execution_id, candidate.venue_order_id, fill.client_order_id,
        "BTCUSDT", "Buy" if fill.side == "BUY" else "Sell", "Trade", NS // 1_000_000,
        fill.quantity, fill.price, fill.quantity * fill.price, fill.commission,
        fill.commission_currency, Decimal("0.00025"), Decimal(0))
    return HistorySnapshot(SCOPE, "BTCUSDT", "snapshot-1", HistoryWindow(
        NS // 1_000_000 - 1000, NS // 1_000_000 + 1000), OBSERVED, OBSERVED,
        (replace(row, **changes),), (), (), False)


def issue(core: Any, observed: HistorySnapshot) -> tuple[Any, str]:
    """Reflection outside the trust boundary; does not exercise a real GET."""
    receipt = object.__new__(AuthenticatedHistoryReceipt)
    core.owner._history_receipts[receipt] = _HistoryReceiptSlot(core.binding, SCOPE,
        core.owner._history_generation, NOW, 10.0, "synthetic-binding",
        HistoryReceiptView(observed, "synthetic-binding"))
    return receipt, core.history.ingest(observed).observation_key


def credit(core: Any, candidate: Any, observed: HistorySnapshot | None = None) -> Any:
    receipt, key = issue(core, snapshot(candidate) if observed is None else observed)
    return core.runtime.corroborate_execution(receipt=receipt, binding=core.binding,
        observation_key=key, candidate_id=candidate_identity(candidate))


def test_live_exact_credit_and_rebate_preserve_journal_and_first_metadata(live: Any, monkeypatch: Any) -> None:
    candidate = native(live, commission=SimpleNamespace(
        as_decimal=lambda: Decimal("-0.01"), currency="USDT"))
    journal = live.journal.list_commands()
    audit = live.journal.audit_events()

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("receipt credit changed command, terminal or reservation state")

    for name in ("acknowledge", "reject", "mark_unknown", "release_reservation", "claim"):
        monkeypatch.setattr(live.journal, name, forbidden)
    assert credit(live, candidate).status == "CREDITED"
    guard = execution_guard(SCOPE, candidate.fill.instrument_id, candidate.fill.execution_id)
    first = live.store.get_inbox_event(guard)
    assert codec.decode(first.payload_json).candidate.fill.commission == Decimal("-0.01")
    live.runtime._persist(("READBACK", "later", ()), live.runtime.projection("position-1"))
    position = live.store.get_position("position-1")
    assert credit(live, candidate, replace(snapshot(candidate), snapshot_id="snapshot-2")).status == "DUPLICATE"
    assert live.store.get_position("position-1") == position
    assert live.store.get_inbox_event(guard) == first
    assert live.runtime.projection("position-1").owned_quantity == Decimal("0.4")
    assert live.journal.list_commands() == journal
    assert live.journal.audit_events() == audit
    assert not live.strategy.commands


@pytest.mark.parametrize("changes,reason", [
    ({"price": Decimal(101)}, "PRICE_MISMATCH"),
    ({"quantity": Decimal("0.3")}, "QUANTITY_MISMATCH"),
    ({"fee": Decimal("0.02")}, "COMMISSION_MISMATCH"),
    ({"side": "Sell"}, "SIDE_MISMATCH"),
    ({"client_order_id": "manual"}, "CLIENT_ORDER_ID_MISMATCH"),
    ({"venue_order_id": "other"}, "VENUE_ORDER_ID_MISMATCH"),
    ({"time_ms": NS // 1_000_000 + 1}, "TS_EVENT_NS_MISMATCH"),
])
def test_exact_mismatches_persist_decisions_without_credit(live: Any, changes: Any, reason: str) -> None:
    candidate = native(live)
    result = credit(live, candidate, snapshot(candidate, **changes))
    assert result.status == "CONFLICT" and reason in result.reason_codes
    assert live.runtime.projection("position-1").owned_quantity == 0
    assert any(row.event_id.startswith("decision:")
               for row in live.store.list_position_events("position-1"))


def test_final_receipt_failure_never_commits_or_mutates_cache(live: Any, monkeypatch: Any) -> None:
    candidate = native(live)
    before = dict(live.runtime._projections)
    saved = live.store.get_position("position-1")
    require = live.owner.require_history_receipt
    calls = 0

    def expire(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            live.owner.discard_history_receipt(args[0])
        return require(*args, **kwargs)

    monkeypatch.setattr(live.owner, "require_history_receipt", expire)
    with pytest.raises(ValueError, match="AUTHENTICATED_HISTORY_RECEIPT_REQUIRED"):
        credit(live, candidate)
    assert live.runtime._projections == before
    assert live.store.get_position("position-1") == saved
    assert live.history.execution_evidence(
        live.history.ingest(snapshot(candidate)).observation_key, candidate.fill.execution_id)


@pytest.mark.parametrize("changes,reason", [
    ({"client_order_id": ""}, "HISTORY_CLIENT_ORDER_ID_REQUIRED"),
    ({"execution_type": "Funding"}, "HISTORY_TRADE_REQUIRED"),
    ({"execution_type": "AdlTrade"}, "HISTORY_TRADE_REQUIRED"),
    ({"execution_type": "BustTrade"}, "HISTORY_TRADE_REQUIRED"),
])
def test_unowned_or_nontrade_rows_are_unresolved(live: Any, changes: Any, reason: str) -> None:
    candidate = native(live)
    before = live.store.get_position("position-1")
    result = credit(live, candidate, snapshot(candidate, **changes))
    assert result.status == "UNRESOLVED" and result.reason_codes == (reason,)
    assert live.store.get_position("position-1") == before


@pytest.mark.parametrize("state", ["PENDING", "REJECTED", "TERMINAL"])
def test_ineligible_commands_never_credit(live: Any, state: str) -> None:
    if state == "PENDING":
        record = queue(live.runtime)
        consume(live.runtime, record.client_order_id, "OrderFilled")
        candidate = live.runtime.persisted_candidates()[0]
    else:
        candidate = native(live)
        if state == "REJECTED":
            live.journal.reject("entry", "synthetic-rejection")
        else:
            live.journal.acknowledge("entry", "synthetic-ack")
            live.journal.release_reservation("entry", confirmed_terminal=True,
                                            terminal_evidence="synthetic-terminal")
    journal = live.journal.list_commands()
    assert credit(live, candidate).reason_codes == ("OWNED_COMMAND_INELIGIBLE",)
    assert live.runtime.projection("position-1").owned_quantity == 0
    assert live.journal.list_commands() == journal


def test_missing_member_candidate_and_wrong_whole_snapshot(live: Any) -> None:
    candidate = native(live)
    empty = replace(snapshot(candidate), executions=())
    assert credit(live, candidate, empty).reason_codes == ("HISTORY_EXECUTION_REQUIRED",)
    receipt, key = issue(live, empty)
    assert live.runtime.corroborate_execution(receipt=receipt, binding=live.binding,
        observation_key=key, candidate_id="a" * 64).reason_codes == ("PERSISTED_EXECUTION_CANDIDATE_REQUIRED",)
    changed = replace(snapshot(candidate), snapshot_id="different")
    receipt, _ = issue(live, changed)
    with pytest.raises(ValueError, match="HISTORY_OBSERVATION_MISMATCH"):
        live.runtime.corroborate_execution(receipt=receipt, binding=live.binding,
            observation_key=key, candidate_id=candidate_identity(candidate))
    assert live.runtime.projection("position-1").owned_quantity == 0


def test_plus_one_native_nanosecond_cannot_round_into_match(live: Any) -> None:
    candidate = native(live, ts_event=NS + 1)
    assert credit(live, candidate).reason_codes == ("TS_EVENT_NS_MISMATCH",)
    assert live.runtime.projection("position-1").owned_quantity == 0


@pytest.mark.parametrize("field,value", [
    ("fee", Decimal("-0.02")), ("value", Decimal("39.9")),
    ("fee_rate", Decimal("0.0002")), ("closed_quantity", Decimal("0.1")),
    ("time_ms", NS // 1_000_000 - 5000),
])
@pytest.mark.parametrize("already_credited", [False, True])
def test_global_history_conflicts_include_outside_window_and_extra_normalized_fields(
    live: Any, field: str, value: Any, already_credited: bool,
) -> None:
    candidate = native(live)
    original = snapshot(candidate)
    if already_credited:
        assert credit(live, candidate, original).status == "CREDITED"
    else:
        live.history.ingest(original)
    altered = replace(snapshot(candidate, **{field: value}), snapshot_id="late-history")
    if field == "time_ms":
        altered = replace(altered, window=HistoryWindow(value - 1000, value))
    live.history.ingest(altered)
    result = credit(live, candidate, replace(original, snapshot_id="requery"))
    assert result.status == "CONFLICT" and "HISTORY_EXECUTION_CONFLICT" in result.reason_codes
    assert live.runtime.projection("position-1").owned_quantity == (Decimal("0.4") if already_credited else 0)
    decision = codec.decode(live.store.list_position_events("position-1")[-1].payload_json)
    assert decision.history_conflict_keys
    assert (decision.prior_credit_event_id is not None) == already_credited


def order_member(candidate: Any, **changes: Any) -> Any:
    from multi_exchange_trader.execution.history import OrderHistoryRecord

    return replace(OrderHistoryRecord(candidate.venue_order_id, candidate.fill.client_order_id,
        "BTCUSDT", "Buy", 0, "PartiallyFilled", Decimal(1), Decimal("0.4"),
        Decimal("0.6"), False, NS // 1_000_000, NS // 1_000_000), **changes)


@pytest.mark.parametrize("changes", [
    {"side": "Sell"}, {"position_idx": 1}, {"reduce_only": True},
    {"client_order_id": "other"}, {"venue_order_id": "other"},
])
def test_late_order_member_contradiction_blocks_duplicate(live: Any, changes: Any) -> None:
    candidate = native(live)
    assert credit(live, candidate).status == "CREDITED"
    observed = replace(snapshot(candidate), snapshot_id="with-order",
                       orders=(order_member(candidate, **changes),))
    if "side" in changes or "client_order_id" in changes:
        # Same-order internally contradictory snapshots are rejected by the
        # existing history writer before any live credit route can receive them.
        with pytest.raises(ValueError, match="HISTORY_EXECUTION_ORDER_CONFLICT"):
            credit(live, candidate, observed)
        return
    result = credit(live, candidate, observed)
    assert result.status == "CONFLICT" and "HISTORY_ORDER_CONFLICT" in result.reason_codes
    # Even a later row without the contradictory member cannot erase the decision.
    result = credit(live, candidate, replace(snapshot(candidate), snapshot_id="no-order"))
    assert result.status == "CONFLICT" and "EXECUTION_DECISION_CONFLICT" in result.reason_codes
    assert live.runtime.projection("position-1").owned_quantity == Decimal("0.4")


def test_distinct_source_variants_block_and_two_fills_resolve_individually(live: Any) -> None:
    candidate = native(live)
    consume(live.runtime, candidate.fill.client_order_id, "OrderFilled", trade_id="fill-2",
            last_qty=Decimal("0.1"))
    assert credit(live, candidate).status == "CREDITED"
    assert any(item.reason == "AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED"
               for item in live.runtime.projection("position-1").conflicts)
    second = next(item for item in live.runtime.persisted_candidates() if item.fill.execution_id == "fill-2")
    assert credit(live, second, replace(snapshot(second), snapshot_id="second-fill")).status == "CREDITED"
    assert not any(item.reason == "AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED"
                   for item in live.runtime.projection("position-1").conflicts)
    consume(live.runtime, candidate.fill.client_order_id, "OrderFilled", reconciliation=True)
    result = credit(live, candidate, replace(snapshot(candidate), snapshot_id="late-variant"))
    assert "NATIVE_CANDIDATE_VARIANT_CONFLICT" in result.reason_codes
    assert live.runtime.projection("position-1").owned_quantity == Decimal("0.5")


def test_commit_failure_keeps_caches_and_history_and_retry_reduces_again(live: Any, monkeypatch: Any) -> None:
    candidate = native(live)
    before = dict(live.runtime._projections)
    events = live.store.list_position_events("position-1")
    apply = live.store.apply_position_event
    reductions = 0
    reduce = live.runtime._reduce_event

    def observe(*args: Any, **kwargs: Any) -> Any:
        nonlocal reductions
        reductions += 1
        return reduce(*args, **kwargs)

    def fail(*args: Any, **kwargs: Any) -> Any:
        assert not live.runtime._active and not live.runtime._readbacks
        raise RuntimeError("synthetic-commit-failure")

    monkeypatch.setattr(live.runtime, "_reduce_event", observe)
    monkeypatch.setattr(live.store, "apply_position_event", fail)
    with pytest.raises(RuntimeError, match="synthetic-commit-failure"):
        credit(live, candidate)
    assert live.runtime._projections == before
    assert live.store.list_position_events("position-1") == events
    assert reductions == 1
    monkeypatch.setattr(live.store, "apply_position_event", apply)
    assert credit(live, candidate).status == "CREDITED"
    assert reductions == 2


def test_cross_position_changed_client_and_venue_cannot_recredit_and_replay(live: Any) -> None:
    first = native(live)
    assert credit(live, first).status == "CREDITED"
    original = live.store.get_position("position-1")
    guard = execution_guard(SCOPE, first.fill.instrument_id, first.fill.execution_id)
    original_credit = live.store.get_inbox_event(guard)
    # Seed a second precommitted immutable command/mapping in temporary stores.
    # No dispatch or qualification bypass; this exercises a recovered multi-position state.
    record = live.journal.get("entry")
    payload = {**json.loads(record.payload_json), "position_id": "a-position"}
    second_command = live.journal.register("second-entry", record.kind, payload)
    live.runtime._recover_entry_mapping(second_command)
    live.runtime.bridge.register_owned(live.runtime._request(second_command))
    live.journal.claim("second-entry")
    consume(live.runtime, second_command.client_order_id, "OrderFilled", venue_order_id="venue-2")
    second = next(item for item in live.runtime.persisted_candidates()
                  if item.fill.position_id == "a-position")
    result = credit(live, second, replace(snapshot(second), snapshot_id="second-position"))
    assert result.status == "CONFLICT"
    assert live.runtime.projection("a-position").owned_quantity == 0
    assert live.store.get_position("position-1") == original
    assert live.store.get_inbox_event(guard) == original_credit
    decision = codec.decode(live.store.list_position_events("a-position")[-1].payload_json)
    assert decision.prior_credit_event_id == guard
    assert decision.client_order_id == second_command.client_order_id
    assert decision.venue_order_id == "venue-2"
    live.runtime.shutdown()
    restored = live.module.NativeExecutionRuntime(**live.kwargs)
    assert restored.projection("a-position").owned_quantity == 0
    assert restored.projection("position-1").owned_quantity == Decimal("0.4")
    restored.shutdown()


def test_cas_failure_does_not_overwrite_concurrent_candidate_and_retry_reduces_fresh(live: Any, monkeypatch: Any) -> None:
    candidate = native(live)
    apply = live.store.apply_position_event
    captured_revision = live.store.get_position("position-1").revision

    def concurrent(*args: Any, **kwargs: Any) -> Any:
        assert args[3] == captured_revision
        monkeypatch.setattr(live.store, "apply_position_event", apply)
        consume(live.runtime, candidate.fill.client_order_id, "OrderFilled",
                trade_id="concurrent-fill", last_qty=Decimal("0.1"))
        return apply(*args, **kwargs)

    monkeypatch.setattr(live.store, "apply_position_event", concurrent)
    with pytest.raises(ValueError, match="revision"):
        credit(live, candidate)
    assert live.store.get_position("position-1").revision == captured_revision + 1
    assert len(live.runtime.persisted_candidates()) == 2
    assert live.runtime.projection("position-1").owned_quantity == 0
    assert not live.runtime._projections["position-1"][3].credit.credits
    assert credit(live, candidate).status == "CREDITED"
    assert live.runtime.projection("position-1").owned_quantity == Decimal("0.4")
    assert any(item.reason == "AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED"
               for item in live.runtime.projection("position-1").conflicts)


def test_constructor_pins_dependencies_and_foreign_session_scope_cannot_credit(live: Any) -> None:
    candidate = native(live)
    assert live.runtime.receipt_session is live.owner
    assert live.runtime.history_store is live.history
    for name in ("receipt_session", "history_store"):
        with pytest.raises(AttributeError):
            setattr(live.runtime, name, None)
    for changes in ({"receipt_session": None}, {"history_store": None},
                    {"receipt_session": object()}, {"history_store": object()}):
        with pytest.raises(ValueError, match="MATCHING_RECEIPT_DEPENDENCIES_REQUIRED"):
            live.module.NativeExecutionRuntime(**{**live.kwargs, **changes})
    with (HistoryStore(live.kwargs["lock_directory"] / "foreign.sqlite3",
                       ProtectionScope("other", "DEMO")) as foreign,
          pytest.raises(ValueError, match="MATCHING_RECEIPT_DEPENDENCIES_REQUIRED")):
        live.module.NativeExecutionRuntime(**{**live.kwargs, "history_store": foreign})
    live.owner._identity = replace(live.owner._identity, uid="foreign-account")
    with pytest.raises(ValueError, match="AUTHENTICATED_ACCOUNT_SCOPE_MISMATCH"):
        credit(live, candidate)
    assert live.runtime.projection("position-1").owned_quantity == 0


async def test_injected_session_public_receipt_and_runtime_routes_reject(live: Any) -> None:
    candidate = native(live)
    calls: list[Any] = []

    def forbidden(request: Any) -> Any:
        calls.append(request)
        pytest.fail("public injected route reached GET")

    owner = BybitDemoAccountSession(api_key="synthetic-key", api_secret="synthetic-secret",
        clock=lambda: OBSERVED, monotonic=lambda: 10.0, transport=httpx.MockTransport(forbidden))
    owner._binding, owner._identity, owner._issued_mono = live.binding, live.owner._identity, 10.0
    try:
        with pytest.raises(ValueError, match="ACCOUNT_NETWORK_TRANSPORT_REQUIRED"):
            await owner.read_history_receipt(live.binding, scope=SCOPE, symbol="BTCUSDT",
                start_ms=NS // 1_000_000 - 1000, end_ms=NS // 1_000_000)
        live.runtime.shutdown()
        live.runtime = live.module.NativeExecutionRuntime(**{**live.kwargs, "receipt_session": owner})
        receipt, key = issue(live, snapshot(candidate))
        with pytest.raises(ValueError, match="ACCOUNT_NETWORK_TRANSPORT_REQUIRED"):
            live.runtime.corroborate_execution(receipt=receipt, binding=live.binding,
                observation_key=key, candidate_id=candidate_identity(candidate))
        assert live.runtime.projection("position-1").owned_quantity == 0
        assert not calls
    finally:
        await owner.aclose()


def test_data_records_and_foreign_handles_cannot_authorize_and_restart_needs_fresh_receipt(live: Any) -> None:
    candidate = native(live)
    receipt, key = issue(live, snapshot(candidate))
    records = (snapshot(candidate), live.history.ingest(snapshot(candidate)),
               live.owner.require_history_receipt(receipt, binding=live.binding, scope=SCOPE),
               object.__new__(AuthenticatedHistoryReceipt))
    for fake in records:
        with pytest.raises(ValueError, match="AUTHENTICATED_HISTORY_RECEIPT_REQUIRED"):
            live.runtime.corroborate_execution(receipt=fake, binding=live.binding,
                observation_key=key, candidate_id=candidate_identity(candidate))
    assert credit(live, candidate).status == "CREDITED"
    event = live.store.get_inbox_event(execution_guard(SCOPE,
        candidate.fill.instrument_id, candidate.fill.execution_id))
    for fake in (codec.decode(event.payload_json), codec.decode(event.payload_json).source):
        with pytest.raises(ValueError, match="AUTHENTICATED_HISTORY_RECEIPT_REQUIRED"):
            live.runtime.corroborate_execution(receipt=fake, binding=live.binding,
                observation_key=key, candidate_id=candidate_identity(candidate))
    live.runtime.shutdown()
    restored = live.module.NativeExecutionRuntime(**{**live.kwargs,
        "receipt_session": None, "history_store": None})
    with pytest.raises(ValueError, match="AUTHENTICATED_EXECUTION_RECEIPT_REQUIRED"):
        restored.corroborate_execution(receipt=receipt, binding=live.binding,
            observation_key=key, candidate_id=candidate_identity(candidate))
    assert restored.projection("position-1").owned_quantity == Decimal("0.4")
    assert restored.status.entries_paused
    with sqlite3.connect(live.kwargs["lock_directory"] / "runtime-state.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM runtime_inbox WHERE event_id LIKE 'credit:%'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM runtime_positions").fetchone()[0] == 1
    restored.shutdown()


def test_matching_order_absence_and_unrelated_history_conflicts_do_not_block(live: Any) -> None:
    candidate = native(live)
    unrelated = snapshot(candidate, execution_id="unrelated")
    live.history.ingest(replace(unrelated, snapshot_id="unrelated-1"))
    live.history.ingest(replace(unrelated, snapshot_id="unrelated-2",
        executions=(replace(unrelated.executions[0], fee=Decimal("0.02")),)))
    assert credit(live, candidate, replace(snapshot(candidate),
        orders=(order_member(candidate),))).status == "CREDITED"


def test_known_native_venue_mapping_and_wrong_instrument_block(live: Any) -> None:
    record = queue(live.runtime)
    live.journal.claim("entry")
    consume(live.runtime, record.client_order_id, "OrderAccepted", venue_order_id="previous-venue")
    consume(live.runtime, record.client_order_id, "OrderFilled")
    candidate = live.runtime.persisted_candidates()[0]
    assert "VENUE_ORDER_ID_MISMATCH" in credit(live, candidate).reason_codes
    wrong = replace(snapshot(candidate), snapshot_id="wrong-instrument", symbol="ETHUSDT",
        executions=(replace(snapshot(candidate).executions[0], symbol="ETHUSDT"),))
    assert "INSTRUMENT_ID_MISMATCH" in credit(live, candidate, wrong).reason_codes


def test_reducer_quantity_refusal_persists_decision_without_credit_guard(live: Any) -> None:
    candidate = native(live, last_qty=Decimal(100))
    result = credit(live, candidate)
    assert result.status == "CONFLICT" and "FILL_EXCEEDS_ORDER_INTENT" in result.reason_codes
    assert live.store.get_inbox_event(execution_guard(SCOPE, candidate.fill.instrument_id,
                                                      candidate.fill.execution_id)) is None
    live.runtime.shutdown()
    restored = live.module.NativeExecutionRuntime(**live.kwargs)
    assert restored.projection("position-1").owned_quantity == 0
    assert restored.projection("position-1").conflicts
    restored.shutdown()


def test_missing_stored_observation_is_unresolved_not_fabricated(live: Any) -> None:
    candidate = native(live)
    receipt, _ = issue(live, snapshot(candidate))
    before = live.store.get_position("position-1")
    result = live.runtime.corroborate_execution(receipt=receipt, binding=live.binding,
        observation_key="f" * 64, candidate_id=candidate_identity(candidate))
    assert result.status == "UNRESOLVED"
    assert result.reason_codes == ("HISTORY_OBSERVATION_REQUIRED",)
    assert live.store.get_position("position-1") == before


def test_precandidate_source_variants_block_credit(live: Any) -> None:
    candidate = native(live)
    consume(live.runtime, candidate.fill.client_order_id, "OrderFilled", reconciliation=True)
    result = credit(live, candidate)
    assert result.status == "CONFLICT" and "NATIVE_CANDIDATE_VARIANT_CONFLICT" in result.reason_codes
    assert live.runtime.projection("position-1").owned_quantity == 0


def test_unrelated_projection_conflict_survives_exact_credit(live: Any) -> None:
    candidate = native(live)
    event = ("CANCEL_IDENTITY_UNVERIFIED", candidate.fill.client_order_id)
    state = live.runtime.projection("position-1")
    reduced = live.runtime._reduce_event(state, event, {})
    live.runtime._persist(event, reduced)
    assert credit(live, candidate).status == "CREDITED"
    assert any(item.reason == "CANCEL_IDENTITY_UNVERIFIED"
               for item in live.runtime.projection("position-1").conflicts)
    assert live.runtime.status.entries_paused


def test_final_lease_change_rejects_without_accounting_commit(live: Any, monkeypatch: Any) -> None:
    candidate = native(live)
    saved = live.store.get_position("position-1")
    cache = dict(live.runtime._projections)
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
        credit(live, candidate)
    assert live.store.get_position("position-1") == saved
    assert live.runtime._projections == cache


@pytest.mark.parametrize("change", ["snapshot_id", "window", "observation_time", "other_member"])
def test_equal_execution_row_does_not_replace_exact_whole_snapshot(live: Any, change: str) -> None:
    candidate = native(live)
    observed = snapshot(candidate)
    _, original_key = issue(live, observed)
    changed = observed
    if change == "snapshot_id":
        changed = replace(changed, snapshot_id="other-snapshot")
    elif change == "window":
        changed = replace(changed, window=HistoryWindow(observed.window.start_ms - 1,
                                                       observed.window.end_ms))
    elif change == "observation_time":
        changed = replace(changed, observed_at=OBSERVED + timedelta(milliseconds=1))
    elif change == "other_member":
        changed = replace(changed, executions=(*observed.executions,
            replace(observed.executions[0], execution_id="unrelated-member")))
    receipt, _ = issue(live, changed)
    if change == "observation_time":
        live.owner._clock = lambda: OBSERVED + timedelta(milliseconds=1)
    before = live.store.get_position("position-1")
    with pytest.raises(ValueError, match="HISTORY_OBSERVATION_MISMATCH"):
        live.runtime.corroborate_execution(receipt=receipt, binding=live.binding,
            observation_key=original_key, candidate_id=candidate_identity(candidate))
    assert live.store.get_position("position-1") == before


def test_sqlite_failure_after_projection_write_rolls_back_event_and_cache(live: Any, monkeypatch: Any) -> None:
    candidate = native(live)
    position = live.store.get_position("position-1")
    events = live.store.list_position_events("position-1")
    cache = dict(live.runtime._projections)
    save = live.store._save

    def fail_after_write(*args: Any, **kwargs: Any) -> Any:
        save(*args, **kwargs)
        assert live.store.get_position("position-1").revision == position.revision + 1
        raise sqlite3.OperationalError("synthetic-disk-failure")

    monkeypatch.setattr(live.store, "_save", fail_after_write)
    with pytest.raises(sqlite3.OperationalError, match="synthetic-disk-failure"):
        credit(live, candidate)
    assert live.store.get_position("position-1") == position
    assert live.store.list_position_events("position-1") == events
    assert live.runtime._projections == cache
    assert live.history.execution_evidence(live.history.ingest(snapshot(candidate)).observation_key,
                                           candidate.fill.execution_id)


def test_native_currency_must_match_exact_history_currency(live: Any) -> None:
    candidate = native(live, commission=SimpleNamespace(
        as_decimal=lambda: Decimal("0.01"), currency="BTC"))
    result = credit(live, candidate, snapshot(candidate, fee_currency="USDT"))
    assert result.status == "CONFLICT" and "COMMISSION_CURRENCY_MISMATCH" in result.reason_codes
    assert live.runtime.projection("position-1").owned_quantity == 0
