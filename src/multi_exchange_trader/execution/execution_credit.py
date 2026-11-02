"""Bounded durable corroboration data; none of these records grants live authority.

The code allowlist starts with exact comparison mismatches. Reducer/runtime
extensions must add named codes with reviewed tests, never provider messages.
Existing candidate/history identity algorithms are deliberately not redefined.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Literal, get_args

from . import protection as p
from .execution_provenance import (
    MismatchCode,
    NativeExecutionCandidate,
    VenueExecutionObservation,
    compare_execution_candidate,
)
from .history import HistoryWindow, _symbol, utc, validate_history_scope
from .native_events import _text
from .protection import ProtectionScope

EXECUTION_CREDIT_CODES: frozenset[str] = frozenset(get_args(MismatchCode)) | frozenset({
    "PERSISTED_EXECUTION_CANDIDATE_REQUIRED", "HISTORY_EXECUTION_REQUIRED", "HISTORY_OBSERVATION_REQUIRED",
    "HISTORY_TRADE_REQUIRED", "HISTORY_CLIENT_ORDER_ID_REQUIRED",
    "OWNED_COMMAND_INELIGIBLE", "HISTORY_EXECUTION_CONFLICT", "HISTORY_ORDER_CONFLICT",
    "EXECUTION_GUARD_CONFLICT", "NATIVE_CANDIDATE_VARIANT_CONFLICT",
    "EXECUTION_DECISION_CONFLICT", "FILL_EXCEEDS_ORDER_INTENT",
    "EXIT_EXCEEDS_OWNED_EXPOSURE", "EXECUTION_ID_CONFLICT",
})


def _digest(value: object) -> None:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("EXECUTION_CREDIT_DIGEST_INVALID")


def _codes(value: tuple[str, ...], *, nonempty: bool = False) -> None:
    if (type(value) is not tuple or len(value) > len(EXECUTION_CREDIT_CODES)
            or (nonempty and not value)
            or any(type(code) is not str or code not in EXECUTION_CREDIT_CODES for code in value)
            or len(set(value)) != len(value)):
        raise ValueError("EXECUTION_CREDIT_CODES_INVALID")


@dataclass(frozen=True)
class ExecutionObservationReference:
    scope: ProtectionScope
    binding_reference: str
    observation_key: str
    snapshot_id: str
    symbol: str
    start_ms: int
    end_ms: int
    observed_at: datetime
    server_observed_at: datetime

    def __post_init__(self) -> None:
        validate_history_scope(self.scope)
        for value in (self.binding_reference, self.snapshot_id):
            _text(value)
        _digest(self.observation_key)
        _symbol(self.symbol)
        HistoryWindow(self.start_ms, self.end_ms)
        utc(self.observed_at)
        utc(self.server_observed_at)
        if self.server_observed_at > self.observed_at:
            raise ValueError("EXECUTION_OBSERVATION_REFERENCE_INVALID")


@dataclass(frozen=True)
class CorroboratedExecutionCredit:
    candidate_id: str
    candidate: NativeExecutionCandidate
    observation: VenueExecutionObservation
    source: ExecutionObservationReference
    history_variant_hash: str
    command_key: str

    def __post_init__(self) -> None:
        _digest(self.candidate_id)
        _digest(self.history_variant_hash)
        _text(self.command_key)
        if (type(self.candidate) is not NativeExecutionCandidate
                or type(self.observation) is not VenueExecutionObservation
                or type(self.source) is not ExecutionObservationReference):
            raise ValueError("EXECUTION_CREDIT_INVALID")
        self.source.__post_init__()
        if (compare_execution_candidate(self.candidate, self.observation).mismatch_codes
                or self.source.scope != self.observation.scope
                or self.source.binding_reference != self.observation.binding_reference
                or self.source.snapshot_id != self.observation.history_batch_id):
            raise ValueError("EXECUTION_CREDIT_INVALID")


@dataclass(frozen=True)
class ExecutionCorroborationConflict:
    candidate_id: str
    scope: ProtectionScope
    instrument_id: str
    execution_id: str
    venue_order_id: str
    client_order_id: str
    source: ExecutionObservationReference
    history_variant_hash: str
    history_conflict_keys: tuple[str, ...]
    mismatch_codes: tuple[str, ...]
    prior_credit_event_id: str | None

    def __post_init__(self) -> None:
        _digest(self.candidate_id)
        _digest(self.history_variant_hash)
        validate_history_scope(self.scope)
        for value in (self.instrument_id, self.execution_id, self.venue_order_id,
                      self.client_order_id):
            _text(value)
        if (type(self.source) is not ExecutionObservationReference
                or self.source.scope != self.scope):
            raise ValueError("EXECUTION_CORROBORATION_CONFLICT_INVALID")
        self.source.__post_init__()
        if (type(self.history_conflict_keys) is not tuple or len(self.history_conflict_keys) > 256
                or any(type(key) is not str for key in self.history_conflict_keys)
                or len(set(self.history_conflict_keys)) != len(self.history_conflict_keys)):
            raise ValueError("EXECUTION_CORROBORATION_CONFLICT_INVALID")
        for key in self.history_conflict_keys:
            _digest(key)
        _codes(self.mismatch_codes, nonempty=True)
        if (self.prior_credit_event_id is not None
                and self.prior_credit_event_id != execution_guard(
                    self.scope, self.instrument_id, self.execution_id)):
            raise ValueError("EXECUTION_CORROBORATION_CONFLICT_INVALID")


@dataclass(frozen=True)
class ExecutionCreditResult:
    candidate_id: str
    status: Literal["CREDITED", "DUPLICATE", "CONFLICT", "UNRESOLVED"]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        try:
            _digest(self.candidate_id)
            _codes(self.reason_codes)
            if type(self.status) is not str or self.status not in (
                    "CREDITED", "DUPLICATE", "CONFLICT", "UNRESOLVED"):
                raise ValueError("EXECUTION_CREDIT_RESULT_INVALID")
        except ValueError:
            raise ValueError("EXECUTION_CREDIT_RESULT_INVALID") from None


def execution_guard(scope: ProtectionScope, instrument_id: str, execution_id: str) -> str:
    """Account-wide execution collision key; full venue/client identity stays in payload."""
    validate_history_scope(scope)
    _text(instrument_id)
    _text(execution_id)
    structured = json.dumps([scope.account_id, scope.environment, instrument_id, execution_id],
                            ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return "credit:" + hashlib.sha256(structured.encode("utf-8")).hexdigest()


def candidate_identity(candidate: NativeExecutionCandidate) -> str:
    """The original V2 identity algorithm, including native source metadata."""
    from . import codec

    fill = candidate.fill
    with localcontext() as context:
        context.prec = 256
        canonical = replace(candidate, fill=replace(fill, ts_init_ns=0, received_at_ns=0,
            quantity=fill.quantity.normalize(), price=fill.price.normalize(),
            commission=fill.commission.normalize() if fill.commission else Decimal(0)))
    return hashlib.sha256(codec.encode(canonical).encode()).hexdigest()


def decision_event_id(event: ExecutionCorroborationConflict) -> str:
    """Content-addressed immutable decision ID shared by replay and persistence."""
    from . import codec

    if type(event) is not ExecutionCorroborationConflict:
        raise ValueError("EXECUTION_CORROBORATION_CONFLICT_INVALID")
    event.__post_init__()
    return "decision:" + hashlib.sha256(codec.encode(event).encode()).hexdigest()


@dataclass(frozen=True)
class CreditReplayState:
    """Derived immutable replay data, never an authority token or serialized record."""

    credits: tuple[CorroboratedExecutionCredit, ...] = ()
    resolved_candidate_ids: tuple[str, ...] = ()
    decisions: tuple[ExecutionCorroborationConflict, ...] = ()

    def credit_for(self, guard: str) -> CorroboratedExecutionCredit | None:
        return next((item for item in self.credits if execution_guard(item.candidate.fill.scope,
            item.candidate.fill.instrument_id, item.candidate.fill.execution_id) == guard), None)


def same_credit(left: CorroboratedExecutionCredit, right: CorroboratedExecutionCredit) -> bool:
    """New receipt references do not change the first credit's durable economics."""
    return (left.candidate_id == right.candidate_id
            and left.command_key == right.command_key
            and left.history_variant_hash == right.history_variant_hash
            and candidate_identity(left.candidate) == candidate_identity(right.candidate)
            and not compare_execution_candidate(left.candidate, right.observation).mismatch_codes)


def _decision(event: CorroboratedExecutionCredit, codes: tuple[str, ...],
              prior: str | None) -> ExecutionCorroborationConflict:
    fill = event.candidate.fill
    return ExecutionCorroborationConflict(event.candidate_id, fill.scope, fill.instrument_id,
        fill.execution_id, event.candidate.venue_order_id, fill.client_order_id, event.source,
        event.history_variant_hash, (), codes, prior)


def _record_decision(state: p.ProtectionProjection, event: ExecutionCorroborationConflict,
                     candidate: NativeExecutionCandidate,
                     replay: CreditReplayState) -> tuple[p.ProtectionProjection, CreditReplayState]:
    conflict = p.ProjectionConflict("EXECUTION_CORROBORATION_CONFLICT",
        event=candidate.fill.to_owned_fill(), client_order_id=candidate.fill.client_order_id)
    if conflict not in state.conflicts:
        state = replace(state, conflicts=state.conflicts + (conflict,))
    if event not in replay.decisions:
        replay = replace(replay, decisions=replay.decisions + (event,))
    return state, replay


def reduce_credit_event(state: p.ProtectionProjection,
                        event: CorroboratedExecutionCredit | ExecutionCorroborationConflict,
                        *, candidates: tuple[NativeExecutionCandidate, ...],
                        replay: CreditReplayState) -> tuple[p.ProtectionProjection, CreditReplayState]:
    """Replay durable evidence with existing ownership/quantity protections; no I/O.

    A constructible durable record is only data. Live callers must independently
    establish receipt authority and commit atomically before installing this result.
    """
    if type(event) not in (CorroboratedExecutionCredit, ExecutionCorroborationConflict):
        raise ValueError("EXECUTION_CREDIT_EVENT_REQUIRED")
    event.__post_init__()
    candidate = next((item for item in candidates
                      if candidate_identity(item) == event.candidate_id), None)
    if candidate is None or (type(event) is CorroboratedExecutionCredit
                             and event.candidate != candidate):
        raise ValueError("EXECUTION_CREDIT_EXACT_CANDIDATE_REQUIRED")
    fill = candidate.fill
    order = next((item for item in state.orders
                  if item.client_order_id == fill.client_order_id), None)
    if (fill.scope != state.scope or fill.position_id != state.position_id
            or fill.instrument_id != state.instrument_id or order is None
            or fill.side != order.side or event.source.scope != state.scope):
        raise ValueError("EXECUTION_CREDIT_OWNERSHIP_REQUIRED")
    if type(event) is ExecutionCorroborationConflict:
        if (event.scope != state.scope or event.instrument_id != state.instrument_id
                or event.execution_id != fill.execution_id):
            raise ValueError("EXECUTION_CREDIT_OWNERSHIP_REQUIRED")
        return _record_decision(state, event, candidate, replay)
    assert isinstance(event, CorroboratedExecutionCredit)
    guard = execution_guard(fill.scope, fill.instrument_id, fill.execution_id)
    previous = replay.credit_for(guard)
    if previous is not None:
        if same_credit(previous, event):
            return state, replay
        codes: tuple[str, ...] = compare_execution_candidate(
            previous.candidate, event.observation).mismatch_codes or ("EXECUTION_GUARD_CONFLICT",)
        return _record_decision(state, _decision(event, codes, guard), candidate, replay)
    variants = {candidate_identity(item) for item in candidates
                if execution_guard(item.fill.scope, item.fill.instrument_id,
                                   item.fill.execution_id) == guard}
    if len(variants) != 1:
        return _record_decision(state, _decision(event,
            ("NATIVE_CANDIDATE_VARIANT_CONFLICT",), None), candidate, replay)
    if any(execution_guard(item.scope, item.instrument_id, item.execution_id) == guard
           for item in replay.decisions):
        return _record_decision(state, _decision(event,
            ("EXECUTION_DECISION_CONFLICT",), None), candidate, replay)
    if any(item.reason == "EXECUTION_ID_CONFLICT" and item.event is not None
           and item.event.execution_id == fill.execution_id for item in state.conflicts):
        return _record_decision(state, _decision(event,
            ("EXECUTION_ID_CONFLICT",), None), candidate, replay)
    reduced = p.apply_fill(state, fill.to_owned_fill())
    if len(reduced.executions) != len(state.executions) + 1:
        codes = tuple(item.reason for item in reduced.conflicts if item not in state.conflicts)
        return _record_decision(reduced, _decision(event,
            codes or ("EXECUTION_ID_CONFLICT",), None), candidate, replay)
    resolved = replay.resolved_candidate_ids + (event.candidate_id,)
    replay = replace(replay, credits=replay.credits + (event,), resolved_candidate_ids=resolved)
    unresolved = any(item.fill.client_order_id == fill.client_order_id
                     and candidate_identity(item) not in resolved for item in candidates)
    if not unresolved:
        reduced = replace(reduced, conflicts=tuple(item for item in reduced.conflicts
            if not (item.reason == "AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED"
                    and item.client_order_id == fill.client_order_id)))
    return reduced, replay
