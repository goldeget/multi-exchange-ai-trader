"""Pure candidate selection, never order authorization or a provider transport.

Only trusted server code may issue the records supplied to this boundary. Opaque IDs
are references, not signatures. A fresh execution assessment and all existing native,
ownership and protection gates remain necessary after any successful selection.
"""

import json
import re
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Literal, get_args, get_origin, get_type_hints


class SelectionRejected(ValueError):
    """Fail-closed invalid selection evidence or model response."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


class AutomaticMode(Enum):
    FULL_AUTO = "FULL_AUTO"
    ALL_IN_ONE = "ALL_IN_ONE"


@dataclass(frozen=True)
class StrategyReference:
    strategy_id: str
    revision: str


@dataclass(frozen=True)
class SelectionScope:
    account_id: str
    environment: str
    allowed_symbols: tuple[str, ...]
    allowed_strategies: tuple[StrategyReference, ...]
    selected_symbols: tuple[str, ...]
    selected_strategies: tuple[StrategyReference, ...]


@dataclass(frozen=True)
class PolicyReference:
    account_id: str
    environment: str
    revision: str


@dataclass(frozen=True)
class PublicSnapshot:
    snapshot_id: str
    observed_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class SelectionLimits:
    max_candidates: int
    max_response_bytes: int
    max_reason_chars: int
    max_identifier_chars: int
    max_snapshot_age_seconds: int
    max_candidate_age_seconds: int
    max_request_age_seconds: int
    max_ttl_seconds: int


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    account_id: str
    environment: str
    symbol: str
    strategy: StrategyReference
    signal_id: str
    snapshot_id: str
    policy_revision: str
    plan_id: str
    enabled: bool
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class SelectionRequest:
    request_id: str
    scope: SelectionScope
    policy: PolicyReference
    snapshot: PublicSnapshot
    candidates: tuple[Candidate, ...]
    limits: SelectionLimits
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class AISelection:
    schema_version: int
    request_id: str
    snapshot_id: str
    policy_revision: str
    decision: Literal["SELECT", "ABSTAIN"]
    candidate_id: str | None
    reason: str


@dataclass(frozen=True)
class SelectionResult:
    mode: AutomaticMode
    status: Literal["SELECTED", "NO_CANDIDATE", "UNAVAILABLE", "ABSTAINED"]
    candidate: Candidate | None
    policy: PolicyReference
    snapshot: PublicSnapshot
    request_id: str | None
    assessed_at: datetime
    ai_selection: AISelection | None


def _utc(value: object) -> None:
    if (type(value) is not datetime or value.tzinfo is None
            or value.utcoffset() != timedelta(0)):
        raise SelectionRejected("INVALID_INPUT", "explicit UTC datetime required")


def _identifier(value: object, maximum: int) -> None:
    if (type(value) is not str or not 0 < len(value) <= maximum
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", value) is None):
        raise SelectionRejected("INVALID_INPUT", "bounded opaque identifier required")


def _record(value: object, expected: object, identifier_limit: int) -> None:
    """Runtime exact types prevent mutable containers, bool-as-int and subclasses."""
    if get_origin(expected) is tuple:
        if type(value) is not tuple or len(value) > 1024:
            raise SelectionRejected("INVALID_INPUT", "bounded immutable tuple required")
        for item in value:
            _record(item, get_args(expected)[0], identifier_limit)
    elif expected is str:
        _identifier(value, identifier_limit)
    elif expected is datetime:
        _utc(value)
    elif isinstance(expected, type) and is_dataclass(expected):
        if type(value) is not expected or not is_dataclass(value) or isinstance(value, type):
            raise SelectionRejected("INVALID_INPUT", "explicit server record required")
        hints = get_type_hints(expected)
        for item in fields(value):
            _record(getattr(value, item.name), hints[item.name], identifier_limit)
    elif type(value) is not expected:
        raise SelectionRejected("INVALID_INPUT", "field differs from exact schema")


def _limits(limits: SelectionLimits) -> None:
    _record(limits, SelectionLimits, 256)
    ceilings = (1024, 65536, 4096, 256, 31536000, 31536000, 31536000, 31536000)
    for item, ceiling in zip(fields(limits), ceilings, strict=True):
        if not 0 < getattr(limits, item.name) <= ceiling:
            raise SelectionRejected("INVALID_INPUT", "positive bounded operational limit required")


def _fresh(
    issued_at: datetime, expires_at: datetime, now: datetime, age_limit: int, ttl_limit: int,
) -> None:
    _utc(issued_at)
    _utc(expires_at)
    _utc(now)
    if issued_at > now:
        raise SelectionRejected("FUTURE_EVIDENCE", "evidence is from the future")
    if now - issued_at > timedelta(seconds=age_limit):
        raise SelectionRejected("STALE_EVIDENCE", "evidence exceeds explicit age limit")
    if expires_at <= now:
        raise SelectionRejected("EXPIRED", "expiry is exclusive")
    if not timedelta(0) < expires_at - issued_at <= timedelta(seconds=ttl_limit):
        raise SelectionRejected("INVALID_TTL", "evidence exceeds explicit lifetime limit")


def _validate(
    scope: SelectionScope, policy: PolicyReference, snapshot: PublicSnapshot,
    candidates: tuple[Candidate, ...], limits: SelectionLimits, now: datetime,
) -> None:
    _limits(limits)
    _utc(now)
    for value, expected in ((scope, SelectionScope), (policy, PolicyReference),
                            (snapshot, PublicSnapshot), (candidates, tuple[Candidate, ...])):
        _record(value, expected, limits.max_identifier_chars)
    if scope.environment not in {"DEMO", "LIVE"}:
        raise SelectionRejected("INVALID_INPUT", "explicit DEMO/LIVE scope required")
    if (policy.account_id, policy.environment) != (scope.account_id, scope.environment):
        raise SelectionRejected("SCOPE_MISMATCH", "policy account/environment differs")
    for values in (scope.allowed_symbols, scope.allowed_strategies,
                   scope.selected_symbols, scope.selected_strategies):
        if len(set(values)) != len(values):
            raise SelectionRejected("INVALID_INPUT", "scope lists must contain unique entries")
    if not scope.allowed_symbols or not scope.allowed_strategies:
        raise SelectionRejected("INVALID_INPUT", "explicit nonempty allowlists required")
    if (not set(scope.selected_symbols) <= set(scope.allowed_symbols)
            or not set(scope.selected_strategies) <= set(scope.allowed_strategies)):
        raise SelectionRejected("SCOPE_MISMATCH", "user selections exceed exact allowlist")
    if len(candidates) > limits.max_candidates:
        raise SelectionRejected("RESOURCE_LIMIT", "candidate bound exceeded")
    _fresh(snapshot.observed_at, snapshot.expires_at, now,
           limits.max_snapshot_age_seconds, limits.max_ttl_seconds)
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.candidate_id in seen:
            raise SelectionRejected("DUPLICATE_ID", "duplicate or conflicting candidate records")
        seen.add(candidate.candidate_id)
        if ((candidate.account_id, candidate.environment) != (scope.account_id, scope.environment)
                or candidate.symbol not in scope.allowed_symbols
                or candidate.strategy not in scope.allowed_strategies):
            raise SelectionRejected("SCOPE_MISMATCH", "candidate exceeds exact account/allowlist")
        if (candidate.snapshot_id != snapshot.snapshot_id
                or candidate.policy_revision != policy.revision):
            raise SelectionRejected("REVISION_MISMATCH", "candidate snapshot/policy differs")
        if not candidate.enabled:
            raise SelectionRejected("DISABLED_CANDIDATE", "candidate is not enabled")
        _fresh(candidate.issued_at, candidate.expires_at, now,
               limits.max_candidate_age_seconds, limits.max_ttl_seconds)
        if candidate.issued_at < snapshot.observed_at:
            raise SelectionRejected("INVALID_INPUT", "candidate predates its public snapshot")


def _request(request: SelectionRequest, now: datetime) -> None:
    if type(request) is not SelectionRequest:
        raise SelectionRejected("INVALID_INPUT", "server-issued request required")
    _validate(request.scope, request.policy, request.snapshot, request.candidates, request.limits, now)
    _identifier(request.request_id, request.limits.max_identifier_chars)
    _fresh(request.issued_at, request.expires_at, now,
           request.limits.max_request_age_seconds, request.limits.max_ttl_seconds)
    if not request.candidates:
        raise SelectionRejected("NO_CANDIDATE", "cannot ask AI to invent candidates")
    if (request.issued_at < request.snapshot.observed_at
            or any(request.issued_at < candidate.issued_at for candidate in request.candidates)):
        raise SelectionRejected("INVALID_INPUT", "request predates its evidence")


def _bounded_text(payload: object, maximum: int) -> None:
    if type(payload) is not str or len(payload) > maximum:
        raise SelectionRejected("RESOURCE_LIMIT", "bounded UTF-8 string required")
    try:
        if len(payload.encode("utf-8")) > maximum:
            raise SelectionRejected("RESOURCE_LIMIT", "UTF-8 byte limit exceeded")
    except UnicodeError as exc:
        raise SelectionRejected("INVALID_JSON", "invalid Unicode") from exc


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise SelectionRejected("INVALID_JSON", "duplicate object key")
        value[key] = item
    return value


def _nonfinite(value: str) -> object:
    raise SelectionRejected("INVALID_JSON", "non-finite JSON number")


def select_full_auto(
    *, scope: SelectionScope, policy: PolicyReference, snapshot: PublicSnapshot,
    candidates: tuple[Candidate, ...], priority_candidate_ids: tuple[str, ...] | None,
    limits: SelectionLimits, now: datetime,
) -> SelectionResult:
    """Select within explicit user choices using only an explicit server tie-break."""
    _validate(scope, policy, snapshot, candidates, limits, now)
    eligible = tuple(candidate for candidate in candidates
                     if candidate.symbol in scope.selected_symbols
                     and candidate.strategy in scope.selected_strategies)
    if priority_candidate_ids is not None:
        _record(priority_candidate_ids, tuple[str, ...], limits.max_identifier_chars)
        if (len(priority_candidate_ids) != len(eligible)
                or len(set(priority_candidate_ids)) != len(priority_candidate_ids)
                or set(priority_candidate_ids) != {candidate.candidate_id for candidate in eligible}):
            raise SelectionRejected("INVALID_PRIORITY", "exact eligible candidate permutation required")
        by_id = {candidate.candidate_id: candidate for candidate in eligible}
        eligible = tuple(by_id[identifier] for identifier in priority_candidate_ids)
    elif len(eligible) > 1:
        raise SelectionRejected("AMBIGUOUS", "explicit server priority required")
    candidate = eligible[0] if eligible else None
    return SelectionResult(AutomaticMode.FULL_AUTO, "SELECTED" if candidate else "NO_CANDIDATE",
                           candidate, policy, snapshot, None, now, None)


def build_all_in_one_request(
    *, request_id: str, scope: SelectionScope, policy: PolicyReference, snapshot: PublicSnapshot,
    candidates: tuple[Candidate, ...], limits: SelectionLimits, now: datetime,
    expires_at: datetime,
) -> SelectionRequest:
    """Retain trusted private binding; only public_candidate_request is provider input."""
    request = SelectionRequest(request_id, scope, policy, snapshot, candidates, limits, now, expires_at)
    _request(request, now)
    return request


def public_candidate_request(request: SelectionRequest, *, now: datetime) -> str:
    """Expose bounded public references only, never private server record serialization."""
    _request(request, now)
    payload = json.dumps({
        "schema_version": 1, "request_id": request.request_id,
        "snapshot_id": request.snapshot.snapshot_id, "policy_revision": request.policy.revision,
        "candidates": [{"candidate_id": candidate.candidate_id, "symbol": candidate.symbol,
                        "strategy_id": candidate.strategy.strategy_id,
                        "strategy_revision": candidate.strategy.revision}
                       for candidate in request.candidates],
    }, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    _bounded_text(payload, request.limits.max_response_bytes)
    return payload


def parse_ai_selection(payload: str, *, request: SelectionRequest, now: datetime) -> AISelection:
    """Reject extra keys, duplicate keys and any model-authored execution settings."""
    _request(request, now)
    _bounded_text(payload, request.limits.max_response_bytes)
    try:
        value = json.loads(payload, object_pairs_hook=_unique_object, parse_constant=_nonfinite)
    except (ValueError, RecursionError) as exc:
        raise SelectionRejected("INVALID_JSON", "strict JSON object required") from exc
    if type(value) is not dict or set(value) != {
        "schema_version", "request_id", "snapshot_id", "policy_revision", "decision",
        "candidate_id", "reason",
    }:
        raise SelectionRejected("INVALID_SCHEMA", "exact model response fields required")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise SelectionRejected("INVALID_SCHEMA", "schema_version must be integer 1")
    for key, expected in (("request_id", request.request_id),
                          ("snapshot_id", request.snapshot.snapshot_id),
                          ("policy_revision", request.policy.revision)):
        if type(value[key]) is not str or value[key] != expected:
            raise SelectionRejected("BINDING_MISMATCH", "response differs from issued request")
    reason = value["reason"]
    _bounded_text(reason, request.limits.max_response_bytes)
    if (len(reason) > request.limits.max_reason_chars
            or any(ord(char) < 32 or ord(char) == 127 for char in reason)):
        raise SelectionRejected("INVALID_SCHEMA", "bounded plain explanation required")
    candidate_id = value["candidate_id"]
    decision = value["decision"]
    if type(decision) is not str or decision not in {"SELECT", "ABSTAIN"}:
        raise SelectionRejected("INVALID_SCHEMA", "explicit SELECT or ABSTAIN required")
    if decision == "ABSTAIN":
        if candidate_id is not None:
            raise SelectionRejected("INVALID_SCHEMA", "abstention must use null candidate_id")
        return AISelection(1, request.request_id, request.snapshot.snapshot_id,
                           request.policy.revision, "ABSTAIN", None, reason)
    _identifier(candidate_id, request.limits.max_identifier_chars)
    if candidate_id not in {candidate.candidate_id for candidate in request.candidates}:
        raise SelectionRejected("UNKNOWN_CANDIDATE", "ID is absent from issued candidate set")
    return AISelection(1, request.request_id, request.snapshot.snapshot_id,
                       request.policy.revision, "SELECT", candidate_id, reason)


def select_all_in_one(
    *, request: SelectionRequest, response: str | None, now: datetime,
) -> SelectionResult:
    """Missing/provider-failed output or abstention never authorizes a fallback."""
    _request(request, now)
    selection = None if response is None else parse_ai_selection(response, request=request, now=now)
    candidate = None
    status: Literal["SELECTED", "UNAVAILABLE", "ABSTAINED"] = "UNAVAILABLE"
    if selection is not None:
        status = "ABSTAINED"
        if selection.decision == "SELECT":
            candidate = next(candidate for candidate in request.candidates
                             if candidate.candidate_id == selection.candidate_id)
            status = "SELECTED"
    return SelectionResult(AutomaticMode.ALL_IN_ONE, status, candidate, request.policy,
                           request.snapshot, request.request_id, now, selection)
