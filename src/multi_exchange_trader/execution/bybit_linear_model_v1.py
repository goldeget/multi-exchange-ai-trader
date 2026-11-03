"""Exact source-free Bybit LINEAR point kernels and closed B results.

Supplied graphs and proofs do not qualify an account, current source, completion
receipt, or order. No clock, transport, or issuer is created here.
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from math import gcd
from typing import Literal, NoReturn, cast

from multi_exchange_trader.execution.bybit_linear_model_inputs_v1 import (
    BybitFeeDocumentRefV1,
    BybitLinearPointInputV1,
    BybitLinearTierTableV1,
    BybitModelFieldRefV1,
    BybitModelSourceRefV1,
    _rational_payload_v1,
    bybit_instrument_pin_sha256_v1,
    bybit_point_input_sha256_v1,
    bybit_tier_table_sha256_v1,
)
from multi_exchange_trader.execution.risk_v2_contracts import (
    _canonical_sha256_v2,
    _decimal_v2,
    _digest_v2,
    _fields_v2,
    _identifier_v2,
    _literal_v2,
    _observation_payload_v2,
    _utc_v2,
)
from multi_exchange_trader.execution.risk_v2_math import (
    RiskV2Rejected,
    ceil_ratio_v2,
    decimal_fraction_v2,
    decimal_text_v2,
    finite_decimal_v2,
    is_zero_origin_member_v2,
    zero_origin_lattice_v2,
)

Side = Literal["LONG", "SHORT"]
Failure = Literal["IMR", "CAPACITY", "LIQUIDATION_BUFFER"]
Phase = Literal["SOURCE", "MARGIN", "LIQUIDATION", "BRIDGE", "SEARCH", "BINDING"]
Reason = Literal[
    "PROFILE_UNSUPPORTED",
    "SOURCE_INVALID",
    "SOURCE_UNIT_UNSUPPORTED",
    "ROOT_OUTSIDE_CAPTURED_TABLE",
    "PIECEWISE_INCONSISTENT",
    "REPRESENTATION_UNSUPPORTED",
    "RESOURCE_LIMIT",
    "INPUT_MISMATCH",
    "NO_FEASIBLE_LEVERAGE",
    "EVIDENCE_INVALID",
]
_MAX_BITS = 1_000_000
_LP_RULE = "BYBIT_LINEAR_FULL_TABLE_LP_V1"
_ROUNDING = "APP_RESERVE_CEILING_QUANTUM_V1"
_BASIS = "POINT_ESTIMATE_TWO_IM_ENVELOPE_V1"
_Q = Decimal((0, (1,), -8))


def _reject(code: str = "RISK_V2_INPUT_INVALID") -> NoReturn:
    raise RiskV2Rejected(code) from None


def _integer(value: int) -> int:
    if type(value) is not int:
        _reject()
    if value.bit_length() > _MAX_BITS:
        _reject("RISK_V2_RESOURCE_LIMIT")
    return value


def _fraction(value: Fraction) -> Fraction:
    if type(value) is not Fraction:
        _reject()
    try:
        n, d = value.numerator, value.denominator
    except AttributeError:
        _reject()
    _integer(n)
    _integer(d)
    if d <= 0 or gcd(n, d) != 1:
        _reject()
    return value


def _product(left: int, right: int) -> int:
    _integer(left)
    _integer(right)
    # Root's stronger preallocation rule forbids even a one-bit temporary overshoot.
    if left and right and left.bit_length() + right.bit_length() > _MAX_BITS:
        _reject("RISK_V2_RESOURCE_LIMIT")
    return _integer(left * right)


def _mul(left: Fraction, right: Fraction) -> Fraction:
    _fraction(left)
    _fraction(right)
    a = gcd(left.numerator, right.denominator)
    b = gcd(right.numerator, left.denominator)
    return _fraction(
        Fraction(
            _product(left.numerator // a, right.numerator // b),
            _product(left.denominator // b, right.denominator // a),
        )
    )


def _add(left: Fraction, right: Fraction) -> Fraction:
    _fraction(left)
    _fraction(right)
    common = gcd(left.denominator, right.denominator)
    a = _product(left.numerator, right.denominator // common)
    b = _product(right.numerator, left.denominator // common)
    denominator = _product(left.denominator, right.denominator // common)
    if a and b and (a > 0) == (b > 0) and max(a.bit_length(), b.bit_length()) + 1 > _MAX_BITS:
        _reject("RISK_V2_RESOURCE_LIMIT")
    return _fraction(Fraction(_integer(a + b), denominator))


def _sub(left: Fraction, right: Fraction) -> Fraction:
    _fraction(right)
    return _add(left, Fraction(-right.numerator, right.denominator))


def _div(left: Fraction, right: Fraction) -> Fraction:
    _fraction(right)
    if right.numerator == 0:
        _reject()
    return _mul(left, Fraction(right.denominator, right.numerator))


def _cmp(left: Fraction, right: Fraction) -> int:
    _fraction(left)
    _fraction(right)
    common = gcd(left.denominator, right.denominator)
    a = _product(left.numerator, right.denominator // common)
    b = _product(right.numerator, left.denominator // common)
    return int(a > b) - int(a < b)


def _max(*values: Fraction) -> Fraction:
    result = values[0]
    for value in values[1:]:
        if _cmp(value, result) > 0:
            result = value
    return result


def _number(value: Decimal, *, source: bool = False, zero: bool = False) -> Fraction:
    return _fraction(
        decimal_fraction_v2(
            _decimal_v2(value, source=source, sign="nonnegative" if zero else "positive")
        )
    )


def _rate(value: Decimal) -> Fraction:
    number = _number(value, zero=True)
    if _cmp(number, Fraction(1)) >= 0:
        _reject()
    return number


class _Value:
    def __post_init__(self) -> None:
        _validate(self)


@dataclass(frozen=True)
class BybitMarginPointV1(_Value):
    side: Side
    quantity: Fraction
    entry_limit: Fraction
    mark: Fraction
    leverage: Fraction
    taker_rate: Fraction
    order_close_rate: Fraction
    order_im: Fraction
    open_estimate: Fraction
    order_close_reserve: Fraction
    position_close_estimate: Fraction
    mark_core: Fraction
    entry_core: Fraction
    initial_margin_envelope: Fraction
    order_cost: Fraction
    point_reserve: Fraction


@dataclass(frozen=True)
class BybitReserveBridgeV1(_Value):
    initial_margin_quote: Decimal
    venue_required_total_reserve_quote: Decimal
    required_reservation_quote: Decimal
    exact_initial_margin: Fraction
    exact_point_reserve: Fraction
    exact_required_reservation: Fraction
    planned_cost_allowance_quote: Decimal
    reserve_quantum_usdt: Decimal
    reserve_basis: Literal["POINT_ESTIMATE_TWO_IM_ENVELOPE_V1"]
    reserve_rounding_revision: Literal["APP_RESERVE_CEILING_QUANTUM_V1"]


@dataclass(frozen=True)
class BybitLiquidationRowProofV1(_Value):
    tier_id: str
    lower_quote: Fraction
    upper_quote: Fraction
    intercept: Fraction
    slope: Fraction
    candidate_price: Fraction
    residual_lower: Fraction
    residual_upper: Fraction
    self_consistent: bool


@dataclass(frozen=True)
class BybitPositiveRootPointV1(_Value):
    kind: Literal["POSITIVE_ROOT"]
    exact_price: Fraction
    candidate_tier_id: str


@dataclass(frozen=True)
class BybitNoPositiveRootPointV1(_Value):
    kind: Literal["NO_POSITIVE_ROOT"]
    reason: Literal["NON_POSITIVE_LONG_INTERCEPT"]
    long_intercept: Fraction
    residual_at_zero: Fraction


@dataclass(frozen=True)
class BybitLiquidationCalculationV1(_Value):
    outcome: BybitPositiveRootPointV1 | BybitNoPositiveRootPointV1
    domain_upper_quote: Decimal
    full_table_sha256: str
    raw_manifest_sha256: str
    consumed_tier_ids: tuple[str, ...]
    rows: tuple[BybitLiquidationRowProofV1, ...]
    rule_revision: Literal["BYBIT_LINEAR_FULL_TABLE_LP_V1"]
    proof_sha256: str


@dataclass(frozen=True)
class BybitCandidateEvaluationV1(_Value):
    grid_index: int
    leverage: Decimal
    margin: BybitMarginPointV1
    reserve: BybitReserveBridgeV1
    liquidation: BybitLiquidationCalculationV1
    liquidation_buffer_result: Literal["GAP_SATISFIED", "MODEL_NO_POSITIVE_TRIGGER"] | None
    failure_codes: tuple[Failure, ...]


@dataclass(frozen=True)
class BybitModelRejectedV1(_Value):
    kind: Literal["REJECTED"]
    phase: Phase
    reason: Reason
    public_code: str
    candidate_leverage: Decimal | None
    evaluated_prefix: tuple[BybitCandidateEvaluationV1, ...]


def _margin_parts(
    side: Side,
    q: Fraction,
    p: Fraction,
    m: Fraction,
    leverage: Fraction,
    fee: Fraction,
    close: Fraction,
) -> dict[str, Fraction]:
    for number in (q, p, m, leverage, fee, close):
        _fraction(number)
    if any(_cmp(x, Fraction(0)) <= 0 for x in (q, p, m)) or _cmp(leverage, Fraction(1)) < 0:
        _reject()
    if any(_cmp(x, Fraction(0)) < 0 or _cmp(x, Fraction(1)) >= 0 for x in (fee, close)):
        _reject()
    inverse = _div(Fraction(1), leverage)
    factor = _sub(Fraction(1), inverse) if side == "LONG" else _add(Fraction(1), inverse)
    quote = _mul(q, p)
    im = _mul(quote, inverse)
    opening = _mul(quote, fee)
    closing = _mul(_mul(quote, factor), close)
    position = _mul(_mul(quote, factor), fee)
    mark_core = _mul(_mul(q, m), inverse)
    order_cost = _add(_add(im, opening), closing)
    reserve = _max(
        order_cost, _add(_add(mark_core, position), opening), _add(_add(im, position), opening)
    )
    return {
        "quantity": q,
        "entry_limit": p,
        "mark": m,
        "leverage": leverage,
        "taker_rate": fee,
        "order_close_rate": close,
        "order_im": im,
        "open_estimate": opening,
        "order_close_reserve": closing,
        "position_close_estimate": position,
        "mark_core": mark_core,
        "entry_core": im,
        "initial_margin_envelope": _max(mark_core, im),
        "order_cost": order_cost,
        "point_reserve": reserve,
    }


def _margin_payload(value: BybitMarginPointV1) -> dict[str, object]:
    fields = _fields_v2(value, BybitMarginPointV1)
    side = cast(Side, _literal_v2(fields["side"], ("LONG", "SHORT")))
    for name, number in fields.items():
        if name != "side":
            _fraction(number)
    # Scalar inputs were finite Decimals. Fractions no longer retain source
    # spelling; strip only trailing coefficient zeroes before checking whether
    # a source-role price has a supported representation (e.g. 1E+1000).
    for name in ("quantity", "entry_limit", "mark", "leverage", "taker_rate", "order_close_rate"):
        decimal = finite_decimal_v2(fields[name])
        if name in ("entry_limit", "mark"):
            parts = decimal.as_tuple()
            digits, exponent = parts.digits, cast(int, parts.exponent)
            if abs(exponent + len(digits) - 1) > 1000:
                _reject("RISK_V2_RESOURCE_LIMIT")
            while len(digits) > 1 and digits[-1] == 0:
                digits, exponent = digits[:-1], exponent + 1
            decimal = Decimal((parts.sign, digits, exponent))
        _number(
            decimal,
            source=name in ("entry_limit", "mark"),
            zero=name in ("taker_rate", "order_close_rate"),
        )
    expected = _margin_parts(
        side,
        value.quantity,
        value.entry_limit,
        value.mark,
        value.leverage,
        value.taker_rate,
        value.order_close_rate,
    )
    if any(fields[name] != number for name, number in expected.items()):
        _reject()
    return {
        "side": side,
        **{name: _rational_payload_v1(number) for name, number in expected.items()},
    }


def _ceil_q(value: Fraction) -> Fraction:
    quantum = decimal_fraction_v2(_Q)
    # Task0's public ceil uses these unreduced products. Guard those exact
    # operands as well as the reduced ratio before delegating to it.
    _product(value.numerator, quantum.denominator)
    _product(value.denominator, quantum.numerator)
    _div(value, quantum)
    return _mul(Fraction(ceil_ratio_v2(value, quantum)), quantum)


def _bridge_parts(initial: Fraction, reserve: Fraction, cost: Fraction) -> tuple[Fraction, ...]:
    for number in (initial, reserve, cost):
        _fraction(number)
    if _cmp(initial, Fraction(0)) <= 0 or _cmp(reserve, initial) < 0 or _cmp(cost, Fraction(0)) < 0:
        _reject()
    exact = _max(_add(initial, cost), reserve)
    out_i, out_v = _ceil_q(initial), _ceil_q(reserve)
    out_r = _max(_add(out_i, cost), out_v)
    if any(_cmp(a, b) < 0 for a, b in ((out_i, initial), (out_v, reserve), (out_r, exact))):
        _reject()
    return out_i, out_v, out_r, exact


def _bridge_payload(value: BybitReserveBridgeV1) -> dict[str, object]:
    fields = _fields_v2(value, BybitReserveBridgeV1)
    _literal_v2(fields["reserve_basis"], (_BASIS,))
    _literal_v2(fields["reserve_rounding_revision"], (_ROUNDING,))
    _number(value.reserve_quantum_usdt)
    if value.reserve_quantum_usdt.as_tuple() != _Q.as_tuple():
        _reject("RISK_V2_REVISION_MISMATCH")
    for name in ("exact_initial_margin", "exact_point_reserve", "exact_required_reservation"):
        _fraction(fields[name])
    cost = _number(value.planned_cost_allowance_quote, zero=True)
    out_i, out_v, out_r, exact = _bridge_parts(
        value.exact_initial_margin, value.exact_point_reserve, cost
    )
    if exact != value.exact_required_reservation:
        _reject()
    for name, expected in zip(
        (
            "initial_margin_quote",
            "venue_required_total_reserve_quote",
            "required_reservation_quote",
        ),
        (out_i, out_v, out_r),
        strict=True,
    ):
        if _number(fields[name]) != expected:
            _reject()
    return {
        name: (
            _rational_payload_v1(number)
            if type(number) is Fraction
            else decimal_text_v2(number)
            if type(number) is Decimal
            else number
        )
        for name, number in fields.items()
    }


def _row_payload(value: BybitLiquidationRowProofV1) -> dict[str, object]:
    fields = _fields_v2(value, BybitLiquidationRowProofV1)
    _identifier_v2(fields["tier_id"])
    if type(fields["self_consistent"]) is not bool:
        _reject()
    for name, number in fields.items():
        if name not in ("tier_id", "self_consistent"):
            _fraction(number)
    if (
        _cmp(value.lower_quote, Fraction(0)) < 0
        or _cmp(value.upper_quote, value.lower_quote) <= 0
        or _cmp(value.slope, Fraction(0)) <= 0
    ):
        _reject()
    if value.residual_lower != _sub(
        _mul(value.slope, value.lower_quote), value.intercept
    ) or value.residual_upper != _sub(_mul(value.slope, value.upper_quote), value.intercept):
        _reject()
    root_quote = _div(value.intercept, value.slope)
    consistent = (
        _cmp(root_quote, value.lower_quote) > 0 and _cmp(root_quote, value.upper_quote) <= 0
    )
    if value.self_consistent != consistent:
        _reject()
    if _cmp(value.candidate_price, Fraction(0)) != _cmp(value.intercept, Fraction(0)):
        _reject()
    return {
        name: _rational_payload_v1(number) if type(number) is Fraction else number
        for name, number in fields.items()
    }


def _outcome_payload(
    value: BybitPositiveRootPointV1 | BybitNoPositiveRootPointV1,
) -> dict[str, object]:
    if type(value) is BybitPositiveRootPointV1:
        fields = _fields_v2(value, BybitPositiveRootPointV1)
        _literal_v2(fields["kind"], ("POSITIVE_ROOT",))
        _fraction(value.exact_price)
        if _cmp(value.exact_price, Fraction(0)) <= 0:
            _reject()
        return {
            "kind": value.kind,
            "exact_price": _rational_payload_v1(value.exact_price),
            "candidate_tier_id": _identifier_v2(value.candidate_tier_id),
        }
    if type(value) is BybitNoPositiveRootPointV1:
        fields = _fields_v2(value, BybitNoPositiveRootPointV1)
        _literal_v2(fields["kind"], ("NO_POSITIVE_ROOT",))
        _literal_v2(fields["reason"], ("NON_POSITIVE_LONG_INTERCEPT",))
        _fraction(value.long_intercept)
        _fraction(value.residual_at_zero)
        if _cmp(value.long_intercept, Fraction(0)) > 0 or value.residual_at_zero != _sub(
            Fraction(0), value.long_intercept
        ):
            _reject()
        return {
            "kind": value.kind,
            "reason": value.reason,
            "long_intercept": _rational_payload_v1(value.long_intercept),
            "residual_at_zero": _rational_payload_v1(value.residual_at_zero),
        }
    _reject()


def _tuple(value: object, maximum: int) -> None:
    if type(value) is not tuple:
        _reject()
    if len(cast(tuple[object, ...], value)) > maximum:
        _reject("RISK_V2_RESOURCE_LIMIT")


def _calculation_payload(value: BybitLiquidationCalculationV1) -> dict[str, object]:
    fields = _fields_v2(value, BybitLiquidationCalculationV1)
    outcome = _outcome_payload(value.outcome)
    upper = _number(value.domain_upper_quote)
    for name in ("full_table_sha256", "raw_manifest_sha256", "proof_sha256"):
        _digest_v2(fields[name])
    _literal_v2(value.rule_revision, (_LP_RULE,))
    _tuple(value.rows, 1024)
    _tuple(value.consumed_tier_ids, 1024)
    if not value.rows or len(value.rows) != len(value.consumed_tier_ids):
        _reject()
    for tier in value.consumed_tier_ids:
        _identifier_v2(tier)
    rows = tuple(bybit_liquidation_row_sha256_v1(row) for row in value.rows)
    if tuple(row.tier_id for row in value.rows) != value.consumed_tier_ids or len(
        set(value.consumed_tier_ids)
    ) != len(value.rows):
        _reject()
    lower = Fraction(0)
    implied_quantity: Fraction | None = None
    for row in value.rows:
        if row.lower_quote != lower:
            _reject()
        lower = row.upper_quote
        if row.intercept:
            quantity = _div(row.intercept, _mul(row.slope, row.candidate_price))
            if _cmp(quantity, Fraction(0)) <= 0 or (
                implied_quantity is not None and quantity != implied_quantity
            ):
                _reject()
            implied_quantity = quantity
    if lower != upper:
        _reject()
    valid = tuple(row for row in value.rows if row.self_consistent)
    if type(value.outcome) is BybitPositiveRootPointV1:
        if (
            len(valid) != 1
            or valid[0].tier_id != value.outcome.candidate_tier_id
            or valid[0].candidate_price != value.outcome.exact_price
        ):
            _reject()
    else:
        no_root = cast(BybitNoPositiveRootPointV1, value.outcome)
        if (
            valid
            or value.rows[0].intercept != no_root.long_intercept
            or any(
                _cmp(row.intercept, Fraction(0)) > 0 or _cmp(row.slope, Fraction(1)) >= 0
                for row in value.rows
            )
        ):
            _reject()
    return {
        "outcome": outcome,
        "domain_upper_quote": decimal_text_v2(value.domain_upper_quote),
        "full_table_sha256": value.full_table_sha256,
        "raw_manifest_sha256": value.raw_manifest_sha256,
        "consumed_tier_ids": value.consumed_tier_ids,
        "rows": rows,
        "rule_revision": value.rule_revision,
        "proof_sha256": value.proof_sha256,
    }


_REASON_CODES: dict[str, tuple[str, ...]] = {
    "PROFILE_UNSUPPORTED": (
        "RISK_V2_UNSUPPORTED_MODE",
        "RISK_V2_UNSUPPORTED_PRODUCT",
        "RISK_V2_MARGIN_UNVERIFIED",
    ),
    "SOURCE_INVALID": ("RISK_V2_RISK_TIER_UNVERIFIED",),
    "SOURCE_UNIT_UNSUPPORTED": ("RISK_V2_RISK_TIER_UNVERIFIED",),
    "ROOT_OUTSIDE_CAPTURED_TABLE": ("RISK_V2_LIQUIDATION_UNVERIFIED",),
    "PIECEWISE_INCONSISTENT": ("RISK_V2_LIQUIDATION_UNVERIFIED",),
    "REPRESENTATION_UNSUPPORTED": ("RISK_V2_INPUT_INVALID",),
    "INPUT_MISMATCH": (
        "RISK_V2_INPUT_INVALID",
        "RISK_V2_SCOPE_MISMATCH",
        "RISK_V2_SOURCE_MISMATCH",
        "RISK_V2_REVISION_MISMATCH",
    ),
    "RESOURCE_LIMIT": ("RISK_V2_RESOURCE_LIMIT",),
    "NO_FEASIBLE_LEVERAGE": ("RISK_V2_LEVERAGE_UNSATISFIABLE",),
    "EVIDENCE_INVALID": (
        "RISK_V2_STALE_EVIDENCE",
        "RISK_V2_FUTURE_EVIDENCE",
        "RISK_V2_EXPIRED",
        "RISK_V2_CAUSAL_ORDER_INVALID",
    ),
}


def _candidate_payload(value: BybitCandidateEvaluationV1) -> dict[str, object]:
    _fields_v2(value, BybitCandidateEvaluationV1)
    if type(value.grid_index) is not int or not 0 <= value.grid_index < 4096:
        _reject()
    leverage = _number(value.leverage)
    margin = bybit_margin_point_sha256_v1(value.margin)
    reserve = bybit_reserve_bridge_sha256_v1(value.reserve)
    liquidation = bybit_liquidation_calculation_sha256_v1(value.liquidation)
    if (
        leverage != value.margin.leverage
        or value.reserve.exact_initial_margin != value.margin.initial_margin_envelope
        or value.reserve.exact_point_reserve != value.margin.point_reserve
    ):
        _reject()
    for row in value.liquidation.rows:
        if (
            row.candidate_price != _div(row.intercept, _mul(value.margin.quantity, row.slope))
            or (value.margin.side == "LONG" and _cmp(row.slope, Fraction(1)) >= 0)
            or (value.margin.side == "SHORT" and _cmp(row.slope, Fraction(1)) <= 0)
        ):
            _reject()
    _tuple(value.failure_codes, 3)
    for code in value.failure_codes:
        _literal_v2(code, ("IMR", "CAPACITY", "LIQUIDATION_BUFFER"))
    if value.failure_codes != tuple(
        code for code in ("IMR", "CAPACITY", "LIQUIDATION_BUFFER") if code in value.failure_codes
    ):
        _reject()
    if value.liquidation_buffer_result is not None:
        _literal_v2(value.liquidation_buffer_result, ("GAP_SATISFIED", "MODEL_NO_POSITIVE_TRIGGER"))
    if type(value.liquidation.outcome) is BybitNoPositiveRootPointV1:
        if (
            value.margin.side != "LONG"
            or value.liquidation_buffer_result != "MODEL_NO_POSITIVE_TRIGGER"
        ):
            _reject()
    elif value.liquidation_buffer_result not in (None, "GAP_SATISFIED"):
        _reject()
    if (value.liquidation_buffer_result is None) != ("LIQUIDATION_BUFFER" in value.failure_codes):
        _reject()
    return {
        "grid_index": value.grid_index,
        "leverage": decimal_text_v2(value.leverage),
        "margin": margin,
        "reserve": reserve,
        "liquidation": liquidation,
        "liquidation_buffer_result": value.liquidation_buffer_result,
        "failure_codes": value.failure_codes,
    }


def _rejected(value: BybitModelRejectedV1) -> None:
    _fields_v2(value, BybitModelRejectedV1)
    _literal_v2(value.kind, ("REJECTED",))
    _literal_v2(value.phase, ("SOURCE", "MARGIN", "LIQUIDATION", "BRIDGE", "SEARCH", "BINDING"))
    reason = _literal_v2(value.reason, tuple(_REASON_CODES))
    _literal_v2(value.public_code, _REASON_CODES[reason])
    if (
        value.candidate_leverage is not None
        and _cmp(_number(value.candidate_leverage), Fraction(1)) < 0
    ):
        _reject()
    _tuple(value.evaluated_prefix, 4096)
    for i, candidate in enumerate(value.evaluated_prefix):
        _candidate_payload(candidate)
        if candidate.grid_index != i or not candidate.failure_codes:
            _reject()
        if (
            i
            and _cmp(_number(value.evaluated_prefix[i - 1].leverage), _number(candidate.leverage))
            >= 0
        ):
            _reject()


def _validate(value: _Value) -> None:
    if type(value) is BybitMarginPointV1:
        _margin_payload(value)
    elif type(value) is BybitReserveBridgeV1:
        _bridge_payload(value)
    elif type(value) is BybitLiquidationRowProofV1:
        _row_payload(value)
    elif type(value) in (BybitPositiveRootPointV1, BybitNoPositiveRootPointV1):
        _outcome_payload(cast(BybitPositiveRootPointV1 | BybitNoPositiveRootPointV1, value))
    elif type(value) is BybitLiquidationCalculationV1:
        _calculation_payload(value)
    elif type(value) is BybitCandidateEvaluationV1:
        _candidate_payload(value)
    elif type(value) is BybitModelRejectedV1:
        _rejected(value)
    else:
        _reject()


def calculate_bybit_margin_point_v1(
    *,
    side: Side,
    quantity: Decimal,
    entry_limit: Decimal,
    mark: Decimal,
    leverage: Decimal,
    taker_rate: Decimal,
    order_close_rate: Decimal,
) -> BybitMarginPointV1:
    _literal_v2(side, ("LONG", "SHORT"))
    parts = _margin_parts(
        side,
        _number(quantity),
        _number(entry_limit, source=True),
        _number(mark, source=True),
        _number(leverage),
        _rate(taker_rate),
        _rate(order_close_rate),
    )
    return BybitMarginPointV1(side=side, **parts)


def bridge_bybit_reserve_v1(
    margin: BybitMarginPointV1,
    *,
    planned_cost_allowance_quote: Decimal,
    reserve_quantum_usdt: Decimal,
    reserve_rounding_revision: Literal["APP_RESERVE_CEILING_QUANTUM_V1"],
) -> BybitReserveBridgeV1:
    _margin_payload(margin)
    _literal_v2(reserve_rounding_revision, (_ROUNDING,))
    _number(reserve_quantum_usdt)
    if reserve_quantum_usdt.as_tuple() != _Q.as_tuple():
        _reject("RISK_V2_REVISION_MISMATCH")
    cost = _number(planned_cost_allowance_quote, zero=True)
    initial, venue, reserve, exact = _bridge_parts(
        margin.initial_margin_envelope, margin.point_reserve, cost
    )
    return BybitReserveBridgeV1(
        finite_decimal_v2(initial),
        finite_decimal_v2(venue),
        finite_decimal_v2(reserve),
        margin.initial_margin_envelope,
        margin.point_reserve,
        exact,
        planned_cost_allowance_quote,
        reserve_quantum_usdt,
        "POINT_ESTIMATE_TWO_IM_ENVELOPE_V1",
        "APP_RESERVE_CEILING_QUANTUM_V1",
    )


def _lp_parts(
    *,
    side: Side,
    quantity: Decimal,
    entry_price: Decimal,
    leverage: Decimal,
    extra_margin: Decimal,
    taker_rate: Decimal,
    table: BybitLinearTierTableV1,
) -> (
    tuple[
        BybitPositiveRootPointV1 | BybitNoPositiveRootPointV1,
        tuple[BybitLiquidationRowProofV1, ...],
        dict[str, Fraction],
    ]
    | BybitModelRejectedV1
):
    _literal_v2(side, ("LONG", "SHORT"))
    bybit_tier_table_sha256_v1(table)
    q, price, lev = _number(quantity), _number(entry_price, source=True), _number(leverage)
    extra, fee = _number(extra_margin, zero=True), _rate(taker_rate)
    if _cmp(lev, Fraction(1)) < 0:
        _reject()
    quote = _mul(q, price)
    initial = _div(quote, lev)
    long = side == "LONG"
    fee_factor = _sub(Fraction(1), fee) if long else _add(Fraction(1), fee)
    intercept = (
        _sub(_sub(quote, initial), _div(extra, fee_factor))
        if long
        else _add(_add(quote, initial), _div(extra, fee_factor))
    )
    rows: list[BybitLiquidationRowProofV1] = []
    for row in table.rows:
        upper = _number(row.upper_inclusive_usdt.value, source=True)
        maintenance = _number(row.maintenance_margin_rate.value, source=True)
        deduction = _number(row.mm_deduction_usdt.value, source=True, zero=True)
        slope = _sub(Fraction(1), maintenance) if long else _add(Fraction(1), maintenance)
        row_intercept = _sub(intercept, deduction) if long else _add(intercept, deduction)
        candidate = _div(row_intercept, _mul(q, slope))
        candidate_quote = _mul(q, candidate)
        consistent = (
            _cmp(candidate, Fraction(0)) > 0
            and _cmp(candidate_quote, row.lower_exclusive_usdt) > 0
            and _cmp(candidate_quote, upper) <= 0
        )
        rows.append(
            BybitLiquidationRowProofV1(
                str(row.risk_id),
                row.lower_exclusive_usdt,
                upper,
                row_intercept,
                slope,
                candidate,
                _sub(_mul(slope, row.lower_exclusive_usdt), row_intercept),
                _sub(_mul(slope, upper), row_intercept),
                consistent,
            )
        )
    valid = tuple(row for row in rows if row.self_consistent)
    if long and _cmp(intercept, Fraction(0)) <= 0:
        if valid:
            return BybitModelRejectedV1(
                "REJECTED",
                "LIQUIDATION",
                "PIECEWISE_INCONSISTENT",
                "RISK_V2_LIQUIDATION_UNVERIFIED",
                leverage,
                (),
            )
        outcome: BybitPositiveRootPointV1 | BybitNoPositiveRootPointV1 = BybitNoPositiveRootPointV1(
            "NO_POSITIVE_ROOT",
            "NON_POSITIVE_LONG_INTERCEPT",
            intercept,
            _sub(Fraction(0), intercept),
        )
    elif len(valid) == 1:
        outcome = BybitPositiveRootPointV1(
            "POSITIVE_ROOT", valid[0].candidate_price, valid[0].tier_id
        )
    else:
        return BybitModelRejectedV1(
            "REJECTED",
            "LIQUIDATION",
            "ROOT_OUTSIDE_CAPTURED_TABLE" if not valid else "PIECEWISE_INCONSISTENT",
            "RISK_V2_LIQUIDATION_UNVERIFIED",
            leverage,
            (),
        )
    return (
        outcome,
        tuple(rows),
        {
            "quantity": q,
            "entry_price": price,
            "leverage": lev,
            "extra_margin": extra,
            "taker_rate": fee,
        },
    )


def _proof_payload(
    value: BybitLiquidationCalculationV1,
    *,
    side: Side,
    economics: dict[str, Fraction],
    table: BybitLinearTierTableV1,
) -> dict[str, object]:
    payload = _calculation_payload(value)
    payload.pop("proof_sha256")
    payload.update(
        side=side, **{name: _rational_payload_v1(number) for name, number in economics.items()}
    )
    payload["table_source_sha256"] = bybit_tier_table_sha256_v1(table)
    payload["table_identity"] = {
        "instrument_pin": bybit_instrument_pin_sha256_v1(table.instrument_pin),
        "reference": _observation_payload_v2(table.reference),
        "raw_manifest_sha256": table.raw_manifest_sha256,
        "full_table_sha256": table.full_table_sha256,
        "rate_encoding_revision": table.rate_encoding_revision,
        "deduction_rule_revision": table.deduction_rule_revision,
    }
    return payload


def _converted(error: RiskV2Rejected, leverage: Decimal) -> BybitModelRejectedV1:
    for reason in (
        "INPUT_MISMATCH",
        "RESOURCE_LIMIT",
        "EVIDENCE_INVALID",
        "SOURCE_INVALID",
        "PROFILE_UNSUPPORTED",
    ):
        if error.code in _REASON_CODES[reason]:
            candidate: Decimal | None = None
            try:
                if _cmp(_number(leverage), Fraction(1)) >= 0:
                    candidate = leverage
            except RiskV2Rejected as invalid:
                if invalid.code not in ("RISK_V2_INPUT_INVALID", "RISK_V2_RESOURCE_LIMIT"):
                    raise
            return BybitModelRejectedV1(
                "REJECTED", "LIQUIDATION", reason, error.code, candidate, ()
            )
    raise error


def solve_bybit_liquidation_v1(
    *,
    side: Side,
    quantity: Decimal,
    entry_price: Decimal,
    leverage: Decimal,
    extra_margin: Decimal,
    taker_rate: Decimal,
    table: BybitLinearTierTableV1,
) -> BybitLiquidationCalculationV1 | BybitModelRejectedV1:
    try:
        parts = _lp_parts(
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            leverage=leverage,
            extra_margin=extra_margin,
            taker_rate=taker_rate,
            table=table,
        )
        if isinstance(parts, BybitModelRejectedV1):
            return parts
        outcome, rows, economics = parts
        value = BybitLiquidationCalculationV1(
            outcome,
            table.rows[-1].upper_inclusive_usdt.value,
            table.full_table_sha256,
            table.raw_manifest_sha256,
            tuple(row.tier_id for row in rows),
            rows,
            "BYBIT_LINEAR_FULL_TABLE_LP_V1",
            "0" * 64,
        )
        proof = _canonical_sha256_v2(
            "BybitLiquidationProofV1",
            _proof_payload(value, side=side, economics=economics, table=table),
        )
        return replace(value, proof_sha256=proof)
    except RiskV2Rejected as error:
        return _converted(error, leverage)


def bybit_margin_point_sha256_v1(value: BybitMarginPointV1) -> str:
    return _canonical_sha256_v2("BybitMarginPointV1", _margin_payload(value))


def bybit_reserve_bridge_sha256_v1(value: BybitReserveBridgeV1) -> str:
    return _canonical_sha256_v2("BybitReserveBridgeV1", _bridge_payload(value))


def bybit_liquidation_row_sha256_v1(value: BybitLiquidationRowProofV1) -> str:
    return _canonical_sha256_v2("BybitLiquidationRowProofV1", _row_payload(value))


def bybit_liquidation_calculation_sha256_v1(value: BybitLiquidationCalculationV1) -> str:
    return _canonical_sha256_v2("BybitLiquidationCalculationV1", _calculation_payload(value))


def bybit_liquidation_proof_sha256_v1(
    value: BybitLiquidationCalculationV1,
    *,
    side: Side,
    quantity: Decimal,
    entry_price: Decimal,
    leverage: Decimal,
    extra_margin: Decimal,
    taker_rate: Decimal,
    table: BybitLinearTierTableV1,
) -> str:
    _calculation_payload(value)
    parts = _lp_parts(
        side=side,
        quantity=quantity,
        entry_price=entry_price,
        leverage=leverage,
        extra_margin=extra_margin,
        taker_rate=taker_rate,
        table=table,
    )
    if isinstance(parts, BybitModelRejectedV1):
        _reject(parts.public_code)
    outcome, rows, economics = parts
    if (
        value.outcome != outcome
        or value.rows != rows
        or value.domain_upper_quote.as_tuple()
        != table.rows[-1].upper_inclusive_usdt.value.as_tuple()
        or value.full_table_sha256 != table.full_table_sha256
        or value.raw_manifest_sha256 != table.raw_manifest_sha256
    ):
        _reject("RISK_V2_SOURCE_MISMATCH")
    digest = _canonical_sha256_v2(
        "BybitLiquidationProofV1",
        _proof_payload(value, side=side, economics=economics, table=table),
    )
    if value.proof_sha256 != digest:
        _reject("RISK_V2_SOURCE_MISMATCH")
    return digest


def bybit_candidate_evaluation_sha256_v1(value: BybitCandidateEvaluationV1) -> str:
    return _canonical_sha256_v2("BybitCandidateEvaluationV1", _candidate_payload(value))


@dataclass(frozen=True)
class BybitLeverageSearchV1:
    point_input_sha256: str
    selected: BybitCandidateEvaluationV1
    evaluated_prefix: tuple[BybitCandidateEvaluationV1, ...]
    selection_rule: Literal["SMALLEST_FEASIBLE_PERMITTED_GRID_V1"]
    model_rule_revision: Literal["BYBIT_LINEAR_ISOLATED_POINT_MODEL_V1"]

    def __post_init__(self) -> None:
        _search_payload(self)


def _search_payload(value: BybitLeverageSearchV1) -> dict[str, object]:
    _fields_v2(value, BybitLeverageSearchV1)
    _digest_v2(value.point_input_sha256)
    _literal_v2(value.selection_rule, ("SMALLEST_FEASIBLE_PERMITTED_GRID_V1",))
    _literal_v2(value.model_rule_revision, ("BYBIT_LINEAR_ISOLATED_POINT_MODEL_V1",))
    _tuple(value.evaluated_prefix, 4096)
    if not value.evaluated_prefix:
        _reject()
    selected = bybit_candidate_evaluation_sha256_v1(value.selected)
    prefix = tuple(bybit_candidate_evaluation_sha256_v1(c) for c in value.evaluated_prefix)
    for i, candidate in enumerate(value.evaluated_prefix):
        if candidate.grid_index != i or bool(candidate.failure_codes) != (i < len(prefix) - 1):
            _reject()
        if (
            i
            and _cmp(_number(candidate.leverage), _number(value.evaluated_prefix[i - 1].leverage))
            <= 0
        ):
            _reject()
    if value.selected != value.evaluated_prefix[-1] or value.selected.failure_codes:
        _reject()
    return {
        "point_input_sha256": value.point_input_sha256,
        "selected": selected,
        "evaluated_prefix": prefix,
        "selection_rule": value.selection_rule,
        "model_rule_revision": value.model_rule_revision,
    }


def _age(stamp: datetime, *, now: datetime, maximum: Fraction) -> None:
    stamp = _utc_v2(stamp)
    if stamp > now:
        _reject("RISK_V2_FUTURE_EVIDENCE")
    delta = now - stamp
    seconds = Fraction(
        (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds, 1_000_000
    )
    if _cmp(seconds, maximum) > 0:
        _reject("RISK_V2_STALE_EVIDENCE")


def _source_server(source: BybitModelSourceRefV1) -> datetime | None:
    if source.envelope_time_ms is None:
        return None
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=source.envelope_time_ms)


def _point_sources(inputs: BybitLinearPointInputV1, *, now: datetime) -> None:
    """Only represented links; opaque raw/mark payload preimages belong to issuers."""
    envelope, profile, fee = inputs.envelope, inputs.profile, inputs.fee
    pin = envelope.instrument_pin
    mark_source = inputs.selected_mark.reference.source
    mark_ref = inputs.selected_mark_reference
    if (
        inputs.table.instrument_pin != pin
        or profile.symbol != pin.key.symbol
        or mark_source.account_scope is not None
        or mark_source.source_environment != pin.source_environment
        or dict(mark_source.query).get("category") != "linear"
        or dict(mark_source.query).get("symbol") != pin.key.symbol
        or mark_ref.source_id != mark_source.source_id
        or mark_ref.source_environment != mark_source.source_environment
        or mark_ref.received_at != mark_source.received_at
        or mark_ref.server_observed_at != _source_server(mark_source)
    ):
        _reject("RISK_V2_SOURCE_MISMATCH")
    if (
        profile.selected_position_quantity.reference.source not in profile.source_records
        or profile.auto_add_margin.source not in profile.source_records
    ):
        _reject("RISK_V2_SOURCE_MISMATCH")
    if (
        _number(profile.selected_position_quantity.value, zero=True) != 0
        or profile.selected_open_order_ids
        or profile.selected_conditional_order_ids
        or profile.local_unresolved_entry_ids
        or profile.auto_add_margin.wire_kind != "INTEGER"
        or profile.auto_add_margin.wire_spelling != "0"
        or _number(profile.prospective_extra_margin_usdt, zero=True) != 0
        or fee.behaviour.lpa_perp is not True
        or fee.behaviour.futures_mnt_fee_deduction is not False
        or fee.behaviour.delta_enable is not False
    ):
        _reject("RISK_V2_UNSUPPORTED_MODE")
    for value in (
        fee.account_taker_rate.value,
        fee.account_maker_rate.value,
        fee.closing_order_reserve_nonvip_rate.value,
    ):
        _rate(value)
    private = (
        *profile.source_records,
        fee.account_taker_rate.reference.source,
        fee.account_maker_rate.reference.source,
        fee.behaviour.source,
    )
    f0 = fee.closing_order_reserve_nonvip_rate.reference
    if type(f0) is BybitModelFieldRefV1:
        private += (f0.source,)
    elif type(f0) is BybitFeeDocumentRefV1:
        if f0.applicability_revision != fee.nonvip_applicability_revision:
            _reject("RISK_V2_REVISION_MISMATCH")
        _age(f0.captured_at, now=now, maximum=_number(inputs.max_evidence_age_seconds, zero=True))
    # Child digests have already rejected hostile fields before these comparisons.
    observations: dict[str, BybitModelSourceRefV1] = {}
    generations: dict[str, str] = {}
    for source in (*inputs.table.pages, mark_source, *private):
        previous = observations.setdefault(source.observation_id, source)
        generation = generations.setdefault(source.source_id, source.source_generation)
        if previous != source or generation != source.source_generation:
            _reject("RISK_V2_SOURCE_MISMATCH")
        if (pin.source_environment == "FIXTURE") != (source.source_environment == "FIXTURE"):
            _reject("RISK_V2_SOURCE_MISMATCH")
        query = dict(source.query)
        if ("symbol" in query and query["symbol"] != pin.key.symbol) or (
            "category" in query and query["category"] != "linear"
        ):
            _reject("RISK_V2_SOURCE_MISMATCH")
        maximum = _number(
            inputs.max_quote_age_seconds
            if source == mark_source
            else inputs.max_evidence_age_seconds,
            zero=True,
        )
        for stamp in (source.request_started_at, source.received_at, _source_server(source)):
            if stamp is not None:
                _age(stamp, now=now, maximum=maximum)
    for source in private:
        if (
            source.account_scope != envelope.scope.product_scope.account
            or source.source_environment
            not in ("FIXTURE", envelope.scope.product_scope.account.environment)
        ):
            _reject("RISK_V2_SCOPE_MISMATCH")
    for stamp in (
        pin.response_time,
        pin.received_at,
        inputs.table.reference.server_observed_at,
        inputs.table.reference.received_at,
    ):
        _age(stamp, now=now, maximum=_number(inputs.max_evidence_age_seconds, zero=True))
    for stamp in (mark_ref.server_observed_at, mark_ref.received_at):
        _age(stamp, now=now, maximum=_number(inputs.max_quote_age_seconds, zero=True))
    if _utc_v2(inputs.valid_until) > _utc_v2(envelope.valid_until):
        _reject("RISK_V2_CAUSAL_ORDER_INVALID")
    if now >= _utc_v2(inputs.valid_until) or now >= _utc_v2(envelope.valid_until):
        _reject("RISK_V2_EXPIRED")


def _point_grid(inputs: BybitLinearPointInputV1) -> tuple[Fraction, Fraction, Fraction]:
    quantity = _number(inputs.envelope.base_quantity)
    mark = _number(inputs.selected_mark.value, source=True)
    upper = _number(inputs.admission_upper_quote)
    if (
        _cmp(_mul(quantity, mark), upper) != 0
        or _cmp(upper, _number(inputs.table.rows[-1].upper_inclusive_usdt.value, source=True)) > 0
    ):
        _reject()
    admission = tuple(row for row in inputs.table.rows if _cmp(row.lower_exclusive_usdt, upper) < 0)
    if inputs.admission_covered_tier_ids != tuple(str(row.risk_id) for row in admission):
        _reject()
    lower, last, step = (
        _number(v)
        for v in (
            inputs.continuous_leverage_lower,
            inputs.continuous_leverage_upper,
            inputs.leverage_step,
        )
    )
    for row in admission:
        if _cmp(last, _number(row.max_leverage.value, source=True)) > 0:
            _reject()
    # Preflight the public lattice's unreduced ratio products using the root's
    # stronger rule, then use the accepted zero-origin helper unchanged.
    for bound in (lower, last):
        _product(bound.numerator, step.denominator)
        _product(bound.denominator, step.numerator)
    first, final = zero_origin_lattice_v2(lower, last, step)
    _fraction(first)
    _fraction(final)
    return first, final, step


def _search_rejection(
    error: RiskV2Rejected,
    *,
    phase: Phase,
    leverage: Decimal | None,
    prefix: tuple[BybitCandidateEvaluationV1, ...],
) -> BybitModelRejectedV1:
    # B owns this exact finite map; all unlisted errors still propagate.
    converted = _converted(error, leverage if leverage is not None else Decimal(1))
    return replace(converted, phase=phase, candidate_leverage=leverage, evaluated_prefix=prefix)


def select_smallest_bybit_leverage_v1(
    inputs: BybitLinearPointInputV1, *, now: datetime
) -> BybitLeverageSearchV1 | BybitModelRejectedV1:
    prefix: tuple[BybitCandidateEvaluationV1, ...] = ()
    leverage: Decimal | None = None
    phase: Phase = "SOURCE"
    try:
        point_hash = bybit_point_input_sha256_v1(inputs)
        now = _utc_v2(now)
        _point_sources(inputs, now=now)
        first, last, step = _point_grid(inputs)
        side: Side = "LONG" if inputs.envelope.side == "BUY" else "SHORT"
        stop = _number(inputs.stop_price, source=True)
        price = _number(inputs.envelope.limit_price, source=True)
        if (side == "LONG" and _cmp(stop, price) >= 0) or (
            side == "SHORT" and _cmp(stop, price) <= 0
        ):
            _reject()
        current = first
        phase = "SEARCH"
        while _cmp(current, last) <= 0:
            leverage = finite_decimal_v2(current)
            if len(prefix) == 4096:
                _reject("RISK_V2_RESOURCE_LIMIT")
            _product(current.numerator, step.denominator)
            _product(current.denominator, step.numerator)
            if not is_zero_origin_member_v2(current, first=first, last=last, step=step):
                _reject()
            failures: list[Failure] = []
            for row in inputs.table.rows:
                if str(row.risk_id) in inputs.admission_covered_tier_ids:
                    if _cmp(current, _number(row.max_leverage.value, source=True)) > 0:
                        _reject()
                    if (
                        _cmp(
                            _mul(current, _number(row.initial_margin_rate.value, source=True)),
                            Fraction(1),
                        )
                        > 0
                        and "IMR" not in failures
                    ):
                        failures.append("IMR")
            phase = "MARGIN"
            margin = calculate_bybit_margin_point_v1(
                side=side,
                quantity=inputs.envelope.base_quantity,
                entry_limit=inputs.envelope.limit_price,
                mark=inputs.selected_mark.value,
                leverage=leverage,
                taker_rate=inputs.fee.account_taker_rate.value,
                order_close_rate=inputs.fee.closing_order_reserve_nonvip_rate.value,
            )
            phase = "BRIDGE"
            reserve = bridge_bybit_reserve_v1(
                margin,
                planned_cost_allowance_quote=inputs.planned_cost_allowance_quote,
                reserve_quantum_usdt=_Q,
                reserve_rounding_revision="APP_RESERVE_CEILING_QUANTUM_V1",
            )
            if (
                _cmp(
                    _number(reserve.required_reservation_quote, zero=True),
                    _number(inputs.capacity_quote, zero=True),
                )
                > 0
            ):
                failures.append("CAPACITY")
            phase = "LIQUIDATION"
            liquidation = solve_bybit_liquidation_v1(
                side=side,
                quantity=inputs.envelope.base_quantity,
                entry_price=inputs.envelope.limit_price,
                leverage=leverage,
                extra_margin=inputs.profile.prospective_extra_margin_usdt,
                taker_rate=inputs.fee.account_taker_rate.value,
                table=inputs.table,
            )
            if isinstance(liquidation, BybitModelRejectedV1):
                return replace(liquidation, candidate_leverage=leverage, evaluated_prefix=prefix)
            # Standalone candidate/hash validation cannot authenticate the actual
            # LP originals. Replay B with this point's five inputs and full table.
            bybit_liquidation_proof_sha256_v1(
                liquidation,
                side=side,
                quantity=inputs.envelope.base_quantity,
                entry_price=inputs.envelope.limit_price,
                leverage=leverage,
                extra_margin=inputs.profile.prospective_extra_margin_usdt,
                taker_rate=inputs.fee.account_taker_rate.value,
                table=inputs.table,
            )
            buffer_result: Literal["GAP_SATISFIED", "MODEL_NO_POSITIVE_TRIGGER"] | None
            if isinstance(liquidation.outcome, BybitNoPositiveRootPointV1):
                buffer_result = "MODEL_NO_POSITIVE_TRIGGER"
            else:
                gap = (
                    _sub(stop, liquidation.outcome.exact_price)
                    if side == "LONG"
                    else _sub(liquidation.outcome.exact_price, stop)
                )
                buffer_result = (
                    "GAP_SATISFIED"
                    if _cmp(gap, Fraction(0)) > 0
                    and _cmp(
                        gap, _mul(_number(inputs.liquidation_buffer_fraction, zero=True), price)
                    )
                    >= 0
                    else None
                )
                if buffer_result is None:
                    failures.append("LIQUIDATION_BUFFER")
            candidate = BybitCandidateEvaluationV1(
                len(prefix), leverage, margin, reserve, liquidation, buffer_result, tuple(failures)
            )
            phase = "SEARCH"
            if not failures:
                result = BybitLeverageSearchV1(
                    point_hash,
                    candidate,
                    prefix + (candidate,),
                    "SMALLEST_FEASIBLE_PERMITTED_GRID_V1",
                    "BYBIT_LINEAR_ISOLATED_POINT_MODEL_V1",
                )
                bybit_leverage_search_sha256_v1(result)
                return result
            prefix += (candidate,)
            if _cmp(current, last) == 0:
                break
            current = _add(current, step)
        return BybitModelRejectedV1(
            "REJECTED",
            "SEARCH",
            "NO_FEASIBLE_LEVERAGE",
            "RISK_V2_LEVERAGE_UNSATISFIABLE",
            None,
            prefix,
        )
    except RiskV2Rejected as error:
        return _search_rejection(error, phase=phase, leverage=leverage, prefix=prefix)


def bybit_leverage_search_sha256_v1(value: BybitLeverageSearchV1) -> str:
    return _canonical_sha256_v2("BybitLeverageSearchV1", _search_payload(value))
