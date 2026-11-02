"""Bounded durable corroboration data; none of these records grants live authority.

The code allowlist starts with exact comparison mismatches. Reducer/runtime
extensions must add named codes with reviewed tests, never provider messages.
Existing candidate/history identity algorithms are deliberately not redefined.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, get_args

from .execution_provenance import (
    MismatchCode,
    NativeExecutionCandidate,
    VenueExecutionObservation,
    compare_execution_candidate,
)
from .history import HistoryWindow, _symbol, utc, validate_history_scope
from .native_events import _text
from .protection import ProtectionScope

EXECUTION_CREDIT_CODES: frozenset[str] = frozenset(get_args(MismatchCode))


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
