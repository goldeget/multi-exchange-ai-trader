"""Exact non-authoritative fixed-limit Linear sizing.

Catalog selection and actual source/model/owner qualification belong to the
producer. This calculation grants no minimum-amount or submission eligibility.
"""

from datetime import datetime
from decimal import Decimal
from fractions import Fraction

from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.risk_v2_contracts import (
    FixedLimitEnvelopeV2,
    LinearCostAllowanceV2,
    LinearEntryProposalV2,
    LinearRiskPolicyV2,
    LinearSizedEntryV2,
    RiskV2Rejected,
)
from multi_exchange_trader.execution.risk_v2_math import (
    decimal_fraction_v2,
    finite_decimal_v2,
    floor_quantity_v2,
)
from multi_exchange_trader.market_data.instrument_definition import (
    InstrumentObservation,
    LinearInstrumentSpec,
)


def _fresh_v2(observed: datetime, *, now: datetime, maximum: Fraction) -> None:
    observed = c._utc_v2(observed)
    if observed > now:
        raise RiskV2Rejected("RISK_V2_FUTURE_EVIDENCE")
    age = now - observed
    microseconds = (age.days * 86400 + age.seconds) * 1_000_000 + age.microseconds
    if microseconds > maximum * 1_000_000:
        raise RiskV2Rejected("RISK_V2_STALE_EVIDENCE")


def _fraction_v2(value: Decimal) -> Fraction:
    return decimal_fraction_v2(value)


def size_linear_entry_v2(
    *,
    policy: LinearRiskPolicyV2,
    proposal: LinearEntryProposalV2,
    instrument: InstrumentObservation,
    catalog_revision: str,
    costs: LinearCostAllowanceV2,
    now: datetime,
) -> LinearSizedEntryV2:
    # Closed Task1 validators revalidate exact full records and nested values
    # before cross-record equality, attributes, hashes or quantity predicates.
    policy_hash = c.risk_policy_sha256_v2(policy)
    proposal_payload = c._proposal_payload_v2(proposal)
    cost_hash = c.cost_allowance_sha256_v2(costs)
    pin = c.pin_instrument_v2(instrument, catalog_revision=catalog_revision)
    now = c._utc_v2(now)
    if (
        c._scope_payload_v2(policy.scope) != proposal_payload["scope"]
        or c._scope_payload_v2(policy.scope) != c._scope_payload_v2(costs.scope)
        or policy.scope.instrument != pin.key
    ):
        raise RiskV2Rejected("RISK_V2_SCOPE_MISMATCH")
    if (
        costs.policy_sha256 != policy_hash
        or costs.instrument_revision != pin.specification_revision
    ):
        raise RiskV2Rejected("RISK_V2_REVISION_MISMATCH")
    if (
        costs.side != proposal.side
        or costs.entry_limit_price != proposal.entry.limit_price
        or costs.stop_price != proposal.stop_price
        or costs.origin_quote_id != proposal.entry.origin_quote_id
    ):
        raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")

    maximum = _fraction_v2(policy.max_evidence_age_seconds)
    for observed in (
        pin.response_time,
        pin.received_at,
        costs.fee_reference.server_observed_at,
        costs.fee_reference.received_at,
        costs.funding_reference.server_observed_at,
        costs.funding_reference.received_at,
        costs.reference.computed_at,
    ):
        _fresh_v2(observed, now=now, maximum=maximum)
    if c._utc_v2(costs.reference.computed_at) < c._utc_v2(pin.received_at):
        raise RiskV2Rejected("RISK_V2_CAUSAL_ORDER_INVALID")
    for expiry in (
        proposal.entry.valid_until,
        costs.valid_until,
        costs.next_funding_at,
        costs.holding_until,
    ):
        if now >= c._utc_v2(expiry):
            raise RiskV2Rejected("RISK_V2_EXPIRED")

    # The shared observation validator establishes this exact spec class and
    # USDT LinearPerpetual/Trading/source/native/raw-row integrity.
    spec = instrument.spec
    if type(spec) is not LinearInstrumentSpec:
        raise RiskV2Rejected("RISK_V2_UNSUPPORTED_PRODUCT")
    limit = _fraction_v2(proposal.entry.limit_price)
    stop = _fraction_v2(proposal.stop_price)
    tick = _fraction_v2(spec.price_tick.value)
    if (limit / tick).denominator != 1 or (stop / tick).denominator != 1:
        raise RiskV2Rejected("RISK_V2_INVALID_PRICE_TICK")
    if limit < _fraction_v2(spec.min_price.value) or limit > _fraction_v2(spec.max_price.value):
        raise RiskV2Rejected("RISK_V2_INVALID_PRICE_TICK")
    distance = limit - stop if proposal.side == "LONG" else stop - limit
    # Task1 already rejects an adverse-stop inversion during revalidation.
    fee = _fraction_v2(costs.fee_quote_per_base)
    funding = _fraction_v2(costs.funding_quote_per_base)
    unit_cost = fee + funding + _fraction_v2(costs.stop_gap_quote_per_base)
    unit_loss = distance + unit_cost
    quantity = floor_quantity_v2(
        _fraction_v2(policy.per_trade_loss), unit_loss, _fraction_v2(spec.quantity_step.value)
    )
    if quantity < _fraction_v2(spec.min_quantity.value):
        raise RiskV2Rejected("RISK_V2_MINIMUM_SIZE")
    if quantity > _fraction_v2(spec.max_limit_quantity.value):
        raise RiskV2Rejected("RISK_V2_MAXIMUM_SIZE")
    envelope = FixedLimitEnvelopeV2(
        2,
        proposal.scope,
        pin,
        policy.revision,
        policy_hash,
        proposal.entry.side,
        finite_decimal_v2(quantity),
        proposal.entry.limit_price,
        "LIMIT",
        "IOC",
        "ENTRY",
        False,
        proposal.entry.origin_quote_id,
        proposal.entry.valid_until,
    )
    return LinearSizedEntryV2(
        proposal.scope,
        now,
        policy_hash,
        envelope,
        costs.reference,
        cost_hash,
        proposal.stop_price,
        finite_decimal_v2(distance),
        finite_decimal_v2(unit_cost),
        finite_decimal_v2(unit_loss),
        finite_decimal_v2(quantity * unit_loss),
        finite_decimal_v2(quantity * unit_cost),
        finite_decimal_v2(quantity * funding),
        "LINEAR_FIXED_LIMIT_LOSS_SIZE_V2",
    )
