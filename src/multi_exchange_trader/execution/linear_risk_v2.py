"""Exact non-authoritative Linear sizing and fresh-mark admission valuation.

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
    _fresh_v2,
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


def value_linear_admission_v2(
    *,
    policy: LinearRiskPolicyV2,
    sizing: LinearSizedEntryV2,
    account: c.LinearAccountRiskSnapshotV2,
    reservations: c.LinearReservationSnapshotV2,
    marks: tuple[c.LinearMarkQuoteV2, ...],
    now: datetime,
) -> c.LinearAdmissionValuationV2:
    # Validate all supplied records before cross-record keys, hashes or caps.
    policy_payload = c._policy_payload_v2(policy)
    sizing_payload = c._sizing_payload_v2(sizing)
    account_payload = c._account_snapshot_payload_v2(account)
    reservation_payload = c._reservation_snapshot_payload_v2(reservations)
    c._marks_payload_v2(marks)
    now = c._utc_v2(now)
    if (
        policy_payload["scope"] != sizing_payload["scope"]
        or c._account_payload_v2(policy.scope.product_scope.account) != account_payload["account"]
        or account_payload["account"] != reservation_payload["account"]
    ):
        raise RiskV2Rejected("RISK_V2_SCOPE_MISMATCH")
    if (
        sizing.policy_sha256 != c.risk_policy_sha256_v2(policy)
        or sizing.envelope.policy_revision != policy.revision
    ):
        raise RiskV2Rejected("RISK_V2_REVISION_MISMATCH")
    account_ids = {item.component_id for item in account.components}
    if account_ids.intersection(item.component_id for item in reservations.components):
        raise RiskV2Rejected("RISK_V2_EXPOSURE_OVERLAP")
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
    maximum = _fraction_v2(policy.max_evidence_age_seconds)
    for observed in (
        account.reference.server_observed_at,
        account.reference.received_at,
        reservations.observed_at,
        sizing.cost_reference.computed_at,
        sizing.envelope.instrument_pin.response_time,
        sizing.envelope.instrument_pin.received_at,
    ):
        _fresh_v2(observed, now=now, maximum=maximum)
    if c._utc_v2(sizing.cost_reference.computed_at) < c._utc_v2(
        sizing.envelope.instrument_pin.received_at
    ):
        raise RiskV2Rejected("RISK_V2_CAUSAL_ORDER_INVALID")
    if now >= c._utc_v2(sizing.envelope.valid_until):
        raise RiskV2Rejected("RISK_V2_EXPIRED")
    # assessed_at is an audit instant, not a source or economic completion age.
    account_gross = c._gross_components_v2(account.components, prices=prices)
    reserved_gross = c._gross_components_v2(reservations.components, prices=prices)
    quantity = _fraction_v2(sizing.envelope.base_quantity)
    proposed = quantity * _fraction_v2(prices[policy.scope.instrument])
    combined = _fraction_v2(account_gross) + _fraction_v2(reserved_gross) + proposed
    if proposed > _fraction_v2(policy.caps.max_order_admission_notional):
        raise RiskV2Rejected("RISK_V2_ORDER_ADMISSION_LIMIT")
    if combined > _fraction_v2(policy.caps.max_account_admission_notional):
        raise RiskV2Rejected("RISK_V2_ACCOUNT_ADMISSION_LIMIT")
    if policy.caps.turnover_requirement == "HARD_EXECUTION_QUOTE_CAP":
        if sizing.envelope.side == "SELL":
            raise RiskV2Rejected("RISK_V2_SELL_EXECUTION_UPPER_UNAVAILABLE")
        # The validated HARD policy necessarily carries its explicit cap.
        cap = policy.caps.max_execution_quote_amount
        assert cap is not None
        if quantity * _fraction_v2(sizing.envelope.limit_price) > _fraction_v2(cap):
            raise RiskV2Rejected("RISK_V2_EXECUTION_QUOTE_LIMIT")
    return c.LinearAdmissionValuationV2(
        policy.scope,
        now,
        account.reference.observation_id,
        reservations.snapshot_id,
        tuple(mark.reference.observation_id for mark in marks),
        account_gross,
        reserved_gross,
        finite_decimal_v2(proposed),
        finite_decimal_v2(combined),
        policy.caps.valuation_rule,
    )


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
