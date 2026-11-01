import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from test_risk import NOW, qualified_inputs

from multi_exchange_trader.execution.coordinator import (
    CoordinatorBlocked,
    OfflineCapabilities,
    OfflineExecutionCoordinator,
    OfflineOrderReport,
    SimulatedReconciliation,
)
from multi_exchange_trader.execution.journal import (
    CommandJournal,
    CommandKind,
    CommandState,
    ExecutionScope,
    JournalConflict,
)
from multi_exchange_trader.execution.ownership import AccountWorkerLock
from multi_exchange_trader.execution.protection import (
    FillEvent,
    ProtectionPolicy,
    ProtectionScope,
)
from multi_exchange_trader.execution.risk import EntryDecision, ReservationTotals, assess_entry


class FakeGateway:
    capabilities = OfflineCapabilities("account-1", "DEMO", True, "OFFLINE_FAKE")

    def __init__(self) -> None:
        self.submissions: list[str] = []
        self.queries: list[str] = []
        self.cancellations: list[str] = []
        self.reports: dict[str, OfflineOrderReport] = {}
        self.timeout = False

    def submit(self, client_order_id: str, payload: object) -> OfflineOrderReport:
        self.submissions.append(client_order_id)
        report = OfflineOrderReport("account-1", "DEMO", client_order_id, "ACCEPTED")
        self.reports[client_order_id] = report
        if self.timeout:
            raise TimeoutError("api_secret=DO_NOT_PERSIST")
        return report

    def query(self, client_order_id: str) -> OfflineOrderReport:
        self.queries.append(client_order_id)
        return self.reports.get(client_order_id, OfflineOrderReport(
            "account-1", "DEMO", client_order_id, "ABSENT"))

    def cancel(self, client_order_id: str) -> OfflineOrderReport:
        self.cancellations.append(client_order_id)
        report = OfflineOrderReport("account-1", "DEMO", client_order_id, "CANCELLED")
        self.reports[client_order_id] = report
        return report


def risk_check(intent: Any, now: datetime, reservations: ReservationTotals) -> EntryDecision:
    data = qualified_inputs()
    data["now"] = now
    data["reservations"] = reservations
    data["liquidation"] = replace(data["liquidation"],
                                    reservation_snapshot_id=reservations.snapshot_id)
    return assess_entry(**data)


@pytest.fixture
def setup(tmp_path: Path) -> Any:
    lock = AccountWorkerLock(tmp_path, account_id="account-1", environment="DEMO")
    lock.acquire()
    journal = CommandJournal(tmp_path / "journal.db", ExecutionScope("account-1", "DEMO"))
    gateway = FakeGateway()
    worker = OfflineExecutionCoordinator(journal=journal, worker_lock=lock,
        lock_directory=tmp_path, gateway=gateway, entry_risk=risk_check,
        clock=lambda: NOW, max_evidence_age=timedelta(seconds=10))
    yield worker, journal, gateway, lock, tmp_path
    journal.close()
    lock.release()


def activate(worker: OfflineExecutionCoordinator) -> None:
    worker.reconcile(SimulatedReconciliation("account-1", "DEMO", NOW, True, False, ()))
    worker.activate()


def queue(worker: OfflineExecutionCoordinator, key: str = "entry") -> Any:
    return worker.queue_entry(key, "position-1", assess_entry(**qualified_inputs()),
                             ProtectionPolicy("policy-1", 0, SCOPE_PRICE, TP_PRICE, "MARK"))


SCOPE_PRICE = Decimal(90)
TP_PRICE = Decimal(120)


def test_timeout_after_acceptance_restart_queries_same_id_never_resubmits(setup: Any) -> None:
    worker, journal, gateway, lock, directory = setup
    activate(worker)
    record = queue(worker)
    gateway.timeout = True
    worker.dispatch("entry")
    assert journal.get("entry").state == CommandState.UNKNOWN
    assert "DO_NOT_PERSIST" not in str(journal.audit_events())
    restarted = OfflineExecutionCoordinator(journal=journal, worker_lock=lock,
        lock_directory=directory, gateway=gateway, entry_risk=risk_check,
        clock=lambda: NOW, max_evidence_age=timedelta(seconds=10))
    activate(restarted)
    restarted.dispatch("entry")
    assert gateway.submissions == [record.client_order_id]
    assert gateway.queries == [record.client_order_id]
    assert journal.get("entry").state == CommandState.ACKNOWLEDGED


def test_claim_commit_failure_has_no_gateway_effect(setup: Any, monkeypatch: Any) -> None:
    worker, journal, gateway, _, _ = setup
    activate(worker)
    queue(worker)

    def fail(key: str) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(journal, "claim", fail)
    with pytest.raises(OSError):
        worker.dispatch("entry")
    assert gateway.submissions == []
    assert journal.get("entry").state == CommandState.PENDING


def test_absent_unknown_never_authorizes_resend(setup: Any) -> None:
    worker, journal, gateway, _, _ = setup
    activate(worker)
    record = queue(worker)
    journal.claim("entry")
    journal.mark_unknown("entry", "ambiguous")
    worker.pause_entries()
    with pytest.raises(CoordinatorBlocked):
        activate(worker)
    assert gateway.queries == [record.client_order_id]
    assert journal.get("entry").state == CommandState.UNKNOWN
    assert gateway.submissions == []


def test_released_and_reacquired_lock_requires_new_worker_recovery(setup: Any) -> None:
    worker, _, gateway, lock, _ = setup
    activate(worker)
    queue(worker)
    lock.release()
    lock.acquire()
    with pytest.raises(CoordinatorBlocked):
        worker.dispatch("entry")
    assert gateway.submissions == []


@pytest.mark.parametrize("change", ["scope", "capability", "production"])
def test_wrong_gateway_scope_or_unqualified_simulated_capability_blocks(setup: Any,
                                                                       change: str) -> None:
    worker, _, gateway, _, _ = setup
    if change == "scope":
        gateway.capabilities = replace(gateway.capabilities, environment="LIVE")
    elif change == "capability":
        gateway.capabilities = replace(gateway.capabilities, attached_protection=False)
    else:
        gateway.capabilities = replace(gateway.capabilities, kind="PRODUCTION")
    with pytest.raises(CoordinatorBlocked):
        activate(worker)
    assert gateway.submissions == []


@pytest.mark.parametrize("change", ["policy", "quantity", "plan", "stale", "external"])
def test_changed_or_stale_submit_risk_is_rejected(setup: Any, change: str) -> None:
    worker, journal, gateway, _, _ = setup
    activate(worker)
    queue(worker)

    def changed(intent: Any, now: datetime, reservations: ReservationTotals) -> EntryDecision:
        decision = risk_check(intent, now, reservations)
        if change == "policy":
            return replace(decision, policy=replace(decision.policy, revision="new-policy"))
        if change == "quantity":
            return replace(decision, quantity=Decimal(2))
        if change == "plan":
            return replace(decision, stage="PLAN", submission_ready=False)
        if change == "stale":
            return replace(decision, assessed_at=NOW - timedelta(seconds=11))
        return replace(decision, account=replace(decision.account,
                                                external_same_symbol_orders=True))

    worker.entry_risk = changed
    with pytest.raises(CoordinatorBlocked):
        worker.dispatch("entry")
    assert gateway.submissions == []
    assert journal.get("entry").state == CommandState.PENDING


def test_duplicate_fill_after_later_fill_does_not_rewind_and_pause_keeps_assessment(setup: Any
                                                                                  ) -> None:
    worker, _, _, _, _ = setup
    activate(worker)
    record = queue(worker)
    worker.dispatch("entry")
    first = FillEvent(ProtectionScope("account-1", "DEMO"), "fill-1", record.client_order_id,
                      "position-1", "BTCUSDT", "BUY", Decimal("0.2"), NOW)
    worker.record_fill(first)
    worker.record_fill(replace(first, execution_id="fill-2", quantity=Decimal("0.3")))
    worker.record_fill(first)
    worker.pause_entries()
    assert worker.projection("position-1").owned_quantity == Decimal("0.5")
    assessment = worker.assess_protection("position-1",
        ProtectionPolicy("policy-1", 0, SCOPE_PRICE, TP_PRICE, "MARK"), (), None)
    assert assessment.entries_blocked
    assert assessment.unprotected_quantity == Decimal("0.5")
    assert worker.status == "TEST_PAUSED"


def test_unknown_fill_conflict_survives_restart(setup: Any) -> None:
    worker, journal, gateway, lock, directory = setup
    unknown = FillEvent(ProtectionScope("account-1", "DEMO"), "foreign-fill", "external",
                        "foreign-position", "BTCUSDT", "BUY", Decimal(1), NOW)
    worker.record_fill(unknown)
    restarted = OfflineExecutionCoordinator(journal=journal, worker_lock=lock,
        lock_directory=directory, gateway=gateway, entry_risk=risk_check,
        clock=lambda: NOW, max_evidence_age=timedelta(seconds=10))
    assert restarted.projection("foreign-position").conflicts[0].event == unknown
    with pytest.raises(CoordinatorBlocked):
        activate(restarted)


def test_reduce_only_exit_bounded_and_cancel_only_owned_id_while_paused(setup: Any) -> None:
    worker, _, gateway, _, _ = setup
    activate(worker)
    entry = queue(worker)
    worker.dispatch("entry")
    worker.record_fill(FillEvent(ProtectionScope("account-1", "DEMO"), "fill", entry.client_order_id,
        "position-1", "BTCUSDT", "BUY", Decimal("0.4"), NOW))
    worker.pause_entries()
    with pytest.raises(CoordinatorBlocked):
        worker.queue_exit("too-large", "position-1", Decimal("0.5"), "policy-1")
    exit_record = worker.queue_exit("exit", "position-1", Decimal("0.4"), "policy-1")
    worker.dispatch("exit")
    assert gateway.submissions == [entry.client_order_id, exit_record.client_order_id]
    assert worker.projection("position-1").orders[-1].reduce_only is True
    with pytest.raises(CoordinatorBlocked):
        worker.cancel_owned("cancel-foreign", "external")
    worker.cancel_owned("cancel-entry", entry.client_order_id)
    assert gateway.cancellations == [entry.client_order_id]


def test_boot_requires_full_reconcile_then_explicit_activation(setup: Any) -> None:
    worker, _, gateway, _, _ = setup
    with pytest.raises(CoordinatorBlocked):
        queue(worker)
    worker.reconcile(SimulatedReconciliation("account-1", "DEMO", NOW, True, False, ()))
    with pytest.raises(CoordinatorBlocked):
        queue(worker)
    worker.activate()
    queue(worker)
    assert worker.status == "TEST_ACTIVE"
    assert gateway.submissions == []


def test_conflicting_duplicate_execution_is_preserved_in_durable_projection(setup: Any) -> None:
    worker, _, _, _, _ = setup
    activate(worker)
    entry = queue(worker)
    worker.dispatch("entry")
    first = FillEvent(ProtectionScope("account-1", "DEMO"), "fill", entry.client_order_id,
                     "position-1", "BTCUSDT", "BUY", Decimal("0.2"), NOW)
    worker.record_fill(first)
    conflicting = replace(first, quantity=Decimal("0.3"))
    worker.record_fill(conflicting)
    state = worker.projection("position-1")
    assert state.owned_quantity == Decimal("0.2")
    assert state.conflicts[0].event == conflicting
    assert worker.status == "TEST_PAUSED"


def test_registration_without_durable_mapping_cannot_dispatch(setup: Any) -> None:
    worker, journal, gateway, _, _ = setup
    activate(worker)
    queue(worker)
    # Simulate a crash after command registration but before owned mapping commit.
    with sqlite3.connect(journal_path := setup[-1] / "journal.db") as connection:
        connection.execute("DELETE FROM projections")
    assert journal_path.exists()
    with pytest.raises(CoordinatorBlocked):
        worker.dispatch("entry")
    assert gateway.submissions == []
    assert journal.get("entry").state == CommandState.PENDING


def test_real_claim_transaction_failure_rolls_back_before_effect(setup: Any) -> None:
    worker, journal, gateway, _, directory = setup
    activate(worker)
    queue(worker)
    with sqlite3.connect(directory / "journal.db") as connection:
        connection.execute("CREATE TRIGGER fail_claim BEFORE INSERT ON audit "
            "WHEN NEW.action='CLAIM' BEGIN SELECT RAISE(ABORT,'storage failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        worker.dispatch("entry")
    assert journal.get("entry").state == CommandState.PENDING
    assert gateway.submissions == []


def test_successful_ack_is_durable_but_is_not_a_fill(setup: Any) -> None:
    worker, journal, _, _, _ = setup
    activate(worker)
    queue(worker)
    worker.dispatch("entry")
    assert journal.get("entry").state == CommandState.ACKNOWLEDGED
    assert worker.projection("position-1").owned_quantity == 0
    assert journal.get("entry").terminal_confirmed is False


def test_submit_runs_after_claim_commit_with_no_sqlite_transaction(setup: Any,
                                                               monkeypatch: Any) -> None:
    worker, journal, gateway, _, directory = setup
    activate(worker)
    queue(worker)
    submit = gateway.submit

    def observe(client_id: str, payload: object) -> OfflineOrderReport:
        with sqlite3.connect(directory / "journal.db", timeout=0.1) as observer:
            assert observer.execute("SELECT state FROM commands").fetchone()[0] == "SENDING"
            observer.execute("BEGIN IMMEDIATE")
            observer.rollback()
        return submit(client_id, payload)

    monkeypatch.setattr(gateway, "submit", observe)
    worker.dispatch("entry")
    assert journal.get("entry").state == CommandState.ACKNOWLEDGED


def test_fresh_connection_recovers_crashed_sending_without_resubmit(setup: Any) -> None:
    worker, journal, gateway, lock, directory = setup
    activate(worker)
    entry = queue(worker)
    journal.claim("entry")
    gateway.reports[entry.client_order_id] = OfflineOrderReport(
        "account-1", "DEMO", entry.client_order_id, "ACCEPTED")
    journal.close()
    with CommandJournal(directory / "journal.db", ExecutionScope("account-1", "DEMO")) as reopened:
        recovered = OfflineExecutionCoordinator(journal=reopened, worker_lock=lock,
            lock_directory=directory, gateway=gateway, entry_risk=risk_check,
            clock=lambda: NOW, max_evidence_age=timedelta(seconds=10))
        assert reopened.get("entry").state == CommandState.UNKNOWN
        activate(recovered)
        recovered.dispatch("entry")
        assert reopened.get("entry").state == CommandState.ACKNOWLEDGED
        assert gateway.queries == [entry.client_order_id]
        assert gateway.submissions == []


def test_changed_instrument_limits_hold_even_if_current_quantity_still_fits(setup: Any) -> None:
    worker, _, gateway, _, _ = setup
    activate(worker)
    queue(worker)

    def changed(intent: Any, now: datetime, reservations: ReservationTotals) -> EntryDecision:
        decision = risk_check(intent, now, reservations)
        return replace(decision, instrument=replace(decision.instrument, max_notional=Decimal(99999)))

    worker.entry_risk = changed
    with pytest.raises(CoordinatorBlocked):
        worker.dispatch("entry")
    assert gateway.submissions == []


def test_risk_callback_does_not_double_count_own_queued_reservation(setup: Any) -> None:
    worker, journal, gateway, _, _ = setup
    activate(worker)
    queue(worker)
    seen: list[ReservationTotals] = []

    def observe(intent: Any, now: datetime, reservations: ReservationTotals) -> EntryDecision:
        seen.append(reservations)
        return risk_check(intent, now, reservations)

    worker.entry_risk = observe
    worker.dispatch("entry")
    assert seen[0].risk_amount == 0
    assert seen[0].notional_amount == 0
    assert seen[0].margin_amount == 0
    assert seen[0].snapshot_id.startswith("journal-")
    assert journal.get("entry").reservation.margin_amount >= assess_entry(
        **qualified_inputs()).margin_amount
    assert len(gateway.submissions) == 1


def test_external_full_reconcile_blocks_activation(setup: Any) -> None:
    worker, _, gateway, _, _ = setup
    with pytest.raises(CoordinatorBlocked):
        worker.reconcile(SimulatedReconciliation("account-1", "DEMO", NOW, True, True, ()))
    with pytest.raises(CoordinatorBlocked):
        worker.activate()
    assert gateway.submissions == []


def test_fresh_later_assessment_can_dispatch_unchanged_execution_plan(setup: Any,
                                                                  monkeypatch: Any) -> None:
    worker, journal, gateway, _, _ = setup
    activate(worker)
    queue(worker)
    monkeypatch.setattr(worker, "_clock", lambda: NOW + timedelta(seconds=1))
    worker.dispatch("entry")
    assert journal.get("entry").state == CommandState.ACKNOWLEDGED
    assert len(gateway.submissions) == 1


def test_one_absent_report_for_acknowledged_order_is_not_full_reconciliation(setup: Any) -> None:
    worker, _, gateway, _, _ = setup
    activate(worker)
    entry = queue(worker)
    worker.dispatch("entry")
    del gateway.reports[entry.client_order_id]
    with pytest.raises(CoordinatorBlocked):
        activate(worker)
    assert len(gateway.submissions) == 1


def test_expired_reconciliation_cannot_activate(setup: Any, monkeypatch: Any) -> None:
    worker, _, gateway, _, _ = setup
    worker.reconcile(SimulatedReconciliation("account-1", "DEMO", NOW, True, False, ()))
    monkeypatch.setattr(worker, "_clock", lambda: NOW + timedelta(seconds=11))
    with pytest.raises(CoordinatorBlocked):
        worker.activate()
    assert gateway.submissions == []


def test_unconfirmed_first_entry_blocks_another_entry(setup: Any) -> None:
    worker, journal, gateway, _, _ = setup
    activate(worker)
    queue(worker)
    worker.dispatch("entry")
    data = qualified_inputs()
    data["reservations"] = replace(data["reservations"], notional_amount=Decimal(100),
        symbol_notional_amount=Decimal(100), risk_amount=Decimal(12),
        margin_amount=journal.get("entry").reservation.margin_amount, position_count=1)
    with pytest.raises(CoordinatorBlocked, match="UNRESOLVED_ENTRY_CONFIRMATION"):
        worker.queue_entry("entry-two", "position-2", assess_entry(**data),
                           ProtectionPolicy("policy-1", 0, SCOPE_PRICE, TP_PRICE, "MARK"))
    assert len(gateway.submissions) == 1


@pytest.mark.parametrize("recovery_delay", [timedelta(0), timedelta(seconds=11)])
def test_mapping_commit_crash_replays_exact_intent_without_second_reservation(
    setup: Any, monkeypatch: Any, recovery_delay: timedelta,
) -> None:
    worker, journal, gateway, lock, directory = setup
    activate(worker)
    data = qualified_inputs()
    data["policy"] = replace(data["policy"], max_positions=1)
    plan = assess_entry(**data)
    policy = ProtectionPolicy("policy-1", 0, SCOPE_PRICE, TP_PRICE, "MARK")

    def fail_mapping(*args: object) -> None:
        raise OSError("injected mapping commit failure")

    monkeypatch.setattr(worker, "_register_order", fail_mapping)
    with pytest.raises(OSError, match="injected mapping commit failure"):
        worker.queue_entry("entry", "position-1", plan, policy)
    original = journal.get("entry")
    assert original.state == CommandState.PENDING
    assert journal.get_projection("offline-owned-state-v1") is None
    worker.shutdown()
    restarted = OfflineExecutionCoordinator(journal=journal, worker_lock=lock,
        lock_directory=directory, gateway=gateway, entry_risk=risk_check,
        clock=lambda: NOW + recovery_delay, max_evidence_age=timedelta(seconds=10))
    restarted.reconcile(SimulatedReconciliation("account-1", "DEMO", NOW + recovery_delay,
                                               True, False, ()))
    restarted.activate()
    for _ in range(2):
        assert restarted.queue_entry("entry", "position-1", plan, policy) == original
    assert len(journal.list_commands()) == 1
    held = restarted._reservation_snapshot(plan.scope, NOW)
    assert held.position_count == 1
    assert held.notional_amount == original.reservation.notional_amount
    assert held.risk_amount == original.reservation.risk_amount
    assert held.margin_amount == original.reservation.margin_amount
    assert len(restarted.projection("position-1").orders) == 1

    def check(intent: Any, now: datetime, reservations: ReservationTotals) -> EntryDecision:
        for name in ("market", "instrument", "account", "tiers", "liquidation",
                     "leverage_readback"):
            data[name] = replace(data[name], observed_at=now)
        data["now"] = now
        data["reservations"] = reservations
        data["liquidation"] = replace(data["liquidation"],
            reservation_snapshot_id=reservations.snapshot_id)
        return assess_entry(**data)

    restarted.entry_risk = check
    restarted.dispatch("entry")
    restarted.dispatch("entry")
    assert journal.get("entry").state == CommandState.ACKNOWLEDGED
    assert gateway.submissions == [original.client_order_id]


@pytest.mark.parametrize("change", ["position", "plan", "policy", "kind"])
def test_entry_replay_rejects_conflicting_immutable_identity(setup: Any, change: str) -> None:
    worker, journal, gateway, _, _ = setup
    activate(worker)
    original = queue(worker)
    position = "position-2" if change == "position" else "position-1"
    data = qualified_inputs()
    if change == "plan":
        data["market"] = replace(data["market"], quote_id="different-quote")
        data["liquidation"] = replace(data["liquidation"], quote_id="different-quote")
    policy = ProtectionPolicy("policy-1", 1 if change == "policy" else 0,
                              SCOPE_PRICE, TP_PRICE, "MARK")
    key = "other-kind" if change == "kind" else "entry"
    if change == "kind":
        journal.register(key, CommandKind.CANCEL, {"target_client_order_id": "external"})
    with pytest.raises(JournalConflict):
        worker.queue_entry(key, position, assess_entry(**data), policy)
    assert journal.get("entry") == original
    assert len(worker.projection("position-1").orders) == 1
    assert gateway.submissions == []


def test_stale_original_plan_only_replays_an_existing_immutable_command(
    setup: Any, monkeypatch: Any,
) -> None:
    worker, journal, gateway, _, _ = setup
    later = NOW + timedelta(seconds=11)
    monkeypatch.setattr(worker, "_clock", lambda: later)
    worker.reconcile(SimulatedReconciliation("account-1", "DEMO", later, True, False, ()))
    worker.activate()
    with pytest.raises(CoordinatorBlocked, match="INVALID_QUEUED_PLAN"):
        queue(worker)
    assert journal.list_commands() == ()
    assert gateway.submissions == []


@pytest.mark.parametrize("delay_at", ["callback", "claim"])
@pytest.mark.parametrize("expired", ["quote", "reconciliation", "costs", "funding", "loss_day"])
def test_elapsed_time_rechecks_current_entry_evidence_before_effect(
    setup: Any, monkeypatch: Any, delay_at: str, expired: str,
) -> None:
    worker, journal, gateway, _, _ = setup
    data = qualified_inputs()
    start = NOW
    delay = timedelta(seconds=3)
    if expired == "reconciliation":
        delay = timedelta(seconds=11)
        data["policy"] = replace(data["policy"], max_quote_age_seconds=Decimal(20),
                                 max_evidence_age_seconds=Decimal(20))
    elif expired == "costs":
        data["market"] = replace(data["market"], costs_valid_until=NOW + timedelta(seconds=1))
        delay = timedelta(seconds=2)
    elif expired == "funding":
        data["market"] = replace(data["market"], next_funding_at=NOW + timedelta(seconds=1))
        delay = timedelta(seconds=1)
    elif expired == "loss_day":
        start = NOW.replace(hour=23, minute=59, second=59)
        delay = timedelta(seconds=1)
        for name in ("market", "instrument", "account", "tiers", "reservations",
                     "liquidation", "leverage_readback"):
            data[name] = replace(data[name], observed_at=start)
        data["market"] = replace(data["market"], next_funding_at=start + timedelta(hours=1),
                                 costs_valid_until=start + timedelta(minutes=1))
    data["now"] = start
    clock = [start]
    monkeypatch.setattr(worker, "_clock", lambda: clock[0])
    worker.reconcile(SimulatedReconciliation("account-1", "DEMO", start, True, False, ()))
    worker.activate()
    original = worker.queue_entry("entry", "position-1", assess_entry(**data),
        ProtectionPolicy("policy-1", 0, SCOPE_PRICE, TP_PRICE, "MARK"))

    def delayed_risk(intent: Any, now: datetime, reservations: ReservationTotals) -> EntryDecision:
        data["now"] = now
        data["reservations"] = reservations
        data["liquidation"] = replace(data["liquidation"],
            reservation_snapshot_id=reservations.snapshot_id)
        decision = assess_entry(**data)
        if delay_at == "callback":
            clock[0] += delay
        return decision

    worker.entry_risk = delayed_risk
    claim = journal.claim

    def delayed_claim(key: str) -> Any:
        claimed = claim(key)
        clock[0] += delay
        return claimed

    if delay_at == "claim":
        monkeypatch.setattr(journal, "claim", delayed_claim)
    with pytest.raises(CoordinatorBlocked):
        worker.dispatch("entry")
    record = journal.get("entry")
    assert record.state == (CommandState.PENDING if delay_at == "callback"
                            else CommandState.REJECTED)
    assert record.reservation == original.reservation
    assert record.terminal_confirmed is False
    assert gateway.submissions == []
    if delay_at == "claim":
        assert "ENTRY_GATE_EXPIRED_BEFORE_EFFECT" in str(journal.audit_events())
        worker.dispatch("entry")
        assert gateway.submissions == []
