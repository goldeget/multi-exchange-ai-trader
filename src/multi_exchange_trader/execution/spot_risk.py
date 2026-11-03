"""Exact, non-authoritative SPOT cash BUY and owned-base SELL arithmetic.

All amounts are explicit already-projected plans/aggregates in the named base
or quote currency. Their values do not authenticate ownership, fee completeness
or currency, account-wide no-debt coverage, freshness, or protection receipts.
Raw wallet balances are not owned lots; unknown/third-currency fees cannot be
replaced with zero. E16 must separately establish those facts and the current
SPOT protection gate before any runtime admission. A result grants no entry,
ledger, history, or execution qualification.
"""

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import cast

from multi_exchange_trader.execution.risk_v2_math import (
    RiskV2Rejected,
    _multiply,
    decimal_fraction_v2,
    finite_decimal_v2,
)


def _amount(value: object, *, positive: bool, source: bool = False) -> Fraction:
    if type(value) is not Decimal or not value.is_finite():
        raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
    parts = value.as_tuple()
    exponent = cast(int, parts.exponent)
    if source and (
        len(parts.digits) > 256
        or abs(exponent) > 1000
        or abs(exponent + len(parts.digits) - 1) > 1000
    ):
        raise RiskV2Rejected("RISK_V2_RESOURCE_LIMIT")
    number = decimal_fraction_v2(value)
    if number < 0 or (positive and number == 0):
        raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
    return number


@dataclass(frozen=True)
class SpotCashRequirementV2:
    quote_debit: Decimal
    remaining_quote: Decimal

    def __post_init__(self) -> None:
        if type(self) is not SpotCashRequirementV2:
            raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
        _amount(getattr(self, "quote_debit", None), positive=True)
        _amount(getattr(self, "remaining_quote", None), positive=False)


@dataclass(frozen=True)
class SpotBaseRequirementV2:
    base_debit: Decimal
    remaining_owned_base: Decimal

    def __post_init__(self) -> None:
        if type(self) is not SpotBaseRequirementV2:
            raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
        _amount(getattr(self, "base_debit", None), positive=True)
        _amount(getattr(self, "remaining_owned_base", None), positive=False)


def check_spot_cash_buy_v2(
    *,
    quantity: Decimal,
    buy_ceiling: Decimal,
    fee_quote_upper: Decimal,
    available_quote: Decimal,
    held_quote: Decimal,
    max_order_quote_debit: Decimal,
    max_total_quote_commitment: Decimal,
) -> SpotCashRequirementV2:
    """Compare explicit quote debit and all quote holds against supplied limits."""
    quantity_value = _amount(quantity, positive=True)
    ceiling = _amount(buy_ceiling, positive=True, source=True)
    fee = _amount(fee_quote_upper, positive=False)
    available = _amount(available_quote, positive=False)
    held = _amount(held_quote, positive=False)
    order_cap = _amount(max_order_quote_debit, positive=True, source=True)
    total_cap = _amount(max_total_quote_commitment, positive=True, source=True)
    debit = _multiply(quantity_value, ceiling) + fee
    debit_decimal = finite_decimal_v2(debit)
    commitment = held + debit
    if debit > order_cap:
        raise RiskV2Rejected("RISK_V2_EXECUTION_QUOTE_LIMIT")
    if commitment > total_cap:
        raise RiskV2Rejected("RISK_V2_ACCOUNT_ADMISSION_LIMIT")
    if commitment > available:
        raise RiskV2Rejected("RISK_V2_MARGIN_LIMIT")
    return SpotCashRequirementV2(debit_decimal, finite_decimal_v2(available - commitment))


def check_spot_owned_sell_v2(
    *,
    quantity: Decimal,
    owned_available_base: Decimal,
    held_base: Decimal,
    fee_base_upper: Decimal,
) -> SpotBaseRequirementV2:
    """Consume only explicit owned base; no proceeds or SELL floor is an asset."""
    quantity_value = _amount(quantity, positive=True)
    owned = _amount(owned_available_base, positive=False)
    held = _amount(held_base, positive=False)
    fee = _amount(fee_base_upper, positive=False)
    debit = quantity_value + fee
    debit_decimal = finite_decimal_v2(debit)
    commitment = held + debit
    if commitment > owned:
        raise RiskV2Rejected("RISK_V2_MARGIN_LIMIT")
    return SpotBaseRequirementV2(debit_decimal, finite_decimal_v2(owned - commitment))
