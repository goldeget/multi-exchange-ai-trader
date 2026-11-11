"""Independent synthetic assessment predicates; never model or GET authority."""

import hashlib
import json
from dataclasses import MISSING, FrozenInstanceError, fields, replace
from datetime import timedelta
from decimal import Decimal as D
from decimal import Inexact, Rounded, localcontext
from fractions import Fraction
from typing import Any, Literal, cast

import pytest
from risk_v2_support import (
    DIGEST,
    EXPIRY,
    NOW,
    costs_v2,
    instrument_v2,
    observation_ref_v2,
    policy_v2,
    proposal_v2,
)

from multi_exchange_trader.execution import linear_risk_evidence_v2 as e
from multi_exchange_trader.execution import linear_risk_v2 as r
from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.risk_v2_math import finite_decimal_v2


def _inputs(side: Literal["LONG", "SHORT"] = "LONG") -> e.LinearRiskInputsV2:
    policy = policy_v2()
    instrument = instrument_v2()
    proposal = proposal_v2()
    costs = costs_v2()
    if side == "SHORT":
        proposal = replace(
            proposal, side="SHORT", stop_price=D(110), entry=replace(proposal.entry, side="SELL")
        )
        costs = replace(costs, side="SHORT", stop_price=D(110))
    sized = r.size_linear_entry_v2(
        policy=policy,
        proposal=proposal,
        instrument=instrument,
        catalog_revision="FIXTURE_CATALOG_V1",
        costs=costs,
        now=NOW,
    )
    account = c.LinearAccountRiskSnapshotV2(
        policy.scope.product_scope.account,
        observation_ref_v2("ACCOUNT"),
        "COVERAGE_V1",
        (),
        "UTC_REALIZED_NET_FEES_FUNDING",
        NOW.date(),
        D(7),
        D(500),
        D(2),
        D(400),
        1,
        D(0),
        D(0),
        0,
        "ISOLATED",
        "ONE_WAY",
        (),
    )
    reservations = c.LinearReservationSnapshotV2(
        account.account,
        "RESERVATION",
        "OWNER_V1",
        1,
        DIGEST,
        NOW,
        None,
        (),
        D(3),
        D(5),
        1,
    )
    tiers = e.LinearTierSnapshotV2(
        instrument.key,
        observation_ref_v2("TIERS"),
        instrument.specification_revision,
        (e.LinearRiskTierV2("TIER", D(0), D(1000), D(1), D(100), D(".1"), D(".05"), D(0)),),
    )
    minimum = e.MinimumAmountAssessmentV2(
        policy.scope,
        c.RiskComputationRefV2("MINIMUM", "MINIMUM_V1", "FIXTURE", NOW, "b" * 64),
        c.envelope_sha256_v2(sized.envelope),
        instrument.specification_revision,
        "MINIMUM_V1",
        ("INSTRUMENT", "LTP"),
        D(95),
        D(5),
        D(95),
        EXPIRY,
    )
    last_trade_quote = e.LinearLastTradeQuoteV2(
        instrument.key, observation_ref_v2("LTP"), D(95)
    )
    requirements = e.RiskRequirementsV2(
        "MAPPING_V1",
        "MINIMUM_V1",
        "FIXTURE_COST_V1",
        "FIXTURE_FEE_V1",
        "FIXTURE_FUNDING_V1",
        c.STOP_GAP_MODEL_REVISION_V2,
        "LINEAR_MARK_GROSS_UPPER_BOUND_V1",
        "LOCAL_ZERO_ORIGIN_LATTICE_V1",
        "MARGIN_V1",
        "LIQUIDATION_V1",
        "POINT_ESTIMATE_WITH_RECONCILIATION_V1",
        "POINT_ESTIMATE_TWO_IM_ENVELOPE_V1",
        "APP_RESERVE_CEILING_QUANTUM_V1",
        D("0.00000001"),
    )
    return e.LinearRiskInputsV2(
        policy,
        proposal,
        instrument,
        "FIXTURE_CATALOG_V1",
        costs,
        account,
        reservations,
        (c.LinearMarkQuoteV2(instrument.key, observation_ref_v2("MARK"), D(140)),),
        tiers,
        DIGEST,
        minimum,
        requirements,
        last_trade_quote,
    )


def _policy(value: e.LinearRiskInputsV2, **changes: Any) -> e.LinearRiskInputsV2:
    policy = replace(value.policy, **changes)
    costs = replace(value.costs, policy_sha256=c.risk_policy_sha256_v2(policy))
    sized = r.size_linear_entry_v2(
        policy=policy,
        proposal=value.proposal,
        instrument=value.instrument,
        catalog_revision=value.catalog_revision,
        costs=costs,
        now=NOW,
    )
    return replace(
        value,
        policy=policy,
        costs=costs,
        minimum_amount=replace(
            value.minimum_amount, envelope_sha256=c.envelope_sha256_v2(sized.envelope)
        ),
    )


def _ratio(numerator: int = 80, denominator: int = 1) -> e.ExactRiskRatioV2:
    number = Fraction(numerator, denominator)
    return e.ExactRiskRatioV2(
        finite_decimal_v2(Fraction(number.numerator)),
        finite_decimal_v2(Fraction(number.denominator)),
    )


def _positive(numerator: int = 80, denominator: int = 1) -> e.LiquidationPositiveRootV2:
    return e.LiquidationPositiveRootV2(
        "POSITIVE_ROOT",
        _ratio(numerator, denominator),
        "TIER",
        D(1000),
        "c" * 64,
        "d" * 64,
        ("TIER",),
        "LP_RULE_V1",
    )


def _margin(value: e.LinearRiskInputsV2) -> e.LinearMarginAssessmentV2:
    preparation = r.prepare_linear_entry_v2(value, now=NOW)
    digest = e.binding_sha256_v2(preparation.binding)
    return e.LinearMarginAssessmentV2(
        digest,
        c.RiskComputationRefV2("MARGIN", "MARGIN_V1", "FIXTURE", NOW, digest),
        "MARGIN_V1",
        D(1),
        D(2),
        "LOCAL_ZERO_ORIGIN_LATTICE_V1",
        "SMALLEST_FEASIBLE_PERMITTED_GRID_V1",
        D(55),
        D(60),
        ("TIER",),
        EXPIRY,
        "POINT_ESTIMATE_TWO_IM_ENVELOPE_V1",
        "APP_RESERVE_CEILING_QUANTUM_V1",
        D("0.00000001"),
        "e" * 64,
        "f" * 64,
    )


def _liquidation(
    value: e.LinearRiskInputsV2,
    margin: e.LinearMarginAssessmentV2,
    outcome: e.LiquidationPositiveRootV2 | e.LiquidationNoPositiveRootV2 | None = None,
) -> e.LinearLiquidationAssessmentV2:
    digest = e.margin_assessment_sha256_v2(margin)
    return e.LinearLiquidationAssessmentV2(
        margin.binding_sha256,
        digest,
        c.RiskComputationRefV2("LIQUIDATION", "LIQUIDATION_V1", "FIXTURE", NOW, digest),
        "LIQUIDATION_V1",
        margin.quantity,
        margin.leverage,
        value.proposal.side,
        value.proposal.entry.limit_price,
        value.policy.margin_mode,
        value.policy.position_mode,
        margin.covered_tier_ids,
        outcome or _positive(120 if value.proposal.side == "SHORT" else 80),
        EXPIRY,
        margin.model_input_sha256,
        margin.exact_calculation_sha256,
    )


def _readback(
    value: e.LinearRiskInputsV2, margin: e.LinearMarginAssessmentV2
) -> e.LinearLeverageReadbackV2:
    reference = replace(observation_ref_v2("READBACK"), server_observed_at=NOW)
    account = value.account.reference
    reference = replace(
        reference, server_observed_at=account.server_observed_at, received_at=account.received_at
    )
    return e.LinearLeverageReadbackV2(
        margin.binding_sha256,
        reference,
        account.observation_id,
        "CONTROL_ENTRY_1",
        account.server_observed_at,
        value.proposal.side,
        margin.leverage,
        value.policy.margin_mode,
        value.policy.position_mode,
    )


def _assess(value: e.LinearRiskInputsV2 | None = None, **changes: Any) -> e.LinearEntryAssessmentV2:
    value = value or _inputs()
    margin = _margin(value)
    arguments: dict[str, Any] = {
        "now": NOW,
        "stage": "SUBMIT",
        "margin": margin,
        "liquidation": _liquidation(value, margin),
        "leverage_readback": _readback(value, margin),
    }
    arguments.update(changes)
    return r.assess_entry_v2(value, **arguments)


def _reject(code: str, value: e.LinearRiskInputsV2 | None = None, **changes: Any) -> None:
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_" + code + "$"):
        _assess(value, **changes)


def _forged(value: Any, **changes: Any) -> Any:
    result = object.__new__(type(value))
    for field in fields(value):
        object.__setattr__(result, field.name, changes.get(field.name, getattr(value, field.name)))
    return result


def test_literal_reserve_sixty_and_sixty_one_capacity() -> None:
    result = _assess()
    assert result.preparation.capacity_quote == D(60)
    assert result.required_reservation_quote == D(60)  # max(55+2,60), not 62
    assert result.liquidation_buffer_result == "GAP_SATISFIED"
    assert result.blockers == ()
    value = _inputs()
    margin = replace(_margin(value), venue_required_total_reserve_quote=D(61))
    _reject("MARGIN_LIMIT", value, margin=margin, liquidation=_liquidation(value, margin))


def test_literal_initial_plus_cost_drives_reserve_without_final_rounding() -> None:
    value = _policy(_inputs(), per_trade_loss=D("12.000000001"))
    value = replace(value, costs=replace(value.costs, fee_quote_per_base=D("1.000000001")))
    margin = replace(
        _margin(value), initial_margin_quote=D(58), venue_required_total_reserve_quote=D(1)
    )
    _reject("MARGIN_LIMIT", value, margin=margin, liquidation=_liquidation(value, margin))
    value = _policy(value, margin_allocation=D("60.000000001"))
    margin = replace(
        _margin(value), initial_margin_quote=D(58), venue_required_total_reserve_quote=D(1)
    )
    result = _assess(value, margin=margin, liquidation=_liquidation(value, margin))
    assert result.required_reservation_quote is not None
    assert result.required_reservation_quote.as_tuple() == D("60.000000001").as_tuple()


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_exact_buffer_equality_and_positive_gap(side: Literal["LONG", "SHORT"]) -> None:
    value = _inputs(side)
    margin = _margin(value)
    equality_price = 89 if side == "LONG" else 111
    result = _assess(value, liquidation=_liquidation(value, margin, _positive(equality_price)))
    assert result.liquidation_buffer_result == "GAP_SATISFIED"
    failing = _positive(891, 10) if side == "LONG" else _positive(1109, 10)
    _reject("LIQUIDATION_BUFFER", value, liquidation=_liquidation(value, margin, failing))
    zero = _positive(90 if side == "LONG" else 110)
    value = _policy(value, liquidation_buffer_fraction=D(0))
    margin = _margin(value)
    _reject("LIQUIDATION_BUFFER", value, liquidation=_liquidation(value, margin, zero))


def test_nonterminating_root_and_non_tick_root_use_exact_ratio() -> None:
    value = _inputs()
    margin = _margin(value)
    for root in (_positive(800, 9), _positive(88001, 1000)):
        assert (
            _assess(value, liquidation=_liquidation(value, margin, root)).liquidation_buffer_result
            == "GAP_SATISFIED"
        )
    _reject("LIQUIDATION_BUFFER", value, liquidation=_liquidation(value, margin, _positive(269, 3)))


def test_long_l1_no_positive_root_is_explicit_and_bound() -> None:
    value = _inputs()
    margin = replace(_margin(value), leverage=D(1))
    outcome = e.LiquidationNoPositiveRootV2(
        "NO_POSITIVE_ROOT",
        "NON_POSITIVE_LONG_INTERCEPT",
        D(1000),
        "c" * 64,
        "d" * 64,
        ("TIER",),
        "LP_RULE_V1",
        margin.model_input_sha256,
        "1" * 64,
    )
    result = _assess(
        value,
        margin=margin,
        liquidation=_liquidation(value, margin, outcome),
        leverage_readback=_readback(value, margin),
    )
    assert result.liquidation_buffer_result == "MODEL_NO_POSITIVE_TRIGGER"
    assert not hasattr(result.liquidation, "liquidation_price")


def test_plan_missing_order_and_all_present_planning_only() -> None:
    result = _assess(stage="PLAN", margin=None, liquidation=None, leverage_readback=None)
    assert result.blockers == (
        "RISK_V2_MARGIN_UNVERIFIED",
        "RISK_V2_LIQUIDATION_UNVERIFIED",
        "RISK_V2_LEVERAGE_READBACK_UNVERIFIED",
    )
    assert result.required_reservation_quote is None
    assert result.liquidation_buffer_result is None
    assert _assess(stage="PLAN").blockers == ("RISK_V2_PLANNING_ONLY",)
    partial = _assess(stage="PLAN", liquidation=None, leverage_readback=None)
    assert partial.blockers == (
        "RISK_V2_LIQUIDATION_UNVERIFIED",
        "RISK_V2_LEVERAGE_READBACK_UNVERIFIED",
    )
    assert partial.required_reservation_quote == D(60)


@pytest.mark.parametrize(
    "field,code",
    [
        ("margin", "MARGIN_UNVERIFIED"),
        ("liquidation", "LIQUIDATION_UNVERIFIED"),
        ("leverage_readback", "LEVERAGE_READBACK_UNVERIFIED"),
    ],
)
def test_submit_missing_fails(field: str, code: str) -> None:
    changes: dict[str, Any] = {field: None}
    if field == "margin":
        changes.update(liquidation=None, leverage_readback=None)
    _reject(code, **changes)


def test_plan_does_not_discard_supplied_dependent_evidence_or_failed_buffer() -> None:
    _reject("INPUT_INVALID", stage="PLAN", margin=None)
    value = _inputs()
    margin = _margin(value)
    _reject(
        "LIQUIDATION_BUFFER",
        value,
        stage="PLAN",
        liquidation=_liquidation(value, margin, _positive(90)),
    )


@pytest.mark.parametrize("stage", [None, "plan", "LIVE", True, 1])
def test_stage_is_exact_closed_literal(stage: Any) -> None:
    _reject("INPUT_INVALID", stage=stage)


def test_no_authority_flags_and_required_frozen_fields() -> None:
    result = _assess()
    for value in (
        result,
        result.margin,
        result.liquidation,
        result.leverage_readback,
        _ratio(),
        _positive(),
        e.LiquidationNoPositiveRootV2(
            "NO_POSITIVE_ROOT",
            "NON_POSITIVE_LONG_INTERCEPT",
            D(1000),
            "c" * 64,
            "d" * 64,
            ("TIER",),
            "LP_RULE_V1",
            "e" * 64,
            "1" * 64,
        ),
    ):
        assert value is not None
        assert all(
            field.default is MISSING and field.default_factory is MISSING for field in fields(value)
        )
        assert not hasattr(value, "submission_ready")
        assert not hasattr(value, "qualified")
    with pytest.raises(FrozenInstanceError):
        result.stage = "PLAN"  # type: ignore[misc]


@pytest.mark.parametrize(
    "numerator,denominator",
    [
        (D("100.0"), D(1)),
        (D("1E+2"), D(1)),
        (D("-0"), D(1)),
        (D(0), D(2)),
        (D(2), D(4)),
        (D(-1), D(1)),
        (D(1), D(0)),
        (D(1), D("1.0")),
        (D(1), D(-1)),
        (D("NaN"), D(1)),
        (1, D(1)),
    ],
)
def test_ratio_rejects_noncanonical_or_invalid_values(numerator: Any, denominator: Any) -> None:
    with pytest.raises(c.RiskV2Rejected):
        e.ExactRiskRatioV2(numerator, denominator)


def test_ratio_zero_is_canonical_but_not_positive_price() -> None:
    assert _ratio(0).numerator.as_tuple() == (0, (0,), 0)
    with pytest.raises(c.RiskV2Rejected):
        replace(_positive(), exact_price=_ratio(0))


@pytest.mark.parametrize(
    "field,change,code",
    [
        ("binding_sha256", "1" * 64, "MARGIN_UNVERIFIED"),
        ("quantity", D(2), "MARGIN_UNVERIFIED"),
        ("leverage", D("2.01"), "LEVERAGE_UNSATISFIABLE"),
        ("leverage", D("100.1"), "LEVERAGE_UNSATISFIABLE"),
        ("covered_tier_ids", ("OTHER",), "MARGIN_UNVERIFIED"),
        ("model_revision", "OTHER_MODEL", "MARGIN_UNVERIFIED"),
    ],
)
def test_margin_mismatch_is_rejected(field: str, change: Any, code: str) -> None:
    value = _inputs()
    margin = _forged(_margin(value), **{field: change})
    if field == "binding_sha256":
        margin = _forged(margin, reference=replace(margin.reference, input_sha256=change))
    if field == "model_revision":
        margin = _forged(margin, reference=replace(margin.reference, model_revision=change))
    _reject(code, value, margin=margin, liquidation=None, leverage_readback=None, stage="PLAN")


def test_readback_wellformed_alternative_control_key_positive_limitation() -> None:
    value = _inputs()
    margin = _margin(value)
    readback = replace(_readback(value, margin), control_command_key="OTHER_VALID_CONTROL")
    assert _assess(value, leverage_readback=readback).blockers == ()


def test_fresh_audit_does_not_invalidate_same_economic_inputs() -> None:
    first = _assess()
    second = _assess(now=NOW + timedelta(microseconds=1))
    assert second.assessed_at > first.assessed_at
    assert e.binding_sha256_v2(second.preparation.binding) == e.binding_sha256_v2(
        first.preparation.binding
    )
    assert second.required_reservation_quote == first.required_reservation_quote


def test_old_binding_from_changed_source_is_rejected() -> None:
    value = _inputs()
    margin = _margin(value)
    changed = replace(value, marks=(replace(value.marks[0], mark_price=D(141)),))
    _reject(
        "MARGIN_UNVERIFIED",
        changed,
        margin=margin,
        liquidation=None,
        leverage_readback=None,
        stage="PLAN",
    )


@pytest.mark.parametrize(
    "field",
    [
        "quantity",
        "leverage",
        "side",
        "entry_limit_price",
        "margin_mode",
        "binding_sha256",
        "margin_assessment_sha256",
        "covered_tier_ids",
        "model_revision",
        "model_input_sha256",
        "exact_calculation_sha256",
    ],
)
def test_liquidation_mismatches_do_not_pass_plan(field: str) -> None:
    value = _inputs()
    margin = _margin(value)
    liquidation = _liquidation(value, margin)
    changes: dict[str, Any] = {
        "quantity": D(2),
        "leverage": D(3),
        "side": "SHORT",
        "entry_limit_price": D(101),
        "margin_mode": "CROSS",
        "binding_sha256": "1" * 64,
        "margin_assessment_sha256": "2" * 64,
        "covered_tier_ids": ("OTHER",),
        "model_revision": "OTHER_MODEL",
        "model_input_sha256": "3" * 64,
        "exact_calculation_sha256": "4" * 64,
    }
    change = changes[field]
    liquidation = _forged(liquidation, **{field: change})
    if field in ("margin_assessment_sha256", "model_revision"):
        liquidation = _forged(
            liquidation,
            reference=replace(
                liquidation.reference,
                **{
                    "input_sha256"
                    if field == "margin_assessment_sha256"
                    else "model_revision": change
                },
            ),
        )
    if field == "covered_tier_ids":
        liquidation = _forged(
            liquidation, outcome=replace(liquidation.outcome, consumed_tier_ids=("TIER", "OTHER"))
        )
    _reject(
        "LIQUIDATION_UNVERIFIED",
        value,
        stage="PLAN",
        liquidation=liquidation,
        leverage_readback=None,
    )


@pytest.mark.parametrize(
    "field,change",
    [
        ("binding_sha256", "1" * 64),
        ("account_observation_id", "OTHER_ACCOUNT"),
        ("side", "SHORT"),
        ("applied_leverage", D(3)),
        ("margin_mode", "CROSS"),
    ],
)
def test_readback_mismatch_rejected(field: str, change: Any) -> None:
    value = _inputs()
    readback = replace(_readback(value, _margin(value)), **{field: change})
    _reject("LEVERAGE_READBACK_UNVERIFIED", value, leverage_readback=readback)


@pytest.mark.parametrize("which", ["margin", "liquidation"])
@pytest.mark.parametrize(
    "kind,code",
    [
        ("future", "FUTURE_EVIDENCE"),
        ("stale", "STALE_EVIDENCE"),
        ("expired", "EXPIRED"),
        ("before_input", "CAUSAL_ORDER_INVALID"),
    ],
)
def test_model_times_are_fresh_and_causal(which: str, kind: str, code: str) -> None:
    value = _inputs()
    margin = _margin(value)
    liquidation = _liquidation(value, margin)
    record = margin if which == "margin" else liquidation
    moment = {
        "future": NOW + timedelta(microseconds=1),
        "stale": NOW - timedelta(seconds=11),
        "before_input": NOW - timedelta(microseconds=1),
        "expired": NOW,
    }[kind]
    record = _forged(record, reference=replace(record.reference, computed_at=moment))
    now = NOW
    if kind == "expired":
        record = _forged(record, valid_until=NOW + timedelta(microseconds=1))
        now += timedelta(microseconds=1)
    if which == "margin":
        margin = cast(e.LinearMarginAssessmentV2, record)
        liquidation = _forged(
            liquidation,
            margin_assessment_sha256=e.margin_assessment_sha256_v2(margin),
            reference=replace(
                liquidation.reference, input_sha256=e.margin_assessment_sha256_v2(margin)
            ),
        )
    else:
        liquidation = cast(e.LinearLiquidationAssessmentV2, record)
    _reject(code, value, margin=margin, liquidation=liquidation, now=now)


@pytest.mark.parametrize(
    "owner", ["account", "reservations", "marks", "tiers", "costs", "minimum_amount", "instrument"]
)
def test_every_visible_margin_predecessor_is_enforced_and_equality_allowed(owner: str) -> None:
    value = _inputs()
    old = NOW - timedelta(seconds=1)
    instrument = replace(value.instrument, source=replace(value.instrument.source, received_at=old))
    value = replace(
        value,
        instrument=instrument,
        account=replace(value.account, reference=replace(value.account.reference, received_at=old)),
        reservations=replace(value.reservations, observed_at=old),
        marks=(
            replace(value.marks[0], reference=replace(value.marks[0].reference, received_at=old)),
        ),
        tiers=replace(value.tiers, reference=replace(value.tiers.reference, received_at=old)),
        costs=replace(
            value.costs,
            reference=replace(value.costs.reference, computed_at=old),
            fee_reference=replace(value.costs.fee_reference, received_at=old),
            funding_reference=replace(value.costs.funding_reference, received_at=old),
        ),
        minimum_amount=replace(
            value.minimum_amount, reference=replace(value.minimum_amount.reference, computed_at=old)
        ),
    )
    # Rebind the independently altered exact instrument receipt.
    sized = r.size_linear_entry_v2(
        policy=value.policy,
        proposal=value.proposal,
        instrument=value.instrument,
        catalog_revision=value.catalog_revision,
        costs=value.costs,
        now=NOW,
    )
    value = replace(
        value,
        minimum_amount=replace(
            value.minimum_amount, envelope_sha256=c.envelope_sha256_v2(sized.envelope)
        ),
    )
    if owner in ("account", "tiers"):
        record = getattr(value, owner)
        value = replace(
            value, **{owner: replace(record, reference=replace(record.reference, received_at=NOW))}
        )
    elif owner == "marks":
        value = replace(
            value,
            marks=(
                replace(
                    value.marks[0], reference=replace(value.marks[0].reference, received_at=NOW)
                ),
            ),
        )
    elif owner == "reservations":
        value = replace(value, reservations=replace(value.reservations, observed_at=NOW))
    elif owner in ("costs", "minimum_amount"):
        record = getattr(value, owner)
        value = replace(
            value, **{owner: replace(record, reference=replace(record.reference, computed_at=NOW))}
        )
    else:
        # This predecessor also forces cost/minimum completion forward; its
        # independent closure is covered by the original Task5 source DAG tests.
        value = _inputs()
    margin = _margin(value)
    assert _assess(value, margin=margin).blockers == ()  # completion equality
    margin = replace(
        margin, reference=replace(margin.reference, computed_at=NOW - timedelta(microseconds=1))
    )
    _reject(
        "CAUSAL_ORDER_INVALID",
        value,
        margin=margin,
        liquidation=None,
        leverage_readback=None,
        stage="PLAN",
    )


@pytest.mark.parametrize(
    "kind", ["after_control", "after_account_server", "after_account_received"]
)
def test_readback_logical_order_reversals(kind: str) -> None:
    value = _inputs()
    readback = _readback(value, _margin(value))
    if kind == "after_control":
        readback = _forged(readback, after_control_at=NOW)
    elif kind == "after_account_server":
        readback = replace(readback, reference=replace(readback.reference, server_observed_at=NOW))
    else:
        readback = replace(
            readback,
            reference=replace(readback.reference, received_at=NOW + timedelta(microseconds=1)),
        )
    _reject(
        "CAUSAL_ORDER_INVALID" if kind != "after_account_received" else "FUTURE_EVIDENCE",
        value,
        leverage_readback=readback,
    )


def test_ratio_and_reserve_derived_resources_not_source256() -> None:
    huge = D((0, (9,) * 5001, 0))
    ratio = e.ExactRiskRatioV2(huge, D(1))
    assert ratio.numerator.as_tuple() == huge.as_tuple()
    value = _inputs()
    margin = replace(_margin(value), initial_margin_quote=huge)
    assert len(e.margin_assessment_sha256_v2(margin)) == 64
    _reject(
        "MARGIN_LIMIT", value, margin=margin, liquidation=None, leverage_readback=None, stage="PLAN"
    )
    assert len(e.margin_assessment_sha256_v2(replace(_margin(value), leverage=huge))) == 64
    assert replace(_liquidation(value, _margin(value)), leverage=huge).leverage == huge
    _reject(
        "LEVERAGE_UNSATISFIABLE",
        value,
        margin=replace(_margin(value), leverage=huge),
        liquidation=None,
        leverage_readback=None,
        stage="PLAN",
    )
    with pytest.raises(c.RiskV2Rejected, match="RESOURCE_LIMIT"):
        replace(_readback(value, _margin(value)), applied_leverage=huge)


def test_model_hash_identity_and_no_root_binding_are_mandatory() -> None:
    value = _inputs()
    margin = _margin(value)
    outcome = e.LiquidationNoPositiveRootV2(
        "NO_POSITIVE_ROOT",
        "NON_POSITIVE_LONG_INTERCEPT",
        D(1000),
        "c" * 64,
        "d" * 64,
        ("TIER",),
        "LP_RULE_V1",
        "1" * 64,
        "2" * 64,
    )
    with pytest.raises(c.RiskV2Rejected, match="LIQUIDATION_UNVERIFIED"):
        _liquidation(value, margin, outcome)
    outcome = replace(outcome, model_input_sha256=margin.model_input_sha256)
    liquidation = _liquidation(value, margin, outcome)
    invalid_modes: tuple[dict[str, Any], ...] = (
        {"side": "SHORT"},
        {"margin_mode": "CROSS"},
        {"position_mode": "HEDGE"},
    )
    for changes in invalid_modes:
        with pytest.raises(c.RiskV2Rejected):
            replace(liquidation, **changes)


@pytest.mark.parametrize(
    "change",
    [
        {"consumed_tier_ids": ()},
        {"consumed_tier_ids": ("TIER", "TIER")},
        {"consumed_tier_ids": ["TIER"]},
        {"candidate_tier_id": "ABSENT"},
        {"full_table_sha256": "BAD"},
        {"raw_manifest_sha256": "BAD"},
        {"domain_upper_quote": D(0)},
        {"kind": "UNSUPPORTED"},
    ],
)
def test_outcome_closed_structure(change: dict[str, Any]) -> None:
    with pytest.raises(c.RiskV2Rejected):
        replace(_positive(), **change)


def test_consumed_range_identity_is_not_lexically_sorted() -> None:
    value = _inputs()
    margin = _margin(value)
    root = replace(_positive(), consumed_tier_ids=("Z", "TIER", "A"))
    result = _assess(value, liquidation=_liquidation(value, margin, root))
    assert result.liquidation is not None
    assert result.liquidation.outcome.consumed_tier_ids == ("Z", "TIER", "A")
    with pytest.raises(c.RiskV2Rejected, match="RESOURCE_LIMIT"):
        replace(root, consumed_tier_ids=tuple("T" + str(i) for i in range(1025)))


def test_every_field_and_nested_record_revalidated() -> None:
    value = _inputs()
    margin = _margin(value)
    liquidation = _liquidation(value, margin)
    readback = _readback(value, margin)
    records = [
        (margin, e.margin_assessment_sha256_v2),
        (liquidation, e._liquidation_payload_v2),
        (readback, e._readback_payload_v2),
        (_positive(), e._positive_root_payload_v2),
        (_ratio(), e._ratio_payload_v2),
        (_assess(value), e._assessment_payload_v2),
    ]
    no_root = e.LiquidationNoPositiveRootV2(
        "NO_POSITIVE_ROOT",
        "NON_POSITIVE_LONG_INTERCEPT",
        D(1000),
        "c" * 64,
        "d" * 64,
        ("TIER",),
        "LP_RULE_V1",
        "e" * 64,
        "1" * 64,
    )
    records.append((no_root, e._no_positive_root_payload_v2))
    for original, consumer in records:
        for field in fields(cast(Any, original)):
            missing = _forged(original)
            object.__delattr__(missing, field.name)
            with pytest.raises(c.RiskV2Rejected, match="INPUT_INVALID"):
                consumer(missing)

        class Subtype(type(original)):  # type: ignore[misc]
            pass

        with pytest.raises(c.RiskV2Rejected, match="INPUT_INVALID"):
            consumer(object.__new__(Subtype))
    bad_root = _forged(_positive(), exact_price=_forged(_ratio(), numerator=object()))
    _reject("INPUT_INVALID", value, liquidation=_forged(liquidation, outcome=bad_root))
    _reject(
        "INPUT_INVALID",
        value,
        margin=None,
        stage="PLAN",
        liquidation=_forged(liquidation, reference=object()),
    )


def test_quantum_and_reference_relations_are_not_numerically_relaxed() -> None:
    value = _inputs()
    margin = _margin(value)
    for change in (
        {"reserve_quantum_usdt": D("0.000000010")},
        {"reference": replace(margin.reference, input_sha256="1" * 64)},
        {"reference": replace(margin.reference, model_revision="OTHER")},
    ):
        with pytest.raises(c.RiskV2Rejected):
            replace(margin, **change)
    _reject(
        "SOURCE_MISMATCH",
        value,
        margin=replace(margin, reference=replace(margin.reference, target_environment="LIVE")),
        liquidation=None,
        leverage_readback=None,
        stage="PLAN",
    )


def test_hostile_ambient_decimal_context_does_not_change_predicates_or_hash() -> None:
    value = _inputs()
    baseline = _assess(value)
    margin = _margin(value)
    digest = e.margin_assessment_sha256_v2(margin)
    with localcontext() as context:
        context.prec = 1
        context.Emin, context.Emax, context.capitals = -1, 1, 0
        context.traps[Inexact] = context.traps[Rounded] = True
        before = (
            context.prec,
            context.Emin,
            context.Emax,
            context.capitals,
            dict(context.traps),
            dict(context.flags),
        )
        result = _assess(value)
        assert result.required_reservation_quote == baseline.required_reservation_quote
        assert e.margin_assessment_sha256_v2(margin) == digest
        assert (
            context.prec,
            context.Emin,
            context.Emax,
            context.capitals,
            dict(context.traps),
            dict(context.flags),
        ) == before


@pytest.mark.parametrize("field", ["initial_margin_quote", "venue_required_total_reserve_quote"])
def test_margin_output_must_be_exact_multiple_of_q(field: str) -> None:
    change: dict[str, Any] = {field: D("55.000000001")}
    with pytest.raises(c.RiskV2Rejected, match="INPUT_INVALID"):
        replace(_margin(_inputs()), **change)


def test_margin_hash_independent_complete_canonical_literal() -> None:
    margin = _margin(_inputs())
    payload = {
        "binding_sha256": margin.binding_sha256,
        "reference": {
            "calculation_id": "MARGIN",
            "model_revision": "MARGIN_V1",
            "target_environment": "FIXTURE",
            "computed_at": "2026-11-03T00:00:00.000000+00:00",
            "input_sha256": margin.binding_sha256,
        },
        "model_revision": "MARGIN_V1",
        "quantity": "1",
        "leverage": "2",
        "leverage_grid_rule": "LOCAL_ZERO_ORIGIN_LATTICE_V1",
        "selection_rule": "SMALLEST_FEASIBLE_PERMITTED_GRID_V1",
        "initial_margin_quote": "55",
        "venue_required_total_reserve_quote": "60",
        "covered_tier_ids": ["TIER"],
        "valid_until": "2026-11-03T00:00:01.000000+00:00",
        "reserve_basis": "POINT_ESTIMATE_TWO_IM_ENVELOPE_V1",
        "reserve_rounding_revision": "APP_RESERVE_CEILING_QUANTUM_V1",
        "reserve_quantum_usdt": "1E-8",
        "model_input_sha256": "e" * 64,
        "exact_calculation_sha256": "f" * 64,
    }
    encoded = json.dumps(
        {
            "schema": "RISK_V2_CANONICAL_V1",
            "record_type": "LinearMarginAssessmentV2",
            "fields": payload,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    assert e.margin_assessment_sha256_v2(margin) == hashlib.sha256(encoded).hexdigest()
    assert e.margin_assessment_sha256_v2(
        replace(margin, initial_margin_quote=D("55.0"))
    ) != e.margin_assessment_sha256_v2(margin)
    assert e.margin_assessment_sha256_v2(
        replace(margin, model_input_sha256="1" * 64)
    ) != e.margin_assessment_sha256_v2(margin)
    assert e.margin_assessment_sha256_v2(
        replace(margin, exact_calculation_sha256="2" * 64)
    ) != e.margin_assessment_sha256_v2(margin)


def test_preserved_original_economic_hash_literals() -> None:
    value = _inputs()
    sized = r.prepare_linear_entry_v2(value, now=NOW).sizing
    assert (
        c.risk_policy_sha256_v2(value.policy)
        == "db628ce1ddc8c09851b09623bf76004dcc558200235e9144357e77b538e222d2"
    )
    assert (
        c.cost_allowance_sha256_v2(value.costs)
        == "ba749ac1e526ab07effb02c5e9cf78b9108c005a78b95f6fe1101b16962e69d1"
    )
    assert (
        c.envelope_sha256_v2(sized.envelope)
        == "dab6230eac400a1207e30356ba90c7d551e2dc687dfcd07d9aa0ef68261ed8cb"
    )
    assert (
        c.sizing_sha256_v2(sized)
        == "090f072bf456febb695f587d185ebd35c7d725d260ad9ec7b3c7134c312c5f2f"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"required_reservation_quote": D(59)},
        {"required_reservation_quote": None},
        {"liquidation_buffer_result": None},
        {"liquidation_buffer_result": "MODEL_NO_POSITIVE_TRIGGER"},
        {"blockers": ("RISK_V2_PLANNING_ONLY",)},
        {"stage": "PLAN"},
        {"margin": None},
    ],
)
def test_assessment_container_rejects_forged_derived_links(change: dict[str, Any]) -> None:
    with pytest.raises(c.RiskV2Rejected):
        e._assessment_payload_v2(_forged(_assess(), **change))


def test_assessment_container_rejects_known_future_expiry_and_causal_clocks() -> None:
    result = _assess()
    with pytest.raises(c.RiskV2Rejected, match="FUTURE_EVIDENCE"):
        replace(result, assessed_at=NOW - timedelta(microseconds=1))
    with pytest.raises(c.RiskV2Rejected, match="EXPIRED"):
        replace(result, assessed_at=EXPIRY)


def test_same_source_receipt_change_rebinds_but_audit_does_not() -> None:
    value = _inputs()
    margin = _margin(value)
    newer = NOW + timedelta(microseconds=1)
    changed = replace(
        value,
        account=replace(
            value.account, reference=replace(value.account.reference, received_at=newer)
        ),
    )
    with pytest.raises(c.RiskV2Rejected, match="MARGIN_UNVERIFIED"):
        r.assess_entry_v2(
            changed,
            now=newer,
            margin=margin,
            liquidation=None,
            leverage_readback=None,
            stage="PLAN",
        )


def test_hash_resource_bound_and_malformed_source_before_economic_acceptance() -> None:
    value = _inputs()
    margin = replace(_margin(value), initial_margin_quote=D((0, (9,) * 65536, 0)))
    with pytest.raises(c.RiskV2Rejected, match="RESOURCE_LIMIT"):
        e.margin_assessment_sha256_v2(margin)
    with pytest.raises(c.RiskV2Rejected, match="RESOURCE_LIMIT"):
        e.ExactRiskRatioV2(D("1E+65537"), D(1))
    bad = _forged(_liquidation(value, _margin(value)), quantity=object())
    _reject("INPUT_INVALID", value, margin=None, liquidation=bad, stage="PLAN")


def test_outcome_rule_is_independent_and_valid_opaque_proof_labels_remain_values() -> None:
    value = _inputs()
    margin = _margin(value)
    root = replace(_positive(), rule_revision="DISTINCT_LP_FORMULA_RULE")
    assert _assess(value, liquidation=_liquidation(value, margin, root)).blockers == ()
    outcome = e.LiquidationNoPositiveRootV2(
        "NO_POSITIVE_ROOT",
        "NON_POSITIVE_LONG_INTERCEPT",
        D(1000),
        "c" * 64,
        "d" * 64,
        ("TIER",),
        "DISTINCT_RULE",
        "e" * 64,
        "1" * 64,
    )
    for field in ("proof_sha256", "full_table_sha256", "raw_manifest_sha256"):
        change: dict[str, Any] = {field: "invalid"}
        with pytest.raises(c.RiskV2Rejected, match="INPUT_INVALID"):
            replace(outcome, **change)
    changed = replace(outcome, proof_sha256="2" * 64)
    assert (
        _assess(value, liquidation=_liquidation(value, margin, changed)).liquidation_buffer_result
        == "MODEL_NO_POSITIVE_TRIGGER"
    )


def test_readback_exact_inclusive_age_and_source_environment() -> None:
    value = _inputs()
    readback = _readback(value, _margin(value))
    old = NOW - timedelta(seconds=10)
    readback = replace(
        readback,
        after_control_at=old,
        reference=replace(readback.reference, server_observed_at=old, received_at=old),
    )
    assert _assess(value, leverage_readback=readback).blockers == ()
    _reject(
        "STALE_EVIDENCE", value, now=NOW + timedelta(microseconds=1), leverage_readback=readback
    )
    _reject(
        "SOURCE_MISMATCH",
        value,
        leverage_readback=replace(
            readback, reference=replace(readback.reference, source_environment="LIVE")
        ),
    )


def test_hostile_nested_fields_never_invoke_equality_hash_or_repr() -> None:
    class Hostile:
        def __eq__(self, other: object) -> bool:
            raise AssertionError("untrusted equality")

        def __hash__(self) -> int:
            raise AssertionError("untrusted hash")

        def __repr__(self) -> str:
            raise AssertionError("untrusted repr")

    value = _inputs()
    margin = _margin(value)
    _reject(
        "INPUT_INVALID",
        value,
        margin=_forged(margin, binding_sha256=Hostile()),
        liquidation=None,
        leverage_readback=None,
        stage="PLAN",
    )
    _reject(
        "INPUT_INVALID",
        value,
        leverage_readback=_forged(_readback(value, margin), control_command_key=Hostile()),
    )
    root = _forged(_positive(), consumed_tier_ids=(Hostile(),))
    _reject("INPUT_INVALID", value, liquidation=_forged(_liquidation(value, margin), outcome=root))


def test_three_amended_type_field_lists_are_exact() -> None:
    assert tuple(field.name for field in fields(e.ExactRiskRatioV2)) == ("numerator", "denominator")
    assert tuple(field.name for field in fields(e.LiquidationPositiveRootV2)) == (
        "kind",
        "exact_price",
        "candidate_tier_id",
        "domain_upper_quote",
        "full_table_sha256",
        "raw_manifest_sha256",
        "consumed_tier_ids",
        "rule_revision",
    )
    assert tuple(field.name for field in fields(e.LiquidationNoPositiveRootV2)) == (
        "kind",
        "reason",
        "domain_upper_quote",
        "full_table_sha256",
        "raw_manifest_sha256",
        "consumed_tier_ids",
        "rule_revision",
        "model_input_sha256",
        "proof_sha256",
    )


def test_compact_assessment_rechecks_observable_target_scope() -> None:
    result = _assess(stage="PLAN", liquidation=None, leverage_readback=None)
    assert result.margin is not None
    margin = replace(
        result.margin, reference=replace(result.margin.reference, target_environment="LIVE")
    )
    with pytest.raises(c.RiskV2Rejected, match="SOURCE_MISMATCH"):
        replace(result, margin=margin)
