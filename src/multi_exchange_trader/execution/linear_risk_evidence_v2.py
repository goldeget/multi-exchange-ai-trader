"""Non-authoritative Linear tier range observations and local grid bounds."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from typing import Literal, cast

from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.risk_v2_math import (
    decimal_fraction_v2,
    finite_decimal_v2,
    zero_origin_lattice_v2,
)
from multi_exchange_trader.market_data.instrument_definition import (
    InstrumentObservation,
    LinearInstrumentSpec,
)
from multi_exchange_trader.trading_product import InstrumentKey


@dataclass(frozen=True)
class LinearRiskTierV2:
    tier_id: str
    lower_exclusive_quote: Decimal
    upper_inclusive_quote: Decimal
    min_leverage: Decimal
    max_leverage: Decimal
    initial_margin_rate: Decimal
    maintenance_margin_rate: Decimal
    maintenance_margin_deduction: Decimal

    def __post_init__(self) -> None:
        _tier_payload_v2(self)


@dataclass(frozen=True)
class LinearTierSnapshotV2:
    instrument: InstrumentKey
    reference: c.RiskObservationRefV2
    instrument_revision: str
    rows: tuple[LinearRiskTierV2, ...]

    def __post_init__(self) -> None:
        _tier_snapshot_payload_v2(self)


@dataclass(frozen=True)
class LinearTierBoundV2:
    instrument: InstrumentKey
    tier_observation_id: str
    mark_observation_id: str
    symbol_gross_upper_quote: Decimal
    covered_tier_ids: tuple[str, ...]
    continuous_leverage_lower: Decimal
    continuous_leverage_upper: Decimal
    leverage_step: Decimal
    leverage_grid_rule: Literal["LOCAL_ZERO_ORIGIN_LATTICE_V1"]
    permitted_leverage_min: Decimal
    permitted_leverage_max: Decimal
    bound_rule: Literal["LINEAR_MARK_GROSS_UPPER_BOUND_V1"]

    def __post_init__(self) -> None:
        _tier_bound_payload_v2(self)


def tier_snapshot_sha256_v2(value: LinearTierSnapshotV2) -> str:
    return c._canonical_sha256_v2("LinearTierSnapshotV2", _tier_snapshot_payload_v2(value))


def tier_bound_sha256_v2(bound: LinearTierBoundV2) -> str:
    return c._canonical_sha256_v2("LinearTierBoundV2", _tier_bound_payload_v2(bound))


def _tier_payload_v2(value: object) -> dict[str, object]:
    values = c._fields_v2(value, LinearRiskTierV2)
    payload: dict[str, object] = {"tier_id": c._identifier_v2(values["tier_id"])}
    for name in (
        "lower_exclusive_quote",
        "upper_inclusive_quote",
        "min_leverage",
        "max_leverage",
        "initial_margin_rate",
        "maintenance_margin_rate",
        "maintenance_margin_deduction",
    ):
        payload[name] = c._decimal_text_field_v2(
            values,
            name,
            source=True,
            sign="nonnegative"
            if name in ("lower_exclusive_quote", "maintenance_margin_deduction")
            else "positive",
        )
    number = {name: decimal_fraction_v2(values[name]) for name in values if name != "tier_id"}
    if (
        number["lower_exclusive_quote"] >= number["upper_inclusive_quote"]
        or number["min_leverage"] > number["max_leverage"]
        or not number["maintenance_margin_rate"] <= number["initial_margin_rate"] <= 1
    ):
        c._reject_v2()
    return payload


def _tier_snapshot_payload_v2(value: object) -> dict[str, object]:
    values = c._fields_v2(value, LinearTierSnapshotV2)
    key = c._key_payload_v2(values["instrument"])
    reference = c._observation_payload_v2(values["reference"])
    revision = c._identifier_v2(values["instrument_revision"])
    rows = c._tuple_v2(values["rows"], minimum=1, maximum=1024)
    payload = tuple(_tier_payload_v2(row) for row in rows)
    if key["product"] != "LINEAR":
        c._reject_v2("RISK_V2_UNSUPPORTED_PRODUCT")
    if reference["source_environment"] not in ("PUBLIC_MAINNET", "PUBLIC_TESTNET", "FIXTURE"):
        c._reject_v2("RISK_V2_SOURCE_MISMATCH")
    previous = Fraction(0)
    identifiers: set[str] = set()
    for row in rows:
        if decimal_fraction_v2(row.lower_exclusive_quote) != previous or row.tier_id in identifiers:
            c._reject_v2()
        identifiers.add(row.tier_id)
        previous = decimal_fraction_v2(row.upper_inclusive_quote)
    return {
        "instrument": key,
        "reference": reference,
        "instrument_revision": revision,
        "rows": payload,
    }


def _tier_bound_payload_v2(value: object) -> dict[str, object]:
    values = c._fields_v2(value, LinearTierBoundV2)
    key = c._key_payload_v2(values["instrument"])
    if key["product"] != "LINEAR":
        c._reject_v2("RISK_V2_UNSUPPORTED_PRODUCT")
    identifiers = c._tuple_v2(values["covered_tier_ids"], minimum=1, maximum=1024)
    for identifier in identifiers:
        c._identifier_v2(identifier)
    if len(set(identifiers)) != len(identifiers):
        c._reject_v2()
    payload: dict[str, object] = {"instrument": key, "covered_tier_ids": identifiers}
    for name in ("tier_observation_id", "mark_observation_id"):
        payload[name] = c._identifier_v2(values[name])
    for name in (
        "symbol_gross_upper_quote",
        "continuous_leverage_lower",
        "continuous_leverage_upper",
        "leverage_step",
        "permitted_leverage_min",
        "permitted_leverage_max",
    ):
        payload[name] = c._decimal_text_field_v2(values, name, source=name == "leverage_step")
    payload["leverage_grid_rule"] = c._literal_v2(
        values["leverage_grid_rule"], ("LOCAL_ZERO_ORIGIN_LATTICE_V1",)
    )
    payload["bound_rule"] = c._literal_v2(
        values["bound_rule"], ("LINEAR_MARK_GROSS_UPPER_BOUND_V1",)
    )
    lower = decimal_fraction_v2(values["continuous_leverage_lower"])
    upper = decimal_fraction_v2(values["continuous_leverage_upper"])
    step = decimal_fraction_v2(values["leverage_step"])
    # A forged bound is malformed, rather than a fresh leverage assessment.
    if lower > upper:
        c._reject_v2()
    first, last = zero_origin_lattice_v2(lower, upper, step)
    if (
        decimal_fraction_v2(values["permitted_leverage_min"]) != first
        or decimal_fraction_v2(values["permitted_leverage_max"]) != last
    ):
        c._reject_v2()
    return payload


def assess_linear_tier_v2(
    *,
    policy: c.LinearRiskPolicyV2,
    sizing: c.LinearSizedEntryV2,
    instrument: InstrumentObservation,
    account: c.LinearAccountRiskSnapshotV2,
    reservations: c.LinearReservationSnapshotV2,
    marks: tuple[c.LinearMarkQuoteV2, ...],
    tiers: LinearTierSnapshotV2,
    now: datetime,
) -> LinearTierBoundV2:
    # Revalidate every supplied record before cross-record keys, hashes or lookup.
    policy_payload = c._policy_payload_v2(policy)
    sizing_payload = c._sizing_payload_v2(sizing)
    account_payload = c._account_snapshot_payload_v2(account)
    reservation_payload = c._reservation_snapshot_payload_v2(reservations)
    c._marks_payload_v2(marks)
    tier_payload = _tier_snapshot_payload_v2(tiers)
    now = c._utc_v2(now)
    pin = c.pin_instrument_v2(
        instrument, catalog_revision=sizing.envelope.instrument_pin.catalog_revision
    )
    if (
        policy_payload["scope"] != sizing_payload["scope"]
        or c._account_payload_v2(policy.scope.product_scope.account) != account_payload["account"]
        or account_payload["account"] != reservation_payload["account"]
        or policy.scope.instrument != pin.key
        or tier_payload["instrument"] != c._key_payload_v2(pin.key)
    ):
        c._reject_v2("RISK_V2_SCOPE_MISMATCH")
    if (
        sizing.policy_sha256 != c.risk_policy_sha256_v2(policy)
        or sizing.envelope.policy_revision != policy.revision
        or tiers.instrument_revision != pin.specification_revision
    ):
        c._reject_v2("RISK_V2_REVISION_MISMATCH")
    if c._pin_payload_v2(sizing.envelope.instrument_pin) != c._pin_payload_v2(pin):
        c._reject_v2("RISK_V2_SOURCE_MISMATCH")
    account_ids = {item.component_id for item in account.components}
    if account_ids.intersection(item.component_id for item in reservations.components):
        c._reject_v2("RISK_V2_EXPOSURE_OVERLAP")
    required = tuple(
        sorted(
            {
                policy.scope.instrument,
                *(item.instrument for item in account.components),
                *(item.instrument for item in reservations.components),
            },
            key=c._key_order_v2,
        )
    )
    prices = c._mark_values_v2(
        marks, required=required, now=now, max_age_seconds=policy.max_quote_age_seconds
    )
    maximum = decimal_fraction_v2(policy.max_evidence_age_seconds)
    for observed in (
        account.reference.server_observed_at,
        account.reference.received_at,
        reservations.observed_at,
        sizing.cost_reference.computed_at,
        pin.response_time,
        pin.received_at,
        tiers.reference.server_observed_at,
        tiers.reference.received_at,
    ):
        c._fresh_v2(observed, now=now, maximum=maximum)
    if c._utc_v2(sizing.cost_reference.computed_at) < c._utc_v2(pin.received_at):
        c._reject_v2("RISK_V2_CAUSAL_ORDER_INVALID")
    if now >= c._utc_v2(sizing.envelope.valid_until):
        c._reject_v2("RISK_V2_EXPIRED")
    selected = policy.scope.instrument
    account_gross = c._gross_components_v2(
        tuple(item for item in account.components if item.instrument == selected), prices=prices
    )
    pending_gross = c._gross_components_v2(
        tuple(item for item in reservations.components if item.instrument == selected),
        prices=prices,
    )
    upper_quote = (
        decimal_fraction_v2(account_gross)
        + decimal_fraction_v2(pending_gross)
        + decimal_fraction_v2(sizing.envelope.base_quantity) * decimal_fraction_v2(prices[selected])
    )
    if upper_quote > decimal_fraction_v2(tiers.rows[-1].upper_inclusive_quote):
        c._reject_v2("RISK_V2_RISK_TIER_UNVERIFIED")
    covered = tuple(
        row for row in tiers.rows if decimal_fraction_v2(row.lower_exclusive_quote) < upper_quote
    )
    # Full-table validation established ordered contiguous coverage from zero.
    spec = instrument.spec
    if type(spec) is not LinearInstrumentSpec:
        c._reject_v2("RISK_V2_UNSUPPORTED_PRODUCT")
    lower = max(
        decimal_fraction_v2(policy.leverage_min),
        decimal_fraction_v2(spec.min_leverage.value),
        *(decimal_fraction_v2(row.min_leverage) for row in covered),
    )
    upper = min(
        decimal_fraction_v2(policy.leverage_max),
        decimal_fraction_v2(spec.max_leverage.value),
        *(decimal_fraction_v2(row.max_leverage) for row in covered),
    )
    if lower > upper:
        c._reject_v2("RISK_V2_LEVERAGE_UNSATISFIABLE")
    try:
        first, last = zero_origin_lattice_v2(
            lower, upper, decimal_fraction_v2(spec.leverage_step.value)
        )
    except c.RiskV2Rejected as error:
        # Positive ordered inputs were validated above; INPUT_INVALID here is
        # the math leaf's empty-grid outcome, translated for this assessment.
        if error.code == "RISK_V2_INPUT_INVALID":
            c._reject_v2("RISK_V2_LEVERAGE_UNSATISFIABLE")
        raise
    mark_id = next(mark.reference.observation_id for mark in marks if mark.instrument == selected)
    return LinearTierBoundV2(
        selected,
        tiers.reference.observation_id,
        mark_id,
        finite_decimal_v2(upper_quote),
        tuple(row.tier_id for row in covered),
        finite_decimal_v2(lower),
        finite_decimal_v2(upper),
        spec.leverage_step.value,
        "LOCAL_ZERO_ORIGIN_LATTICE_V1",
        finite_decimal_v2(first),
        finite_decimal_v2(last),
        "LINEAR_MARK_GROSS_UPPER_BOUND_V1",
    )


@dataclass(frozen=True)
class MinimumAmountAssessmentV2:
    scope: c.RiskScopeV2
    reference: c.RiskComputationRefV2
    envelope_sha256: str
    instrument_revision: str
    rule_revision: str
    input_observation_ids: tuple[str, ...]
    evaluation_price: Decimal
    minimum_quote_amount: Decimal
    evaluated_quote_amount: Decimal
    valid_until: datetime

    def __post_init__(self) -> None:
        _minimum_payload_v2(self)


@dataclass(frozen=True)
class RiskRequirementsV2:
    source_mapping_revision: str
    minimum_amount_rule_revision: str
    cost_rule_revision: str
    fee_rule_revision: str
    funding_rule_revision: str
    stop_gap_model_revision: str
    tier_bound_rule_revision: Literal["LINEAR_MARK_GROSS_UPPER_BOUND_V1"]
    leverage_grid_rule: Literal["LOCAL_ZERO_ORIGIN_LATTICE_V1"]
    margin_model_revision: str
    liquidation_model_revision: str
    cost_assessment_basis: Literal[
        "POINT_ESTIMATE_WITH_RECONCILIATION_V1", "HARD_EXECUTION_FEE_UPPER_V1"
    ]
    margin_reserve_basis: Literal["POINT_ESTIMATE_TWO_IM_ENVELOPE_V1"]
    reserve_rounding_revision: Literal["APP_RESERVE_CEILING_QUANTUM_V1"]
    reserve_quantum_usdt: Decimal

    def __post_init__(self) -> None:
        _requirements_payload_v2(self)


@dataclass(frozen=True)
class LinearRiskBindingV2:
    scope: c.RiskScopeV2
    policy_sha256: str
    envelope_sha256: str
    sizing_sha256: str
    instrument_pin: c.InstrumentPinV2
    source_mapping_revision: str
    cost_calculation_id: str
    cost_allowance_sha256: str
    account_observation_id: str
    account_snapshot_sha256: str
    account_coverage_revision: str
    reservation_snapshot_id: str
    reservation_snapshot_sha256: str
    reservation_owner_generation: str
    mark_set_sha256: str
    valuation_sha256: str
    tier_observation_id: str
    tier_snapshot_sha256: str
    tier_bound_sha256: str
    risk_domain_sha256: str
    minimum_assessment_sha256: str
    requirements_sha256: str

    def __post_init__(self) -> None:
        _binding_payload_v2(self)


@dataclass(frozen=True)
class LinearRiskInputsV2:
    policy: c.LinearRiskPolicyV2
    proposal: c.LinearEntryProposalV2
    instrument: InstrumentObservation
    catalog_revision: str
    costs: c.LinearCostAllowanceV2
    account: c.LinearAccountRiskSnapshotV2
    reservations: c.LinearReservationSnapshotV2
    marks: tuple[c.LinearMarkQuoteV2, ...]
    tiers: LinearTierSnapshotV2
    risk_domain_sha256: str
    minimum_amount: MinimumAmountAssessmentV2
    requirements: RiskRequirementsV2

    def __post_init__(self) -> None:
        _inputs_payload_v2(self)


@dataclass(frozen=True)
class LinearEntryPreparationV2:
    sizing: c.LinearSizedEntryV2
    valuation: c.LinearAdmissionValuationV2
    tier_bound: LinearTierBoundV2
    minimum_amount: MinimumAmountAssessmentV2
    binding: LinearRiskBindingV2
    capacity_quote: Decimal
    estimated_loss_fraction_display: Decimal
    display_ratio_rule: Literal["RISK_V2_EXACT_FINITE_ELSE_CEILING80_V1"]

    def __post_init__(self) -> None:
        _preparation_payload_v2(self)


def minimum_assessment_sha256_v2(value: MinimumAmountAssessmentV2) -> str:
    return c._canonical_sha256_v2("MinimumAmountAssessmentV2", _minimum_payload_v2(value))


def requirements_sha256_v2(value: RiskRequirementsV2) -> str:
    return c._canonical_sha256_v2("RiskRequirementsV2", _requirements_payload_v2(value))


def binding_sha256_v2(binding: LinearRiskBindingV2) -> str:
    return c._canonical_sha256_v2("LinearRiskBindingV2", _binding_payload_v2(binding))


def _minimum_payload_v2(value: object) -> dict[str, object]:
    values = c._fields_v2(value, MinimumAmountAssessmentV2)
    payload: dict[str, object] = {
        "scope": c._scope_payload_v2(values["scope"]),
        "reference": c._computation_payload_v2(values["reference"]),
        "envelope_sha256": c._digest_v2(values["envelope_sha256"]),
        "instrument_revision": c._digest_v2(values["instrument_revision"]),
        "rule_revision": c._identifier_v2(values["rule_revision"]),
        "valid_until": c._time_text_v2(values["valid_until"]),
    }
    identifiers = c._tuple_v2(values["input_observation_ids"], minimum=1, maximum=1024)
    for identifier in identifiers:
        c._identifier_v2(identifier)
    if len(set(identifiers)) != len(identifiers):
        c._reject_v2()
    payload["input_observation_ids"] = identifiers
    for name in ("evaluation_price", "minimum_quote_amount", "evaluated_quote_amount"):
        payload[name] = c._decimal_text_field_v2(
            values, name, source=name != "evaluated_quote_amount"
        )
    reference = cast(c.RiskComputationRefV2, values["reference"])
    c._target_environment_v2(reference.target_environment, values["scope"])
    if reference.model_revision != values["rule_revision"]:
        c._reject_v2("RISK_V2_MINIMUM_RULE_UNVERIFIED")
    if c._utc_v2(values["valid_until"]) <= c._utc_v2(reference.computed_at):
        c._reject_v2("RISK_V2_CAUSAL_ORDER_INVALID")
    # The full-input reference hash is opaque here; it is not an envelope hash.
    return payload


def _requirements_payload_v2(value: object) -> dict[str, object]:
    values = c._fields_v2(value, RiskRequirementsV2)
    payload: dict[str, object] = {}
    for name in (
        "source_mapping_revision",
        "minimum_amount_rule_revision",
        "cost_rule_revision",
        "fee_rule_revision",
        "funding_rule_revision",
        "stop_gap_model_revision",
        "margin_model_revision",
        "liquidation_model_revision",
    ):
        payload[name] = c._identifier_v2(values[name])
    for name, choices in (
        ("tier_bound_rule_revision", ("LINEAR_MARK_GROSS_UPPER_BOUND_V1",)),
        ("leverage_grid_rule", ("LOCAL_ZERO_ORIGIN_LATTICE_V1",)),
        (
            "cost_assessment_basis",
            ("POINT_ESTIMATE_WITH_RECONCILIATION_V1", "HARD_EXECUTION_FEE_UPPER_V1"),
        ),
        ("margin_reserve_basis", ("POINT_ESTIMATE_TWO_IM_ENVELOPE_V1",)),
        ("reserve_rounding_revision", ("APP_RESERVE_CEILING_QUANTUM_V1",)),
    ):
        payload[name] = c._literal_v2(values[name], choices)
    payload["reserve_quantum_usdt"] = c._decimal_text_field_v2(
        values, "reserve_quantum_usdt", source=True
    )
    quantum = cast(Decimal, values["reserve_quantum_usdt"])
    if quantum.as_tuple() != (0, (1,), -8):
        c._reject_v2()
    return payload


def _binding_payload_v2(value: object) -> dict[str, object]:
    values = c._fields_v2(value, LinearRiskBindingV2)
    payload: dict[str, object] = {
        "scope": c._scope_payload_v2(values["scope"]),
        "instrument_pin": c._pin_payload_v2(values["instrument_pin"]),
    }
    for name in (
        "policy_sha256",
        "envelope_sha256",
        "sizing_sha256",
        "cost_allowance_sha256",
        "account_snapshot_sha256",
        "reservation_snapshot_sha256",
        "mark_set_sha256",
        "valuation_sha256",
        "tier_snapshot_sha256",
        "tier_bound_sha256",
        "risk_domain_sha256",
        "minimum_assessment_sha256",
        "requirements_sha256",
    ):
        payload[name] = c._digest_v2(values[name])
    for name in (
        "source_mapping_revision",
        "cost_calculation_id",
        "account_observation_id",
        "account_coverage_revision",
        "reservation_snapshot_id",
        "reservation_owner_generation",
        "tier_observation_id",
    ):
        payload[name] = c._identifier_v2(values[name])
    scope = cast(c.RiskScopeV2, values["scope"])
    pin = cast(c.InstrumentPinV2, values["instrument_pin"])
    if c._key_payload_v2(scope.instrument) != c._key_payload_v2(pin.key):
        c._reject_v2("RISK_V2_SCOPE_MISMATCH")
    return payload


def _inputs_payload_v2(value: object) -> dict[str, object]:
    values = c._fields_v2(value, LinearRiskInputsV2)
    # Validate every nested record before cross-record equality or lookup.
    payload: dict[str, object] = {
        "policy": c._policy_payload_v2(values["policy"]),
        "proposal": c._proposal_payload_v2(values["proposal"]),
        "costs": c._cost_payload_v2(values["costs"]),
        "account": c._account_snapshot_payload_v2(values["account"]),
        "reservations": c._reservation_snapshot_payload_v2(values["reservations"]),
        "marks": c._marks_payload_v2(values["marks"]),
        "tiers": _tier_snapshot_payload_v2(values["tiers"]),
        "risk_domain_sha256": c._digest_v2(values["risk_domain_sha256"]),
        "minimum_amount": _minimum_payload_v2(values["minimum_amount"]),
        "requirements": _requirements_payload_v2(values["requirements"]),
        "catalog_revision": c._identifier_v2(values["catalog_revision"]),
    }
    pin = c.pin_instrument_v2(values["instrument"], catalog_revision=values["catalog_revision"])
    payload["instrument_pin"] = c._pin_payload_v2(pin)
    return payload


def _preparation_payload_v2(value: object) -> dict[str, object]:
    values = c._fields_v2(value, LinearEntryPreparationV2)
    payload: dict[str, object] = {
        "sizing": c._sizing_payload_v2(values["sizing"]),
        "valuation": c._valuation_payload_v2(values["valuation"]),
        "tier_bound": _tier_bound_payload_v2(values["tier_bound"]),
        "minimum_amount": _minimum_payload_v2(values["minimum_amount"]),
        "binding": _binding_payload_v2(values["binding"]),
        "capacity_quote": c._decimal_text_field_v2(
            values, "capacity_quote", source=False, sign="nonnegative"
        ),
        "estimated_loss_fraction_display": c._decimal_text_field_v2(
            values, "estimated_loss_fraction_display", source=False, sign="nonnegative"
        ),
        "display_ratio_rule": c._literal_v2(
            values["display_ratio_rule"], ("RISK_V2_EXACT_FINITE_ELSE_CEILING80_V1",)
        ),
    }
    sizing = cast(c.LinearSizedEntryV2, values["sizing"])
    valuation = cast(c.LinearAdmissionValuationV2, values["valuation"])
    tier = cast(LinearTierBoundV2, values["tier_bound"])
    minimum = cast(MinimumAmountAssessmentV2, values["minimum_amount"])
    binding = cast(LinearRiskBindingV2, values["binding"])
    scope = c._scope_payload_v2(binding.scope)
    if (
        any(
            c._scope_payload_v2(item) != scope
            for item in (sizing.scope, valuation.scope, minimum.scope)
        )
        or tier.instrument != binding.scope.instrument
    ):
        c._reject_v2("RISK_V2_SCOPE_MISMATCH")
    if (
        binding.policy_sha256 != sizing.policy_sha256
        or binding.envelope_sha256 != c.envelope_sha256_v2(sizing.envelope)
        or binding.sizing_sha256 != c.sizing_sha256_v2(sizing)
        or binding.cost_calculation_id != sizing.cost_reference.calculation_id
        or binding.cost_allowance_sha256 != sizing.cost_allowance_sha256
        or binding.valuation_sha256 != c.valuation_sha256_v2(valuation)
        or binding.tier_bound_sha256 != tier_bound_sha256_v2(tier)
        or binding.minimum_assessment_sha256 != minimum_assessment_sha256_v2(minimum)
        or c._pin_payload_v2(binding.instrument_pin)
        != c._pin_payload_v2(sizing.envelope.instrument_pin)
        or minimum.envelope_sha256 != binding.envelope_sha256
        or minimum.instrument_revision != binding.instrument_pin.specification_revision
        or binding.account_observation_id != valuation.account_observation_id
        or binding.reservation_snapshot_id != valuation.reservation_snapshot_id
        or binding.tier_observation_id != tier.tier_observation_id
        or tier.mark_observation_id not in valuation.mark_observation_ids
    ):
        c._reject_v2("RISK_V2_SOURCE_MISMATCH")
    if decimal_fraction_v2(minimum.evaluated_quote_amount) != decimal_fraction_v2(
        sizing.envelope.base_quantity
    ) * decimal_fraction_v2(minimum.evaluation_price):
        c._reject_v2("RISK_V2_MINIMUM_RULE_UNVERIFIED")
    if decimal_fraction_v2(minimum.evaluated_quote_amount) < decimal_fraction_v2(
        minimum.minimum_quote_amount
    ):
        c._reject_v2("RISK_V2_MINIMUM_SIZE")
    if decimal_fraction_v2(values["capacity_quote"]) < decimal_fraction_v2(
        sizing.cost_allowance_quote
    ):
        c._reject_v2("RISK_V2_MARGIN_LIMIT")
    return payload
