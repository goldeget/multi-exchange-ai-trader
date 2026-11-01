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
    CommandState,
    ExecutionScope,
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
