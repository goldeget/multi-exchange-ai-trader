"""Independent literal and hostile-boundary tests for the scalar leaf."""

import subprocess
import sys
from collections.abc import Callable
from decimal import (
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    ROUND_UP,
    Decimal,
    Inexact,
    Rounded,
    getcontext,
    localcontext,
)
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from multi_exchange_trader.execution.risk_v2_math import (
    RiskV2Rejected,
    ceil_ratio_v2,
    decimal_fraction_v2,
    decimal_text_v2,
    display_ratio_v2,
    finite_decimal_v2,
    floor_quantity_v2,
    floor_ratio_v2,
    is_zero_origin_member_v2,
    zero_origin_lattice_v2,
)


def test_literal_ceil80_display_and_exact_financial_floor() -> None:
    assert display_ratio_v2(Fraction(12), Fraction(7)) == Decimal("1." + "714285" * 13 + "8")
    assert floor_quantity_v2(Fraction(14) - Fraction(1, 10**200), Fraction(7), Fraction(1)) == 1
    assert floor_quantity_v2(Fraction(14), Fraction(7), Fraction(1)) == 2


@pytest.mark.parametrize(
    "delta,expected", [(-1, Fraction(0)), (0, Fraction(1, 10)), (1, Fraction(1, 10))]
)
def test_exact_step_boundary(delta: int, expected: Fraction) -> None:
    budget = Fraction(6, 5) + Fraction(delta, 10**200)
    quantity = floor_quantity_v2(budget, Fraction(12), Fraction(1, 10))
    assert quantity == expected
    assert quantity * 12 <= budget < (quantity + Fraction(1, 10)) * 12


def test_zero_origin_grid_literal_and_empty_interval() -> None:
    first, last = zero_origin_lattice_v2(Fraction(11, 10), Fraction(14, 10), Fraction(3, 10))
    assert (first, last) == (Fraction(6, 5), Fraction(6, 5))
    for value, expected in [
        (Fraction(11, 10), False),
        (Fraction(6, 5), True),
        (Fraction(14, 10), False),
    ]:
        assert (
            is_zero_origin_member_v2(value, first=first, last=last, step=Fraction(3, 10))
            is expected
        )
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        zero_origin_lattice_v2(Fraction(121, 100), Fraction(139, 100), Fraction(3, 10))


@pytest.mark.parametrize(
    "numerator,denominator,floor,ceil", [(0, 7, 0, 0), (12, 7, 1, 2), (14, 7, 2, 2), (1, 100, 0, 1)]
)
def test_floor_ceil_literals(numerator: int, denominator: int, floor: int, ceil: int) -> None:
    assert floor_ratio_v2(Fraction(numerator), Fraction(denominator)) == floor
    assert ceil_ratio_v2(Fraction(numerator), Fraction(denominator)) == ceil


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0", Fraction(0)),
        ("-0E+3", Fraction(0)),
        ("-12.50", Fraction(-25, 2)),
        ("1E+3", Fraction(1000)),
        ("0.000125", Fraction(1, 8000)),
    ],
)
def test_decimal_fraction_literal(text: str, expected: Fraction) -> None:
    assert decimal_fraction_v2(Decimal(text)) == expected


@pytest.mark.parametrize(
    "value,text",
    [
        (Fraction(0), "0"),
        (Fraction(-1, 8), "-0.125"),
        (Fraction(3, 40), "0.075"),
        (Fraction(100), "100"),
        (Fraction(12, 5), "2.4"),
    ],
)
def test_finite_reconstruction_literals(value: Fraction, text: str) -> None:
    assert finite_decimal_v2(value).as_tuple() == Decimal(text).as_tuple()


@pytest.mark.parametrize(
    "text,canonical",
    [
        ("100.0", "100.0"),
        ("1E3", "1E+3"),
        ("0.00", "0.00"),
        ("-0E3", "-0E+3"),
        ("0.0000010", "0.0000010"),
        ("0.00000010", "1.0E-7"),
        ("-123E4", "-1.23E+6"),
        ("0E-7", "0E-7"),
        ("-0", "-0"),
    ],
)
def test_tuple_preserving_canonical_text(text: str, canonical: str) -> None:
    value = Decimal(text)
    assert decimal_text_v2(value) == canonical
    assert Decimal(canonical).as_tuple() == value.as_tuple()


def test_derived_2001_digit_sum_and_internal_5001_digit_integer() -> None:
    total = decimal_fraction_v2(Decimal("1E1000")) + decimal_fraction_v2(Decimal("1E-1000"))
    result = finite_decimal_v2(total)
    assert result.as_tuple() == (0, (1,) + (0,) * 1999 + (1,), -1000)
    assert decimal_fraction_v2(result) == total
    huge = 10**5000 + 7  # Never routes the fixture through Python's int-string conversion.
    reconstructed = finite_decimal_v2(Fraction(huge))
    assert reconstructed.as_tuple() == (0, (1,) + (0,) * 4999 + (7,), 0)
    assert Decimal(decimal_text_v2(reconstructed)).as_tuple() == reconstructed.as_tuple()
    assert decimal_fraction_v2(reconstructed) == Fraction(huge)


def _context_snapshot() -> tuple[object, ...]:
    context = getcontext()
    return (
        context.prec,
        context.rounding,
        context.Emin,
        context.Emax,
        context.capitals,
        context.clamp,
        dict(context.traps),
        dict(context.flags),
    )


@pytest.mark.parametrize("rounding", [ROUND_UP, ROUND_DOWN, ROUND_HALF_EVEN])
@pytest.mark.parametrize("capitals", [0, 1])
def test_pathological_ambient_context_untouched(rounding: str, capitals: int) -> None:
    with localcontext() as context:
        context.prec = 1
        context.rounding = rounding
        context.Emin = -1
        context.Emax = 1
        context.capitals = capitals
        context.clamp = 1
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        context.flags[Inexact] = True
        before = _context_snapshot()
        test_literal_ceil80_display_and_exact_financial_floor()
        test_derived_2001_digit_sum_and_internal_5001_digit_integer()
        test_zero_origin_grid_literal_and_empty_interval()
        assert display_ratio_v2(Fraction(1), Fraction(8)).as_tuple() == Decimal("0.125").as_tuple()
        assert display_ratio_v2(Fraction(0), Fraction(9)).as_tuple() == Decimal(0).as_tuple()
        assert decimal_text_v2(Decimal("-0E3")) == "-0E+3"
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
            finite_decimal_v2(Fraction(1, 3))
        assert _context_snapshot() == before


class _Poison:
    def __str__(self) -> str:
        raise AssertionError("untrusted str hook")

    def __repr__(self) -> str:
        raise AssertionError("untrusted repr hook")

    def __eq__(self, other: object) -> bool:
        raise AssertionError("untrusted equality hook")

    def __hash__(self) -> int:
        raise AssertionError("untrusted hash hook")


class _DecimalSubclass(Decimal):
    def as_tuple(self) -> Any:
        raise AssertionError("subclass hook")


class _FractionSubclass(Fraction):
    @property
    def numerator(self) -> int:
        raise AssertionError("subclass hook")


class _StringSubclass(str):
    def __hash__(self) -> int:
        raise AssertionError("subclass hook")


class _IntSubclass(int):
    def __le__(self, other: object) -> bool:
        raise AssertionError("subclass hook")


def _forged_fraction(numerator: object, denominator: object) -> Fraction:
    value = object.__new__(Fraction)
    object.__setattr__(value, "_numerator", numerator)
    object.__setattr__(value, "_denominator", denominator)
    return value


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        1,
        1.0,
        "1",
        _Poison(),
        _DecimalSubclass("1"),
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
def test_decimal_inputs_fail_closed_without_hooks(value: Any) -> None:
    for helper in (decimal_fraction_v2, decimal_text_v2):
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$") as error:
            helper(value)
        assert error.value.code == "RISK_V2_INPUT_INVALID"
        assert error.value.args == ("RISK_V2_INPUT_INVALID",)


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        1,
        1.0,
        "1",
        Decimal(1),
        _Poison(),
        _FractionSubclass(1),
        object.__new__(Fraction),
        _forged_fraction(_Poison(), 1),
        _forged_fraction(1, _Poison()),
        _forged_fraction(True, 1),
        _forged_fraction(1, _IntSubclass(2)),
        _forged_fraction(1, 0),
        _forged_fraction(1, -2),
    ],
)
def test_fraction_inputs_fail_closed_without_hooks(value: Any) -> None:
    operations: list[Callable[[], object]] = [
        lambda: finite_decimal_v2(value),
        lambda: floor_ratio_v2(value, Fraction(1)),
        lambda: ceil_ratio_v2(Fraction(1), value),
        lambda: floor_quantity_v2(Fraction(1), value, Fraction(1)),
        lambda: zero_origin_lattice_v2(Fraction(1), value, Fraction(1)),
        lambda: is_zero_origin_member_v2(
            value, first=Fraction(1), last=Fraction(2), step=Fraction(1)
        ),
        lambda: display_ratio_v2(Fraction(1), value),
    ]
    for operation in operations:
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
            operation()


def test_forged_unreduced_fraction_is_safely_normalized() -> None:
    value = _forged_fraction(6, 8)
    assert finite_decimal_v2(value) == Decimal("0.75")
    assert floor_ratio_v2(value, Fraction(1, 4)) == 3
    assert display_ratio_v2(value, Fraction(3)) == Decimal("0.25")


@pytest.mark.parametrize(
    "operation",
    [
        lambda: floor_ratio_v2(Fraction(-1), Fraction(1)),
        lambda: ceil_ratio_v2(Fraction(1), Fraction(0)),
        lambda: floor_quantity_v2(Fraction(-1), Fraction(1), Fraction(1)),
        lambda: floor_quantity_v2(Fraction(1), Fraction(0), Fraction(1)),
        lambda: floor_quantity_v2(Fraction(1), Fraction(1), Fraction(0)),
        lambda: zero_origin_lattice_v2(Fraction(2), Fraction(1), Fraction(1)),
        lambda: zero_origin_lattice_v2(Fraction(0), Fraction(1), Fraction(1)),
        lambda: is_zero_origin_member_v2(
            Fraction(1), first=Fraction(2), last=Fraction(1), step=Fraction(1)
        ),
        lambda: is_zero_origin_member_v2(
            Fraction(1), first=Fraction(1), last=Fraction(2), step=Fraction(0)
        ),
        lambda: display_ratio_v2(Fraction(-1), Fraction(1)),
        lambda: display_ratio_v2(Fraction(1), Fraction(0)),
        lambda: finite_decimal_v2(Fraction(1, 7)),
    ],
)
def test_sign_zero_order_and_nonterminating_rejection(operation: Any) -> None:
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        operation()


@pytest.mark.parametrize(
    "operation",
    [
        lambda: decimal_fraction_v2(Decimal((0, (1,), 65537))),
        lambda: decimal_text_v2(Decimal((0, (0,), -65537))),
        lambda: decimal_fraction_v2(Decimal((0, (1,) * 65537, 0))),
        lambda: finite_decimal_v2(Fraction(1 << 1000000)),
        lambda: finite_decimal_v2(Fraction(1, 2**65537)),
        lambda: finite_decimal_v2(Fraction(10**65536)),
        lambda: floor_ratio_v2(Fraction(1 << 600000), Fraction(1, (1 << 600001) - 1)),
    ],
)
def test_resource_guards_use_fixed_code(operation: Any) -> None:
    before = sys.get_int_max_str_digits()
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_RESOURCE_LIMIT$"):
        operation()
    assert sys.get_int_max_str_digits() == before


def test_comparison_work_limit_is_checked_before_large_cross_products() -> None:
    huge = Fraction((1 << 600000) + 1, 3)
    tiny = Fraction(1, (1 << 600001) - 1)
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_RESOURCE_LIMIT$"):
        is_zero_origin_member_v2(tiny, first=huge, last=huge, step=Fraction(1))


@pytest.mark.parametrize(
    "code",
    [None, True, 1, "UNKNOWN_PRIVATE_DETAIL", _StringSubclass("RISK_V2_INPUT_INVALID"), _Poison()],
)
def test_rejection_constructor_rejects_unknown_codes_without_hooks(code: Any) -> None:
    with pytest.raises(ValueError, match="^INVALID_RISK_V2_REJECTION_CODE$") as error:
        RiskV2Rejected(code)
    assert type(error.value) is ValueError


def test_complete_fixed_rejection_vocabulary() -> None:
    codes = [
        "INPUT_INVALID",
        "RESOURCE_LIMIT",
        "SCOPE_MISMATCH",
        "SOURCE_MISMATCH",
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
    for suffix in codes:
        code = "RISK_V2_" + suffix
        error = RiskV2Rejected(code)
        assert error.code == code
        assert str(error) == code
        assert error.args == (code,)


def test_fresh_pure_import_and_evaluation_under_effect_guards() -> None:
    module = (
        Path(__file__).resolve().parents[2] / "src/multi_exchange_trader/execution/risk_v2_math.py"
    )
    program = r"""
import builtins, decimal, fractions, os, pathlib, sys, time
source = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
code = compile(source, "risk_v2_math.py", "exec")
def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("forbidden external effect")
class NoEnvironment(dict):
    __getitem__ = get = __iter__ = __contains__ = forbidden
os.environ = NoEnvironment()
os.getenv = forbidden
time.time = time.monotonic = time.perf_counter = forbidden
def audit(event: str, args: tuple[object, ...]) -> None:
    if event == "open" or event.startswith(("socket.", "subprocess.", "ctypes.", "os.system", "os.spawn")):
        forbidden()
sys.addaudithook(audit)
namespace = {"__name__": "isolated_risk_v2_math"}
exec(code, namespace)
F = fractions.Fraction
assert namespace["decimal_fraction_v2"](decimal.Decimal("-12.50")) == F(-25, 2)
assert namespace["finite_decimal_v2"](F(1, 8)) == decimal.Decimal("0.125")
assert namespace["decimal_text_v2"](decimal.Decimal("-0E3")) == "-0E+3"
assert namespace["floor_ratio_v2"](F(12), F(7)) == 1
assert namespace["ceil_ratio_v2"](F(12), F(7)) == 2
assert namespace["floor_quantity_v2"](F(14)-F(1,10**200), F(7), F(1)) == 1
first,last = namespace["zero_origin_lattice_v2"](F(11,10), F(14,10), F(3,10))
assert namespace["is_zero_origin_member_v2"](F(6,5), first=first, last=last, step=F(3,10))
assert namespace["display_ratio_v2"](F(12),F(7)) == decimal.Decimal("1."+"714285"*13+"8")
print("PURE_IMPORT_AND_NINE_HELPERS_OK")
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(module)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PURE_IMPORT_AND_NINE_HELPERS_OK"
