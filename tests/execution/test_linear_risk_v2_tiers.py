"""Synthetic generic tier bounds; no actual venue/model qualification."""

import hashlib
import json
from dataclasses import fields, replace
from datetime import timedelta
from decimal import Decimal as D
from decimal import Inexact, Rounded, localcontext
from typing import Any, cast

import pytest
from risk_v2_support import (
    DIGEST,
    NOW,
    instrument_v2,
    observation_ref_v2,
    policy_v2,
    sizing_v2,
)

from multi_exchange_trader.execution import linear_risk_evidence_v2 as e
from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.market_data.instrument_definition import (
    INSTRUMENT_ENDPOINT,
    CatalogSource,
    parse_bybit_instrument_response,
)
from multi_exchange_trader.trading_product import InstrumentKey

X = InstrumentKey("BYBIT", "LINEAR", "XAAUSDT")
Y = InstrumentKey("BYBIT", "LINEAR", "YBBUSDT")


def _rows() -> tuple[e.LinearRiskTierV2, ...]:
    return (
        e.LinearRiskTierV2("z_first", D(0), D(100), D(1), D(150), D(".1"), D(".05"), D(7)),
        e.LinearRiskTierV2("a_second", D(100), D(200), D(1), D("1.5"), D(".7"), D(".2"), D(8)),
        e.LinearRiskTierV2("m_third", D(200), D(500), D(1), D(100), D(".2"), D(".1"), D(9)),
    )


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
        (c.ExposureQuantityV2("RX", X, "PENDING_ENTRY", "SELL", D(".5")),),
        D(0),
        D(0),
        1,
    )
    instrument = instrument_v2()
    return {
        "policy": policy,
        "sizing": sizing_v2(),
        "instrument": instrument,
        "account": account,
        "reservations": reservations,
        "marks": (
            c.LinearMarkQuoteV2(X, observation_ref_v2("MARK_X"), D(140)),
            c.LinearMarkQuoteV2(Y, observation_ref_v2("MARK_Y"), D(50)),
        ),
        "tiers": e.LinearTierSnapshotV2(
            X, observation_ref_v2("TIERS"), instrument.specification_revision, _rows()
        ),
        "now": NOW,
    }


def _policy(inputs: dict[str, Any], **changes: Any) -> dict[str, Any]:
    policy = replace(inputs["policy"], **changes)
    digest = c.risk_policy_sha256_v2(policy)
    sizing = replace(
        inputs["sizing"],
        policy_sha256=digest,
        envelope=replace(inputs["sizing"].envelope, policy_sha256=digest),
    )
    return {**inputs, "policy": policy, "sizing": sizing}


def test_literal_nonmonotonic_whole_interval_and_range_id_order() -> None:
    bound = e.assess_linear_tier_v2(**_inputs())
    # (account1 + pending.5 + proposed1)*mark140 =350; Y is excluded from U.
    assert bound.symbol_gross_upper_quote == D(350)
    assert bound.covered_tier_ids == ("z_first", "a_second", "m_third")
    assert (bound.continuous_leverage_lower, bound.continuous_leverage_upper) == (D(1), D("1.5"))
    assert (bound.permitted_leverage_min, bound.permitted_leverage_max) == (D(1), D("1.5"))
    assert bound.mark_observation_id == "MARK_X"


def test_literal_nonmonotonic_user_minimum_two_rejects() -> None:
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_LEVERAGE_UNSATISFIABLE$"):
        e.assess_linear_tier_v2(**_policy(_inputs(), leverage_min=D(2)))


def test_hashes_retain_generic_imr_mmr_and_noncontinuous_deductions() -> None:
    inputs = _inputs()
    snapshot = inputs["tiers"]
    first_hash = e.tier_snapshot_sha256_v2(snapshot)
    changed = replace(
        snapshot,
        rows=(
            *snapshot.rows[:2],
            replace(snapshot.rows[2], maintenance_margin_deduction=D("9.00")),
        ),
    )
    assert first_hash != e.tier_snapshot_sha256_v2(changed)
    assert len(e.tier_bound_sha256_v2(e.assess_linear_tier_v2(**inputs))) == 64


def _grid(inputs: dict[str, Any], minimum: str, maximum: str, step: str) -> dict[str, Any]:
    instrument = inputs["instrument"]
    row = json.loads(instrument.raw_row_canonical)
    row["leverageFilter"] = {
        "minLeverage": minimum,
        "maxLeverage": maximum,
        "leverageStep": "0" + step if step.startswith(".") else step,
    }
    instrument = parse_bybit_instrument_response(
        {"retCode": 0, "result": {"category": "linear", "list": [row]}, "time": 1793663999000},
        expected=X,
        source=CatalogSource("FIXTURE_CATALOG", "FIXTURE", INSTRUMENT_ENDPOINT, NOW),
    )
    pin = c.pin_instrument_v2(instrument, catalog_revision="FIXTURE_CATALOG_V1")
    return {
        **inputs,
        "instrument": instrument,
        "tiers": replace(inputs["tiers"], instrument_revision=instrument.specification_revision),
        "sizing": replace(
            inputs["sizing"], envelope=replace(inputs["sizing"].envelope, instrument_pin=pin)
        ),
    }


@pytest.mark.parametrize(
    ("low", "high", "step", "first", "last"),
    [
        ("1.1", "1.4", ".3", "1.2", "1.2"),
        ("1.21", "1.5", ".3", "1.5", "1.5"),
        ("1.2", "1.5", ".3", "1.2", "1.5"),
    ],
)
def test_zero_origin_ceil_floor_literal_endpoints(
    low: str,
    high: str,
    step: str,
    first: str,
    last: str,
) -> None:
    result = e.assess_linear_tier_v2(**_grid(_inputs(), low, high, step))
    assert (result.continuous_leverage_lower, result.continuous_leverage_upper) == (D(low), D(high))
    assert (result.permitted_leverage_min, result.permitted_leverage_max) == (D(first), D(last))


def test_continuous_intersection_without_grid_point_rejects() -> None:
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_LEVERAGE_UNSATISFIABLE$"):
        e.assess_linear_tier_v2(**_grid(_inputs(), "1.21", "1.39", ".3"))


@pytest.mark.parametrize(
    ("price", "covered", "maximum"),
    [
        ("40", ("z_first",), "150"),
        ("40.000000000000000000000000000001", ("z_first", "a_second"), "1.5"),
        ("80", ("z_first", "a_second"), "1.5"),
        ("200", ("z_first", "a_second", "m_third"), "1.5"),
    ],
)
def test_open_lower_closed_upper_coverage(
    price: str, covered: tuple[str, ...], maximum: str
) -> None:
    inputs = _inputs()
    inputs["marks"] = (replace(inputs["marks"][0], mark_price=D(price)), inputs["marks"][1])
    result = e.assess_linear_tier_v2(**inputs)
    assert result.covered_tier_ids == covered
    assert result.continuous_leverage_upper == D(maximum)


def test_table_must_cover_upper_and_invalid_unused_row_is_not_ignored() -> None:
    inputs = _inputs()
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_RISK_TIER_UNVERIFIED$"):
        e.assess_linear_tier_v2(**{**inputs, "tiers": replace(inputs["tiers"], rows=_rows()[:2])})
    bad = _forge(_rows()[2], initial_margin_rate=D(0))
    inputs["marks"] = (replace(inputs["marks"][0], mark_price=D(20)), inputs["marks"][1])
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        e.assess_linear_tier_v2(
            **{**inputs, "tiers": _forge(inputs["tiers"], rows=(*_rows()[:2], bad))}
        )


def _forge(value: Any, **changes: Any) -> Any:
    result = object.__new__(type(value))
    for field in fields(value):
        object.__setattr__(result, field.name, changes.get(field.name, getattr(value, field.name)))
    return result


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tier_id", ""),
        ("lower_exclusive_quote", D(-1)),
        ("upper_inclusive_quote", D(0)),
        ("min_leverage", D(0)),
        ("max_leverage", D(".5")),
        ("initial_margin_rate", D("1.01")),
        ("initial_margin_rate", D(".01")),
        ("maintenance_margin_rate", D(0)),
        ("maintenance_margin_deduction", D(-1)),
        ("initial_margin_rate", D("NaN")),
        ("max_leverage", 100),
        ("lower_exclusive_quote", D("1E-1001")),
        ("upper_inclusive_quote", D("1" * 257)),
    ],
)
def test_source_row_invariants(field: str, value: object) -> None:
    code = (
        "RESOURCE_LIMIT"
        if field == "lower_exclusive_quote" and value == D("1E-1001")
        else "INPUT_INVALID"
    )
    if (
        field == "upper_inclusive_quote"
        and type(value) is D
        and len(value.as_tuple().digits) == 257
    ):
        code = "RESOURCE_LIMIT"
    with pytest.raises(c.RiskV2Rejected, match=f"^RISK_V2_{code}$"):
        replace(_rows()[0], **cast(dict[str, Any], {field: value}))


@pytest.mark.parametrize(
    "kind", ["gap", "overlap", "reverse", "duplicate", "nonzero", "empty", "list"]
)
def test_snapshot_contiguous_range_order_and_unique_ids(kind: str) -> None:
    rows: Any = _rows()
    if kind == "gap":
        rows = (rows[0], replace(rows[1], lower_exclusive_quote=D(101)), rows[2])
    elif kind == "overlap":
        rows = (rows[0], replace(rows[1], lower_exclusive_quote=D(99)), rows[2])
    elif kind == "reverse":
        rows = tuple(reversed(rows))
    elif kind == "duplicate":
        rows = (rows[0], replace(rows[1], tier_id=rows[0].tier_id), rows[2])
    elif kind == "nonzero":
        rows = (replace(rows[0], lower_exclusive_quote=D(1)), *rows[1:])
    elif kind == "empty":
        rows = ()
    elif kind == "list":
        rows = list(rows)
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        replace(_inputs()["tiers"], rows=rows)


@pytest.mark.parametrize(
    "target", ["policy", "sizing", "instrument", "account", "reservations", "marks", "tiers", "now"]
)
def test_full_public_boundary_rejects_arbitrary_input(target: str) -> None:
    inputs = _inputs()
    inputs[target] = object()
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        e.assess_linear_tier_v2(**inputs)


def test_all_mark_coverage_even_other_symbol_and_no_netting() -> None:
    inputs = _inputs()
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_MARK_COVERAGE_REQUIRED$"):
        e.assess_linear_tier_v2(**{**inputs, "marks": inputs["marks"][:1]})
    # SELL pending stays a positive gross quantity.
    assert e.assess_linear_tier_v2(**inputs).symbol_gross_upper_quote == D(350)
    overlap = replace(
        inputs["reservations"],
        components=(replace(inputs["reservations"].components[0], component_id="AX"),),
    )
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_EXPOSURE_OVERLAP$"):
        e.assess_linear_tier_v2(**{**inputs, "reservations": overlap})


@pytest.mark.parametrize(
    ("target", "code"),
    [
        ("account", "SCOPE_MISMATCH"),
        ("tiers_key", "SCOPE_MISMATCH"),
        ("tier_revision", "REVISION_MISMATCH"),
        ("policy_hash", "REVISION_MISMATCH"),
        ("pin", "SOURCE_MISMATCH"),
        ("tiers_label", "SOURCE_MISMATCH"),
    ],
)
def test_context_and_source_binding(target: str, code: str) -> None:
    inputs = _inputs()
    if target == "account":
        inputs["account"] = replace(
            inputs["account"], account=replace(inputs["account"].account, account_id="OTHER")
        )
    elif target == "tiers_key":
        inputs["tiers"] = replace(inputs["tiers"], instrument=Y)
    elif target == "tier_revision":
        inputs["tiers"] = replace(inputs["tiers"], instrument_revision="OTHER_V1")
    elif target == "policy_hash":
        inputs["policy"] = replace(inputs["policy"], leverage_max=D(100))
    elif target == "pin":
        pin = replace(inputs["sizing"].envelope.instrument_pin, raw_row_sha256="b" * 64)
        inputs["sizing"] = replace(
            inputs["sizing"], envelope=replace(inputs["sizing"].envelope, instrument_pin=pin)
        )
    elif target == "tiers_label":
        inputs["tiers"] = _forge(
            inputs["tiers"], reference=replace(inputs["tiers"].reference, source_environment="DEMO")
        )
    with pytest.raises(c.RiskV2Rejected, match=f"^RISK_V2_{code}$"):
        e.assess_linear_tier_v2(**inputs)


@pytest.mark.parametrize(
    "target",
    ["tier_server", "tier_receipt", "mark_y", "account", "reservation", "cost", "instrument"],
)
@pytest.mark.parametrize(("offset", "code"), [(11, "STALE_EVIDENCE"), (-1, "FUTURE_EVIDENCE")])
def test_available_source_freshness(target: str, offset: int, code: str) -> None:
    inputs = _inputs()
    moment = NOW - timedelta(seconds=offset)
    if target.startswith("tier"):
        ref = inputs["tiers"].reference
        ref = replace(
            ref,
            server_observed_at=moment,
            received_at=moment if target == "tier_receipt" or offset < 0 else NOW,
        )
        inputs["tiers"] = replace(inputs["tiers"], reference=ref)
    elif target == "mark_y":
        ref = replace(inputs["marks"][1].reference, server_observed_at=moment, received_at=moment)
        inputs["marks"] = (inputs["marks"][0], replace(inputs["marks"][1], reference=ref))
    elif target == "account":
        ref = replace(inputs["account"].reference, server_observed_at=moment, received_at=moment)
        inputs["account"] = replace(inputs["account"], reference=ref)
    elif target == "reservation":
        inputs["reservations"] = replace(inputs["reservations"], observed_at=moment)
    elif target == "cost":
        inputs["sizing"] = _forge(
            inputs["sizing"],
            cost_reference=replace(inputs["sizing"].cost_reference, computed_at=moment),
        )
        if offset < 0:
            # Keep the static sizing relationship valid so current-time validation is tested.
            inputs["sizing"] = _forge(
                inputs["sizing"],
                assessed_at=moment,
                envelope=replace(inputs["sizing"].envelope, valid_until=NOW + timedelta(seconds=3)),
            )
    elif target == "instrument":
        # Full pin/source equality is tested elsewhere; this clock can move now instead.
        inputs["now"] = NOW + timedelta(seconds=offset)
        inputs["sizing"] = replace(
            inputs["sizing"],
            envelope=replace(inputs["sizing"].envelope, valid_until=NOW + timedelta(seconds=30)),
        )
    with pytest.raises(c.RiskV2Rejected, match=f"^RISK_V2_{code}$"):
        e.assess_linear_tier_v2(**inputs)


def test_expiry_causal_order_and_inclusive_freshness_boundary() -> None:
    inputs = _inputs()
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_EXPIRED$"):
        e.assess_linear_tier_v2(**{**inputs, "now": NOW + timedelta(seconds=1)})
    bad = replace(
        inputs["sizing"],
        cost_reference=replace(
            inputs["sizing"].cost_reference, computed_at=NOW - timedelta(microseconds=1)
        ),
    )
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_CAUSAL_ORDER_INVALID$"):
        e.assess_linear_tier_v2(**{**inputs, "sizing": bad})
    ref = replace(inputs["tiers"].reference, server_observed_at=NOW - timedelta(seconds=10))
    e.assess_linear_tier_v2(**{**inputs, "tiers": replace(inputs["tiers"], reference=ref)})


def test_row_count_and_hash_byte_resource_limits() -> None:
    inputs = _inputs()
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_RESOURCE_LIMIT$"):
        replace(inputs["tiers"], rows=_rows() * 342)
    rows = tuple(
        e.LinearRiskTierV2(f"T{i}", D(i), D(i + 1), D(1), D(150), D(".1"), D(".05"), D(0))
        for i in range(1024)
    )
    table = replace(inputs["tiers"], rows=rows)
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_RESOURCE_LIMIT$"):
        e.tier_snapshot_sha256_v2(table)


class _Hostile:
    def __eq__(self, other: object) -> bool:
        raise AssertionError("untrusted equality")

    def __hash__(self) -> int:
        raise AssertionError("untrusted hashing")

    def __repr__(self) -> str:
        raise AssertionError("untrusted representation")


@pytest.mark.parametrize("record", ["row", "snapshot", "bound"])
def test_every_new_field_missing_or_hostile_revalidated_by_consumers(record: str) -> None:
    inputs = _inputs()
    value: Any = {
        "row": _rows()[0],
        "snapshot": inputs["tiers"],
        "bound": e.assess_linear_tier_v2(**inputs),
    }[record]
    for field in fields(value):
        for missing in (True, False):
            bad = _forge(value)
            if missing:
                object.__delattr__(bad, field.name)
            else:
                object.__setattr__(bad, field.name, _Hostile())
            with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
                if record == "row":
                    e.tier_snapshot_sha256_v2(_forge(inputs["tiers"], rows=(bad, *_rows()[1:])))
                elif record == "snapshot":
                    e.tier_snapshot_sha256_v2(bad)
                else:
                    e.tier_bound_sha256_v2(bad)


def test_bound_rejects_nonendpoints_duplicates_wrong_grid_and_wrong_literals() -> None:
    bound = e.assess_linear_tier_v2(**_grid(_inputs(), "1.1", "1.4", ".3"))
    for changes in (
        {"permitted_leverage_min": D("1.1")},
        {"permitted_leverage_max": D("1.4")},
        {"covered_tier_ids": ("z_first", "z_first")},
        {"leverage_grid_rule": "OTHER"},
        {"bound_rule": "OTHER"},
    ):
        with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
            e.tier_bound_sha256_v2(_forge(bound, **changes))


def test_literal_bound_canonical_bytes_and_each_field_bound() -> None:
    bound = e.assess_linear_tier_v2(**_inputs())
    payload = {
        "schema": "RISK_V2_CANONICAL_V1",
        "record_type": "LinearTierBoundV2",
        "fields": {
            "instrument": {"venue": "BYBIT", "product": "LINEAR", "symbol": "XAAUSDT"},
            "tier_observation_id": "TIERS",
            "mark_observation_id": "MARK_X",
            "symbol_gross_upper_quote": "350",
            "covered_tier_ids": ["z_first", "a_second", "m_third"],
            "continuous_leverage_lower": "1",
            "continuous_leverage_upper": "1.5",
            "leverage_step": "0.1",
            "leverage_grid_rule": "LOCAL_ZERO_ORIGIN_LATTICE_V1",
            "permitted_leverage_min": "1",
            "permitted_leverage_max": "1.5",
            "bound_rule": "LINEAR_MARK_GROSS_UPPER_BOUND_V1",
        },
    }
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "ascii"
        )
    ).hexdigest()
    assert e.tier_bound_sha256_v2(bound) == expected
    for changes in (
        {"tier_observation_id": "OTHER"},
        {"mark_observation_id": "OTHER"},
        {"symbol_gross_upper_quote": D("350.0")},
        {"covered_tier_ids": ("m_third", "a_second", "z_first")},
        {"leverage_step": D(".10")},
        {"instrument": Y},
    ):
        assert e.tier_bound_sha256_v2(replace(bound, **changes)) != expected


@pytest.mark.parametrize("rounding", ["ROUND_UP", "ROUND_DOWN", "ROUND_HALF_EVEN"])
def test_hostile_decimal_context_preserves_results_digests_and_context(rounding: str) -> None:
    inputs = _inputs()
    expected_bound = e.assess_linear_tier_v2(**inputs)
    expected = e.tier_snapshot_sha256_v2(inputs["tiers"]), e.tier_bound_sha256_v2(expected_bound)
    with localcontext() as ctx:
        ctx.prec = 1
        ctx.rounding = rounding
        ctx.Emax = 1
        ctx.Emin = -1
        ctx.capitals = 0
        ctx.traps[Inexact] = True
        ctx.traps[Rounded] = True
        before = ctx.copy()
        result = e.assess_linear_tier_v2(**inputs)
        assert result == expected_bound
        assert (
            e.tier_snapshot_sha256_v2(inputs["tiers"]),
            e.tier_bound_sha256_v2(result),
        ) == expected
        assert ctx.flags == before.flags and ctx.traps == before.traps
        assert (ctx.prec, ctx.rounding, ctx.Emin, ctx.Emax, ctx.capitals) == (1, rounding, -1, 1, 0)


def test_derived_gross_beyond_source_digits_is_accepted_and_rehashable() -> None:
    inputs = _inputs()
    tiny = D("1E-1000")
    large = D("1E+1000")
    pending = replace(inputs["reservations"].components[0], base_quantity=tiny)
    inputs["reservations"] = replace(inputs["reservations"], components=(pending,))
    inputs["account"] = replace(
        inputs["account"],
        components=(replace(inputs["account"].components[0], base_quantity=large),),
    )
    inputs["marks"] = (replace(inputs["marks"][0], mark_price=D(1)),)
    inputs["tiers"] = replace(
        inputs["tiers"],
        rows=(
            e.LinearRiskTierV2("ONLY", D(0), D("2E+1000"), D(1), D(100), D(".1"), D(".05"), D(0)),
        ),
    )
    result = e.assess_linear_tier_v2(**inputs)
    assert len(result.symbol_gross_upper_quote.as_tuple().digits) == 2001
    assert len(e.tier_bound_sha256_v2(result)) == 64
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_RESOURCE_LIMIT$"):
        replace(_rows()[0], upper_inclusive_quote=result.symbol_gross_upper_quote)


def test_supplied_empty_coverage_cannot_authenticate_real_omissions() -> None:
    inputs = _inputs()
    inputs["account"] = replace(inputs["account"], components=())
    inputs["reservations"] = replace(inputs["reservations"], components=())
    inputs["marks"] = inputs["marks"][:1]
    result = e.assess_linear_tier_v2(**inputs)
    assert result.symbol_gross_upper_quote == D(140)
    assert not hasattr(result, "qualified") and not hasattr(result, "submission_ready")


def test_every_covered_row_minimum_participates_and_marks_recompute_u() -> None:
    inputs = _inputs()
    rows = _rows()
    inputs["tiers"] = replace(
        inputs["tiers"], rows=(replace(rows[0], min_leverage=D("1.3")), *rows[1:])
    )
    assert e.assess_linear_tier_v2(**inputs).permitted_leverage_min == D("1.3")
    inputs["marks"] = (replace(inputs["marks"][0], mark_price=D(141)), inputs["marks"][1])
    assert e.assess_linear_tier_v2(**inputs).symbol_gross_upper_quote == D("352.5")


def test_zero_quote_age_exact_tick_and_audit_recheck_do_not_change_economic_bound() -> None:
    inputs = _policy(_inputs(), max_quote_age_seconds=D(0))
    inputs["marks"] = tuple(
        replace(mark, reference=replace(mark.reference, server_observed_at=NOW))
        for mark in inputs["marks"]
    )
    result = e.assess_linear_tier_v2(**inputs)
    assert result.symbol_gross_upper_quote == D(350)
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_STALE_EVIDENCE$"):
        e.assess_linear_tier_v2(**{**inputs, "now": NOW + timedelta(microseconds=1)})
    inputs = _inputs()
    assert e.tier_bound_sha256_v2(e.assess_linear_tier_v2(**inputs)) == e.tier_bound_sha256_v2(
        e.assess_linear_tier_v2(**{**inputs, "now": NOW + timedelta(microseconds=1)})
    )


def test_source_labels_are_not_actual_mapping_and_other_budgets_are_later_gates() -> None:
    inputs = _inputs()
    inputs["tiers"] = replace(
        inputs["tiers"],
        reference=replace(inputs["tiers"].reference, source_environment="PUBLIC_MAINNET"),
    )
    inputs = _policy(
        inputs,
        caps=replace(
            inputs["policy"].caps,
            max_order_admission_notional=D(1),
            max_account_admission_notional=D(1),
        ),
    )
    # Tier arithmetic cannot authenticate this mapping or substitute full admission.
    assert e.assess_linear_tier_v2(**inputs).symbol_gross_upper_quote == D(350)


def test_exact_classes_and_datetime_boundary_precede_hooks() -> None:
    class SnapshotSubclass(e.LinearTierSnapshotV2):
        pass

    value = _inputs()["tiers"]
    bad = object.__new__(SnapshotSubclass)
    for field in fields(value):
        object.__setattr__(bad, field.name, getattr(value, field.name))
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        e.tier_snapshot_sha256_v2(bad)
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        e.assess_linear_tier_v2(**{**_inputs(), "now": NOW.replace(tzinfo=None)})
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        e.assess_linear_tier_v2(**{**_inputs(), "tiers": _forge(value, instrument=_Hostile())})


def test_snapshot_canonical_literal_and_full_table_source_hashing() -> None:
    snapshot = _inputs()["tiers"]
    # One-row literal keeps canonical expectations independent of payload helpers.
    snapshot = replace(snapshot, instrument_revision="INSTRUMENT_V1", rows=_rows()[:1])
    payload = {
        "schema": "RISK_V2_CANONICAL_V1",
        "record_type": "LinearTierSnapshotV2",
        "fields": {
            "instrument": {"venue": "BYBIT", "product": "LINEAR", "symbol": "XAAUSDT"},
            "reference": {
                "observation_id": "TIERS",
                "source_id": "FIXTURE_SOURCE",
                "source_environment": "FIXTURE",
                "source_rule_revision": "FIXTURE_SOURCE_V1",
                "server_observed_at": "2026-11-02T23:59:59.000000+00:00",
                "received_at": "2026-11-03T00:00:00.000000+00:00",
                "payload_sha256": "a" * 64,
            },
            "instrument_revision": "INSTRUMENT_V1",
            "rows": [
                {
                    "tier_id": "z_first",
                    "lower_exclusive_quote": "0",
                    "upper_inclusive_quote": "100",
                    "min_leverage": "1",
                    "max_leverage": "150",
                    "initial_margin_rate": "0.1",
                    "maintenance_margin_rate": "0.05",
                    "maintenance_margin_deduction": "7",
                }
            ],
        },
    }
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "ascii"
        )
    ).hexdigest()
    assert e.tier_snapshot_sha256_v2(snapshot) == expected
    full = _inputs()["tiers"]
    original = e.tier_snapshot_sha256_v2(full)
    for field, value in {
        "tier_id": "OTHER",
        "lower_exclusive_quote": D("200.0"),
        "upper_inclusive_quote": D(501),
        "min_leverage": D("1.1"),
        "max_leverage": D(99),
        "initial_margin_rate": D(".3"),
        "maintenance_margin_rate": D(".11"),
        "maintenance_margin_deduction": D(10),
    }.items():
        changed = replace(
            full,
            rows=(*full.rows[:2], replace(full.rows[2], **cast(dict[str, Any], {field: value}))),
        )
        assert e.tier_snapshot_sha256_v2(changed) != original
    for changed in (
        replace(full, instrument=Y),
        replace(full, instrument_revision="OTHER"),
        replace(full, reference=replace(full.reference, source_id="OTHER")),
        replace(
            full, reference=replace(full.reference, received_at=NOW + timedelta(microseconds=1))
        ),
    ):
        assert e.tier_snapshot_sha256_v2(changed) != original


def test_derived_bound_resource_overflow_remains_closed() -> None:
    result = e.assess_linear_tier_v2(**_inputs())
    with pytest.raises(c.RiskV2Rejected, match="^RISK_V2_RESOURCE_LIMIT$"):
        e.tier_bound_sha256_v2(_forge(result, symbol_gross_upper_quote=D("1E+65537")))
