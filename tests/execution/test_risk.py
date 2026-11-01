"""Risk boundaries use literal, hand-calculated quantities and no venue calls."""

from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal, Inexact, localcontext
from typing import Any

import pytest

from multi_exchange_trader.execution.risk import (
    AccountEvidence,
    EntryProposal,
    InstrumentEvidence,
    LeverageReadback,
    LiquidationEvidence,
    MarketEvidence,
    ReservationTotals,
    RiskRejected,
    RiskScope,
    RiskTier,
    TierEvidence,
    UserRiskPolicy,
    assess_entry,
)

D = Decimal
NOW = datetime(2026, 11, 2, 1, tzinfo=UTC)
SCOPE = RiskScope("account-1", "DEMO", "BTCUSDT", "strategy-1")


def inputs() -> dict[str, Any]:
    return {
        "policy": UserRiskPolicy(
            scope=SCOPE, revision="policy-1", sizing_rule="LOSS_FIXED",
            leverage_min=D("1"), leverage_max=D("150"), per_trade_loss=D("12"),
            max_daily_loss=D("100"), max_drawdown=D("200"), max_consecutive_losses=3,
            max_positions=4, max_total_notional=D("10000"), max_total_risk=D("100"),
            max_total_margin=D("1000"), margin_allocation=D("60"),
            margin_mode="ISOLATED", position_mode="ONE_WAY",
            liquidation_buffer_fraction=D("0.01"), max_quote_age_seconds=D("2"),
            max_evidence_age_seconds=D("10"),
        ),
        "proposal": EntryProposal(side="LONG", stop_price=D("90")),
        "market": MarketEvidence(
            scope=SCOPE, quote_id="quote-1", observed_at=NOW, qualified=True,
            entry_price=D("100"), mark_price=D("100"), fee_per_unit=D("1"),
            funding_per_unit=D("0.5"), slippage_per_unit=D("0.5"),
            funding_rate=D("0.0001"), next_funding_at=NOW + timedelta(hours=1),
            costs_valid_until=NOW + timedelta(minutes=1),
        ),
        "instrument": InstrumentEvidence(
            scope=SCOPE, revision="instrument-1", observed_at=NOW, qualified=True,
            quantity_step=D("0.1"), price_tick=D("0.1"), min_quantity=D("0.1"),
            max_quantity=D("100"), min_notional=D("5"), max_notional=D("10000"),
            leverage_step=D("0.1"),
        ),
        "account": AccountEvidence(
            scope=SCOPE, snapshot_id="account-state-1", observed_at=NOW, complete=True,
            ledger_qualified=True, loss_accounting="UTC_REALIZED_NET_FEES_FUNDING",
            loss_day=NOW.date(), equity=D("1000"), available_margin=D("1000"),
            total_notional=D("0"), symbol_notional=D("0"), total_risk=D("0"),
            total_margin=D("0"), position_count=0, daily_loss=D("0"), drawdown=D("0"),
            consecutive_losses=0, margin_mode="ISOLATED", position_mode="ONE_WAY",
            external_same_symbol_orders=False, external_same_symbol_position=False,
        ),
        "tiers": TierEvidence(
            scope=SCOPE, revision="tiers-1", observed_at=NOW, complete=True, qualified=True,
            tiers=(RiskTier("tier-1", D("0"), D("10000"), D("1"), D("150"), D("0.005")),),
        ),
        "reservations": ReservationTotals(
            scope=SCOPE, snapshot_id="reservations-1", observed_at=NOW,
            complete=True, notional_amount=D("0"),
            symbol_notional_amount=D("0"), risk_amount=D("0"), margin_amount=D("0"),
            position_count=0,
        ),
        "now": NOW,
        "stage": "PLAN",
        "liquidation": None,
        "leverage_readback": None,
    }


def qualified_inputs() -> dict[str, Any]:
    data = inputs()
    data["stage"] = "SUBMIT"
    data["liquidation"] = LiquidationEvidence(
        scope=SCOPE, observed_at=NOW, qualified=True, source="qualified-venue-calculator-v1",
        account_snapshot_id="account-state-1", reservation_snapshot_id="reservations-1",
        quote_id="quote-1", policy_revision="policy-1",
        instrument_revision="instrument-1", tier_revision="tiers-1", tier_id="tier-1",
        quantity=D("1"), leverage=D("1.8"), side="LONG", entry_price=D("100"),
        margin_mode="ISOLATED", position_mode="ONE_WAY", liquidation_price=D("80"),
    )
    data["leverage_readback"] = LeverageReadback(
        scope=SCOPE, observed_at=NOW, qualified=True, account_snapshot_id="account-state-1",
        policy_revision="policy-1", applied_leverage=D("1.8"), side="LONG",
        margin_mode="ISOLATED", position_mode="ONE_WAY",
    )
    return data


def test_loss_fixed_size_includes_all_costs_and_planning_cannot_authorize_submission() -> None:
    result = assess_entry(**inputs())
    assert result.quantity == D("1")  # 12 / (10 stop + 1 fee + .5 funding + .5 slip)
    assert result.estimated_loss == D("12")
    assert result.cost_allowance == D("2")
    assert result.notional == D("100")
    assert result.leverage == D("1.8")  # ceil(.1 step, 100 / (60 - 2))
    assert result.estimated_loss_fraction == D("0.012")
    assert result.submission_ready is False
    assert set(result.blockers) == {"UNVERIFIED_LIQUIDATION", "UNVERIFIED_LEVERAGE"}


def test_submission_requires_matching_qualified_evidence_and_reports_distances() -> None:
    result = assess_entry(**qualified_inputs())
    assert result.submission_ready is True
    assert result.blockers == ()
    assert result.liquidation_price == D("80")
    assert result.liquidation_distance_fraction == D("0.2")
    assert result.stop_distance_fraction == D("0.1")
    assert result.liquidation_buffer_fraction == D("0.1")


@pytest.mark.parametrize("missing", ["policy", "proposal", "market", "instrument", "account",
                                     "tiers", "reservations", "now", "stage"])
def test_missing_top_level_input_never_uses_defaults(missing: str) -> None:
    data = inputs()
    data[missing] = None
    with pytest.raises(RiskRejected, match="INVALID_INPUT"):
        assess_entry(**data)


@pytest.mark.parametrize("field", [f.name for f in fields(UserRiskPolicy)])
def test_every_missing_policy_value_blocks(field: str) -> None:
    data = inputs()
    data["policy"] = replace(data["policy"], **{field: None})
    with pytest.raises(RiskRejected, match="INVALID_INPUT"):
        assess_entry(**data)


@pytest.mark.parametrize("kind", ["market", "instrument", "account", "tiers", "reservations"])
@pytest.mark.parametrize("age", [timedelta(seconds=11), timedelta(seconds=-1)])
def test_stale_or_future_scoped_evidence_blocks(kind: str, age: timedelta) -> None:
    data = inputs()
    data[kind] = replace(data[kind], observed_at=NOW - age)
    with pytest.raises(RiskRejected, match="STALE_EVIDENCE|FUTURE_EVIDENCE"):
        assess_entry(**data)


def test_quote_age_uses_explicit_quote_limit() -> None:
    data = inputs()
    data["market"] = replace(data["market"], observed_at=NOW - timedelta(seconds=3))
    with pytest.raises(RiskRejected, match="STALE_EVIDENCE"):
        assess_entry(**data)


@pytest.mark.parametrize("value", [D("NaN"), D("sNaN"), D("Infinity"), D("-Infinity"),
                                   1, 1.0, True, "1", None])
def test_nonfinite_or_untyped_numeric_input_blocks(value: object) -> None:
    data = inputs()
    data["market"] = replace(data["market"], entry_price=value)
    with pytest.raises(RiskRejected, match="INVALID_INPUT"):
        assess_entry(**data)


@pytest.mark.parametrize("kind", ["policy", "market", "instrument", "account", "tiers",
                                   "reservations"])
def test_wrong_scope_blocks(kind: str) -> None:
    data = inputs()
    data[kind] = replace(data[kind], scope=replace(SCOPE, environment="LIVE"))
    with pytest.raises(RiskRejected, match="SCOPE_MISMATCH"):
        assess_entry(**data)


@pytest.mark.parametrize("field,value", [("margin_mode", "CROSS"),
                                         ("position_mode", "HEDGE"),
                                         ("external_same_symbol_orders", True),
                                         ("external_same_symbol_position", True)])
def test_wrong_mode_and_external_symbol_activity_blocks(field: str, value: object) -> None:
    data = inputs()
    data["account"] = replace(data["account"], **{field: value})
    with pytest.raises(RiskRejected, match="MODE_MISMATCH|EXTERNAL_CONFLICT"):
        assess_entry(**data)


def test_quantity_rounds_down_even_when_minimum_would_require_rounding_up() -> None:
    data = inputs()
    data["policy"] = replace(data["policy"], per_trade_loss=D("11.9"))
    assert assess_entry(**data).quantity == D("0.9")
    data["instrument"] = replace(data["instrument"], min_quantity=D("1"))
    with pytest.raises(RiskRejected, match="MINIMUM_SIZE"):
        assess_entry(**data)


@pytest.mark.parametrize("field,value,code", [("min_notional", D("101"), "MINIMUM_SIZE"),
                                             ("max_quantity", D("0.9"), "MAXIMUM_SIZE"),
                                             ("max_notional", D("99"), "MAXIMUM_SIZE")])
def test_instrument_runtime_size_bounds(field: str, value: Decimal, code: str) -> None:
    data = inputs()
    data["instrument"] = replace(data["instrument"], **{field: value})
    with pytest.raises(RiskRejected, match=code):
        assess_entry(**data)


def test_size_dependent_tier_uses_existing_and_reserved_same_symbol_exposure() -> None:
    data = inputs()
    data["tiers"] = replace(data["tiers"], tiers=(
        RiskTier("small", D("0"), D("100"), D("1"), D("150"), D("0.005")),
        RiskTier("large", D("100"), D("500"), D("1"), D("1.5"), D("0.01")),
    ))
    data["reservations"] = replace(data["reservations"], notional_amount=D("1"),
                                    symbol_notional_amount=D("1"))
    with pytest.raises(RiskRejected, match="LEVERAGE_UNSATISFIABLE"):
        assess_entry(**data)


def test_quantity_cannot_escape_last_verified_tier() -> None:
    data = inputs()
    data["tiers"] = replace(data["tiers"], tiers=(
        RiskTier("small", D("0"), D("99"), D("1"), D("150"), D("0.005")),
    ))
    with pytest.raises(RiskRejected, match="UNVERIFIED_RISK_TIER"):
        assess_entry(**data)


def test_no_arbitrary_program_leverage_cap_and_user_minimum_is_recorded() -> None:
    data = inputs()
    data["policy"] = replace(data["policy"], leverage_min=D("120"))
    result = assess_entry(**data)
    assert result.leverage == D("120")
    assert "USER_MINIMUM" in result.clamp_reasons


def test_user_venue_intersection_and_step_must_cover_required_margin() -> None:
    data = inputs()
    data["policy"] = replace(data["policy"], leverage_max=D("1.75"))
    with pytest.raises(RiskRejected, match="LEVERAGE_UNSATISFIABLE"):
        assess_entry(**data)
    data["policy"] = replace(data["policy"], leverage_min=D("151"), leverage_max=D("200"))
    with pytest.raises(RiskRejected, match="LEVERAGE_UNSATISFIABLE"):
        assess_entry(**data)


@pytest.mark.parametrize("field,value,code", [
    ("max_total_notional", D("99"), "EXPOSURE_LIMIT"),
    ("max_total_risk", D("11"), "TOTAL_RISK_LIMIT"),
    ("max_total_margin", D("2"), "MARGIN_LIMIT"),
    ("max_daily_loss", D("11"), "DAILY_LOSS_LIMIT"),
    ("max_drawdown", D("11"), "DRAWDOWN_LIMIT"),
])
def test_account_limits_include_proposed_risk(field: str, value: Decimal, code: str) -> None:
    data = inputs()
    data["policy"] = replace(data["policy"], **{field: value})
    with pytest.raises(RiskRejected, match=code):
        assess_entry(**data)


@pytest.mark.parametrize("field,value,code", [
    ("notional_amount", D("9999"), "EXPOSURE_LIMIT"),
    ("risk_amount", D("99"), "TOTAL_RISK_LIMIT"),
    ("margin_amount", D("1000"), "MARGIN_LIMIT"),
    ("position_count", 4, "POSITION_LIMIT"),
])
def test_pending_reservations_count_against_limits(field: str, value: object, code: str) -> None:
    data = inputs()
    data["reservations"] = replace(data["reservations"], **{field: value})
    with pytest.raises(RiskRejected, match=code):
        assess_entry(**data)


@pytest.mark.parametrize("field,value,code", [
    ("daily_loss", D("89"), "DAILY_LOSS_LIMIT"),
    ("drawdown", D("189"), "DRAWDOWN_LIMIT"),
    ("position_count", 4, "POSITION_LIMIT"),
    ("consecutive_losses", 3, "CONSECUTIVE_LOSS_LIMIT"),
    ("loss_day", NOW.date() - timedelta(days=1), "UNVERIFIED_LOSS_LEDGER"),
    ("ledger_qualified", False, "UNVERIFIED_LOSS_LEDGER"),
    ("complete", False, "INCOMPLETE_EVIDENCE"),
])
def test_account_loss_ledger_and_counts(field: str, value: object, code: str) -> None:
    data = inputs()
    data["account"] = replace(data["account"], **{field: value})
    with pytest.raises(RiskRejected, match=code):
        assess_entry(**data)


@pytest.mark.parametrize("kind", ["liquidation", "leverage_readback"])
def test_submit_rejects_missing_readback_or_liquidation(kind: str) -> None:
    data = qualified_inputs()
    data[kind] = None
    with pytest.raises(RiskRejected, match="UNVERIFIED_LIQUIDATION|UNVERIFIED_LEVERAGE"):
        assess_entry(**data)


@pytest.mark.parametrize("field,value", [
    ("quantity", D("1.1")), ("leverage", D("2")), ("side", "SHORT"),
    ("entry_price", D("101")), ("margin_mode", "CROSS"), ("position_mode", "HEDGE"),
    ("account_snapshot_id", "other"), ("quote_id", "other"), ("policy_revision", "other"),
    ("reservation_snapshot_id", "other"),
    ("instrument_revision", "other"), ("tier_revision", "other"), ("tier_id", "other"),
    ("qualified", False), ("observed_at", NOW - timedelta(seconds=11)),
])
def test_liquidation_evidence_is_bound_to_exact_proposal(field: str, value: object) -> None:
    data = qualified_inputs()
    data["liquidation"] = replace(data["liquidation"], **{field: value})
    with pytest.raises(RiskRejected):
        assess_entry(**data)


@pytest.mark.parametrize("price", [D("90"), D("89.1"), D("101")])
def test_liquidation_must_follow_stop_by_explicit_user_buffer(price: Decimal) -> None:
    data = qualified_inputs()
    data["liquidation"] = replace(data["liquidation"], liquidation_price=price)
    with pytest.raises(RiskRejected, match="LIQUIDATION_BUFFER"):
        assess_entry(**data)


@pytest.mark.parametrize("field,value", [
    ("applied_leverage", D("1.7")), ("qualified", False), ("side", "SHORT"),
    ("margin_mode", "CROSS"), ("account_snapshot_id", "other"),
    ("observed_at", NOW + timedelta(seconds=1)),
])
def test_readback_is_not_just_a_success_ack(field: str, value: object) -> None:
    data = qualified_inputs()
    data["leverage_readback"] = replace(data["leverage_readback"], **{field: value})
    with pytest.raises(RiskRejected):
        assess_entry(**data)


def test_short_uses_symmetric_loss_size_and_liquidation_direction() -> None:
    data = qualified_inputs()
    data["proposal"] = replace(data["proposal"], side="SHORT", stop_price=D("110"))
    data["liquidation"] = replace(data["liquidation"], side="SHORT", liquidation_price=D("120"))
    data["leverage_readback"] = replace(data["leverage_readback"], side="SHORT")
    result = assess_entry(**data)
    assert result.quantity == D("1")
    assert result.liquidation_buffer_fraction == D("0.1")
    assert result.submission_ready is True


def test_even_complete_plan_evidence_is_never_submission_ready() -> None:
    data = qualified_inputs()
    data["stage"] = "PLAN"
    result = assess_entry(**data)
    assert result.submission_ready is False
    assert result.blockers == ("PLANNING_ONLY",)


@pytest.mark.parametrize("field,value", [
    ("next_funding_at", NOW), ("costs_valid_until", NOW - timedelta(seconds=1)),
    ("qualified", False), ("observed_at", NOW.replace(tzinfo=None)),
])
def test_missing_current_funding_costs_or_aware_time_blocks(field: str, value: object) -> None:
    data = inputs()
    data["market"] = replace(data["market"], **{field: value})
    with pytest.raises(RiskRejected):
        assess_entry(**data)


def test_decimal_context_cannot_change_calculation_or_freshness() -> None:
    data = inputs()
    data["market"] = replace(data["market"], observed_at=NOW - timedelta(microseconds=1))
    with localcontext() as context:
        context.prec = 1
        context.traps[Inexact] = True
        result = assess_entry(**data)
    assert result.quantity == D("1")
    assert result.leverage == D("1.8")


@pytest.mark.parametrize("kind,cls", [
    ("proposal", EntryProposal), ("market", MarketEvidence), ("instrument", InstrumentEvidence),
    ("account", AccountEvidence), ("tiers", TierEvidence), ("reservations", ReservationTotals),
    ("liquidation", LiquidationEvidence), ("leverage_readback", LeverageReadback),
])
def test_every_evidence_field_is_mandatory(kind: str, cls: type[object]) -> None:
    assert is_dataclass(cls)
    for field in fields(cls):
        data = qualified_inputs()
        data[kind] = replace(data[kind], **{field.name: None})
        with pytest.raises(RiskRejected, match="INVALID_INPUT"):
            assess_entry(**data)


@pytest.mark.parametrize("value", [D("NaN"), D("sNaN"), D("Infinity"), True, 1.0])
def test_all_decimal_fields_reject_nonfinite_and_implicit_conversion(value: object) -> None:
    for kind, original in qualified_inputs().items():
        if not hasattr(original, "__dataclass_fields__"):
            continue
        for field in fields(original):
            if type(getattr(original, field.name)) is Decimal:
                data = qualified_inputs()
                data[kind] = replace(original, **{field.name: value})
                with pytest.raises(RiskRejected, match="INVALID_INPUT"):
                    assess_entry(**data)


@pytest.mark.parametrize("field", ["account_id", "environment", "symbol", "strategy_id"])
def test_scope_cannot_have_missing_identity(field: str) -> None:
    data = inputs()
    data["policy"] = replace(data["policy"], scope=replace(SCOPE, **{field: ""}))
    with pytest.raises(RiskRejected, match="INVALID_INPUT"):
        assess_entry(**data)


@pytest.mark.parametrize("environment", ["MAINNET", "TESTNET", "", "demo"])
def test_environment_has_no_implicit_production_interpretation(environment: str) -> None:
    data = inputs()
    data["policy"] = replace(data["policy"], scope=replace(SCOPE, environment=environment))
    with pytest.raises(RiskRejected, match="INVALID_INPUT"):
        assess_entry(**data)


@pytest.mark.parametrize("kind,field", [
    ("account", "position_count"), ("account", "consecutive_losses"),
    ("policy", "max_consecutive_losses"), ("policy", "max_positions"),
    ("reservations", "position_count"), ("market", "qualified"),
    ("account", "external_same_symbol_orders"),
])
def test_bool_and_int_are_not_interchangeable(kind: str, field: str) -> None:
    data = inputs()
    original = getattr(data[kind], field)
    data[kind] = replace(data[kind], **{field: 1 if type(original) is bool else True})
    with pytest.raises(RiskRejected, match="INVALID_INPUT"):
        assess_entry(**data)


def test_reserved_risk_counts_in_daily_and_drawdown_headroom() -> None:
    data = inputs()
    data["reservations"] = replace(data["reservations"], risk_amount=D("20"))
    data["policy"] = replace(data["policy"], max_daily_loss=D("31"))
    with pytest.raises(RiskRejected, match="DAILY_LOSS_LIMIT"):
        assess_entry(**data)
    data["policy"] = replace(data["policy"], max_daily_loss=D("100"), max_drawdown=D("31"))
    with pytest.raises(RiskRejected, match="DRAWDOWN_LIMIT"):
        assess_entry(**data)


def test_current_own_same_symbol_exposure_selects_tier() -> None:
    data = inputs()
    data["account"] = replace(data["account"], total_notional=D("1"), symbol_notional=D("1"))
    data["tiers"] = replace(data["tiers"], tiers=(
        RiskTier("small", D("0"), D("100"), D("1"), D("150"), D("0.005")),
        RiskTier("large", D("100"), D("1000"), D("1"), D("100"), D("0.01")),
    ))
    assert assess_entry(**data).tier_id == "large"


@pytest.mark.parametrize("tier", [
    RiskTier("bad", D("1"), D("100"), D("1"), D("150"), D("0.005")),
    RiskTier("bad", D("0"), D("100"), D("2"), D("1"), D("0.005")),
    RiskTier("bad", D("0"), D("100"), D("1"), D("150"), D("1")),
    RiskTier("bad", D("0"), D("100"), D("1"), D("150"), D("NaN")),
])
def test_invalid_runtime_risk_tier_never_gets_silently_repaired(tier: RiskTier) -> None:
    data = inputs()
    data["tiers"] = replace(data["tiers"], tiers=(tier,))
    with pytest.raises(RiskRejected, match="INVALID_INPUT"):
        assess_entry(**data)


@pytest.mark.parametrize("side,stop", [("LONG", D("100")), ("LONG", D("101")),
                                      ("SHORT", D("100")), ("SHORT", D("99"))])
def test_stop_must_be_strictly_on_loss_side(side: str, stop: Decimal) -> None:
    data = inputs()
    data["proposal"] = EntryProposal(side, stop)
    with pytest.raises(RiskRejected, match="INVALID_STOP"):
        assess_entry(**data)


def test_fractional_lot_and_tick_are_runtime_grids() -> None:
    data = inputs()
    data["instrument"] = replace(data["instrument"], quantity_step=D("0.3"))
    assert assess_entry(**data).quantity == D("0.9")
    data["proposal"] = replace(data["proposal"], stop_price=D("90.01"))
    with pytest.raises(RiskRejected, match="INVALID_PRICE_TICK"):
        assess_entry(**data)


class FallBack(tzinfo):
    """Portable repeated hour without the optional IANA timezone database."""

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return timedelta(hours=-4 if dt is None or dt.fold == 0 else -5)

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(hours=1 if dt is None or dt.fold == 0 else 0)

    def tzname(self, dt: datetime | None) -> str:
        return "fall-back-test"


def fold_inputs(now: datetime) -> dict[str, Any]:
    data = qualified_inputs()
    data["now"] = now
    for kind in ("market", "instrument", "account", "tiers", "reservations",
                 "liquidation", "leverage_readback"):
        data[kind] = replace(data[kind], observed_at=now)
    data["account"] = replace(data["account"], loss_day=now.astimezone(UTC).date())
    data["market"] = replace(data["market"],
                             costs_valid_until=now.astimezone(UTC) + timedelta(hours=1),
                             next_funding_at=now.astimezone(UTC) + timedelta(hours=2))
    return data


@pytest.mark.parametrize("kind", ["market", "instrument", "account", "tiers", "reservations",
                                  "liquidation", "leverage_readback"])
@pytest.mark.parametrize("now_fold,observed_fold,code", [
    (1, 0, "STALE_EVIDENCE"), (0, 1, "FUTURE_EVIDENCE"),
])
def test_fold_freshness_uses_real_utc_age(kind: str, now_fold: int, observed_fold: int,
                                        code: str) -> None:
    zone = FallBack()
    now = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=now_fold)
    observed = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=observed_fold)
    data = fold_inputs(now)
    data[kind] = replace(data[kind], observed_at=observed)
    assert abs((now.astimezone(UTC) - observed.astimezone(UTC)).total_seconds()) == 3600
    with pytest.raises(RiskRejected, match=code):
        assess_entry(**data)


@pytest.mark.parametrize("field", ["costs_valid_until", "next_funding_at"])
def test_fold_cost_deadline_uses_actual_expiration(field: str) -> None:
    zone = FallBack()
    now = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    deadline = datetime(2026, 11, 1, 1, 31, tzinfo=zone, fold=0)
    data = fold_inputs(now)
    data["market"] = replace(data["market"], **{field: deadline})
    assert (now.astimezone(UTC) - deadline.astimezone(UTC)).total_seconds() == 3540
    with pytest.raises(RiskRejected, match="STALE_COSTS"):
        assess_entry(**data)


@pytest.mark.parametrize("value", [
    D("1E+999999999999999999"), D("1E-999999999999999999"),
    D("0E+999999999999999999"), D("0E-999999999999999999"),
    D("1" * 257), D("11E+1000"),
])
@pytest.mark.parametrize("kind,field", [
    ("market", "funding_rate"), ("policy", "max_total_notional"),
    ("instrument", "max_notional"), ("account", "available_margin"),
    ("reservations", "risk_amount"), ("tier", "max_notional"),
    ("liquidation", "liquidation_price"), ("leverage_readback", "applied_leverage"),
])
def test_source_decimal_representation_rejected_before_precision_allocation(
    kind: str, field: str, value: Decimal, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution import risk

    data = qualified_inputs()
    if kind == "tier":
        data["tiers"] = replace(data["tiers"],
                                tiers=(replace(data["tiers"].tiers[0], **{field: value}),))
    else:
        data[kind] = replace(data[kind], **{field: value})

    def forbid_precision(*records: object) -> int:
        pytest.fail("Unbounded source Decimal reached precision allocation")

    monkeypatch.setattr(risk, "_precision", forbid_precision)
    with pytest.raises(RiskRejected, match="INVALID_INPUT"):
        assess_entry(**data)


def test_derived_division_keeps_more_than_256_digits() -> None:
    result = assess_entry(**qualified_inputs())
    assert result.submission_ready
    assert len(result.initial_margin.as_tuple().digits) > 256


@pytest.mark.parametrize("value", [D("1E+1000"), D("1E-1000"), D("0E+1000"),
                                   D("0E-1000"), D("1" * 256)])
def test_source_representation_boundaries_are_not_financial_caps(value: Decimal) -> None:
    data = qualified_inputs()
    data["market"] = replace(data["market"], funding_rate=value)
    assert assess_entry(**data).submission_ready
