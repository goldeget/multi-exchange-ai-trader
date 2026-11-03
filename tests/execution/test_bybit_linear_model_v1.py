"""Independent synthetic economic literals; no transport or issuer authority."""

import hashlib
import json
import sys
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from decimal import getcontext, localcontext
from fractions import Fraction as F
from typing import Any, cast

import pytest

from multi_exchange_trader.account.instrument_envelope import HttpsOrigin
from multi_exchange_trader.account.risk_limit_envelope import BybitRiskLimitEnvelopeReadV1
from multi_exchange_trader.execution import bybit_linear_model_inputs_v1 as a
from multi_exchange_trader.execution import bybit_linear_model_v1 as b
from multi_exchange_trader.execution.bybit_linear_model_inputs_v1 import (
    BybitLinearTierTableV1,
    bybit_instrument_pin_sha256_v1,
    bybit_tier_table_sha256_v1,
    parse_bybit_risk_limit_table_v1,
)
from multi_exchange_trader.execution.modes import StrategyReference
from multi_exchange_trader.execution.product_scope import ProductScope
from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.execution.risk_v2_contracts import (
    FixedLimitEnvelopeV2,
    InstrumentPinV2,
    RiskObservationRefV2,
    RiskScopeV2,
    _canonical_sha256_v2,
)
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected
from multi_exchange_trader.market_data.instrument_definition import (
    LINEAR_SOURCE_RULE_REVISION,
    PARSER_REVISION,
)
from multi_exchange_trader.trading_product import InstrumentKey


def table(
    rows: tuple[tuple[str, str, str, str, str], ...] = (("1000000", "0.01", "0.005", "100", "0"),),
) -> BybitLinearTierTableV1:
    text = []
    for i, (upper, imr, mmr, leverage, deduction) in enumerate(rows, 1):
        text.append(
            '{"id":'
            + str(i)
            + ',"symbol":"BTCUSDT","riskLimitValue":"'
            + upper
            + '","initialMargin":'
            + imr
            + ',"maintenanceMargin":'
            + mmr
            + ',"maxLeverage":"'
            + leverage
            + '","mmDeduction":"'
            + deduction
            + '","isLowestRisk":'
            + str(int(i == 1))
            + "}"
        )
    raw = (
        '{"retCode":0,"result":{"category":"linear","list":['
        + ",".join(text)
        + '],"nextPageCursor":""},"time":1}'
    ).encode()
    stamp = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=1)
    key = InstrumentKey("BYBIT", "LINEAR", "BTCUSDT")
    pin = InstrumentPinV2(
        key,
        "BTCUSDT-LINEAR.BYBIT",
        "catalog-v1",
        "a" * 64,
        PARSER_REVISION,
        LINEAR_SOURCE_RULE_REVISION,
        "instruments",
        "FIXTURE",
        stamp,
        stamp,
        "b" * 64,
    )
    origin = HttpsOrigin("https", "api.bybit.com", 443)
    envelope = BybitRiskLimitEnvelopeReadV1(
        key,
        "GET",
        "/v5/market/risk-limit",
        (("category", "linear"), ("symbol", "BTCUSDT")),
        origin,
        origin,
        200,
        stamp,
        stamp,
        0.0,
        1.0,
        stamp,
        raw,
        hashlib.sha256(raw).hexdigest(),
        1,
        0,
        1,
        1,
        "page1",
        "source1",
        "gen1",
        "FIXTURE",
    )
    return parse_bybit_risk_limit_table_v1(
        (envelope,), instrument_pin=pin, observation_id="table1"
    ).table


def selector_point(
    *,
    side: Any = "BUY",
    quantity: str = "1",
    price: str = "1000",
    mark: str = "1010",
    stop: str | None = None,
    cost: str = "20",
    capacity: str = "225",
    buffer: str = "0.01",
    admission: str = "1010",
    lower: str = "1",
    upper: str = "5",
    step: str = "1",
    fee: str = "0.00055",
    rows: tuple[tuple[str, str, str, str, str], ...] = (("3000", "0.1", "0.005", "5", "0"),),
    covered: tuple[str, ...] = ("1",),
) -> Any:
    """Complete supplied synthetic graph; no account mode/readback claim is made."""
    full = table(rows)
    stamp = full.pages[0].received_at
    scope = ProtectionScope("synthetic-account", "DEMO")
    public = full.pages[0]
    mark_source = replace(
        public, observation_id="mark1", path="/v5/market/tickers", raw_body_sha256="c" * 64
    )
    position_source = replace(
        public, observation_id="position1", account_scope=scope, path="/v5/position/list"
    )
    fee_source = replace(position_source, observation_id="fee1", path="/v5/account/fee-rate")
    behaviour_source = replace(
        position_source, observation_id="behaviour1", path="/v5/account/info", query=()
    )

    def field(source: Any, pointer: str, spelling: str) -> Any:
        return a.BybitDecimalFieldV1(
            D(spelling), a.BybitModelFieldRefV1(source, pointer, "STRING", spelling)
        )

    mark_field = field(mark_source, "/result/list/0/markPrice", mark)
    profile = a.BybitSelectedEntryProfileV1(
        "BYBIT_USDT_LINEAR_ISOLATED_ONE_WAY_NEW_ENTRY_V1",
        "BTCUSDT",
        0,
        field(position_source, "/result/list/0/size", "0"),
        (),
        (),
        (),
        a.BybitModelFieldRefV1(position_source, "/result/list/0/autoAddMargin", "INTEGER", "0"),
        D(0),
        "NEW_SELECTED_FLAT_NO_ADD_V1",
        (position_source,),
    )
    document = a.BybitFeeDocumentRefV1(
        "https://www.bybit.com/en/help-center/article/Trading-Fee-Structure",
        "synthetic-v1",
        stamp,
        "d" * 64,
        "synthetic-non-vip-usdt-linear-taker",
        fee,
        "FRACTION",
        "e" * 64,
        "synthetic-applicability-v1",
    )
    fees = a.BybitLinearFeeInputsV1(
        field(fee_source, "/result/list/0/takerFeeRate", fee),
        field(fee_source, "/result/list/0/makerFeeRate", "0"),
        a.BybitNonVipFeeBasisV1(D(fee), document),
        "USDT",
        a.BybitTradeBehaviourV1(behaviour_source, True, False, False),
        "synthetic-applicability-v1",
    )
    expiry = stamp + timedelta(seconds=60)
    envelope = FixedLimitEnvelopeV2(
        2,
        RiskScopeV2(
            ProductScope(scope, "LINEAR"),
            full.instrument_pin.key,
            StrategyReference("strategy1", "rev1"),
        ),
        full.instrument_pin,
        "policy-v1",
        "f" * 64,
        side,
        D(quantity),
        D(price),
        "LIMIT",
        "IOC",
        "ENTRY",
        False,
        "quote1",
        expiry,
    )
    return a.BybitLinearPointInputV1(
        envelope,
        D(stop if stop is not None else ("950" if side == "BUY" else "1050")),
        mark_field,
        RiskObservationRefV2(
            "mark1", "source1", "FIXTURE", "synthetic-mark-v1", stamp, stamp, "c" * 64
        ),
        fees,
        full,
        profile,
        D(cost),
        D(capacity),
        D(buffer),
        D(admission),
        covered,
        D(lower),
        D(upper),
        D(step),
        D(10),
        D(10),
        expiry,
        "BYBIT_LINEAR_ISOLATED_POINT_MODEL_V1",
    )


@pytest.mark.parametrize("side,expected", [("BUY", F(160000, 199)), ("SELL", F(240000, 201))])
def test_f5_complete_selector(side: str, expected: F) -> None:
    point = selector_point(side=side)
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert type(result) is b.BybitLeverageSearchV1
    assert result.selected.leverage == D(5)
    assert result.selected.reserve.required_reservation_quote == D(222)
    assert isinstance(result.selected.liquidation.outcome, b.BybitPositiveRootPointV1)
    assert result.selected.liquidation.outcome.exact_price == expected
    assert [candidate.failure_codes for candidate in result.evaluated_prefix] == [
        ("CAPACITY",)
    ] * 4 + [()]
    assert result.evaluated_prefix[3].reserve.required_reservation_quote == D("272.5")


def test_rounded_reserve_changes_minimum() -> None:
    point = selector_point(
        quantity="1.000000001",
        price="1",
        mark="1",
        stop="0.9",
        cost="0",
        capacity="1.000000005",
        buffer="0",
        admission="1.000000001",
        upper="2",
        fee="0",
        rows=(("10", "0.1", "0.005", "2", "0"),),
    )
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert type(result) is b.BybitLeverageSearchV1
    assert result.selected.leverage == D(2)
    assert result.evaluated_prefix[0].failure_codes == ("CAPACITY",)
    assert result.evaluated_prefix[0].reserve.initial_margin_quote == D("1.00000001")
    assert result.selected.reserve.required_reservation_quote == D("0.50000001")
    assert isinstance(result.selected.liquidation.outcome, b.BybitPositiveRootPointV1)
    assert result.selected.liquidation.outcome.exact_price == F(100, 199)


def test_l1_no_root_does_not_force_higher_leverage() -> None:
    point = selector_point(capacity="2000")
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert type(result) is b.BybitLeverageSearchV1
    assert result.selected.leverage == D(1)
    assert result.selected.liquidation_buffer_result == "MODEL_NO_POSITIVE_TRIGGER"
    assert len(result.evaluated_prefix) == 1


def test_capacity_failure_cannot_hide_unknown_lp() -> None:
    point = selector_point(
        side="SELL",
        price="1000",
        mark="1000",
        admission="1000",
        capacity="0",
        fee="0",
        upper="2",
        rows=(("1500", "0.1", "0.005", "2", "0"),),
    )
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert type(result) is b.BybitModelRejectedV1
    assert result.reason == "ROOT_OUTSIDE_CAPTURED_TABLE"
    assert result.candidate_leverage == D(1)
    assert result.evaluated_prefix == ()


def test_imr_independent_from_max_leverage() -> None:
    point = selector_point(capacity="0", upper="3", rows=(("3000", "0.4", "0.005", "3", "0"),))
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert type(result) is b.BybitModelRejectedV1
    assert result.public_code == "RISK_V2_LEVERAGE_UNSATISFIABLE"
    assert result.evaluated_prefix[-1].failure_codes == ("IMR", "CAPACITY")


def test_fatal_later_lp_retains_only_complete_earlier_prefix() -> None:
    point = selector_point(
        mark="100",
        admission="100",
        capacity="0",
        upper="2",
        fee="0",
        rows=(("150", "0.1", "0.005", "2", "0"),),
    )
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert type(result) is b.BybitModelRejectedV1
    assert result.reason == "ROOT_OUTSIDE_CAPTURED_TABLE" and result.candidate_leverage == D(2)
    assert [(c.grid_index, c.leverage, c.failure_codes) for c in result.evaluated_prefix] == [
        (0, D(1), ("CAPACITY",))
    ]


@pytest.mark.parametrize(
    "capacity,codes", [("222", ()), ("221.999999999", ("CAPACITY",)), ("222.000000001", ())]
)
def test_selector_capacity_uses_rounded_exact_boundary(
    capacity: str, codes: tuple[str, ...]
) -> None:
    point = selector_point(capacity=capacity, lower="5", upper="5")
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert result.evaluated_prefix[-1].failure_codes == codes


@pytest.mark.parametrize(
    "stop,buffer,code",
    [
        ("60", "0.1", ()),
        ("50", "0", ("LIQUIDATION_BUFFER",)),
        ("60", "0.100000001", ("LIQUIDATION_BUFFER",)),
    ],
)
def test_selector_buffer_requires_positive_gap_and_inclusive_fraction(
    stop: str, buffer: str, code: tuple[str, ...]
) -> None:
    # LONG L2 with MMR .01: LP=(100-50)/.99=5000/99; choose
    # P99 instead so exact LP50, gap10 and buffer*P9.9 for stop60.
    point = selector_point(
        price="99",
        mark="99",
        stop=stop,
        admission="99",
        capacity="100",
        cost="0",
        fee="0",
        lower="2",
        upper="2",
        buffer=buffer,
        rows=(("200", "0.1", "0.01", "2", "0"),),
    )
    if stop == "60":
        # Exact equality uses gap=9.9, not a rounded display of a repeating root.
        point = replace(point, stop_price=D("59.9"))
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert isinstance(result.evaluated_prefix[-1].liquidation.outcome, b.BybitPositiveRootPointV1)
    assert result.evaluated_prefix[-1].liquidation.outcome.exact_price == F(50)
    assert result.evaluated_prefix[-1].failure_codes == code


def test_selector_failure_reasons_are_complete_fixed_order() -> None:
    point = selector_point(
        capacity="0", lower="3", upper="3", stop="600", rows=(("3000", "0.4", "0.005", "3", "0"),)
    )
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert result.evaluated_prefix[0].failure_codes == ("IMR", "CAPACITY", "LIQUIDATION_BUFFER")


def test_selector_zero_origin_fractional_grid() -> None:
    point = selector_point(lower="1.1", upper="2.1", step="0.5", capacity="2000")
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert type(result) is b.BybitLeverageSearchV1
    assert result.selected.leverage == D("1.5") and result.selected.grid_index == 0
    assert result.selected.margin.quantity == F(1) and result.selected.margin.entry_limit == F(1000)
    assert point.stop_price == D(950) and point.planned_cost_allowance_quote == D(20)


@pytest.mark.parametrize(
    "changes",
    [
        {"admission_upper_quote": D(1000)},
        {"admission_covered_tier_ids": ("2",)},
        {"continuous_leverage_upper": D(6)},
        {"leverage_step": D(10)},
    ],
)
def test_selector_supplied_bound_mismatches_are_upfront(changes: dict[str, Any]) -> None:
    point = replace(selector_point(), **changes)
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert type(result) is b.BybitModelRejectedV1
    assert result.public_code == "RISK_V2_INPUT_INVALID" and result.reason == "INPUT_MISMATCH"
    assert result.candidate_leverage is None and result.evaluated_prefix == ()


def test_selector_admission_boundary_and_full_table_coverage() -> None:
    full_rows = (("1010", "0.1", "0.005", "5", "0"), ("3000", "0.2", "0.01", "4", "5.05"))
    point = selector_point(side="SELL", rows=full_rows)
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert type(result) is b.BybitLeverageSearchV1
    assert point.admission_covered_tier_ids == ("1",)
    assert result.selected.liquidation.consumed_tier_ids == ("1", "2")
    assert isinstance(result.selected.liquidation.outcome, b.BybitPositiveRootPointV1)
    assert result.selected.liquidation.outcome.candidate_tier_id == "2"
    missing = selector_point(
        rows=(("1000", "0.1", "0.005", "5", "0"), ("3000", "0.2", "0.01", "4", "5")), upper="4"
    )
    rejected: Any = b.select_smallest_bybit_leverage_v1(
        missing, now=missing.table.pages[0].received_at
    )
    assert rejected.reason == "INPUT_MISMATCH" and rejected.evaluated_prefix == ()
    outside = selector_point(rows=(("1000", "0.1", "0.005", "5", "0"),))
    assert (
        cast(
            Any,
            b.select_smallest_bybit_leverage_v1(outside, now=outside.table.pages[0].received_at),
        ).reason
        == "INPUT_MISMATCH"
    )


@pytest.mark.parametrize(
    "field",
    [
        "selected_open_order_ids",
        "selected_conditional_order_ids",
        "local_unresolved_entry_ids",
        "prospective_extra_margin_usdt",
    ],
)
def test_selector_rejects_selected_activity_and_added_margin(field: str) -> None:
    point = selector_point()
    profile = replace(
        point.profile, **{field: D(1) if field == "prospective_extra_margin_usdt" else ("active1",)}
    )
    point = replace(point, profile=profile)
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert result.reason == "PROFILE_UNSUPPORTED" and result.evaluated_prefix == ()


@pytest.mark.parametrize(
    "change",
    [
        {"lpa_perp": False},
        {"futures_mnt_fee_deduction": True},
        {"delta_enable": None},
        {"delta_enable": True},
    ],
)
def test_selector_requires_explicit_supported_behaviour(change: dict[str, Any]) -> None:
    point = selector_point()
    point = replace(point, fee=replace(point.fee, behaviour=replace(point.fee.behaviour, **change)))
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert result.reason == "PROFILE_UNSUPPORTED" and result.evaluated_prefix == ()


def test_selector_rejects_nonflat_selected_quantity_and_keeps_all_profile_sources() -> None:
    point = selector_point(capacity="2000")
    quantity = point.profile.selected_position_quantity
    nonflat = replace(
        quantity, value=D(1), reference=replace(quantity.reference, wire_spelling="1")
    )
    rejected: Any = b.select_smallest_bybit_leverage_v1(
        replace(point, profile=replace(point.profile, selected_position_quantity=nonflat)),
        now=point.table.pages[0].received_at,
    )
    assert rejected.reason == "PROFILE_UNSUPPORTED"
    # Additional supplied account evidence is retained and hashed. A point has
    # no whole-account held-position/order carrier; D must bind actual held Y.
    extra = replace(
        point.profile.source_records[0],
        observation_id="wallet1",
        path="/v5/account/wallet-balance",
        query=(),
    )
    augmented = replace(
        point, profile=replace(point.profile, source_records=(*point.profile.source_records, extra))
    )
    selected: Any = b.select_smallest_bybit_leverage_v1(
        augmented, now=point.table.pages[0].received_at
    )
    assert type(selected) is b.BybitLeverageSearchV1 and len(augmented.profile.source_records) == 2
    assert selected.point_input_sha256 != a.bybit_point_input_sha256_v1(point)


def test_selector_consumes_explicit_nonvip_field_basis_and_rejects_out_of_range_rate() -> None:
    point = selector_point(capacity="2000")
    observed = point.fee.account_taker_rate.reference
    as_field = replace(
        point,
        fee=replace(
            point.fee,
            closing_order_reserve_nonvip_rate=a.BybitNonVipFeeBasisV1(D("0.00055"), observed),
        ),
    )
    selected: Any = b.select_smallest_bybit_leverage_v1(
        as_field, now=point.table.pages[0].received_at
    )
    assert type(selected) is b.BybitLeverageSearchV1
    field = replace(
        point.fee.account_taker_rate, value=D(1), reference=replace(observed, wire_spelling="1")
    )
    invalid = replace(point, fee=replace(point.fee, account_taker_rate=field))
    rejected: Any = b.select_smallest_bybit_leverage_v1(
        invalid, now=point.table.pages[0].received_at
    )
    assert rejected.reason == "INPUT_MISMATCH" and rejected.public_code == "RISK_V2_INPUT_INVALID"


@pytest.mark.parametrize("kind,spelling", [("STRING", "0"), ("INTEGER", "1"), ("INTEGER", "-0")])
def test_selector_amr_is_observed_integer_zero(kind: str, spelling: str) -> None:
    point = selector_point()
    point = replace(
        point,
        profile=replace(
            point.profile,
            auto_add_margin=replace(
                point.profile.auto_add_margin, wire_kind=kind, wire_spelling=spelling
            ),
        ),
    )
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert result.reason == "PROFILE_UNSUPPORTED" and result.evaluated_prefix == ()


@pytest.mark.parametrize(
    "kind", ["source", "scope", "generation", "symbol", "markref", "membership", "applicability"]
)
def test_selector_rejects_borrowed_source_links(kind: str) -> None:
    point = selector_point()
    if kind in ("source", "scope", "generation", "symbol"):
        original = point.fee.account_taker_rate.reference.source
        source_changes: dict[str, dict[str, Any]] = {
            "source": {"account_scope": None, "path": "/v5/market/tickers"},
            "scope": {"account_scope": ProtectionScope("other-account", "DEMO")},
            "generation": {"source_generation": "gen2"},
            "symbol": {"query": (("category", "linear"), ("symbol", "ETHUSDT"))},
        }
        changes = source_changes[kind]
        field = replace(
            point.fee.account_taker_rate,
            reference=replace(
                point.fee.account_taker_rate.reference, source=replace(original, **changes)
            ),
        )
        point = replace(point, fee=replace(point.fee, account_taker_rate=field))
    elif kind == "markref":
        point = replace(
            point,
            selected_mark_reference=replace(
                point.selected_mark_reference, source_id="other-source"
            ),
        )
    elif kind == "membership":
        original = point.profile.auto_add_margin.source
        point = replace(
            point,
            profile=replace(
                point.profile, source_records=(replace(original, observation_id="other1"),)
            ),
        )
    else:
        point = replace(
            point, fee=replace(point.fee, nonvip_applicability_revision="other-revision")
        )
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert result.reason == "INPUT_MISMATCH" and result.evaluated_prefix == ()


@pytest.mark.parametrize(
    "delta,expected",
    [
        (0, None),
        (10_000_000, None),
        (10_000_001, "RISK_V2_STALE_EVIDENCE"),
        (-1, "RISK_V2_FUTURE_EVIDENCE"),
    ],
)
def test_selector_inclusive_source_age_and_future(delta: int, expected: str | None) -> None:
    point = selector_point(capacity="2000")
    now = point.table.pages[0].received_at + timedelta(microseconds=delta)
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=now)
    if expected:
        assert result.public_code == expected and result.evaluated_prefix == ()
    else:
        assert type(result) is b.BybitLeverageSearchV1


def test_selector_quote_age_expiry_and_individual_source_time() -> None:
    point = selector_point(capacity="2000")
    stamp = point.table.pages[0].received_at
    quote = replace(point, max_quote_age_seconds=D(0))
    assert (
        cast(
            Any, b.select_smallest_bybit_leverage_v1(quote, now=stamp + timedelta(microseconds=1))
        ).public_code
        == "RISK_V2_STALE_EVIDENCE"
    )
    longer = replace(point, max_evidence_age_seconds=D(100), max_quote_age_seconds=D(100))
    assert (
        cast(Any, b.select_smallest_bybit_leverage_v1(longer, now=longer.valid_until)).public_code
        == "RISK_V2_EXPIRED"
    )
    assert (
        cast(
            Any,
            b.select_smallest_bybit_leverage_v1(
                replace(longer, valid_until=longer.valid_until + timedelta(microseconds=1)),
                now=stamp,
            ),
        ).public_code
        == "RISK_V2_CAUSAL_ORDER_INVALID"
    )
    # Aggregate reference max does not rescue a separately supplied old request.
    source = replace(
        point.profile.source_records[0], request_started_at=stamp - timedelta(seconds=11)
    )
    profile = replace(
        point.profile,
        source_records=(source,),
        auto_add_margin=replace(point.profile.auto_add_margin, source=source),
        selected_position_quantity=replace(
            point.profile.selected_position_quantity,
            reference=replace(point.profile.selected_position_quantity.reference, source=source),
        ),
    )
    assert (
        cast(
            Any, b.select_smallest_bybit_leverage_v1(replace(point, profile=profile), now=stamp)
        ).public_code
        == "RISK_V2_STALE_EVIDENCE"
    )


@pytest.mark.parametrize(
    "changed", ["quantity", "entry_price", "leverage", "extra_margin", "taker_rate", "table"]
)
def test_selector_replays_actual_five_lp_inputs_and_full_table(
    monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    point = selector_point(capacity="2000")
    real = b.solve_bybit_liquidation_v1

    def foreign(**kwargs: Any) -> Any:
        # A real, complete foreign calculation is structurally hash-valid; only
        # replay using the actual point originals can reject its preimage.
        kwargs[changed] = {
            "quantity": D(2),
            "entry_price": D(1001),
            "leverage": D(2),
            "extra_margin": D(1),
            "taker_rate": D("0.001"),
            "table": table((("3000", "0.1", "0.0050", "5", "0"),)),
        }[changed]
        return real(**kwargs)

    monkeypatch.setattr(b, "solve_bybit_liquidation_v1", foreign)
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert result.public_code == "RISK_V2_SOURCE_MISMATCH" and result.evaluated_prefix == ()


def test_search_digest_has_independent_recipe_and_golden() -> None:
    point = selector_point()
    search: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    expected = canonical(
        "BybitLeverageSearchV1",
        {
            "point_input_sha256": a.bybit_point_input_sha256_v1(point),
            "selected": b.bybit_candidate_evaluation_sha256_v1(search.selected),
            "evaluated_prefix": [
                b.bybit_candidate_evaluation_sha256_v1(c) for c in search.evaluated_prefix
            ],
            "selection_rule": "SMALLEST_FEASIBLE_PERMITTED_GRID_V1",
            "model_rule_revision": "BYBIT_LINEAR_ISOLATED_POINT_MODEL_V1",
        },
    )
    assert b.bybit_leverage_search_sha256_v1(search) == expected
    raw = b'{"fields":{"evaluated_prefix":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],"model_rule_revision":"BYBIT_LINEAR_ISOLATED_POINT_MODEL_V1","point_input_sha256":"0000000000000000000000000000000000000000000000000000000000000000","selected":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","selection_rule":"SMALLEST_FEASIBLE_PERMITTED_GRID_V1"},"record_type":"BybitLeverageSearchV1","schema":"RISK_V2_CANONICAL_V1"}'
    assert (
        len(raw) == 452
        and hashlib.sha256(raw).hexdigest()
        == "a8505f6e635d718c7632e1dd28bc6cdd1545838e88f6a78483a63838107886e8"
    )
    assert (
        canonical("BybitLeverageSearchV1", json.loads(raw)["fields"])
        == hashlib.sha256(raw).hexdigest()
    )
    for change in (
        {"evaluated_prefix": ()},
        {"evaluated_prefix": search.evaluated_prefix[1:]},
        {"evaluated_prefix": tuple(reversed(search.evaluated_prefix))},
        {"selected": search.evaluated_prefix[0]},
        {"point_input_sha256": "x" * 64},
    ):
        with pytest.raises(RiskV2Rejected):
            b.bybit_leverage_search_sha256_v1(forged(search, **change))


def test_selector_large_grid_may_succeed_at_first_and_derived_quantity_keeps_role() -> None:
    point = selector_point(
        quantity="1" * 300,
        price="1",
        mark="1",
        stop="0.9",
        admission="1" * 300,
        capacity="1E+500",
        cost="0",
        fee="0",
        upper="5000",
        rows=(("1E+500", "0.0001", "0.00001", "5000", "0"),),
    )
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert type(result) is b.BybitLeverageSearchV1 and len(result.evaluated_prefix) == 1
    assert result.selected.leverage == D(1) and result.selected.margin.quantity == F(int("1" * 300))


def test_selector_ambient_context_and_staged_authority_limits() -> None:
    point = selector_point(capacity="2000")
    digit_limit = sys.get_int_max_str_digits()
    with localcontext() as context:
        context.prec = 1
        context.rounding = "ROUND_FLOOR"
        context.capitals = 0
        for signal in context.traps:
            context.traps[signal] = True
        before = (
            context.prec,
            context.rounding,
            context.capitals,
            context.flags.copy(),
            context.traps.copy(),
        )
        first: Any = b.select_smallest_bybit_leverage_v1(
            point, now=point.table.pages[0].received_at
        )
        second: Any = b.select_smallest_bybit_leverage_v1(
            point, now=point.table.pages[0].received_at + timedelta(seconds=1)
        )
        assert b.bybit_leverage_search_sha256_v1(first) == b.bybit_leverage_search_sha256_v1(second)
        assert before == (
            context.prec,
            context.rounding,
            context.capitals,
            context.flags.copy(),
            context.traps.copy(),
        )
    assert sys.get_int_max_str_digits() == digit_limit
    # No UTA status/applied margin mode/readback or whole-account held Y field
    # exists in PointInput. A successful synthetic local search supplies none.
    assert {f.name for f in fields(type(point))}.isdisjoint(
        {"account_mode", "flat_leverage", "held_positions", "held_orders"}
    )
    assert point.profile.source_records == (
        point.profile.selected_position_quantity.reference.source,
    )


def test_selector_budget_stops_only_when_next_candidate_remains() -> None:
    point = selector_point(
        price="1",
        mark="1",
        stop="0.9999",
        admission="1",
        capacity="0",
        cost="0",
        fee="0",
        buffer="0",
        upper="4097",
        rows=(("10", "0.0001", "0.00001", "5000", "0"),),
    )
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert result.reason == "RESOURCE_LIMIT" and result.public_code == "RISK_V2_RESOURCE_LIMIT"
    assert result.candidate_leverage == D(4097) and len(result.evaluated_prefix) == 4096
    assert result.evaluated_prefix[0].leverage == D(1) and result.evaluated_prefix[
        -1
    ].leverage == D(4096)
    assert all(
        c.grid_index == i and c.failure_codes == ("CAPACITY",)
        for i, c in enumerate(result.evaluated_prefix)
    )


def test_selector_final_canonical_bound_keeps_complete_failure_prefix() -> None:
    point = selector_point(
        price="1",
        mark="1",
        stop="0.9999",
        admission="1",
        capacity="0.00091",
        cost="0",
        fee="0",
        buffer="0",
        upper="1200",
        rows=(("10", "0.0001", "0.00001", "1200", "0"),),
    )
    # ceil_q(1/1098)=.00091075 > capacity, ceil_q(1/1099)=.00090992
    # fits. The full unpruned search hash exceeds the independent 64 KiB cap.
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert result.reason == "RESOURCE_LIMIT" and result.candidate_leverage == D(1099)
    assert len(result.evaluated_prefix) == 1098 and result.evaluated_prefix[-1].leverage == D(1098)
    assert all(c.failure_codes == ("CAPACITY",) for c in result.evaluated_prefix)


@pytest.mark.parametrize(
    "code", ["RISK_V2_RESOURCE_LIMIT", "RISK_V2_SOURCE_MISMATCH", "RISK_V2_FUTURE_EVIDENCE"]
)
def test_selector_fatal_scalar_failure_preserves_prior_complete_prefix(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    point = selector_point()
    real = b.calculate_bybit_margin_point_v1

    def fail_second(**kwargs: Any) -> Any:
        if kwargs["leverage"] == D(2):
            raise RiskV2Rejected(code)
        return real(**kwargs)

    monkeypatch.setattr(b, "calculate_bybit_margin_point_v1", fail_second)
    result: Any = b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert result.public_code == code and result.candidate_leverage == D(2)
    assert len(result.evaluated_prefix) == 1 and result.evaluated_prefix[0].failure_codes == (
        "CAPACITY",
    )


def test_selector_unlisted_and_programmer_errors_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    point = selector_point()

    def unlisted(*args: Any, **kwargs: Any) -> Any:
        raise RiskV2Rejected("RISK_V2_TRADE_LOSS_LIMIT")

    monkeypatch.setattr(b, "calculate_bybit_margin_point_v1", unlisted)
    with pytest.raises(RiskV2Rejected, match="TRADE_LOSS_LIMIT"):
        b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)

    def fault(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("synthetic-fault")

    monkeypatch.setattr(b, "calculate_bybit_margin_point_v1", fault)
    with pytest.raises(RuntimeError, match="synthetic-fault"):
        b.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)


def margin(side: str = "LONG", **updates: Any) -> Any:
    args: dict[str, Any] = {
        "side": side,
        "quantity": D(".5"),
        "entry_limit": D(50000),
        "mark": D(50500),
        "leverage": D(10),
        "taker_rate": D(".00055"),
        "order_close_rate": D(".00055"),
    }
    args.update(updates)
    return b.calculate_bybit_margin_point_v1(**args)


def lp(side: str = "LONG", **updates: Any) -> Any:
    args: dict[str, Any] = {
        "side": side,
        "quantity": D(".5"),
        "entry_price": D(50000),
        "leverage": D(10),
        "extra_margin": D(0),
        "taker_rate": D(".00055"),
        "table": table(),
    }
    args.update(updates)
    return b.solve_bybit_liquidation_v1(**args)


@pytest.mark.parametrize(
    "side,close,cost,reserve",
    [
        ("LONG", F(99, 8), F(20209, 8), F(20409, 8)),
        ("SHORT", F(121, 8), F(20231, 8), F(20431, 8)),
    ],
)
def test_f1_two_im_envelope(side: str, close: F, cost: F, reserve: F) -> None:
    value = margin(side)
    assert value.initial_margin_envelope == F(2525)
    assert value.order_im == F(2500)
    assert value.open_estimate == F(55, 4)
    assert value.order_close_reserve == close
    assert value.order_cost == cost
    assert value.point_reserve == reserve


@pytest.mark.parametrize("side,expected", [("LONG", F(5047, 500)), ("SHORT", F(5053, 500))])
def test_distinct_account_and_order_fee_rates(side: str, expected: F) -> None:
    value = margin(
        side,
        quantity=D(1),
        entry_limit=D(100),
        mark=D(100),
        taker_rate=D(".0004"),
        order_close_rate=D(".0006"),
    )
    assert value.point_reserve == expected
    assert value.position_close_estimate == (F(9, 250) if side == "LONG" else F(11, 250))


@pytest.mark.parametrize("side,expected", [("LONG", F(9000000, 199)), ("SHORT", F(11000000, 201))])
def test_f3_exact_lp(side: str, expected: F) -> None:
    value = lp(side)
    assert value.outcome.exact_price == expected


def test_f4_complete_table_and_recorded_intercept() -> None:
    full = table((("1050", "0.1", "0.01", "10", "0"), ("2000", "0.1", "0.02", "10", "10.5")))
    value = lp("SHORT", quantity=D(1), entry_price=D(1000), table=full)
    assert value.outcome.exact_price == F(55525, 51)
    assert value.outcome.candidate_tier_id == "2"
    assert value.consumed_tier_ids == ("1", "2")
    assert value.rows[1].intercept == F(2221, 2)
    assert value.rows[0].self_consistent is False
    assert value.rows[1].self_consistent is True


def test_b_import_and_closed_rejected_return() -> None:
    short = table((("1050", "0.1", "0.01", "10", "0"), ("1080", "0.1", "0.02", "10", "10.5")))
    result = lp("SHORT", quantity=D(1), entry_price=D(1000), table=short)
    assert result.kind == "REJECTED"
    assert result.reason == "ROOT_OUTSIDE_CAPTURED_TABLE"
    assert result.public_code == "RISK_V2_LIQUIDATION_UNVERIFIED"
    assert result.evaluated_prefix == ()


def test_l1_no_positive_root_and_shared_boundary() -> None:
    value = lp(leverage=D(1))
    assert value.outcome.kind == "NO_POSITIVE_ROOT"
    assert value.outcome.long_intercept == F(0)
    assert value.outcome.residual_at_zero == F(0)
    boundary = table((("1050", "0.2", "0.2", "5", "0"), ("2000", "0.3", "0.3", "3", "105")))
    value = lp("SHORT", quantity=D(1), entry_price=D(1050), leverage=D(5), table=boundary)
    assert value.outcome.exact_price == F(1050)
    assert value.outcome.candidate_tier_id == "1"


def test_q_bridge_nonmultiple_cost() -> None:
    value = margin(
        quantity=D(1),
        entry_limit=D(1),
        mark=D(1),
        leverage=D(7),
        taker_rate=D(0),
        order_close_rate=D(0),
    )
    result = b.bridge_bybit_reserve_v1(
        value,
        planned_cost_allowance_quote=D(".000000001"),
        reserve_quantum_usdt=D("1e-8"),
        reserve_rounding_revision="APP_RESERVE_CEILING_QUANTUM_V1",
    )
    assert result.initial_margin_quote == D(".14285715")
    assert result.venue_required_total_reserve_quote == D(".14285715")
    assert result.required_reservation_quote == D(".142857151")


def canonical(record: str, payload: dict[str, object]) -> str:
    raw = json.dumps(
        {"schema": "RISK_V2_CANONICAL_V1", "record_type": record, "fields": payload},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def ratio(n: str, d: str = "1") -> dict[str, object]:
    return {"numerator": n, "denominator": d}


def proof(value: Any, full: BybitLinearTierTableV1, **updates: Any) -> str:
    args: dict[str, Any] = {
        "side": "LONG",
        "quantity": D(".5"),
        "entry_price": D(50000),
        "leverage": D(10),
        "extra_margin": D(0),
        "taker_rate": D(".00055"),
        "table": full,
    }
    args.update(updates)
    return b.bybit_liquidation_proof_sha256_v1(value, **args)


def forged(value: Any, **updates: Any) -> Any:
    result = object.__new__(type(value))
    for field in fields(type(value)):
        object.__setattr__(result, field.name, updates.get(field.name, getattr(value, field.name)))
    return result


def test_lp_row_golden_literal_bytes_and_sha() -> None:
    row = b.BybitLiquidationRowProofV1(
        "1", F(0), F(10), F(0), F(199, 200), F(0), F(0), F(199, 20), False
    )
    raw = b'{"fields":{"candidate_price":{"denominator":"1","numerator":"0"},"intercept":{"denominator":"1","numerator":"0"},"lower_quote":{"denominator":"1","numerator":"0"},"residual_lower":{"denominator":"1","numerator":"0"},"residual_upper":{"denominator":"20","numerator":"199"},"self_consistent":false,"slope":{"denominator":"200","numerator":"199"},"tier_id":"1","upper_quote":{"denominator":"1","numerator":"10"}},"record_type":"BybitLiquidationRowProofV1","schema":"RISK_V2_CANONICAL_V1"}'
    assert len(raw) == 485
    assert (
        hashlib.sha256(raw).hexdigest()
        == "fad33ae4e7ea74f1df82af2a03225f0fdb7573b375158e9cde09b385ed1bcab0"
    )
    assert (
        b.bybit_liquidation_row_sha256_v1(row)
        == "fad33ae4e7ea74f1df82af2a03225f0fdb7573b375158e9cde09b385ed1bcab0"
    )


def test_document_lp_proof_codec_golden_is_distinct_from_admissible_graph() -> None:
    raw = b'{"fields":{"consumed_tier_ids":["1"],"domain_upper_quote":"10","entry_price":{"denominator":"1","numerator":"1"},"extra_margin":{"denominator":"1","numerator":"0"},"full_table_sha256":"728be44d0457dd75d8b6c153631f4d615eb209ab765848e8640280e529b056c6","leverage":{"denominator":"1","numerator":"1"},"outcome":{"kind":"NO_POSITIVE_ROOT","long_intercept":{"denominator":"1","numerator":"0"},"reason":"NON_POSITIVE_LONG_INTERCEPT","residual_at_zero":{"denominator":"1","numerator":"0"}},"quantity":{"denominator":"1","numerator":"1"},"raw_manifest_sha256":"0000000000000000000000000000000000000000000000000000000000000000","rows":["fad33ae4e7ea74f1df82af2a03225f0fdb7573b375158e9cde09b385ed1bcab0"],"rule_revision":"BYBIT_LINEAR_FULL_TABLE_LP_V1","side":"LONG","table_identity":{"deduction_rule_revision":"EXACT_CONTINUOUS_MM_DEDUCTION_V1","full_table_sha256":"728be44d0457dd75d8b6c153631f4d615eb209ab765848e8640280e529b056c6","instrument_pin":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","rate_encoding_revision":"BYBIT_LINEAR_RISK_NUMBER_FRACTION_V1","raw_manifest_sha256":"0000000000000000000000000000000000000000000000000000000000000000","reference":{"observation_id":"t1","payload_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","received_at":"2000-01-01T00:00:00.000000+00:00","server_observed_at":"2000-01-01T00:00:00.000000+00:00","source_environment":"FIXTURE","source_id":"fixture","source_rule_revision":"BYBIT_LINEAR_RISK_LIMIT_TABLE_V1"}},"table_source_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","taker_rate":{"denominator":"1","numerator":"0"}},"record_type":"BybitLiquidationProofV1","schema":"RISK_V2_CANONICAL_V1"}'
    assert len(raw) == 1708
    assert (
        hashlib.sha256(raw).hexdigest()
        == "91b9432ddb8d9ef910d2de28e3fa35b70dcf94486621756c59c5bf4a784c6894"
    )
    payload = json.loads(raw)["fields"]
    payload["consumed_tier_ids"] = tuple(payload["consumed_tier_ids"])
    payload["rows"] = tuple(payload["rows"])
    assert (
        _canonical_sha256_v2("BybitLiquidationProofV1", payload)
        == "91b9432ddb8d9ef910d2de28e3fa35b70dcf94486621756c59c5bf4a784c6894"
    )


def test_independent_margin_bridge_and_candidate_recipes() -> None:
    value = margin()
    expected: dict[str, object] = {
        "side": "LONG",
        "quantity": ratio("1", "2"),
        "entry_limit": ratio("50000"),
        "mark": ratio("50500"),
        "leverage": ratio("10"),
        "taker_rate": ratio("11", "20000"),
        "order_close_rate": ratio("11", "20000"),
        "order_im": ratio("2500"),
        "open_estimate": ratio("55", "4"),
        "order_close_reserve": ratio("99", "8"),
        "position_close_estimate": ratio("99", "8"),
        "mark_core": ratio("2525"),
        "entry_core": ratio("2500"),
        "initial_margin_envelope": ratio("2525"),
        "order_cost": ratio("20209", "8"),
        "point_reserve": ratio("20409", "8"),
    }
    margin_sha = canonical("BybitMarginPointV1", expected)
    assert b.bybit_margin_point_sha256_v1(value) == margin_sha
    reserve = b.bridge_bybit_reserve_v1(
        value,
        planned_cost_allowance_quote=D(20),
        reserve_quantum_usdt=D("1e-8"),
        reserve_rounding_revision="APP_RESERVE_CEILING_QUANTUM_V1",
    )
    reserve_sha = canonical(
        "BybitReserveBridgeV1",
        {
            "initial_margin_quote": "2525",
            "venue_required_total_reserve_quote": "2551.125",
            "required_reservation_quote": "2551.125",
            "exact_initial_margin": ratio("2525"),
            "exact_point_reserve": ratio("20409", "8"),
            "exact_required_reservation": ratio("20409", "8"),
            "planned_cost_allowance_quote": "20",
            "reserve_quantum_usdt": "1E-8",
            "reserve_basis": "POINT_ESTIMATE_TWO_IM_ENVELOPE_V1",
            "reserve_rounding_revision": "APP_RESERVE_CEILING_QUANTUM_V1",
        },
    )
    assert b.bybit_reserve_bridge_sha256_v1(reserve) == reserve_sha
    liquid = lp()
    candidate = b.BybitCandidateEvaluationV1(0, D(10), value, reserve, liquid, "GAP_SATISFIED", ())
    assert b.bybit_candidate_evaluation_sha256_v1(candidate) == canonical(
        "BybitCandidateEvaluationV1",
        {
            "grid_index": 0,
            "leverage": "10",
            "margin": margin_sha,
            "reserve": reserve_sha,
            "liquidation": expected_f3_identities(table())[1],
            "liquidation_buffer_result": "GAP_SATISFIED",
            "failure_codes": (),
        },
    )


def test_proof_replay_binds_every_economic_and_source_child() -> None:
    full = table()
    value = lp(table=full)
    assert proof(value, full) == value.proof_sha256
    for name, new in [
        ("side", "SHORT"),
        ("quantity", D(1)),
        ("entry_price", D(50001)),
        ("leverage", D(9)),
        ("extra_margin", D(1)),
        ("taker_rate", D(".00056")),
    ]:
        with pytest.raises(RiskV2Rejected):
            proof(value, full, **{name: new})
    for name, new in [
        ("proof_sha256", "a" * 64),
        ("raw_manifest_sha256", "a" * 64),
        ("full_table_sha256", "a" * 64),
        ("domain_upper_quote", D(1000001)),
    ]:
        with pytest.raises(RiskV2Rejected):
            proof(forged(value, **{name: new}), full)
    other = table((("1000000", "0.01", "0.0050", "100", "0"),))
    assert other.full_table_sha256 == full.full_table_sha256
    assert other.raw_manifest_sha256 != full.raw_manifest_sha256
    with pytest.raises(RiskV2Rejected, match="RISK_V2_SOURCE_MISMATCH"):
        proof(value, other)


def expected_f3_identities(full: BybitLinearTierTableV1) -> tuple[str, str]:
    # F3 LONG K22500; row residual at upper=995000-22500=972500.
    row_sha = canonical(
        "BybitLiquidationRowProofV1",
        {
            "tier_id": "1",
            "lower_quote": ratio("0"),
            "upper_quote": ratio("1000000"),
            "intercept": ratio("22500"),
            "slope": ratio("199", "200"),
            "candidate_price": ratio("9000000", "199"),
            "residual_lower": ratio("-22500"),
            "residual_upper": ratio("972500"),
            "self_consistent": True,
        },
    )
    outcome: dict[str, object] = {
        "kind": "POSITIVE_ROOT",
        "exact_price": ratio("9000000", "199"),
        "candidate_tier_id": "1",
    }
    reference: dict[str, object] = {
        "observation_id": "table1",
        "source_id": "source1",
        "source_environment": "FIXTURE",
        "server_observed_at": "1970-01-01T00:00:00.001000+00:00",
        "received_at": "1970-01-01T00:00:00.001000+00:00",
        "source_rule_revision": "BYBIT_LINEAR_RISK_LIMIT_TABLE_V1",
        "payload_sha256": full.reference.payload_sha256,
    }
    expected: dict[str, object] = {
        "side": "LONG",
        "quantity": ratio("1", "2"),
        "entry_price": ratio("50000"),
        "leverage": ratio("10"),
        "extra_margin": ratio("0"),
        "taker_rate": ratio("11", "20000"),
        "table_source_sha256": bybit_tier_table_sha256_v1(full),
        "table_identity": {
            "instrument_pin": bybit_instrument_pin_sha256_v1(full.instrument_pin),
            "reference": reference,
            "raw_manifest_sha256": full.raw_manifest_sha256,
            "full_table_sha256": full.full_table_sha256,
            "rate_encoding_revision": "BYBIT_LINEAR_RISK_NUMBER_FRACTION_V1",
            "deduction_rule_revision": "EXACT_CONTINUOUS_MM_DEDUCTION_V1",
        },
        "outcome": outcome,
        "domain_upper_quote": "1000000",
        "full_table_sha256": full.full_table_sha256,
        "raw_manifest_sha256": full.raw_manifest_sha256,
        "consumed_tier_ids": ("1",),
        "rows": (row_sha,),
        "rule_revision": "BYBIT_LINEAR_FULL_TABLE_LP_V1",
    }
    expected_sha = canonical("BybitLiquidationProofV1", expected)
    calculation_sha = canonical(
        "BybitLiquidationCalculationV1",
        {
            "outcome": outcome,
            "domain_upper_quote": "1000000",
            "full_table_sha256": full.full_table_sha256,
            "raw_manifest_sha256": full.raw_manifest_sha256,
            "consumed_tier_ids": ("1",),
            "rows": (row_sha,),
            "rule_revision": "BYBIT_LINEAR_FULL_TABLE_LP_V1",
            "proof_sha256": expected_sha,
        },
    )
    return expected_sha, calculation_sha


def test_independent_complete_lp_proof_and_calculation_recipes() -> None:
    full = table()
    value = lp(table=full)
    expected_proof, expected_calculation = expected_f3_identities(full)
    assert value.proof_sha256 == expected_proof
    assert proof(value, full) == expected_proof
    assert b.bybit_liquidation_calculation_sha256_v1(value) == expected_calculation


@pytest.mark.parametrize(
    "code,reason",
    [
        ("RISK_V2_INPUT_INVALID", "INPUT_MISMATCH"),
        ("RISK_V2_SCOPE_MISMATCH", "INPUT_MISMATCH"),
        ("RISK_V2_SOURCE_MISMATCH", "INPUT_MISMATCH"),
        ("RISK_V2_REVISION_MISMATCH", "INPUT_MISMATCH"),
        ("RISK_V2_RESOURCE_LIMIT", "RESOURCE_LIMIT"),
        ("RISK_V2_STALE_EVIDENCE", "EVIDENCE_INVALID"),
        ("RISK_V2_FUTURE_EVIDENCE", "EVIDENCE_INVALID"),
        ("RISK_V2_EXPIRED", "EVIDENCE_INVALID"),
        ("RISK_V2_CAUSAL_ORDER_INVALID", "EVIDENCE_INVALID"),
        ("RISK_V2_RISK_TIER_UNVERIFIED", "SOURCE_INVALID"),
        ("RISK_V2_UNSUPPORTED_MODE", "PROFILE_UNSUPPORTED"),
        ("RISK_V2_UNSUPPORTED_PRODUCT", "PROFILE_UNSUPPORTED"),
        ("RISK_V2_MARGIN_UNVERIFIED", "PROFILE_UNSUPPORTED"),
    ],
)
def test_whitelisted_lp_errors_preserve_exact_public_code(
    code: str, reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject(*args: Any, **kwargs: Any) -> Any:
        raise RiskV2Rejected(code)

    monkeypatch.setattr(b, "bybit_tier_table_sha256_v1", reject)
    result = lp()
    assert type(result) is b.BybitModelRejectedV1
    assert result.reason == reason
    assert result.public_code == code
    assert result.evaluated_prefix == ()


def test_candidate_rejects_crossed_side_and_quantity_even_with_consistent_children() -> None:
    value = margin()
    reserve = b.bridge_bybit_reserve_v1(
        value,
        planned_cost_allowance_quote=D(20),
        reserve_quantum_usdt=D("1e-8"),
        reserve_rounding_revision="APP_RESERVE_CEILING_QUANTUM_V1",
    )
    for liquid in (lp("SHORT"), lp(quantity=D(1))):
        with pytest.raises(RiskV2Rejected, match="RISK_V2_INPUT_INVALID"):
            b.BybitCandidateEvaluationV1(0, D(10), value, reserve, liquid, "GAP_SATISFIED", ())


def test_candidate_and_rejected_prefix_validate_complete_ordered_children() -> None:
    value = margin()
    reserve = b.bridge_bybit_reserve_v1(
        value,
        planned_cost_allowance_quote=D(20),
        reserve_quantum_usdt=D("1e-8"),
        reserve_rounding_revision="APP_RESERVE_CEILING_QUANTUM_V1",
    )
    liquid = lp()
    candidate = b.BybitCandidateEvaluationV1(
        0, D(10), value, reserve, liquid, "GAP_SATISFIED", ("CAPACITY",)
    )
    result = b.BybitModelRejectedV1(
        "REJECTED", "SEARCH", "RESOURCE_LIMIT", "RISK_V2_RESOURCE_LIMIT", D(11), (candidate,)
    )
    assert result.evaluated_prefix == (candidate,)
    for change in (
        {"grid_index": True},
        {"failure_codes": ("CAPACITY", "IMR")},
        {"failure_codes": ("CAPACITY", "CAPACITY")},
        {"liquidation_buffer_result": None},
        {"margin": forged(value, point_reserve=F(0))},
    ):
        with pytest.raises(RiskV2Rejected):
            b.bybit_candidate_evaluation_sha256_v1(forged(candidate, **change))
    with pytest.raises(RiskV2Rejected):
        replace(result, evaluated_prefix=(replace(candidate, grid_index=1),))
    with pytest.raises(RiskV2Rejected):
        replace(result, evaluated_prefix=(replace(candidate, failure_codes=()),))


def test_f6_large_quantum_ceiling_and_source_derived_exponent_boundaries() -> None:
    value = margin(
        quantity=D(1000),
        entry_limit=D(1),
        mark=D(1),
        leverage=D(7),
        taker_rate=D(0),
        order_close_rate=D(0),
    )
    result = b.bridge_bybit_reserve_v1(
        value,
        planned_cost_allowance_quote=D(0),
        reserve_quantum_usdt=D("1e-8"),
        reserve_rounding_revision="APP_RESERVE_CEILING_QUANTUM_V1",
    )
    assert result.required_reservation_quote == D("142.85714286")
    assert margin(entry_limit=D("1e1000"), mark=D("1e1000")).entry_limit == F(10**1000)
    with pytest.raises(RiskV2Rejected, match="RISK_V2_RESOURCE_LIMIT"):
        margin(mark=D("1e1001"))
    with pytest.raises(RiskV2Rejected, match="RISK_V2_RESOURCE_LIMIT"):
        margin(quantity=D("1e65537"))


def test_negative_intercept_long_and_positive_short_with_extra_margin() -> None:
    value = lp(quantity=D(1), entry_price=D(100), leverage=D(1), extra_margin=D(1), taker_rate=D(0))
    assert value.outcome.long_intercept == F(-1)
    assert value.outcome.residual_at_zero == F(1)
    assert value.rows[0].candidate_price == F(-200, 199)
    assert value.rows[0].self_consistent is False
    value = lp(
        "SHORT",
        quantity=D(1),
        entry_price=D(100),
        leverage=D(10),
        extra_margin=D(1),
        taker_rate=D(0),
    )
    assert value.outcome.exact_price == F(22200, 201)


def test_row_residual_and_no_root_tamper_are_rejected() -> None:
    value = lp()
    for change in (
        {"intercept": F(1)},
        {"residual_lower": F(0)},
        {"self_consistent": False},
        {"slope": F(0)},
        {"tier_id": ""},
    ):
        with pytest.raises(RiskV2Rejected):
            b.bybit_liquidation_row_sha256_v1(forged(value.rows[0], **change))
    no_root = lp(leverage=D(1))
    with pytest.raises(RiskV2Rejected):
        b.bybit_liquidation_calculation_sha256_v1(
            forged(no_root, outcome=forged(no_root.outcome, residual_at_zero=F(1)))
        )
    with pytest.raises(RiskV2Rejected):
        b.bybit_liquidation_calculation_sha256_v1(forged(value, consumed_tier_ids=("2",)))


@pytest.mark.parametrize(
    "name,new",
    [
        ("initial_margin_envelope", F(2524)),
        ("point_reserve", F(1)),
        ("open_estimate", F(0)),
        ("order_im", F(2501)),
        ("entry_limit", F(1, 3)),
    ],
)
def test_bridge_replays_all_supplied_margin_parts(name: str, new: F) -> None:
    with pytest.raises(RiskV2Rejected, match="RISK_V2_INPUT_INVALID"):
        b.bridge_bybit_reserve_v1(
            forged(margin(), **{name: new}),
            planned_cost_allowance_quote=D(20),
            reserve_quantum_usdt=D("1e-8"),
            reserve_rounding_revision="APP_RESERVE_CEILING_QUANTUM_V1",
        )


@pytest.mark.parametrize("q", ["0.000000010", "0.0000001", "-1e-8"])
def test_quantum_is_exact_tuple_not_same_value_or_other_step(q: str) -> None:
    with pytest.raises(RiskV2Rejected):
        b.bridge_bybit_reserve_v1(
            margin(),
            planned_cost_allowance_quote=D(0),
            reserve_quantum_usdt=D(q),
            reserve_rounding_revision="APP_RESERVE_CEILING_QUANTUM_V1",
        )


def test_f2_recurrence_and_nonmonotonic_table_are_consumed_in_full() -> None:
    full = table(
        (
            ("50000", "0.1", "0.005", "10", "0"),
            ("100000", "0.1", "0.015", "10", "500"),
            ("150000", "0.1", "0.025", "10", "1500"),
            ("500000", "0.1", "0.035", "10", "3000"),
        )
    )
    result = lp("SHORT", table=full)
    assert result.consumed_tier_ids == ("1", "2", "3", "4")
    assert result.rows[3].slope == F(207, 200)
    assert result.rows[3].intercept == F(30500)
    assert F(400000) * (result.rows[3].slope - 1) - (result.rows[3].intercept - F(27500)) == F(
        11000
    )
    nonmonotonic = table(
        (
            ("100", "0.2", "0.1", "5", "0"),
            ("200", "0.4", "0.3", "2", "20"),
            ("300", "0.3", "0.25", "3", "10"),
        )
    )
    result = lp(quantity=D(1), entry_price=D(250), leverage=D(2), table=nonmonotonic)
    assert result.consumed_tier_ids == ("1", "2", "3")


def test_derived_5001_digits_are_not_source256_and_canonical_bound_is_separate() -> None:
    huge = D((0, (1,) + (0,) * 5000, 0))
    value = margin(
        quantity=huge,
        leverage=huge,
        entry_limit=D(1),
        mark=D(1),
        taker_rate=D(0),
        order_close_rate=D(0),
    )
    assert value.initial_margin_envelope == F(1)
    assert len(b.bybit_margin_point_sha256_v1(value)) == 64
    with pytest.raises(RiskV2Rejected, match="RISK_V2_RESOURCE_LIMIT"):
        margin(entry_limit=huge)
    result = lp(entry_price=huge)
    assert result.reason == "RESOURCE_LIMIT"
    assert result.public_code == "RISK_V2_RESOURCE_LIMIT"
    larger = D((0, (1,) + (0,) * 35000, 0))
    value = margin(
        quantity=larger,
        leverage=larger,
        entry_limit=D(1),
        mark=D(1),
        taker_rate=D(0),
        order_close_rate=D(0),
    )
    with pytest.raises(RiskV2Rejected, match="RISK_V2_RESOURCE_LIMIT"):
        b.bybit_margin_point_sha256_v1(value)


def test_forged_margin_cannot_move_derived_quantity_into_source_price_role() -> None:
    huge = D("1e1001")
    value = margin(
        quantity=huge,
        leverage=huge,
        entry_limit=D(1),
        mark=D(1),
        taker_rate=D(0),
        order_close_rate=D(0),
    )
    # Arithmetic remains identical; source-role P/M exceed adjusted exponent1000.
    bad = forged(value, quantity=F(1), entry_limit=F(10**1001), mark=F(10**1001))
    with pytest.raises(RiskV2Rejected, match="RISK_V2_RESOURCE_LIMIT"):
        b.bybit_margin_point_sha256_v1(bad)


def test_strict_preallocation_guards_and_cross_cancellation() -> None:
    # Operand bit sum=1,000,001 is rejected although this actual product fits 1m.
    with pytest.raises(RiskV2Rejected, match="RISK_V2_RESOURCE_LIMIT"):
        b._product(1 << 999999, 1)
    assert b._product(0, 1 << 999999) == 0
    left, right = F(1 << 600000, 3), F(3, 1 << 600000)
    assert b._mul(left, right) == F(1)
    with pytest.raises(RiskV2Rejected, match="RISK_V2_RESOURCE_LIMIT"):
        b._mul(F(1 << 600000), F(1 << 600000))
    with pytest.raises(RiskV2Rejected, match="RISK_V2_RESOURCE_LIMIT"):
        b._add(F(1 << 999999), F(1 << 999999))
    assert b._sub(F(1 << 999998), F(1 << 999998)) == F(0)
    with pytest.raises(RiskV2Rejected, match="RISK_V2_INPUT_INVALID"):
        b._div(F(1), F(0))


def test_exact_types_forgery_and_closed_rejection_matrix() -> None:
    class Hostile:
        def __eq__(self, other: object) -> bool:
            raise AssertionError("untrusted equality invoked")

    result = lp(table=Hostile())
    assert result.reason == "INPUT_MISMATCH"
    assert result.public_code == "RISK_V2_INPUT_INVALID"
    result = lp(leverage=D(0))
    assert result.reason == "INPUT_MISMATCH"
    assert result.candidate_leverage is None
    for reason, code in [
        ("SOURCE_INVALID", "RISK_V2_INPUT_INVALID"),
        ("INPUT_MISMATCH", "RISK_V2_MARGIN_LIMIT"),
    ]:
        with pytest.raises(RiskV2Rejected):
            b.BybitModelRejectedV1("REJECTED", "LIQUIDATION", reason, code, None, ())  # type: ignore[arg-type]
    full = table()
    bad = forged(full, rate_encoding_revision="UNKNOWN")
    assert lp(table=bad).public_code == "RISK_V2_INPUT_INVALID"
    bad = forged(full, rows=(forged(full.rows[0], mm_deduction_usdt=Hostile()),))
    assert lp(table=bad).public_code == "RISK_V2_INPUT_INVALID"


def test_unlisted_risk_and_programmer_faults_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    def unlisted(*args: Any, **kwargs: Any) -> Any:
        raise RiskV2Rejected("RISK_V2_MARGIN_LIMIT")

    monkeypatch.setattr(b, "bybit_tier_table_sha256_v1", unlisted)
    with pytest.raises(RiskV2Rejected, match="RISK_V2_MARGIN_LIMIT"):
        lp()

    def unowned_lp(*args: Any, **kwargs: Any) -> Any:
        raise RiskV2Rejected("RISK_V2_LIQUIDATION_UNVERIFIED")

    monkeypatch.setattr(b, "bybit_tier_table_sha256_v1", unowned_lp)
    with pytest.raises(RiskV2Rejected, match="RISK_V2_LIQUIDATION_UNVERIFIED"):
        lp()

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("synthetic programmer fault")

    monkeypatch.setattr(b, "bybit_tier_table_sha256_v1", unexpected)
    with pytest.raises(RuntimeError, match="synthetic programmer fault"):
        lp()


def test_ambient_context_digit_limit_and_stamps_unchanged() -> None:
    global_digits = sys.get_int_max_str_digits()
    with localcontext() as context:
        context.prec, context.capitals = 1, 0
        for signal in context.traps:
            context.traps[signal] = True
        before = (
            context.prec,
            context.rounding,
            context.capitals,
            dict(context.traps),
            dict(context.flags),
        )
        value = margin()
        liquid = lp()
        b.bybit_margin_point_sha256_v1(value)
        b.bybit_liquidation_calculation_sha256_v1(liquid)
        after = (
            context.prec,
            context.rounding,
            context.capitals,
            dict(context.traps),
            dict(context.flags),
        )
        assert before == after
    assert getcontext().prec > 0
    assert sys.get_int_max_str_digits() == global_digits
    assert all(
        "time" not in field.name and "computed_at" != field.name
        for field in fields(b.BybitLiquidationCalculationV1)
    )


def full_d_bound() -> Any:
    from test_bybit_linear_model_inputs_v1 import bind_d_graph, full_d_graph  # type: ignore[import-not-found]

    return bind_d_graph(full_d_graph())


def d_success_calculation(bound: Any, *, now: datetime) -> b.BybitBoundModelCalculationV1:
    result = b.calculate_bound_bybit_model_v1(bound, now=now)
    assert isinstance(result, b.BybitBoundModelCalculationV1)
    return result


def test_d_bound_calculation_replays_real_preparation_and_complete_selector() -> None:
    bound = full_d_bound()
    calculation = d_success_calculation(bound, now=bound.preparation.sizing.assessed_at)
    assert type(calculation) is b.BybitBoundModelCalculationV1
    assert calculation.search.selected.leverage == D(3)
    assert calculation.search.selected.reserve.required_reservation_quote == D("48.66666667")
    assert isinstance(calculation.search.selected.liquidation.outcome, b.BybitPositiveRootPointV1)
    assert calculation.search.selected.liquidation.outcome.exact_price == F(40000, 597)
    assert tuple(candidate.failure_codes for candidate in calculation.search.evaluated_prefix) == (
        ("CAPACITY",),
        ("CAPACITY",),
        (),
    )
    assert calculation.exact_calculation_sha256 == b.bybit_bound_calculation_sha256_v1(calculation)


def test_d_two_stage_owner_completion_and_generic_plan_blocker() -> None:
    from multi_exchange_trader.execution import linear_risk_evidence_v2 as e
    from multi_exchange_trader.execution import linear_risk_v2 as r
    from multi_exchange_trader.execution.risk_v2_contracts import RiskComputationRefV2

    bound = full_d_bound()
    now = bound.preparation.sizing.assessed_at
    events = ["economic-calculation"]
    calculation = d_success_calculation(bound, now=now)
    events.append("owner-margin-completion")
    margin_ref = RiskComputationRefV2(
        "margin", "margin-v1", "FIXTURE", now, calculation.binding_sha256
    )
    margin = b.build_bybit_margin_assessment_v1(
        calculation, inputs=bound, reference=margin_ref, now=now
    )
    events += ["margin-assembly", "additional-lp-work", "owner-lp-completion"]
    lp_ref = RiskComputationRefV2(
        "lp", "lp-v1", "FIXTURE", now, e.margin_assessment_sha256_v2(margin)
    )
    lp = b.build_bybit_liquidation_assessment_v1(
        calculation, inputs=bound, margin=margin, reference=lp_ref, now=now
    )
    events.append("lp-assembly")
    assert events == [
        "economic-calculation",
        "owner-margin-completion",
        "margin-assembly",
        "additional-lp-work",
        "owner-lp-completion",
        "lp-assembly",
    ]
    assert margin.initial_margin_quote == D("46.66666667")
    assert isinstance(lp.outcome, e.LiquidationPositiveRootV2)
    assert lp.outcome.exact_price.numerator.as_tuple() == D(40000).as_tuple()
    assert lp.outcome.exact_price.denominator.as_tuple() == D(597).as_tuple()
    assert lp.outcome.consumed_tier_ids == ("1",)
    assessment = r.assess_entry_v2(
        bound.risk_inputs,
        now=now,
        stage="PLAN",
        margin=margin,
        liquidation=lp,
        leverage_readback=None,
    )
    assert assessment.required_reservation_quote == D("48.66666667")
    assert assessment.blockers == ("RISK_V2_LEVERAGE_READBACK_UNVERIFIED",)
    assert assessment.liquidation_buffer_result == "GAP_SATISFIED"
    assert not hasattr(calculation, "qualified") and not hasattr(lp, "qualified")


def test_d_audit_now_preserves_economic_bound_hash() -> None:
    bound = full_d_bound()
    now = bound.preparation.sizing.assessed_at
    first = d_success_calculation(bound, now=now)
    later = d_success_calculation(bound, now=now + timedelta(microseconds=1))
    assert first.exact_calculation_sha256 == later.exact_calculation_sha256


@pytest.mark.parametrize("kind", ["held", "fee-document", "unused-z", "page"])
def test_d_all_available_sources_precede_margin_completion(kind: str) -> None:
    from dataclasses import is_dataclass

    from test_bybit_linear_model_inputs_v1 import bind_d_graph, d_reprepare, full_d_graph

    from multi_exchange_trader.execution import risk_v2_contracts as c
    from multi_exchange_trader.market_data.instrument_catalog import build_instrument_catalog

    graph = full_d_graph()
    original_now = graph["now"]
    later = original_now + timedelta(microseconds=1)
    graph["now"] = original_now + timedelta(microseconds=2)

    def move(value: Any) -> Any:
        if type(value) is a.BybitModelSourceRefV1:
            identifier = "held-Y" if kind == "held" else "page-XAAUSDT"
            return (
                replace(value, received_at=later)
                if kind in ("held", "page") and value.observation_id == identifier
                else value
            )
        if (
            type(value) is RiskObservationRefV2
            and kind == "page"
            and value.observation_id == "table-XAAUSDT"
        ):
            return replace(value, received_at=later)
        if type(value) is a.BybitFeeDocumentRefV1 and kind == "fee-document":
            return replace(value, captured_at=later)
        if type(value) is tuple:
            return tuple(move(child) for child in value)
        if is_dataclass(value) and not isinstance(value, type):
            return replace(
                value, **{field.name: move(getattr(value, field.name)) for field in fields(value)}
            )
        return value

    if kind == "unused-z":
        catalog = graph["catalog"]
        z = catalog.observations[2]
        z = replace(z, source=replace(z.source, received_at=later))
        graph["catalog"] = build_instrument_catalog(
            (*catalog.observations[:2], z),
            declared_universe=catalog.declared_universe,
            now=graph["now"],
            max_age=catalog.max_age,
        )
    else:
        graph["venue_fields"] = move(graph["venue_fields"])
    if kind == "page":
        graph = d_reprepare(graph, move(graph["inputs"]), members=move(graph["domain"].members))
    bound = bind_d_graph(graph)
    calc = d_success_calculation(bound, now=graph["now"])
    ref = c.RiskComputationRefV2(
        "margin", "margin-v1", "FIXTURE", original_now, calc.binding_sha256
    )
    with pytest.raises(RiskV2Rejected, match="CAUSAL_ORDER_INVALID"):
        b.build_bybit_margin_assessment_v1(calc, inputs=bound, reference=ref, now=graph["now"])
    margin = b.build_bybit_margin_assessment_v1(
        calc, inputs=bound, reference=replace(ref, computed_at=later), now=graph["now"]
    )
    assert margin.reference.computed_at == later


@pytest.mark.parametrize("child", ["unused-z", "selected-instrument"])
def test_d_receipt_changes_cannot_reuse_old_bound_calculation(child: str) -> None:
    from test_bybit_linear_model_inputs_v1 import bind_d_graph, full_d_graph

    from multi_exchange_trader.execution.risk_v2_contracts import RiskComputationRefV2

    first = full_d_bound()
    now = first.preparation.sizing.assessed_at
    calc = d_success_calculation(first, now=now)
    changed = bind_d_graph(
        full_d_graph(
            **{
                "unused_received" if child == "unused-z" else "selected_received": now
                - timedelta(microseconds=1)
            }
        )
    )
    ref = RiskComputationRefV2("margin", "margin-v1", "FIXTURE", now, calc.binding_sha256)
    with pytest.raises(RiskV2Rejected, match="SOURCE_MISMATCH"):
        b.build_bybit_margin_assessment_v1(calc, inputs=changed, reference=ref, now=now)


@pytest.mark.parametrize("target", ["hash", "prefix", "proof", "quantity", "shorter-expiry"])
def test_d_complete_calculation_replay_rejects_coherent_hash_borrowing(target: str) -> None:
    from multi_exchange_trader.execution.risk_v2_contracts import RiskComputationRefV2

    bound = full_d_bound()
    now = bound.preparation.sizing.assessed_at
    calc = d_success_calculation(bound, now=now)
    if target == "hash":
        calc = forged(calc, exact_calculation_sha256="f" * 64)
    elif target == "prefix":
        search = forged(calc.search, evaluated_prefix=calc.search.evaluated_prefix[1:])
        calc = forged(calc, search=search)
    elif target == "proof":
        selected = calc.search.selected
        lp = forged(selected.liquidation, proof_sha256="f" * 64)
        changed = forged(selected, liquidation=lp)
        search = forged(
            calc.search,
            selected=changed,
            evaluated_prefix=(*calc.search.evaluated_prefix[:-1], changed),
        )
        calc = forged(calc, search=search)
        calc = forged(calc, exact_calculation_sha256=b.bybit_bound_calculation_sha256_v1(calc))
    elif target == "quantity":
        point = forged(bound.point, envelope=replace(bound.point.envelope, base_quantity=D(2)))
        bound = forged(bound, point=point)
    else:
        bound = forged(bound, valid_until=now + timedelta(microseconds=1))
    ref = RiskComputationRefV2("margin", "margin-v1", "FIXTURE", now, calc.binding_sha256)
    with pytest.raises(RiskV2Rejected):
        b.build_bybit_margin_assessment_v1(calc, inputs=bound, reference=ref, now=now)


def test_d_no_root_full_bound_assembly_and_original_rejection_owner() -> None:
    from test_bybit_linear_model_inputs_v1 import bind_d_graph, full_d_graph

    from multi_exchange_trader.execution import linear_risk_evidence_v2 as e
    from multi_exchange_trader.execution.risk_v2_contracts import RiskComputationRefV2

    bound = bind_d_graph(full_d_graph(allocation="200"))
    now = bound.preparation.sizing.assessed_at
    calc = d_success_calculation(bound, now=now)
    assert calc.search.selected.leverage == D(1)
    margin = b.build_bybit_margin_assessment_v1(
        calc,
        inputs=bound,
        reference=RiskComputationRefV2("margin", "margin-v1", "FIXTURE", now, calc.binding_sha256),
        now=now,
    )
    lp = b.build_bybit_liquidation_assessment_v1(
        calc,
        inputs=bound,
        margin=margin,
        reference=RiskComputationRefV2(
            "lp", "lp-v1", "FIXTURE", now, e.margin_assessment_sha256_v2(margin)
        ),
        now=now,
    )
    assert type(lp.outcome) is e.LiquidationNoPositiveRootV2
    assert lp.outcome.model_input_sha256 == calc.model_input_sha256
    assert lp.outcome.proof_sha256 == calc.search.selected.liquidation.proof_sha256
    rejected_bound = bind_d_graph(full_d_graph(allocation="10"))
    rejected = b.calculate_bound_bybit_model_v1(rejected_bound, now=now)
    assert type(rejected) is b.BybitModelRejectedV1
    assert rejected.public_code == "RISK_V2_LEVERAGE_UNSATISFIABLE"
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_LEVERAGE_UNSATISFIABLE$"):
        b.build_bybit_margin_assessment_v1(
            cast(Any, rejected), inputs=rejected_bound, reference=margin.reference, now=now
        )


def test_d_lp_completion_after_margin_and_exact_margin_reference() -> None:
    from multi_exchange_trader.execution import linear_risk_evidence_v2 as e
    from multi_exchange_trader.execution.risk_v2_contracts import RiskComputationRefV2

    bound = full_d_bound()
    now = bound.preparation.sizing.assessed_at
    audit = now + timedelta(microseconds=2)
    calc = d_success_calculation(bound, now=audit)
    margin = b.build_bybit_margin_assessment_v1(
        calc,
        inputs=bound,
        reference=RiskComputationRefV2(
            "margin", "margin-v1", "FIXTURE", now + timedelta(microseconds=1), calc.binding_sha256
        ),
        now=audit,
    )
    ref = RiskComputationRefV2("lp", "lp-v1", "FIXTURE", now, e.margin_assessment_sha256_v2(margin))
    with pytest.raises(RiskV2Rejected, match="CAUSAL_ORDER_INVALID"):
        b.build_bybit_liquidation_assessment_v1(
            calc, inputs=bound, margin=margin, reference=ref, now=audit
        )
    with pytest.raises(RiskV2Rejected, match="LIQUIDATION_UNVERIFIED"):
        b.build_bybit_liquidation_assessment_v1(
            calc,
            inputs=bound,
            margin=margin,
            reference=replace(ref, computed_at=audit, input_sha256=calc.binding_sha256),
            now=audit,
        )
    with pytest.raises(RiskV2Rejected, match="MARGIN_UNVERIFIED"):
        b.build_bybit_liquidation_assessment_v1(
            calc,
            inputs=bound,
            margin=replace(margin, initial_margin_quote=D("46.66666668")),
            reference=replace(ref, computed_at=audit),
            now=audit,
        )


def test_d_full_table_lp_and_independent_bound_digest_recipe() -> None:
    from test_bybit_linear_model_inputs_v1 import bind_d_graph, expected_hash, full_d_graph

    bound = bind_d_graph(full_d_graph(two_rows=True))
    calc = d_success_calculation(bound, now=bound.preparation.sizing.assessed_at)
    assert calc.search.selected.liquidation.consumed_tier_ids == ("1", "2")
    assert len(calc.search.selected.liquidation.rows) == 2
    payload: dict[str, object] = {
        "model_input_sha256": calc.model_input_sha256,
        "point_input_sha256": calc.point_input_sha256,
        "binding_sha256": calc.binding_sha256,
        "risk_domain_sha256": calc.risk_domain_sha256,
        "requirements_sha256": calc.requirements_sha256,
        "search": b.bybit_leverage_search_sha256_v1(calc.search),
        "valid_until": "2026-11-03T00:00:01.000000+00:00",
    }
    assert b.bybit_bound_calculation_sha256_v1(calc) == expected_hash(
        "BybitBoundModelCalculationV1", payload
    )


def test_d_actual_local_selector_rejected_is_preserved_unchanged() -> None:
    from test_bybit_linear_model_inputs_v1 import bind_d_graph, full_d_graph

    bound = bind_d_graph(full_d_graph(short=True, risk_upper="150"))
    now = bound.preparation.sizing.assessed_at
    direct = b.select_smallest_bybit_leverage_v1(bound.point, now=now)
    result = b.calculate_bound_bybit_model_v1(bound, now=now)
    assert type(result) is b.BybitModelRejectedV1
    assert result == direct
    assert result.reason == "ROOT_OUTSIDE_CAPTURED_TABLE"
    assert result.public_code == "RISK_V2_LIQUIDATION_UNVERIFIED"
    assert result.evaluated_prefix == ()
