"""Owned executions and exact venue protection evidence contracts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from multi_exchange_trader.execution import protection as p

D = Decimal
NOW = datetime(2026, 11, 2, tzinfo=UTC)


def projection(side: p.Side = "BUY") -> p.ProtectionProjection:
    state = p.ProtectionProjection(p.ProtectionScope("acct", "DEMO"), "position", "BTC", side)
    return p.register_order(
        state, p.OwnedOrder("entry", "position", "BTC", side, "ENTRY", D("3"), False, 0, "policy")
    )


def fill(
    state: p.ProtectionProjection, execution_id: str = "fill", order_id: str = "entry",
    quantity: str = "1", side: p.Side = "BUY", **changes: Any,
) -> p.ProtectionProjection:
    event = p.FillEvent(
        state.scope, execution_id, order_id, "position", "BTC", side, D(quantity), NOW
    )
    return p.apply_fill(state, replace(event, **changes))


def protected_setup(
    quantity: str = "3", generation: int = 1,
) -> tuple[p.ProtectionProjection, p.ProtectionPolicy,
           tuple[p.ProtectionEvidence, ...], p.PositionEvidence]:
    state = fill(projection(), quantity=quantity)
    evidence = []
    intents: tuple[tuple[str, p.Role, str], ...] = (
        ("stop", "STOP", "90"), ("take", "TAKE_PROFIT", "120"),
    )
    for order_id, role, price in intents:
        order = p.OwnedOrder(
            order_id,
            "position",
            "BTC",
            "SELL",
            role,
            D(quantity),
            True,
            generation,
            "policy",
            D(price),
            "MARK_PRICE",
        )
        state = p.register_order(state, order)
        evidence.append(
            p.ProtectionEvidence(
                state.scope,
                order_id,
                "position",
                "BTC",
                "SELL",
                role,
                D(quantity),
                True,
                generation,
                "policy",
                D(price),
                "MARK_PRICE",
                "ACTIVE",
                NOW,
                "cycle",
            )
        )
    position = p.PositionEvidence(
        state.scope, "position", "BTC", "BUY", D(quantity), NOW, "cycle", True, False
    )
    policy = p.ProtectionPolicy("policy", generation, D("90"), D("120"), "MARK_PRICE")
    return state, policy, tuple(evidence), position


def assess(
    state: p.ProtectionProjection, policy: p.ProtectionPolicy,
    evidence: tuple[p.ProtectionEvidence, ...], position: p.PositionEvidence,
) -> p.ProtectionAssessment:
    return p.assess_protection(
        state, policy, evidence, position, now=NOW, max_age=timedelta(seconds=10)
    )


def test_three_partial_fills_duplicate_and_late_fill_after_cancel() -> None:
    state = fill(projection(), "f1")
    state = p.record_order_cancelled(state, "entry")
    state = fill(state, "f2")
    state = fill(state, "f3")
    duplicate = fill(state, "f2")
    assert duplicate == state
    assert state.owned_quantity == D("3")
    assert state.signed_quantity == D("3")
    assert len(state.executions) == 3
    assert not state.conflicts


def test_changed_duplicate_and_excess_entry_preserve_evidence_and_block() -> None:
    state = fill(projection(), "f1")
    changed = fill(state, "f1", quantity="2")
    assert changed.owned_quantity == D("1")
    assert changed.conflicts[-1].event is not None
    assert changed.conflicts[-1].event.quantity == D("2")
    assert changed.entries_blocked
    excess = fill(changed, "f2", quantity="3")
    assert excess.owned_quantity == D("1")
    assert excess.conflicts[-1].event is not None
    assert excess.conflicts[-1].event.execution_id == "f2"


@pytest.mark.parametrize(
    "changes",
    [
        {"client_order_id": "entry-looking-prefix"},
        {"scope": None},
        {"position_id": "external"},
        {"instrument_id": "ETH"},
        {"side": "SELL"},
    ],
)
def test_wrong_ownership_reports_never_change_owned_quantity(changes: dict[str, Any]) -> None:
    if changes.get("scope", 1) is None:
        changes = {"scope": p.ProtectionScope("other", "LIVE")}
    state = fill(projection(), **changes)
    assert state.owned_quantity == 0
    assert state.entries_blocked
    assert len(state.conflicts) == 1


def test_mapping_is_immutable_and_conflicting_reregistration_blocks() -> None:
    state = projection()
    assert p.register_order(state, state.orders[0]) == state
    conflict = p.register_order(state, replace(state.orders[0], quantity=D("4")))
    assert conflict.orders == state.orders
    assert conflict.entries_blocked
    assert conflict.conflicts[-1].order is not None
    assert conflict.conflicts[-1].order.quantity == D("4")


def test_partial_take_profit_then_stop_is_bounded_and_race_preserved() -> None:
    state, _, _, _ = protected_setup()
    state = fill(state, "take-fill", "take", "1", "SELL")
    state = fill(state, "stop-fill", "stop", "2", "SELL")
    assert state.owned_quantity == 0
    race = fill(state, "late-take", "take", "1", "SELL")
    assert race.owned_quantity == 0
    assert race.conflicts[-1].event is not None
    assert race.conflicts[-1].event.execution_id == "late-take"
    assert race.entries_blocked


def test_short_exposure_and_reduce_only_exits_cannot_flip() -> None:
    state = fill(projection("SELL"), quantity="2", side="SELL")
    assert state.signed_quantity == D("-2")
    state = p.register_order(
        state, p.OwnedOrder("exit", "position", "BTC", "BUY", "EXIT", D("2"), True, 1, "policy")
    )
    result = fill(state, "exit-fill", "exit", "3", "BUY")
    assert result.signed_quantity == D("-2")
    assert result.conflicts


def test_full_exact_stop_and_take_profit_required() -> None:
    state, policy, evidence, position = protected_setup()
    result = assess(state, policy, evidence, position)
    assert result.status == "OPEN_PROTECTED"
    assert result.unprotected_quantity == 0
    assert not result.entries_blocked
    absent = assess(state, policy, (), position)
    assert absent.status == "OPEN_UNPROTECTED"
    assert absent.unprotected_quantity == D("3")
    assert absent.entries_blocked


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": None},
        {"position_id": "external"},
        {"instrument_id": "ETH"},
        {"side": "BUY"},
        {"reduce_only": False},
        {"role": "TAKE_PROFIT"},
        {"quantity": D("2")},
        {"quantity": D("4")},
        {"generation": 0},
        {"policy_revision": "old"},
        {"trigger_price": D("89")},
        {"trigger_source": "LAST_PRICE"},
        {"state": "UNKNOWN"},
        {"state": "REJECTED"},
        {"observed_at": NOW - timedelta(seconds=11)},
        {"observed_at": NOW + timedelta(seconds=1)},
        {"cycle_id": "old-cycle"},
        {"client_order_id": "prefix-stop"},
    ],
)
def test_mismatched_stop_evidence_does_not_confirm_or_cancel(changes: dict[str, Any]) -> None:
    state, policy, evidence, position = protected_setup()
    if changes.get("scope", 1) is None:
        changes = {"scope": p.ProtectionScope("acct", "LIVE")}
    result = assess(state, policy, (replace(evidence[0], **changes), evidence[1]), position)
    assert result.status != "OPEN_PROTECTED"
    assert result.entries_blocked
    assert result.proposed_cancellations == ()


def test_partial_coverage_and_position_external_conflict() -> None:
    state, policy, evidence, position = protected_setup()
    result = assess(state, policy, (replace(evidence[0], quantity=D("1")), evidence[1]), position)
    assert result.unprotected_quantity == D("2")
    assert result.entries_blocked
    conflict = assess(state, policy, evidence, replace(position, has_external_activity=True))
    assert conflict.status == "ATTENTION"
    assert conflict.unprotected_quantity == D("3")


def test_pending_replacement_preserves_old_generation_until_both_new_confirmed() -> None:
    state, _, old_evidence, position = protected_setup(generation=1)
    new_evidence = []
    for old in old_evidence:
        new = replace(old, client_order_id=old.client_order_id + "2", generation=2)
        state = p.register_order(
            state,
            p.OwnedOrder(
                new.client_order_id,
                "position",
                "BTC",
                "SELL",
                new.role,
                D("3"),
                True,
                2,
                "policy",
                new.trigger_price,
                new.trigger_source,
            ),
        )
        new_evidence.append(new)
    policy = p.ProtectionPolicy("policy", 2, D("90"), D("120"), "MARK_PRICE")
    pending = assess(state, policy, old_evidence + tuple(new_evidence[:1]), position)
    assert pending.proposed_cancellations == ()
    assert pending.entries_blocked
    confirmed = assess(state, policy, old_evidence + tuple(new_evidence), position)
    assert confirmed.status == "OPEN_PROTECTED"
    assert confirmed.proposed_cancellations == ("stop", "take")


def test_contradictory_duplicate_protection_reports_cannot_mark_protected() -> None:
    state, policy, evidence, position = protected_setup()
    result = assess(state, policy, evidence + (replace(evidence[0], state="REJECTED"),), position)
    assert result.status == "ATTENTION"
    assert result.proposed_cancellations == ()


def test_local_flat_is_unverified_until_independent_complete_same_cycle_snapshot() -> None:
    state = projection()
    assert p.assess_flat(state, None, now=NOW, max_age=timedelta(seconds=10)) == "FLAT_UNVERIFIED"
    snapshot = p.FlatEvidence(
        state.scope, "position", "BTC", D("0"), (), NOW, NOW, "cycle", "cycle", True, True
    )
    assert p.assess_flat(state, snapshot, now=NOW, max_age=timedelta(seconds=10)) == "STOPPED_FLAT"
    for bad in [
        replace(snapshot, working_order_ids=("entry",)),
        replace(snapshot, orders_complete=False),
        replace(snapshot, orders_cycle_id="other"),
        replace(snapshot, position_quantity=D("1")),
        replace(snapshot, position_observed_at=NOW + timedelta(seconds=1)),
    ]:
        assert (
            p.assess_flat(state, bad, now=NOW, max_age=timedelta(seconds=10)) == "FLAT_UNVERIFIED"
        )


@pytest.mark.parametrize("quantity", [D("NaN"), D("Infinity"), D("-1"), D("0"), True])
def test_invalid_fill_value_rejected_before_projection_change(quantity: Any) -> None:
    with pytest.raises(ValueError):
        p.FillEvent(
            p.ProtectionScope("acct", "DEMO"), "f", "entry", "position", "BTC", "BUY", quantity, NOW
        )


def test_naive_time_and_implicit_environment_rejected() -> None:
    with pytest.raises(ValueError):
        p.ProtectionScope("acct", "MAINNET")
    with pytest.raises(ValueError):
        p.FillEvent(
            p.ProtectionScope("acct", "DEMO"),
            "f",
            "entry",
            "position",
            "BTC",
            "BUY",
            D("1"),
            datetime(2026, 11, 2),
        )


def test_snapshot_before_latest_execution_cannot_verify_current_exposure() -> None:
    state, policy, evidence, position = protected_setup()
    old = NOW - timedelta(seconds=1)
    result = assess(
        state,
        policy,
        tuple(replace(item, observed_at=old) for item in evidence),
        replace(position, observed_at=old),
    )
    assert result.status == "ATTENTION"
    assert result.unprotected_quantity == D("3")
    result = assess(
        state, policy, tuple(replace(item, observed_at=old) for item in evidence), position
    )
    assert result.status == "OPEN_UNPROTECTED"


def test_conflicting_current_generation_report_is_not_hidden_by_valid_sibling() -> None:
    state, policy, evidence, position = protected_setup()
    extra = replace(state.orders[1], client_order_id="take-extra", trigger_price=D("121"))
    state = p.register_order(state, extra)
    bad = replace(evidence[1], client_order_id="take-extra", trigger_price=D("121"))
    result = assess(state, policy, evidence + (bad,), position)
    assert result.status == "ATTENTION"
    assert result.entries_blocked
    assert result.proposed_cancellations == ()


def test_direct_projection_cannot_forge_ownership_by_duplicate_ids() -> None:
    state = projection()
    with pytest.raises(ValueError):
        replace(state, orders=state.orders + (state.orders[0],))
    with pytest.raises(ValueError):
        replace(
            state,
            executions=(
                p.FillEvent(
                    state.scope, "unregistered", "unknown", "position", "BTC", "BUY", D("1"), NOW
                ),
            ),
        )


def test_flat_evidence_predating_latest_fill_cannot_prove_stopped() -> None:
    state, _, _, _ = protected_setup()
    state = fill(state, "closed", "stop", "3", "SELL")
    snapshot = p.FlatEvidence(
        state.scope,
        "position",
        "BTC",
        D("0"),
        (),
        NOW - timedelta(seconds=1),
        NOW - timedelta(seconds=1),
        "cycle",
        "cycle",
        True,
        True,
    )
    assert (
        p.assess_flat(state, snapshot, now=NOW, max_age=timedelta(seconds=10)) == "FLAT_UNVERIFIED"
    )


def test_fractional_fill_precision_never_rounds_ownership_up_or_hides_overfill() -> None:
    state = fill(projection(), quantity="2.99999999999999999999999999999")
    assert state.owned_quantity == D("2.99999999999999999999999999999")
    state = p.register_order(
        state,
        p.OwnedOrder(
            "exit-too-large", "position", "BTC", "SELL", "EXIT", D("3"), True, 1, "policy"
        ),
    )
    assert all(order.client_order_id != "exit-too-large" for order in state.orders)
    assert state.conflicts[-1].reason == "UNSAFE_ORDER_INTENT"
    overfill = fill(state, "tiny-overfill", quantity="0.00000000000000000000000000002")
    assert overfill.owned_quantity == D("2.99999999999999999999999999999")
    assert overfill.conflicts[-1].event is not None
    assert overfill.conflicts[-1].event.execution_id == "tiny-overfill"


def test_scoped_records_reject_foreign_scope_types() -> None:
    state, _, evidence, position = protected_setup()
    invalid_scope: Any = "DEMO"
    for record in (state, state.executions[0], evidence[0], position):
        with pytest.raises(TypeError):
            replace(record, scope=invalid_scope)


@pytest.mark.parametrize("changes", [
    {"reduce_only": False}, {"side": "BUY"},
    {"scope": p.ProtectionScope("other", "DEMO")},
    {"scope": p.ProtectionScope("acct", "LIVE")},
    {"position_id": "other"}, {"instrument_id": "ETH"},
    {"role": "TAKE_PROFIT"}, {"trigger_price": D("89")},
    {"trigger_source": "LAST_PRICE"}, {"generation": 0},
    {"policy_revision": "other"}, {"quantity": D("4")},
])
def test_owned_active_sibling_contradiction_blocks_despite_exact_pair(changes: dict[str, Any]) -> None:
    state, _, old, position = protected_setup()
    current = tuple(replace(item, client_order_id=item.client_order_id + "2", generation=2)
                    for item in old)
    for order in state.orders[1:]:
        state = p.register_order(state, replace(order, client_order_id=order.client_order_id + "2",
                                               generation=2))
    state = p.register_order(state, replace(state.orders[-2], client_order_id="stop-extra"))
    bad = replace(current[0], client_order_id="stop-extra", **changes)
    reports = old + current + (bad,)
    policy = p.ProtectionPolicy("policy", 2, D("90"), D("120"), "MARK_PRICE")
    result = assess(state, policy, reports, position)
    assert result.status == "ATTENTION"
    assert result.entries_blocked
    assert result.proposed_cancellations == ()
    assert result.unprotected_quantity == 0  # Known good SL/TP coverage is retained.
    assert state.owned_quantity == D("3")
    assert state.orders[-1].client_order_id == "stop-extra"
    assert reports[-1] is bad  # Pure assessment preserves the caller's evidence.


@pytest.mark.parametrize("changes", [
    {"quantity": D("1")}, {"state": "UNKNOWN"}, {"state": "REJECTED"},
    {"side": "BUY", "observed_at": NOW - timedelta(seconds=11)},
    {"side": "BUY", "cycle_id": "old-cycle"},
])
def test_noncontradictory_sibling_does_not_erase_exact_coverage(changes: dict[str, Any]) -> None:
    state, policy, evidence, position = protected_setup()
    state = p.register_order(state, replace(state.orders[1], client_order_id="stop-extra"))
    report = replace(evidence[0], client_order_id="stop-extra", **changes)
    result = assess(state, policy, evidence + (report,), position)
    assert result.status == "OPEN_PROTECTED"
    assert result.unprotected_quantity == 0


def test_external_report_is_not_owned_from_shared_position_or_id_prefix() -> None:
    state, policy, evidence, position = protected_setup()
    external = replace(evidence[0], client_order_id="stop-external", reduce_only=False)
    assert assess(state, policy, evidence + (external,), position).status == "OPEN_PROTECTED"
    assert assess(state, policy, evidence + (external, replace(external, state="UNKNOWN")),
                  position).status == "OPEN_PROTECTED"
    assert assess(state, policy, evidence + (external,),
                  replace(position, has_external_activity=True)).status == "ATTENTION"


def test_partial_exit_readback_below_original_intent_remains_valid_coverage() -> None:
    state, policy, evidence, position = protected_setup()
    state = fill(state, "partial-exit", "take", "1", "SELL")
    reports = tuple(replace(item, quantity=D("2")) for item in evidence)
    result = assess(state, policy, reports, replace(position, quantity=D("2")))
    assert result.unprotected_quantity == 0
    assert result.status == "OPEN_UNPROTECTED"  # Exact immutable replacement still required.
    assert result.entries_blocked
    assert result.proposed_cancellations == ()


@pytest.mark.parametrize("value", [
    D("1E+999999999999999999"), D("1E-999999999999999999"),
    D("0E+999999999999999999"), D("0E-999999999999999999"),
    D("1" * 257), D("11E+1000"),
])
def test_protection_rejects_unbounded_decimal_at_every_domain_input(value: Decimal) -> None:
    state, policy, evidence, position = protected_setup()
    flat = p.FlatEvidence(state.scope, "position", "BTC", D("0"), (), NOW, NOW,
                          "cycle", "cycle", True, True)
    for record, field in (
        (state.orders[0], "quantity"), (state.orders[1], "trigger_price"),
        (state.executions[0], "quantity"), (policy, "stop_price"),
        (policy, "take_profit_price"), (evidence[0], "quantity"),
        (evidence[0], "trigger_price"), (position, "quantity"), (flat, "position_quantity"),
    ):
        invalid_field: dict[str, Any] = {field: value}
        with pytest.raises(ValueError):
            replace(record, **invalid_field)


@pytest.mark.parametrize("value", [D("1E+1000"), D("1E-1000"), D("1" * 256)])
def test_protection_accepts_decimal_representation_boundaries(value: Decimal) -> None:
    event = p.FillEvent(p.ProtectionScope("acct", "DEMO"), "f", "entry", "position", "BTC",
                        "BUY", value, NOW)
    assert event.quantity == value


def test_256_digit_fill_preserves_exact_ownership_at_boundary() -> None:
    value = "1." + "1" * 255
    assert fill(projection(), quantity=value).owned_quantity == D(value)
