"""OFFLINE contract harness, never a production gateway or activation path.

The injected callback and simulated snapshot supplier are trusted test boundaries,
not evidence provenance. No native leverage application/readback or automatic
SL/TP replacement is implemented. Public LIVE capability remains unsupported.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal, localcontext
from pathlib import Path
from threading import RLock
from typing import Literal, Protocol

from . import codec
from . import protection as p
from . import risk as r
from .journal import (
    CommandJournal,
    CommandKind,
    CommandRecord,
    CommandState,
    Reservation,
    ReservationLimits,
)
from .ownership import AccountWorkerLock


class CoordinatorBlocked(ValueError):
    """A fixed, sanitized harness gate refused an operation."""


@dataclass(frozen=True)
class OfflineCapabilities:
    account_id: str
    environment: str
    attached_protection: bool
    kind: str


@dataclass(frozen=True)
class OfflineOrderReport:
    account_id: str
    environment: str
    client_order_id: str
    state: Literal["ACCEPTED", "CANCELLED", "ABSENT", "UNKNOWN"]
    fills: tuple[p.FillEvent, ...] = ()


class OfflineGateway(Protocol):
    """Injected fake only. Capabilities are simulated contract declarations."""

    @property
    def capabilities(self) -> OfflineCapabilities: ...

    def submit(self, client_order_id: str, payload: object) -> OfflineOrderReport: ...

    def query(self, client_order_id: str) -> OfflineOrderReport: ...

    def cancel(self, client_order_id: str) -> OfflineOrderReport: ...


@dataclass(frozen=True)
class SimulatedReconciliation:
    account_id: str
    environment: str
    observed_at: datetime
    complete: bool
    external_activity: bool
    positions: tuple[p.PositionEvidence, ...]


@dataclass(frozen=True)
class EntryIntent:
    command_key: str
    position_id: str
    plan: r.EntryDecision
    protection_policy: p.ProtectionPolicy


EntryRiskCheck = Callable[[EntryIntent, datetime, r.ReservationTotals], r.EntryDecision]
_PROJECTION = "offline-owned-state-v1"


def _sum_amounts(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        return Decimal(0)
    exponent = min(int(value.as_tuple().exponent) for value in values)
    with localcontext() as context:
        context.prec = max(value.adjusted() for value in values) - exponent + len(values) + 3
        return sum(values, Decimal(0))


def _reserved_margin(value: Decimal) -> Decimal:
    """Journal stores at most 256 digits; reserve upward, never reduce capacity use."""
    if len(value.as_tuple().digits) <= 256:
        return value
    with localcontext() as context:
        context.prec = 256
        return value.quantize(Decimal(1).scaleb(value.adjusted() - 255), rounding=ROUND_CEILING)


class OfflineExecutionCoordinator:
    """One synchronous fake-gateway worker under an actual account OS lease.

    All workers must use the same private, local lock directory and journal.
    A released/reacquired lease requires a fresh worker and full reconciliation.
    Entry pause still permits fill recording, assessment, owned cancel and exit.
    Existing open exposure blocks every additional entry in this narrow harness.
    """

    def __init__(
        self, *, journal: CommandJournal, worker_lock: AccountWorkerLock,
        lock_directory: Path, gateway: OfflineGateway, entry_risk: EntryRiskCheck,
        clock: Callable[[], datetime], max_evidence_age: timedelta,
    ) -> None:
        if (type(worker_lock) is not AccountWorkerLock
                or type(max_evidence_age) is not timedelta or max_evidence_age < timedelta(0)):
            raise CoordinatorBlocked("EXPLICIT_LOCK_AND_EVIDENCE_AGE_REQUIRED")
        self.journal = journal
        self.worker_lock = worker_lock
        self.gateway = gateway
        self.entry_risk = entry_risk
        self._clock = clock
        self._max_age = max_evidence_age
        self._mutex = RLock()
        self._lease = worker_lock.acquisition_id
        self._expected_lock = AccountWorkerLock(lock_directory,
            account_id=journal.scope.account_id, environment=journal.scope.environment).path.resolve()
        self._active = False
        self._reconciled = False
        self._reconciled_at: datetime | None = None
        self._closed = False
        self._guard()
        self._states()  # Invalid persisted state aborts before any recovery or effect.
        journal.recover_sending(exclusive_owner=True)

    def _guard(self) -> None:
        scope = self.journal.scope
        if (self._closed or self.worker_lock.acquisition_id != self._lease
                or self.worker_lock.path.resolve() != self._expected_lock
                or not self.worker_lock.matches_scope(
                    account_id=scope.account_id, environment=scope.environment)):
            self._active = self._reconciled = False
            raise CoordinatorBlocked("ACCOUNT_WORKER_LEASE_REQUIRED")

    def _capabilities(self) -> None:
        caps = self.gateway.capabilities
        scope = self.journal.scope
        if (type(caps) is not OfflineCapabilities or caps.kind != "OFFLINE_FAKE"
                or caps.account_id != scope.account_id or caps.environment != scope.environment
                or caps.attached_protection is not True):
            self._active = self._reconciled = False
            raise CoordinatorBlocked("MATCHING_SIMULATED_ATTACHED_PROTECTION_REQUIRED")

    def _now(self) -> datetime:
        now = self._clock()
        if (type(now) is not datetime or now.tzinfo is None
                or now.utcoffset() != timedelta(0)):
            raise CoordinatorBlocked("UTC_CLOCK_REQUIRED")
        return now

    def _fresh(self, value: datetime) -> bool:
        return (type(value) is datetime and value.tzinfo is not None
                and value.utcoffset() == timedelta(0)
                and timedelta(0) <= self._now() - value <= self._max_age)

    @property
    def status(self) -> str:
        with self._mutex:
            self._guard()
            return "TEST_ACTIVE" if self._active else "TEST_PAUSED"

    def _states(self) -> tuple[p.ProtectionProjection, ...]:
        record = self.journal.get_projection(_PROJECTION)
        if record is None:
            return ()
        restored = codec.decode(record.payload_json)
        if (type(restored) is not tuple
                or any(type(item) is not p.ProtectionProjection for item in restored)):
            raise CoordinatorBlocked("INVALID_DURABLE_PROTECTION_STATE")
        states: tuple[p.ProtectionProjection, ...] = restored
        scope = p.ProtectionScope(self.journal.scope.account_id, self.journal.scope.environment)
        if (len({state.position_id for state in states}) != len(states)
                or any(state.scope != scope for state in states)):
            raise CoordinatorBlocked("INVALID_DURABLE_PROTECTION_SCOPE")
        return states

    def projection(self, position_id: str) -> p.ProtectionProjection:
        with self._mutex:
            self._guard()
            return self._position(position_id)

    def _position(self, position_id: str) -> p.ProtectionProjection:
        state = next((item for item in self._states() if item.position_id == position_id), None)
        if state is None:
            raise CoordinatorBlocked("UNKNOWN_OWNED_POSITION")
        return state

    def _persist(self, event_id: str, event: object, state: p.ProtectionProjection) -> None:
        self._guard()
        current = self.journal.get_projection(_PROJECTION)
        states = self._states()
        updated = tuple(item for item in states if item.position_id != state.position_id) + (state,)
        self.journal.apply_event(event_id, json.loads(codec.encode(event)), _PROJECTION,
            0 if current is None else current.revision, json.loads(codec.encode(updated)))

    def record_fill(self, event: p.FillEvent) -> None:
        with self._mutex:
            self._guard()
            states = self._states()
            state = next((item for item in states if item.position_id == event.position_id), None)
            if state is None:
                state = p.ProtectionProjection(p.ProtectionScope(self.journal.scope.account_id,
                    self.journal.scope.environment), event.position_id, event.instrument_id, event.side)
            reduced = p.apply_fill(state, event)
            # Different reports reusing an execution ID are distinct immutable
            # inbox evidence; the reducer retains the conflicting original report.
            event_key = hashlib.sha256(codec.encode(event).encode()).hexdigest()
            self._persist("fill:" + event_key, event, reduced)
            if reduced.conflicts:
                self._active = self._reconciled = False

    def _register_order(self, state: p.ProtectionProjection, order: p.OwnedOrder) -> None:
        updated = p.register_order(state, order)
        self._persist("order:" + order.client_order_id, order, updated)
        if updated.conflicts:
            self._active = self._reconciled = False
            raise CoordinatorBlocked("OWNED_ORDER_CONFLICT")

    def _reservation_snapshot(self, scope: r.RiskScope, now: datetime,
                              exclude: str | None = None) -> r.ReservationTotals:
        records = tuple(record for record in self.journal.list_commands()
            if record.command_key != exclude and record.reservation is not None
            and not record.terminal_confirmed)
        # Codec canonical identity changes when a held command/state changes.
        identity = json.dumps([(item.command_key, item.state.value, item.payload_hash)
                               for item in records], separators=(",", ":"))
        snapshot_id = "journal-" + hashlib.sha256(identity.encode()).hexdigest()
        amounts = tuple(item.reservation for item in records if item.reservation is not None)
        return r.ReservationTotals(scope, snapshot_id, now, True,
            _sum_amounts(tuple(item.notional_amount for item in amounts)),
            _sum_amounts(tuple(record.reservation.notional_amount for record in records
                 if record.reservation is not None and self._intent(record).plan.scope.symbol
                 == scope.symbol)),
            _sum_amounts(tuple(item.risk_amount for item in amounts)),
            _sum_amounts(tuple(item.margin_amount for item in amounts)), len(amounts))

    @staticmethod
    def _reassess(decision: r.EntryDecision) -> r.EntryDecision:
        return r.assess_entry(policy=decision.policy, proposal=decision.proposal,
            market=decision.market, instrument=decision.instrument, account=decision.account,
            tiers=decision.tiers, reservations=decision.reservations, now=decision.assessed_at,
            stage=decision.stage, liquidation=decision.liquidation,
            leverage_readback=decision.leverage_readback)

    @staticmethod
    def _plan_key(decision: r.EntryDecision) -> object:
        return (decision.scope, decision.policy, decision.proposal, decision.quantity,
            decision.notional, decision.estimated_loss, decision.cost_allowance,
            decision.funding_allowance, decision.leverage, decision.initial_margin,
            decision.margin_amount, decision.tier_id, decision.formula_version,
            decision.required_leverage, decision.venue_leverage_min, decision.venue_leverage_max,
            decision.clamp_reasons, decision.stop_distance_fraction,
            decision.liquidation_price, decision.liquidation_distance_fraction,
            decision.liquidation_buffer_fraction,
            tuple(getattr(decision.market, field.name) for field in fields(decision.market)
                  if field.name not in ("quote_id", "observed_at")),
            tuple(getattr(decision.instrument, field.name) for field in fields(decision.instrument)
                  if field.name != "observed_at"),
            tuple(getattr(decision.tiers, field.name) for field in fields(decision.tiers)
                  if field.name != "observed_at"))

    def _entry_gate(self) -> None:
        self._guard()
        self._capabilities()
        if (not self._active or not self._reconciled or self._reconciled_at is None
                or not self._fresh(self._reconciled_at)):
            raise CoordinatorBlocked("RECONCILE_AND_EXPLICIT_ACTIVATION_REQUIRED")
        if (any(state.entries_blocked for state in self._states())
                or self.journal.list_commands((CommandState.UNKNOWN, CommandState.SENDING))):
            raise CoordinatorBlocked("UNRESOLVED_EXPOSURE_OR_COMMAND")
        if any(record.kind is CommandKind.ENTRY and not record.terminal_confirmed
               for record in self.journal.list_commands((CommandState.ACKNOWLEDGED,))):
            raise CoordinatorBlocked("UNRESOLVED_ENTRY_CONFIRMATION")

    def queue_entry(self, command_key: str, position_id: str, plan: r.EntryDecision,
                    protection_policy: p.ProtectionPolicy) -> CommandRecord:
        with self._mutex:
            self._entry_gate()
            scope = self.journal.scope
            try:
                if (type(plan) is not r.EntryDecision or self._reassess(plan) != plan
                        or not self._fresh(plan.assessed_at)
                        or (plan.scope.account_id, plan.scope.environment)
                        != (scope.account_id, scope.environment)
                        or protection_policy.revision != plan.policy.revision
                        or protection_policy.stop_price != plan.proposal.stop_price
                        or (plan.proposal.side == "LONG"
                            and protection_policy.take_profit_price <= plan.market.entry_price)
                        or (plan.proposal.side == "SHORT"
                            and protection_policy.take_profit_price >= plan.market.entry_price)):
                    raise CoordinatorBlocked("INVALID_QUEUED_PLAN")
            except (ValueError, TypeError, AttributeError):
                raise CoordinatorBlocked("INVALID_QUEUED_PLAN") from None
            reservations = self._reservation_snapshot(plan.scope, self._now())
            if (plan.reservations.notional_amount, plan.reservations.risk_amount,
                    plan.reservations.margin_amount, plan.reservations.position_count) != (
                    reservations.notional_amount, reservations.risk_amount,
                    reservations.margin_amount, reservations.position_count):
                raise CoordinatorBlocked("RESERVATIONS_MUST_EXCLUDE_ACCOUNT_COUNTERS")
            payload = {"position_id": position_id, "plan": json.loads(codec.encode(plan)),
                       "protection_policy": json.loads(codec.encode(protection_policy))}
            policy, account = plan.policy, plan.account
            record = self.journal.register(command_key, CommandKind.ENTRY, payload,
                reservation=Reservation(plan.estimated_loss, plan.notional,
                                        _reserved_margin(plan.margin_amount)),
                limits=ReservationLimits(policy.max_total_risk, policy.max_total_notional,
                    policy.max_total_margin, policy.max_positions, account.total_risk,
                    account.total_notional, account.total_margin, account.position_count))
            states = self._states()
            state = next((item for item in states if item.position_id == position_id), None)
            if state is None:
                state = p.ProtectionProjection(p.ProtectionScope(scope.account_id, scope.environment),
                    position_id, plan.scope.symbol, "BUY" if plan.proposal.side == "LONG" else "SELL")
            self._register_order(state, p.OwnedOrder(record.client_order_id, position_id,
                plan.scope.symbol, state.entry_side, "ENTRY", plan.quantity, False,
                protection_policy.generation, policy.revision))
            return record

    def _intent(self, record: CommandRecord) -> EntryIntent:
        payload = json.loads(record.payload_json)
        plan = codec.decode(json.dumps(payload["plan"]))
        policy = codec.decode(json.dumps(payload["protection_policy"]))
        if (type(plan) is not r.EntryDecision or type(policy) is not p.ProtectionPolicy
                or type(payload["position_id"]) is not str):
            raise CoordinatorBlocked("INVALID_IMMUTABLE_ENTRY_PAYLOAD")
        return EntryIntent(record.command_key, payload["position_id"], plan, policy)

    def _validate_submit(self, record: CommandRecord) -> None:
        intent = self._intent(record)
        state = self._owned_state(record.client_order_id)
        expected_order = p.OwnedOrder(record.client_order_id, intent.position_id,
            intent.plan.scope.symbol, "BUY" if intent.plan.proposal.side == "LONG" else "SELL",
            "ENTRY", intent.plan.quantity, False, intent.protection_policy.generation,
            intent.plan.policy.revision)
        if expected_order not in state.orders:
            raise CoordinatorBlocked("DURABLE_IMMUTABLE_ENTRY_MAPPING_REQUIRED")
        now = self._now()
        reservations = self._reservation_snapshot(intent.plan.scope, now, record.command_key)
        try:
            final = self.entry_risk(intent, now, reservations)
            if (type(final) is not r.EntryDecision or final.assessed_at != now
                    or final.stage != "SUBMIT" or final.submission_ready is not True
                    or final.blockers or final.reservations != reservations
                    or self._plan_key(final) != self._plan_key(intent.plan)
                    or self._reassess(final) != final):
                raise CoordinatorBlocked("SUBMIT_RISK_OR_PLAN_CHANGED")
        except Exception:  # noqa: BLE001 - callback errors must not leak arbitrary secret strings.
            raise CoordinatorBlocked("SUBMIT_RISK_OR_PLAN_CHANGED") from None

    def _required(self, key: str) -> CommandRecord:
        record = self.journal.get(key)
        if record is None:
            raise CoordinatorBlocked("UNKNOWN_COMMAND")
        return record

    def dispatch(self, command_key: str) -> CommandRecord:
        with self._mutex:
            self._guard()
            self._capabilities()
            record = self._required(command_key)
            if record.state is not CommandState.PENDING:
                return record  # UNKNOWN is only queried, never re-claimed.
            if record.kind is CommandKind.ENTRY:
                self._entry_gate()
                self._validate_submit(record)
            elif record.kind is CommandKind.EXIT:
                self._validate_exit(record)
            elif record.kind is not CommandKind.CANCEL:
                raise CoordinatorBlocked("UNIMPLEMENTED_EFFECT_KIND")
            self._guard()
            claimed = self.journal.claim(command_key)  # Commits before any fake effect.
            if claimed is None:
                return self._required(command_key)
            self._guard()
            try:
                if record.kind is CommandKind.CANCEL:
                    target = self._cancel_target(record)
                    report = self.gateway.cancel(target)
                else:
                    target = record.client_order_id
                    report = self.gateway.submit(target, json.loads(record.payload_json))
                self._consume_report(claimed, target, report)
            except Exception:  # noqa: BLE001 - every ambiguous effect retains its ID/reservation.
                self._guard()
                self.journal.mark_unknown(command_key, "AMBIGUOUS_OFFLINE_EFFECT")
                self._active = self._reconciled = False
            return self._required(command_key)

    def _consume_report(self, record: CommandRecord, target: str,
                        report: OfflineOrderReport) -> None:
        self._guard()
        scope = self.journal.scope
        if (type(report) is not OfflineOrderReport
                or (report.account_id, report.environment, report.client_order_id)
                != (scope.account_id, scope.environment, target)
                or report.state not in ("ACCEPTED", "CANCELLED", "ABSENT", "UNKNOWN")
                or type(report.fills) is not tuple):
            raise CoordinatorBlocked("INVALID_SCOPED_QUERY_REPORT")
        for event in report.fills:
            self.record_fill(event)
        if report.state in ("ABSENT", "UNKNOWN"):
            if record.state in (CommandState.SENDING, CommandState.UNKNOWN):
                self.journal.mark_unknown(record.command_key, "UNRESOLVED_SAME_ID_QUERY")
            self._active = self._reconciled = False
            if record.state is CommandState.ACKNOWLEDGED:
                raise CoordinatorBlocked("PRIOR_ACK_IS_NOT_CURRENT_ORDER_RECONCILIATION")
            return
        if report.state == "CANCELLED":
            state = self._owned_state(target)
            self._persist("cancelled:" + target, ("CANCELLED", target),
                          p.record_order_cancelled(state, target))
        if record.state in (CommandState.SENDING, CommandState.UNKNOWN):
            self.journal.acknowledge(record.command_key, "SCOPED_SIMULATED_REPORT")

    def _owned_state(self, client_order_id: str) -> p.ProtectionProjection:
        for state in self._states():
            if any(order.client_order_id == client_order_id for order in state.orders):
                return state
        raise CoordinatorBlocked("IMMUTABLE_OWNED_ORDER_REQUIRED")

    def queue_exit(self, command_key: str, position_id: str, quantity: Decimal,
                   policy_revision: str) -> CommandRecord:
        with self._mutex:
            self._guard()
            self._capabilities()
            state = self._position(position_id)
            if (type(quantity) is not Decimal or not quantity.is_finite()
                    or quantity <= 0 or quantity > state.owned_quantity or state.conflicts):
                raise CoordinatorBlocked("EXIT_EXCEEDS_CONFIRMED_OWNERSHIP")
            record = self.journal.register(command_key, CommandKind.EXIT,
                {"position_id": position_id, "instrument_id": state.instrument_id,
                 "quantity": quantity, "side": "SELL" if state.entry_side == "BUY" else "BUY",
                 "reduce_only": True, "policy_revision": policy_revision})
            self._register_order(state, p.OwnedOrder(record.client_order_id, position_id,
                state.instrument_id, "SELL" if state.entry_side == "BUY" else "BUY", "EXIT",
                quantity, True, 0, policy_revision))
            return record

    def _validate_exit(self, record: CommandRecord) -> None:
        state = self._owned_state(record.client_order_id)
        order = next(order for order in state.orders if order.client_order_id == record.client_order_id)
        if (order.role != "EXIT" or not order.reduce_only or state.conflicts
                or order.quantity > state.owned_quantity):
            raise CoordinatorBlocked("EXIT_EXCEEDS_CONFIRMED_OWNERSHIP")

    def _cancel_target(self, record: CommandRecord) -> str:
        payload = json.loads(record.payload_json)
        target = payload["target_client_order_id"]
        if type(target) is not str:
            raise CoordinatorBlocked("IMMUTABLE_OWNED_ORDER_REQUIRED")
        self._owned_state(target)
        return target

    def cancel_owned(self, command_key: str, client_order_id: str) -> CommandRecord:
        with self._mutex:
            self._guard()
            self._capabilities()
            self._owned_state(client_order_id)
            self.journal.register(command_key, CommandKind.CANCEL,
                                  {"target_client_order_id": client_order_id})
            return self.dispatch(command_key)

    def reconcile(self, snapshot: SimulatedReconciliation) -> None:
        with self._mutex:
            self._guard()
            self._capabilities()
            self._active = self._reconciled = False
            scope = self.journal.scope
            if (type(snapshot) is not SimulatedReconciliation
                    or (snapshot.account_id, snapshot.environment)
                    != (scope.account_id, scope.environment)
                    or snapshot.complete is not True or snapshot.external_activity is not False
                    or not self._fresh(snapshot.observed_at) or type(snapshot.positions) is not tuple):
                raise CoordinatorBlocked("COMPLETE_SIMULATED_RECONCILIATION_REQUIRED")
            for record in self.journal.list_commands():
                if record.state not in (CommandState.UNKNOWN, CommandState.ACKNOWLEDGED):
                    continue
                target = (self._cancel_target(record) if record.kind is CommandKind.CANCEL
                          else record.client_order_id)
                try:
                    self._consume_report(record, target, self.gateway.query(target))
                except Exception:  # noqa: BLE001 - sanitize untrusted fake query failures.
                    raise CoordinatorBlocked("AMBIGUOUS_SAME_ID_RECONCILIATION") from None
            states = self._states()
            positions = {item.position_id: item for item in snapshot.positions}
            if (len(positions) != len(snapshot.positions)
                    or set(positions) != {state.position_id for state in states
                                          if state.owned_quantity > 0}
                    or any(state.conflicts for state in states)
                    or self.journal.list_commands((CommandState.UNKNOWN, CommandState.SENDING))):
                raise CoordinatorBlocked("UNRESOLVED_RECONCILIATION")
            for state in states:
                if state.owned_quantity == 0:
                    continue
                position = positions[state.position_id]
                if (position.scope != state.scope or position.instrument_id != state.instrument_id
                        or position.side != state.entry_side or not position.complete
                        or position.has_external_activity or position.quantity != state.owned_quantity
                        or not self._fresh(position.observed_at)
                        or position.observed_at != snapshot.observed_at
                        or (state.latest_fill_at is not None
                            and position.observed_at < state.latest_fill_at)):
                    raise CoordinatorBlocked("POSITION_RECONCILIATION_CONFLICT")
            self._guard()
            self._reconciled = True
            self._reconciled_at = snapshot.observed_at

    def activate(self) -> None:
        with self._mutex:
            self._guard()
            self._capabilities()
            if (not self._reconciled or self._reconciled_at is None
                    or not self._fresh(self._reconciled_at)):
                raise CoordinatorBlocked("FULL_RECONCILIATION_REQUIRED")
            self._active = True

    def pause_entries(self) -> None:
        with self._mutex:
            self._guard()
            self._active = False

    def assess_protection(self, position_id: str, policy: p.ProtectionPolicy,
                          evidence: tuple[p.ProtectionEvidence, ...],
                          position: p.PositionEvidence | None) -> p.ProtectionAssessment:
        with self._mutex:
            self._guard()
            state = self._position(position_id)
            return p.assess_protection(state, policy, evidence, position,
                                       now=self._now(), max_age=self._max_age)

    def shutdown(self) -> None:
        with self._mutex:
            self._guard()
            self._active = self._reconciled = False
            self._closed = True  # Caller closes journal/releases lock after this returns.
