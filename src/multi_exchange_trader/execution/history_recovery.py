"""Closed historical evidence and pure replay; no receipt or execution authority."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Literal

from . import protection as p
from .execution_credit import (
    EXECUTION_CREDIT_CODES,
    CreditReplayState,
    ExecutionObservationReference,
    HistoricalOrderEvidence,
    _digest,
    candidate_identity,
    execution_guard,
)
from .execution_provenance import NativeExecutionCandidate
from .history import (
    EPOCH,
    ExecutionRecord,
    HistoryWindow,
    OrderHistoryRecord,
    validate_history_record,
)
from .history_store import _variant
from .native_events import _text

HISTORY_RECOVERY_CODES = EXECUTION_CREDIT_CODES | frozenset({
    "HISTORY_CURRENT_ORDER_REQUIRED", "NATIVE_CORROBORATION_REQUIRED", "HISTORY_RECOVERY_CONFLICT",
})


def _codes(values: tuple[str, ...], *, nonempty: bool = False) -> None:
    if (type(values) is not tuple or len(values) > len(HISTORY_RECOVERY_CODES)
            or (nonempty and not values)
            or any(type(item) is not str or item not in HISTORY_RECOVERY_CODES for item in values)
            or len(set(values)) != len(values)):
        raise ValueError("HISTORY_RECOVERY_CODES_INVALID")


def _source(source: ExecutionObservationReference,
            row: ExecutionRecord | OrderHistoryRecord, digest: str) -> None:
    if type(source) is not ExecutionObservationReference:
        raise ValueError("HISTORY_RECOVERY_SOURCE_INVALID")
    source.__post_init__()
    _digest(digest)
    validate_history_record(row, symbol=source.symbol,
        window=HistoryWindow(source.start_ms, source.end_ms))
    if _variant(row)[2] != digest:
        raise ValueError("HISTORY_RECOVERY_VARIANT_INVALID")


@dataclass(frozen=True)
class HistoryExecutionEvidence:
    source: ExecutionObservationReference
    execution: ExecutionRecord
    execution_variant_hash: str

    def __post_init__(self) -> None:
        if type(self.execution) is not ExecutionRecord:
            raise ValueError("HISTORY_RECOVERY_EXECUTION_INVALID")
        _source(self.source, self.execution, self.execution_variant_hash)


@dataclass(frozen=True)
class HistoryOrderEvidence:
    source: ExecutionObservationReference
    order: OrderHistoryRecord
    order_variant_hash: str

    def __post_init__(self) -> None:
        if type(self.order) is not OrderHistoryRecord:
            raise ValueError("HISTORY_RECOVERY_ORDER_INVALID")
        _source(self.source, self.order, self.order_variant_hash)


def _owned(owned: p.OwnedOrder, command_key: str, command_payload_hash: str) -> None:
    if type(owned) is not p.OwnedOrder:
        raise ValueError("HISTORY_RECOVERY_OWNERSHIP_REQUIRED")
    owned.__post_init__()
    for value in (owned.client_order_id, owned.position_id, owned.instrument_id,
                  owned.policy_revision, command_key):
        _text(value)
    _digest(command_payload_hash)
    if (owned.role not in ("ENTRY", "EXIT") or owned.side not in ("BUY", "SELL")
            or type(owned.generation) is not int
            or owned.reduce_only != (owned.role == "EXIT")):
        raise ValueError("HISTORY_RECOVERY_OWNERSHIP_REQUIRED")


@dataclass(frozen=True)
class HistoryExecutionCredit:
    execution: HistoryExecutionEvidence
    order: HistoryOrderEvidence
    owned_order: p.OwnedOrder
    command_key: str
    command_payload_hash: str

    def __post_init__(self) -> None:
        if (type(self.execution) is not HistoryExecutionEvidence
                or type(self.order) is not HistoryOrderEvidence):
            raise ValueError("HISTORY_RECOVERY_CREDIT_INVALID")
        self.execution.__post_init__()
        self.order.__post_init__()
        _owned(self.owned_order, self.command_key, self.command_payload_hash)
        row, order, owned = self.execution.execution, self.order.order, self.owned_order
        if (self.execution.source != self.order.source or row.execution_type != "Trade"
                or not row.client_order_id or not row.venue_order_id
                or row.client_order_id != order.client_order_id
                or row.venue_order_id != order.venue_order_id or row.symbol != order.symbol
                or row.client_order_id != owned.client_order_id
                or row.side != order.side or row.side.upper() != owned.side
                or order.position_idx != 0 or order.reduce_only != owned.reduce_only
                or order.quantity != owned.quantity):
            raise ValueError("HISTORY_RECOVERY_CREDIT_INVALID")


@dataclass(frozen=True)
class HistoryRecoveryConflict:
    execution: HistoryExecutionEvidence
    owned_order: p.OwnedOrder
    command_key: str
    command_payload_hash: str
    mismatch_codes: tuple[str, ...]
    history_conflict_keys: tuple[str, ...]
    order_sources: tuple[HistoricalOrderEvidence, ...]
    prior_credit_event_id: str | None
    candidate_id: str | None

    def __post_init__(self) -> None:
        if type(self.execution) is not HistoryExecutionEvidence:
            raise ValueError("HISTORY_RECOVERY_CONFLICT_INVALID")
        self.execution.__post_init__()
        _owned(self.owned_order, self.command_key, self.command_payload_hash)
        _codes(self.mismatch_codes, nonempty=True)
        if (type(self.history_conflict_keys) is not tuple or len(self.history_conflict_keys) > 256
                or any(type(item) is not str for item in self.history_conflict_keys)
                or len(set(self.history_conflict_keys)) != len(self.history_conflict_keys)):
            raise ValueError("HISTORY_RECOVERY_CONFLICT_INVALID")
        for key in self.history_conflict_keys:
            _digest(key)
        if (type(self.order_sources) is not tuple or len(self.order_sources) > 128
                or any(type(item) is not HistoricalOrderEvidence
                       or item.scope != self.execution.source.scope for item in self.order_sources)
                or len(set(self.order_sources)) != len(self.order_sources)):
            raise ValueError("HISTORY_RECOVERY_CONFLICT_INVALID")
        for item in self.order_sources:
            item.__post_init__()
        if self.candidate_id is not None:
            _digest(self.candidate_id)
        if self.prior_credit_event_id is not None and self.prior_credit_event_id != _guard(self):
            raise ValueError("HISTORY_RECOVERY_CONFLICT_INVALID")


@dataclass(frozen=True)
class NativeHistoryResolution:
    candidate_id: str
    prior_credit_event_id: str
    credit_digest: str
    execution: HistoryExecutionEvidence

    def __post_init__(self) -> None:
        _digest(self.candidate_id)
        _digest(self.credit_digest)
        if (type(self.prior_credit_event_id) is not str
                or not self.prior_credit_event_id.startswith("credit:")):
            raise ValueError("HISTORY_RECOVERY_RESOLUTION_INVALID")
        _digest(self.prior_credit_event_id[7:])
        if type(self.execution) is not HistoryExecutionEvidence:
            raise ValueError("HISTORY_RECOVERY_RESOLUTION_INVALID")
        self.execution.__post_init__()


@dataclass(frozen=True)
class HistoryRecoveryResult:
    execution_id: str
    status: Literal["CREDITED", "DUPLICATE", "CONFLICT", "UNRESOLVED"]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.execution_id)
        _codes(self.reason_codes)
        if type(self.status) is not str or self.status not in (
                "CREDITED", "DUPLICATE", "CONFLICT", "UNRESOLVED"):
            raise ValueError("HISTORY_RECOVERY_RESULT_INVALID")


def _hash(record: object) -> str:
    from . import codec

    return hashlib.sha256(codec.encode(record).encode()).hexdigest()


def history_decision_event_id(conflict: HistoryRecoveryConflict) -> str:
    if type(conflict) is not HistoryRecoveryConflict:
        raise ValueError("HISTORY_RECOVERY_CONFLICT_INVALID")
    conflict.__post_init__()
    return "history-decision:" + _hash(conflict)


def history_resolution_event_id(resolution: NativeHistoryResolution) -> str:
    if type(resolution) is not NativeHistoryResolution:
        raise ValueError("HISTORY_RECOVERY_RESOLUTION_INVALID")
    resolution.__post_init__()
    return "history-resolution:" + _hash(resolution)


def history_credit_digest(credit: HistoryExecutionCredit) -> str:
    if type(credit) is not HistoryExecutionCredit:
        raise ValueError("HISTORY_RECOVERY_CREDIT_INVALID")
    credit.__post_init__()
    return _hash(credit)


def _guard(event: HistoryExecutionCredit | HistoryRecoveryConflict) -> str:
    return execution_guard(event.execution.source.scope, event.owned_order.instrument_id,
                           event.execution.execution.execution_id)


@dataclass(frozen=True)
class HistoryRecoveryReplayState:
    credits: tuple[HistoryExecutionCredit, ...] = ()
    decisions: tuple[HistoryRecoveryConflict, ...] = ()
    resolutions: tuple[NativeHistoryResolution, ...] = ()

    def __post_init__(self) -> None:
        for records, kind in ((self.credits, HistoryExecutionCredit),
                              (self.decisions, HistoryRecoveryConflict),
                              (self.resolutions, NativeHistoryResolution)):
            if type(records) is not tuple or any(type(item) is not kind for item in records):
                raise ValueError("HISTORY_RECOVERY_REPLAY_INVALID")

    def credit_for(self, guard: str) -> HistoryExecutionCredit | None:
        return next((item for item in self.credits if _guard(item) == guard), None)


def compare_history_candidate(candidate: NativeExecutionCandidate,
                              execution: HistoryExecutionEvidence,
                              instrument_id: str) -> tuple[str, ...]:
    """Exact shared values from genuine records; never manufacture native evidence."""
    if (type(candidate) is not NativeExecutionCandidate
            or type(execution) is not HistoryExecutionEvidence):
        raise ValueError("HISTORY_RECOVERY_COMPARISON_INVALID")
    candidate.__post_init__()
    execution.__post_init__()
    row, fill = execution.execution, candidate.fill
    comparisons = (
        (fill.scope, execution.source.scope, "SCOPE_MISMATCH"),
        (fill.execution_id, row.execution_id, "EXECUTION_ID_MISMATCH"),
        (fill.client_order_id, row.client_order_id, "CLIENT_ORDER_ID_MISMATCH"),
        (candidate.venue_order_id, row.venue_order_id, "VENUE_ORDER_ID_MISMATCH"),
        (fill.instrument_id, instrument_id, "INSTRUMENT_ID_MISMATCH"),
        (fill.side, row.side.upper(), "SIDE_MISMATCH"),
        (fill.quantity, row.quantity, "QUANTITY_MISMATCH"),
        (fill.price, row.price, "PRICE_MISMATCH"),
        (fill.commission, row.fee, "COMMISSION_MISMATCH"),
        (fill.commission_currency, row.fee_currency, "COMMISSION_CURRENCY_MISMATCH"),
        (fill.ts_event_ns, row.time_ms * 1_000_000, "TS_EVENT_NS_MISMATCH"),
    )
    return tuple(code for left, right, code in comparisons if left != right)


def _fill(event: HistoryExecutionCredit) -> p.FillEvent:
    row, owned = event.execution.execution, event.owned_order
    return p.FillEvent(event.execution.source.scope, row.execution_id, owned.client_order_id,
        owned.position_id, owned.instrument_id, owned.side, row.quantity,
        EPOCH + timedelta(milliseconds=row.time_ms))


def _decision(event: HistoryExecutionCredit, codes: tuple[str, ...], prior: str | None = None,
              candidate: str | None = None) -> HistoryRecoveryConflict:
    return HistoryRecoveryConflict(event.execution, event.owned_order, event.command_key,
        event.command_payload_hash, codes, (), (), prior, candidate)


def _record_decision(state: p.ProtectionProjection, event: HistoryRecoveryConflict,
                     replay: HistoryRecoveryReplayState
                     ) -> tuple[p.ProtectionProjection, HistoryRecoveryReplayState]:
    conflict = p.ProjectionConflict("HISTORY_RECOVERY_CONFLICT", order=event.owned_order,
                                    client_order_id=event.owned_order.client_order_id)
    if conflict not in state.conflicts:
        state = replace(state, conflicts=state.conflicts + (conflict,))
    if event not in replay.decisions:
        replay = replace(replay, decisions=replay.decisions + (event,))
    return state, replay


def _resolve(state: p.ProtectionProjection, event: NativeHistoryResolution,
             candidates: tuple[NativeExecutionCandidate, ...], native: CreditReplayState,
             replay: HistoryRecoveryReplayState
             ) -> tuple[p.ProtectionProjection, CreditReplayState, HistoryRecoveryReplayState]:
    previous = replay.credit_for(event.prior_credit_event_id)
    candidate = next((item for item in candidates
                      if candidate_identity(item) == event.candidate_id), None)
    if (previous is None or candidate is None
            or history_credit_digest(previous) != event.credit_digest
            or previous.owned_order not in state.orders
            or previous.execution.source.scope != state.scope
            or previous.owned_order.position_id != state.position_id
            or previous.owned_order.instrument_id != state.instrument_id
            or candidate.fill.position_id != state.position_id
            or event.execution.execution != previous.execution.execution
            or compare_history_candidate(candidate, event.execution, state.instrument_id)
            or compare_history_candidate(candidate, previous.execution, state.instrument_id)
            or any(_guard(item) == event.prior_credit_event_id for item in replay.decisions)
            or any(execution_guard(item.scope, item.instrument_id, item.execution_id)
                   == event.prior_credit_event_id for item in native.decisions)):
        raise ValueError("HISTORY_RECOVERY_RESOLUTION_INVALID")
    if event not in replay.resolutions:
        replay = replace(replay, resolutions=replay.resolutions + (event,))
    if event.candidate_id not in native.resolved_candidate_ids:
        native = replace(native, resolved_candidate_ids=native.resolved_candidate_ids + (event.candidate_id,))
    if not any(item.fill.client_order_id == candidate.fill.client_order_id
               and candidate_identity(item) not in native.resolved_candidate_ids for item in candidates):
        state = replace(state, conflicts=tuple(item for item in state.conflicts
            if not (item.reason == "AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED"
                    and item.client_order_id == candidate.fill.client_order_id)))
    return state, native, replay


def reduce_history_event(state: p.ProtectionProjection,
                         event: HistoryExecutionCredit | HistoryRecoveryConflict | NativeHistoryResolution,
                         *, candidates: tuple[NativeExecutionCandidate, ...],
                         native_replay: CreditReplayState, replay: HistoryRecoveryReplayState
                         ) -> tuple[p.ProtectionProjection, CreditReplayState, HistoryRecoveryReplayState]:
    """Apply data to registered intent only; caller separately verifies journal/receipt authority."""
    if type(event) not in (HistoryExecutionCredit, HistoryRecoveryConflict, NativeHistoryResolution):
        raise ValueError("HISTORY_RECOVERY_EVENT_REQUIRED")
    event.__post_init__()
    if type(event) is NativeHistoryResolution:
        return _resolve(state, event, candidates, native_replay, replay)
    assert isinstance(event, (HistoryExecutionCredit, HistoryRecoveryConflict))
    owned = event.owned_order
    if (event.execution.source.scope != state.scope or owned not in state.orders
            or owned.position_id != state.position_id or owned.instrument_id != state.instrument_id):
        raise ValueError("HISTORY_RECOVERY_OWNERSHIP_REQUIRED")
    if type(event) is HistoryRecoveryConflict:
        if event.candidate_id is not None and not any(candidate_identity(item) == event.candidate_id
                and execution_guard(item.fill.scope, item.fill.instrument_id, item.fill.execution_id)
                == _guard(event) for item in candidates):
            raise ValueError("HISTORY_RECOVERY_EXACT_CANDIDATE_REQUIRED")
        state, replay = _record_decision(state, event, replay)
        return state, native_replay, replay
    assert isinstance(event, HistoryExecutionCredit)
    guard = _guard(event)
    previous = replay.credit_for(guard)
    native_credit = native_replay.credit_for(guard)
    codes: tuple[str, ...] = ()
    prior: str | None = None
    candidate_id: str | None = None
    if previous is not None:
        if (previous.execution.execution == event.execution.execution
                and previous.owned_order == owned and previous.command_key == event.command_key
                and previous.command_payload_hash == event.command_payload_hash):
            return state, native_replay, replay
        codes, prior = ("HISTORY_EXECUTION_CONFLICT",), guard
    elif native_credit is not None:
        if not any(item == native_credit.candidate for item in candidates):
            raise ValueError("HISTORY_RECOVERY_EXACT_CANDIDATE_REQUIRED")
        codes = compare_history_candidate(native_credit.candidate, event.execution, owned.instrument_id)
        if (not codes and native_credit.command_key == event.command_key
                and native_credit.candidate.fill.position_id == owned.position_id):
            return state, native_replay, replay
        codes = codes or ("EXECUTION_GUARD_CONFLICT",)
        prior, candidate_id = guard, native_credit.candidate_id
    elif any(execution_guard(item.fill.scope, item.fill.instrument_id, item.fill.execution_id) == guard
             for item in candidates):
        codes = ("NATIVE_CORROBORATION_REQUIRED",)
    elif (any(_guard(item) == guard for item in replay.decisions)
          or any(execution_guard(item.scope, item.instrument_id, item.execution_id) == guard
                 for item in native_replay.decisions)):
        codes = ("EXECUTION_DECISION_CONFLICT",)
    elif any(item.reason == "EXECUTION_ID_CONFLICT" and item.event is not None
             and item.event.execution_id == event.execution.execution.execution_id for item in state.conflicts):
        codes = ("EXECUTION_ID_CONFLICT",)
    if codes:
        state, replay = _record_decision(state, _decision(event, codes, prior, candidate_id), replay)
        return state, native_replay, replay
    reduced = p.apply_fill(state, _fill(event))
    if len(reduced.executions) != len(state.executions) + 1:
        codes = tuple(item.reason for item in reduced.conflicts if item not in state.conflicts)
        reduced, replay = _record_decision(reduced, _decision(event,
            codes or ("EXECUTION_ID_CONFLICT",)), replay)
        return reduced, native_replay, replay
    return reduced, native_replay, replace(replay, credits=replay.credits + (event,))
