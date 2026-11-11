"""Synthetic fixed-limit sizing behavior, never operational admission."""

import json
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal as D
from decimal import Inexact, Rounded, getcontext, localcontext
from fractions import Fraction
from typing import Any

import pytest
from risk_v2_support import NOW, costs_v2, instrument_v2, policy_v2, proposal_v2

from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.linear_risk_v2 import size_linear_entry_v2
from multi_exchange_trader.market_data.instrument_definition import (
    InstrumentObservation,
    KnownNotPublished,
    parse_bybit_instrument_response,
)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_literal_loss_cost_funding(side: str) -> None:
    proposal = proposal_v2()
    costs = costs_v2()
    if side == "SHORT":
        proposal = replace(
            proposal, side="SHORT", stop_price=D(110), entry=replace(proposal.entry, side="SELL")
        )
        costs = replace(costs, side="SHORT", stop_price=D(110))
    result = size_linear_entry_v2(
        policy=policy_v2(),
        proposal=proposal,
        instrument=instrument_v2(),
        catalog_revision="FIXTURE_CATALOG_V1",
        costs=costs,
        now=NOW,
    )
    assert result.envelope.base_quantity == D(1)
    assert result.estimated_loss_quote == D(12)
    assert result.cost_allowance_quote == D(2)
    assert result.funding_allowance_quote == D("0.5")


def test_literal_smaller_loss_budget() -> None:
    policy = replace(policy_v2(), per_trade_loss=D("11.9"))
    costs = replace(costs_v2(), policy_sha256=c.risk_policy_sha256_v2(policy))
    result = size_linear_entry_v2(
        policy=policy,
        proposal=proposal_v2(),
        instrument=instrument_v2(),
        catalog_revision="FIXTURE_CATALOG_V1",
        costs=costs,
        now=NOW,
    )
    assert result.envelope.base_quantity == D("0.9")
    assert result.estimated_loss_quote == D("10.8")


def test_limit_cap_is_distinct_from_market_cap() -> None:
    result = size_linear_entry_v2(
        policy=policy_v2(),
        proposal=proposal_v2(),
        instrument=instrument_v2(),
        catalog_revision="FIXTURE_CATALOG_V1",
        costs=costs_v2(),
        now=NOW,
    )
    assert result.envelope.base_quantity == D(1)
    assert instrument_v2().spec.max_market_quantity.value == D("0.9")


def test_stop_gap_budget_is_required_and_cost_must_match_the_policy():
    _reject("SLIPPAGE_BOUND_UNAVAILABLE", policy=replace(policy_v2(), max_stop_gap_quote_per_base=None))
    _reject("SOURCE_MISMATCH", costs=replace(costs_v2(), stop_gap_quote_per_base=D("0.49")))


def _inputs() -> dict[str, Any]:
    return {
        "policy": policy_v2(),
        "proposal": proposal_v2(),
        "instrument": instrument_v2(),
        "catalog_revision": "FIXTURE_CATALOG_V1",
        "costs": costs_v2(),
        "now": NOW,
    }


def _size(**changes: Any) -> c.LinearSizedEntryV2:
    inputs = _inputs()
    inputs.update(changes)
    return size_linear_entry_v2(**inputs)


def _reject(code: str, **changes: Any) -> None:
    with pytest.raises(c.RiskV2Rejected) as caught:
        _size(**changes)
    assert caught.value.code == "RISK_V2_" + code
    assert str(caught.value) == caught.value.code


def _forged(value: Any, *, missing: object = None, **changes: Any) -> Any:
    result = object.__new__(type(value))
    for field in fields(type(value)):
        if field.name != missing:
            object.__setattr__(
                result, field.name, changes.get(field.name, getattr(value, field.name))
            )
    return result


def _budget(value: D) -> dict[str, Any]:
    policy = replace(policy_v2(), per_trade_loss=value)
    return {
        "policy": policy,
        "costs": replace(costs_v2(), policy_sha256=c.risk_policy_sha256_v2(policy)),
    }


def _prices(limit: str, stop: str, *, short: bool = False) -> dict[str, Any]:
    proposal = replace(
        proposal_v2(),
        side="SHORT" if short else "LONG",
        stop_price=D(stop),
        entry=replace(proposal_v2().entry, side="SELL" if short else "BUY", limit_price=D(limit)),
    )
    costs = replace(costs_v2(), side=proposal.side, entry_limit_price=D(limit), stop_price=D(stop))
    return {"proposal": proposal, "costs": costs}


def _variant(
    *, price: dict[str, str] | None = None, lot: dict[str, str] | None = None
) -> InstrumentObservation:
    original = instrument_v2()
    row = json.loads(original.raw_row_canonical)
    row["priceFilter"].update(price or {})
    row["lotSizeFilter"].update(lot or {})
    return parse_bybit_instrument_response(
        {"retCode": 0, "result": {"category": "linear", "list": [row]}, "time": 1793663999000},
        expected=original.key,
        source=original.source,
    )


@pytest.mark.parametrize("delta,expected", [(-1, "0.9"), (0, "1"), (1, "1")])
def test_exact_step_boundary_without_ambient_rounding(delta: int, expected: str) -> None:
    # Full literal budget keeps its 1E-200 difference before ambient contexts.
    value = D("11." + "9" * 200) if delta < 0 else (D("12." + "0" * 199 + "1") if delta else D(12))
    result = _size(**_budget(value))
    assert result.envelope.base_quantity == D(expected)
    q = Fraction(result.envelope.base_quantity)
    assert q * 12 <= Fraction(value) < (q + Fraction(1, 10)) * 12


@pytest.mark.parametrize("budget,code", [("0.1", "MINIMUM_SIZE"), ("132", "MAXIMUM_SIZE")])
def test_no_upsize_or_clamp(budget: str, code: str) -> None:
    _reject(code, **_budget(D(budget)))


def test_limit_max_boundary_and_local_minimum_variant() -> None:
    assert _size(**_budget(D(120))).envelope.base_quantity == D(10)
    instrument = _variant(lot={"minOrderQty": "0.5"})
    changes = _budget(D("4.8"))
    changes.update(
        instrument=instrument,
        costs=replace(changes["costs"], instrument_revision=instrument.specification_revision),
    )
    _reject("MINIMUM_SIZE", **changes)


@pytest.mark.parametrize(
    "limit,stop", [("100.01", "90"), ("100", "90.01"), ("1000.1", "990"), ("0.01", "0.001")]
)
def test_price_tick_and_entry_bounds(limit: str, stop: str) -> None:
    _reject("INVALID_PRICE_TICK", **_prices(limit, stop))


def test_aligned_entry_below_minimum() -> None:
    instrument = _variant(price={"minPrice": "1"})
    changes = _prices("0.5", "0.1")
    changes.update(
        instrument=instrument,
        costs=replace(changes["costs"], instrument_revision=instrument.specification_revision),
    )
    _reject("INVALID_PRICE_TICK", **changes)


@pytest.mark.parametrize(
    "short,stop", [(False, "100"), (False, "101"), (True, "100"), (True, "99")]
)
def test_adverse_stop_inversion(short: bool, stop: str) -> None:
    proposal = _forged(
        proposal_v2(),
        side="SHORT" if short else "LONG",
        stop_price=D(stop),
        entry=replace(proposal_v2().entry, side="SELL" if short else "BUY"),
    )
    _reject("INVALID_STOP", proposal=proposal)


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("policy_sha256", "b" * 64, "REVISION_MISMATCH"),
        ("instrument_revision", "b" * 64, "REVISION_MISMATCH"),
        ("origin_quote_id", "FIXTURE_OTHER_QUOTE", "SOURCE_MISMATCH"),
        ("entry_limit_price", D(101), "SOURCE_MISMATCH"),
        ("stop_price", D(89), "SOURCE_MISMATCH"),
    ],
)
def test_cost_binding_before_quantity(field: str, value: object, code: str) -> None:
    changes = _budget(D("0.1"))
    changes["costs"] = replace(changes["costs"], **{field: value})
    _reject(code, **changes)


def test_cost_side_and_scope_mismatch() -> None:
    _reject("SOURCE_MISMATCH", costs=replace(costs_v2(), side="SHORT", stop_price=D(110)))
    other = replace(
        policy_v2().scope, strategy=replace(policy_v2().scope.strategy, strategy_id="FIXTURE_OTHER")
    )
    _reject("SCOPE_MISMATCH", proposal=replace(proposal_v2(), scope=other))
    _reject("SCOPE_MISMATCH", costs=replace(costs_v2(), scope=other))


def test_cost_revision_and_private_environment_revalidated() -> None:
    _reject("REVISION_MISMATCH", costs=_forged(costs_v2(), cost_rule_revision="FIXTURE_CHANGED"))
    _reject(
        "SOURCE_MISMATCH",
        costs=_forged(
            costs_v2(), fee_reference=replace(costs_v2().fee_reference, source_environment="LIVE")
        ),
    )


@pytest.mark.parametrize("field", ["next_funding_at", "holding_until", "valid_until"])
def test_cost_schedules_must_still_be_future(field: str) -> None:
    changes: dict[str, Any] = {field: NOW + timedelta(microseconds=1)}
    costs = replace(costs_v2(), **changes)
    _reject("EXPIRED", costs=costs, now=NOW + timedelta(microseconds=1))


def test_preserves_original_fixed_price_id_and_expiry() -> None:
    proposal = proposal_v2()
    entry = replace(
        proposal.entry,
        limit_price=D("100.00"),
        origin_quote_id="FIXTURE_ORIGIN_NO_CLOCK",
        valid_until=NOW + timedelta(seconds=4),
    )
    proposal = replace(proposal, entry=entry)
    costs = replace(
        costs_v2(), entry_limit_price=entry.limit_price, origin_quote_id=entry.origin_quote_id
    )
    result = _size(proposal=proposal, costs=costs)
    assert result.envelope.limit_price.as_tuple() == entry.limit_price.as_tuple()
    assert result.envelope.origin_quote_id == entry.origin_quote_id
    assert result.envelope.valid_until == entry.valid_until
    assert result.envelope.valid_until > costs.valid_until
    _reject("EXPIRED", proposal=proposal, costs=costs, now=costs.valid_until)


def test_expiry_is_strict_and_audit_only_time_keeps_economic_digest() -> None:
    first = _size()
    second = _size(now=NOW + timedelta(microseconds=1))
    assert c.sizing_sha256_v2(first) == c.sizing_sha256_v2(second)
    assert first.assessed_at != second.assessed_at
    _reject("EXPIRED", now=proposal_v2().entry.valid_until)
    assert _size(now=proposal_v2().entry.valid_until - timedelta(microseconds=1))


def _all_times_at(when: datetime) -> dict[str, Any]:
    instrument = instrument_v2()
    instrument = replace(
        instrument, response_time=when, source=replace(instrument.source, received_at=when)
    )
    costs = costs_v2()
    costs = replace(
        costs,
        reference=replace(costs.reference, computed_at=when),
        fee_reference=replace(costs.fee_reference, server_observed_at=when, received_at=when),
        funding_reference=replace(
            costs.funding_reference, server_observed_at=when, received_at=when
        ),
        next_funding_at=when + timedelta(hours=1),
        holding_until=when + timedelta(hours=2),
        valid_until=when + timedelta(seconds=1),
    )
    return {"instrument": instrument, "costs": costs}


def test_age_inclusive_microsecond_and_zero_boundary() -> None:
    policy = replace(policy_v2(), max_evidence_age_seconds=D("0.000001"))
    changes = _all_times_at(NOW - timedelta(microseconds=1))
    changes.update(
        policy=policy,
        costs=replace(changes["costs"], policy_sha256=c.risk_policy_sha256_v2(policy)),
    )
    assert _size(**changes)
    _reject("STALE_EVIDENCE", now=NOW + timedelta(microseconds=1), **changes)
    policy = replace(policy, max_evidence_age_seconds=D(0))
    changes = _all_times_at(NOW)
    changes.update(
        policy=policy,
        costs=replace(changes["costs"], policy_sha256=c.risk_policy_sha256_v2(policy)),
    )
    assert _size(**changes)
    _reject("STALE_EVIDENCE", now=NOW + timedelta(microseconds=1), **changes)


@pytest.mark.parametrize(
    "field",
    [
        "instrument_response",
        "instrument_receipt",
        "fee_server",
        "fee_receipt",
        "funding_server",
        "funding_receipt",
        "completion",
    ],
)
def test_each_available_time_stale(field: str) -> None:
    changes = _all_times_at(NOW)
    old = NOW - timedelta(seconds=10, microseconds=1)
    instrument, costs = changes["instrument"], changes["costs"]
    if field.startswith("instrument"):
        instrument = replace(instrument, response_time=old)
        if field.endswith("receipt"):
            instrument = replace(instrument, source=replace(instrument.source, received_at=old))
    elif field == "completion":
        changes = _all_times_at(old)
        instrument, costs = changes["instrument"], changes["costs"]
    else:
        name, kind = field.split("_")
        reference = getattr(costs, name + "_reference")
        reference = replace(reference, server_observed_at=old)
        if kind == "receipt":
            reference = replace(reference, received_at=old)
        costs = replace(costs, **{name + "_reference": reference})
    _reject("STALE_EVIDENCE", instrument=instrument, costs=costs)


@pytest.mark.parametrize("field", ["instrument", "fee", "funding", "completion"])
def test_available_future_evidence(field: str) -> None:
    future = NOW + timedelta(microseconds=1)
    changes = _all_times_at(NOW)
    costs = replace(
        changes["costs"], reference=replace(changes["costs"].reference, computed_at=future)
    )
    if field == "instrument":
        changes["instrument"] = replace(
            changes["instrument"],
            response_time=future,
            source=replace(changes["instrument"].source, received_at=future),
        )
    elif field != "completion":
        costs = replace(
            costs,
            **{
                field + "_reference": replace(
                    getattr(costs, field + "_reference"),
                    server_observed_at=future,
                    received_at=future,
                )
            },
        )
    changes["costs"] = costs
    _reject("FUTURE_EVIDENCE", **changes)


def test_instrument_receipt_to_completion_dag_and_equality() -> None:
    costs = costs_v2()
    earlier = NOW - timedelta(microseconds=1)
    costs = replace(
        costs,
        fee_reference=replace(costs.fee_reference, received_at=earlier),
        funding_reference=replace(costs.funding_reference, received_at=earlier),
        reference=replace(costs.reference, computed_at=earlier),
    )
    _reject("CAUSAL_ORDER_INVALID", costs=costs)
    assert _size()


def test_fee_receipt_100009_cannot_finish_100008() -> None:
    now = datetime(2026, 11, 3, 10, 0, 10, tzinfo=UTC)
    changes = _all_times_at(now - timedelta(seconds=1))
    proposal = replace(
        proposal_v2(), entry=replace(proposal_v2().entry, valid_until=now + timedelta(seconds=1))
    )
    costs = changes["costs"]
    # _all_times_at needs future expiry/schedules; use a forged bundle whose
    # full static revalidation must catch the impossible completion.
    costs = _forged(
        costs,
        reference=replace(costs.reference, computed_at=now - timedelta(seconds=2)),
        valid_until=now + timedelta(seconds=1),
    )
    _reject(
        "CAUSAL_ORDER_INVALID",
        proposal=proposal,
        instrument=changes["instrument"],
        costs=costs,
        now=now,
    )


@pytest.mark.parametrize("funding_rate", ["-0.001", "0", "0.001"])
def test_signed_rate_is_not_an_implicit_allowance(funding_rate: str) -> None:
    result = _size(
        costs=replace(costs_v2(), funding_rate=D(funding_rate), funding_quote_per_base=D(0))
    )
    assert result.funding_allowance_quote == D(0)
    assert result.unit_loss_quote == D("11.5")
    _reject("INPUT_INVALID", costs=_forged(costs_v2(), funding_quote_per_base=D("-0.1")))


def test_short_floor_and_absence_do_not_create_authority() -> None:
    policy = replace(
        policy_v2(),
        caps=replace(
            policy_v2().caps,
            turnover_requirement="HARD_EXECUTION_QUOTE_CAP",
            max_execution_quote_amount=D(1),
        ),
    )
    changes = _prices("100", "110", short=True)
    changes.update(
        policy=policy,
        costs=replace(changes["costs"], policy_sha256=c.risk_policy_sha256_v2(policy)),
    )
    result = _size(**changes)
    assert result.envelope.side == "SELL"
    assert result.envelope.limit_price == D(100)
    assert type(instrument_v2().spec.max_quote_amount) is KnownNotPublished
    assert not hasattr(result.envelope, "execution_upper_price")


class _Hostile:
    def __eq__(self, other: object) -> bool:
        raise AssertionError("untrusted equality invoked")

    def __repr__(self) -> str:
        raise AssertionError("untrusted repr invoked")


@pytest.mark.parametrize(
    "name", ["policy", "proposal", "instrument", "catalog_revision", "costs", "now"]
)
def test_wrong_top_level_types_before_hooks(name: str) -> None:
    for invalid in (_Hostile(), object(), [], True):
        _reject("INPUT_INVALID", **{name: invalid})


@pytest.mark.parametrize("name", ["policy", "proposal", "instrument", "costs"])
def test_every_incoming_field_missing_or_forged(name: str) -> None:
    value = _inputs()[name]
    for field in fields(type(value)):
        _reject("INPUT_INVALID", **{name: _forged(value, missing=field.name)})
        code = (
            "UNSUPPORTED_MODE"
            if name == "policy" and field.name in ("margin_mode", "position_mode")
            else "INPUT_INVALID"
        )
        _reject(code, **{name: _forged(value, **{field.name: _Hostile()})})
    subclass = type("UntrustedRecord", (type(value),), {"__eq__": _Hostile.__eq__})
    _reject("INPUT_INVALID", **{name: object.__new__(subclass)})


@pytest.mark.parametrize("name", ["fee_reference", "funding_reference", "reference"])
def test_nested_cost_fields_fully_revalidated(name: str) -> None:
    value = getattr(costs_v2(), name)
    for field in fields(type(value)):
        costs = _forged(costs_v2(), **{name: _forged(value, **{field.name: _Hostile()})})
        _reject("INPUT_INVALID", costs=costs)


def test_available_dto_scope_and_instrument_nested_types_revalidated() -> None:
    _reject(
        "INPUT_INVALID",
        policy=_forged(policy_v2(), scope=_forged(policy_v2().scope, instrument=_Hostile())),
    )
    _reject(
        "INPUT_INVALID",
        proposal=_forged(proposal_v2(), entry=_forged(proposal_v2().entry, limit_price=_Hostile())),
    )
    _reject(
        "INPUT_INVALID",
        instrument=_forged(
            instrument_v2(), spec=_forged(instrument_v2().spec, max_quote_amount=_Hostile())
        ),
    )
    _reject(
        "INPUT_INVALID",
        instrument=_forged(
            instrument_v2(), source=_forged(instrument_v2().source, received_at=_Hostile())
        ),
    )


class _HostileTimezone(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta:
        raise AssertionError("untrusted timezone invoked")

    def dst(self, dt: datetime | None) -> timedelta:
        raise AssertionError("untrusted timezone invoked")

    def tzname(self, dt: datetime | None) -> str:
        raise AssertionError("untrusted timezone invoked")


def test_now_exact_timezone_and_normalization() -> None:
    _reject("INPUT_INVALID", now=datetime(2026, 11, 3))
    _reject("INPUT_INVALID", now=NOW.replace(tzinfo=_HostileTimezone()))
    assert _size(now=NOW.astimezone(timezone(timedelta(hours=9)))).assessed_at == NOW


@pytest.mark.parametrize("rounding", ["ROUND_UP", "ROUND_DOWN", "ROUND_HALF_EVEN"])
def test_hostile_ambient_context_independent(rounding: str) -> None:
    inputs = _budget(D("11." + "9" * 200))
    expected = c.sizing_sha256_v2(_size(**inputs))
    with localcontext() as context:
        context.prec = 1
        context.rounding = rounding
        context.Emax = 2
        context.Emin = -2
        context.capitals = 0
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        context.flags[Inexact] = True
        before = context.copy()
        result = _size(**inputs)
        assert result.envelope.base_quantity == D("0.9")
        assert c.sizing_sha256_v2(result) == expected
        assert getcontext() == context
        assert context.prec == before.prec and context.rounding == before.rounding
        assert context.flags == before.flags and context.traps == before.traps


def test_generated_derived_2001_digit_result_revalidates() -> None:
    policy = replace(policy_v2(), per_trade_loss=D("1E1000"))
    costs = replace(
        costs_v2(),
        policy_sha256=c.risk_policy_sha256_v2(policy),
        fee_quote_per_base=D("1E1000"),
        funding_quote_per_base=D("1E-1000"),
        stop_gap_quote_per_base=D("0.5"),
    )
    result = _size(policy=policy, costs=costs)
    assert result.envelope.base_quantity == D("0.9")
    assert len(result.unit_loss_quote.as_tuple().digits) == 2001
    assert len(c.sizing_sha256_v2(result)) == 64


def test_no_quote_time_or_holding_model_authority_from_ids() -> None:
    # Only actual-registry consumers can compare these valid alternate claims
    # with the real cost issuer; sizing preserves their new economic hash.
    costs = replace(costs_v2(), holding_until=NOW + timedelta(hours=3))
    assert _size(costs=costs).cost_allowance_sha256 != _size().cost_allowance_sha256
    policy = replace(policy_v2(), max_quote_age_seconds=D(0))
    costs = replace(costs, policy_sha256=c.risk_policy_sha256_v2(policy))
    assert _size(policy=policy, costs=costs)


def test_spot_and_mismatched_instrument_scope_rejected() -> None:
    _reject("UNSUPPORTED_PRODUCT", instrument=instrument_v2("SPOT"))
    other = replace(
        policy_v2().scope, instrument=replace(policy_v2().scope.instrument, symbol="XBBUSDT")
    )
    policy = replace(policy_v2(), scope=other)
    _reject("SCOPE_MISMATCH", policy=policy)


def test_sizing_does_not_apply_later_minimum_cap_margin_gates() -> None:
    instrument = _variant(lot={"minNotionalValue": "500"})
    policy = replace(
        policy_v2(),
        caps=replace(
            policy_v2().caps, max_order_admission_notional=D(1), max_account_admission_notional=D(1)
        ),
        margin_allocation=D("0.1"),
    )
    costs = replace(
        costs_v2(),
        instrument_revision=instrument.specification_revision,
        policy_sha256=c.risk_policy_sha256_v2(policy),
    )
    result = _size(policy=policy, instrument=instrument, costs=costs)
    assert result.envelope.base_quantity == D(1)
