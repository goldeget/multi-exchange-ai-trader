"""Non-authoritative Linear tier range observations and local grid bounds."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from typing import Literal

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
