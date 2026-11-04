"""Exact, non-authoritative Risk V2 scalar arithmetic.

Only nonterminating *display* uses a fresh Decimal context. These helpers
neither qualify a source/model nor authorize financial activity.
"""

from decimal import (
    ROUND_CEILING,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
)
from fractions import Fraction
from math import gcd

_MAX_DIGITS = 65536
_MAX_BITS = 1_000_000
_CODES = frozenset(
    "RISK_V2_" + suffix
    for suffix in [
        "INPUT_INVALID",
        "RESOURCE_LIMIT",
        "SCOPE_MISMATCH",
        "SOURCE_MISMATCH",
        "SOURCE_READ_FAILED",
        "SOURCE_READ_BUDGET_EXHAUSTED",
        "REVISION_MISMATCH",
        "STALE_EVIDENCE",
        "FUTURE_EVIDENCE",
        "EXPIRED",
        "UNSUPPORTED_PRODUCT",
        "UNSUPPORTED_MODE",
        "CAP_POLICY_MIGRATION_REQUIRED",
        "INVALID_PRICE_TICK",
        "INVALID_STOP",
        "MINIMUM_SIZE",
        "MAXIMUM_SIZE",
        "MINIMUM_RULE_UNVERIFIED",
        "ORDER_ADMISSION_LIMIT",
        "ACCOUNT_ADMISSION_LIMIT",
        "EXECUTION_QUOTE_LIMIT",
        "SELL_EXECUTION_UPPER_UNAVAILABLE",
        "MARK_COVERAGE_REQUIRED",
        "EXPOSURE_OVERLAP",
        "RISK_TIER_UNVERIFIED",
        "LEVERAGE_UNSATISFIABLE",
        "TRADE_LOSS_LIMIT",
        "TOTAL_RISK_LIMIT",
        "DAILY_LOSS_LIMIT",
        "DRAWDOWN_LIMIT",
        "CONSECUTIVE_LOSS_LIMIT",
        "POSITION_LIMIT",
        "LOSS_LEDGER_UNVERIFIED",
        "EXTERNAL_CONFLICT",
        "MARGIN_LIMIT",
        "MARGIN_UNVERIFIED",
        "LIQUIDATION_UNVERIFIED",
        "LIQUIDATION_BUFFER",
        "LEVERAGE_READBACK_UNVERIFIED",
        "PLANNING_ONLY",
        "LEGACY_RESERVATION_RECONCILIATION_REQUIRED",
        "CAUSAL_ORDER_INVALID",
        "GRID_SOURCE_MODEL_UNVERIFIED",
        "HELD_COVERAGE_REQUIRED",
        "LOCAL_ADMISSION_CONFLICT",
    ]
)


class RiskV2Rejected(ValueError):
    """The single closed rejection vocabulary, also re-exported by future contracts."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _CODES:
            raise ValueError("INVALID_RISK_V2_REJECTION_CODE")
        self.code = code
        super().__init__(code)


def _bounded(value: int) -> int:
    if value.bit_length() > _MAX_BITS:
        raise RiskV2Rejected("RISK_V2_RESOURCE_LIMIT")
    return value


def _product(left: int, right: int) -> int:
    if left and right and left.bit_length() + right.bit_length() - 1 > _MAX_BITS:
        raise RiskV2Rejected("RISK_V2_RESOURCE_LIMIT")
    return _bounded(left * right)


def _power(base: int, exponent: int) -> int:
    # Integer-only conservative upper bound, checked before allocation.
    if exponent > _MAX_DIGITS or exponent * base.bit_length() + 1 > _MAX_BITS:
        raise RiskV2Rejected("RISK_V2_RESOURCE_LIMIT")
    return _bounded(base**exponent)


def _fraction(value: Fraction) -> Fraction:
    if type(value) is not Fraction:
        raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
    try:
        numerator = value.numerator
        denominator = value.denominator
    except AttributeError:
        raise RiskV2Rejected("RISK_V2_INPUT_INVALID") from None
    if type(numerator) is not int or type(denominator) is not int or denominator <= 0:
        raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
    _bounded(numerator)
    _bounded(denominator)
    return Fraction(numerator, denominator)


def _multiply(left: Fraction, right: Fraction) -> Fraction:
    # Cancel first so only the actual reduced products consume the work budget.
    common_left = gcd(left.numerator, right.denominator)
    common_right = gcd(right.numerator, left.denominator)
    return Fraction(
        _product(left.numerator // common_left, right.numerator // common_right),
        _product(left.denominator // common_right, right.denominator // common_left),
    )


def _compare(left: Fraction, right: Fraction) -> int:
    common = gcd(left.denominator, right.denominator)
    left_scaled = _product(left.numerator, right.denominator // common)
    right_scaled = _product(right.numerator, left.denominator // common)
    return int(left_scaled > right_scaled) - int(left_scaled < right_scaled)


def _decimal_parts(value: Decimal) -> tuple[int, tuple[int, ...], int]:
    if type(value) is not Decimal or not value.is_finite():
        raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
    parts = value.as_tuple()
    exponent = parts.exponent
    if not isinstance(exponent, int):
        raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
    if len(parts.digits) > _MAX_DIGITS or abs(exponent) > _MAX_DIGITS:
        raise RiskV2Rejected("RISK_V2_RESOURCE_LIMIT")
    return parts.sign, parts.digits, exponent


def _digits(coefficient: int) -> tuple[int, ...]:
    if coefficient == 0:
        return (0,)
    digits: list[int] = []
    while coefficient:
        if len(digits) == _MAX_DIGITS:
            raise RiskV2Rejected("RISK_V2_RESOURCE_LIMIT")
        coefficient, digit = divmod(coefficient, 10)
        digits.append(digit)
    digits.reverse()
    return tuple(digits)


def _denominator_factors(denominator: int) -> tuple[int, int, int]:
    # Inputs already have the integer-work bound. Classification alone imposes
    # no finite-decimal scale bound on a potentially nonterminating display.
    twos = (denominator & -denominator).bit_length() - 1
    remaining = denominator >> twos
    fives = 0
    while remaining % 5 == 0:
        remaining //= 5
        fives += 1
    return twos, fives, remaining


def decimal_fraction_v2(value: Decimal) -> Fraction:
    sign, digits, exponent = _decimal_parts(value)
    coefficient = 0
    for digit in digits:
        coefficient = _bounded(_product(coefficient, 10) + digit)
    if coefficient == 0:
        return Fraction(0)
    if sign:
        coefficient = -coefficient
    if exponent >= 0:
        return Fraction(_product(coefficient, _power(10, exponent)))
    return Fraction(coefficient, _power(10, -exponent))


def finite_decimal_v2(value: Fraction) -> Decimal:
    value = _fraction(value)
    twos, fives, remaining = _denominator_factors(value.denominator)
    if remaining != 1:
        raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
    scale = max(twos, fives)
    if scale > _MAX_DIGITS:
        raise RiskV2Rejected("RISK_V2_RESOURCE_LIMIT")
    coefficient = _product(abs(value.numerator), _power(2, scale - twos))
    coefficient = _product(coefficient, _power(5, scale - fives))
    return Decimal((int(value.numerator < 0), _digits(coefficient), -scale))


def decimal_text_v2(value: Decimal) -> str:
    sign, digits, exponent = _decimal_parts(value)
    text = "".join(chr(48 + digit) for digit in digits)
    adjusted = exponent + len(digits) - 1
    point = len(digits) + exponent
    if exponent <= 0 and adjusted >= -6:
        if point <= 0:
            text = "0." + "0" * (-point) + text
        elif point < len(digits):
            text = text[:point] + "." + text[point:]
    else:
        mantissa = text[0] + ("." + text[1:] if len(digits) > 1 else "")
        text = mantissa + "E" + ("+" if adjusted >= 0 else "-") + str(abs(adjusted))
    return ("-" if sign else "") + text


def _ratio_parts(numerator: Fraction, denominator: Fraction) -> tuple[int, int]:
    numerator = _fraction(numerator)
    denominator = _fraction(denominator)
    if numerator < 0 or denominator <= 0:
        raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
    return (
        _product(numerator.numerator, denominator.denominator),
        _product(numerator.denominator, denominator.numerator),
    )


def floor_ratio_v2(numerator: Fraction, denominator: Fraction) -> int:
    top, bottom = _ratio_parts(numerator, denominator)
    return _bounded(top // bottom)


def ceil_ratio_v2(numerator: Fraction, denominator: Fraction) -> int:
    top, bottom = _ratio_parts(numerator, denominator)
    quotient, remainder = divmod(top, bottom)
    return _bounded(quotient + int(remainder != 0))


def floor_quantity_v2(loss_budget: Fraction, unit_loss: Fraction, step: Fraction) -> Fraction:
    loss_budget, unit_loss, step = (_fraction(loss_budget), _fraction(unit_loss), _fraction(step))
    if loss_budget < 0 or unit_loss <= 0 or step <= 0:
        raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
    units = floor_ratio_v2(loss_budget, _multiply(unit_loss, step))
    return _multiply(Fraction(units), step)


def zero_origin_lattice_v2(
    lower: Fraction, upper: Fraction, step: Fraction
) -> tuple[Fraction, Fraction]:
    lower, upper, step = (_fraction(lower), _fraction(upper), _fraction(step))
    if lower <= 0 or upper <= 0 or step <= 0 or _compare(lower, upper) > 0:
        raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
    first = ceil_ratio_v2(lower, step)
    last = floor_ratio_v2(upper, step)
    if first > last:
        raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
    return _multiply(Fraction(first), step), _multiply(Fraction(last), step)


def is_zero_origin_member_v2(
    value: Fraction, *, first: Fraction, last: Fraction, step: Fraction
) -> bool:
    value, first, last, step = (
        _fraction(value),
        _fraction(first),
        _fraction(last),
        _fraction(step),
    )
    if first <= 0 or last <= 0 or step <= 0 or _compare(first, last) > 0:
        raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
    if _compare(first, value) > 0 or _compare(value, last) > 0:
        return False
    top, bottom = _ratio_parts(value, step)
    return top % bottom == 0


def display_ratio_v2(numerator: Fraction, denominator: Fraction) -> Decimal:
    numerator, denominator = _fraction(numerator), _fraction(denominator)
    if numerator < 0 or denominator <= 0:
        raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
    ratio = _multiply(numerator, Fraction(denominator.denominator, denominator.numerator))
    _, _, remaining = _denominator_factors(ratio.denominator)
    if remaining == 1:
        return finite_decimal_v2(ratio)
    context = Context(
        prec=80,
        rounding=ROUND_CEILING,
        Emin=-999999999,
        Emax=999999999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )
    try:
        return context.divide(
            finite_decimal_v2(Fraction(ratio.numerator)),
            finite_decimal_v2(Fraction(ratio.denominator)),
        )
    except (InvalidOperation, DivisionByZero, Overflow):
        raise RiskV2Rejected("RISK_V2_INPUT_INVALID") from None
