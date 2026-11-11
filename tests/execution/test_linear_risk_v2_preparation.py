"""Synthetic complete preparation inputs; these values confer no entry authority."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import fields, replace
from datetime import timedelta, timezone
from decimal import Decimal as D
from decimal import Inexact, Rounded, localcontext
from typing import Any, cast

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
from multi_exchange_trader.market_data.instrument_definition import (
    INSTRUMENT_ENDPOINT,
    CatalogSource,
    InstrumentObservation,
    parse_bybit_instrument_response,
)


def _instrument(minimum: str = "95", *, received: Any = NOW) -> InstrumentObservation:
    original = instrument_v2()
    row = json.loads(original.raw_row_canonical)
    row["lotSizeFilter"]["minNotionalValue"] = minimum
    return parse_bybit_instrument_response(
        {"retCode": 0, "result": {"category": "linear", "list": [row]}, "time": 1793663999000},
        expected=original.key,
        source=CatalogSource("FIXTURE_CATALOG", "FIXTURE", INSTRUMENT_ENDPOINT, received),
    )


def _inputs(minimum: str = "95") -> e.LinearRiskInputsV2:
    policy = policy_v2()
    instrument = _instrument(minimum)
    costs = replace(costs_v2(), instrument_revision=instrument.specification_revision)
    sized = r.size_linear_entry_v2(
        policy=policy,
        proposal=proposal_v2(),
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
    minimum_amount = e.MinimumAmountAssessmentV2(
        policy.scope,
        c.RiskComputationRefV2("MINIMUM", "MINIMUM_V1", "FIXTURE", NOW, "b" * 64),
        c.envelope_sha256_v2(sized.envelope),
        instrument.specification_revision,
        "MINIMUM_V1",
        ("INSTRUMENT", "LTP"),
        D(95),
        D(minimum),
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
        proposal_v2(),
        instrument,
        "FIXTURE_CATALOG_V1",
        costs,
        account,
        reservations,
        (c.LinearMarkQuoteV2(instrument.key, observation_ref_v2("MARK"), D(140)),),
        tiers,
        DIGEST,
        minimum_amount,
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


def _forged(value: Any, **changes: Any) -> Any:
    result = object.__new__(type(value))
    for field in fields(value):
        object.__setattr__(result, field.name, changes.get(field.name, getattr(value, field.name)))
    return result


def _reject(value: e.LinearRiskInputsV2, code: str, *, now: Any = NOW) -> None:
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_" + code + "$"):
        r.prepare_linear_entry_v2(value, now=now)


def test_literal_distinct_mark_limit_minimum_and_exact_display() -> None:
    inputs = _inputs()
    assert inputs.proposal.entry.limit_price == D(100)
    assert inputs.last_trade_quote.last_price == D(95)
    assert inputs.marks[0].mark_price == D(140)
    prepared = r.prepare_linear_entry_v2(inputs, now=NOW)
    assert prepared.sizing.envelope.base_quantity == D(1)
    assert prepared.sizing.envelope.limit_price == D(100)
    assert prepared.minimum_amount.evaluation_price == D(95)
    assert prepared.minimum_amount.evaluated_quote_amount == D(95)
    assert prepared.valuation.proposed_mark_quote == D(140)
    assert prepared.sizing.estimated_loss_quote == D(12)
    assert prepared.capacity_quote == D(60)
    assert prepared.estimated_loss_fraction_display == D("1." + "714285" * 13 + "8")
    assert prepared.display_ratio_rule == "RISK_V2_EXACT_FINITE_ELSE_CEILING80_V1"
    assert not hasattr(prepared, "submission_ready") and not hasattr(prepared, "qualified")


def test_linear_inputs_wire_shape_carries_the_last_trade_source_identity() -> None:
    payload = e._inputs_payload_v2(_inputs())
    quote = payload["last_trade_quote"]
    assert quote["last_price"] == "95"
    assert quote["reference"]["observation_id"] == "LTP"
    assert quote["reference"]["source_rule_revision"] == "FIXTURE_SOURCE_V1"
    assert "LTP" in payload["minimum_amount"]["input_observation_ids"]


def test_minimum_96_rejects_without_upsize() -> None:
    _reject(_inputs("96"), "MINIMUM_SIZE")


def test_minimum_below_minimum_quantity_rejects_at_authentic_last_trade_price() -> None:
    value = _policy(_inputs(), per_trade_loss=D(6))
    sizing = r.size_linear_entry_v2(
        policy=value.policy,
        proposal=value.proposal,
        instrument=value.instrument,
        catalog_revision=value.catalog_revision,
        costs=value.costs,
        now=NOW,
    )
    assert sizing.envelope.base_quantity == D("0.5")
    assert sizing.envelope.base_quantity * value.last_trade_quote.last_price == D("47.5")
    assert value.minimum_amount.envelope_sha256 == c.envelope_sha256_v2(sizing.envelope)
    authentic_assessment = replace(value.minimum_amount, evaluated_quote_amount=D("47.5"))
    _reject(replace(value, minimum_amount=authentic_assessment), "MINIMUM_SIZE")


def test_minimum_cannot_use_inflated_price_to_hide_small_quantity() -> None:
    value = _policy(_inputs(), per_trade_loss=D(6))
    sizing = r.size_linear_entry_v2(
        policy=value.policy,
        proposal=value.proposal,
        instrument=value.instrument,
        catalog_revision=value.catalog_revision,
        costs=value.costs,
        now=NOW,
    )
    assert sizing.envelope.base_quantity == D("0.5")
    assert sizing.envelope.base_quantity * value.proposal.entry.limit_price < D(95)
    assert value.last_trade_quote.last_price == D(95)
    assert value.minimum_amount.envelope_sha256 == c.envelope_sha256_v2(sizing.envelope)
    forged_evaluation = replace(
        value.minimum_amount,
        evaluation_price=D(200),
        evaluated_quote_amount=D(100),
    )
    assert sizing.envelope.base_quantity * forged_evaluation.evaluation_price >= D(95)
    _reject(
        replace(value, minimum_amount=forged_evaluation),
        "MINIMUM_RULE_UNVERIFIED",
    )


@pytest.mark.parametrize(
    ("changes", "account_changes", "code"),
    [
        ({"max_total_risk": D(16)}, {}, "TOTAL_RISK_LIMIT"),
        ({}, {"daily_loss": D(84)}, "DAILY_LOSS_LIMIT"),
        ({}, {"drawdown": D(184)}, "DRAWDOWN_LIMIT"),
        ({}, {"consecutive_losses": 3}, "CONSECUTIVE_LOSS_LIMIT"),
        ({}, {"position_count": 3}, "POSITION_LIMIT"),
        ({}, {"loss_day": (NOW - timedelta(days=1)).date()}, "LOSS_LEDGER_UNVERIFIED"),
        ({}, {"external_same_symbol_activity_ids": ("UNRESOLVED",)}, "EXTERNAL_CONFLICT"),
        ({}, {"margin_mode": "CROSS"}, "UNSUPPORTED_MODE"),
    ],
)
def test_original_loss_count_ledger_and_external_predicates(
    changes: dict[str, Any], account_changes: dict[str, Any], code: str
) -> None:
    value = _policy(_inputs(), **changes)
    _reject(replace(value, account=replace(value.account, **account_changes)), code)


def test_loss_and_count_exact_equalities_are_accepted() -> None:
    value = _policy(_inputs(), max_total_risk=D(17), max_positions=3)
    value = replace(
        value,
        account=replace(value.account, daily_loss=D(83), drawdown=D(183), consecutive_losses=2),
    )
    assert r.prepare_linear_entry_v2(value, now=NOW).sizing.estimated_loss_quote == D(12)


@pytest.mark.parametrize(
    ("allocation", "available", "total_margin", "held", "want"),
    [
        ("60", "500", "400", "5", "60"),
        ("60", "7", "400", "5", "2"),
        ("60", "500", "993", "5", "2"),
        ("60", "6.99999999999999999999", "400", "5", None),
        ("60", "500", "993.00000000000000000001", "5", None),
        ("60", "4", "400", "5", None),
        ("60", "500", "996", "5", None),
        ("1.99999999999999999999", "500", "400", "5", None),
    ],
)
def test_literal_capacity_each_branch_and_exact_cost_boundary(
    allocation: str, available: str, total_margin: str, held: str, want: str | None
) -> None:
    value = _policy(_inputs(), margin_allocation=D(allocation))
    value = replace(
        value,
        account=replace(value.account, available_margin=D(available), total_margin=D(total_margin)),
        reservations=replace(value.reservations, margin_amount=D(held)),
    )
    if want is None:
        _reject(value, "MARGIN_LIMIT")
    else:
        assert r.prepare_linear_entry_v2(value, now=NOW).capacity_quote == D(want)


@pytest.mark.parametrize(
    "name",
    [
        "minimum_amount_rule_revision",
        "cost_rule_revision",
        "fee_rule_revision",
        "funding_rule_revision",
        "stop_gap_model_revision",
    ],
)
def test_required_model_pins_precede_economic_rejection(name: str) -> None:
    value = _policy(_inputs("96"), max_total_risk=D(16))
    changes: dict[str, Any] = {name: "WRONG"}
    value = replace(value, requirements=replace(value.requirements, **changes))
    _reject(
        value,
        "MINIMUM_RULE_UNVERIFIED"
        if name == "minimum_amount_rule_revision"
        else "REVISION_MISMATCH",
    )


def test_minimum_opaque_full_input_hash_and_order_are_preserved() -> None:
    value = _inputs()
    first = r.prepare_linear_entry_v2(value, now=NOW)
    assert first.minimum_amount.reference.input_sha256 != first.minimum_amount.envelope_sha256
    reordered = replace(
        value,
        minimum_amount=replace(value.minimum_amount, input_observation_ids=("LTP", "INSTRUMENT")),
    )
    assert e.binding_sha256_v2(
        r.prepare_linear_entry_v2(reordered, now=NOW).binding
    ) != e.binding_sha256_v2(first.binding)


@pytest.mark.parametrize(
    ("name", "replacement", "code"),
    [
        ("envelope_sha256", "c" * 64, "SOURCE_MISMATCH"),
        ("instrument_revision", "c" * 64, "REVISION_MISMATCH"),
        ("evaluation_price", D(94), "MINIMUM_RULE_UNVERIFIED"),
        ("minimum_quote_amount", D(94), "MINIMUM_RULE_UNVERIFIED"),
        ("evaluated_quote_amount", D(94), "MINIMUM_RULE_UNVERIFIED"),
    ],
)
def test_minimum_exact_cross_binding(name: str, replacement: Any, code: str) -> None:
    value = _inputs()
    _reject(
        replace(value, minimum_amount=replace(value.minimum_amount, **{name: replacement})), code
    )


@pytest.mark.parametrize(
    ("field", "time", "code"),
    [
        ("computed_at", NOW - timedelta(microseconds=1), "CAUSAL_ORDER_INVALID"),
        ("computed_at", NOW + timedelta(microseconds=1), "FUTURE_EVIDENCE"),
    ],
)
def test_minimum_known_causal_future_expiry(field: str, time: Any, code: str) -> None:
    value = _inputs()
    minimum = (
        replace(
            value.minimum_amount,
            reference=replace(value.minimum_amount.reference, computed_at=time),
        )
        if field == "computed_at"
        else replace(value.minimum_amount, valid_until=time)
    )
    _reject(replace(value, minimum_amount=minimum), code)


def test_minimum_strict_expiry_with_valid_completion_order() -> None:
    value = _inputs()
    value = replace(
        value,
        minimum_amount=replace(value.minimum_amount, valid_until=NOW + timedelta(microseconds=1)),
    )
    _reject(value, "EXPIRED", now=NOW + timedelta(microseconds=1))


def test_audit_now_is_not_economic_identity_or_a_causal_source() -> None:
    value = _inputs()
    first = r.prepare_linear_entry_v2(value, now=NOW)
    second = r.prepare_linear_entry_v2(value, now=NOW + timedelta(microseconds=1))
    assert first.sizing.assessed_at != second.sizing.assessed_at
    assert first.valuation.assessed_at != second.valuation.assessed_at
    assert c.sizing_sha256_v2(first.sizing) == c.sizing_sha256_v2(second.sizing)
    assert c.valuation_sha256_v2(first.valuation) == c.valuation_sha256_v2(second.valuation)
    assert e.binding_sha256_v2(first.binding) == e.binding_sha256_v2(second.binding)
    # Existing model completion at NOW can satisfy every available source lower
    # bound despite the later audit. Task6 performs actual model validation.
    sources = (
        value.costs.reference.computed_at,
        value.minimum_amount.reference.computed_at,
        value.account.reference.received_at,
        value.reservations.observed_at,
        *(mark.reference.received_at for mark in value.marks),
        value.tiers.reference.received_at,
        value.instrument.source.received_at,
    )
    assert max(sources) <= NOW < second.sizing.assessed_at


@pytest.mark.parametrize(
    "source",
    [
        "account_receipt",
        "account_coverage",
        "reservation",
        "mark",
        "tier",
        "minimum",
        "domain",
        "requirements",
        "source_mapping",
        "cost_input",
        "instrument_receipt",
    ],
)
def test_economic_source_metadata_changes_binding(source: str) -> None:
    value = _inputs()
    first = r.prepare_linear_entry_v2(value, now=NOW)
    if source == "account_receipt":
        value = replace(
            value,
            account=replace(
                value.account,
                reference=replace(
                    value.account.reference, received_at=NOW + timedelta(microseconds=1)
                ),
            ),
        )
    elif source == "account_coverage":
        value = replace(value, account=replace(value.account, coverage_revision="COVERAGE_V2"))
    elif source == "reservation":
        value = replace(value, reservations=replace(value.reservations, journal_revision=2))
    elif source == "mark":
        value = replace(value, marks=(replace(value.marks[0], mark_price=D(141)),))
    elif source == "tier":
        value = replace(
            value,
            tiers=replace(
                value.tiers, reference=replace(value.tiers.reference, payload_sha256="b" * 64)
            ),
        )
    elif source == "minimum":
        value = replace(
            value,
            minimum_amount=replace(
                value.minimum_amount,
                reference=replace(value.minimum_amount.reference, input_sha256="c" * 64),
            ),
        )
    elif source == "domain":
        value = replace(value, risk_domain_sha256="b" * 64)
    elif source == "requirements":
        value = replace(
            value,
            requirements=replace(
                value.requirements, cost_assessment_basis="HARD_EXECUTION_FEE_UPPER_V1"
            ),
        )
    elif source == "source_mapping":
        value = replace(
            value, requirements=replace(value.requirements, source_mapping_revision="MAPPING_V2")
        )
    elif source == "cost_input":
        value = replace(
            value,
            costs=replace(
                value.costs, reference=replace(value.costs.reference, input_sha256="d" * 64)
            ),
        )
    else:
        instrument = _instrument(received=NOW - timedelta(microseconds=1))
        sized = r.size_linear_entry_v2(
            policy=value.policy,
            proposal=value.proposal,
            instrument=instrument,
            catalog_revision=value.catalog_revision,
            costs=value.costs,
            now=NOW,
        )
        value = replace(
            value,
            instrument=instrument,
            minimum_amount=replace(
                value.minimum_amount, envelope_sha256=c.envelope_sha256_v2(sized.envelope)
            ),
        )
    second = r.prepare_linear_entry_v2(value, now=NOW + timedelta(microseconds=1))
    assert e.binding_sha256_v2(first.binding) != e.binding_sha256_v2(second.binding)


def test_hostile_decimal_context_has_no_effect_or_mutation() -> None:
    with localcontext() as context:
        context.prec = 1
        context.Emin = -2
        context.Emax = 2
        context.capitals = 0
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        before = context.copy()
        value = r.prepare_linear_entry_v2(_inputs(), now=NOW)
        assert value.capacity_quote == D(60)
        assert value.sizing.envelope.base_quantity == D(1)
        assert value.estimated_loss_fraction_display == D("1." + "714285" * 13 + "8")
        assert context == before or (str(context) == str(before) and context.flags == before.flags)


def test_utc_loss_day_and_explicit_time_type() -> None:
    value = _inputs()
    assert r.prepare_linear_entry_v2(
        value, now=NOW.astimezone(timezone(timedelta(hours=9)))
    ).capacity_quote == D(60)
    _reject(value, "INPUT_INVALID", now=NOW.replace(tzinfo=None))
    _reject(value, "INPUT_INVALID", now=cast(Any, 0))


@pytest.mark.parametrize(
    ("name", "bad"),
    [
        ("cost_assessment_basis", "UNKNOWN"),
        ("margin_reserve_basis", "UNKNOWN"),
        ("reserve_rounding_revision", "UNKNOWN"),
        ("reserve_quantum_usdt", D("0.000000010")),
        ("reserve_quantum_usdt", D("1E-7")),
        ("reserve_quantum_usdt", True),
    ],
)
def test_m1_requirements_mandatory_exact_bases_and_quantum(name: str, bad: Any) -> None:
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        e.requirements_sha256_v2(_forged(_inputs().requirements, **{name: bad}))


@pytest.mark.parametrize(
    "name",
    ["input_observation_ids", "evaluation_price", "minimum_quote_amount", "evaluated_quote_amount"],
)
def test_minimum_forged_type_is_validated_by_hash(name: str) -> None:
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        e.minimum_assessment_sha256_v2(
            _forged(
                _inputs().minimum_amount, **{name: [] if name == "input_observation_ids" else True}
            )
        )


@pytest.mark.parametrize("identifiers", [(), ("DUP", "DUP"), ("bad space",)])
def test_minimum_input_identity_tuple_rejects_invalid_members(identifiers: tuple[str, ...]) -> None:
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        e.minimum_assessment_sha256_v2(
            _forged(_inputs().minimum_amount, input_observation_ids=identifiers)
        )


def test_minimum_input_identity_resource_bound() -> None:
    value = _inputs().minimum_amount
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_RESOURCE_LIMIT$"):
        e.minimum_assessment_sha256_v2(
            _forged(value, input_observation_ids=tuple(f"I{i}" for i in range(1025)))
        )


@pytest.mark.parametrize(
    "part",
    [
        "policy",
        "proposal",
        "instrument",
        "costs",
        "account",
        "reservations",
        "marks",
        "tiers",
        "minimum_amount",
        "requirements",
        "risk_domain_sha256",
        "catalog_revision",
    ],
)
def test_full_input_closed_revalidation_precedes_economic_work(part: str) -> None:
    value = _inputs()
    bad = _forged(value, **{part: object()})
    _reject(bad, "INPUT_INVALID")


def test_missing_new_dto_fields_rejected_by_consumers() -> None:
    value = _inputs()
    prepared = r.prepare_linear_entry_v2(value, now=NOW)
    consumers: tuple[tuple[Any, Callable[[Any], Any]], ...] = (
        (value.minimum_amount, e.minimum_assessment_sha256_v2),
        (value.requirements, e.requirements_sha256_v2),
        (prepared.binding, e.binding_sha256_v2),
        (value, lambda item: r.prepare_linear_entry_v2(item, now=NOW)),
        (prepared, e._preparation_payload_v2),
    )
    for original, consume in consumers:
        for field in fields(original):
            bad = _forged(original)
            object.__delattr__(bad, field.name)
            with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
                consume(bad)


def test_binding_hash_rejects_malformed_nested_scope_before_hooks() -> None:
    value = r.prepare_linear_entry_v2(_inputs(), now=NOW).binding

    class Hostile:
        def __eq__(self, other: object) -> bool:
            raise AssertionError("unvalidated equality")

        def __hash__(self) -> int:
            raise AssertionError("unvalidated hash")

    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        e.binding_sha256_v2(_forged(value, scope=Hostile()))


def _literal_hash(record_type: str, payload: dict[str, Any]) -> str:
    text = json.dumps(
        {"schema": "RISK_V2_CANONICAL_V1", "record_type": record_type, "fields": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _literal_scope() -> dict[str, Any]:
    return {
        "product_scope": {
            "account": {"account_id": "FIXTURE_ACCOUNT", "environment": "DEMO"},
            "product": "LINEAR",
        },
        "instrument": {"venue": "BYBIT", "product": "LINEAR", "symbol": "XAAUSDT"},
        "strategy": {"strategy_id": "FIXTURE_STRATEGY", "revision": "FIXTURE_STRATEGY_V1"},
    }


def test_requirements_literal_canonical_all_four_m1_fields() -> None:
    payload = {
        "source_mapping_revision": "MAPPING_V1",
        "minimum_amount_rule_revision": "MINIMUM_V1",
        "cost_rule_revision": "FIXTURE_COST_V1",
        "fee_rule_revision": "FIXTURE_FEE_V1",
        "funding_rule_revision": "FIXTURE_FUNDING_V1",
        "stop_gap_model_revision": "EXPLICIT_POLICY_STOP_GAP_CAP_V1",
        "tier_bound_rule_revision": "LINEAR_MARK_GROSS_UPPER_BOUND_V1",
        "leverage_grid_rule": "LOCAL_ZERO_ORIGIN_LATTICE_V1",
        "margin_model_revision": "MARGIN_V1",
        "liquidation_model_revision": "LIQUIDATION_V1",
        "cost_assessment_basis": "POINT_ESTIMATE_WITH_RECONCILIATION_V1",
        "margin_reserve_basis": "POINT_ESTIMATE_TWO_IM_ENVELOPE_V1",
        "reserve_rounding_revision": "APP_RESERVE_CEILING_QUANTUM_V1",
        "reserve_quantum_usdt": "1E-8",
    }
    assert e.requirements_sha256_v2(_inputs().requirements) == _literal_hash(
        "RiskRequirementsV2", payload
    )


def test_minimum_literal_canonical_source_times_and_spellings() -> None:
    value = replace(_inputs().minimum_amount, envelope_sha256=DIGEST, instrument_revision=DIGEST)
    payload: dict[str, Any] = {
        "scope": _literal_scope(),
        "reference": {
            "calculation_id": "MINIMUM",
            "model_revision": "MINIMUM_V1",
            "target_environment": "FIXTURE",
            "computed_at": "2026-11-03T00:00:00.000000+00:00",
            "input_sha256": "b" * 64,
        },
        "envelope_sha256": DIGEST,
        "instrument_revision": DIGEST,
        "rule_revision": "MINIMUM_V1",
        "input_observation_ids": ["INSTRUMENT", "LTP"],
        "evaluation_price": "95",
        "minimum_quote_amount": "95",
        "evaluated_quote_amount": "95",
        "valid_until": "2026-11-03T00:00:01.000000+00:00",
    }
    assert e.minimum_assessment_sha256_v2(value) == _literal_hash(
        "MinimumAmountAssessmentV2", payload
    )
    assert e.minimum_assessment_sha256_v2(
        replace(value, evaluation_price=D("95.0"))
    ) != e.minimum_assessment_sha256_v2(value)


def test_binding_literal_canonical_every_field_and_pin() -> None:
    prepared = r.prepare_linear_entry_v2(_inputs(), now=NOW)
    original = prepared.binding
    changes: dict[str, Any] = {
        field.name: DIGEST for field in fields(original) if field.name.endswith("sha256")
    }
    pin = replace(original.instrument_pin, specification_revision=DIGEST, raw_row_sha256=DIGEST)
    value = replace(original, instrument_pin=pin, **changes)
    payload: dict[str, Any] = {
        "scope": _literal_scope(),
        "instrument_pin": {
            "key": {"venue": "BYBIT", "product": "LINEAR", "symbol": "XAAUSDT"},
            "native_instrument_id": "XAAUSDT-LINEAR.BYBIT",
            "catalog_revision": "FIXTURE_CATALOG_V1",
            "specification_revision": DIGEST,
            "parser_revision": "BYBIT_INSTRUMENT_PARSER_V1",
            "source_rule_revision": "BYBIT_LINEAR_USDT_PERPETUAL_V1",
            "source_id": "FIXTURE_CATALOG",
            "source_environment": "FIXTURE",
            "response_time": "2026-11-02T23:59:59.000000+00:00",
            "received_at": "2026-11-03T00:00:00.000000+00:00",
            "raw_row_sha256": DIGEST,
        },
        "source_mapping_revision": "MAPPING_V1",
        "cost_calculation_id": "FIXTURE_COST",
        "account_observation_id": "ACCOUNT",
        "account_coverage_revision": "COVERAGE_V1",
        "reservation_snapshot_id": "RESERVATION",
        "reservation_owner_generation": "OWNER_V1",
        "tier_observation_id": "TIERS",
        **changes,
    }
    assert e.binding_sha256_v2(value) == _literal_hash("LinearRiskBindingV2", payload)


def test_derived_capacity_is_not_restricted_to_source_precision() -> None:
    value = _policy(_inputs(), margin_allocation=D("1E1000"), max_total_margin=D("1E1000"))
    value = replace(
        value,
        account=replace(value.account, available_margin=D("1E1000"), total_margin=D("1E-5000")),
        reservations=replace(value.reservations, margin_amount=D(0)),
    )
    prepared = r.prepare_linear_entry_v2(value, now=NOW)
    assert prepared.capacity_quote.as_tuple() == (0, (9,) * 6000, -5000)
    e._preparation_payload_v2(prepared)


@pytest.mark.parametrize(
    ("name", "bad"), [("evaluation_price", D("1E1001")), ("evaluated_quote_amount", D("1E65537"))]
)
def test_minimum_source_and_derived_resource_limits(name: str, bad: D) -> None:
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_RESOURCE_LIMIT$"):
        e.minimum_assessment_sha256_v2(_forged(_inputs().minimum_amount, **{name: bad}))


def test_minimum_input_array_canonical_size_limit() -> None:
    value = replace(
        _inputs().minimum_amount,
        input_observation_ids=tuple(f"I{i:04}" + "x" * 90 for i in range(1024)),
    )
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_RESOURCE_LIMIT$"):
        e.minimum_assessment_sha256_v2(value)


@pytest.mark.parametrize("part", ["account", "marks", "tiers", "costs", "minimum_amount"])
def test_source_freshness_precedes_minimum_economic_rejection(part: str) -> None:
    value = _inputs("96")
    future = NOW + timedelta(microseconds=1)
    if part == "account":
        value = replace(
            value,
            account=replace(
                value.account, reference=replace(value.account.reference, received_at=future)
            ),
        )
    elif part == "marks":
        value = replace(
            value,
            marks=(
                replace(
                    value.marks[0], reference=replace(value.marks[0].reference, received_at=future)
                ),
            ),
        )
    elif part == "tiers":
        value = replace(
            value,
            tiers=replace(
                value.tiers, reference=replace(value.tiers.reference, received_at=future)
            ),
        )
    elif part == "costs":
        value = replace(
            value,
            costs=replace(
                value.costs, reference=replace(value.costs.reference, computed_at=future)
            ),
        )
    else:
        value = replace(
            value,
            minimum_amount=replace(
                value.minimum_amount,
                reference=replace(value.minimum_amount.reference, computed_at=future),
            ),
        )
    _reject(value, "FUTURE_EVIDENCE")


def test_minimum_available_age_boundary_and_no_inferred_id_times() -> None:
    value = _inputs()
    # Unresolved IDs have no invented observation timestamp. Known receipt and
    # computed-at equality pass; actual registry consumption is still required.
    value = _policy(value, max_evidence_age_seconds=D(1), max_quote_age_seconds=D(1))
    assert r.prepare_linear_entry_v2(value, now=NOW).minimum_amount.input_observation_ids == (
        "INSTRUMENT",
        "LTP",
    )
    policy = replace(value.policy, max_evidence_age_seconds=D("0.999999"))
    value = replace(
        value,
        policy=policy,
        costs=replace(value.costs, policy_sha256=c.risk_policy_sha256_v2(policy)),
    )
    _reject(value, "STALE_EVIDENCE")


def test_minimum_reference_model_and_target_are_revalidated() -> None:
    value = _inputs().minimum_amount
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_MINIMUM_RULE_UNVERIFIED$"):
        e.minimum_assessment_sha256_v2(
            _forged(value, reference=replace(value.reference, model_revision="OTHER"))
        )
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_SOURCE_MISMATCH$"):
        e.minimum_assessment_sha256_v2(
            _forged(value, reference=replace(value.reference, target_environment="LIVE"))
        )


def test_malformed_later_nested_field_precedes_cross_scope_comparison() -> None:
    value = _inputs()
    wrong_scope = replace(
        value.policy.scope, strategy=replace(value.policy.scope.strategy, revision="OTHER")
    )
    forged = _forged(
        value,
        proposal=replace(value.proposal, scope=wrong_scope),
        minimum_amount=_forged(value.minimum_amount, reference=object()),
    )
    _reject(forged, "INPUT_INVALID")
