"""Durable native lifecycle, with real handoff held behind missing qualification.

CommandJournal owns immutable commands/reservations; RuntimeStateStore owns
per-position inbox/projection state. Cross-store operations commit command intent
first, then mapping, and only then may claim/send. Incomplete mapping never sends.
ACK/fill/report processing remains available while entries are paused. No HTTP
write path, implicit credentials, native node startup or fake qualification flag.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal, localcontext
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import uuid4
from weakref import WeakValueDictionary

from . import codec, modes
from . import protection as p
from . import reservations_v2 as reservations
from . import risk as r
from .account_binding import AuthenticatedAccountBinding, BybitDemoAccountSession
from .attached_protection_readback_v1 import (
    AttachedProtectionTargetV1,
    AttachedProtectionViewV1,
    AuthenticatedAttachedProtectionReceiptV1,
)
from .bybit_native import (
    AttachedProtection,
    LinearFixedLimitNativeOrderRequestV2,
    NativeBybitBridge,
    NativeOrderRequest,
    NativeRequest,
)
from .execution_credit import (
    CorroboratedExecutionCredit,
    CreditReplayState,
    ExecutionCorroborationConflict,
    ExecutionCreditResult,
    ExecutionObservationReference,
    HistoricalOrderEvidence,
    candidate_identity,
    decision_event_id,
    execution_guard,
    reduce_credit_event,
    same_credit,
)
from .execution_provenance import (
    FillQuarantineRecord,
    NativeExecutionCandidate,
    VenueExecutionObservation,
    compare_execution_candidate,
)
from .history import HistoryError, _symbol
from .history_receipts import AuthenticatedHistoryReceipt
from .history_recovery import (
    HistoryExecutionCredit,
    HistoryExecutionEvidence,
    HistoryOrderEvidence,
    HistoryRecoveryConflict,
    HistoryRecoveryReplayState,
    HistoryRecoveryResult,
    NativeHistoryResolution,
    compare_history_candidate,
    history_credit_digest,
    history_decision_event_id,
    history_resolution_event_id,
    reduce_history_event,
)
from .history_store import HistoryStore
from .journal import (
    CommandJournal,
    CommandKind,
    CommandRecord,
    CommandState,
    Reservation,
    ReservationLimits,
)
from .monitoring_budget import MonitoringReadBudget
from .monitoring_models import ApplicationMonitoringSummary
from .native_controls import (
    LeverageObservation,
    NativeLeverageControl,
    observe_leverage,
    require_flat_snapshot,
)
from .native_events import NativeEventContext, NativeEventDecoder, NativeOrderEvidence
from .ownership import AccountWorkerLock
from .product_scope import _validate_account
from .risk_v2_codec import DurableEntryIntentV2, decode_entry_intent_v2, encode_entry_intent_v2
from .state_store import RuntimeStateStore
from .venue_readback import BybitVenueReadback, VenueSnapshot

QUALIFICATION_BLOCKERS = (
    "NATIVE_WORKER_QUALIFICATION_REQUIRED",
    "AUTHENTICATED_ACCOUNT_BINDING_REQUIRED",
    "COMPLETE_EXECUTION_HISTORY_REQUIRED",
    "QUALIFIED_LOSS_AND_LIQUIDATION_EVIDENCE_REQUIRED",
    "NATIVE_LEVERAGE_SEQUENCE_AND_READBACK_REQUIRED",
    "ATTACHED_CHILD_OWNERSHIP_AND_PROTECTION_QUALIFICATION_REQUIRED",
    "OWNED_CANCEL_AND_PROTECTION_REPAIR_REQUIRED",
    "VENUE_READBACK_QUALIFICATION_REQUIRED",
)
_PROTECTION_RECOVERY_PROJECTION_V1 = "protection-recovery-admission-v1"
_PROTECTION_RECOVERY_SCHEMA_V1 = "PROTECTION_RECOVERY_ADMISSION_V1"
_PROTECTION_RECOVERY_OVERFLOW_PROJECTION_V1 = "protection-recovery-overflow-v1"
_PROTECTION_RECOVERY_OVERFLOW_SCHEMA_V1 = "PROTECTION_RECOVERY_OVERFLOW_V1"
_PROTECTION_RECOVERY_ACTIVE_LIMIT_V1 = 256
_PROTECTION_RECOVERY_PROJECTION_LIMIT_V1 = 65536
RiskCheck = Callable[[r.EntryDecision, datetime, r.ReservationTotals], r.EntryDecision]
_OWNERS: WeakValueDictionary[str, NativeExecutionRuntime] = WeakValueDictionary()
_OWNERS_LOCK = RLock()


class RuntimeBlocked(ValueError):
    """Fixed execution blocker; never transport error text."""


class ProtectionRecoveryGateError(RuntimeBlocked):
    """A durable protection-recovery admission gate cannot be trusted."""


class ProtectionRecoveryPersistenceError(ProtectionRecoveryGateError):
    """An authenticated recovery gate could not be durably recorded."""


@dataclass(frozen=True)
class NativeOwnedRequest:
    """Validated immutable ownership, without recovery writes or fresh authority."""

    request: NativeRequest
    command_state: CommandState
    venue_order_ids: frozenset[str]


NativeOwnershipResolver = Callable[[tuple[tuple[str, str], ...]], tuple[NativeOwnedRequest, ...]]


@dataclass(frozen=True)
class RuntimeStatus:
    state: Literal["BLOCKED", "PAUSED", "RUNNING"]
    entries_paused: bool
    unresolved_commands: int
    owned_positions: int
    blockers: tuple[str, ...]
    production_supported: Literal[False] = False


@dataclass
class _ReplayContext:
    candidates: tuple[NativeExecutionCandidate, ...] = ()
    credit: CreditReplayState = field(default_factory=CreditReplayState)
    history: HistoryRecoveryReplayState = field(default_factory=HistoryRecoveryReplayState)


_CapturedContext = tuple[tuple[NativeExecutionCandidate, ...], CreditReplayState, HistoryRecoveryReplayState]


def _record_digest(records: tuple[object, ...]) -> str:
    # Frame canonical complete records; sorting makes traversal order irrelevant.
    encoded = json.dumps(sorted(codec.encode(item) for item in records),
                         ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _sum(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        return Decimal(0)
    exponent = min(int(item.as_tuple().exponent) for item in values)
    with localcontext() as context:
        context.prec = max(item.adjusted() for item in values) - exponent + len(values) + 3
        return sum(values, Decimal(0))


def _margin(value: Decimal) -> Decimal:
    if len(value.as_tuple().digits) <= 256:
        return value
    with localcontext() as context:
        context.prec = 256
        return value.quantize(Decimal(1).scaleb(value.adjusted() - 255), rounding=ROUND_CEILING)


def _reassess(plan: r.EntryDecision, now: datetime) -> r.EntryDecision:
    return r.assess_entry(policy=plan.policy, proposal=plan.proposal, market=plan.market,
        instrument=plan.instrument, account=plan.account, tiers=plan.tiers,
        reservations=plan.reservations, now=now, stage=plan.stage,
        liquidation=plan.liquidation, leverage_readback=plan.leverage_readback)


def _economic_identity(plan: r.EntryDecision) -> object:
    """Fresh snapshots may advance; immutable command economics may not."""
    omitted = {"market", "instrument", "account", "tiers", "reservations", "liquidation",
               "leverage_readback", "assessed_at", "stage", "submission_ready", "blockers",
               "liquidation_price", "liquidation_distance_fraction", "liquidation_buffer_fraction",
               "required_leverage", "initial_margin", "margin_amount"}
    # Adding qualified evidence changes risk's precision budget. Repeating ratios
    # may differ in their last digit, even for identical notional/leverage/costs.
    # Compare the same conservative margin actually reserved by the journal.
    return (tuple(getattr(plan, field.name) for field in fields(plan) if field.name not in omitted),
        _margin(plan.margin_amount),
        tuple(getattr(plan.market, field.name) for field in fields(plan.market)
              if field.name not in ("observed_at", "quote_id")),
        tuple(getattr(plan.instrument, field.name) for field in fields(plan.instrument)
              if field.name != "observed_at"),
        tuple(getattr(plan.tiers, field.name) for field in fields(plan.tiers)
              if field.name != "observed_at"))


class NativeExecutionRuntime:
    """One designated account worker, with passive recovery and monitoring.

    Start and every native effect path consult the fixed
    missing-qualification gate. Removing it requires an independently reviewed
    deployed worker/identity/evidence implementation, not a caller-provided bool.
    The native dispatch implementation is exercised with fake native boundaries.
    """

    production_supported: Literal[False] = False

    def __init__(self, *, journal: CommandJournal, store: RuntimeStateStore,
                 worker_lock: AccountWorkerLock, lock_directory: Path,
                 bridge: NativeBybitBridge, entry_risk: RiskCheck,
                 clock: Callable[[], datetime], max_evidence_age: timedelta,
                 receipt_session: BybitDemoAccountSession | None = None,
                 history_store: HistoryStore | None = None,
                 _pre_recovery: Callable[[NativeOwnershipResolver], None] | None = None,
                 ) -> None:
        if (type(journal) is not CommandJournal or type(store) is not RuntimeStateStore
                or type(bridge) is not NativeBybitBridge
                or type(worker_lock) is not AccountWorkerLock
                or type(max_evidence_age) is not timedelta or max_evidence_age <= timedelta(0)
                or journal.scope != store.scope
                or bridge.scope != p.ProtectionScope(
                    journal.scope.account_id, journal.scope.environment)):
            raise RuntimeBlocked("MATCHING_DURABLE_NATIVE_SCOPE_REQUIRED")
        if ((receipt_session is None) != (history_store is None)
                or (receipt_session is not None and type(receipt_session) is not BybitDemoAccountSession)
                or (history_store is not None and (type(history_store) is not HistoryStore
                                                   or history_store.scope != bridge.scope))):
            raise RuntimeBlocked("MATCHING_RECEIPT_DEPENDENCIES_REQUIRED")
        self._receipt_session, self._history_store = receipt_session, history_store
        self.journal, self.store, self.bridge = journal, store, bridge
        self.worker_lock = worker_lock
        self.entry_risk = entry_risk
        self._clock, self._max_age = clock, max_evidence_age
        self._lease = worker_lock.acquisition_id
        self._lock_path = AccountWorkerLock(lock_directory,
            account_id=journal.scope.account_id, environment=journal.scope.environment).path.resolve()
        self._mutex = RLock()
        self._readbacks: dict[str, VenueSnapshot] = {}
        self._attached_readbacks_v1: dict[str, tuple[AuthenticatedAttachedProtectionReceiptV1, AttachedProtectionTargetV1]] = {}
        self._attached_read_operations_v1: dict[str, object] = {}
        self._leverage_readbacks: dict[str, LeverageObservation] = {}
        self._control_busy = False
        self._projections: dict[str, tuple[int, p.ProtectionProjection,
                                           dict[str, tuple[object, ...]], _ReplayContext]] = {}
        self._active = self._closed = False
        self._guard()
        # The OS lease excludes other processes. Also exclude a second object
        # sharing the same live descriptor; it must not recover an active sender.
        with _OWNERS_LOCK:
            previous = _OWNERS.get(str(self._lock_path))
            if (previous is not None and not previous._closed
                    and previous._lease == self._lease and previous.worker_lock.acquired):
                raise RuntimeBlocked("ACCOUNT_RUNTIME_ALREADY_RUNNING")
            rows = self._restore_rows()
            batch, _ = self._restore_batch(_phase="MAPPING")
            self._guard()
            if self._restore_rows() != rows:
                raise RuntimeBlocked("DURABLE_RESTORE_CHANGED")
            if _pre_recovery is not None:
                _pre_recovery(self.native_owned_requests)
            self._guard()
            if self._restore_rows() != rows:
                raise RuntimeBlocked("DURABLE_RESTORE_CHANGED")
            batch, _ = self._restore_batch(_phase="MAPPING")
            self._guard()
            if self._restore_rows() != rows:
                raise RuntimeBlocked("DURABLE_RESTORE_CHANGED")
            for _, request, _ in batch:
                self._register_restored_request(request)
            self._guard()
            if self._restore_rows() != rows:
                raise RuntimeBlocked("DURABLE_RESTORE_CHANGED")
            self.journal.recover_sending(exclusive_owner=True)
            self.store.recover(exclusive_owner=True)
            for record, _, orphan in batch:
                if orphan:
                    self._recover_entry_mapping(record)
            _OWNERS[str(self._lock_path)] = self

    def _restore_rows(self) -> tuple[object, ...]:
        """Logical local rows only: detect callback changes, never issue authority."""
        connections = [self.journal._db, self.store._db]
        if self._history_store is not None:
            connections.append(self._history_store._db)
        result: list[object] = []
        for connection in connections:
            headers = (connection.execute("PRAGMA user_version").fetchone()[0],
                       connection.execute("PRAGMA application_id").fetchone()[0])
            objects = tuple(tuple(row) for row in connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name,tbl_name,sql"))
            content = []
            for object_type, name, _, _ in objects:
                if object_type != "table":
                    continue
                quoted = '"' + name.replace('"', '""') + '"'
                rows = tuple(sorted(repr(tuple(row)) for row in connection.execute("SELECT * FROM " + quoted)))
                content.append((name, rows))
            result.append((headers, objects, tuple(content)))
        return tuple(result)

    def _restore_batch(self, native_venues: tuple[tuple[str, str], ...] = (), *,
                       _phase: Literal["MAPPING", "NATIVE"]) -> tuple[
            tuple[tuple[CommandRecord, NativeRequest, bool], ...], tuple[NativeOwnedRequest, ...]]:
        self._guard()
        if type(_phase) is not str or _phase not in ("MAPPING", "NATIVE"):
            raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
        records = self.journal.list_commands()  # Validate every row, including terminal/controls.
        self._projections.clear()
        states = self._states()
        contexts = tuple((self._projections[state.position_id][3].candidates,
                          self._projections[state.position_id][3].credit,
                          self._projections[state.position_id][3].history) for state in states)
        return self._enumerate_requests(records, states, contexts, native_venues, _phase=_phase)

    def _enumerate_requests(self, records: tuple[CommandRecord, ...],
                            states: tuple[p.ProtectionProjection, ...],
                            contexts: tuple[_CapturedContext, ...],
                            native_venues: tuple[tuple[str, str], ...], *,
                            _phase: Literal["MAPPING", "NATIVE", "OBSERVATION"]) -> tuple[
            tuple[tuple[CommandRecord, NativeRequest, bool], ...], tuple[NativeOwnedRequest, ...]]:
        """Enumerate the captured view without replaying or changing its caches."""
        if (type(_phase) is not str or _phase not in ("MAPPING", "NATIVE", "OBSERVATION")
                or len(states) != len(contexts) or (_phase == "OBSERVATION" and native_venues != ())):
            raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
        if (type(native_venues) is not tuple or any(type(pair) is not tuple or len(pair) != 2
                or any(type(value) is not str or not value.strip() or len(value) > 256
                       for value in pair) for pair in native_venues)):
            raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
        candidates = tuple(item for context in contexts for item in context[0])
        clients = {record.client_order_id for record in records
                   if record.kind in (CommandKind.ENTRY, CommandKind.EXIT)}
        if _phase == "NATIVE" and any(
                order.role in ("ENTRY", "EXIT") and order.client_order_id not in clients
                for state in states for order in state.orders):
            raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
        batch: list[tuple[CommandRecord, NativeRequest, bool]] = []
        result: list[NativeOwnedRequest] = []
        entry_positions: set[str] = set()
        for record in records:
            if record.kind not in (CommandKind.ENTRY, CommandKind.EXIT):
                continue
            matches = tuple(state for state in states if any(
                order.client_order_id == record.client_order_id for order in state.orders))
            orphan = not matches and record.kind is CommandKind.ENTRY and record.state is CommandState.PENDING
            if orphan:
                state, order = self._entry_mapping(record)
                request = self._request_for_state(record, p.register_order(state, order))
                if (type(request) is LinearFixedLimitNativeOrderRequestV2
                        and self._history_store is None):
                    raise RuntimeBlocked("V2_ORPHAN_HISTORY_REQUIRED")
            elif len(matches) == 1:
                request = self._request_for_state(record, matches[0])
            else:
                raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
            if record.kind is CommandKind.ENTRY:
                if request.owned.position_id in entry_positions:
                    raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
                entry_positions.add(request.owned.position_id)
            batch.append((record, request, orphan))
            if type(request) is NativeOrderRequest and (orphan or _phase == "MAPPING"):
                # Legacy observation retains replayed conflict state. Only the
                # public native resolver applies restrictive V1 venue admission.
                continue
            venues, evidence = self._history_venues(request, native_venues, candidates)
            if orphan:
                if venues or evidence:
                    raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
                continue
            result.append(NativeOwnedRequest(request, record.state, venues))
        foreign = tuple((state, context) for state, context in zip(states, contexts, strict=True)
                        if any(order.role in ("ENTRY", "EXIT") and order.client_order_id not in clients
                               for order in state.orders))
        if len(foreign) > 1:
            raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
        for state, context in foreign:
            if not self._legacy_foreign_quarantine(state, context, records, tuple(batch)):
                raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
        return tuple(batch), tuple(result)

    def _legacy_foreign_quarantine(self, state: p.ProtectionProjection, context: _CapturedContext,
                                   records: tuple[CommandRecord, ...],
                                   batch: tuple[tuple[CommandRecord, NativeRequest, bool], ...]) -> bool:
        """One retained legacy conflict graph is observation data, never a request."""
        candidates, credit, history = context
        if (type(state) is not p.ProtectionProjection or len(state.orders) != 1
                or type(state.orders[0]) is not p.OwnedOrder or state.scope != self.bridge.scope
                or state.owned_quantity != 0 or state.executions or state.cancelled_order_ids
                or not state.entries_blocked or credit.credits or credit.resolved_candidate_ids
                or history.credits or history.decisions or history.resolutions
                or len(candidates) != 1 or type(candidates[0]) is not NativeExecutionCandidate
                or len(credit.decisions) != 1
                or type(credit.decisions[0]) is not ExecutionCorroborationConflict):
            return False
        order, candidate, decision = state.orders[0], candidates[0], credit.decisions[0]
        order.__post_init__()
        candidate.__post_init__()
        decision.__post_init__()
        fill = candidate.fill
        if (order.role != "ENTRY" or order.reduce_only or order.trigger_price is not None
                or order.trigger_source is not None
                or any(record.client_order_id == order.client_order_id for record in records)
                or order.position_id != state.position_id or order.instrument_id != state.instrument_id
                or order.side != state.entry_side or fill.scope != state.scope
                or fill.position_id != state.position_id or fill.client_order_id != order.client_order_id
                or fill.instrument_id != state.instrument_id or fill.side != order.side
                or decision.prior_credit_event_id is None):
            return False
        inbox = self.store.get_inbox_event(decision.prior_credit_event_id)
        if inbox is None:
            return False
        prior = codec.decode(inbox.payload_json)
        if type(prior) is not CorroboratedExecutionCredit:
            return False
        prior.__post_init__()
        prior_fill = prior.candidate.fill
        if (prior.candidate_id != candidate_identity(prior.candidate)
                or codec.encode(prior) != inbox.payload_json
                or inbox.position_id != prior_fill.position_id or prior_fill.scope != self.bridge.scope
                or prior.source.scope != prior_fill.scope
                or inbox.event_id != execution_guard(prior_fill.scope,
                    prior_fill.instrument_id, prior_fill.execution_id)):
            return False
        matches = tuple((record, request) for record, request, _ in batch
                        if record.kind is CommandKind.ENTRY and type(request) is NativeOrderRequest
                        and self._entry_version(record) == 1 and request.scope == prior_fill.scope
                        and request.owned.client_order_id == prior_fill.client_order_id
                        and request.owned.position_id == prior_fill.position_id
                        and request.owned.instrument_id == prior_fill.instrument_id
                        and request.owned.side == prior_fill.side and not request.owned.reduce_only)
        if len(matches) != 1:
            return False
        original_record, original_request = matches[0]
        original = original_request.owned
        # A missing historical label remains data. A different real command
        # cannot be borrowed; all financial/NATIVE prior-credit checks stay strict.
        if any(record.command_key == prior.command_key and record.command_key != original_record.command_key
               for record in records):
            return False
        prior_events = self.store.list_position_events(inbox.position_id)
        decoded = tuple(codec.decode(row.payload_json) for row in prior_events)
        owners = tuple(item for item in decoded if type(item) is p.OwnedOrder
                       and item.client_order_id == prior_fill.client_order_id)
        quarantines = tuple(item for item in decoded if type(item) is FillQuarantineRecord
                            and codec.encode(item.candidate) == codec.encode(prior.candidate))
        if (len(owners) != 1 or codec.encode(owners[0]) != codec.encode(original)
                or len(quarantines) != 1 or not any(
                    row.event_id == "native:" + candidate_identity(prior.candidate)
                    and row.payload_json == codec.encode(quarantines[0]) for row in prior_events)):
            return False
        if (original.position_id == order.position_id
                or original.client_order_id == order.client_order_id
                or candidate.venue_order_id == prior.candidate.venue_order_id
                or codec.encode(order) != codec.encode(replace(original,
                    position_id=order.position_id, client_order_id=order.client_order_id))
                or codec.encode(fill) != codec.encode(replace(prior.candidate.fill,
                    position_id=order.position_id, client_order_id=order.client_order_id))):
            return False
        expected = ExecutionCorroborationConflict(candidate_identity(candidate), state.scope,
            fill.instrument_id, fill.execution_id, candidate.venue_order_id, order.client_order_id,
            prior.source, prior.history_variant_hash, (), ("VENUE_ORDER_ID_MISMATCH",),
            execution_guard(state.scope, fill.instrument_id, fill.execution_id))
        conflicts = (p.ProjectionConflict("AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED",
                                         client_order_id=order.client_order_id),
                     p.ProjectionConflict("EXECUTION_CORROBORATION_CONFLICT",
                         event=fill.to_owned_fill(), client_order_id=order.client_order_id))
        if codec.encode(decision) != codec.encode(expected) or codec.encode(state.conflicts) != codec.encode(conflicts):
            return False
        events = self.store.list_position_events(state.position_id)
        expected_events = {
            "native:" + hashlib.sha256(codec.encode(order).encode()).hexdigest(): codec.encode(order),
            "native:" + candidate_identity(candidate): codec.encode(
                FillQuarantineRecord(candidate, "AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED")),
            decision_event_id(decision): codec.encode(decision),
        }
        return (len(events) == 3 and {row.event_id: row.payload_json for row in events} == expected_events)

    def native_owned_requests(self, native_venues: tuple[tuple[str, str], ...] = ()
                              ) -> tuple[NativeOwnedRequest, ...]:
        """Read-only full replay seam shared by startup and quiesced capture."""
        return self._restore_batch(native_venues, _phase="NATIVE")[1]

    def _history_venues(self, request: NativeRequest, native_venues: tuple[tuple[str, str], ...],
                        candidates: tuple[NativeExecutionCandidate, ...]) -> tuple[frozenset[str], bool]:
        venues = self._known_venues(request.owned.client_order_id, candidates)
        # Only the coordinator supplies seeds from fully validated detached
        # native orders. They restrict identity, never grant event authority.
        venues.update(venue for client, venue in native_venues
                      if client == request.owned.client_order_id)
        seen = bool(venues)
        def validate_venues() -> None:
            if (len(venues) > 16 or any(type(venue) is not str or not venue.strip()
                                      or len(venue) > 256 for venue in venues)):
                raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
        if type(request) is LinearFixedLimitNativeOrderRequestV2:
            validate_venues()
        if self._history_store is not None:
            while True:
                validate_venues()
                queried_venues = frozenset(venues)
                for evidence in self._history_store.order_evidence(
                        client_order_id=request.owned.client_order_id,
                        venue_order_ids=tuple(sorted(queried_venues))):
                    row, owned = evidence.record, request.owned
                    seen = True
                    if (evidence.conflict_keys or row.client_order_id != owned.client_order_id
                            or row.symbol + "-LINEAR.BYBIT" != owned.instrument_id
                            or row.side != ("Buy" if owned.side == "BUY" else "Sell")
                            or row.quantity != owned.quantity or row.reduce_only != owned.reduce_only
                            or row.position_idx != 0 or not row.venue_order_id):
                        raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
                    venues.add(row.venue_order_id)
                for execution in self._history_store.execution_identity_evidence(
                        client_order_id=request.owned.client_order_id,
                        venue_order_ids=tuple(sorted(queried_venues))):
                    fill, owned = execution.record, request.owned
                    seen = True
                    if (execution.conflict_keys or fill.client_order_id != owned.client_order_id
                            or fill.symbol + "-LINEAR.BYBIT" != owned.instrument_id
                            or fill.side != ("Buy" if owned.side == "BUY" else "Sell")
                            or not fill.venue_order_id):
                        raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
                    # Partial fill quantity/price are not original order economics.
                    venues.add(fill.venue_order_id)
                if venues == queried_venues:
                    break
        if len(venues) > 1:
            raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
        return frozenset(venues), seen

    def monitoring_state(self) -> ApplicationMonitoringSummary:
        """Typed read-only logical view; no order acknowledgement or fill promotion."""
        with self._mutex:
            self._guard()
            records = self.journal.list_commands()
            states = self._states()
            contexts = tuple((self._projections[state.position_id][3].candidates,
                              self._projections[state.position_id][3].credit,
                              self._projections[state.position_id][3].history) for state in states)
            owned = self._enumerate_requests(records, states, contexts, (), _phase="OBSERVATION")[1]
            connections = [self.journal._db, self.store._db]
            if self._history_store is not None:
                connections.append(self._history_store._db)
            ledgers = []
            for connection in connections:
                tables = connection.execute(
                    "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
                content = []
                for name, schema in tables:
                    quoted = '"' + name.replace('"', '""') + '"'
                    rows = sorted(repr(tuple(row)) for row in connection.execute("SELECT * FROM " + quoted))
                    content.append((name, schema, rows))
                ledgers.append(content)
            digest = hashlib.sha256(repr((ledgers, self._active, self._control_busy)).encode()).hexdigest()
            conflicts = tuple(sorted(
                ["PROJECTION_CONFLICT" for state, context in zip(states, contexts, strict=True)
                 if not self._monitorable_quarantine(state, _ReplayContext(*context))]
                + ["CREDIT_DECISION" for context in contexts
                    if context[1].decisions or context[2].decisions]))
            return ApplicationMonitoringSummary(owned, records, states,
                tuple(item for context in contexts for item in context[0]),
                tuple(item for context in contexts for item in context[1].credits),
                tuple(item for context in contexts for item in context[2].credits),
                conflicts, digest)

    @staticmethod
    def _monitorable_quarantine(state: p.ProtectionProjection, context: _ReplayContext) -> bool:
        """Classify monitoring only; the durable accounting hold remains untouched."""
        seen: set[str] = set()
        for conflict in state.conflicts:
            client = conflict.client_order_id
            order = next((row for row in state.orders if row.client_order_id == client), None)
            if (type(conflict) is not p.ProjectionConflict
                    or conflict.reason != "AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED"
                    or conflict.event is not None or conflict.order is not None
                    or not client or client in seen or order is None):
                return False
            seen.add(client)
            candidates = tuple(row for row in context.candidates
                               if row.fill.client_order_id == client)
            if not candidates or any(
                type(row) is not NativeExecutionCandidate
                or row.fill.scope != state.scope or row.fill.position_id != state.position_id
                or row.fill.instrument_id != state.instrument_id or row.fill.side != order.side
                or not row.venue_order_id for row in candidates
            ):
                return False
        return True

    @property
    def receipt_session(self) -> BybitDemoAccountSession | None:
        """Pinned issuer identity for worker lifetime fences; never an authority grant."""
        return self._receipt_session

    @property
    def history_store(self) -> HistoryStore | None:
        return self._history_store

    def _guard(self) -> None:
        if (self._closed or self.worker_lock.acquisition_id != self._lease
                or self.worker_lock.path.resolve() != self._lock_path
                or not self.worker_lock.matches_scope(account_id=self.journal.scope.account_id,
                                                      environment=self.journal.scope.environment)):
            self._active = False
            raise RuntimeBlocked("ACCOUNT_WORKER_LEASE_REQUIRED")

    def _protection_recovery_projection_v1(self) -> tuple[int, dict[str, dict[str, object]]]:
        try:
            record = self.journal.get_projection(_PROTECTION_RECOVERY_PROJECTION_V1)
        except Exception:  # noqa: BLE001 - inability to read the admission gate fails closed.
            self._active = False
            raise ProtectionRecoveryPersistenceError(
                "PROTECTION_RECOVERY_PERSISTENCE_FAILED") from None
        if record is None:
            return 0, {}
        try:
            payload = json.loads(record.payload)
            if (type(payload) is not dict or set(payload) != {"schema", "active"}
                    or payload["schema"] != _PROTECTION_RECOVERY_SCHEMA_V1
                    or type(payload["active"]) is not dict or len(payload["active"]) > 256):
                raise ValueError
            active: dict[str, dict[str, object]] = {}
            required = {"entry_client_order_id", "policy_revision", "policy_generation",
                        "target_sha256", "observed_at", "server_observed_at",
                        "reason_code"}
            for position_id, item in payload["active"].items():
                if (type(position_id) is not str or not position_id.strip()
                        or len(position_id) > 256 or type(item) is not dict
                        or set(item) != required
                        or type(item["entry_client_order_id"]) is not str
                        or re.fullmatch(r"ec_[0-9a-f]{32}", item["entry_client_order_id"]) is None
                        or type(item["policy_revision"]) is not str or not item["policy_revision"]
                        or len(item["policy_revision"]) > 256
                        or type(item["policy_generation"]) is not int
                        or item["policy_generation"] < 0
                        or type(item["target_sha256"]) is not str
                        or re.fullmatch(r"[0-9a-f]{64}", item["target_sha256"]) is None
                        or type(item["reason_code"]) is not str
                        or item["reason_code"] != "AUTHENTICATED_PROTECTION_COVERAGE_INCOMPLETE"):
                    raise ValueError
                for field_name in ("observed_at", "server_observed_at"):
                    stamp = datetime.fromisoformat(item[field_name])
                    if (type(item[field_name]) is not str or stamp.tzinfo is None
                            or stamp.utcoffset() != timedelta(0)):
                        raise ValueError
                active[position_id] = item
            return record.revision, active
        except Exception:  # noqa: BLE001 - retained recovery state must fail closed.
            self._active = False
            raise ProtectionRecoveryGateError("PROTECTION_RECOVERY_STATE_INVALID") from None

    def _recovery_required_v1(self) -> bool:
        return (bool(self._protection_recovery_projection_v1()[1])
                or self._protection_recovery_overflow_state_v1()[1])

    def _protection_recovery_overflow_state_v1(self) -> tuple[int, bool]:
        try:
            record = self.journal.get_projection(_PROTECTION_RECOVERY_OVERFLOW_PROJECTION_V1)
        except Exception:  # noqa: BLE001 - inability to read the global latch fails closed.
            self._active = False
            raise ProtectionRecoveryPersistenceError(
                "PROTECTION_RECOVERY_PERSISTENCE_FAILED") from None
        if record is None:
            return 0, False
        try:
            payload = json.loads(record.payload)
            if (type(payload) is not dict
                    or set(payload) != {"schema", "reason_code", "position_id_sha256",
                                        "observed_at", "server_observed_at"}
                    or payload["schema"] != _PROTECTION_RECOVERY_OVERFLOW_SCHEMA_V1
                    or payload["reason_code"] not in (
                        "PROTECTION_RECOVERY_ACTIVE_COUNT_LIMIT",
                        "PROTECTION_RECOVERY_PROJECTION_SIZE_LIMIT")
                    or type(payload["position_id_sha256"]) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", payload["position_id_sha256"]) is None):
                raise ValueError
            for field_name in ("observed_at", "server_observed_at"):
                stamp = datetime.fromisoformat(payload[field_name])
                if (type(payload[field_name]) is not str or stamp.tzinfo is None
                        or stamp.utcoffset() != timedelta(0)):
                    raise ValueError
            return record.revision, True
        except Exception:  # noqa: BLE001 - a damaged global latch cannot be ignored.
            self._active = False
            raise ProtectionRecoveryGateError("PROTECTION_RECOVERY_STATE_INVALID") from None

    def _latch_protection_recovery_overflow_v1(self, *, target: AttachedProtectionTargetV1,
            snapshot: VenueSnapshot, reason_code: str) -> None:
        revision, latched = self._protection_recovery_overflow_state_v1()
        if latched:
            return
        server_observed = snapshot.server_observed_at
        if server_observed is None:
            raise ProtectionRecoveryGateError("AUTHENTICATED_ATTACHED_PROTECTION_REQUIRED")
        payload = {
            "schema": _PROTECTION_RECOVERY_OVERFLOW_SCHEMA_V1,
            "reason_code": reason_code,
            "position_id_sha256": hashlib.sha256(target.position_id.encode()).hexdigest(),
            "observed_at": snapshot.observed_at.isoformat(),
            "server_observed_at": server_observed.isoformat(),
        }
        event = {
            "schema": _PROTECTION_RECOVERY_OVERFLOW_SCHEMA_V1,
            "action": "LATCHED",
            "reason_code": reason_code,
            "position_id": target.position_id,
            "target_sha256": target.projection_sha256,
            "cycle_id": snapshot.cycle_id,
            "observed_at": snapshot.observed_at.isoformat(),
            "server_observed_at": server_observed.isoformat(),
        }
        event_json = json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        event_id = "protection-recovery-overflow-v1-" + hashlib.sha256(event_json.encode()).hexdigest()
        try:
            self.journal.apply_event(event_id, event, _PROTECTION_RECOVERY_OVERFLOW_PROJECTION_V1,
                                     revision, payload)
        except Exception:  # noqa: BLE001 - capacity overflow must persist or runtime stays inactive.
            self._active = False
            raise ProtectionRecoveryPersistenceError(
                "PROTECTION_RECOVERY_PERSISTENCE_FAILED") from None

    def _apply_protection_recovery_event_v1(self, *, event: dict[str, object],
            active: dict[str, dict[str, object]], expected_revision: int) -> None:
        event_json = json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        event_id = "protection-recovery-v1-" + hashlib.sha256(event_json.encode()).hexdigest()
        payload = {"schema": _PROTECTION_RECOVERY_SCHEMA_V1, "active": active}
        try:
            self.journal.apply_event(event_id, event, _PROTECTION_RECOVERY_PROJECTION_V1,
                                     expected_revision, payload)
        except Exception:  # noqa: BLE001 - do not continue after a failed durable gate write.
            self._active = False
            raise ProtectionRecoveryPersistenceError(
                "PROTECTION_RECOVERY_PERSISTENCE_FAILED") from None

    def _require_protection_recovery_clear_v1(self) -> None:
        if self._recovery_required_v1():
            raise ProtectionRecoveryGateError("PROTECTION_RECOVERY_REQUIRED")

    def _require_protection_recovery_v1(self, target: AttachedProtectionTargetV1,
            snapshot: VenueSnapshot) -> None:
        revision, active = self._protection_recovery_projection_v1()
        previous = active.get(target.position_id)
        if previous is not None and previous["entry_client_order_id"] != target.parent_client_order_id:
            raise ProtectionRecoveryGateError("PROTECTION_RECOVERY_OWNER_MISMATCH")
        overflow = self._protection_recovery_overflow_state_v1()[1]
        if overflow and previous is None:
            return
        observed = snapshot.observed_at
        server_observed = snapshot.server_observed_at
        if server_observed is None:
            raise ProtectionRecoveryGateError("AUTHENTICATED_ATTACHED_PROTECTION_REQUIRED")
        if previous is not None:
            prior_observed = datetime.fromisoformat(previous["observed_at"])
            prior_server = datetime.fromisoformat(previous["server_observed_at"])
            if observed <= prior_observed or server_observed <= prior_server:
                return
        item = {
            "entry_client_order_id": target.parent_client_order_id,
            "policy_revision": target.policy_revision,
            "policy_generation": target.policy_generation,
            "target_sha256": target.projection_sha256,
            "observed_at": observed.isoformat(),
            "server_observed_at": server_observed.isoformat(),
            "reason_code": "AUTHENTICATED_PROTECTION_COVERAGE_INCOMPLETE",
        }
        if previous is None and len(active) >= _PROTECTION_RECOVERY_ACTIVE_LIMIT_V1:
            self._latch_protection_recovery_overflow_v1(target=target, snapshot=snapshot,
                reason_code="PROTECTION_RECOVERY_ACTIVE_COUNT_LIMIT")
            return
        candidate_active = dict(active)
        candidate_active[target.position_id] = item
        candidate_payload = {"schema": _PROTECTION_RECOVERY_SCHEMA_V1, "active": candidate_active}
        candidate_size = len(json.dumps(candidate_payload, ensure_ascii=True, sort_keys=True,
                                        separators=(",", ":")).encode("utf-8"))
        if candidate_size > _PROTECTION_RECOVERY_PROJECTION_LIMIT_V1:
            self._latch_protection_recovery_overflow_v1(target=target, snapshot=snapshot,
                reason_code="PROTECTION_RECOVERY_PROJECTION_SIZE_LIMIT")
            return
        active[target.position_id] = item
        event = {
            "schema": _PROTECTION_RECOVERY_SCHEMA_V1,
            "action": "REQUIRED",
            "position_id": target.position_id,
            "entry_client_order_id": target.parent_client_order_id,
            "policy_revision": target.policy_revision,
            "policy_generation": target.policy_generation,
            "target_sha256": target.projection_sha256,
            "cycle_id": snapshot.cycle_id,
            "observed_at": observed.isoformat(),
            "server_observed_at": server_observed.isoformat(),
            "reason_code": "AUTHENTICATED_PROTECTION_COVERAGE_INCOMPLETE",
        }
        self._apply_protection_recovery_event_v1(event=event, active=active,
                                                 expected_revision=revision)

    def _clear_protection_recovery_v1(self, target: AttachedProtectionTargetV1,
            snapshot: VenueSnapshot) -> None:
        revision, active = self._protection_recovery_projection_v1()
        previous = active.get(target.position_id)
        if previous is None:
            return
        if (previous["entry_client_order_id"] != target.parent_client_order_id
                or previous["policy_revision"] != target.policy_revision
                or previous["policy_generation"] != target.policy_generation):
            raise ProtectionRecoveryGateError("PROTECTION_RECOVERY_OWNER_MISMATCH")
        server_observed = snapshot.server_observed_at
        if server_observed is None:
            raise ProtectionRecoveryGateError("AUTHENTICATED_ATTACHED_PROTECTION_REQUIRED")
        prior_observed = datetime.fromisoformat(previous["observed_at"])
        prior_server = datetime.fromisoformat(previous["server_observed_at"])
        if snapshot.observed_at <= prior_observed or server_observed <= prior_server:
            return
        del active[target.position_id]
        event = {
            "schema": _PROTECTION_RECOVERY_SCHEMA_V1,
            "action": "CLEARED",
            "position_id": target.position_id,
            "entry_client_order_id": target.parent_client_order_id,
            "policy_revision": target.policy_revision,
            "policy_generation": target.policy_generation,
            "target_sha256": target.projection_sha256,
            "cycle_id": snapshot.cycle_id,
            "observed_at": snapshot.observed_at.isoformat(),
            "server_observed_at": server_observed.isoformat(),
        }
        self._apply_protection_recovery_event_v1(event=event, active=active,
                                                 expected_revision=revision)

    def _now(self) -> datetime:
        now = self._clock()
        if (type(now) is not datetime or now.tzinfo is None
                or now.utcoffset() != timedelta(0)):
            raise RuntimeBlocked("UTC_CLOCK_REQUIRED")
        return now

    def _fresh(self, when: datetime) -> bool:
        return timedelta(0) <= self._now() - when <= self._max_age

    def _require_qualification(self) -> None:
        raise RuntimeBlocked("NATIVE_WORKER_QUALIFICATION_REQUIRED")

    @property
    def status(self) -> RuntimeStatus:
        with self._mutex:
            self._guard()
            unresolved = len(self.journal.list_commands((CommandState.UNKNOWN, CommandState.SENDING)))
            states = self._states()
            return RuntimeStatus("BLOCKED", not self._active, unresolved,
                sum(state.owned_quantity > 0 for state in states), QUALIFICATION_BLOCKERS)

    def start(self) -> None:
        with self._mutex:
            self._guard()
            self._require_qualification()
            if (self.journal.list_commands((CommandState.UNKNOWN, CommandState.SENDING))
                    or any(state.entries_blocked for state in self._states())):
                raise RuntimeBlocked("UNRESOLVED_EXPOSURE_OR_COMMAND")
            self._active = True

    def pause_entries(self) -> None:
        with self._mutex:
            self._guard()
            self._active = False

    def shutdown(self) -> None:
        with self._mutex:
            self._guard()
            self._active = False
            self._closed = True

    def transport_lost(self) -> None:
        """A native disconnect/timeout holds every unacknowledged handoff."""
        with self._mutex:
            self._guard()
            self._active = False
            for record in self.journal.list_commands((CommandState.SENDING,)):
                self.journal.mark_unknown(record.command_key, "NATIVE_TRANSPORT_LOST")

    def _states(self) -> tuple[p.ProtectionProjection, ...]:
        states: list[p.ProtectionProjection] = []
        for record in self.store.list_positions():
            cached = self._projections.get(record.position_id)
            if cached is not None and cached[0] == record.revision:
                states.append(cached[1])
                continue
            persisted = codec.decode(record.payload_json)
            identities: dict[str, tuple[object, ...]] = {}
            context = _ReplayContext()
            events = self.store.list_position_events(record.position_id)
            decoded: list[object] = []
            for item in events:
                event = codec.decode(item.payload_json)
                if type(event) is CorroboratedExecutionCredit:
                    fill = event.candidate.fill
                    if item.event_id != execution_guard(
                            fill.scope, fill.instrument_id, fill.execution_id):
                        raise RuntimeBlocked("INVALID_DURABLE_CREDIT_GUARD")
                elif type(event) is ExecutionCorroborationConflict:
                    key = decision_event_id(event)
                    if item.event_id != key:
                        raise RuntimeBlocked("INVALID_DURABLE_DECISION_ID")
                    if event.prior_credit_event_id is not None:
                        original = self.store.get_inbox_event(event.prior_credit_event_id)
                        prior = None if original is None else codec.decode(original.payload_json)
                        if type(prior) is CorroboratedExecutionCredit:
                            # Existing native V3 records replay as their original data,
                            # without granting live authority or extending their schema.
                            fill = prior.candidate.fill
                            if (original is None or original.position_id != fill.position_id
                                    or fill.scope != self.bridge.scope
                                    or prior.source.scope != fill.scope
                                    or execution_guard(fill.scope, fill.instrument_id, fill.execution_id)
                                    != event.prior_credit_event_id):
                                raise RuntimeBlocked("INVALID_DURABLE_PRIOR_CREDIT")
                        elif (type(prior) is not HistoryExecutionCredit
                              or self._prior_credit(event.prior_credit_event_id) is None):
                            raise RuntimeBlocked("INVALID_DURABLE_PRIOR_CREDIT")
                elif type(event) is HistoryExecutionCredit:
                    if item.event_id != execution_guard(event.execution.source.scope,
                            event.owned_order.instrument_id, event.execution.execution.execution_id):
                        raise RuntimeBlocked("INVALID_DURABLE_CREDIT_GUARD")
                elif type(event) is HistoryRecoveryConflict:
                    if item.event_id != history_decision_event_id(event):
                        raise RuntimeBlocked("INVALID_DURABLE_DECISION_ID")
                    if (event.prior_credit_event_id is not None
                            and self._prior_credit(event.prior_credit_event_id) is None):
                        raise RuntimeBlocked("INVALID_DURABLE_PRIOR_CREDIT")
                elif type(event) is NativeHistoryResolution:
                    if item.event_id != history_resolution_event_id(event):
                        raise RuntimeBlocked("INVALID_DURABLE_RESOLUTION_ID")
                    prior = self._prior_credit(event.prior_credit_event_id)
                    if (type(prior) is not HistoryExecutionCredit
                            or history_credit_digest(prior) != event.credit_digest):
                        raise RuntimeBlocked("INVALID_DURABLE_PRIOR_CREDIT")
                decoded.append(event)
            if type(persisted) is p.ProtectionProjection:
                # Version-one projections already have independently committed inbox
                # evidence. Their next write replaces the aggregate with a summary.
                state = p.ProtectionProjection(persisted.scope, persisted.position_id,
                                               persisted.instrument_id, persisted.entry_side)
                for event in decoded:
                    self._verify_replay_command(event, state)
                    state = self._reduce_event(state, event, identities, context)
                if (context.credit.credits or context.credit.decisions or context.history.credits
                        or context.history.decisions or context.history.resolutions):
                    raise RuntimeBlocked("INVALID_DURABLE_PROJECTION_HISTORY")
                # Retain the aggregate's original conflicts. Any legacy fill
                # tuple above has already failed closed without a recovery write.
                if (replace(state, conflicts=persisted.conflicts) != persisted
                        or any(item not in state.conflicts for item in persisted.conflicts)):
                    raise RuntimeBlocked("INVALID_DURABLE_PROJECTION_HISTORY")
            elif (type(persisted) is tuple and len(persisted) == 11
                  and persisted[0] == "RUNTIME_POSITION_V1") or (
                      type(persisted) is tuple and len(persisted) == 13
                      and persisted[0] == "RUNTIME_POSITION_V2") or (
                          type(persisted) is tuple and len(persisted) == 17
                          and persisted[0] == "RUNTIME_POSITION_V3") or (
                              type(persisted) is tuple and len(persisted) == 23
                              and persisted[0] == "RUNTIME_POSITION_V4"):
                state = p.ProtectionProjection(persisted[1], persisted[2],
                                               persisted[3], persisted[4])
                for event in decoded:
                    self._verify_replay_command(event, state)
                    state = self._reduce_event(state, event, identities, context)
                if self._summary(state, identities, context.credit, context.history) != persisted:
                    raise RuntimeBlocked("INVALID_DURABLE_PROJECTION_HISTORY")
            else:
                raise RuntimeBlocked("INVALID_DURABLE_PROJECTION")
            if (type(state) is not p.ProtectionProjection or state.scope != self.bridge.scope
                    or state.position_id != record.position_id):
                raise RuntimeBlocked("INVALID_DURABLE_PROJECTION")
            self._projections[record.position_id] = (record.revision, state, identities, context)
            states.append(state)
        return tuple(states)

    @staticmethod
    def _summary(state: p.ProtectionProjection,
                 identities: dict[str, tuple[object, ...]] | None = None,
                 replay: CreditReplayState | None = None,
                 history_replay: HistoryRecoveryReplayState | None = None) -> tuple[object, ...]:
        """Bounded current state; immutable event rows hold all historical evidence."""
        summary = ("RUNTIME_POSITION_V1", state.scope, state.position_id, state.instrument_id,
                state.entry_side, state.signed_quantity, state.latest_fill_at,
                len(state.orders), len(state.executions), len(state.cancelled_order_ids),
                len(state.conflicts))
        v4 = history_replay is not None and bool(history_replay.credits
            or history_replay.decisions or history_replay.resolutions)
        v3 = v4 or (replay is not None and bool(replay.credits or replay.decisions))
        if not identities and not v3:
            return summary
        keys = sorted(str(key) for values in (identities or {}).values() for key in values)
        digest = hashlib.sha256("".join(keys).encode()).hexdigest()
        if v3:
            replay = CreditReplayState() if replay is None else replay
            native = ("RUNTIME_POSITION_V3", *summary[1:], len(keys), digest,
                      len(replay.credits), _record_digest(replay.credits),
                      len(replay.decisions), _record_digest(replay.decisions))
            if v4 and history_replay is not None:
                return ("RUNTIME_POSITION_V4", *native[1:],
                        len(history_replay.credits), _record_digest(history_replay.credits),
                        len(history_replay.decisions), _record_digest(history_replay.decisions),
                        len(history_replay.resolutions), _record_digest(history_replay.resolutions))
            return native
        return ("RUNTIME_POSITION_V2", *summary[1:], len(keys), digest)

    @staticmethod
    def _candidate_identity(candidate: NativeExecutionCandidate) -> str:
        """Canonical candidate content; receipt/init times never create executions."""
        return candidate_identity(candidate)

    @staticmethod
    def _reduce_event(state: p.ProtectionProjection, event: object,
                      identities: dict[str, tuple[object, ...]],
                      context: _ReplayContext | None = None) -> p.ProtectionProjection:
        if type(event) in (HistoryExecutionCredit, HistoryRecoveryConflict, NativeHistoryResolution):
            if context is None:
                raise RuntimeBlocked("HISTORY_RECOVERY_REPLAY_CONTEXT_REQUIRED")
            assert isinstance(event, (HistoryExecutionCredit, HistoryRecoveryConflict, NativeHistoryResolution))
            state, context.credit, context.history = reduce_history_event(state, event,
                candidates=context.candidates, native_replay=context.credit, replay=context.history)
            return state
        if type(event) in (CorroboratedExecutionCredit, ExecutionCorroborationConflict):
            if context is None:
                raise RuntimeBlocked("EXECUTION_CREDIT_REPLAY_CONTEXT_REQUIRED")
            assert isinstance(event, (CorroboratedExecutionCredit, ExecutionCorroborationConflict))
            if (type(event) is CorroboratedExecutionCredit
                    and context.history.credit_for(execution_guard(event.candidate.fill.scope,
                        event.candidate.fill.instrument_id, event.candidate.fill.execution_id)) is not None):
                raise RuntimeBlocked("INVALID_DURABLE_CREDIT_GUARD")
            state, context.credit = reduce_credit_event(state, event,
                candidates=context.candidates, replay=context.credit)
            return state
        if type(event) is p.OwnedOrder:
            return p.register_order(state, event)
        if type(event) is FillQuarantineRecord:
            event.__post_init__()
            fill = event.candidate.fill
            order = next((item for item in state.orders
                          if item.client_order_id == fill.client_order_id), None)
            if (order is None or fill.scope != state.scope
                    or fill.position_id != state.position_id
                    or fill.instrument_id != state.instrument_id or fill.side != order.side):
                raise RuntimeBlocked("QUARANTINE_OWNERSHIP_REQUIRED")
            identity = NativeExecutionRuntime._candidate_identity(event.candidate)
            previous = identities.get(fill.execution_id, ())
            if identity in previous:
                return state
            conflicts = list(state.conflicts)
            incomplete = p.ProjectionConflict(event.reason, client_order_id=fill.client_order_id)
            if incomplete not in conflicts:
                conflicts.append(incomplete)
            if previous:
                conflict = p.ProjectionConflict("EXECUTION_ID_CONFLICT", event=fill.to_owned_fill())
                if conflict not in conflicts:
                    conflicts.append(conflict)
            identities[fill.execution_id] = (*previous, identity)
            if context is not None:
                context.candidates += (event.candidate,)
            return replace(state, conflicts=tuple(conflicts))
        if type(event) is not tuple:
            raise RuntimeBlocked("INVALID_DURABLE_EVENT")
        if len(event) == 3 and event[0] == "READBACK":
            return state
        if (len(event) == 2 and event[0] == "CANCEL_IDENTITY_UNVERIFIED"
                and type(event[1]) is str
                and any(order.client_order_id == event[1] for order in state.orders)):
            conflict = p.ProjectionConflict(event[0], client_order_id=event[1])
            return (state if conflict in state.conflicts else
                    replace(state, conflicts=state.conflicts + (conflict,)))
        if len(event) != 6:
            raise RuntimeBlocked("INVALID_DURABLE_EVENT")
        fill_identity = event[5]
        if event[0] == "FILL" or fill_identity is not None:
            # Old tuples contain no transport/source provenance. Refuse restore
            # before recovery writes, retaining both their bytes and quantities.
            raise RuntimeBlocked("LEGACY_NATIVE_FILL_PROVENANCE_REQUIRED")
        if event[0] in ("CANCELED", "EXPIRED"):
            return p.record_order_cancelled(state, event[1])
        return state

    def projection(self, position_id: str) -> p.ProtectionProjection:
        with self._mutex:
            self._guard()
            for state in self._states():
                if state.position_id == position_id:
                    return state
            raise RuntimeBlocked("UNKNOWN_OWNED_POSITION")

    def persisted_candidates(self, *, symbol: str | None = None) -> tuple[NativeExecutionCandidate, ...]:
        """Read-only quarantine history, including credited records, without time filtering."""
        with self._mutex:
            self._guard()
            if symbol is not None:
                _symbol(symbol)
            self._states()
            result: list[NativeExecutionCandidate] = []
            for position_id in sorted(self._projections):
                for candidate in sorted(self._projections[position_id][3].candidates,
                                        key=candidate_identity):
                    if symbol is not None:
                        record = next((item for item in self.journal.list_commands()
                            if item.client_order_id == candidate.fill.client_order_id), None)
                        if record is None:
                            raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
                        request = self._request(record)
                        if request.owned.instrument_id != candidate.fill.instrument_id:
                            raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
                        if request.owned.instrument_id != symbol + "-LINEAR.BYBIT":
                            continue
                    result.append(candidate)
            return tuple(result)

    def corroborate_execution(self, *, receipt: AuthenticatedHistoryReceipt,
                              binding: AuthenticatedAccountBinding, observation_key: str,
                              candidate_id: str) -> ExecutionCreditResult:
        """Join an exact live receipt to committed history and owned native evidence.

        All reads and the sole accounting transaction are synchronous under the
        lease mutex. Receipt acquisition/GET and history ingestion happen outside.
        """
        with self._mutex:
            self._guard()
            session, history = self._receipt_session, self._history_store
            if session is None or history is None:
                raise RuntimeBlocked("AUTHENTICATED_EXECUTION_RECEIPT_REQUIRED")
            view = session.require_history_receipt(receipt, binding=binding, scope=self.bridge.scope)
            try:
                history.require_observation(view.snapshot, observation_key)
            except HistoryError as error:
                if str(error) != "HISTORY_OBSERVATION_NOT_FOUND":
                    raise
                return ExecutionCreditResult(candidate_id, "UNRESOLVED", ("HISTORY_OBSERVATION_REQUIRED",))
            # Validate a caller's identifier even when no matching candidate exists.
            missing = ExecutionCreditResult(candidate_id, "UNRESOLVED",
                                            ("PERSISTED_EXECUTION_CANDIDATE_REQUIRED",))
            candidates = self.persisted_candidates()
            candidate = next((item for item in candidates
                              if candidate_identity(item) == candidate_id), None)
            if candidate is None:
                return missing
            fill = candidate.fill
            # New-credit exclusions cannot suppress contradiction of a credit
            # already recorded under this account-wide execution guard.
            guard = execution_guard(fill.scope, fill.instrument_id, fill.execution_id)
            previous_row = self.store.get_inbox_event(guard)
            previous = self._prior_credit(guard)
            if type(previous) is HistoryExecutionCredit:
                result = self._recover_history(receipt=receipt, binding=binding,
                    observation_key=observation_key, execution_id=fill.execution_id, candidate=candidate)
                return ExecutionCreditResult(candidate_id, result.status, result.reason_codes)
            if previous_row is not None:
                decoded = codec.decode(previous_row.payload_json)
                if (type(decoded) is not CorroboratedExecutionCredit
                        or execution_guard(decoded.candidate.fill.scope,
                            decoded.candidate.fill.instrument_id, decoded.candidate.fill.execution_id) != guard):
                    raise RuntimeBlocked("INVALID_DURABLE_CREDIT_GUARD")
                previous = decoded
            assert previous is None or isinstance(previous, CorroboratedExecutionCredit)
            record = next((item for item in self.journal.list_commands()
                           if item.client_order_id == fill.client_order_id), None)
            if record is None or (previous is None and (
                    record.state in (CommandState.PENDING, CommandState.REJECTED) or record.terminal_confirmed)):
                return ExecutionCreditResult(candidate_id, "UNRESOLVED", ("OWNED_COMMAND_INELIGIBLE",))
            request = self._request(record)
            try:
                evidence = history.execution_evidence(observation_key, fill.execution_id)
            except HistoryError as error:
                if str(error) != "HISTORY_EXECUTION_NOT_FOUND":
                    raise
                return ExecutionCreditResult(candidate_id, "UNRESOLVED", ("HISTORY_EXECUTION_REQUIRED",))
            row, snapshot = evidence.record, view.snapshot
            if previous is None and row.execution_type != "Trade":
                return ExecutionCreditResult(candidate_id, "UNRESOLVED", ("HISTORY_TRADE_REQUIRED",))
            if previous is None and not row.client_order_id:
                return ExecutionCreditResult(candidate_id, "UNRESOLVED",
                                             ("HISTORY_CLIENT_ORDER_ID_REQUIRED",))
            source = ExecutionObservationReference(snapshot.scope, view.binding_reference,
                observation_key, snapshot.snapshot_id, snapshot.symbol, snapshot.window.start_ms,
                snapshot.window.end_ms, snapshot.observed_at, snapshot.server_observed_at)
            observation: VenueExecutionObservation | None = None
            codes: list[str] = []
            if row.execution_type != "Trade":
                codes.append("HISTORY_TRADE_REQUIRED")
            if not row.client_order_id:
                codes.extend(("HISTORY_CLIENT_ORDER_ID_REQUIRED", "CLIENT_ORDER_ID_MISMATCH"))
            if not row.venue_order_id:
                codes.append("VENUE_ORDER_ID_MISMATCH")
            if not codes:
                observation = VenueExecutionObservation(snapshot.scope, view.binding_reference,
                    snapshot.snapshot_id, row.execution_id, row.client_order_id, row.venue_order_id,
                    request.owned.instrument_id, "BUY" if row.side == "Buy" else "SELL",
                    row.quantity, row.price, row.fee, row.fee_currency, row.time_ms * 1_000_000)
                codes.extend(compare_execution_candidate(candidate, observation).mismatch_codes)
            if (request.owned.instrument_id != row.symbol + "-LINEAR.BYBIT"
                    or row.symbol != snapshot.symbol):
                codes.append("INSTRUMENT_ID_MISMATCH")
            if evidence.conflict_keys:
                codes.append("HISTORY_EXECUTION_CONFLICT")
            order_sources: list[HistoricalOrderEvidence] = []
            conflict_keys = set(evidence.conflict_keys)
            venue_ids = tuple(sorted({candidate.venue_order_id, row.venue_order_id} - {""}))
            for stored in history.order_evidence(client_order_id=fill.client_order_id,
                                                  venue_order_ids=venue_ids):
                order = stored.record
                if (stored.conflict_keys or order.client_order_id != fill.client_order_id
                        or order.venue_order_id != candidate.venue_order_id
                        or request.owned.instrument_id != order.symbol + "-LINEAR.BYBIT"
                        or order.side != ("Buy" if request.owned.side == "BUY" else "Sell")
                        or order.position_idx != 0 or order.reduce_only != request.owned.reduce_only
                        or order.quantity != request.owned.quantity):
                    codes.append("HISTORY_ORDER_CONFLICT")
                    conflict_keys.update(stored.conflict_keys)
                    for original in stored.observations:
                        order_sources.append(HistoricalOrderEvidence(fill.scope,
                            original.observation_key, original.snapshot_id, original.symbol,
                            original.window.start_ms, original.window.end_ms,
                            original.observed_at, original.server_observed_at, original.transport_injected,
                            stored.variant_hash, order.venue_order_id, order.client_order_id,
                            "Buy" if order.side == "Buy" else "Sell", order.position_idx, order.reduce_only))
            variants = {candidate_identity(item) for item in candidates
                        if execution_guard(item.fill.scope, item.fill.instrument_id,
                                           item.fill.execution_id) == guard}
            if len(variants) != 1:
                codes.append("NATIVE_CANDIDATE_VARIANT_CONFLICT")
            # Known venue mappings come from immutable stored evidence, never a
            # guessed association or an in-memory native cache.
            known = {item.venue_order_id for item in candidates
                     if item.fill.client_order_id == fill.client_order_id}
            for position in self.store.list_positions():
                for inbox in self.store.list_position_events(position.position_id):
                    native = codec.decode(inbox.payload_json)
                    if (type(native) is tuple and len(native) == 6
                            and native[1] == fill.client_order_id and native[2] is not None):
                        known.add(native[2])
                    elif (type(native) is CorroboratedExecutionCredit
                          and native.candidate.fill.client_order_id == fill.client_order_id):
                        known.add(native.candidate.venue_order_id)
            if known != {row.venue_order_id}:
                codes.append("VENUE_ORDER_ID_MISMATCH")
            if self._has_execution_decision(guard):
                codes.append("EXECUTION_DECISION_CONFLICT")
            proposed: CorroboratedExecutionCredit | None = None
            if not codes:
                assert observation is not None
                proposed = CorroboratedExecutionCredit(candidate_id, candidate, observation,
                    source, evidence.variant_hash, record.command_key)
                if previous is not None:
                    if same_credit(previous, proposed):
                        # Still validate authority at the end of this synchronous
                        # read path. A duplicate never changes first receipt metadata.
                        session.require_history_receipt(receipt, binding=binding, scope=self.bridge.scope)
                        self._guard()
                        return ExecutionCreditResult(candidate_id, "DUPLICATE", ())
                    codes.append("EXECUTION_GUARD_CONFLICT")
            if codes:
                decision = ExecutionCorroborationConflict(candidate_id, fill.scope,
                    fill.instrument_id, fill.execution_id, row.venue_order_id, row.client_order_id,
                    source, evidence.variant_hash, tuple(sorted(conflict_keys)),
                    tuple(dict.fromkeys(codes)), guard if previous is not None else None,
                    tuple(order_sources))
                return self._commit_corroboration(decision, candidate, receipt, binding)
            assert proposed is not None
            return self._commit_corroboration(proposed, candidate, receipt, binding)

    def _prior_credit(self, guard: str) -> HistoryExecutionCredit | CorroboratedExecutionCredit | None:
        inbox = self.store.get_inbox_event(guard)
        if inbox is None:
            return None
        event = codec.decode(inbox.payload_json)
        if type(event) is HistoryExecutionCredit:
            scope, owned = event.execution.source.scope, event.owned_order
            record = self.journal.get(event.command_key)
            if (record is not None and record.kind is CommandKind.ENTRY
                    and self._entry_version(record) == 2):
                expected = self._fixed_request_v2(record, self._entry_intent_v2(record)).owned
                if not self._same_owned_v2(owned, expected):
                    raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
            identity = event.execution.execution.execution_id
        elif type(event) is CorroboratedExecutionCredit:
            fill = event.candidate.fill
            scope, identity = fill.scope, fill.execution_id
            record = self.journal.get(event.command_key)
            orders = tuple(codec.decode(item.payload_json)
                for item in self.store.list_position_events(inbox.position_id))
            owned_records = tuple(item for item in orders if type(item) is p.OwnedOrder
                                  and item.client_order_id == fill.client_order_id)
            if record is None or len(owned_records) != 1 or not any(
                    type(item) is FillQuarantineRecord and item.candidate == event.candidate for item in orders):
                raise RuntimeBlocked("INVALID_DURABLE_PRIOR_CREDIT")
            owned = owned_records[0]
            assert isinstance(owned, p.OwnedOrder)
            native_state = p.ProtectionProjection(scope, owned.position_id, owned.instrument_id,
                owned.side if owned.role == "ENTRY" else ("SELL" if owned.side == "BUY" else "BUY"),
                orders=(owned,))
            owned = self._request_for_state(record, native_state).owned
            if (owned.client_order_id != fill.client_order_id or owned.position_id != fill.position_id
                    or owned.instrument_id != fill.instrument_id or owned.side != fill.side):
                raise RuntimeBlocked("INVALID_DURABLE_PRIOR_CREDIT")
        else:
            raise RuntimeBlocked("INVALID_DURABLE_PRIOR_CREDIT")
        if (scope != self.bridge.scope or inbox.position_id != owned.position_id
                or execution_guard(scope, owned.instrument_id, identity) != guard):
            raise RuntimeBlocked("INVALID_DURABLE_PRIOR_CREDIT")
        if type(event) is HistoryExecutionCredit:
            state = p.ProtectionProjection(scope, owned.position_id, owned.instrument_id,
                owned.side if owned.role == "ENTRY" else ("SELL" if owned.side == "BUY" else "BUY"),
                orders=(owned,))
            self._verify_history_command(event, state)
        return event

    def _verify_replay_command(self, event: object, state: p.ProtectionProjection) -> None:
        if type(event) in (HistoryExecutionCredit, HistoryRecoveryConflict):
            assert isinstance(event, (HistoryExecutionCredit, HistoryRecoveryConflict))
            self._verify_history_command(event, state)
        elif type(event) is NativeHistoryResolution:
            prior = self._prior_credit(event.prior_credit_event_id)
            if type(prior) is not HistoryExecutionCredit:
                raise RuntimeBlocked("INVALID_DURABLE_PRIOR_CREDIT")
            self._verify_history_command(prior, state)

    def _verify_history_command(self, event: HistoryExecutionCredit | HistoryRecoveryConflict,
                                state: p.ProtectionProjection) -> None:
        record = self.journal.get(event.command_key)
        if (record is None or record.payload_hash != event.command_payload_hash
                or hashlib.sha256(record.payload_json.encode()).hexdigest() != record.payload_hash):
            raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
        request = self._request_for_state(record, state)
        if ((type(request) is LinearFixedLimitNativeOrderRequestV2
             and not self._same_owned_v2(event.owned_order, request.owned))
                or (type(request) is NativeOrderRequest
                    and (record.client_order_id != event.owned_order.client_order_id
                         or request.owned != event.owned_order))):
            raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")

    def _prior_execution_targets(self, execution_id: str) -> tuple[tuple[str, str, str], ...]:
        """Data-only routing for existing credits; never establish first ownership."""
        targets: list[tuple[str, str, str]] = []
        for cached in self._projections.values():
            for event in (*cached[3].credit.credits, *cached[3].history.credits):
                if type(event) is HistoryExecutionCredit:
                    scope, instrument = event.execution.source.scope, event.owned_order.instrument_id
                    identity = event.execution.execution.execution_id
                    client, venue = event.owned_order.client_order_id, event.execution.execution.venue_order_id
                else:
                    assert isinstance(event, CorroboratedExecutionCredit)
                    fill = event.candidate.fill
                    scope, instrument, identity = fill.scope, fill.instrument_id, fill.execution_id
                    client, venue = fill.client_order_id, event.candidate.venue_order_id
                if identity == execution_id and scope == self.bridge.scope:
                    targets.append((execution_guard(scope, instrument, identity), client, venue))
        return tuple(dict.fromkeys(targets))

    def recover_history_execution(self, *, receipt: AuthenticatedHistoryReceipt,
                                  binding: AuthenticatedAccountBinding, observation_key: str,
                                  execution_id: str) -> HistoryRecoveryResult:
        """Receipt-gated explicit recovery; never synthesizes native evidence."""
        with self._mutex:
            return self._recover_history(receipt=receipt, binding=binding,
                observation_key=observation_key, execution_id=execution_id)

    def _recover_history(self, *, receipt: AuthenticatedHistoryReceipt,
                         binding: AuthenticatedAccountBinding, observation_key: str,
                         execution_id: str, candidate: NativeExecutionCandidate | None = None
                         ) -> HistoryRecoveryResult:
        self._guard()
        result = HistoryRecoveryResult(execution_id, "UNRESOLVED", ())
        session, history = self._receipt_session, self._history_store
        if session is None or history is None:
            raise RuntimeBlocked("AUTHENTICATED_EXECUTION_RECEIPT_REQUIRED")
        view = session.require_history_receipt(receipt, binding=binding, scope=self.bridge.scope)
        try:
            history.require_observation(view.snapshot, observation_key)
        except HistoryError as error:
            if str(error) != "HISTORY_OBSERVATION_NOT_FOUND":
                raise
            return replace(result, reason_codes=("HISTORY_OBSERVATION_REQUIRED",))
        try:
            evidence = history.execution_evidence(observation_key, execution_id)
        except HistoryError as error:
            if str(error) != "HISTORY_EXECUTION_NOT_FOUND":
                raise
            return replace(result, reason_codes=("HISTORY_EXECUTION_REQUIRED",))
        snapshot, row = view.snapshot, evidence.record
        candidates = self.persisted_candidates()
        instrument = snapshot.symbol + "-LINEAR.BYBIT"
        client = candidate.fill.client_order_id if candidate is not None else row.client_order_id
        records = tuple(item for item in self.journal.list_commands() if item.client_order_id == client
                        and item.kind in (CommandKind.ENTRY, CommandKind.EXIT))
        guard_instrument = instrument if candidate is None else candidate.fill.instrument_id
        if candidate is None and len(records) == 1:
            target = self._find_owned(records[0].client_order_id)
            if target is not None:
                guard_instrument = self._request_for_state(records[0], target).owned.instrument_id
        guard = execution_guard(snapshot.scope, guard_instrument, execution_id)
        intended_guard = guard
        targets = self._prior_execution_targets(execution_id)
        if targets:
            linked = tuple(item for item in targets if (
                (candidate is not None and item[0] == intended_guard)
                or (candidate is None and (
                    (bool(row.client_order_id) and item[1] == row.client_order_id)
                    or (bool(row.venue_order_id) and item[2] == row.venue_order_id)
                    or (len(records) == 1 and item[0] == intended_guard)))))
            possible = {item[0] for item in linked or targets}
            if len(possible) != 1:
                return replace(result, reason_codes=("EXECUTION_GUARD_CONFLICT",))
            guard = next(iter(possible))
        previous = self._prior_credit(guard)
        if targets and previous is None:
            raise RuntimeBlocked("INVALID_DURABLE_PRIOR_CREDIT")
        if type(previous) is CorroboratedExecutionCredit:
            if previous.candidate not in candidates:
                raise RuntimeBlocked("INVALID_DURABLE_PRIOR_CREDIT")
            native_result = self.corroborate_execution(receipt=receipt, binding=binding,
                observation_key=observation_key, candidate_id=previous.candidate_id)
            return replace(result, status=native_result.status, reason_codes=native_result.reason_codes)
        assert previous is None or isinstance(previous, HistoryExecutionCredit)
        if previous is None and any(execution_guard(item.fill.scope, item.fill.instrument_id,
                item.fill.execution_id) == guard for item in candidates):
            return replace(result, reason_codes=("NATIVE_CORROBORATION_REQUIRED",))
        if previous is None:
            if row.execution_type != "Trade":
                return replace(result, reason_codes=("HISTORY_TRADE_REQUIRED",))
            if not row.client_order_id:
                return replace(result, reason_codes=("HISTORY_CLIENT_ORDER_ID_REQUIRED",))
        if previous is not None and (not records or guard != intended_guard):
            records = (self._required(previous.command_key),)
        if len(records) != 1 or (previous is None and (records[0].state in (
                CommandState.PENDING, CommandState.REJECTED) or records[0].terminal_confirmed)):
            return replace(result, reason_codes=("OWNED_COMMAND_INELIGIBLE",))
        record = records[0]
        state = self._find_owned(record.client_order_id)
        if state is None:
            raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
        request = self._request_for_state(record, state)
        owned = request.owned
        source = ExecutionObservationReference(snapshot.scope, view.binding_reference,
            observation_key, snapshot.snapshot_id, snapshot.symbol, snapshot.window.start_ms,
            snapshot.window.end_ms, snapshot.observed_at, snapshot.server_observed_at)
        execution = HistoryExecutionEvidence(source, row, evidence.variant_hash)
        codes: list[str] = []
        if row.execution_type != "Trade":
            codes.append("HISTORY_TRADE_REQUIRED")
        if not row.client_order_id:
            codes.append("HISTORY_CLIENT_ORDER_ID_REQUIRED")
        if not row.venue_order_id:
            codes.append("VENUE_ORDER_ID_MISMATCH")
        if previous is None and codes:
            return replace(result, reason_codes=tuple(codes))
        if row.client_order_id != owned.client_order_id:
            codes.append("CLIENT_ORDER_ID_MISMATCH")
        if row.side.upper() != owned.side:
            codes.append("SIDE_MISMATCH")
        if owned.instrument_id != instrument:
            codes.append("INSTRUMENT_ID_MISMATCH")
        if previous is not None:
            if (previous.execution.execution != row or previous.owned_order != owned
                    or previous.command_key != record.command_key):
                codes.append("HISTORY_EXECUTION_CONFLICT")
            if previous.owned_order.position_id != owned.position_id:
                codes.append("EXECUTION_GUARD_CONFLICT")
        conflict_keys = set(evidence.conflict_keys)
        if conflict_keys:
            codes.append("HISTORY_EXECUTION_CONFLICT")
        if self._has_execution_decision(guard):
            codes.append("EXECUTION_DECISION_CONFLICT")
        known = self._known_venues(owned.client_order_id, candidates)
        if known and known != {row.venue_order_id}:
            codes.append("VENUE_ORDER_ID_MISMATCH")
        if previous is not None and previous.execution.execution.venue_order_id != row.venue_order_id:
            codes.append("VENUE_ORDER_ID_MISMATCH")
        order_sources: list[HistoricalOrderEvidence] = []
        venue_ids = tuple(sorted((known | {row.venue_order_id}) - {""}))
        current_order: HistoryOrderEvidence | None = None
        for stored in history.order_evidence(client_order_id=owned.client_order_id, venue_order_ids=venue_ids):
            order = stored.record
            mismatch = (order.client_order_id != owned.client_order_id or order.venue_order_id != row.venue_order_id
                or owned.instrument_id != order.symbol + "-LINEAR.BYBIT" or order.side.upper() != owned.side
                or order.position_idx != 0 or order.reduce_only != owned.reduce_only or order.quantity != owned.quantity)
            if mismatch or stored.conflict_keys:
                codes.append("HISTORY_ORDER_CONFLICT")
                conflict_keys.update(stored.conflict_keys)
                for original in stored.observations:
                    order_sources.append(HistoricalOrderEvidence(snapshot.scope, original.observation_key,
                        original.snapshot_id, original.symbol, original.window.start_ms, original.window.end_ms,
                        original.observed_at, original.server_observed_at, original.transport_injected,
                        stored.variant_hash, order.venue_order_id, order.client_order_id,
                        "Buy" if order.side == "Buy" else "Sell",
                        order.position_idx, order.reduce_only))
            if order in snapshot.orders and not mismatch:
                current_order = HistoryOrderEvidence(source, order, stored.variant_hash)
        if previous is None and current_order is None and not codes:
            return replace(result, reason_codes=("HISTORY_CURRENT_ORDER_REQUIRED",))
        if candidate is not None:
            codes.extend(compare_history_candidate(candidate, execution, owned.instrument_id))
            # Every source-distinct variant must independently agree with the first credit.
            if any(compare_history_candidate(item, execution, owned.instrument_id)
                   or item.fill.position_id != owned.position_id for item in candidates
                   if execution_guard(item.fill.scope, item.fill.instrument_id, item.fill.execution_id) == guard):
                codes.append("NATIVE_CANDIDATE_VARIANT_CONFLICT")
        if codes:
            event: HistoryExecutionCredit | HistoryRecoveryConflict | NativeHistoryResolution = HistoryRecoveryConflict(
                execution, owned, record.command_key, record.payload_hash, tuple(dict.fromkeys(codes)),
                tuple(sorted(conflict_keys)), tuple(dict.fromkeys(order_sources)),
                guard if previous is not None else None, None if candidate is None else candidate_identity(candidate))
        elif previous is not None:
            if candidate is None or candidate_identity(candidate) in self._projections[
                    state.position_id][3].credit.resolved_candidate_ids:
                session.require_history_receipt(receipt, binding=binding, scope=self.bridge.scope)
                self._guard()
                return replace(result, status="DUPLICATE")
            event = NativeHistoryResolution(candidate_identity(candidate), guard, history_credit_digest(previous), execution)
        else:
            assert current_order is not None
            event = HistoryExecutionCredit(execution, current_order, owned, record.command_key, record.payload_hash)
        return self._commit_history(event, state, receipt, binding)

    def _has_execution_decision(self, guard: str) -> bool:
        return any(execution_guard(item.scope, item.instrument_id, item.execution_id) == guard
            for cached in self._projections.values() for item in cached[3].credit.decisions) or any(
            execution_guard(item.execution.source.scope, item.owned_order.instrument_id,
                item.execution.execution.execution_id) == guard for cached in self._projections.values()
            for item in cached[3].history.decisions)

    def _known_venues(self, client: str, candidates: tuple[NativeExecutionCandidate, ...]) -> set[str]:
        known = {item.venue_order_id for item in candidates if item.fill.client_order_id == client}
        for position in self.store.list_positions():
            for inbox in self.store.list_position_events(position.position_id):
                event = codec.decode(inbox.payload_json)
                if type(event) is tuple and len(event) == 6 and event[1] == client and event[2] is not None:
                    known.add(event[2])
                elif type(event) is HistoryExecutionCredit and event.owned_order.client_order_id == client:
                    known.add(event.execution.execution.venue_order_id)
                elif type(event) is CorroboratedExecutionCredit and event.candidate.fill.client_order_id == client:
                    known.add(event.candidate.venue_order_id)
        return known

    def _commit_history(self, event: HistoryExecutionCredit | HistoryRecoveryConflict | NativeHistoryResolution,
                         state: p.ProtectionProjection, receipt: AuthenticatedHistoryReceipt,
                         binding: AuthenticatedAccountBinding) -> HistoryRecoveryResult:
        cached = self._projections[state.position_id]
        identities, context = dict(cached[2]), replace(cached[3])
        reduced = self._reduce_event(state, event, identities, context)
        if type(event) is HistoryExecutionCredit and context.history.credits == cached[3].history.credits:
            event = context.history.decisions[-1]
            identities, context = dict(cached[2]), replace(cached[3])
            reduced = self._reduce_event(state, event, identities, context)
        if type(event) is HistoryExecutionCredit:
            key = execution_guard(state.scope, state.instrument_id, event.execution.execution.execution_id)
        elif type(event) is HistoryRecoveryConflict:
            key = history_decision_event_id(event)
        else:
            assert isinstance(event, NativeHistoryResolution)
            key = history_resolution_event_id(event)
        summary = json.loads(codec.encode(self._summary(reduced, identities, context.credit, context.history)))
        payload = json.loads(codec.encode(event))
        self._readbacks.clear()
        self._leverage_readbacks.clear()
        self._active = False
        session = self._receipt_session
        if session is None:
            raise RuntimeBlocked("AUTHENTICATED_EXECUTION_RECEIPT_REQUIRED")
        session.require_history_receipt(receipt, binding=binding, scope=self.bridge.scope)
        self._guard()
        saved = self.store.apply_position_event(key, payload, state.position_id, cached[0], summary)
        if saved.revision == cached[0] + 1:
            self._projections[state.position_id] = saved.revision, reduced, identities, context
        status: Literal["CREDITED", "DUPLICATE", "CONFLICT"] = (
            "CONFLICT" if type(event) is HistoryRecoveryConflict else
            "DUPLICATE" if type(event) is NativeHistoryResolution else "CREDITED")
        return HistoryRecoveryResult(event.execution.execution.execution_id, status,
            event.mismatch_codes if type(event) is HistoryRecoveryConflict else ())

    def _commit_corroboration(self,
                             event: CorroboratedExecutionCredit | ExecutionCorroborationConflict,
                             candidate: NativeExecutionCandidate,
                             receipt: AuthenticatedHistoryReceipt,
                             binding: AuthenticatedAccountBinding) -> ExecutionCreditResult:
        """Reduce fresh durable revision and install caches only after atomic commit."""
        state = self.projection(candidate.fill.position_id)
        cached = self._projections[state.position_id]
        identities, context = dict(cached[2]), replace(cached[3])
        reduced = self._reduce_event(state, event, identities, context)
        if (type(event) is CorroboratedExecutionCredit
                and context.credit.credits == cached[3].credit.credits):
            # A quantity/ownership refusal is a decision, never a credit guard.
            event = context.credit.decisions[-1]
            identities, context = dict(cached[2]), replace(cached[3])
            reduced = self._reduce_event(state, event, identities, context)
        if isinstance(event, CorroboratedExecutionCredit):
            key = execution_guard(candidate.fill.scope, candidate.fill.instrument_id,
                                  candidate.fill.execution_id)
        else:
            key = decision_event_id(event)
        payload = json.loads(codec.encode(event))
        summary = json.loads(codec.encode(self._summary(reduced, identities, context.credit,
                                                        context.history)))
        self._readbacks.clear()
        self._leverage_readbacks.clear()
        self._active = False
        session = self._receipt_session
        if session is None:
            raise RuntimeBlocked("AUTHENTICATED_EXECUTION_RECEIPT_REQUIRED")
        session.require_history_receipt(receipt, binding=binding, scope=self.bridge.scope)
        self._guard()
        saved = self.store.apply_position_event(key, payload, state.position_id, cached[0], summary)
        if saved.revision == cached[0] + 1:
            self._projections[state.position_id] = (saved.revision, reduced, identities, context)
        if type(event) is ExecutionCorroborationConflict:
            return ExecutionCreditResult(event.candidate_id, "CONFLICT", event.mismatch_codes)
        return ExecutionCreditResult(event.candidate_id, "CREDITED", ())

    def _persist(self, event: object, state: p.ProtectionProjection) -> None:
        self._guard()
        if type(event) in (CorroboratedExecutionCredit, ExecutionCorroborationConflict,
                           HistoryExecutionCredit, HistoryRecoveryConflict, NativeHistoryResolution):
            raise RuntimeBlocked("AUTHENTICATED_EXECUTION_RECEIPT_REQUIRED")
        cached = self._projections.get(state.position_id)
        identities = {} if cached is None else dict(cached[2])
        context = _ReplayContext() if cached is None else replace(cached[3])
        encoded = codec.encode(event)
        key = "native:" + hashlib.sha256(encoded.encode()).hexdigest()
        if type(event) is FillQuarantineRecord:
            identity = self._candidate_identity(event.candidate)
            if identity in identities.get(event.candidate.fill.execution_id, ()):
                return  # Keep the first durable observation, including its original receipt times.
            key = "native:" + identity
        self._reduce_event(state, event, identities, context)
        current = self.store.get_position(state.position_id)
        saved = self.store.apply_position_event(key, json.loads(encoded), state.position_id,
            0 if current is None else current.revision,
            json.loads(codec.encode(self._summary(state, identities, context.credit, context.history))))
        # Cache only after the atomic inbox/projection commit. A duplicate event
        # returns the current revision, so cannot replace it with an older reduction.
        if saved.revision == (0 if current is None else current.revision) + 1:
            self._projections[state.position_id] = (saved.revision, state, identities, context)

    def _find_owned(self, client_id: str) -> p.ProtectionProjection | None:
        return next((state for state in self._states()
                     if any(order.client_order_id == client_id for order in state.orders)), None)

    def _register(self, state: p.ProtectionProjection, order: p.OwnedOrder) -> None:
        reduced = p.register_order(state, order)
        self._persist(order, reduced)
        if reduced.conflicts:
            self._active = False
            raise RuntimeBlocked("IMMUTABLE_OWNERSHIP_CONFLICT")

    def _required(self, key: str) -> CommandRecord:
        record = self.journal.get(key)
        if record is None:
            raise RuntimeBlocked("UNKNOWN_COMMAND")
        return record

    def _entry_version(self, record: CommandRecord) -> Literal[1, 2]:
        """Recognize only the reserved outer frame, never strings inside legacy data."""
        duplicate = False
        def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
            nonlocal duplicate
            result: dict[str, object] = {}
            for key, value in items:
                duplicate |= key in result
                result[key] = value
            return result
        try:
            if type(record.payload_json) is not str or len(record.payload_json.encode("utf-8")) > 65536:
                raise ValueError
            raw = json.loads(record.payload_json, object_pairs_hook=pairs)
        except (ValueError, TypeError, RecursionError):
            raise RuntimeBlocked("INVALID_DURABLE_ENTRY_VERSION") from None
        framed = type(raw) is dict and (
            raw.get("schema") == "RISK_V2_DURABLE_INTENT"
            or raw.get("record_type") == "DurableEntryIntentV2"
            or ("wire_version" in raw and ("fields" in raw or "content_sha256" in raw))
            or ("fields" in raw and "content_sha256" in raw))
        if record.reservation_v2 is not None or framed:
            if duplicate:
                raise RuntimeBlocked("INVALID_DURABLE_ENTRY_VERSION")
            return 2
        if record.kind is CommandKind.ENTRY and (type(raw) is not dict
                or not {"position_id", "plan", "protection_policy"}.issubset(raw)):
            raise RuntimeBlocked("INVALID_DURABLE_ENTRY_VERSION")
        return 1

    def _entry_intent_v2(self, record: CommandRecord) -> DurableEntryIntentV2:
        if (record.kind is not CommandKind.ENTRY or record.reservation is not None
                or record.reservation_v2 is None):
            raise RuntimeBlocked("DURABLE_V2_RESERVATION_REQUIRED")
        intent = decode_entry_intent_v2(record.payload_json)
        if (encode_entry_intent_v2(intent) != record.payload_json
                or hashlib.sha256(record.payload_json.encode("utf-8")).hexdigest() != record.payload_hash):
            raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
        reservations.entry_command_identity_sha256_v2(self.bridge.scope,
            record.command_key, intent, record.reservation_v2)
        return intent

    def _fixed_request_v2(self, record: CommandRecord,
                          intent: DurableEntryIntentV2) -> LinearFixedLimitNativeOrderRequestV2:
        policy, envelope = intent.protection_policy, intent.envelope
        try:
            order = p.OwnedOrder(record.client_order_id, intent.position_id,
                envelope.instrument_pin.native_instrument_id, envelope.side, "ENTRY",
                envelope.base_quantity, False, policy.generation, policy.revision)
            request = LinearFixedLimitNativeOrderRequestV2(envelope.scope.product_scope, order,
                envelope, AttachedProtection(policy.stop_price, policy.take_profit_price,
                                             policy.trigger_source), 0)
            if request.scope != self.bridge.scope:
                raise ValueError
            return request
        except ValueError:
            raise RuntimeBlocked("DURABLE_NATIVE_V2_MAPPING_UNSUPPORTED") from None

    @staticmethod
    def _same_owned_v2(actual: p.OwnedOrder, expected: p.OwnedOrder) -> bool:
        """Finite V2 leaf comparison, including the original Decimal representation."""
        if type(actual) is not p.OwnedOrder or type(expected) is not p.OwnedOrder:
            return False
        names = ("client_order_id", "position_id", "instrument_id", "side", "role", "policy_revision")
        identities = []
        for owner in (actual, expected):
            try:
                values = {field.name: object.__getattribute__(owner, field.name)
                          for field in fields(p.OwnedOrder)}
            except AttributeError:
                return False
            quantity, generation = values["quantity"], values["generation"]
            if (any(type(values[name]) is not str for name in names)
                    or type(quantity) is not Decimal or not quantity.is_finite() or quantity <= 0
                    or type(values["reduce_only"]) is not bool or type(generation) is not int
                    or generation < 0 or generation.bit_length() > 63
                    or values["trigger_price"] is not None or values["trigger_source"] is not None):
                return False
            identities.append((*(values[name] for name in names), quantity.as_tuple(),
                               values["reduce_only"], generation, None, None))
        return identities[0] == identities[1]

    def _register_restored_request(self, request: NativeRequest) -> None:
        if type(request) is LinearFixedLimitNativeOrderRequestV2:
            self.bridge.register_fixed_limit_v2(request)
        elif type(request) is NativeOrderRequest:
            self.bridge.register_owned(request)
        else:
            raise RuntimeBlocked("UNSUPPORTED_NATIVE_COMMAND")

    def _plan(self, record: CommandRecord) -> r.EntryDecision:
        if self._entry_version(record) == 2:
            raise RuntimeBlocked("V2_FINANCIAL_DISPATCH_UNAVAILABLE")
        result = codec.decode(json.dumps(json.loads(record.payload_json)["plan"]))
        if type(result) is not r.EntryDecision:
            raise RuntimeBlocked("INVALID_DURABLE_PLAN")
        return result

    def _policy(self, record: CommandRecord) -> p.ProtectionPolicy:
        if record.kind is CommandKind.ENTRY and self._entry_version(record) == 2:
            return self._entry_intent_v2(record).protection_policy
        result = codec.decode(json.dumps(json.loads(record.payload_json)["protection_policy"]))
        if type(result) is not p.ProtectionPolicy:
            raise RuntimeBlocked("INVALID_DURABLE_PROTECTION_POLICY")
        return result

    def _recover_entry_mapping(self, record: CommandRecord) -> None:
        """Restore committed ownership under the lease; never renew selection authority."""
        self._guard()
        state, order = self._entry_mapping(record)
        self._register(state, order)

    def _entry_mapping(self, record: CommandRecord) -> tuple[p.ProtectionProjection, p.OwnedOrder]:
        if self._entry_version(record) == 2:
            request = self._fixed_request_v2(record, self._entry_intent_v2(record))
            order = request.owned
            if self.store.get_position(order.position_id) is not None:
                raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
            return p.ProtectionProjection(request.scope, order.position_id,
                                          order.instrument_id, order.side), order
        raw = json.loads(record.payload_json)
        plan, policy = self._plan(record), self._policy(record)
        if (hashlib.sha256(record.payload_json.encode()).hexdigest() != record.payload_hash
                or (plan.scope.account_id, plan.scope.environment)
                != (self.journal.scope.account_id, self.journal.scope.environment)
                or _reassess(plan, plan.assessed_at) != plan
                or policy.revision != plan.policy.revision
                or policy.stop_price != plan.proposal.stop_price
                or self.store.get_position(raw["position_id"]) is not None):
            raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
        order = p.OwnedOrder(record.client_order_id, raw["position_id"],
            plan.scope.symbol + "-LINEAR.BYBIT", "BUY" if plan.proposal.side == "LONG" else "SELL",
            "ENTRY", plan.quantity, False, policy.generation, policy.revision)
        NativeOrderRequest(self.bridge.scope, order, "MARKET", plan.market.entry_price,
            attached=AttachedProtection(policy.stop_price, policy.take_profit_price,
                                        policy.trigger_source))
        return p.ProtectionProjection(self.bridge.scope, order.position_id,
                                      order.instrument_id, order.side), order

    def _request(self, record: CommandRecord) -> NativeRequest:
        state = self._find_owned(record.client_order_id)
        if state is None:
            raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
        return self._request_for_state(record, state)

    def _request_for_state(self, record: CommandRecord,
                           state: p.ProtectionProjection) -> NativeRequest:
        """Check immutable journal intent without recursively replaying projections."""
        v2 = record.kind is CommandKind.ENTRY and self._entry_version(record) == 2
        if v2:
            if (type(state) is not p.ProtectionProjection
                    or any(type(value) is not str for value in
                           (state.position_id, state.instrument_id, state.entry_side))
                    or any(type(item) is not p.OwnedOrder or type(item.client_order_id) is not str
                           for item in state.orders)):
                raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
            try:
                _validate_account(state.scope)
            except ValueError:
                raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED") from None
        orders = tuple(item for item in state.orders if item.client_order_id == record.client_order_id)
        if len(orders) != 1 or state.scope != self.bridge.scope:
            raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
        order = orders[0]
        if hashlib.sha256(record.payload_json.encode()).hexdigest() != record.payload_hash:
            raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
        raw = json.loads(record.payload_json)
        if record.kind is CommandKind.ENTRY:
            if v2:
                request = self._fixed_request_v2(record, self._entry_intent_v2(record))
                expected = request.owned
                if (not self._same_owned_v2(order, expected)
                        or state.position_id != expected.position_id
                        or state.instrument_id != expected.instrument_id
                        or state.entry_side != expected.side or state.scope != request.scope):
                    raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
                return request
            plan, policy = self._plan(record), self._policy(record)
            expected = p.OwnedOrder(record.client_order_id, raw["position_id"],
                plan.scope.symbol + "-LINEAR.BYBIT", "BUY" if plan.proposal.side == "LONG" else "SELL",
                "ENTRY", plan.quantity, False, policy.generation, policy.revision)
            if (order != expected or state.position_id != order.position_id
                    or state.instrument_id != order.instrument_id
                    or (plan.scope.account_id, plan.scope.environment)
                    != (state.scope.account_id, state.scope.environment)):
                raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
            return NativeOrderRequest(state.scope, order, "MARKET", plan.market.entry_price,
                attached=AttachedProtection(policy.stop_price, policy.take_profit_price,
                                            policy.trigger_source))
        if record.kind is not CommandKind.EXIT or order.role != "EXIT":
            raise RuntimeBlocked("UNSUPPORTED_NATIVE_COMMAND")
        if (order.position_id != raw["position_id"] or order.quantity != Decimal(raw["quantity"])
                or order.policy_revision != raw["policy_revision"]
                or order.position_id != state.position_id or order.instrument_id != state.instrument_id
                or order.side != ("SELL" if state.entry_side == "BUY" else "BUY")
                or not order.reduce_only or order.generation != 0):
            raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
        return NativeOrderRequest(state.scope, order, "MARKET", Decimal(raw["reference_price"]))

    def reservation_totals(self, scope: r.RiskScope,
                           exclude: str | None = None) -> r.ReservationTotals:
        records = tuple(record for record in self.journal.list_commands()
            if record.command_key != exclude and record.reservation is not None
            and not record.terminal_confirmed)
        amounts = tuple(record.reservation for record in records if record.reservation is not None)
        identity = json.dumps([(item.command_key, item.state.value, item.payload_hash)
                               for item in records], separators=(",", ":"))
        return r.ReservationTotals(scope, "journal-" + hashlib.sha256(identity.encode()).hexdigest(),
            self._now(), True, _sum(tuple(item.notional_amount for item in amounts)),
            _sum(tuple(record.reservation.notional_amount for record in records
                       if record.reservation is not None and self._plan(record).scope.symbol
                       == scope.symbol)), _sum(tuple(item.risk_amount for item in amounts)),
            _sum(tuple(item.margin_amount for item in amounts)), len(amounts))

    def queue_entry(self, command_key: str, position_id: str, *, plan_id: str,
                    plan: r.EntryDecision, protection_policy: p.ProtectionPolicy,
                    mode: modes.AutomaticMode, selection_request: modes.SelectionRequest,
                    ai_response: str | None = None,
                    priority_candidate_ids: tuple[str, ...] | None = None) -> CommandRecord:
        """Stage a server plan. Selection authorizes neither risk nor dispatch."""
        with self._mutex:
            self._guard()
            self._require_protection_recovery_clear_v1()
            now = self._now()
            modes.public_candidate_request(selection_request, now=now)
            if mode is modes.AutomaticMode.FULL_AUTO:
                selected = modes.select_full_auto(scope=selection_request.scope,
                    policy=selection_request.policy, snapshot=selection_request.snapshot,
                    candidates=selection_request.candidates, limits=selection_request.limits,
                    priority_candidate_ids=priority_candidate_ids, now=now)
            elif mode is modes.AutomaticMode.ALL_IN_ONE:
                selected = modes.select_all_in_one(request=selection_request,
                                                   response=ai_response, now=now)
            else:
                raise RuntimeBlocked("EXPLICIT_AUTOMATIC_MODE_REQUIRED")
            candidate = selected.candidate
            if (candidate is None or selected.status != "SELECTED"
                    or candidate.plan_id != plan_id
                    or (candidate.account_id, candidate.environment, candidate.symbol,
                        candidate.strategy.strategy_id, candidate.policy_revision)
                    != (plan.scope.account_id, plan.scope.environment, plan.scope.symbol,
                        plan.scope.strategy_id, plan.policy.revision)
                    or (plan.scope.account_id, plan.scope.environment)
                    != (self.journal.scope.account_id, self.journal.scope.environment)
                    or _reassess(plan, plan.assessed_at) != plan
                    or protection_policy.revision != plan.policy.revision
                    or protection_policy.stop_price != plan.proposal.stop_price):
                raise RuntimeBlocked("SELECTION_PLAN_POLICY_MISMATCH")
            # Validate the native contract before committing any command.
            dummy_order = p.OwnedOrder("ec_" + "0" * 32, position_id,
                plan.scope.symbol + "-LINEAR.BYBIT", "BUY" if plan.proposal.side == "LONG" else "SELL",
                "ENTRY", plan.quantity, False, protection_policy.generation, plan.policy.revision)
            NativeOrderRequest(self.bridge.scope, dummy_order, "MARKET", plan.market.entry_price,
                attached=AttachedProtection(protection_policy.stop_price,
                    protection_policy.take_profit_price, protection_policy.trigger_source))
            if self.journal.get(command_key) is None:
                if self.store.get_position(position_id) is not None:
                    raise RuntimeBlocked("POSITION_ID_ALREADY_OWNED")
                if (not self._fresh(plan.assessed_at)
                        or any(state.entries_blocked for state in self._states())
                        or any(not record.terminal_confirmed
                               for record in self.journal.list_commands())):
                    raise RuntimeBlocked("STALE_PLAN_OR_UNRESOLVED_EXPOSURE")
                totals = self.reservation_totals(plan.scope)
                if (plan.reservations.notional_amount, plan.reservations.risk_amount,
                    plan.reservations.margin_amount, plan.reservations.position_count) != (
                        totals.notional_amount, totals.risk_amount,
                        totals.margin_amount, totals.position_count):
                    raise RuntimeBlocked("RESERVATION_SNAPSHOT_MISMATCH")
            payload = {"position_id": position_id, "plan_id": plan_id,
                "candidate_id": candidate.candidate_id, "strategy_revision": candidate.strategy.revision,
                "mode": mode.value, "selection_expires": min(candidate.expires_at,
                    selection_request.expires_at, selection_request.snapshot.expires_at).isoformat(),
                "plan": json.loads(codec.encode(plan)),
                "protection_policy": json.loads(codec.encode(protection_policy))}
            policy, account = plan.policy, plan.account
            record = self.journal.register(command_key, CommandKind.ENTRY, payload,
                reservation=Reservation(plan.estimated_loss, plan.notional, _margin(plan.margin_amount)),
                limits=ReservationLimits(policy.max_total_risk, policy.max_total_notional,
                    policy.max_total_margin, policy.max_positions, account.total_risk,
                    account.total_notional, account.total_margin, account.position_count))
            state = self._find_owned(record.client_order_id)
            if state is None:
                state = p.ProtectionProjection(self.bridge.scope, position_id,
                    dummy_order.instrument_id, dummy_order.side)
            self._register(state, replace(dummy_order, client_order_id=record.client_order_id))
            self._register_restored_request(self._request(record))
            return record

    def _entry_gate(self, record: CommandRecord, final: r.EntryDecision) -> None:
        self._guard()
        self._require_protection_recovery_clear_v1()
        self._require_qualification()
        self._leverage_gate(record, final)
        if (self._control_busy or not self._active
                or any(state.entries_blocked for state in self._states())
                or any(item.command_key != record.command_key and not item.terminal_confirmed
                       for item in self.journal.list_commands())
                or self._now() >= datetime.fromisoformat(
                    json.loads(record.payload_json)["selection_expires"])):
            raise RuntimeBlocked("ENTRY_PAUSED_OR_UNRESOLVED_OR_SELECTION_EXPIRED")
        now = self._now()
        if _reassess(final, now) != replace(final, assessed_at=now):
            raise RuntimeBlocked("CURRENT_RISK_REQUIRED")

    def _leverage_gate(self, record: CommandRecord, final: r.EntryDecision) -> None:
        observation = self._leverage_readbacks.get(record.command_key)
        readback = final.leverage_readback
        if (observation is None or not self._fresh(observation.observed_at)
                or observation.leverage != final.leverage
                or observation.policy_revision != final.policy.revision
                or observation.symbol != final.scope.symbol
                or readback is None or readback.applied_leverage != observation.leverage
                or readback.observed_at < observation.observed_at
                or final.account.observed_at < observation.observed_at
                or final.assessed_at < observation.observed_at):
            raise RuntimeBlocked("NATIVE_LEVERAGE_SEQUENCE_AND_FRESH_RISK_REQUIRED")

    def _leverage_plan(self, entry_key: str, control_key: str) -> r.EntryDecision:
        self._guard()
        record = self._required(entry_key)
        if self._entry_version(record) == 2:
            raise RuntimeBlocked("V2_FINANCIAL_DISPATCH_UNAVAILABLE")
        self._require_qualification()
        if (record.kind is not CommandKind.ENTRY or record.state is not CommandState.PENDING
                or any(state.owned_quantity != 0 or state.conflicts for state in self._states())
                or any(item.command_key not in (entry_key, control_key)
                       and not item.terminal_confirmed for item in self.journal.list_commands())):
            raise RuntimeBlocked("UNRESOLVED_EXPOSURE_FORBIDS_LEVERAGE_CHANGE")
        plan = self._plan(record)
        totals = self.reservation_totals(plan.scope, entry_key)
        try:
            fresh = self.entry_risk(plan, self._now(), totals)
        except Exception:  # noqa: BLE001 - provider exceptions may contain transport secrets.
            raise RuntimeBlocked("FRESH_EXPLICIT_LEVERAGE_RISK_REQUIRED") from None
        checked_at = self._now()
        if (type(fresh) is not r.EntryDecision or fresh.reservations != totals
                or _economic_identity(fresh) != _economic_identity(plan)
                or fresh.account.external_same_symbol_orders
                or fresh.account.external_same_symbol_position
                or fresh.account.symbol_notional != 0
                or not fresh.policy.leverage_min <= fresh.leverage <= fresh.policy.leverage_max
                or not fresh.venue_leverage_min <= fresh.leverage <= fresh.venue_leverage_max
                or _reassess(fresh, checked_at) != replace(fresh, assessed_at=checked_at)):
            raise RuntimeBlocked("FRESH_EXPLICIT_LEVERAGE_RISK_REQUIRED")
        return fresh

    async def apply_leverage(self, command_key: str, entry_key: str, *,
                             control: NativeLeverageControl,
                             adapter: BybitVenueReadback) -> CommandRecord:
        """Durable claim -> existing native writer -> independent GET -> fresh risk later.

        Any ambiguous handoff is never replayed. A separate GET reconciliation can
        resolve the setting without retrying the mutation. Entry reservation stays held.
        """
        with self._mutex:
            self._guard()
            entry = self._required(entry_key)
            if self._entry_version(entry) == 2:
                raise RuntimeBlocked("V2_FINANCIAL_DISPATCH_UNAVAILABLE")
            self._require_qualification()
            if (type(control) is not NativeLeverageControl or control.scope != self.bridge.scope
                    or control.native_account_id != self.bridge.native_account_id
                    or type(adapter) is not BybitVenueReadback or adapter.scope != self.bridge.scope
                    or self._control_busy):
                raise RuntimeBlocked("SCOPED_SINGLE_NATIVE_CONTROL_REQUIRED")
            plan = self._plan(self._required(entry_key))
            existing = self.journal.get(command_key)
            payload = {"entry_key": entry_key, "symbol": plan.scope.symbol,
                       "leverage": str(plan.leverage), "policy_revision": plan.policy.revision}
            if existing is not None:
                if (existing.kind is not CommandKind.LEVERAGE
                        or json.loads(existing.payload_json) != payload):
                    raise RuntimeBlocked("IMMUTABLE_CONTROL_CONFLICT")
                if existing.state is not CommandState.PENDING:
                    return existing
            self._leverage_plan(entry_key, command_key)
            record = self.journal.register(command_key, CommandKind.LEVERAGE, payload)
            self._control_busy = True
            self._leverage_readbacks.pop(entry_key, None)
        try:
            before = await adapter.snapshot(plan.scope.symbol)
            with self._mutex:
                self._guard()
                self._leverage_plan(entry_key, command_key)
                require_flat_snapshot(before, scope=self.bridge.scope, symbol=plan.scope.symbol,
                                      now=self._now(), max_age=self._max_age)
                claimed = self.journal.claim(command_key)
                if claimed is None:
                    return self._required(command_key)
                self._guard()
            try:
                await control.apply(plan.scope.symbol, plan.leverage)
                with self._mutex:
                    self._guard()
                    finished_at = self._now()
                snapshot = await adapter.snapshot(plan.scope.symbol)
                with self._mutex:
                    self._guard()
                    if snapshot.cycle_id == before.cycle_id:
                        raise RuntimeBlocked("INDEPENDENT_POST_EFFECT_READBACK_REQUIRED")
                    self._confirm_leverage(record, snapshot, after=finished_at)
            except BaseException as error:
                # Includes cancellation while awaiting native response. No retry.
                with self._mutex:
                    self._guard()
                    self._unknown(command_key)
                if isinstance(error, Exception):
                    raise RuntimeBlocked("NATIVE_LEVERAGE_AMBIGUOUS_OR_UNVERIFIED") from None
                raise
            return self._required(command_key)
        finally:
            with self._mutex:
                self._control_busy = False

    def _confirm_leverage(self, record: CommandRecord, snapshot: VenueSnapshot, *,
                          after: datetime) -> None:
        self._guard()
        raw = json.loads(record.payload_json)
        observation = observe_leverage(snapshot, command_key=record.command_key,
            entry_key=raw["entry_key"], scope=self.bridge.scope, symbol=raw["symbol"],
            leverage=Decimal(raw["leverage"]), policy_revision=raw["policy_revision"],
            now=self._now(), max_age=self._max_age, after=after)
        self.journal.acknowledge(record.command_key, "INDEPENDENT_GET_LEVERAGE_MATCH")
        self.journal.release_reservation(record.command_key, confirmed_terminal=True,
                                        terminal_evidence="CONTROL_SETTING_READ_BACK")
        self._leverage_readbacks[raw["entry_key"]] = observation

    async def reconcile_leverage(self, command_key: str,
                                 adapter: BybitVenueReadback) -> CommandRecord:
        """Read-only restart recovery, never repeat an UNKNOWN mutation."""
        with self._mutex:
            self._guard()
            record = self._required(command_key)
            if (record.kind is not CommandKind.LEVERAGE
                    or record.state not in (CommandState.UNKNOWN, CommandState.ACKNOWLEDGED)
                    or type(adapter) is not BybitVenueReadback or adapter.scope != self.bridge.scope):
                raise RuntimeBlocked("LEVERAGE_RECONCILIATION_SCOPE_REQUIRED")
            raw = json.loads(record.payload_json)
            started_at = self._now()
        snapshot = await adapter.snapshot(raw["symbol"])
        with self._mutex:
            self._guard()
            self._confirm_leverage(self._required(command_key), snapshot, after=started_at)
            return self._required(command_key)

    def queue_cancel(self, command_key: str, target_client_id: str) -> CommandRecord:
        with self._mutex:
            self._guard()
            # The mutex and scoped OS lease serialize this check with registration
            # across all runtime callers/processes. Reject before creating a second
            # durable intent that could block the first pending cancellation.
            if any(control.command_key != command_key
                   for control in self._cancel_controls(target_client_id)):
                raise RuntimeBlocked("CANCEL_ALREADY_UNRESOLVED")
            state = self._find_owned(target_client_id)
            if state is None or state.conflicts:
                raise RuntimeBlocked("CANCEL_EXACT_OWNERSHIP_REQUIRED")
            target = next(item for item in self.journal.list_commands()
                          if item.client_order_id == target_client_id)
            if target.state is not CommandState.ACKNOWLEDGED:
                raise RuntimeBlocked("CANCEL_KNOWN_NATIVE_TARGET_REQUIRED")
            order = self.bridge.cached_owned_order(target_client_id)
            venue_id = str(getattr(order, "venue_order_id", ""))
            known_ids = set()
            for event_record in self.store.list_position_events(state.position_id):
                event = codec.decode(event_record.payload_json)
                if (type(event) is tuple and len(event) == 6 and event[1] == target_client_id
                        and event[2] is not None):
                    known_ids.add(event[2])
            if not venue_id or known_ids != {venue_id}:
                raise RuntimeBlocked("PERSISTED_NATIVE_CANCEL_IDENTITY_REQUIRED")
            self._guard()
            return self.journal.register(command_key, CommandKind.CANCEL,
                {"target_client_id": target_client_id, "position_id": state.position_id,
                 "venue_order_id": venue_id, "instrument_id": state.instrument_id,
                 "native_account_id": self.bridge.native_account_id})

    def _cancel_controls(self, target_client_id: str) -> tuple[CommandRecord, ...]:
        return tuple(item for item in self.journal.list_commands()
                     if item.kind is CommandKind.CANCEL and not item.terminal_confirmed
                     and json.loads(item.payload_json)["target_client_id"] == target_client_id)

    @staticmethod
    def _cancel_identity_matches(control: CommandRecord, evidence: NativeOrderEvidence,
                                 state: p.ProtectionProjection) -> bool:
        raw = json.loads(control.payload_json)
        return (evidence.venue_order_id is not None
                and evidence.venue_order_id == raw["venue_order_id"]
                and evidence.client_order_id == raw["target_client_id"]
                and evidence.instrument_id == raw["instrument_id"] == state.instrument_id
                and evidence.native_account_id == raw["native_account_id"]
                and state.position_id == raw["position_id"] and evidence.scope == state.scope)

    def dispatch_cancel(self, command_key: str) -> CommandRecord:
        with self._mutex:
            self._guard()
            record = self._required(command_key)
            if record.kind is not CommandKind.CANCEL:
                raise RuntimeBlocked("CANCEL_COMMAND_REQUIRED")
            if record.state is not CommandState.PENDING:
                return record
            self._require_qualification()
            if self._control_busy:
                raise RuntimeBlocked("NATIVE_CONTROL_BUSY")
            raw = json.loads(record.payload_json)
            state = self._find_owned(raw["target_client_id"])
            if state is None or state.conflicts or state.position_id != raw["position_id"]:
                raise RuntimeBlocked("CANCEL_EXACT_OWNERSHIP_REQUIRED")
            if any(item.kind is CommandKind.CANCEL and item.command_key != command_key
                   and not item.terminal_confirmed
                   and json.loads(item.payload_json)["target_client_id"] == raw["target_client_id"]
                   for item in self.journal.list_commands()):
                raise RuntimeBlocked("CANCEL_ALREADY_UNRESOLVED")
            order = self.bridge.cached_owned_order(raw["target_client_id"])
            if (str(getattr(order, "venue_order_id", "")) != raw["venue_order_id"]
                    or raw["instrument_id"] != state.instrument_id
                    or raw["native_account_id"] != self.bridge.native_account_id):
                raise RuntimeBlocked("PERSISTED_NATIVE_CANCEL_IDENTITY_REQUIRED")
            if self.journal.claim(command_key) is None:
                return self._required(command_key)
            self._guard()
            self._active = False
            try:
                result = self.bridge.cancel_owned(raw["target_client_id"])
                if result.state == "UNKNOWN":
                    self._unknown(command_key)
            except Exception:  # noqa: BLE001 - cached order may change during native handoff.
                self._unknown(command_key)
            return self._required(command_key)

    def dispatch(self, command_key: str) -> CommandRecord:
        with self._mutex:
            self._guard()
            record = self._required(command_key)
            if record.state is not CommandState.PENDING:
                return record  # In particular, UNKNOWN never resubmits.
            if record.kind not in (CommandKind.ENTRY, CommandKind.EXIT):
                raise RuntimeBlocked("EXPLICIT_CONTROL_DISPATCH_REQUIRED")
            if record.kind is CommandKind.ENTRY:
                self._require_protection_recovery_clear_v1()
            if record.kind is CommandKind.ENTRY and self._entry_version(record) == 2:
                raise RuntimeBlocked("V2_FINANCIAL_DISPATCH_UNAVAILABLE")
            self._require_qualification()
            request = self._request(record)
            if type(request) is not NativeOrderRequest:
                raise RuntimeBlocked("UNSUPPORTED_NATIVE_COMMAND")
            if record.kind is CommandKind.ENTRY:
                plan = self._plan(record)
                reservations = self.reservation_totals(plan.scope, record.command_key)
                try:
                    final = self.entry_risk(plan, self._now(), reservations)
                    if (type(final) is not r.EntryDecision or final.stage != "SUBMIT"
                            or not final.submission_ready or final.blockers
                            or final.reservations != reservations
                            or _economic_identity(final) != _economic_identity(plan)):
                        raise RuntimeBlocked("FRESH_IMMUTABLE_SUBMIT_RISK_REQUIRED")
                    self._entry_gate(record, final)
                except Exception:  # noqa: BLE001 - callback errors can carry secret text.
                    raise RuntimeBlocked("FRESH_IMMUTABLE_SUBMIT_RISK_REQUIRED") from None
            else:
                self._exit_gate(record, request)
            claimed = self.journal.claim(command_key)  # Durable before native effect.
            if claimed is None:
                return self._required(command_key)
            self._guard()
            try:
                if record.kind is CommandKind.ENTRY:
                    self._entry_gate(record, final)
                else:
                    self._exit_gate(record, request)
            except Exception:  # noqa: BLE001 - fail closed before native effect.
                self.journal.reject(command_key, "GATE_EXPIRED_BEFORE_NATIVE_EFFECT")
                self._active = False
                raise RuntimeBlocked("GATE_EXPIRED_BEFORE_NATIVE_EFFECT") from None
            try:
                result = self.bridge.submit(request)
                if result.state == "UNKNOWN":
                    self._unknown(command_key)
            except Exception:  # noqa: BLE001 - uncertain invocation is never automatically retried.
                self._unknown(command_key)
            return self._required(command_key)

    def _unknown(self, command_key: str) -> None:
        record = self._required(command_key)
        # A reentrant real fill/ACK may have committed before submit raised.
        if record.state in (CommandState.SENDING, CommandState.UNKNOWN):
            self.journal.mark_unknown(command_key, "NATIVE_HANDOFF_AMBIGUOUS")
        self._active = False

    def consume_native(self, event: object, context: NativeEventContext, *,
                       decoder: NativeEventDecoder | None = None) -> NativeOrderEvidence:
        with self._mutex:
            self._guard()
            evidence = self.bridge.consume(event, context, decoder=decoder)
            record = next((item for item in self.journal.list_commands()
                           if item.client_order_id == evidence.client_order_id), None)
            state = self._find_owned(evidence.client_order_id)
            if record is None or state is None:
                raise RuntimeBlocked("DURABLE_IMMUTABLE_MAPPING_REQUIRED")
            if evidence.kind == "FILL":
                # Pause and revoke all readbacks before any candidate validation
                # or persistence attempt. Failure must never restore authority.
                self._readbacks.pop(state.position_id, None)
                self._leverage_readbacks.clear()
                self._active = False
                if evidence.fill is None or evidence.venue_order_id is None:
                    raise RuntimeBlocked("NATIVE_FILL_CANDIDATE_REQUIRED")
                quarantine = FillQuarantineRecord(
                    NativeExecutionCandidate(evidence.fill, evidence.venue_order_id),
                    "AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED")
                reduced = self._reduce_event(state, quarantine,
                    dict(self._projections[state.position_id][2]))
                self._persist(quarantine, reduced)
                return evidence  # No ACK, accounting, terminal conclusion or reservation release.
            if record.state is CommandState.PENDING:
                self._active = False
                raise RuntimeBlocked("UNSENT_COMMAND_HAS_NATIVE_EVENT")
            cancellations = self._cancel_controls(evidence.client_order_id)
            if ((evidence.kind in ("CANCELED", "EXPIRED") or (
                    evidence.kind == "STATUS" and evidence.native_status == "CANCELED"))
                    and any(not self._cancel_identity_matches(control, evidence, state)
                            for control in cancellations)):
                # One fixed conflict per owned target, regardless of callback count
                # or untrusted venue IDs. Do not record cancellation or ACK it.
                conflict_identity = ("CANCEL_IDENTITY_UNVERIFIED", evidence.client_order_id)
                reduced = self._reduce_event(state, conflict_identity,
                    dict(self._projections[state.position_id][2]))
                self._persist(conflict_identity, reduced)
                for control in cancellations:
                    if control.state is CommandState.SENDING:
                        self.journal.mark_unknown(control.command_key,
                                                  "NATIVE_CANCEL_IDENTITY_UNVERIFIED")
                self._active = False
                return evidence
            # Non-fill observations retain their independent acknowledgement guards.
            identity = (evidence.kind, evidence.client_order_id, evidence.venue_order_id,
                        evidence.ts_event_ns, evidence.native_status, None)
            reduced = self._reduce_event(state, identity,
                dict(self._projections[state.position_id][2]))
            self._persist(identity, reduced)
            if reduced.conflicts:
                self._active = False
            accepted = evidence.kind in ("ACCEPTED", "CANCELED", "EXPIRED") or (
                evidence.kind == "STATUS" and evidence.native_status in (
                    "ACCEPTED", "PARTIALLY_FILLED", "FILLED", "CANCELED", "EXPIRED"))
            rejected = evidence.kind in ("DENIED", "REJECTED") or (
                evidence.kind == "STATUS" and evidence.native_status in ("DENIED", "REJECTED"))
            if accepted:
                if record.state in (CommandState.SENDING, CommandState.UNKNOWN):
                    self.journal.acknowledge(record.command_key, "SCOPED_NATIVE_EVENT")
            elif rejected:
                if record.state in (CommandState.SENDING, CommandState.UNKNOWN):
                    self.journal.reject(record.command_key, "SCOPED_NATIVE_REJECTION")
                self._active = False
            if evidence.kind == "CANCELED":
                for control in cancellations:
                    if (control.state in (CommandState.SENDING, CommandState.UNKNOWN)
                            and self._cancel_identity_matches(control, evidence, state)):
                        self.journal.acknowledge(control.command_key, "SCOPED_NATIVE_CANCEL_EVENT")
            # Terminal status is not full execution/flat proof; reservation stays held.
            return evidence

    def _exit_gate(self, record: CommandRecord, request: NativeOrderRequest) -> None:
        state = self.projection(request.owned.position_id)
        self._exclusive_readback(state)
        if (record.kind is not CommandKind.EXIT or request.owned.role != "EXIT"
                or not request.owned.reduce_only or state.conflicts
                or request.owned.quantity > state.owned_quantity
                or any(item.command_key != record.command_key and item.kind is CommandKind.EXIT
                       and not item.terminal_confirmed for item in self.journal.list_commands())):
            raise RuntimeBlocked("EXIT_EXCEEDS_EXCLUSIVE_OWNERSHIP")

    def _attached_target_v1(self, position_id: str) -> AttachedProtectionTargetV1:
        """Derive the target from durable attached ENTRY and credited actual ownership."""
        self._guard()
        state = self.projection(position_id)
        entries = tuple(order for order in state.orders if order.role == "ENTRY")
        if (len(entries) != 1 or state.conflicts or state.owned_quantity <= 0
                or state.latest_fill_at is None):
            raise RuntimeBlocked("AUTHENTICATED_ATTACHED_PROTECTION_REQUIRED")
        parent = entries[0]
        records = tuple(record for record in self.journal.list_commands()
            if record.kind is CommandKind.ENTRY and record.client_order_id == parent.client_order_id)
        if len(records) != 1:
            raise RuntimeBlocked("AUTHENTICATED_ATTACHED_PROTECTION_REQUIRED")
        record = records[0]
        if (record.state not in (CommandState.SENDING, CommandState.UNKNOWN, CommandState.ACKNOWLEDGED)
                or record.terminal_confirmed
                or hashlib.sha256(record.payload_json.encode()).hexdigest() != record.payload_hash):
            raise RuntimeBlocked("AUTHENTICATED_ATTACHED_PROTECTION_REQUIRED")
        request, policy = self._request(record), self._policy(record)
        original = self.bridge._requests.get(record.client_order_id)
        if (type(request) not in (NativeOrderRequest, LinearFixedLimitNativeOrderRequestV2)
                or type(original) is not type(request) or original != request
                or request.owned != parent or type(request.attached) is not AttachedProtection
                or request.attached.stop_loss != policy.stop_price
                or request.attached.take_profit != policy.take_profit_price
                or request.attached.trigger_source != policy.trigger_source
                or parent.policy_revision != policy.revision or parent.generation != policy.generation):
            raise RuntimeBlocked("AUTHENTICATED_ATTACHED_PROTECTION_REQUIRED")
        venues, _seen = self._history_venues(request, (), self.persisted_candidates())
        if len(venues) != 1:
            raise RuntimeBlocked("AUTHENTICATED_ATTACHED_PROTECTION_REQUIRED")
        return AttachedProtectionTargetV1(state.scope, position_id, state.instrument_id,
            parent.client_order_id, next(iter(venues)), parent.quantity, state.owned_quantity,
            state.entry_side, policy.revision, policy.generation, policy.stop_price,
            policy.take_profit_price, policy.trigger_source, state.latest_fill_at,
            record.payload_hash, _record_digest((parent, *state.executions)))

    def _current_attached_view_v1(self, receipt: AuthenticatedAttachedProtectionReceiptV1,
            target: AttachedProtectionTargetV1, snapshot: VenueSnapshot, operation: object) -> AttachedProtectionViewV1:
        session = self._receipt_session
        if session is None:
            raise RuntimeBlocked("AUTHENTICATED_ATTACHED_PROTECTION_REQUIRED")
        view = session.require_attached_protection_v1(receipt, target=target)
        # The Session and runtime clocks are separate configured callables.
        now = self._now()
        closed = session._require_attached_protection_closed_v1(receipt, target=target, now=now)
        cached = self._projections.get(target.position_id)
        state = None if cached is None else cached[1]
        entries = () if state is None else tuple(order for order in state.orders if order.role == "ENTRY")
        if (self._closed or self._receipt_session is not session or not self.worker_lock.acquired
                or self.worker_lock.acquisition_id != self._lease
                or self._attached_read_operations_v1.get(target.position_id) is not operation
                or view is not closed or view.snapshot is not snapshot
                or state is None or len(entries) != 1 or state.conflicts
                or state.scope != target.scope or state.instrument_id != target.instrument_id
                or state.entry_side != target.entry_side or state.owned_quantity != target.owned_quantity
                or state.latest_fill_at != target.latest_fill_at
                or entries[0].client_order_id != target.parent_client_order_id
                or entries[0].quantity != target.parent_quantity
                or entries[0].policy_revision != target.policy_revision
                or entries[0].generation != target.policy_generation
                or _record_digest((entries[0], *state.executions)) != target.projection_sha256
                or not timedelta(0) <= now - snapshot.observed_at <= self._max_age
                or snapshot.server_observed_at is None
                or not timedelta(0) <= now - snapshot.server_observed_at <= self._max_age):
            raise RuntimeBlocked("AUTHENTICATED_ATTACHED_PROTECTION_REQUIRED")
        return view

    def _exclusive_readback(self, state: p.ProtectionProjection) -> None:
        snapshot = self._readbacks.get(state.position_id)
        if (snapshot is None or not snapshot.complete or len(snapshot.positions) != 1
                or snapshot.scope != state.scope
                or snapshot.symbol != state.instrument_id.removesuffix("-LINEAR.BYBIT")
                or not self._fresh(snapshot.observed_at)
                or snapshot.server_observed_at is None
                or not self._fresh(snapshot.server_observed_at)
                or (state.latest_fill_at is not None
                    and snapshot.server_observed_at < state.latest_fill_at)):
            raise RuntimeBlocked("CURRENT_EXCLUSIVE_POSITION_READBACK_REQUIRED")
        row = snapshot.positions[0]
        owned_ids = {order.client_order_id for order in state.orders}
        attached_ids: dict[str, str] = {}
        issued = self._attached_readbacks_v1.get(state.position_id)
        if issued is not None:
            try:
                receipt, target = issued
                session = self._receipt_session
                if session is None or self._attached_target_v1(state.position_id) != target:
                    raise RuntimeBlocked("CURRENT_EXCLUSIVE_POSITION_READBACK_REQUIRED")
                operation = self._attached_read_operations_v1.get(state.position_id)
                if operation is None:
                    raise RuntimeBlocked("CURRENT_EXCLUSIVE_POSITION_READBACK_REQUIRED")
                view = self._current_attached_view_v1(receipt, target, snapshot, operation)
                attached_ids = {child.venue_order_id: child.client_order_id for child in view.children}
            except Exception:  # noqa: BLE001 - retained authority must fail closed without provider text.
                self._attached_readbacks_v1.pop(state.position_id, None)
                raise RuntimeBlocked("CURRENT_EXCLUSIVE_POSITION_READBACK_REQUIRED") from None
        if (row.position_idx != 0 or row.side != state.entry_side
                or row.quantity != state.owned_quantity
                or any(not item.reduce_only or (item.client_order_id not in owned_ids
                    and attached_ids.get(item.venue_order_id) != item.client_order_id)
                       for item in snapshot.orders)):
            raise RuntimeBlocked("CURRENT_EXCLUSIVE_POSITION_READBACK_REQUIRED")

    def queue_exit(self, command_key: str, position_id: str, quantity: Decimal,
                   reference_price: Decimal) -> CommandRecord:
        with self._mutex:
            self._guard()
            state = self.projection(position_id)
            self._exclusive_readback(state)
            if (state.conflicts or quantity <= 0 or quantity > state.owned_quantity
                    or any(item.kind is CommandKind.EXIT and not item.terminal_confirmed
                           and item.command_key != command_key for item in self.journal.list_commands())):
                raise RuntimeBlocked("EXIT_EXCEEDS_EXCLUSIVE_OWNERSHIP")
            policy = state.orders[0].policy_revision
            dummy = p.OwnedOrder("ec_" + "0" * 32, position_id, state.instrument_id,
                "SELL" if state.entry_side == "BUY" else "BUY", "EXIT", quantity, True, 0, policy)
            NativeOrderRequest(state.scope, dummy, "MARKET", reference_price)
            record = self.journal.register(command_key, CommandKind.EXIT,
                {"position_id": position_id, "quantity": str(quantity),
                 "reference_price": str(reference_price), "policy_revision": policy})
            order = replace(dummy, client_order_id=record.client_order_id)
            self._register(state, order)
            self._register_restored_request(self._request(record))
            return record

    async def poll_readback(self, adapter: BybitVenueReadback,
                            position_id: str) -> p.ProtectionAssessment:
        """Network occurs outside the state mutex; reduction reloads after await."""
        with self._mutex:
            self._guard()
            state = self.projection(position_id)
            if type(adapter) is not BybitVenueReadback or adapter.scope != state.scope:
                raise RuntimeBlocked("MATCHING_READBACK_SCOPE_REQUIRED")
            symbol = state.instrument_id.removesuffix("-LINEAR.BYBIT")
            session = self._receipt_session
            self._attached_readbacks_v1.pop(position_id, None)
            self._readbacks.pop(position_id, None)
        if session is None:
            snapshot = await adapter.snapshot(symbol)
            return self.consume_readback(position_id, snapshot)
        try:
            with self._mutex:
                target = self._attached_target_v1(position_id)
                operation = object()
                self._attached_read_operations_v1[position_id] = operation
                binding = session._binding
                if binding is None or adapter._client is not session.read_client(binding, scope=target.scope):
                    raise RuntimeBlocked("MATCHING_READBACK_SCOPE_REQUIRED")

            def guard() -> None:
                with self._mutex:
                    self._guard()
                    if (self._attached_read_operations_v1.get(position_id) is not operation
                            or self._attached_target_v1(position_id) != target):
                        raise RuntimeBlocked("AUTHENTICATED_ATTACHED_PROTECTION_REQUIRED")
                    issued = session._attached_protection_current_v1
                    if issued is not None and issued.view is not None:
                        observed = issued.view.snapshot
                        if (not self._fresh(observed.observed_at) or observed.server_observed_at is None
                                or not self._fresh(observed.server_observed_at)):
                            raise RuntimeBlocked("AUTHENTICATED_ATTACHED_PROTECTION_REQUIRED")

            receipt = await session.read_attached_protection_v1(binding, target=target,
                budget=MonitoringReadBudget(guard=guard), owner_guard=guard, max_evidence_age=self._max_age)
            view = session.require_attached_protection_v1(receipt, target=target)
            return self.consume_readback(position_id, view.snapshot,
                attached_receipt=receipt, attached_target=target, _attached_operation=operation)
        except ProtectionRecoveryGateError:
            raise
        except Exception:  # noqa: BLE001 - missing, conflicting and retired proofs are observations, not authority.
            snapshot = VenueSnapshot(self.bridge.scope, symbol, uuid4().hex, self._now(),
                None, False, (), (), ("AUTHENTICATED_ATTACHED_PROTECTION_REQUIRED",))
            return self.consume_readback(position_id, snapshot)

    def consume_readback(self, position_id: str, snapshot: VenueSnapshot, *,
            attached_receipt: AuthenticatedAttachedProtectionReceiptV1 | None = None,
            attached_target: AttachedProtectionTargetV1 | None = None,
            _attached_operation: object | None = None) -> p.ProtectionAssessment:
        with self._mutex:
            self._guard()
            state = self.projection(position_id)
            record = next(item for item in self.journal.list_commands()
                if item.kind is CommandKind.ENTRY and any(order.client_order_id == item.client_order_id
                                                         for order in state.orders))
            policy = self._policy(record)
            position: p.PositionEvidence | None = None
            reports: list[p.ProtectionEvidence] = []
            valid = (type(snapshot) is VenueSnapshot and snapshot.scope == state.scope
                and snapshot.symbol == state.instrument_id.removesuffix("-LINEAR.BYBIT")
                and snapshot.complete and self._fresh(snapshot.observed_at)
                and snapshot.server_observed_at is not None
                and self._fresh(snapshot.server_observed_at))
            if valid:
                owned = {order.client_order_id: order for order in state.orders}
                external = (any(item.client_order_id not in owned for item in snapshot.orders)
                            or len(snapshot.positions) != 1)
                if len(snapshot.positions) == 1:
                    row = snapshot.positions[0]
                    external = external or row.position_idx != 0
                    observed = snapshot.server_observed_at
                    assert observed is not None
                    position = p.PositionEvidence(state.scope, state.position_id,
                        state.instrument_id, row.side or state.entry_side, row.quantity,
                        observed, snapshot.cycle_id, True, external)
                    for order_report in snapshot.orders:
                        order = owned.get(order_report.client_order_id)
                        if (order is None or order.role not in ("STOP", "TAKE_PROFIT")
                                or order_report.trigger_price is None
                                or order_report.trigger_source is None):
                            continue
                        reports.append(p.ProtectionEvidence(state.scope, order.client_order_id,
                            state.position_id, state.instrument_id, order_report.side, order.role,
                            order_report.leaves_quantity, order_report.reduce_only, order.generation,
                            order.policy_revision, order_report.trigger_price, order_report.trigger_source,
                            "ACTIVE" if order_report.status == "Untriggered" else "UNKNOWN",
                            observed, snapshot.cycle_id))
            assessment = p.assess_protection(state, policy, tuple(reports), position,
                                             now=self._now(), max_age=self._max_age)
            self._attached_readbacks_v1.pop(position_id, None)
            authenticated = False
            attached_view: AttachedProtectionViewV1 | None = None
            if attached_receipt is not None and attached_target is not None and self._receipt_session is not None:
                try:
                    if (not valid or _attached_operation is None
                            or self._attached_read_operations_v1.get(position_id) is not _attached_operation
                            or self._attached_target_v1(position_id) != attached_target):
                        raise RuntimeBlocked("AUTHENTICATED_ATTACHED_PROTECTION_REQUIRED")
                    view = self._current_attached_view_v1(attached_receipt, attached_target, snapshot, _attached_operation)
                    attached_view = view
                    # Actual child venue IDs remain separate from local registered orders.
                    # The same original receipt proves ownership even if protection is incomplete.
                    # Its coverage label is observational; no entry/financial permission is granted.
                    complete = view.protection_coverage == "COMPLETE"
                    assessment = p.ProtectionAssessment(
                        "OPEN_PROTECTED" if complete else "ATTENTION", state.owned_quantity,
                        Decimal(0) if complete else state.owned_quantity, True,
                        ("FINANCIAL_ENTRY_AUTHORITY_NOT_GRANTED",) if complete else (
                            "AUTHENTICATED_PROTECTION_COVERAGE_INCOMPLETE",
                            "FINANCIAL_ENTRY_AUTHORITY_NOT_GRANTED"))
                    self._attached_readbacks_v1[position_id] = attached_receipt, attached_target
                    authenticated = True
                except Exception:  # noqa: BLE001 - no copied, stale or foreign receipt is authority.
                    valid = False
            clear_recovery_after_persist = (
                authenticated and attached_view is not None and attached_target is not None
                and attached_view.protection_coverage == "COMPLETE")
            if (authenticated and attached_view is not None and attached_target is not None
                    and not clear_recovery_after_persist):
                self._require_protection_recovery_v1(attached_target, snapshot)
            if not authenticated:
                self._attached_read_operations_v1.pop(position_id, None)
                assessment = replace(assessment, entries_blocked=True,
                    status="ATTENTION" if assessment.status == "OPEN_PROTECTED" else assessment.status,
                    reasons=assessment.reasons + ("AUTHENTICATED_ACCOUNT_BINDING_REQUIRED",))
            self._persist(("READBACK", snapshot.cycle_id, assessment), state)
            if clear_recovery_after_persist and attached_target is not None:
                self._clear_protection_recovery_v1(attached_target, snapshot)
            self._readbacks.pop(position_id, None)
            if valid:
                self._readbacks[position_id] = snapshot
            self._active = False
            return assessment
