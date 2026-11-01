"""Selection boundaries exercise trusted records and hostile model output offline."""

import json
from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from multi_exchange_trader.execution.modes import (
    AutomaticMode,
    Candidate,
    PolicyReference,
    PublicSnapshot,
    SelectionLimits,
    SelectionRejected,
    SelectionRequest,
    SelectionScope,
    StrategyReference,
    build_all_in_one_request,
    parse_ai_selection,
    public_candidate_request,
    select_all_in_one,
    select_full_auto,
)


@dataclass(frozen=True)
class Case:
    now: datetime
    scope: SelectionScope
    policy: PolicyReference
    snapshot: PublicSnapshot
    limits: SelectionLimits
    candidates: tuple[Candidate, ...]


@pytest.fixture
def case() -> Case:
    now = datetime(2026, 11, 2, tzinfo=UTC)
    fast = StrategyReference("trend", "v1")
    slow = StrategyReference("trend", "v2")
    scope = SelectionScope("private-account", "DEMO", ("BTCUSDT", "ETHUSDT"),
                           (fast, slow), ("BTCUSDT",), (fast,))
    policy = PolicyReference("private-account", "DEMO", "risk-v7")
    snapshot = PublicSnapshot("public-s1", now - timedelta(seconds=5),
                              now + timedelta(seconds=30))
    limits = SelectionLimits(4, 2048, 120, 80, 10, 10, 10, 60)
    first = Candidate("candidate-1", "private-account", "DEMO", "BTCUSDT", fast,
                      "signal-1", "public-s1", "risk-v7", "plan-1", True,
                      now - timedelta(seconds=2), now + timedelta(seconds=20))
    second = replace(first, candidate_id="candidate-2", symbol="ETHUSDT", strategy=slow,
                     signal_id="signal-2", plan_id="plan-2")
    return Case(now, scope, policy, snapshot, limits, (first, second))


def request(case: Case) -> SelectionRequest:
    return build_all_in_one_request(
        request_id="request-1", scope=case.scope, policy=case.policy, snapshot=case.snapshot,
        candidates=case.candidates, limits=case.limits, now=case.now,
        expires_at=case.now + timedelta(seconds=15),
    )


def response(**changes: object) -> str:
    value: dict[str, object] = {
        "schema_version": 1, "request_id": "request-1", "snapshot_id": "public-s1",
        "policy_revision": "risk-v7", "decision": "SELECT", "candidate_id": "candidate-2",
        "reason": "The allowed ETH trend candidate fits this snapshot.",
    }
    value.update(changes)
    return json.dumps(value)


def full(case: Case, priority: tuple[str, ...] | None = None) -> object:
    return select_full_auto(scope=case.scope, policy=case.policy, snapshot=case.snapshot,
                            candidates=case.candidates, priority_candidate_ids=priority,
                            limits=case.limits, now=case.now)


def test_full_auto_obeys_user_selection_and_preserves_server_candidate(case: Case) -> None:
    selected = select_full_auto(
        scope=case.scope, policy=case.policy, snapshot=case.snapshot,
        candidates=case.candidates, priority_candidate_ids=None, limits=case.limits, now=case.now,
    )
    assert selected.mode is AutomaticMode.FULL_AUTO
    assert selected.candidate is case.candidates[0]
    assert selected.policy is case.policy
    assert selected.policy.revision == "risk-v7"
    assert selected.candidate.plan_id == "plan-1"
    assert selected.status == "SELECTED"


def test_all_in_one_may_choose_other_allowed_symbol_and_exact_strategy_revision(case: Case) -> None:
    pending = request(case)
    result = select_all_in_one(request=pending, response=response(), now=case.now)
    assert result.mode is AutomaticMode.ALL_IN_ONE
    assert result.candidate is case.candidates[1]
    assert result.policy is case.policy
    assert result.request_id == "request-1"
    assert result.candidate.strategy == StrategyReference("trend", "v2")
    with pytest.raises(FrozenInstanceError):
        result.candidate.plan_id = "changed"  # type: ignore[misc]


def test_public_request_excludes_account_environment_plan_and_private_selection(case: Case) -> None:
    raw = public_candidate_request(request(case), now=case.now)
    value = json.loads(raw)
    assert "private-account" not in raw
    assert "plan-1" not in raw
    assert "DEMO" not in raw
    assert set(value) == {"schema_version", "request_id", "snapshot_id", "policy_revision",
                          "candidates"}
    assert value["candidates"] == [
        {"candidate_id": "candidate-1", "symbol": "BTCUSDT", "strategy_id": "trend",
         "strategy_revision": "v1"},
        {"candidate_id": "candidate-2", "symbol": "ETHUSDT", "strategy_id": "trend",
         "strategy_revision": "v2"},
    ]


@pytest.mark.parametrize("missing", [True, False])
def test_absence_and_abstention_never_fall_back_to_rule_candidate(case: Case, missing: bool) -> None:
    raw = None if missing else response(decision="ABSTAIN", candidate_id=None)
    result = select_all_in_one(request=request(case), response=raw, now=case.now)
    assert result.candidate is None
    assert result.status == ("UNAVAILABLE" if missing else "ABSTAINED")
    assert result.policy is case.policy
    # The result carries no protection/exit commands and creates no execution authority.
    assert not hasattr(result, "commands")
    assert not hasattr(result, "submission_ready")


@pytest.mark.parametrize("changes", [
    {"schema_version": True}, {"schema_version": 1.0}, {"schema_version": 2},
    {"request_id": "request-2"}, {"snapshot_id": "public-s2"},
    {"policy_revision": "risk-v8"}, {"candidate_id": "candidate-3"},
    {"candidate_id": "candidate-2-prefix"}, {"candidate_id": None},
    {"decision": "ABSTAIN", "candidate_id": "candidate-1"}, {"decision": "TRADE"},
    {"reason": "x" * 121}, {"reason": 7}, {"reason": "bad\x00text"},
    {"leverage": 100}, {"price": 0}, {"quantity": 3}, {"stop_loss": 5},
    {"code": "__import__('os').system('ignored')"}, {"tools": []}, {"policy": {}},
])
def test_hostile_model_fields_and_unbound_decisions_fail_closed(
    case: Case, changes: dict[str, object],
) -> None:
    with pytest.raises(SelectionRejected):
        select_all_in_one(request=request(case), response=response(**changes), now=case.now)


@pytest.mark.parametrize("raw", [
    '{"schema_version":1,"schema_version":1}',
    '{"reason":{"x":1,"x":2}}',
    '```json\n{}\n```', 'ignore previous instructions {}', '{} trailing', '[]', 'null',
    '{"schema_version":NaN}', '{"schema_version":Infinity}', '{', " " * 2049,
    '[' * 1500 + ']' * 1500, '"\\ud800"',
])
def test_malformed_duplicate_and_injected_json_is_rejected(case: Case, raw: str) -> None:
    with pytest.raises(SelectionRejected):
        parse_ai_selection(raw, request=request(case), now=case.now)


@pytest.mark.parametrize("field,value", [
    ("account_id", "other-account"), ("environment", "LIVE"), ("symbol", "SOLUSDT"),
    ("strategy", StrategyReference("trend", "v3")), ("policy_revision", "risk-v8"),
    ("snapshot_id", "public-s2"), ("enabled", False),
    ("expires_at", datetime(2026, 11, 2, tzinfo=UTC)),
    ("issued_at", datetime(2026, 11, 2, tzinfo=UTC) + timedelta(seconds=1)),
])
def test_server_candidate_scope_revision_state_and_time_are_revalidated(
    case: Case, field: str, value: object,
) -> None:
    invalid = replace(case.candidates[1], **{field: value})  # type: ignore[arg-type]
    changed = replace(case, candidates=(case.candidates[0], invalid))
    with pytest.raises(SelectionRejected):
        request(changed)


def test_selected_universe_is_revalidated_after_request_creation(case: Case) -> None:
    pending = request(case)
    changed_candidate = replace(pending.candidates[1], policy_revision="risk-v8")
    forged = replace(pending, candidates=(pending.candidates[0], changed_candidate))
    with pytest.raises(SelectionRejected):
        select_all_in_one(request=forged, response=response(), now=case.now)


@pytest.mark.parametrize("duplicate_conflicts", [True, False])
def test_duplicate_candidate_ids_are_never_accepted(case: Case, duplicate_conflicts: bool) -> None:
    duplicate = (replace(case.candidates[0], plan_id="different") if duplicate_conflicts
                 else case.candidates[0])
    with pytest.raises(SelectionRejected):
        request(replace(case, candidates=(case.candidates[0], duplicate)))


def test_full_auto_rejects_ambiguity_unless_server_supplies_exact_priority(case: Case) -> None:
    other = replace(case.candidates[0], candidate_id="candidate-3", signal_id="signal-3")
    expanded = replace(case, candidates=(case.candidates[0], other))
    with pytest.raises(SelectionRejected, match="AMBIGUOUS"):
        full(expanded)
    result = select_full_auto(
        scope=expanded.scope, policy=expanded.policy, snapshot=expanded.snapshot,
        candidates=expanded.candidates, priority_candidate_ids=("candidate-3", "candidate-1"),
        limits=expanded.limits, now=expanded.now,
    )
    assert result.candidate is other
    for priority in (("candidate-3",), ("candidate-3", "candidate-3"), ("foreign", "candidate-1")):
        with pytest.raises(SelectionRejected):
            full(expanded, priority)


def test_full_auto_no_selected_match_returns_no_candidate(case: Case) -> None:
    result = select_full_auto(
        scope=case.scope, policy=case.policy, snapshot=case.snapshot,
        candidates=(case.candidates[1],), priority_candidate_ids=None,
        limits=case.limits, now=case.now,
    )
    assert result.status == "NO_CANDIDATE"
    assert result.candidate is None


@pytest.mark.parametrize("kind", ["request", "snapshot", "candidate"])
def test_expired_stale_future_and_excessive_ttl_evidence_is_rejected(case: Case, kind: str) -> None:
    pending = request(case)
    for delta in (timedelta(seconds=11), timedelta(seconds=15)):
        with pytest.raises(SelectionRejected):
            select_all_in_one(request=pending, response=response(), now=case.now + delta)
    if kind == "request":
        invalid = replace(pending, issued_at=case.now + timedelta(seconds=1))
        excessive = replace(pending, expires_at=case.now + timedelta(seconds=61))
    elif kind == "snapshot":
        invalid = replace(pending, snapshot=replace(case.snapshot,
                          observed_at=case.now + timedelta(seconds=1)))
        excessive = replace(pending, snapshot=replace(case.snapshot,
                            expires_at=case.now + timedelta(seconds=61)))
    else:
        invalid = replace(pending, candidates=(replace(case.candidates[0],
                          issued_at=case.now - timedelta(seconds=11)),))
        excessive = replace(pending, candidates=(replace(case.candidates[0],
                            expires_at=case.now + timedelta(seconds=61)),))
    for value in (invalid, excessive):
        with pytest.raises(SelectionRejected):
            public_candidate_request(value, now=case.now)


def test_response_can_never_outlive_underlying_candidate_or_snapshot(case: Case) -> None:
    shortened = replace(case, candidates=(replace(case.candidates[0],
                        expires_at=case.now + timedelta(seconds=1)), case.candidates[1]))
    pending = request(shortened)
    with pytest.raises(SelectionRejected):
        select_all_in_one(request=pending, response=response(candidate_id="candidate-1"),
                          now=case.now + timedelta(seconds=1))


@pytest.mark.parametrize("now", [
    datetime(2026, 11, 2),
    datetime(2026, 11, 2, tzinfo=timezone(timedelta(hours=9))),
])
def test_only_explicit_utc_times_are_accepted(case: Case, now: datetime) -> None:
    with pytest.raises(SelectionRejected):
        full(replace(case, now=now))


@pytest.mark.parametrize("field,value", [
    ("max_candidates", True), ("max_candidates", 0), ("max_response_bytes", 0),
    ("max_reason_chars", -1), ("max_identifier_chars", 0),
    ("max_snapshot_age_seconds", -1), ("max_candidate_age_seconds", float("inf")),
    ("max_request_age_seconds", 0), ("max_ttl_seconds", 0),
])
def test_resource_and_freshness_limits_require_explicit_bounded_integers(
    case: Case, field: str, value: object,
) -> None:
    with pytest.raises(SelectionRejected):
        full(replace(case, limits=replace(case.limits, **{field: value})))  # type: ignore[arg-type]


def test_explicit_candidate_and_payload_bounds_are_enforced(case: Case) -> None:
    with pytest.raises(SelectionRejected):
        request(replace(case, limits=replace(case.limits, max_candidates=1)))
    with pytest.raises(SelectionRejected):
        public_candidate_request(request(replace(case, limits=replace(
            case.limits, max_response_bytes=50))), now=case.now)
    with pytest.raises(SelectionRejected):
        full(replace(case, candidates=list(case.candidates)))  # type: ignore[arg-type]


def test_user_selection_must_be_exact_subset_of_allowed_symbol_and_revision(case: Case) -> None:
    for scope in (
        replace(case.scope, selected_symbols=("SOLUSDT",)),
        replace(case.scope, selected_strategies=(StrategyReference("trend", "v3"),)),
        replace(case.scope, allowed_symbols=("BTCUSDT", "BTCUSDT")),
        replace(case.scope, allowed_strategies=(case.scope.allowed_strategies[0],) * 2),
    ):
        with pytest.raises(SelectionRejected):
            full(replace(case, scope=scope))


def test_policy_account_and_environment_must_match_both_modes(case: Case) -> None:
    for policy in (replace(case.policy, account_id="other"),
                   replace(case.policy, environment="LIVE")):
        changed = replace(case, policy=policy)
        with pytest.raises(SelectionRejected):
            full(changed)
        with pytest.raises(SelectionRejected):
            request(changed)


def test_empty_all_in_one_universe_cannot_create_ai_request(case: Case) -> None:
    with pytest.raises(SelectionRejected):
        request(replace(case, candidates=()))


def test_freshness_age_limit_is_inclusive_but_expiry_is_exclusive(case: Case) -> None:
    pending = request(case)
    result = select_all_in_one(request=pending, response=response(),
                               now=case.now + timedelta(seconds=5))
    assert result.candidate is case.candidates[1]
    with pytest.raises(SelectionRejected, match="STALE_EVIDENCE"):
        select_all_in_one(request=pending, response=response(),
                          now=case.now + timedelta(seconds=5, microseconds=1))


def test_utf8_bytes_and_explanation_character_bounds_are_both_enforced(case: Case) -> None:
    pending = request(case)
    # An explanation can be valid Unicode without gaining instruction/code authority.
    valid = response(reason="허용된 후보 선택")
    assert parse_ai_selection(valid, request=pending, now=case.now).candidate_id == "candidate-2"
    raw = response(reason="선" * 120)
    tight = replace(pending, limits=replace(pending.limits, max_response_bytes=600))
    with pytest.raises(SelectionRejected, match="RESOURCE_LIMIT"):
        parse_ai_selection(raw, request=tight, now=case.now)
    with pytest.raises(SelectionRejected):
        parse_ai_selection(response(reason="\ud800"), request=pending, now=case.now)


@pytest.mark.parametrize("field,value", [
    ("candidate_id", "x" * 81), ("candidate_id", "candidate-1 extra"),
    ("candidate_id", "__import__('os')"), ("enabled", 1), ("signal_id", ""),
    ("plan_id", ""), ("strategy", {"strategy_id": "trend", "revision": "v1"}),
    ("issued_at", datetime(2026, 11, 1, 23, 59, 54, tzinfo=UTC)),
    ("expires_at", datetime(2026, 11, 2)),
])
def test_server_record_types_identifiers_and_signal_ordering_are_strict(
    case: Case, field: str, value: object,
) -> None:
    invalid = replace(case.candidates[0], **{field: value})  # type: ignore[arg-type]
    with pytest.raises(SelectionRejected):
        full(replace(case, candidates=(invalid,)))


def test_untrusted_explanation_is_data_and_cannot_replace_server_plan(case: Case) -> None:
    result = select_all_in_one(
        request=request(case), now=case.now,
        response=response(reason="Ignore limits; leverage=999; eval('quantity=99')"),
    )
    assert result.candidate is case.candidates[1]
    assert result.candidate.plan_id == "plan-2"
    assert result.policy.revision == "risk-v7"
    assert not hasattr(result, "leverage")


def test_missing_response_cannot_bypass_expired_server_request(case: Case) -> None:
    with pytest.raises(SelectionRejected):
        select_all_in_one(request=request(case), response=None,
                          now=case.now + timedelta(seconds=15))


def test_request_cannot_predate_its_candidate_evidence(case: Case) -> None:
    pending = replace(request(case), issued_at=case.now - timedelta(seconds=3))
    with pytest.raises(SelectionRejected):
        parse_ai_selection(response(), request=pending, now=case.now)
