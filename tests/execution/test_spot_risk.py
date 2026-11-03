"""FIXTURE-only arithmetic; E16 ownership/debt/fee/protection gates stay separate.

These functions receive explicit already-projected planned/aggregate amounts.
They cannot authenticate fresh account-wide debt coverage, owned lots, fee
currency/completeness, or current protection receipts. A raw wallet balance is
not an owned-lot producer. A successful result does not permit SPOT ENTRY.
"""

from dataclasses import FrozenInstanceError, fields
from decimal import (
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    ROUND_UP,
    Decimal,
    Inexact,
    Rounded,
    localcontext,
)
from typing import cast

import pytest

from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected
from multi_exchange_trader.execution.spot_risk import (
    SpotBaseRequirementV2,
    SpotCashRequirementV2,
    check_spot_cash_buy_v2,
    check_spot_owned_sell_v2,
)


def buy(**changes: Decimal) -> SpotCashRequirementV2:
    inputs = {
        "quantity": Decimal(2),
        "buy_ceiling": Decimal(100),
        "fee_quote_upper": Decimal(3),
        "available_quote": Decimal(250),
        "held_quote": Decimal(20),
        "max_order_quote_debit": Decimal(203),
        "max_total_quote_commitment": Decimal(223),
    }
    inputs.update(changes)
    return check_spot_cash_buy_v2(**inputs)


def sell(**changes: Decimal) -> SpotBaseRequirementV2:
    inputs = {
        "quantity": Decimal(2),
        "owned_available_base": Decimal(3),
        "held_base": Decimal("0.5"),
        "fee_base_upper": Decimal("0.01"),
    }
    inputs.update(changes)
    return check_spot_owned_sell_v2(**inputs)


def test_buy_includes_quote_fee_and_all_held_quote() -> None:
    result = buy()
    assert result.quote_debit == Decimal(203)
    assert result.remaining_quote == Decimal(27)


def test_buy_enforces_order_quote_debit_cap() -> None:
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_EXECUTION_QUOTE_LIMIT$"):
        buy(max_order_quote_debit=Decimal(202))


def test_buy_enforces_held_plus_proposed_commitment_cap() -> None:
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_ACCOUNT_ADMISSION_LIMIT$"):
        buy(max_total_quote_commitment=Decimal(222))


def test_sell_consumes_owned_base_and_base_fee() -> None:
    result = sell()
    assert result.base_debit == Decimal("2.01")
    assert result.remaining_owned_base == Decimal("0.49")


@pytest.mark.parametrize(
    ("available", "accepted"),
    [("223", True), ("222", False), ("222.999999999999999999999999999999", False)],
)
def test_buy_cash_equality_and_one_unit_under(available: str, accepted: bool) -> None:
    if accepted:
        assert buy(available_quote=Decimal(available)).remaining_quote == Decimal(0)
    else:
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_MARGIN_LIMIT$"):
            buy(available_quote=Decimal(available))


@pytest.mark.parametrize(
    ("owned", "accepted"), [("2.51", True), ("2.50", False), ("2.509999999999999999999", False)]
)
def test_sell_base_equality_and_one_unit_under(owned: str, accepted: bool) -> None:
    if accepted:
        assert sell(owned_available_base=Decimal(owned)).remaining_owned_base == Decimal(0)
    else:
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_MARGIN_LIMIT$"):
            sell(owned_available_base=Decimal(owned))


@pytest.mark.parametrize(
    ("name", "cap", "code"),
    [
        ("max_order_quote_debit", "202.99999999999999999999999999", "EXECUTION_QUOTE_LIMIT"),
        ("max_total_quote_commitment", "222.99999999999999999999999999", "ACCOUNT_ADMISSION_LIMIT"),
    ],
)
def test_buy_fractional_unit_under_caps_is_rejected(name: str, cap: str, code: str) -> None:
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_" + code + "$"):
        buy(**{name: Decimal(cap)})


def test_explicit_zero_fees_and_holds_are_valid_without_implicit_fee_default() -> None:
    result = buy(fee_quote_upper=Decimal(0), held_quote=Decimal(0))
    assert result.quote_debit == Decimal(200)
    assert result.remaining_quote == Decimal(50)
    base = sell(fee_base_upper=Decimal(0), held_base=Decimal(0))
    assert base.base_debit == Decimal(2)
    assert base.remaining_owned_base == Decimal(1)


@pytest.mark.parametrize("call", [buy, sell], ids=["cash", "owned"])
def test_zero_available_and_excess_existing_hold_cannot_fund_new_order(call: object) -> None:
    if call is buy:
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_MARGIN_LIMIT$"):
            buy(available_quote=Decimal(0))
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_MARGIN_LIMIT$"):
            buy(held_quote=Decimal(251), max_total_quote_commitment=Decimal(1000))
    else:
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_MARGIN_LIMIT$"):
            sell(owned_available_base=Decimal(0))
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_MARGIN_LIMIT$"):
            sell(held_base=Decimal(4))


BUY_FIELDS = (
    "quantity",
    "buy_ceiling",
    "fee_quote_upper",
    "available_quote",
    "held_quote",
    "max_order_quote_debit",
    "max_total_quote_commitment",
)
SELL_FIELDS = ("quantity", "owned_available_base", "held_base", "fee_base_upper")


class DecimalSubclass(Decimal):
    def is_finite(self) -> bool:
        raise AssertionError("untrusted Decimal hook")


class Hostile:
    def __eq__(self, other: object) -> bool:
        raise AssertionError("untrusted equality hook")

    def __repr__(self) -> str:
        raise AssertionError("untrusted repr hook")


MALFORMED = (
    None,
    True,
    2,
    2.0,
    "2",
    Decimal("NaN"),
    Decimal("sNaN"),
    Decimal("Infinity"),
    Decimal("-Infinity"),
    DecimalSubclass("2"),
    Hostile(),
)
MALFORMED_IDS = (
    "none",
    "bool",
    "int",
    "float",
    "str",
    "nan",
    "snan",
    "inf",
    "neg-inf",
    "decimal-subclass",
    "hostile",
)


@pytest.mark.parametrize("name", BUY_FIELDS)
@pytest.mark.parametrize("value", MALFORMED, ids=MALFORMED_IDS)
def test_buy_rejects_nonexact_nonfinite_and_hostile_scalars(name: str, value: object) -> None:
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$") as rejected:
        buy(**{name: cast(Decimal, value)})
    assert rejected.value.code == "RISK_V2_INPUT_INVALID"
    assert str(rejected.value) == "RISK_V2_INPUT_INVALID"


@pytest.mark.parametrize("name", SELL_FIELDS)
@pytest.mark.parametrize("value", MALFORMED, ids=MALFORMED_IDS)
def test_sell_rejects_nonexact_nonfinite_and_hostile_scalars(name: str, value: object) -> None:
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        sell(**{name: cast(Decimal, value)})


@pytest.mark.parametrize("name", BUY_FIELDS)
def test_buy_rejects_negative_scalars_before_cash_or_cap_outcomes(name: str) -> None:
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        buy(**{"available_quote": Decimal(0), name: Decimal("-0.01")})


@pytest.mark.parametrize("name", SELL_FIELDS)
def test_sell_rejects_negative_scalars_before_inventory_outcome(name: str) -> None:
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        sell(**{name: Decimal("-0.01")})


@pytest.mark.parametrize(
    "name", ["quantity", "buy_ceiling", "max_order_quote_debit", "max_total_quote_commitment"]
)
@pytest.mark.parametrize("value", [Decimal(0), Decimal("-0")])
def test_buy_requires_positive_quantity_price_and_caps(name: str, value: Decimal) -> None:
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        buy(**{name: value})


def test_sell_requires_positive_quantity() -> None:
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        sell(quantity=Decimal(0))


def test_all_inputs_validate_before_economic_rejection() -> None:
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        buy(available_quote=Decimal(0), max_total_quote_commitment=cast(Decimal, None))
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        sell(owned_available_base=Decimal(0), fee_base_upper=cast(Decimal, None))


@pytest.mark.parametrize("rounding", [ROUND_UP, ROUND_DOWN, ROUND_HALF_EVEN])
@pytest.mark.parametrize("capitals", [0, 1])
def test_exact_financial_results_and_rejections_ignore_and_preserve_context(
    rounding: str, capitals: int
) -> None:
    with localcontext() as ambient:
        ambient.prec = 1
        ambient.rounding = rounding
        ambient.Emin = -1
        ambient.Emax = 1
        ambient.capitals = capitals
        ambient.traps[Inexact] = True
        ambient.traps[Rounded] = True
        ambient.flags[Inexact] = True
        before = ambient.copy()
        cash = buy()
        base = sell()
        assert (cash.quote_debit, cash.remaining_quote) == (Decimal(203), Decimal(27))
        assert (base.base_debit, base.remaining_owned_base) == (Decimal("2.01"), Decimal("0.49"))
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_EXECUTION_QUOTE_LIMIT$"):
            buy(max_order_quote_debit=Decimal("202.99999999999999999999999999"))
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_MARGIN_LIMIT$"):
            sell(owned_available_base=Decimal("2.509999999999999999999"))
        assert (
            ambient.prec,
            ambient.rounding,
            ambient.Emin,
            ambient.Emax,
            ambient.capitals,
            ambient.clamp,
            ambient.flags,
            ambient.traps,
        ) == (
            before.prec,
            before.rounding,
            before.Emin,
            before.Emax,
            before.capitals,
            before.clamp,
            before.flags,
            before.traps,
        )


@pytest.mark.parametrize(
    "name", ["buy_ceiling", "max_order_quote_debit", "max_total_quote_commitment"]
)
@pytest.mark.parametrize(
    "value", [Decimal("1" * 257), Decimal("1E+1001"), Decimal("1E-1001"), Decimal("99E+1000")]
)
def test_source_price_and_caps_keep_narrow_representation_budget(name: str, value: Decimal) -> None:
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_RESOURCE_LIMIT$"):
        buy(**{name: value})


def test_source_price_and_caps_accept_exact_256_digit_and_exponent_boundaries() -> None:
    result = buy(
        quantity=Decimal("1E-255"),
        buy_ceiling=Decimal("1" * 256),
        fee_quote_upper=Decimal(0),
        held_quote=Decimal(0),
        max_order_quote_debit=Decimal("1" * 256),
        max_total_quote_commitment=Decimal("1E+1000"),
    )
    assert result.quote_debit == Decimal("1." + "1" * 255)
    tiny = buy(
        quantity=Decimal(1),
        buy_ceiling=Decimal("1E-1000"),
        fee_quote_upper=Decimal(0),
        held_quote=Decimal(0),
        max_order_quote_debit=Decimal("1E-1000"),
        max_total_quote_commitment=Decimal("1E-1000"),
        available_quote=Decimal("1E-1000"),
    )
    assert tiny.quote_debit == Decimal("1E-1000")
    assert tiny.remaining_quote == Decimal(0)


def test_projected_quantities_fees_and_aggregate_balances_are_broad_derived_values() -> None:
    quantity = Decimal("1." + "0" * 256 + "1")
    fee = Decimal("0." + "0" * 256 + "1")
    base = sell(quantity=quantity, fee_base_upper=fee)
    assert base.base_debit == Decimal("1." + "0" * 256 + "2")
    cash = buy(quantity=quantity, fee_quote_upper=fee)
    assert cash.quote_debit == Decimal("100." + "0" * 254 + "101")
    large = Decimal("1" + "0" * 5000)
    large_remaining = Decimal("9" * 4997 + "777")
    assert buy(available_quote=large).remaining_quote == large_remaining
    assert sell(owned_available_base=large, held_base=Decimal(0), fee_base_upper=Decimal(0)) == (
        SpotBaseRequirementV2(Decimal(2), Decimal("9" * 4999 + "8"))
    )
    assert sell(
        quantity=large, owned_available_base=large, held_base=Decimal(0), fee_base_upper=Decimal(0)
    ).remaining_owned_base == Decimal(0)


@pytest.mark.parametrize("name", BUY_FIELDS)
def test_buy_closes_derived_resource_overflow(name: str) -> None:
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_RESOURCE_LIMIT$"):
        buy(**{name: Decimal("1E+65537")})


@pytest.mark.parametrize("name", SELL_FIELDS)
def test_sell_closes_derived_resource_overflow(name: str) -> None:
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_RESOURCE_LIMIT$"):
        sell(**{name: Decimal("1E-65537")})


def test_result_resource_boundary_closes_exact_product_scale_overflow() -> None:
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_RESOURCE_LIMIT$"):
        buy(quantity=Decimal("1E-65536"), buy_ceiling=Decimal("0.1"), fee_quote_upper=Decimal(0))


@pytest.mark.parametrize("kind", [SpotCashRequirementV2, SpotBaseRequirementV2])
def test_result_containers_require_fields_are_frozen_and_reject_malformed_values(
    kind: type[SpotCashRequirementV2] | type[SpotBaseRequirementV2],
) -> None:
    # No defaults can synthesize an unknown fee, resource or authority value.
    with pytest.raises(TypeError):
        kind()  # type: ignore[call-arg]
    result = kind(Decimal(1), Decimal(0))
    with pytest.raises(FrozenInstanceError):
        setattr(result, fields(kind)[0].name, Decimal(2))
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        kind(Decimal(0), Decimal(0))
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        kind(Decimal(1), Decimal(-1))
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        kind(Decimal(1), cast(Decimal, Hostile()))
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_RESOURCE_LIMIT$"):
        kind(Decimal("1E+65537"), Decimal(0))


def test_result_subclasses_and_forged_output_objects_do_not_become_scalar_authority() -> None:
    class CashSubclass(SpotCashRequirementV2):
        pass

    class BaseSubclass(SpotBaseRequirementV2):
        pass

    for kind in (CashSubclass, BaseSubclass):
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
            kind(Decimal(1), Decimal(0))
    forged = object.__new__(SpotCashRequirementV2)
    object.__setattr__(forged, "quote_debit", Hostile())
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        buy(available_quote=cast(Decimal, forged))
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        sell(owned_available_base=cast(Decimal, object.__new__(SpotBaseRequirementV2)))


def test_results_remain_calculations_with_explicit_units_and_no_qualification_fields() -> None:
    # Currency/ownership/debt/source/protection verification has no representation
    # in these two scalar results and cannot be inferred from their success.
    cash = buy()
    base = sell()
    assert tuple(field.name for field in fields(cash)) == ("quote_debit", "remaining_quote")
    assert tuple(field.name for field in fields(base)) == ("base_debit", "remaining_owned_base")
    with pytest.raises(TypeError):
        check_spot_owned_sell_v2(
            quantity=Decimal(2),
            owned_available_base=Decimal(3),
            held_base=Decimal(0),
            fee_base_upper=Decimal(0),
            sell_floor=Decimal(100),  # type: ignore[call-arg]
        )
