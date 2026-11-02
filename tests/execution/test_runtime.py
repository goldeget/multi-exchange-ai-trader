"""Durable native handoff tested only through explicit fake native objects.

Qualification is patched ONLY in selected tests to exercise the otherwise blocked
handoff implementation. No production switch accepts these simulated proofs.
"""

import importlib
import json
import sqlite3
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_bybit_native import FakeFactory, FakeStrategy
from test_risk import NOW, qualified_inputs

from multi_exchange_trader.execution.bybit_native import NativeBindings, NativeBybitBridge
from multi_exchange_trader.execution.journal import CommandJournal, CommandState, ExecutionScope
from multi_exchange_trader.execution.modes import (
    AutomaticMode,
    Candidate,
    PolicyReference,
    PublicSnapshot,
    SelectionLimits,
    SelectionRequest,
    SelectionScope,
    StrategyReference,
)
from multi_exchange_trader.execution.native_events import NativeEventContext, NativeEventDecoder
from multi_exchange_trader.execution.ownership import AccountWorkerLock
from multi_exchange_trader.execution.protection import ProtectionPolicy, ProtectionScope
from multi_exchange_trader.execution.risk import assess_entry
from multi_exchange_trader.execution.state_store import RuntimeStateStore

SCOPE = ProtectionScope("account-1", "DEMO")
NS = int(NOW.timestamp()) * 1_000_000_000


def selection() -> SelectionRequest:
    strategy = StrategyReference("strategy-1", "v1")
    scope = SelectionScope("account-1", "DEMO", ("BTCUSDT",), (strategy,),
                           ("BTCUSDT",), (strategy,))
    snapshot = PublicSnapshot("public-1", NOW, NOW + timedelta(seconds=10))
    candidate = Candidate("candidate-1", "account-1", "DEMO", "BTCUSDT", strategy,
        "signal-1", "public-1", "policy-1", "plan-1", True, NOW,
        NOW + timedelta(seconds=10))
    return SelectionRequest("request-1", scope, PolicyReference("account-1", "DEMO", "policy-1"),
        snapshot, (candidate,), SelectionLimits(10, 4096, 100, 100, 10, 10, 10, 20),
        NOW, NOW + timedelta(seconds=10))


def risk_check(plan: Any, now: Any, reservations: Any) -> Any:
    data = qualified_inputs()
    data["now"] = now
    data["reservations"] = reservations
    data["liquidation"] = replace(data["liquidation"],
        reservation_snapshot_id=reservations.snapshot_id)
    return assess_entry(**data)


@pytest.fixture
def setup(tmp_path: Path) -> Any:
    module = importlib.import_module("multi_exchange_trader.execution.runtime")
    lock = AccountWorkerLock(tmp_path, account_id="account-1", environment="DEMO")
    lock.acquire()
    scope = ExecutionScope("account-1", "DEMO")
    journal = CommandJournal(tmp_path / "journal.sqlite3", scope)
    store = RuntimeStateStore(tmp_path / "runtime-state.sqlite3", scope)
    strategy = FakeStrategy()
    bridge = NativeBybitBridge(scope=SCOPE, native_account_id="BYBIT-UNIFIED",
        factory=FakeFactory(), strategy=strategy,
        bindings=NativeBindings(str, str, str, str, str))
    kwargs = {"journal": journal, "store": store, "worker_lock": lock, "lock_directory": tmp_path,
        "bridge": bridge, "entry_risk": risk_check, "clock": lambda: NOW,
        "max_evidence_age": timedelta(seconds=10)}
    worker = module.NativeExecutionRuntime(**kwargs)
    yield worker, journal, store, strategy, kwargs, module
    store.close()
    journal.close()
    lock.release()


def queue(worker: Any, key: str = "entry") -> Any:
    return worker.queue_entry(key, "position-1", plan_id="plan-1",
        plan=assess_entry(**qualified_inputs()),
        protection_policy=ProtectionPolicy("policy-1", 0, Decimal(90), Decimal(120), "MarkPrice"),
        mode=AutomaticMode.FULL_AUTO, selection_request=selection())


def enable_fake_handoff(worker: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "_require_qualification", lambda: None)
    # These legacy event/projection tests isolate entry handoff. The actual
    # leverage sequence and fresh-risk gate have dedicated controls tests.
    monkeypatch.setattr(worker, "_leverage_gate", lambda *_args: None)
    worker.start()


def consume(worker: Any, client_id: str, kind: str, **changes: object) -> Any:
    fields: dict[str, object] = {"account_id": "BYBIT-UNIFIED", "client_order_id": client_id,
        "instrument_id": "BTCUSDT-LINEAR.BYBIT", "venue_order_id": "venue-1", "ts_event": NS,
        "ts_init": NS, "order_side": "BUY", "trade_id": "fill-1", "last_qty": Decimal("0.4"),
        "last_px": Decimal(100), "commission": SimpleNamespace(
            as_decimal=lambda: Decimal("0.01"), currency="USDT")}
    fields.update(changes)
    return worker.consume_native(SimpleNamespace(**fields),
        NativeEventContext(SCOPE, "BYBIT-UNIFIED", NS),
        decoder=NativeEventDecoder(event_types={SimpleNamespace: kind}))


def test_default_qualification_blocks_real_handoff_but_retains_durable_plan(setup: Any) -> None:
    worker, journal, store, strategy, _, module = setup
    record = queue(worker)
    with pytest.raises(module.RuntimeBlocked, match="QUALIFICATION"):
        worker.start()
    with pytest.raises(module.RuntimeBlocked):
        worker.dispatch("entry")
    assert journal.get("entry").state is CommandState.PENDING
    assert journal.get("entry").reservation == record.reservation
    assert store.get_position("position-1") is not None
    assert not strategy.commands
    assert not worker.status.production_supported
    assert "AUTHENTICATED_ACCOUNT_BINDING_REQUIRED" in worker.status.blockers


def test_claim_precedes_native_call_and_ack_is_not_fill(setup: Any,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    worker, journal, _, strategy, kwargs, _ = setup
    record = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    original = strategy.submit_order

    def observe(order: object, *, params: dict[str, object]) -> None:
        with sqlite3.connect(kwargs["lock_directory"] / "journal.sqlite3") as observer:
            assert observer.execute("SELECT state FROM commands").fetchone()[0] == "SENDING"
            observer.execute("BEGIN IMMEDIATE")
            observer.rollback()
        original(order, params=params)

    monkeypatch.setattr(strategy, "submit_order", observe)
    assert worker.dispatch("entry").state is CommandState.SENDING
    consume(worker, record.client_order_id, "OrderAccepted")
    assert journal.get("entry").state is CommandState.ACKNOWLEDGED
    assert worker.projection("position-1").owned_quantity == 0
    assert journal.get("entry").terminal_confirmed is False
    worker.dispatch("entry")
    assert len(strategy.commands) == 1


def test_partial_fill_duplicate_conflict_and_restart_survive_in_normalized_store(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, journal, _, strategy, kwargs, module = setup
    record = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    consume(worker, record.client_order_id, "OrderFilled")
    consume(worker, record.client_order_id, "OrderFilled", trade_id="fill-2",
            last_qty=Decimal("0.1"))
    consume(worker, record.client_order_id, "OrderFilled")
    assert worker.projection("position-1").owned_quantity == Decimal("0.5")
    assert worker.status.entries_paused
    assert journal.get("entry").reservation is not None
    consume(worker, record.client_order_id, "OrderFilled", last_qty=Decimal("0.3"))
    worker.shutdown()
    restarted = module.NativeExecutionRuntime(**kwargs)
    state = restarted.projection("position-1")
    assert state.owned_quantity == Decimal("0.5")
    assert state.conflicts
    assert restarted.status.entries_paused
    assert restarted.dispatch("entry").state is CommandState.ACKNOWLEDGED
    assert len(strategy.commands) == 1


def test_ambiguous_native_send_recovery_never_retries(setup: Any,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    worker, journal, _, strategy, kwargs, module = setup
    record = queue(worker)
    strategy.fail = True
    enable_fake_handoff(worker, monkeypatch)
    assert worker.dispatch("entry").state is CommandState.UNKNOWN
    worker.shutdown()
    restarted = module.NativeExecutionRuntime(**kwargs)
    assert restarted.dispatch("entry").state is CommandState.UNKNOWN
    consume(restarted, record.client_order_id, "OrderAccepted")
    assert journal.get("entry").state is CommandState.ACKNOWLEDGED
    assert len(strategy.commands) == 1
    assert "UNTRUSTED" not in repr(journal.audit_events())


def test_second_account_worker_and_reacquired_lease_cannot_dispatch(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, _, _, strategy, kwargs, module = setup
    queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    other = AccountWorkerLock(kwargs["lock_directory"], account_id="account-1", environment="DEMO")
    with pytest.raises(RuntimeError):
        other.acquire()
    kwargs["worker_lock"].release()
    kwargs["worker_lock"].acquire()
    with pytest.raises(module.RuntimeBlocked, match="LEASE"):
        worker.dispatch("entry")
    assert not strategy.commands


def test_commit_failure_never_calls_native(setup: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    worker, journal, _, strategy, kwargs, _ = setup
    queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    with sqlite3.connect(kwargs["lock_directory"] / "journal.sqlite3") as conn:
        conn.execute("CREATE TRIGGER fail_claim BEFORE INSERT ON audit "
            "WHEN NEW.action='CLAIM' BEGIN SELECT RAISE(ABORT,'storage failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        worker.dispatch("entry")
    assert journal.get("entry").state is CommandState.PENDING
    assert not strategy.commands


def test_manual_order_never_becomes_owned_or_receives_an_exit(setup: Any) -> None:
    worker, journal, store, strategy, _, module = setup
    with pytest.raises(ValueError, match="OWNERSHIP"):
        consume(worker, "manual-order", "OrderFilled")
    with pytest.raises(module.RuntimeBlocked):
        worker.queue_exit("exit", "manual-position", Decimal(1), Decimal(100))
    assert not journal.list_commands()
    assert not store.list_positions()
    assert not strategy.commands


def test_same_lease_cannot_create_two_active_runtimes(setup: Any) -> None:
    worker, _, _, _, kwargs, module = setup
    queue(worker)
    with pytest.raises(module.RuntimeBlocked, match="ALREADY"):
        module.NativeExecutionRuntime(**kwargs)


def test_transport_disconnect_preserves_unknown_across_restart(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, journal, _, strategy, kwargs, module = setup
    queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    worker.transport_lost()
    assert journal.get("entry").state is CommandState.UNKNOWN
    worker.shutdown()
    recovered = module.NativeExecutionRuntime(**kwargs)
    assert recovered.dispatch("entry").state is CommandState.UNKNOWN
    assert len(strategy.commands) == 1


@pytest.mark.parametrize("status", ["INITIALIZED", "SUBMITTED", "PENDING_UPDATE", "REJECTED"])
def test_native_status_is_not_blanket_acceptance(setup: Any,
    monkeypatch: pytest.MonkeyPatch, status: str,
) -> None:
    worker, journal, _, _, _, _ = setup
    record = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    consume(worker, record.client_order_id, "OrderStatusReport", order_status=status,
            quantity=Decimal(1), ts_last=NS)
    expected = CommandState.REJECTED if status == "REJECTED" else CommandState.SENDING
    assert journal.get("entry").state is expected
    assert not journal.get("entry").terminal_confirmed


def test_full_auto_obeys_user_selection_and_all_in_one_requires_server_id(setup: Any) -> None:
    worker, journal, _, _, _, module = setup
    request = selection()
    changed = replace(request, scope=replace(request.scope, selected_symbols=()))
    args = {"plan_id": "plan-1", "plan": assess_entry(**qualified_inputs()),
        "protection_policy": ProtectionPolicy("policy-1", 0, Decimal(90), Decimal(120), "MarkPrice")}
    with pytest.raises(module.RuntimeBlocked):
        worker.queue_entry("entry", "position-1", mode=AutomaticMode.FULL_AUTO,
                           selection_request=changed, **args)
    with pytest.raises(module.RuntimeBlocked):
        worker.queue_entry("entry", "position-1", mode=AutomaticMode.ALL_IN_ONE,
                           selection_request=request, **args)
    response = json.dumps({"schema_version": 1, "request_id": "request-1",
        "snapshot_id": "public-1", "policy_revision": "policy-1", "decision": "SELECT",
        "candidate_id": "candidate-1", "reason": "eligible"})
    record = worker.queue_entry("entry", "position-1", mode=AutomaticMode.ALL_IN_ONE,
        selection_request=changed, ai_response=response, **args)
    assert journal.get("entry").state is CommandState.PENDING
    assert json.loads(record.payload_json)["mode"] == "ALL_IN_ONE"


@pytest.mark.parametrize("change", ["external", "stale", "empty", "partial", "position_tp_sl"])
def test_readback_never_invents_attached_order_ownership_or_releases_reservations(
    setup: Any, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from multi_exchange_trader.execution.venue_readback import VenuePosition, VenueSnapshot

    worker, journal, _, _, kwargs, module = setup
    record = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    consume(worker, record.client_order_id, "OrderFilled")
    row = VenuePosition("BTCUSDT", 0, "BUY", Decimal("0.4"), Decimal(2),
                        Decimal(90), Decimal(120), Decimal(80))
    if change == "external":
        row = replace(row, quantity=Decimal("1.4"))
    snapshot = VenueSnapshot(SCOPE, "BTCUSDT", "cycle-1", NOW,
        NOW - timedelta(seconds=11) if change == "stale" else NOW,
        change != "partial", () if change == "empty" else (row,), (), ())
    result = worker.consume_readback("position-1", snapshot)
    assert result.entries_blocked
    assert result.status != "OPEN_PROTECTED"
    assert result.unprotected_quantity == Decimal("0.4")
    assert journal.get("entry").terminal_confirmed is False
    assert len(worker.projection("position-1").orders) == 1
    worker.shutdown()
    restarted = module.NativeExecutionRuntime(**kwargs)
    assert restarted.projection("position-1").owned_quantity == Decimal("0.4")


def test_late_native_fill_after_rejection_is_retained_without_release(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, journal, _, _, _, _ = setup
    record = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    consume(worker, record.client_order_id, "OrderRejected")
    consume(worker, record.client_order_id, "OrderFilled")
    assert worker.projection("position-1").owned_quantity == Decimal("0.4")
    assert journal.get("entry").reservation is not None
    assert not journal.get("entry").terminal_confirmed


def test_stale_risk_after_durable_claim_never_reaches_native(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, journal, _, strategy, _, module = setup
    queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    claim = journal.claim

    def delay(key: str) -> Any:
        result = claim(key)
        monkeypatch.setattr(worker, "_clock", lambda: NOW + timedelta(seconds=11))
        return result

    monkeypatch.setattr(journal, "claim", delay)
    with pytest.raises(module.RuntimeBlocked, match="GATE_EXPIRED"):
        worker.dispatch("entry")
    assert journal.get("entry").state is CommandState.REJECTED
    assert not strategy.commands
    assert journal.get("entry").reservation is not None


def test_position_identity_is_never_recycled_over_durable_history(setup: Any) -> None:
    worker, journal, _, _, _, module = setup
    first = queue(worker)
    journal.claim("entry")
    journal.acknowledge("entry", "fixture-ack")
    journal.release_reservation("entry", confirmed_terminal=True, terminal_evidence="fixture-flat")
    with pytest.raises(module.RuntimeBlocked, match="POSITION_ID"):
        queue(worker, "entry-two")
    assert worker.projection("position-1").orders[0].client_order_id == first.client_order_id
    assert journal.get("entry-two") is None


def test_exit_requires_current_exact_readback_and_refuses_manual_exposure(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution.venue_readback import VenuePosition, VenueSnapshot

    worker, journal, _, strategy, _, module = setup
    record = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    consume(worker, record.client_order_id, "OrderFilled")
    with pytest.raises(module.RuntimeBlocked, match="READBACK"):
        worker.queue_exit("exit", "position-1", Decimal("0.4"), Decimal(100))
    row = VenuePosition("BTCUSDT", 0, "BUY", Decimal("0.4"), Decimal(2), None, None, None)
    snapshot = VenueSnapshot(SCOPE, "BTCUSDT", "exit-cycle", NOW, NOW, True, (row,), (), ())
    worker.consume_readback("position-1", snapshot)
    exiting = worker.queue_exit("exit", "position-1", Decimal("0.4"), Decimal(100))
    # External activity arrives after staging and must be checked again before send.
    worker.consume_readback("position-1", replace(snapshot, cycle_id="external-cycle",
        positions=(replace(row, quantity=Decimal("1.4")),)))
    with pytest.raises(module.RuntimeBlocked, match="READBACK"):
        worker.dispatch("exit")
    assert journal.get("exit").state is CommandState.PENDING
    assert len(strategy.commands) == 1
    worker.consume_readback("position-1", replace(snapshot, cycle_id="clean-cycle"))
    assert worker.dispatch("exit").state is CommandState.SENDING
    order = strategy.commands[-1][0]
    assert order.client_order_id == exiting.client_order_id
    assert order.is_reduce_only is True
    assert order.quantity == "0.4"


def test_projection_commit_before_ack_survives_ack_failure_and_duplicate_replay(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, journal, _, _, kwargs, module = setup
    record = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    with sqlite3.connect(kwargs["lock_directory"] / "journal.sqlite3") as conn:
        conn.execute("CREATE TRIGGER fail_ack BEFORE INSERT ON audit "
            "WHEN NEW.action='ACKNOWLEDGED' BEGIN SELECT RAISE(ABORT,'storage failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        consume(worker, record.client_order_id, "OrderFilled")
    assert worker.projection("position-1").owned_quantity == Decimal("0.4")
    assert journal.get("entry").state is CommandState.SENDING
    worker.shutdown()
    with sqlite3.connect(kwargs["lock_directory"] / "journal.sqlite3") as conn:
        conn.execute("DROP TRIGGER fail_ack")
    recovered = module.NativeExecutionRuntime(**kwargs)
    assert journal.get("entry").state is CommandState.UNKNOWN
    consume(recovered, record.client_order_id, "OrderFilled")
    assert recovered.projection("position-1").owned_quantity == Decimal("0.4")
    assert journal.get("entry").state is CommandState.ACKNOWLEDGED


def test_bad_exit_price_does_not_poison_durable_restart(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution.venue_readback import VenuePosition, VenueSnapshot

    worker, journal, _, _, _, _ = setup
    record = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    consume(worker, record.client_order_id, "OrderFilled")
    worker.consume_readback("position-1", VenueSnapshot(SCOPE, "BTCUSDT", "exit-cycle", NOW,
        NOW, True, (VenuePosition("BTCUSDT", 0, "BUY", Decimal("0.4"), Decimal(2),
        None, None, None),), (), ()))
    with pytest.raises(ValueError):
        worker.queue_exit("bad-exit", "position-1", Decimal("0.4"), Decimal("NaN"))
    assert journal.get("bad-exit") is None


def test_thousand_partial_fills_restart_and_early_execution_identity(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, journal, store, strategy, kwargs, module = setup
    record = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    for index in range(1100):
        consume(worker, record.client_order_id, "OrderFilled", trade_id=f"execution-{index}",
                last_qty=Decimal("0.0005"))
    assert worker.projection("position-1").owned_quantity == Decimal("0.55")
    assert len(store.get_position("position-1").payload_json.encode()) < 2048
    assert len(store.list_position_events("position-1")) == 1101
    assert journal.get("entry").state is CommandState.ACKNOWLEDGED
    worker.shutdown()
    # A genuinely separate connection/cache must reconstruct the durable event rows.
    with RuntimeStateStore(kwargs["lock_directory"] / "runtime-state.sqlite3", store.scope) as opened:
        restarted = module.NativeExecutionRuntime(**{**kwargs, "store": opened})
        assert restarted.projection("position-1").owned_quantity == Decimal("0.55")
        before = opened.get_position("position-1").revision
        consume(restarted, record.client_order_id, "OrderFilled", trade_id="execution-0",
                last_qty=Decimal("0.0005"))
        assert opened.get_position("position-1").revision == before
        for changes in ({"last_qty": Decimal("0.0006")}, {"last_px": Decimal(101)},
                        {"commission": SimpleNamespace(as_decimal=lambda: Decimal("0.02"),
                                                       currency="USDT")},
                        {"commission": SimpleNamespace(as_decimal=lambda: Decimal("0.01"),
                                                       currency="BTC")}):
            consume(restarted, record.client_order_id, "OrderFilled",
                **{"trade_id": "execution-0", "last_qty": Decimal("0.0005"), **changes})
            assert restarted.projection("position-1").owned_quantity == Decimal("0.55")
            assert restarted.projection("position-1").conflicts
        consume(restarted, record.client_order_id, "OrderFilled", trade_id="execution-1100",
                last_qty=Decimal("0.0005"))
        restarted.shutdown()
    again = module.NativeExecutionRuntime(**kwargs)
    assert again.projection("position-1").owned_quantity == Decimal("0.5505")
    assert len(again.projection("position-1").executions) == 1101
    assert again.projection("position-1").conflicts
    assert len(strategy.commands) == 1
    assert journal.get("entry").reservation == record.reservation
    assert not journal.get("entry").terminal_confirmed


def test_fill_history_and_summary_roll_back_together_before_ack(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, journal, store, _, kwargs, _ = setup
    record = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    before = store.get_position("position-1")
    with sqlite3.connect(kwargs["lock_directory"] / "runtime-state.sqlite3") as conn:
        conn.execute("CREATE TRIGGER fail_event BEFORE INSERT ON runtime_inbox "
                     "BEGIN SELECT RAISE(ABORT,'storage failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        consume(worker, record.client_order_id, "OrderFilled")
    assert store.get_position("position-1") == before
    assert worker.projection("position-1").owned_quantity == 0
    assert len(store.list_position_events("position-1")) == 1
    assert journal.get("entry").state is CommandState.SENDING
    with sqlite3.connect(kwargs["lock_directory"] / "runtime-state.sqlite3") as conn:
        conn.execute("DROP TRIGGER fail_event")
    consume(worker, record.client_order_id, "OrderFilled")
    assert worker.projection("position-1").owned_quantity == Decimal("0.4")
    assert journal.get("entry").state is CommandState.ACKNOWLEDGED


@pytest.mark.parametrize("changed", ["price", "fee", "currency"])
def test_changed_execution_economics_conflict_after_restart(
    setup: Any, monkeypatch: pytest.MonkeyPatch, changed: str,
) -> None:
    worker, _, _, _, kwargs, module = setup
    record = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    consume(worker, record.client_order_id, "OrderFilled")
    worker.shutdown()
    recovered = module.NativeExecutionRuntime(**kwargs)
    assert not recovered.projection("position-1").conflicts
    changes = ({"last_px": Decimal(101)} if changed == "price" else
               {"commission": SimpleNamespace(
                   as_decimal=lambda: Decimal("0.02" if changed == "fee" else "0.01"),
                   currency="BTC" if changed == "currency" else "USDT")})
    consume(recovered, record.client_order_id, "OrderFilled", **changes)
    assert recovered.projection("position-1").owned_quantity == Decimal("0.4")
    assert recovered.projection("position-1").conflicts[0].reason == "EXECUTION_ID_CONFLICT"
    recovered.shutdown()
    again = module.NativeExecutionRuntime(**kwargs)
    assert again.projection("position-1").conflicts[0].reason == "EXECUTION_ID_CONFLICT"


def test_missing_normalized_execution_history_fails_closed_on_restart(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, _, store, strategy, kwargs, module = setup
    record = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    consume(worker, record.client_order_id, "OrderFilled")
    event = store.list_position_events("position-1")[-1]
    worker.shutdown()
    with sqlite3.connect(kwargs["lock_directory"] / "runtime-state.sqlite3") as conn:
        conn.execute("DELETE FROM runtime_inbox WHERE event_id=?", (event.event_id,))
    with pytest.raises(module.RuntimeBlocked, match="PROJECTION_HISTORY"):
        module.NativeExecutionRuntime(**kwargs)
    assert len(strategy.commands) == 1


def test_expired_orphan_entry_recovers_immutable_mapping_without_dispatch(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, journal, store, strategy, kwargs, module = setup
    with monkeypatch.context() as fault:
        def interrupt(*args: Any, **kwargs: Any) -> None:
            raise OSError("simulated crash after command registration")

        fault.setattr(store, "apply_position_event", interrupt)
        with pytest.raises(OSError):
            queue(worker)
    original = journal.get("entry")
    assert original.state is CommandState.PENDING
    assert original.reservation is not None
    assert store.get_position("position-1") is None
    worker.shutdown()
    expired = NOW + timedelta(seconds=60)
    recovered = module.NativeExecutionRuntime(**{**kwargs, "clock": lambda: expired})
    assert journal.get("entry") == original
    assert recovered.projection("position-1").orders[0] == recovered._request(original).owned
    assert recovered.projection("position-1").owned_quantity == 0
    assert not strategy.commands
    with pytest.raises(module.RuntimeBlocked, match="QUALIFICATION"):
        recovered.dispatch("entry")
    enable_fake_handoff(recovered, monkeypatch)
    with pytest.raises(module.RuntimeBlocked, match="SELECTION_EXPIRED"):
        recovered._entry_gate(original, recovered._plan(original))
    with pytest.raises(module.RuntimeBlocked):
        recovered.dispatch("entry")
    assert journal.get("entry") == original
    assert not original.terminal_confirmed
    assert not strategy.commands


def test_legacy_projection_normalizes_without_losing_durable_fill_history(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution import codec

    worker, _, store, _, kwargs, module = setup
    record = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    consume(worker, record.client_order_id, "OrderFilled")
    legacy = worker.projection("position-1")
    current = store.get_position("position-1")
    store.save_position("position-1", json.loads(codec.encode(legacy)), current.revision)
    worker.shutdown()
    recovered = module.NativeExecutionRuntime(**kwargs)
    consume(recovered, record.client_order_id, "OrderFilled", trade_id="next", last_qty=Decimal("0.1"))
    recovered.shutdown()
    again = module.NativeExecutionRuntime(**kwargs)
    assert again.projection("position-1").owned_quantity == Decimal("0.5")
    consume(again, record.client_order_id, "OrderFilled")
    assert again.projection("position-1").owned_quantity == Decimal("0.5")
