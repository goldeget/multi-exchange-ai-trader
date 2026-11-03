"""Behavioral static-boundary tests using explicitly synthetic values."""

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal as D
from decimal import Inexact, Rounded, getcontext, localcontext
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution import risk_v2_math as m
from multi_exchange_trader.market_data.instrument_definition import KnownNotPublished
from tests.execution.risk_v2_support import (
    EXPIRY,
    NOW,
    computation_ref_v2,
    costs_v2,
    envelope_v2,
    instrument_v2,
    observation_ref_v2,
    policy_v2,
    proposal_v2,
    scope_v2,
    sizing_v2,
)


def _forged(value: Any, *, missing: object = None, **changes: object) -> Any:
    result = object.__new__(type(value))
    for field in fields(type(value)):
        if field.name != missing:
            object.__setattr__(
                result, field.name, changes.get(field.name, getattr(value, field.name))
            )
    return result


def _consume(value: Any) -> object:
    kind = type(value)
    if kind is c.RiskScopeV2:
        return c.risk_policy_sha256_v2(_forged(policy_v2(), scope=value))
    if kind is c.InstrumentPinV2:
        return c.envelope_sha256_v2(_forged(envelope_v2(), instrument_pin=value))
    if kind is c.RiskObservationRefV2:
        return c.cost_allowance_sha256_v2(_forged(costs_v2(), fee_reference=value))
    if kind is c.RiskComputationRefV2:
        return c.cost_allowance_sha256_v2(_forged(costs_v2(), reference=value))
    if kind is c.LinearCapitalCapsV2:
        return c.risk_policy_sha256_v2(_forged(policy_v2(), caps=value))
    if kind is c.LinearRiskPolicyV2:
        return c.risk_policy_sha256_v2(value)
    if kind is c.FixedLimitPriceV2:
        return c._proposal_payload_v2(_forged(proposal_v2(), entry=value))
    if kind is c.LinearEntryProposalV2:
        return c._proposal_payload_v2(value)
    if kind is c.LinearCostAllowanceV2:
        return c.cost_allowance_sha256_v2(value)
    if kind is c.FixedLimitEnvelopeV2:
        return c.envelope_sha256_v2(value)
    if kind is c.LinearSizedEntryV2:
        return c.sizing_sha256_v2(value)
    raise AssertionError("test supplied unsupported record")


BUILDERS: tuple[Callable[[], Any], ...] = (
    scope_v2,
    lambda: c.pin_instrument_v2(instrument_v2(), catalog_revision="FIXTURE_CATALOG_V1"),
    observation_ref_v2,
    computation_ref_v2,
    lambda: policy_v2().caps,
    policy_v2,
    lambda: proposal_v2().entry,
    proposal_v2,
    costs_v2,
    envelope_v2,
    sizing_v2,
)


def test_known_absence_is_preserved_but_app_caps_are_mandatory() -> None:
    observation = instrument_v2()
    absence = observation.spec.max_quote_amount
    assert type(absence) is KnownNotPublished
    assert absence.field == "max_quote_amount"
    pin = c.pin_instrument_v2(observation, catalog_revision="FIXTURE_CATALOG_V1")
    assert pin.key == observation.key
    assert observation.spec.max_quote_amount is absence
    for bad in (None, D(0), D("NaN"), D("Infinity"), 1.0, True):
        for name in ("max_order_admission_notional", "max_account_admission_notional"):
            with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
                c.risk_policy_sha256_v2(
                    _forged(policy_v2(), caps=_forged(policy_v2().caps, **{name: bad}))
                )


def test_shared_product_and_pin_are_exact() -> None:
    observation = instrument_v2()
    pin = c.pin_instrument_v2(observation, catalog_revision="FIXTURE_CATALOG_V1")
    assert pin.native_instrument_id == "XAAUSDT-LINEAR.BYBIT"
    assert pin.response_time == NOW - timedelta(seconds=1)
    assert pin.received_at == NOW
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_UNSUPPORTED_PRODUCT$"):
        c.pin_instrument_v2(instrument_v2("SPOT"), catalog_revision="FIXTURE_CATALOG_V1")
    for changes in (
        {"native_instrument_id": "XAAUSDT-SPOT.BYBIT"},
        {"source_rule_revision": "FIXTURE_UNSUPPORTED_RULE"},
        {"raw_row_sha256": "0" * 64},
        {"spec": _forged(observation.spec, max_quote_amount=None)},
    ):
        with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$") as caught:
            c.pin_instrument_v2(
                _forged(observation, **changes), catalog_revision="FIXTURE_CATALOG_V1"
            )
        assert caught.value.__cause__ is None


@pytest.mark.parametrize("builder", BUILDERS)
def test_all_core_fields_are_revalidated(builder: Callable[[], Any]) -> None:
    value = builder()
    _consume(value)
    for field in fields(type(value)):
        for forged in (
            _forged(value, missing=field.name),
            _forged(value, **{field.name: object()}),
            _forged(value, **{field.name: []}),
        ):
            with pytest.raises(c.RiskV2Rejected):
                _consume(forged)
        kwargs = {item.name: getattr(value, item.name) for item in fields(type(value))}
        kwargs[field.name] = object()
        with pytest.raises(c.RiskV2Rejected):
            type(value)(**kwargs)


def test_subclasses_and_hooks_are_rejected_without_invocation() -> None:
    class Hostile:
        def __eq__(self, other: object) -> bool:
            raise AssertionError("equality hook")

        def __repr__(self) -> str:
            raise AssertionError("repr hook")

        def __str__(self) -> str:
            raise AssertionError("string hook")

    def hostile_getattribute(self: object, name: str) -> Any:
        raise AssertionError("attribute hook")

    for builder in BUILDERS:
        value = builder()
        subclass = type("HostileRecord", (type(value),), {"__getattribute__": hostile_getattribute})
        malicious: Any = object.__new__(subclass)
        with pytest.raises(c.RiskV2Rejected):
            type(value).__post_init__(malicious)
        for field in fields(type(value)):
            with pytest.raises(c.RiskV2Rejected):
                _consume(_forged(value, **{field.name: Hostile()}))
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        c.risk_policy_sha256_v2(_forged(policy_v2(), per_trade_loss=type("SubD", (D,), {})("12")))


def test_error_export_is_the_same_closed_class() -> None:
    assert c.RiskV2Rejected is m.RiskV2Rejected
    with pytest.raises(ValueError, match="^INVALID_RISK_V2_REJECTION_CODE$"):
        c.RiskV2Rejected("provider-private-detail")


@pytest.mark.parametrize(
    "name,bad",
    [
        ("leverage_min", D(151)),
        ("leverage_max", D(0)),
        ("per_trade_loss", D(0)),
        ("max_daily_loss", D(-1)),
        ("max_drawdown", D(0)),
        ("max_consecutive_losses", True),
        ("max_positions", 0),
        ("max_total_risk", D(0)),
        ("max_total_margin", D(0)),
        ("margin_allocation", D(0)),
        ("liquidation_buffer_fraction", D(1)),
        ("liquidation_buffer_fraction", D("-0.1")),
        ("max_quote_age_seconds", D(-1)),
        ("max_evidence_age_seconds", D(-1)),
        ("margin_mode", "HEDGE"),
        ("position_mode", "HEDGE"),
        ("sizing_rule", "DEFAULT"),
        ("revision", "private value\n"),
    ],
)
def test_policy_economic_and_literal_constraints(name: str, bad: object) -> None:
    with pytest.raises(c.RiskV2Rejected):
        c.risk_policy_sha256_v2(_forged(policy_v2(), **{name: bad}))


def test_turnover_choice_has_no_implicit_cap() -> None:
    caps = policy_v2().caps
    for changes in (
        {"turnover_requirement": "HARD_EXECUTION_QUOTE_CAP"},
        {"max_execution_quote_amount": D(1)},
        {"turnover_requirement": "HARD_EXECUTION_QUOTE_CAP", "max_execution_quote_amount": D(0)},
    ):
        with pytest.raises(c.RiskV2Rejected):
            c.risk_policy_sha256_v2(_forged(policy_v2(), caps=_forged(caps, **changes)))
    hard = replace(
        caps, turnover_requirement="HARD_EXECUTION_QUOTE_CAP", max_execution_quote_amount=D(5)
    )
    assert len(c.risk_policy_sha256_v2(replace(policy_v2(), caps=hard))) == 64


def test_static_scope_side_stop_binding_and_sizing_products() -> None:
    proposal = proposal_v2()
    for changes in ({"side": "SHORT"}, {"stop_price": D(100)}, {"stop_price": D(101)}):
        with pytest.raises(c.RiskV2Rejected):
            c._proposal_payload_v2(_forged(proposal, **changes))
    sized = sizing_v2()
    for name in (
        "stop_distance_quote_per_base",
        "unit_loss_quote",
        "estimated_loss_quote",
        "cost_allowance_quote",
    ):
        with pytest.raises(c.RiskV2Rejected):
            c.sizing_sha256_v2(_forged(sized, **{name: D(1)}))
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_SCOPE_MISMATCH$"):
        c.envelope_sha256_v2(
            _forged(
                envelope_v2(),
                instrument_pin=replace(
                    envelope_v2().instrument_pin,
                    key=replace(scope_v2().instrument, symbol="YBBUSDT"),
                    native_instrument_id="YBBUSDT-LINEAR.BYBIT",
                ),
            )
        )
    for name, bad in (
        ("schema_version", True),
        ("reduce_only", 0),
        ("order_type", "MARKET"),
        ("time_in_force", "GTC"),
        ("role", "EXIT"),
    ):
        with pytest.raises(c.RiskV2Rejected):
            c.envelope_sha256_v2(_forged(envelope_v2(), **{name: bad}))


def test_computation_and_observation_time_semantics_are_distinct() -> None:
    costs = costs_v2()
    for changes in (
        {"reference": _forged(costs.reference, model_revision="FIXTURE_OTHER")},
        {"reference": _forged(costs.reference, computed_at=NOW - timedelta(microseconds=1))},
        {
            "fee_reference": _forged(
                costs.fee_reference, server_observed_at=NOW + timedelta(seconds=1)
            )
        },
        {"valid_until": NOW},
        {"next_funding_at": NOW},
        {"holding_until": NOW},
    ):
        with pytest.raises(c.RiskV2Rejected):
            c.cost_allowance_sha256_v2(_forged(costs, **changes))
    # Static hashing deliberately has no current clock or invented freshness limit.
    stale = replace(
        costs,
        fee_reference=replace(
            costs.fee_reference,
            server_observed_at=NOW - timedelta(days=1),
            received_at=NOW - timedelta(days=1),
        ),
    )
    assert c.cost_allowance_sha256_v2(stale) != c.cost_allowance_sha256_v2(costs)
    with pytest.raises(c.RiskV2Rejected):
        c.cost_allowance_sha256_v2(_forged(costs, reference=observation_ref_v2()))


def test_only_audit_sizing_time_is_excluded_from_economic_hash() -> None:
    sized = sizing_v2()
    digest = c.sizing_sha256_v2(sized)
    assert c.sizing_sha256_v2(replace(sized, assessed_at=NOW + timedelta(microseconds=1))) == digest
    for changed in (
        replace(
            sized,
            cost_reference=replace(
                sized.cost_reference, computed_at=NOW - timedelta(microseconds=1)
            ),
        ),
        replace(sized, envelope=replace(sized.envelope, valid_until=EXPIRY + timedelta(seconds=1))),
        replace(sized, envelope=replace(sized.envelope, origin_quote_id="FIXTURE_OTHER")),
    ):
        assert c.sizing_sha256_v2(changed) != digest
    costs = costs_v2()
    assert c.cost_allowance_sha256_v2(
        replace(
            costs, reference=replace(costs.reference, computed_at=NOW + timedelta(microseconds=1))
        )
    ) != c.cost_allowance_sha256_v2(costs)


def test_canonical_payload_matches_independent_literal_policy() -> None:
    expected = {
        "schema": "RISK_V2_CANONICAL_V1",
        "record_type": "LinearRiskPolicyV2",
        "fields": {
            "scope": {
                "product_scope": {
                    "account": {"account_id": "FIXTURE_ACCOUNT", "environment": "DEMO"},
                    "product": "LINEAR",
                },
                "instrument": {"venue": "BYBIT", "product": "LINEAR", "symbol": "XAAUSDT"},
                "strategy": {"strategy_id": "FIXTURE_STRATEGY", "revision": "FIXTURE_STRATEGY_V1"},
            },
            "revision": "FIXTURE_POLICY_V1",
            "sizing_rule": "LOSS_FIXED",
            "caps": {
                "max_order_admission_notional": "500",
                "max_account_admission_notional": "1000",
                "valuation_rule": "LINEAR_MARK_GROSS_AT_ADMISSION_V1",
                "turnover_requirement": "NOT_REQUIRED",
                "max_execution_quote_amount": None,
            },
            "leverage_min": "1",
            "leverage_max": "150",
            "per_trade_loss": "12",
            "max_daily_loss": "100",
            "max_drawdown": "200",
            "max_consecutive_losses": 3,
            "max_positions": 4,
            "max_total_risk": "300",
            "max_total_margin": "1000",
            "margin_allocation": "60",
            "margin_mode": "ISOLATED",
            "position_mode": "ONE_WAY",
            "liquidation_buffer_fraction": "0.01",
            "max_quote_age_seconds": "2",
            "max_evidence_age_seconds": "10",
        },
    }
    text = json.dumps(
        expected, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )
    assert c.risk_policy_sha256_v2(policy_v2()) == hashlib.sha256(text.encode("ascii")).hexdigest()
    assert c.risk_policy_sha256_v2(
        replace(policy_v2(), per_trade_loss=D("12.0"))
    ) != c.risk_policy_sha256_v2(policy_v2())


def test_valid_large_integer_counts_do_not_depend_on_global_integer_string_limit() -> None:
    # 5001 digits are inside the closed payload budget and need no str(int).
    policy = replace(policy_v2(), max_positions=10**5000)
    before = sys.get_int_max_str_digits()
    assert len(c.risk_policy_sha256_v2(policy)) == 64
    assert sys.get_int_max_str_digits() == before


def test_public_hashes_reject_top_level_subclasses_and_nested_shared_forgeries() -> None:
    for function, value in (
        (c.risk_policy_sha256_v2, policy_v2()),
        (c.cost_allowance_sha256_v2, costs_v2()),
        (c.envelope_sha256_v2, envelope_v2()),
        (c.sizing_sha256_v2, sizing_v2()),
    ):
        subclass = type("Forged", (type(value),), {})
        with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
            function(object.__new__(subclass))
    scope = scope_v2()
    for parent_name in ("product_scope", "instrument", "strategy"):
        parent = getattr(scope, parent_name)
        for field in fields(type(parent)):
            with pytest.raises(c.RiskV2Rejected):
                c.risk_policy_sha256_v2(
                    _forged(
                        policy_v2(),
                        scope=_forged(scope, **{parent_name: _forged(parent, missing=field.name)}),
                    )
                )
    for field in fields(type(scope.product_scope.account)):
        with pytest.raises(c.RiskV2Rejected):
            c.risk_policy_sha256_v2(
                _forged(
                    policy_v2(),
                    scope=_forged(
                        scope,
                        product_scope=_forged(
                            scope.product_scope,
                            account=_forged(scope.product_scope.account, **{field.name: object()}),
                        ),
                    ),
                )
            )


class _CustomTimezone(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta:
        raise AssertionError("custom timezone hook")

    def dst(self, dt: datetime | None) -> timedelta:
        raise AssertionError("custom timezone hook")

    def tzname(self, dt: datetime | None) -> str:
        raise AssertionError("custom timezone hook")


@pytest.mark.parametrize(
    "bad",
    [
        NOW.replace(tzinfo=None),
        NOW.replace(tzinfo=_CustomTimezone()),
        NOW.replace(tzinfo=ZoneInfo("UTC")),
        "2026-11-03T00:00:00Z",
    ],
)
def test_only_exact_standard_timezone_is_accepted(bad: object) -> None:
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        c.cost_allowance_sha256_v2(
            _forged(costs_v2(), reference=_forged(computation_ref_v2(), computed_at=bad))
        )


def test_standard_timezone_normalizes_and_hashes_to_exact_microseconds() -> None:
    local = NOW.astimezone(timezone(timedelta(hours=9)))
    reference = replace(computation_ref_v2(), computed_at=local)
    assert reference.computed_at.tzinfo is UTC
    assert c.cost_allowance_sha256_v2(
        replace(costs_v2(), reference=reference)
    ) == c.cost_allowance_sha256_v2(costs_v2())
    payload = c._proposal_payload_v2(proposal_v2())
    assert payload["entry"] == {
        "side": "BUY",
        "limit_price": "100",
        "origin_quote_id": "FIXTURE_QUOTE",
        "valid_until": "2026-11-03T00:00:01.000000+00:00",
    }


def test_source_narrow_bounds_and_derived_large_values_are_separate() -> None:
    large = D((0, (1,) + (0,) * 1999 + (1,), -1000))
    for source in (large, D("1E1001"), D("1E-1001"), D("1" * 257)):
        with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_RESOURCE_LIMIT$"):
            c.risk_policy_sha256_v2(_forged(policy_v2(), per_trade_loss=source))
    costs = replace(costs_v2(), fee_quote_per_base=large)
    assert len(c.cost_allowance_sha256_v2(costs)) == 64
    assert (
        len(
            c.cost_allowance_sha256_v2(
                replace(costs_v2(), fee_quote_per_base=D((0, (1,) * 63000, 0)))
            )
        )
        == 64
    )
    for too_large in (D((0, (1,) * 65537, 0)), D("1E65537")):
        with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_RESOURCE_LIMIT$"):
            c.cost_allowance_sha256_v2(_forged(costs_v2(), fee_quote_per_base=too_large))
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_RESOURCE_LIMIT$"):
        c.cost_allowance_sha256_v2(_forged(costs_v2(), fee_quote_per_base=D((0, (1,) * 65000, 0))))


def test_generated_2001_digit_sizing_result_survives_digest_revalidation() -> None:
    # Exact literals: (1E1000 - 1E-1000) + 2E-1000 = 1E1000 + 1E-1000.
    distance = D((0, (9,) * 2000, -1000))
    unit_loss = D((0, (1,) + (0,) * 1999 + (1,), -1000))
    sized = replace(
        sizing_v2(),
        envelope=replace(envelope_v2(), limit_price=D("1E1000")),
        stop_price=D("1E-1000"),
        stop_distance_quote_per_base=distance,
        unit_cost_quote=D("2E-1000"),
        unit_loss_quote=unit_loss,
        estimated_loss_quote=unit_loss,
        cost_allowance_quote=D("2E-1000"),
        funding_allowance_quote=D(0),
    )
    assert len(c.sizing_sha256_v2(sized)) == 64
    assert c.sizing_sha256_v2(_forged(sized)) == c.sizing_sha256_v2(sized)


def test_ambient_decimal_context_is_unchanged_by_hashing_or_rejection() -> None:
    policy, costs, envelope, sizing = policy_v2(), costs_v2(), envelope_v2(), sizing_v2()
    checks: tuple[tuple[Callable[[Any], str], Any], ...] = (
        (c.risk_policy_sha256_v2, policy),
        (c.cost_allowance_sha256_v2, costs),
        (c.envelope_sha256_v2, envelope),
        (c.sizing_sha256_v2, sizing),
    )
    expected = tuple(function(value) for function, value in checks)
    for rounding in ("ROUND_UP", "ROUND_DOWN", "ROUND_HALF_EVEN"):
        with localcontext() as ctx:
            ctx.prec = 1
            ctx.rounding = rounding
            ctx.Emax = 1
            ctx.Emin = -1
            ctx.capitals = 0
            ctx.clamp = 1
            ctx.traps[Inexact] = True
            ctx.traps[Rounded] = True
            ctx.flags[Inexact] = True
            before = ctx.copy()
            actual = tuple(function(value) for function, value in checks)
            assert actual == expected
            with pytest.raises(c.RiskV2Rejected):
                c.risk_policy_sha256_v2(_forged(policy, per_trade_loss=D("NaN")))
            after = getcontext()
            assert (
                after.prec,
                after.rounding,
                after.Emax,
                after.Emin,
                after.capitals,
                after.clamp,
                after.flags,
                after.traps,
            ) == (
                before.prec,
                before.rounding,
                before.Emax,
                before.Emin,
                before.capitals,
                before.clamp,
                before.flags,
                before.traps,
            )


def test_import_and_hashing_need_no_environment_clock_or_external_effect() -> None:
    script = """
import builtins, datetime, os, pathlib, socket, sys, time, types
from tests.execution.risk_v2_support import policy_v2
from multi_exchange_trader.execution import risk_v2_contracts as c
source=pathlib.Path(c.__file__).read_text(encoding="utf-8")
module=types.ModuleType("task1_guarded_contracts")
sys.modules[module.__name__]=module
def denied(*args, **kwargs): raise AssertionError("forbidden effect")
os.getenv=denied
os.environ=type("NoEnvironment",(),{"__getitem__":denied,"get":denied})()
socket.socket=denied
builtins.open=denied
def audit(event,args):
    if event in ("open","socket.__new__","subprocess.Popen","os.system","sqlite3.connect"):
        denied()
sys.addaudithook(audit)
def profile(frame,event,arg):
    if event=="c_call" and getattr(arg,"__name__",None) in (
        "now","utcnow","today","time","monotonic","perf_counter","process_time"):
        denied()
sys.setprofile(profile)
exec(compile(source,"<guarded-contracts>","exec"),module.__dict__)
policy=policy_v2()
copied_scope=module.RiskScopeV2(policy.scope.product_scope,policy.scope.instrument,policy.scope.strategy)
caps=policy.caps
copied_caps=module.LinearCapitalCapsV2(caps.max_order_admission_notional,
    caps.max_account_admission_notional,caps.valuation_rule,caps.turnover_requirement,
    caps.max_execution_quote_amount)
values={field.name:getattr(policy,field.name) for field in module.fields(module.LinearRiskPolicyV2)}
values.update(scope=copied_scope,caps=copied_caps)
assert len(module.risk_policy_sha256_v2(module.LinearRiskPolicyV2(**values)))==64
sys.setprofile(None)
print("TASK1_PURE_OK")
"""
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=environment, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "TASK1_PURE_OK\n"
