"""Synthetic mark admission; supplied sets do not prove actual account coverage."""

import hashlib
import json
from dataclasses import fields, replace
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal as D
from decimal import Inexact, Rounded, localcontext
from fractions import Fraction
from typing import Any, cast

import pytest

from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.linear_risk_v2 import value_linear_admission_v2
from multi_exchange_trader.trading_product import InstrumentKey
from tests.execution.risk_v2_support import (
    DIGEST,
    NOW,
    costs_v2,
    envelope_v2,
    observation_ref_v2,
    policy_v2,
    sizing_v2,
)

X = InstrumentKey("BYBIT", "LINEAR", "XAAUSDT")
Y = InstrumentKey("BYBIT", "LINEAR", "YBBUSDT")


def _inputs() -> dict[str, Any]:
    policy = policy_v2()
    account = c.LinearAccountRiskSnapshotV2(
        policy.scope.product_scope.account,
        observation_ref_v2("ACCOUNT"),
        "COVERAGE_V1",
        (
            c.ExposureQuantityV2("AX", X, "ACCOUNT_POSITION", "BUY", D(1)),
            c.ExposureQuantityV2("AY", Y, "ACCOUNT_ENTRY_ORDER", "SELL", D(2)),
        ),
        "UTC_REALIZED_NET_FEES_FUNDING",
        NOW.date(),
        D(1000),
        D(500),
        D(0),
        D(0),
        1,
        D(0),
        D(0),
        0,
        "ISOLATED",
        "ONE_WAY",
        (),
    )
    reservations = c.LinearReservationSnapshotV2(
        account.account,
        "RESERVATION",
        "OWNER_V1",
        1,
        DIGEST,
        NOW,
        None,
        (c.ExposureQuantityV2("RX", X, "PENDING_ENTRY", "SELL", D("0.5")),),
        D(0),
        D(0),
        1,
    )
    return {
        "policy": policy,
        "sizing": sizing_v2(),
        "account": account,
        "reservations": reservations,
        "marks": (
            c.LinearMarkQuoteV2(X, observation_ref_v2("MARK_X"), D(140)),
            c.LinearMarkQuoteV2(Y, observation_ref_v2("MARK_Y"), D(50)),
        ),
        "now": NOW,
    }


def _value(**changes: Any) -> c.LinearAdmissionValuationV2:
    inputs = _inputs()
    inputs.update(changes)
    return value_linear_admission_v2(**inputs)


def _with_policy(inputs: dict[str, Any], **caps: Any) -> dict[str, Any]:
    policy = replace(inputs["policy"], caps=replace(inputs["policy"].caps, **caps))
    digest = c.risk_policy_sha256_v2(policy)
    sizing = replace(
        inputs["sizing"],
        policy_sha256=digest,
        envelope=replace(inputs["sizing"].envelope, policy_sha256=digest),
    )
    return {**inputs, "policy": policy, "sizing": sizing}


def test_literal_all_supplied_quantities_at_mark() -> None:
    v = _value()
    assert (
        v.account_gross_quote,
        v.reserved_gross_quote,
        v.proposed_mark_quote,
        v.combined_gross_quote,
    ) == (D(240), D(70), D(140), D(450))


@pytest.mark.parametrize(
    "cap,amount,code",
    [
        ("max_order_admission_notional", D(139), "RISK_V2_ORDER_ADMISSION_LIMIT"),
        ("max_account_admission_notional", D(449), "RISK_V2_ACCOUNT_ADMISSION_LIMIT"),
    ],
)
def test_literal_cap_failures(cap: str, amount: D, code: str) -> None:
    with pytest.raises(c.RiskV2Rejected, match=f"^{code}$"):
        value_linear_admission_v2(**_with_policy(_inputs(), **{cap: amount}))


def test_exact_450_passes_and_mark_141_revalues_452_5() -> None:
    inputs = _with_policy(_inputs(), max_account_admission_notional=D(450))
    assert value_linear_admission_v2(**inputs).combined_gross_quote == D(450)
    inputs["marks"] = (replace(inputs["marks"][0], mark_price=D(141)), inputs["marks"][1])
    assert value_linear_admission_v2(
        **_with_policy(inputs, max_account_admission_notional=D(1000))
    ).combined_gross_quote == D("452.5")
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_ACCOUNT_ADMISSION_LIMIT$"):
        value_linear_admission_v2(**inputs)


def test_mark_and_execution_and_minimum_valuations_are_distinct() -> None:
    inputs = _with_policy(
        _inputs(),
        turnover_requirement="HARD_EXECUTION_QUOTE_CAP",
        max_execution_quote_amount=D(100),
    )
    assert value_linear_admission_v2(**inputs).proposed_mark_quote == D(140)
    assert inputs["sizing"].envelope.limit_price == D(100)
    # A hypothetical later accepted minimum rule evaluates Q1 at95. Task3 has
    # no minimum DTO/input and cannot issue that later rule's qualification.
    assert inputs["sizing"].envelope.base_quantity * D(95) == D(95)


def _forged(value: Any, **changes: Any) -> Any:
    result = object.__new__(type(value))
    for field in fields(value):
        object.__setattr__(result, field.name, changes.get(field.name, getattr(value, field.name)))
    return result


def _reject(code: str = "RISK_V2_INPUT_INVALID") -> Any:
    return pytest.raises(c.RiskV2Rejected, match=f"^{code}$")


@pytest.mark.parametrize("missing", [0, 1])
def test_missing_selected_or_held_y_mark_rejected(missing: int) -> None:
    marks = _inputs()["marks"]
    with _reject("RISK_V2_MARK_COVERAGE_REQUIRED"):
        _value(marks=(marks[1 - missing],))


def test_extra_duplicate_unsorted_marks_and_same_symbol_spot() -> None:
    marks = _inputs()["marks"]
    extra = c.LinearMarkQuoteV2(
        InstrumentKey("BYBIT", "LINEAR", "ZCCUSDT"), observation_ref_v2("MARK_Z"), D(10)
    )
    for invalid in ((marks[0], marks[0]), marks[::-1], (*marks, extra)):
        with _reject("RISK_V2_MARK_COVERAGE_REQUIRED"):
            _value(marks=invalid)
    spot = _forged(marks[0], instrument=InstrumentKey("BYBIT", "SPOT", "XAAUSDT"))
    with _reject("RISK_V2_UNSUPPORTED_PRODUCT"):
        _value(marks=(spot, marks[1]))


def test_component_ids_globally_unique_with_disjoint_origins() -> None:
    inputs = _inputs()
    account, reservations = inputs["account"], inputs["reservations"]
    duplicate = replace(account.components[1], component_id="AX")
    with _reject("RISK_V2_EXPOSURE_OVERLAP"):
        replace(account, components=(account.components[0], duplicate))
    overlap = replace(reservations.components[0], component_id="AX")
    with _reject("RISK_V2_EXPOSURE_OVERLAP"):
        _value(reservations=replace(reservations, components=(overlap,)))
    with _reject("RISK_V2_EXPOSURE_OVERLAP"):
        replace(account, components=(replace(account.components[0], origin="PENDING_ENTRY"),))
    with _reject("RISK_V2_EXPOSURE_OVERLAP"):
        replace(
            reservations,
            components=(replace(reservations.components[0], origin="ACCOUNT_ENTRY_ORDER"),),
        )
    with _reject():
        replace(account, components=account.components[::-1])


def test_no_netting_and_empty_sets_supported() -> None:
    inputs = _inputs()
    assert _value().reserved_gross_quote == D(70)  # pending SELL adds to held BUY
    assert _value(
        account=replace(inputs["account"], components=()),
        reservations=replace(inputs["reservations"], components=()),
        marks=(inputs["marks"][0],),
    ).combined_gross_quote == D(140)


def test_ack_absent_from_both_sets_cannot_be_detected_from_local_ids() -> None:
    # There is no native-order/ACK registry in this signature. Two realities
    # with identical supplied tuples are indistinguishable here. Task13's
    # actual producer must prove ACK/UNKNOWN/SENDING inclusion before admission.
    inputs = _inputs()
    inputs["account"] = replace(inputs["account"], components=())
    inputs["reservations"] = replace(inputs["reservations"], components=())
    inputs["marks"] = (inputs["marks"][0],)
    result = value_linear_admission_v2(**inputs)
    assert result.combined_gross_quote == D(140)
    assert not hasattr(result, "qualified")
    assert not hasattr(result, "submission_ready")


def test_buy_hard_execution_exact_boundary_and_sell_floor_never_upper() -> None:
    inputs = _with_policy(
        _inputs(),
        turnover_requirement="HARD_EXECUTION_QUOTE_CAP",
        max_execution_quote_amount=D(100),
    )
    assert value_linear_admission_v2(**inputs).combined_gross_quote == D(450)
    with _reject("RISK_V2_EXECUTION_QUOTE_LIMIT"):
        value_linear_admission_v2(**_with_policy(inputs, max_execution_quote_amount=D("99.999")))
    sizing = inputs["sizing"]
    inputs["sizing"] = replace(
        sizing, envelope=replace(sizing.envelope, side="SELL"), stop_price=D(110)
    )
    assert inputs["sizing"].envelope.limit_price == D(100)
    better_sell_fill = D(300)
    assert better_sell_fill > inputs["policy"].caps.max_execution_quote_amount
    with _reject("RISK_V2_SELL_EXECUTION_UPPER_UNAVAILABLE"):
        value_linear_admission_v2(**inputs)
    assert value_linear_admission_v2(
        **_with_policy(inputs, turnover_requirement="NOT_REQUIRED", max_execution_quote_amount=None)
    ).proposed_mark_quote == D(140)


def test_private_account_and_public_mark_labels_without_fake_mapping() -> None:
    inputs = _inputs()
    account, marks = inputs["account"], inputs["marks"]
    valid_account = replace(
        account, reference=replace(account.reference, source_environment="DEMO")
    )
    public = tuple(
        replace(m, reference=replace(m.reference, source_environment="PUBLIC_MAINNET"))
        for m in marks
    )
    assert _value(account=valid_account, marks=public).combined_gross_quote == D(450)
    for environment in ("LIVE", "PUBLIC_MAINNET", "PUBLIC_TESTNET"):
        with _reject("RISK_V2_SOURCE_MISMATCH"):
            replace(account, reference=replace(account.reference, source_environment=environment))
    for environment in ("DEMO", "LIVE"):
        with _reject("RISK_V2_SOURCE_MISMATCH"):
            replace(marks[0], reference=replace(marks[0].reference, source_environment=environment))


@pytest.mark.parametrize("snapshot", ["account", "reservations"])
def test_account_environment_scope_mismatch(snapshot: str) -> None:
    inputs = _inputs()
    from multi_exchange_trader.execution.protection import ProtectionScope

    inputs[snapshot] = replace(inputs[snapshot], account=ProtectionScope("OTHER", "DEMO"))
    with _reject("RISK_V2_SCOPE_MISMATCH"):
        value_linear_admission_v2(**inputs)


@pytest.mark.parametrize(
    "location",
    [
        "mark_server",
        "mark_receipt",
        "account_server",
        "account_receipt",
        "reservation",
        "cost",
        "pin_server",
        "pin_receipt",
    ],
)
@pytest.mark.parametrize("direction", ["stale", "future"])
def test_each_available_source_time_is_checked(location: str, direction: str) -> None:
    inputs = _inputs()
    seconds = 3 if location.startswith("mark") else 11
    instant = (
        NOW - timedelta(seconds=seconds)
        if direction == "stale"
        else NOW + timedelta(microseconds=1)
    )
    if location.startswith("mark"):
        mark = inputs["marks"][0]
        reference = replace(mark.reference, server_observed_at=instant, received_at=instant)
        if location == "mark_receipt" and direction == "future":
            reference = replace(reference, server_observed_at=NOW)
        inputs["marks"] = (replace(mark, reference=reference), inputs["marks"][1])
    elif location.startswith("account"):
        account = inputs["account"]
        reference = replace(account.reference, server_observed_at=instant, received_at=instant)
        if location == "account_receipt" and direction == "future":
            reference = replace(reference, server_observed_at=NOW)
        inputs["account"] = replace(account, reference=reference)
    elif location == "reservation":
        inputs["reservations"] = replace(inputs["reservations"], observed_at=instant)
    elif location == "cost":
        # A forged sizing retains the valid static audit ordering in future case.
        sizing = inputs["sizing"]
        inputs["sizing"] = replace(
            sizing,
            cost_reference=replace(sizing.cost_reference, computed_at=instant),
            assessed_at=instant if direction == "future" else NOW,
        )
    else:
        sizing = inputs["sizing"]
        pin = replace(sizing.envelope.instrument_pin, response_time=instant, received_at=instant)
        inputs["sizing"] = replace(sizing, envelope=replace(sizing.envelope, instrument_pin=pin))
    with _reject("RISK_V2_STALE_EVIDENCE" if direction == "stale" else "RISK_V2_FUTURE_EVIDENCE"):
        value_linear_admission_v2(**inputs)


def test_freshness_inclusive_microseconds_zero_age_and_strict_expiry() -> None:
    inputs = _inputs()
    marks = tuple(
        replace(
            m,
            reference=replace(
                m.reference, server_observed_at=NOW - timedelta(seconds=2), received_at=NOW
            ),
        )
        for m in inputs["marks"]
    )
    assert _value(marks=marks).combined_gross_quote == D(450)
    too_old = replace(
        marks[0],
        reference=replace(
            marks[0].reference, server_observed_at=NOW - timedelta(seconds=2, microseconds=1)
        ),
    )
    with _reject("RISK_V2_STALE_EVIDENCE"):
        _value(marks=(too_old, marks[1]))
    inputs["marks"] = tuple(
        replace(m, reference=replace(m.reference, server_observed_at=NOW, received_at=NOW))
        for m in marks
    )
    policy = replace(inputs["policy"], max_quote_age_seconds=D(0))
    digest = c.risk_policy_sha256_v2(policy)
    inputs["policy"] = policy
    inputs["sizing"] = replace(
        inputs["sizing"],
        policy_sha256=digest,
        envelope=replace(inputs["sizing"].envelope, policy_sha256=digest),
    )
    assert value_linear_admission_v2(**inputs).combined_gross_quote == D(450)
    with _reject("RISK_V2_EXPIRED"):
        _value(now=NOW + timedelta(seconds=1))


def test_audit_time_is_not_source_age_and_available_pin_cost_dag_checked() -> None:
    inputs = _inputs()
    old = NOW - timedelta(seconds=9)
    sizing = inputs["sizing"]
    pin = replace(sizing.envelope.instrument_pin, response_time=old, received_at=old)
    sizing = replace(
        sizing,
        cost_reference=replace(sizing.cost_reference, computed_at=old),
        assessed_at=old,
        envelope=replace(sizing.envelope, instrument_pin=pin),
    )
    assert _value(sizing=sizing).combined_gross_quote == D(450)
    later_audit = replace(sizing, assessed_at=NOW)
    assert c.sizing_sha256_v2(sizing) == c.sizing_sha256_v2(later_audit)
    broken = replace(
        sizing, envelope=replace(sizing.envelope, instrument_pin=replace(pin, received_at=NOW))
    )
    with _reject("RISK_V2_CAUSAL_ORDER_INVALID"):
        _value(sizing=broken)


def test_source_and_freshness_errors_precede_cap_failure() -> None:
    inputs = _with_policy(_inputs(), max_account_admission_notional=D(1))
    marks = inputs["marks"]
    inputs["marks"] = (
        replace(
            marks[0],
            reference=replace(marks[0].reference, server_observed_at=NOW - timedelta(seconds=3)),
        ),
        marks[1],
    )
    with _reject("RISK_V2_STALE_EVIDENCE"):
        value_linear_admission_v2(**inputs)
    bad = _forged(
        inputs["account"], reference=replace(inputs["account"].reference, source_environment="LIVE")
    )
    inputs["account"] = bad
    with _reject("RISK_V2_SOURCE_MISMATCH"):
        value_linear_admission_v2(**inputs)


def test_repeated_observation_ids_preserved_in_key_order() -> None:
    inputs = _inputs()
    marks = tuple(
        replace(m, reference=replace(m.reference, observation_id="SAME_SOURCE"))
        for m in inputs["marks"]
    )
    assert _value(marks=marks).mark_observation_ids == ("SAME_SOURCE", "SAME_SOURCE")


def test_original_four_hash_bytes_preserved() -> None:
    assert (
        c.risk_policy_sha256_v2(policy_v2())
        == "db628ce1ddc8c09851b09623bf76004dcc558200235e9144357e77b538e222d2"
    )
    assert (
        c.cost_allowance_sha256_v2(costs_v2())
        == "ba749ac1e526ab07effb02c5e9cf78b9108c005a78b95f6fe1101b16962e69d1"
    )
    assert (
        c.envelope_sha256_v2(envelope_v2())
        == "dab6230eac400a1207e30356ba90c7d551e2dc687dfcd07d9aa0ef68261ed8cb"
    )
    assert (
        c.sizing_sha256_v2(sizing_v2())
        == "090f072bf456febb695f587d185ebd35c7d725d260ad9ec7b3c7134c312c5f2f"
    )


def test_literal_valuation_canonical_and_audit_hash_exclusion() -> None:
    value = _value()
    # Independently specified complete financial payload, including ordered IDs.
    literal = {
        "schema": "RISK_V2_CANONICAL_V1",
        "record_type": "LinearAdmissionValuationV2",
        "fields": {
            "scope": {
                "product_scope": {
                    "account": {"account_id": "FIXTURE_ACCOUNT", "environment": "DEMO"},
                    "product": "LINEAR",
                },
                "instrument": {"venue": "BYBIT", "product": "LINEAR", "symbol": "XAAUSDT"},
                "strategy": {"strategy_id": "FIXTURE_STRATEGY", "revision": "FIXTURE_STRATEGY_V1"},
            },
            "account_observation_id": "ACCOUNT",
            "reservation_snapshot_id": "RESERVATION",
            "mark_observation_ids": ["MARK_X", "MARK_Y"],
            "account_gross_quote": "240",
            "reserved_gross_quote": "70",
            "proposed_mark_quote": "140",
            "combined_gross_quote": "450",
            "valuation_rule": "LINEAR_MARK_GROSS_AT_ADMISSION_V1",
        },
    }
    expected = hashlib.sha256(
        json.dumps(literal, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "ascii"
        )
    ).hexdigest()
    assert c.valuation_sha256_v2(value) == expected
    assert (
        c.valuation_sha256_v2(replace(value, assessed_at=NOW + timedelta(microseconds=1)))
        == expected
    )
    with _reject():
        replace(value, combined_gross_quote=D(451))


def test_all_four_new_hashes_bind_economic_and_source_fields() -> None:
    inputs = _inputs()
    account, reservations, marks = inputs["account"], inputs["reservations"], inputs["marks"]
    assert c.account_snapshot_sha256_v2(account) != c.account_snapshot_sha256_v2(
        replace(account, equity=D("1000.0"))
    )
    assert c.account_snapshot_sha256_v2(account) != c.account_snapshot_sha256_v2(
        replace(account, coverage_revision="COVERAGE_V2")
    )
    assert c.reservation_snapshot_sha256_v2(reservations) != c.reservation_snapshot_sha256_v2(
        replace(reservations, journal_revision=2)
    )
    assert c.mark_set_sha256_v2(marks) != c.mark_set_sha256_v2(
        (replace(marks[0], mark_price=D("140.0")), marks[1])
    )
    assert c.mark_set_sha256_v2(marks) != c.mark_set_sha256_v2(
        (
            replace(
                marks[0],
                reference=replace(marks[0].reference, received_at=NOW + timedelta(microseconds=1)),
            ),
            marks[1],
        )
    )
    assert c.valuation_sha256_v2(_value()) != c.valuation_sha256_v2(
        _value(marks=(replace(marks[0], mark_price=D(141)), marks[1]))
    )


class _Hostile:
    def __eq__(self, other: object) -> bool:
        raise AssertionError("equality hook invoked")

    def __hash__(self) -> int:
        raise AssertionError("hash hook invoked")

    def __repr__(self) -> str:
        raise AssertionError("repr hook invoked")


@pytest.mark.parametrize(
    "argument", ["policy", "sizing", "account", "reservations", "marks", "now"]
)
def test_top_level_exact_types_before_hooks(argument: str) -> None:
    with _reject():
        _value(**{argument: _Hostile()})


def test_every_new_dto_field_missing_or_hostile_revalidated() -> None:
    inputs = _inputs()
    samples = [
        inputs["account"].components[0],
        inputs["marks"][0],
        inputs["account"],
        inputs["reservations"],
        _value(),
    ]
    for sample in samples:
        for field in fields(sample):
            for missing in (False, True):
                bad = _forged(sample, **{field.name: _Hostile()})
                if missing:
                    object.__delattr__(bad, field.name)
                with pytest.raises(c.RiskV2Rejected):
                    if type(sample) is c.ExposureQuantityV2:
                        _value(account=_forged(inputs["account"], components=(bad,)))
                    elif type(sample) is c.LinearMarkQuoteV2:
                        _value(marks=(bad, inputs["marks"][1]))
                    elif type(sample) is c.LinearAccountRiskSnapshotV2:
                        c.account_snapshot_sha256_v2(bad)
                    elif type(sample) is c.LinearReservationSnapshotV2:
                        c.reservation_snapshot_sha256_v2(bad)
                    else:
                        c.valuation_sha256_v2(bad)


def test_public_boundary_revalidates_full_nested_policy_sizing_account_and_reservations() -> None:
    inputs = _inputs()
    for name in ("policy", "sizing", "account", "reservations"):
        sample = inputs[name]
        for field in fields(sample):
            with pytest.raises(c.RiskV2Rejected):
                _value(**{name: _forged(sample, **{field.name: _Hostile()})})
    for name in ("account", "reservations"):
        account = _forged(inputs[name].account, account_id=_Hostile())
        with _reject():
            _value(**{name: _forged(inputs[name], account=account)})
    reference = _forged(inputs["marks"][0].reference, source_environment=_Hostile())
    with _reject():
        _value(marks=(_forged(inputs["marks"][0], reference=reference), inputs["marks"][1]))


def test_subclasses_and_mutable_containers_rejected() -> None:
    class AccountSubclass(c.LinearAccountRiskSnapshotV2):
        pass

    inputs = _inputs()
    fake = object.__new__(AccountSubclass)
    with _reject():
        _value(account=fake)
    for name in ("account", "reservations"):
        with _reject():
            replace(inputs[name], components=list(inputs[name].components))
    with _reject():
        _value(marks=list(inputs["marks"]))
    with _reject():
        replace(inputs["account"], external_same_symbol_activity_ids=["EXTERNAL"])


@pytest.mark.parametrize("quantity", [D(0), D(-1), D("NaN"), D("Infinity"), 1, True, "1"])
def test_exact_finite_positive_component_and_mark_values(quantity: Any) -> None:
    inputs = _inputs()
    with _reject():
        replace(inputs["account"].components[0], base_quantity=quantity)
    with _reject():
        replace(inputs["marks"][0], mark_price=quantity)


def test_source_vs_derived_representation_and_boundaries() -> None:
    inputs = _inputs()
    large = D((0, (1,) * 257, -256))
    for item, name in (
        (inputs["account"].components[0], "base_quantity"),
        (inputs["marks"][0], "mark_price"),
    ):
        with _reject("RISK_V2_RESOURCE_LIMIT"):
            replace(item, **{name: large})
    pending = replace(inputs["reservations"].components[0], base_quantity=large)
    assert c.reservation_snapshot_sha256_v2(replace(inputs["reservations"], components=(pending,)))
    assert c.account_snapshot_sha256_v2(replace(inputs["account"], equity=large, daily_loss=large))
    assert c.reservation_snapshot_sha256_v2(replace(inputs["reservations"], risk_amount=large))
    with _reject("RISK_V2_RESOURCE_LIMIT"):
        replace(inputs["account"], equity=D((0, (1,) * 65537, -1)))
    for count in (True, -1, D(1)):
        with _reject():
            replace(inputs["account"], position_count=count)
    with _reject("RISK_V2_RESOURCE_LIMIT"):
        c.account_snapshot_sha256_v2(replace(inputs["account"], position_count=1 << 1_000_000))


def test_local_tuple_limits_and_external_canonical_ids() -> None:
    inputs = _inputs()
    account, reservations = inputs["account"], inputs["reservations"]
    for sample in (account, reservations):
        with _reject("RISK_V2_RESOURCE_LIMIT"):
            replace(sample, components=sample.components * 1025)
    with _reject("RISK_V2_RESOURCE_LIMIT"):
        _value(marks=(inputs["marks"][0],) * 101)
    with _reject():
        _value(marks=())
    for external in (("B", "A"), ("A", "A"), ("bad space",)):
        with _reject():
            replace(account, external_same_symbol_activity_ids=external)
    allowed = tuple(f"E{i:04}" for i in range(1024))
    assert c.account_snapshot_sha256_v2(replace(account, external_same_symbol_activity_ids=allowed))
    with _reject("RISK_V2_RESOURCE_LIMIT"):
        replace(account, external_same_symbol_activity_ids=(*allowed, "E1024"))


def test_canonical_array_bytes_bounded_before_encoding() -> None:
    inputs = _inputs()
    components = tuple(
        replace(inputs["account"].components[0], component_id=f"C{i:04}") for i in range(1024)
    )
    account = replace(inputs["account"], components=components)
    with _reject("RISK_V2_RESOURCE_LIMIT"):
        c.account_snapshot_sha256_v2(account)
    with _reject():
        c._json_size_v2(["not a validated tuple"])
    with _reject():
        c._json_text_v2(_Hostile())


def test_derived_large_valuation_rehashed_without_source_limit() -> None:
    # Two bounded source prices whose exact sum has2001 coefficient digits.
    inputs = _inputs()
    account = replace(inputs["account"], components=(inputs["account"].components[0],))
    reservations = replace(inputs["reservations"], components=())
    prices = (replace(inputs["marks"][0], mark_price=D("1E-1000")),)
    amount = c._gross_components_v2(account.components, prices={X: D("1E+1000")})
    small = c._gross_components_v2(account.components, prices={X: D("1E-1000")})
    from multi_exchange_trader.execution.risk_v2_math import finite_decimal_v2

    total = finite_decimal_v2(Fraction(amount) + Fraction(small))
    value = c.LinearAdmissionValuationV2(
        inputs["policy"].scope,
        NOW,
        "ACCOUNT",
        "RESERVATION",
        ("MARK_X",),
        amount,
        D(0),
        small,
        total,
        "LINEAR_MARK_GROSS_AT_ADMISSION_V1",
    )
    assert len(total.as_tuple().digits) == 2001
    assert c.valuation_sha256_v2(value)
    assert _value(
        account=account, reservations=reservations, marks=prices
    ).proposed_mark_quote == D("1E-1000")


def test_helpers_revalidate_before_lookup_and_require_exact_keys() -> None:
    inputs = _inputs()
    with _reject():
        c._mark_values_v2(inputs["marks"], required=(Y, X), now=NOW, max_age_seconds=D(2))
    with _reject():
        c._mark_values_v2(
            inputs["marks"], required=(cast(Any, _Hostile()),), now=NOW, max_age_seconds=D(2)
        )
    with _reject():
        c._gross_components_v2(inputs["account"].components, prices={cast(Any, "bad"): D(140)})
    with _reject("RISK_V2_MARK_COVERAGE_REQUIRED"):
        c._gross_components_v2(inputs["account"].components, prices={X: D(140)})


class _BadTZ(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta:
        raise AssertionError("timezone hook invoked")

    def dst(self, dt: datetime | None) -> timedelta:
        raise AssertionError("timezone hook invoked")

    def tzname(self, dt: datetime | None) -> str:
        raise AssertionError("timezone hook invoked")


def test_times_date_exact_types_and_utc_normalization() -> None:
    inputs = _inputs()
    offset = timezone(timedelta(hours=9))
    normalized = _value(now=NOW.astimezone(offset))
    assert normalized.assessed_at == NOW and normalized.assessed_at.tzinfo is UTC
    assert replace(inputs["reservations"], observed_at=NOW.astimezone(offset)).observed_at == NOW
    for invalid in (NOW.replace(tzinfo=None), NOW.replace(tzinfo=_BadTZ()), date(2026, 11, 3)):
        with _reject():
            _value(now=invalid)
    with _reject():
        replace(inputs["account"], loss_day=NOW)


@pytest.mark.parametrize("rounding", ["ROUND_UP", "ROUND_DOWN", "ROUND_HALF_EVEN"])
def test_ambient_decimal_context_is_unchanged(rounding: str) -> None:
    baseline = _value()
    expected = c.valuation_sha256_v2(baseline)
    inputs = _inputs()
    expected_hashes = (
        c.account_snapshot_sha256_v2(inputs["account"]),
        c.reservation_snapshot_sha256_v2(inputs["reservations"]),
        c.mark_set_sha256_v2(inputs["marks"]),
    )
    with localcontext() as context:
        context.prec = 1
        context.rounding = rounding
        context.Emax = 1
        context.Emin = -1
        context.capitals = 0
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        before = (context.copy(), dict(context.flags), dict(context.traps))
        assert _value() == baseline
        assert c.valuation_sha256_v2(_value()) == expected
        assert (
            c.account_snapshot_sha256_v2(inputs["account"]),
            c.reservation_snapshot_sha256_v2(inputs["reservations"]),
            c.mark_set_sha256_v2(inputs["marks"]),
        ) == expected_hashes
        assert context.prec == before[0].prec and context.rounding == before[0].rounding
        assert context.Emax == before[0].Emax and context.Emin == before[0].Emin
        assert context.capitals == before[0].capitals
        assert dict(context.flags) == before[1] and dict(context.traps) == before[2]
