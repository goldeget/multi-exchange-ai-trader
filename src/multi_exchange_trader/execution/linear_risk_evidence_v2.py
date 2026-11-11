"""Non-authoritative Linear tier range observations and local grid bounds."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from math import gcd
from typing import Literal, cast

from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.risk_v2_math import (
    decimal_fraction_v2,
    finite_decimal_v2,
    is_zero_origin_member_v2,
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
class LinearLastTradeQuoteV2:
    """Typed last-trade price evidence; its owner must prove original-source currentness."""

    instrument: InstrumentKey
    reference: c.RiskObservationRefV2
    last_price: Decimal

    def __post_init__(self) -> None:
        _last_trade_quote_payload_v2(self)


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
    last_trade_quote: LinearLastTradeQuoteV2

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


def _last_trade_quote_payload_v2(value: object) -> dict[str, object]:
    values = c._fields_v2(value, LinearLastTradeQuoteV2)
    return {
        "instrument": c._key_payload_v2(values["instrument"]),
        "reference": c._observation_payload_v2(values["reference"]),
        "last_price": c._decimal_text_field_v2(values, "last_price", source=True, sign="positive"),
    }


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
        "last_trade_quote": _last_trade_quote_payload_v2(values["last_trade_quote"]),
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


@dataclass(frozen=True)
class ExactRiskRatioV2:
    numerator: Decimal
    denominator: Decimal

    def __post_init__(self) -> None:
        _ratio_payload_v2(self)


@dataclass(frozen=True)
class LiquidationPositiveRootV2:
    kind: Literal["POSITIVE_ROOT"]
    exact_price: ExactRiskRatioV2
    candidate_tier_id: str
    domain_upper_quote: Decimal
    full_table_sha256: str
    raw_manifest_sha256: str
    consumed_tier_ids: tuple[str, ...]
    rule_revision: str

    def __post_init__(self) -> None:
        _positive_root_payload_v2(self)


@dataclass(frozen=True)
class LiquidationNoPositiveRootV2:
    kind: Literal["NO_POSITIVE_ROOT"]
    reason: Literal["NON_POSITIVE_LONG_INTERCEPT"]
    domain_upper_quote: Decimal
    full_table_sha256: str
    raw_manifest_sha256: str
    consumed_tier_ids: tuple[str, ...]
    rule_revision: str
    model_input_sha256: str
    proof_sha256: str

    def __post_init__(self) -> None:
        _no_positive_root_payload_v2(self)


@dataclass(frozen=True)
class LinearMarginAssessmentV2:
    binding_sha256: str
    reference: c.RiskComputationRefV2
    model_revision: str
    quantity: Decimal
    leverage: Decimal
    leverage_grid_rule: Literal["LOCAL_ZERO_ORIGIN_LATTICE_V1"]
    selection_rule: Literal["SMALLEST_FEASIBLE_PERMITTED_GRID_V1"]
    initial_margin_quote: Decimal
    venue_required_total_reserve_quote: Decimal
    covered_tier_ids: tuple[str, ...]
    valid_until: datetime
    reserve_basis: Literal["POINT_ESTIMATE_TWO_IM_ENVELOPE_V1"]
    reserve_rounding_revision: Literal["APP_RESERVE_CEILING_QUANTUM_V1"]
    reserve_quantum_usdt: Decimal
    model_input_sha256: str
    exact_calculation_sha256: str

    def __post_init__(self) -> None:
        _margin_payload_v2(self)


@dataclass(frozen=True)
class LinearLiquidationAssessmentV2:
    binding_sha256: str
    margin_assessment_sha256: str
    reference: c.RiskComputationRefV2
    model_revision: str
    quantity: Decimal
    leverage: Decimal
    side: Literal["LONG", "SHORT"]
    entry_limit_price: Decimal
    margin_mode: Literal["ISOLATED", "CROSS"]
    position_mode: Literal["ONE_WAY"]
    covered_tier_ids: tuple[str, ...]
    outcome: LiquidationPositiveRootV2 | LiquidationNoPositiveRootV2
    valid_until: datetime
    model_input_sha256: str
    exact_calculation_sha256: str

    def __post_init__(self) -> None:
        _liquidation_payload_v2(self)


@dataclass(frozen=True)
class LinearLeverageReadbackV2:
    binding_sha256: str
    reference: c.RiskObservationRefV2
    account_observation_id: str
    control_command_key: str
    after_control_at: datetime
    side: Literal["LONG", "SHORT"]
    applied_leverage: Decimal
    margin_mode: Literal["ISOLATED", "CROSS"]
    position_mode: Literal["ONE_WAY"]

    def __post_init__(self) -> None:
        _readback_payload_v2(self)


@dataclass(frozen=True)
class LinearEntryAssessmentV2:
    stage: Literal["PLAN", "SUBMIT"]
    assessed_at: datetime
    preparation: LinearEntryPreparationV2
    margin: LinearMarginAssessmentV2 | None
    liquidation: LinearLiquidationAssessmentV2 | None
    leverage_readback: LinearLeverageReadbackV2 | None
    required_reservation_quote: Decimal | None
    blockers: tuple[str, ...]
    formula_version: Literal["LINEAR_FIXED_LIMIT_MARK_ADMISSION_V2"]
    liquidation_buffer_result: Literal["GAP_SATISFIED", "MODEL_NO_POSITIVE_TRIGGER"] | None

    def __post_init__(self) -> None:
        _assessment_payload_v2(self)


def margin_assessment_sha256_v2(value: LinearMarginAssessmentV2) -> str:
    return c._canonical_sha256_v2("LinearMarginAssessmentV2", _margin_payload_v2(value))


def _ratio_payload_v2(value: object) -> dict[str, object]:
    values = c._fields_v2(value, ExactRiskRatioV2)
    payload = {
        name: c._decimal_text_field_v2(
            values, name, source=False, sign="nonnegative" if name == "numerator" else "positive"
        )
        for name in ("numerator", "denominator")
    }
    for name in ("numerator", "denominator"):
        parts = cast(Decimal, values[name]).as_tuple()
        if parts.sign != 0 or parts.exponent != 0:
            c._reject_v2()
    numerator = decimal_fraction_v2(values["numerator"]).numerator
    denominator = decimal_fraction_v2(values["denominator"]).numerator
    if gcd(numerator, denominator) != 1:
        c._reject_v2()
    return dict(payload)


def _ordered_tier_ids_v2(value: object) -> tuple[str, ...]:
    identifiers = c._tuple_v2(value, minimum=1, maximum=1024)
    for identifier in identifiers:
        c._identifier_v2(identifier)
    if len(set(identifiers)) != len(identifiers):
        c._reject_v2()
    return cast(tuple[str, ...], identifiers)


def _root_domain_payload_v2(values: dict[str, object]) -> dict[str, object]:
    return {
        "domain_upper_quote": c._decimal_text_field_v2(values, "domain_upper_quote", source=False),
        "full_table_sha256": c._digest_v2(values["full_table_sha256"]),
        "raw_manifest_sha256": c._digest_v2(values["raw_manifest_sha256"]),
        "consumed_tier_ids": _ordered_tier_ids_v2(values["consumed_tier_ids"]),
        "rule_revision": c._identifier_v2(values["rule_revision"]),
    }


def _positive_root_payload_v2(value: object) -> dict[str, object]:
    values = c._fields_v2(value, LiquidationPositiveRootV2)
    payload = {
        "kind": c._literal_v2(values["kind"], ("POSITIVE_ROOT",)),
        "exact_price": _ratio_payload_v2(values["exact_price"]),
        "candidate_tier_id": c._identifier_v2(values["candidate_tier_id"]),
        **_root_domain_payload_v2(values),
    }
    price = cast(ExactRiskRatioV2, values["exact_price"])
    if decimal_fraction_v2(price.numerator) <= 0 or values["candidate_tier_id"] not in cast(
        tuple[str, ...], values["consumed_tier_ids"]
    ):
        c._reject_v2()
    return payload


def _no_positive_root_payload_v2(value: object) -> dict[str, object]:
    values = c._fields_v2(value, LiquidationNoPositiveRootV2)
    return {
        "kind": c._literal_v2(values["kind"], ("NO_POSITIVE_ROOT",)),
        "reason": c._literal_v2(values["reason"], ("NON_POSITIVE_LONG_INTERCEPT",)),
        **_root_domain_payload_v2(values),
        "model_input_sha256": c._digest_v2(values["model_input_sha256"]),
        "proof_sha256": c._digest_v2(values["proof_sha256"]),
    }


def _margin_payload_v2(value: object) -> dict[str, object]:
    values = c._fields_v2(value, LinearMarginAssessmentV2)
    payload: dict[str, object] = {
        name: c._digest_v2(values[name])
        for name in ("binding_sha256", "model_input_sha256", "exact_calculation_sha256")
    }
    payload["reference"] = c._computation_payload_v2(values["reference"])
    payload["model_revision"] = c._identifier_v2(values["model_revision"])
    payload["covered_tier_ids"] = _ordered_tier_ids_v2(values["covered_tier_ids"])
    for name in (
        "quantity",
        "leverage",
        "initial_margin_quote",
        "venue_required_total_reserve_quote",
        "reserve_quantum_usdt",
    ):
        payload[name] = c._decimal_text_field_v2(
            values, name, source=name == "reserve_quantum_usdt"
        )
    for name, choices in (
        ("leverage_grid_rule", ("LOCAL_ZERO_ORIGIN_LATTICE_V1",)),
        ("selection_rule", ("SMALLEST_FEASIBLE_PERMITTED_GRID_V1",)),
        ("reserve_basis", ("POINT_ESTIMATE_TWO_IM_ENVELOPE_V1",)),
        ("reserve_rounding_revision", ("APP_RESERVE_CEILING_QUANTUM_V1",)),
    ):
        payload[name] = c._literal_v2(values[name], choices)
    payload["valid_until"] = c._time_text_v2(values["valid_until"])
    quantum = cast(Decimal, values["reserve_quantum_usdt"])
    if quantum.as_tuple() != (0, (1,), -8):
        c._reject_v2()
    for name in ("initial_margin_quote", "venue_required_total_reserve_quote"):
        if (decimal_fraction_v2(values[name]) / decimal_fraction_v2(quantum)).denominator != 1:
            c._reject_v2()
    reference = cast(c.RiskComputationRefV2, values["reference"])
    if (
        reference.model_revision != values["model_revision"]
        or reference.input_sha256 != values["binding_sha256"]
    ):
        c._reject_v2("RISK_V2_MARGIN_UNVERIFIED")
    if c._utc_v2(values["valid_until"]) <= c._utc_v2(reference.computed_at):
        c._reject_v2("RISK_V2_CAUSAL_ORDER_INVALID")
    return payload


def _liquidation_payload_v2(value: object) -> dict[str, object]:
    values = c._fields_v2(value, LinearLiquidationAssessmentV2)
    payload: dict[str, object] = {
        name: c._digest_v2(values[name])
        for name in (
            "binding_sha256",
            "margin_assessment_sha256",
            "model_input_sha256",
            "exact_calculation_sha256",
        )
    }
    payload["reference"] = c._computation_payload_v2(values["reference"])
    payload["model_revision"] = c._identifier_v2(values["model_revision"])
    for name in ("quantity", "leverage", "entry_limit_price"):
        payload[name] = c._decimal_text_field_v2(values, name, source=name == "entry_limit_price")
    for name, choices in (
        ("side", ("LONG", "SHORT")),
        ("margin_mode", ("ISOLATED", "CROSS")),
        ("position_mode", ("ONE_WAY",)),
    ):
        payload[name] = c._literal_v2(values[name], choices)
    payload["covered_tier_ids"] = _ordered_tier_ids_v2(values["covered_tier_ids"])
    outcome = values["outcome"]
    if type(outcome) is LiquidationPositiveRootV2:
        payload["outcome"] = _positive_root_payload_v2(outcome)
    elif type(outcome) is LiquidationNoPositiveRootV2:
        payload["outcome"] = _no_positive_root_payload_v2(outcome)
    else:
        c._reject_v2()
    payload["valid_until"] = c._time_text_v2(values["valid_until"])
    if not set(values["covered_tier_ids"]).issubset(outcome.consumed_tier_ids):
        c._reject_v2("RISK_V2_LIQUIDATION_UNVERIFIED")
    if type(outcome) is LiquidationNoPositiveRootV2:
        if (
            values["side"] != "LONG"
            or values["margin_mode"] != "ISOLATED"
            or values["position_mode"] != "ONE_WAY"
        ):
            c._reject_v2("RISK_V2_UNSUPPORTED_MODE")
        if outcome.model_input_sha256 != values["model_input_sha256"]:
            c._reject_v2("RISK_V2_LIQUIDATION_UNVERIFIED")
    reference = cast(c.RiskComputationRefV2, values["reference"])
    if (
        reference.model_revision != values["model_revision"]
        or reference.input_sha256 != values["margin_assessment_sha256"]
    ):
        c._reject_v2("RISK_V2_LIQUIDATION_UNVERIFIED")
    if c._utc_v2(values["valid_until"]) <= c._utc_v2(reference.computed_at):
        c._reject_v2("RISK_V2_CAUSAL_ORDER_INVALID")
    return payload


def _readback_payload_v2(value: object) -> dict[str, object]:
    values = c._fields_v2(value, LinearLeverageReadbackV2)
    payload: dict[str, object] = {
        "binding_sha256": c._digest_v2(values["binding_sha256"]),
        "reference": c._observation_payload_v2(values["reference"]),
        "account_observation_id": c._identifier_v2(values["account_observation_id"]),
        "control_command_key": c._identifier_v2(values["control_command_key"]),
        "after_control_at": c._time_text_v2(values["after_control_at"]),
        "applied_leverage": c._decimal_text_field_v2(values, "applied_leverage", source=True),
    }
    for name, choices in (
        ("side", ("LONG", "SHORT")),
        ("margin_mode", ("ISOLATED", "CROSS")),
        ("position_mode", ("ONE_WAY",)),
    ):
        payload[name] = c._literal_v2(values[name], choices)
    reference = cast(c.RiskObservationRefV2, values["reference"])
    if reference.source_environment not in ("DEMO", "LIVE", "FIXTURE"):
        c._reject_v2("RISK_V2_SOURCE_MISMATCH")
    if c._utc_v2(values["after_control_at"]) > c._utc_v2(reference.server_observed_at):
        c._reject_v2("RISK_V2_CAUSAL_ORDER_INVALID")
    return payload


def _assessment_links_v2(
    preparation: LinearEntryPreparationV2,
    margin: LinearMarginAssessmentV2 | None,
    liquidation: LinearLiquidationAssessmentV2 | None,
    readback: LinearLeverageReadbackV2 | None,
) -> None:
    # Callers validate the complete records before this cross-record phase.
    if margin is None:
        if liquidation is not None or readback is not None:
            c._reject_v2()
        return
    binding_hash = binding_sha256_v2(preparation.binding)
    sizing, bound = preparation.sizing, preparation.tier_bound
    side = "LONG" if sizing.envelope.side == "BUY" else "SHORT"
    if (
        margin.binding_sha256 != binding_hash
        or margin.quantity != sizing.envelope.base_quantity
        or margin.covered_tier_ids != bound.covered_tier_ids
    ):
        c._reject_v2("RISK_V2_MARGIN_UNVERIFIED")
    if margin.leverage_grid_rule != bound.leverage_grid_rule:
        c._reject_v2("RISK_V2_GRID_SOURCE_MODEL_UNVERIFIED")
    if not is_zero_origin_member_v2(
        decimal_fraction_v2(margin.leverage),
        first=decimal_fraction_v2(bound.permitted_leverage_min),
        last=decimal_fraction_v2(bound.permitted_leverage_max),
        step=decimal_fraction_v2(bound.leverage_step),
    ):
        c._reject_v2("RISK_V2_LEVERAGE_UNSATISFIABLE")
    if liquidation is not None and (
        liquidation.binding_sha256 != binding_hash
        or liquidation.margin_assessment_sha256 != margin_assessment_sha256_v2(margin)
        or liquidation.quantity != margin.quantity
        or liquidation.leverage != margin.leverage
        or liquidation.side != side
        or liquidation.entry_limit_price != sizing.envelope.limit_price
        or liquidation.covered_tier_ids != bound.covered_tier_ids
        or liquidation.model_input_sha256 != margin.model_input_sha256
        or liquidation.exact_calculation_sha256 != margin.exact_calculation_sha256
    ):
        c._reject_v2("RISK_V2_LIQUIDATION_UNVERIFIED")
    if readback is not None and (
        readback.binding_sha256 != binding_hash
        or readback.side != side
        or readback.applied_leverage != margin.leverage
        or readback.account_observation_id != preparation.binding.account_observation_id
        or (
            liquidation is not None
            and (
                readback.margin_mode != liquidation.margin_mode
                or readback.position_mode != liquidation.position_mode
            )
        )
    ):
        c._reject_v2("RISK_V2_LEVERAGE_READBACK_UNVERIFIED")


def _assessment_payload_v2(value: object) -> dict[str, object]:
    values = c._fields_v2(value, LinearEntryAssessmentV2)
    payload: dict[str, object] = {
        "stage": c._literal_v2(values["stage"], ("PLAN", "SUBMIT")),
        "assessed_at": c._time_text_v2(values["assessed_at"]),
        "preparation": _preparation_payload_v2(values["preparation"]),
        "formula_version": c._literal_v2(
            values["formula_version"], ("LINEAR_FIXED_LIMIT_MARK_ADMISSION_V2",)
        ),
    }
    for name, validator in (
        ("margin", _margin_payload_v2),
        ("liquidation", _liquidation_payload_v2),
        ("leverage_readback", _readback_payload_v2),
    ):
        payload[name] = None if values[name] is None else validator(values[name])
    reserve = values["required_reservation_quote"]
    payload["required_reservation_quote"] = (
        None
        if reserve is None
        else c._decimal_text_field_v2(
            values, "required_reservation_quote", source=False, sign="nonnegative"
        )
    )
    buffer_result = values["liquidation_buffer_result"]
    payload["liquidation_buffer_result"] = (
        None
        if buffer_result is None
        else c._literal_v2(buffer_result, ("GAP_SATISFIED", "MODEL_NO_POSITIVE_TRIGGER"))
    )
    blockers = c._tuple_v2(values["blockers"], minimum=0, maximum=3)
    for blocker in blockers:
        c._literal_v2(
            blocker,
            (
                "RISK_V2_MARGIN_UNVERIFIED",
                "RISK_V2_LIQUIDATION_UNVERIFIED",
                "RISK_V2_LEVERAGE_READBACK_UNVERIFIED",
                "RISK_V2_PLANNING_ONLY",
            ),
        )
    payload["blockers"] = blockers
    preparation = cast(LinearEntryPreparationV2, values["preparation"])
    margin = cast(LinearMarginAssessmentV2 | None, values["margin"])
    liquidation = cast(LinearLiquidationAssessmentV2 | None, values["liquidation"])
    readback = cast(LinearLeverageReadbackV2 | None, values["leverage_readback"])
    _assessment_links_v2(preparation, margin, liquidation, readback)
    assessed_at = c._utc_v2(values["assessed_at"])
    pin = preparation.binding.instrument_pin
    moments = [
        pin.response_time,
        pin.received_at,
        preparation.sizing.cost_reference.computed_at,
        preparation.minimum_amount.reference.computed_at,
    ]
    expiries = [preparation.sizing.envelope.valid_until, preparation.minimum_amount.valid_until]
    for model in (margin, liquidation):
        if model is not None:
            c._target_environment_v2(model.reference.target_environment, preparation.binding.scope)
            moments.append(model.reference.computed_at)
            expiries.append(model.valid_until)
    if readback is not None:
        c._target_environment_v2(readback.reference.source_environment, preparation.binding.scope)
        moments.extend((readback.reference.server_observed_at, readback.reference.received_at))
    if any(c._utc_v2(moment) > assessed_at for moment in moments):
        c._reject_v2("RISK_V2_FUTURE_EVIDENCE")
    if any(assessed_at >= c._utc_v2(expiry) for expiry in expiries):
        c._reject_v2("RISK_V2_EXPIRED")
    if margin is not None and any(
        c._utc_v2(moment) > c._utc_v2(margin.reference.computed_at)
        for moment in (
            pin.received_at,
            preparation.sizing.cost_reference.computed_at,
            preparation.minimum_amount.reference.computed_at,
        )
    ):
        c._reject_v2("RISK_V2_CAUSAL_ORDER_INVALID")
    if liquidation is not None and margin is not None:
        if c._utc_v2(liquidation.reference.computed_at) < c._utc_v2(margin.reference.computed_at):
            c._reject_v2("RISK_V2_CAUSAL_ORDER_INVALID")
        if type(liquidation.outcome) is LiquidationPositiveRootV2:
            price = decimal_fraction_v2(
                liquidation.outcome.exact_price.numerator
            ) / decimal_fraction_v2(liquidation.outcome.exact_price.denominator)
            stop = decimal_fraction_v2(preparation.sizing.stop_price)
            gap = stop - price if liquidation.side == "LONG" else price - stop
            if gap <= 0:
                c._reject_v2("RISK_V2_LIQUIDATION_BUFFER")
    missing = tuple(
        code
        for item, code in (
            (margin, "RISK_V2_MARGIN_UNVERIFIED"),
            (liquidation, "RISK_V2_LIQUIDATION_UNVERIFIED"),
            (readback, "RISK_V2_LEVERAGE_READBACK_UNVERIFIED"),
        )
        if item is None
    )
    if values["stage"] == "SUBMIT" and missing:
        c._reject_v2(missing[0])
    expected = (missing or ("RISK_V2_PLANNING_ONLY",)) if values["stage"] == "PLAN" else ()
    if blockers != expected:
        c._reject_v2()
    if margin is None:
        if reserve is not None:
            c._reject_v2()
    else:
        required = max(
            decimal_fraction_v2(margin.initial_margin_quote)
            + decimal_fraction_v2(preparation.sizing.cost_allowance_quote),
            decimal_fraction_v2(margin.venue_required_total_reserve_quote),
        )
        if reserve is None or decimal_fraction_v2(reserve) != required:
            c._reject_v2()
        if required > decimal_fraction_v2(preparation.capacity_quote):
            c._reject_v2("RISK_V2_MARGIN_LIMIT")
    expected_buffer = (
        None
        if liquidation is None
        else (
            "GAP_SATISFIED"
            if type(liquidation.outcome) is LiquidationPositiveRootV2
            else "MODEL_NO_POSITIVE_TRIGGER"
        )
    )
    if buffer_result != expected_buffer:
        c._reject_v2()
    return payload
