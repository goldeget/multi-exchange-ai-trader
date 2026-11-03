"""Hand-calculated raw-source and nonmonotonic continuity fixtures."""

import hashlib
import json
import sys
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Any

import pytest
from test_bybit_linear_model_v1 import selector_point  # type: ignore[import-not-found]

from multi_exchange_trader.account.instrument_envelope import HttpsOrigin
from multi_exchange_trader.account.risk_limit_envelope import BybitRiskLimitEnvelopeReadV1
from multi_exchange_trader.execution import bybit_linear_model_inputs_v1 as a1
from multi_exchange_trader.execution import bybit_linear_model_v1 as c_model
from multi_exchange_trader.execution.bybit_linear_model_inputs_v1 import (
    BybitDecimalFieldV1,
    BybitFeeDocumentRefV1,
    BybitFlatLeverageV1,
    BybitHeldOrderV1,
    BybitHeldPositionV1,
    BybitLinearFeeInputsV1,
    BybitModelFieldRefV1,
    BybitModelSourceRefV1,
    BybitNonVipFeeBasisV1,
    BybitRiskLimitPageV1,
    BybitSelectedEntryProfileV1,
    BybitTradeBehaviourV1,
    BybitUsdtWalletV1,
    BybitUtaModeV1,
    decode_bybit_risk_limit_page_v1,
    normalize_bybit_tier_rows_v1,
)
from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.execution.risk_v2_contracts import (
    InstrumentPinV2,
    _canonical_sha256_v2,
    envelope_sha256_v2,
)
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected, decimal_fraction_v2
from multi_exchange_trader.market_data.instrument_definition import (
    LINEAR_SOURCE_RULE_REVISION,
    PARSER_REVISION,
)
from multi_exchange_trader.trading_product import InstrumentKey

ROW = (
    '{"id":1,"symbol":"BTCUSDT","riskLimitValue":"100",'
    '"initialMargin":0.5,"maintenanceMargin":0.1,"maxLeverage":"2",'
    '"mmDeduction":"0","isLowestRisk":1}'
)


def test_point_digest_has_complete_supplied_recipe() -> None:
    value = selector_point()
    expected = {
        "envelope": envelope_sha256_v2(value.envelope),
        "stop_price": "950",
        "selected_mark": a1.bybit_decimal_field_sha256_v1(value.selected_mark),
        "selected_mark_reference": {
            "observation_id": "mark1",
            "source_id": "source1",
            "source_environment": "FIXTURE",
            "source_rule_revision": "synthetic-mark-v1",
            "server_observed_at": "1970-01-01T00:00:00.001000+00:00",
            "received_at": "1970-01-01T00:00:00.001000+00:00",
            "payload_sha256": "c" * 64,
        },
        "fee": a1.bybit_fee_inputs_sha256_v1(value.fee),
        "table": a1.bybit_tier_table_sha256_v1(value.table),
        "profile": a1.bybit_selected_profile_sha256_v1(value.profile),
        "planned_cost_allowance_quote": "20",
        "capacity_quote": "225",
        "liquidation_buffer_fraction": "0.01",
        "admission_upper_quote": "1010",
        "admission_covered_tier_ids": ["1"],
        "continuous_leverage_lower": "1",
        "continuous_leverage_upper": "5",
        "leverage_step": "1",
        "max_evidence_age_seconds": "10",
        "max_quote_age_seconds": "10",
        "valid_until": "1970-01-01T00:01:00.001000+00:00",
        "model_rule_revision": "BYBIT_LINEAR_ISOLATED_POINT_MODEL_V1",
    }
    raw = json.dumps(
        {
            "schema": "RISK_V2_CANONICAL_V1",
            "record_type": "BybitLinearPointInputV1",
            "fields": expected,
        },
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    assert a1.bybit_point_input_sha256_v1(value) == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    "field,bad",
    [
        ("stop_price", Decimal(0)),
        ("planned_cost_allowance_quote", Decimal(-1)),
        ("capacity_quote", Decimal(-1)),
        ("liquidation_buffer_fraction", Decimal(1)),
        ("admission_upper_quote", Decimal(0)),
        ("continuous_leverage_lower", Decimal("0.9")),
        ("continuous_leverage_upper", Decimal("0.9")),
        ("leverage_step", Decimal(0)),
        ("max_evidence_age_seconds", Decimal(-1)),
        ("max_quote_age_seconds", Decimal(-1)),
        ("admission_covered_tier_ids", ("1", "1")),
        ("admission_covered_tier_ids", ["1"]),
        ("model_rule_revision", "other"),
        ("valid_until", datetime(1970, 1, 1)),
    ],
)
def test_point_validates_fixed_scalar_types_and_bounds(field: str, bad: Any) -> None:
    point = selector_point()
    with pytest.raises(RiskV2Rejected, match="INPUT_INVALID"):
        replace(point, **{field: bad})


def test_point_digest_revalidates_forged_children_and_preserves_source_spelling() -> None:
    point = selector_point()
    forged = object.__new__(type(point))
    for item in fields(type(point)):
        object.__setattr__(forged, item.name, getattr(point, item.name))
    object.__setattr__(forged, "capacity_quote", True)
    with pytest.raises(RiskV2Rejected, match="INPUT_INVALID"):
        a1.bybit_point_input_sha256_v1(forged)
    mark = point.selected_mark
    altered = replace(
        mark, value=Decimal("1010.0"), reference=replace(mark.reference, wire_spelling="1010.0")
    )
    assert a1.bybit_point_input_sha256_v1(
        replace(point, selected_mark=altered)
    ) != a1.bybit_point_input_sha256_v1(point)
    assert a1.bybit_point_input_sha256_v1(
        replace(point, capacity_quote=Decimal("225.0"))
    ) != a1.bybit_point_input_sha256_v1(point)
    with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
        replace(point, stop_price=Decimal("1" * 257))


def test_point_codec_does_not_invent_absent_mark_authority_or_rewrite_refs() -> None:
    point = selector_point()
    altered = replace(
        point,
        selected_mark_reference=replace(point.selected_mark_reference, payload_sha256="9" * 64),
    )
    # This opaque owner-specific payload is bound, without guessing a raw/semantic
    # preimage recipe. Both point hashes can be structurally valid; neither is a
    # qualified source receipt, full generic preparation or applied UTA readback.
    assert a1.bybit_point_input_sha256_v1(point) != a1.bybit_point_input_sha256_v1(altered)
    assert tuple(item.name for item in fields(type(point))) == (
        "envelope",
        "stop_price",
        "selected_mark",
        "selected_mark_reference",
        "fee",
        "table",
        "profile",
        "planned_cost_allowance_quote",
        "capacity_quote",
        "liquidation_buffer_fraction",
        "admission_upper_quote",
        "admission_covered_tier_ids",
        "continuous_leverage_lower",
        "continuous_leverage_upper",
        "leverage_step",
        "max_evidence_age_seconds",
        "max_quote_age_seconds",
        "valid_until",
        "model_rule_revision",
    )


_POLICY_RESOURCE_CASES: list[tuple[str, str, str, bool]] = []
for _policy_field in (
    "liquidation_buffer_fraction",
    "max_evidence_age_seconds",
    "max_quote_age_seconds",
    "stop_price",
):
    _fractional = _policy_field in ("liquidation_buffer_fraction", "stop_price")
    _POLICY_RESOURCE_CASES.extend(
        [
            (_policy_field, "coefficient256", ("0." if _fractional else "") + "1" * 256, True),
            (_policy_field, "coefficient257", ("0." if _fractional else "") + "1" * 257, False),
            (_policy_field, "negative-exponent1000", "1E-1000", True),
            (_policy_field, "negative-exponent1001", "1E-1001", False),
        ]
    )
    if not _fractional:
        _POLICY_RESOURCE_CASES.extend(
            [
                (_policy_field, "positive-exponent1000", "1E+1000", True),
                (_policy_field, "positive-exponent1001", "1E+1001", False),
                (_policy_field, "adjusted-exponent1000", "11E+999", True),
                (_policy_field, "adjusted-exponent1001", "11E+1000", False),
            ]
        )


@pytest.mark.parametrize("gate", ["constructor", "digest", "selector"])
@pytest.mark.parametrize(
    "field,boundary,spelling,accepted",
    _POLICY_RESOURCE_CASES,
    ids=[f"{field}-{boundary}" for field, boundary, _, _ in _POLICY_RESOURCE_CASES],
)
def test_correction_policy_source_resource_boundaries(
    gate: str, field: str, boundary: str, spelling: str, accepted: bool
) -> None:
    """The three public gates must agree on original policy source limits."""
    point = selector_point(capacity="2000")
    value = Decimal(spelling)
    # Deliberately bypass construction for public digest/selector trust-boundary
    # tests. Each gate must validate original Decimal spelling independently.
    forged = object.__new__(type(point))
    for item in fields(type(point)):
        object.__setattr__(forged, item.name, getattr(point, item.name))
    object.__setattr__(forged, field, value)
    if gate == "constructor":
        if accepted:
            constructed = replace(point, **{field: value})
            assert getattr(constructed, field).as_tuple() == value.as_tuple()
        else:
            with pytest.raises(RiskV2Rejected) as error:
                replace(point, **{field: value})
            assert error.value.code == "RISK_V2_RESOURCE_LIMIT"
    elif gate == "digest":
        if accepted:
            assert len(a1.bybit_point_input_sha256_v1(forged)) == 64
        else:
            with pytest.raises(RiskV2Rejected) as error:
                a1.bybit_point_input_sha256_v1(forged)
            assert error.value.code == "RISK_V2_RESOURCE_LIMIT"
    else:
        result = c_model.select_smallest_bybit_leverage_v1(
            forged, now=point.table.pages[0].received_at
        )
        if accepted:
            assert isinstance(result, c_model.BybitLeverageSearchV1)
            assert result.selected.leverage == Decimal(1)
            assert result.selected.liquidation_buffer_result == "MODEL_NO_POSITIVE_TRIGGER"
        else:
            assert isinstance(result, c_model.BybitModelRejectedV1)
            assert (result.phase, result.reason, result.public_code) == (
                "SOURCE",
                "RESOURCE_LIMIT",
                "RISK_V2_RESOURCE_LIMIT",
            )
            assert result.candidate_leverage is None and result.evaluated_prefix == ()


@pytest.mark.parametrize("cost,capacity", [("1" * 300, "2" * 300), ("1E+1001", "2E+1001")])
def test_correction_planned_cost_capacity_keep_derived_roles(cost: str, capacity: str) -> None:
    point = selector_point(cost=cost, capacity=capacity)
    assert point.planned_cost_allowance_quote.as_tuple() == Decimal(cost).as_tuple()
    assert point.capacity_quote.as_tuple() == Decimal(capacity).as_tuple()
    assert len(a1.bybit_point_input_sha256_v1(point)) == 64
    result = c_model.select_smallest_bybit_leverage_v1(point, now=point.table.pages[0].received_at)
    assert isinstance(result, c_model.BybitLeverageSearchV1)
    assert result.selected.leverage == Decimal(1)
    assert (
        result.selected.reserve.planned_cost_allowance_quote.as_tuple() == Decimal(cost).as_tuple()
    )


def body(rows: str = ROW, cursor: str = "") -> bytes:
    return (
        '{"retCode":0,"result":{"category":"linear","list":['
        + rows
        + '],"nextPageCursor":"'
        + cursor
        + '"},"time":1}'
    ).encode()


def read(raw: bytes, ordinal: int = 1, cursor: str = "") -> BybitRiskLimitEnvelopeReadV1:
    stamp = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=1)
    params: tuple[tuple[str, str], ...] = (("category", "linear"), ("symbol", "BTCUSDT"))
    if cursor:
        params += (("cursor", cursor),)
    return BybitRiskLimitEnvelopeReadV1(
        InstrumentKey("BYBIT", "LINEAR", "BTCUSDT"),
        "GET",
        "/v5/market/risk-limit",
        params,
        HttpsOrigin("https", "api.bybit.com", 443),
        HttpsOrigin("https", "api.bybit.com", 443),
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
        ordinal,
        f"obs{ordinal}",
        "source1",
        "gen1",
        "FIXTURE",
    )


def page(rows: str = ROW) -> BybitRiskLimitPageV1:
    return decode_bybit_risk_limit_page_v1(read(body(rows)), page_ordinal=1)


def test_decode_number_spelling_and_raw_row_no_fabricated_lower() -> None:
    value = page(
        ROW.replace('"maintenanceMargin":0.1', '"maintenanceMargin":0.0050').replace(
            '"initialMargin":0.5', '"initialMargin":5e-3'
        )
    )
    row = value.rows[0]
    assert row.maintenance_margin_rate.reference.wire_spelling == "0.0050"
    assert row.initial_margin_rate.reference.wire_spelling == "5e-3"
    assert decimal_fraction_v2(row.maintenance_margin_rate.value) == Fraction(1, 200)
    assert decimal_fraction_v2(row.initial_margin_rate.value) == Fraction(1, 200)
    assert row.maintenance_margin_rate.value.as_tuple() == Decimal("0.0050").as_tuple()
    assert row.upper_inclusive_usdt.reference.json_pointer == "/result/list/0/riskLimitValue"
    assert row.upper_inclusive_usdt.reference.wire_kind == "STRING"
    assert (
        value.source.raw_body_sha256
        == hashlib.sha256(value.envelope.decoded_body_bytes).hexdigest()
    )
    assert tuple(f.name for f in fields(type(row))) == (
        "risk_id",
        "symbol",
        "upper_inclusive_usdt",
        "initial_margin_rate",
        "maintenance_margin_rate",
        "max_leverage",
        "mm_deduction_usdt",
        "is_lowest_risk",
    )


def test_normalize_continuity_nonmonotonic() -> None:
    second = ROW.replace('"id":1', '"id":2').replace('"100"', '"200"')
    second = second.replace('"maintenanceMargin":0.1', '"maintenanceMargin":0.3')
    second = second.replace('"mmDeduction":"0"', '"mmDeduction":"20"').replace('Risk":1', 'Risk":0')
    third = second.replace('"id":2', '"id":3').replace('"200"', '"300"')
    third = third.replace('"maintenanceMargin":0.3', '"maintenanceMargin":0.25')
    third = third.replace('"mmDeduction":"20"', '"mmDeduction":"10"')
    source = page(third + "," + ROW + "," + second)
    with localcontext() as context:
        context.prec = 1
        for signal in context.traps:
            context.traps[signal] = True
        rows = normalize_bybit_tier_rows_v1((source,))
    assert [r.lower_exclusive_usdt for r in rows] == [Fraction(0), Fraction(100), Fraction(200)]
    assert [r.mm_deduction_usdt.value for r in rows] == [Decimal(0), Decimal(20), Decimal(10)]
    assert rows[2].upper_inclusive_usdt.reference.json_pointer == "/result/list/0/riskLimitValue"
    assert len(source.rows) == len(rows) == 3


@pytest.mark.parametrize(
    "old,new",
    [
        ('"id":1', '"id":true'),
        ('"id":1', '"id":1.0'),
        ('"id":1', '"id":1e0'),
        ('"id":1', '"id":0'),
        ('"id":1', '"id":9223372036854775808'),
        ('"maintenanceMargin":0.1', '"maintenanceMargin":"0.1"'),
        ('"riskLimitValue":"100"', '"riskLimitValue":100'),
        ('"riskLimitValue":"100"', '"riskLimitValue":"0100"'),
        ('"riskLimitValue":"100"', '"riskLimitValue":"+100"'),
        ('"symbol":"BTCUSDT"', '"symbol":"ETHUSDT"'),
        ('"mmDeduction":"0"', '"mmDeduction":""'),
        ('"mmDeduction":"0",', ""),
        ('"mmDeduction":"0"', '"extra":0,"mmDeduction":"0"'),
        ('"isLowestRisk":1', '"isLowestRisk":true'),
    ],
)
def test_wire_schema_rejections(old: str, new: str) -> None:
    with pytest.raises(RiskV2Rejected, match="INPUT_INVALID"):
        page(ROW.replace(old, new))


@pytest.mark.parametrize(
    "rows",
    [
        ROW.replace('"mmDeduction":"0"', '"mmDeduction":"1"'),
        ROW.replace('"maintenanceMargin":0.1', '"maintenanceMargin":1'),
        ROW.replace('"maintenanceMargin":0.1', '"maintenanceMargin":0'),
        ROW.replace('"initialMargin":0.5', '"initialMargin":0.05'),
        ROW.replace('"isLowestRisk":1', '"isLowestRisk":0'),
        ROW + "," + ROW,
    ],
)
def test_invalid_ranges_and_continuity(rows: str) -> None:
    with pytest.raises(RiskV2Rejected):
        normalize_bybit_tier_rows_v1((page(rows),))


@pytest.mark.parametrize(
    "old,new",
    [
        ('"retCode":0', '"retCode":false'),
        ('"retCode":0', '"retCode":1'),
        ('"time":1', '"time":1e0'),
        ('"time":1', '"time":true'),
        ('"time":1', '"time":0'),
        ('"time":1', '"time":253402300800000'),
        ('"category":"linear"', '"category":"spot"'),
        ('"nextPageCursor":""', '"nextPageCursor":null'),
        ('"nextPageCursor":""', '"nextPageCursor":"","extra":0'),
        ('"retCode":0', '"retMsg":false,"retCode":0'),
        ('"retCode":0', '"retExtInfo":[],"retCode":0'),
        ('"retCode":0', '"unknown":0,"retCode":0'),
    ],
)
def test_closed_root_result_and_integer_controls(old: str, new: str) -> None:
    with pytest.raises(RiskV2Rejected):
        decode_bybit_risk_limit_page_v1(
            read(body().replace(old.encode(), new.encode())), page_ordinal=1
        )


def test_cursor_chain_and_forged_page_revalidation() -> None:
    first = decode_bybit_risk_limit_page_v1(read(body(cursor="next")), page_ordinal=1)
    secondrow = (
        ROW.replace('"id":1', '"id":2').replace('"100"', '"200"').replace('Risk":1', 'Risk":0')
    )
    second = decode_bybit_risk_limit_page_v1(read(body(secondrow), 2, "next"), page_ordinal=2)
    assert len(normalize_bybit_tier_rows_v1((first, second))) == 2
    for pages in ((first,), (second, first), (first, first)):
        with pytest.raises(RiskV2Rejected):
            normalize_bybit_tier_rows_v1(pages)
    with pytest.raises(RiskV2Rejected):
        normalize_bybit_tier_rows_v1((replace(first, next_cursor="bad"), second))
    object.__setattr__(second.rows[0].max_leverage, "value", Decimal(999))
    with pytest.raises(RiskV2Rejected):
        normalize_bybit_tier_rows_v1((first, second))


def test_source_numeric_limits_before_decimal_conversion() -> None:
    for spelling in ("1" * 256, "1e1000", "1e-1000"):
        assert page(ROW.replace('"100"', '"' + spelling + '"')).rows
    for spelling in ("1" * 257, "1e1001", "1e-1001", "1" * 1025, "1e" + "9" * 1021):
        with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
            page(ROW.replace('"100"', '"' + spelling + '"'))


def captured_pages(
    count: int, total_rows: int, *, padded: bool = False
) -> tuple[BybitRiskLimitPageV1, ...]:
    pages = []
    risk_id = 1
    for ordinal in range(1, count + 1):
        nrows = total_rows // count + int(ordinal <= total_rows % count)
        rows = []
        for _ in range(nrows):
            rows.append(
                ROW.replace('"id":1', f'"id":{risk_id}')
                .replace('"100"', f'"{risk_id * 100}"')
                .replace('Risk":1', f'Risk":{int(risk_id == 1)}')
            )
            risk_id += 1
        raw = body(",".join(rows), f"c{ordinal}" if ordinal < count else "")
        if padded:
            raw += b" " * (65536 - len(raw))
        pages.append(
            decode_bybit_risk_limit_page_v1(
                read(raw, ordinal, f"c{ordinal - 1}" if ordinal > 1 else ""), page_ordinal=ordinal
            )
        )
    return tuple(pages)


def test_row_and_page_limits_independent_of_canonical_payload() -> None:
    pages = captured_pages(64, 1024)
    rows = normalize_bybit_tier_rows_v1(pages)
    assert len(rows) == 1024
    assert rows[-1].lower_exclusive_usdt == Fraction(102300)
    with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
        normalize_bybit_tier_rows_v1(captured_pages(64, 1025))
    with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
        normalize_bybit_tier_rows_v1(pages + (pages[-1],))


def test_total_body_bytes_independent_boundary() -> None:
    pages = captured_pages(16, 16, padded=True)
    assert len(normalize_bybit_tier_rows_v1(pages)) == 16
    extra = captured_pages(1, 1)[0]
    with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
        normalize_bybit_tier_rows_v1(pages + (extra,))


def private_source() -> BybitModelSourceRefV1:
    return replace(
        page().source,
        path="/v5/account/wallet-balance",
        query=(),
        account_scope=ProtectionScope("synthetic-account", "DEMO"),
    )


def number_field(source: BybitModelSourceRefV1, spelling: str = "1") -> BybitDecimalFieldV1:
    return BybitDecimalFieldV1(
        Decimal(spelling), BybitModelFieldRefV1(source, "/result/value", "STRING", spelling)
    )


def test_source_scope_pointer_tuple_and_exact_types() -> None:
    source = private_source()
    changes: tuple[dict[str, Any], ...] = (
        {"account_scope": None},
        {"source_environment": "PUBLIC_MAINNET"},
        {"path": "https://api.bybit.com/secret"},
        {"query": []},
        {"page_ordinal": True},
        {"accepted_attempt": 0},
    )
    for change in changes:
        with pytest.raises(RiskV2Rejected):
            replace(source, **change)
    for pointer in ("bad", "/bad~", "/bad~2"):
        with pytest.raises(RiskV2Rejected):
            BybitModelFieldRefV1(source, pointer, "STRING", "1")
    assert BybitModelFieldRefV1(source, "/a~0b~1c", "STRING", "1")
    for kind, spelling in (("INTEGER", "1.0"), ("BOOLEAN", "0"), ("NUMBER", "nan")):
        with pytest.raises(RiskV2Rejected):
            BybitModelFieldRefV1(source, "/x", kind, spelling)  # type: ignore[arg-type]
    with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
        BybitModelFieldRefV1(source, "/" + "a" * 2048, "STRING", "1")

    class Hostile:
        def __eq__(self, other: object) -> bool:
            raise AssertionError("foreign comparison executed")

    object.__setattr__(source, "raw_body_sha256", Hostile())
    with pytest.raises(RiskV2Rejected):
        number_field(source)


def test_source_times_are_utc_normalized_without_clock_read() -> None:
    source = private_source()
    offset = timezone(timedelta(hours=9))
    value = replace(
        source,
        request_started_at=source.request_started_at.astimezone(offset),
        received_at=source.received_at.astimezone(offset),
    )
    assert value.received_at.tzinfo is UTC
    assert value.request_started_at.tzinfo is UTC


def test_decimal_preserves_tuple_and_detects_equivalent_value_forgery() -> None:
    source = private_source()
    field = number_field(source, "0.0050")
    with pytest.raises(RiskV2Rejected):
        replace(field, value=Decimal("0.005"))
    with pytest.raises(RiskV2Rejected):
        replace(field, value=True)  # type: ignore[arg-type]

    class SubDecimal(Decimal):
        pass

    with pytest.raises(RiskV2Rejected):
        replace(field, value=SubDecimal("0.0050"))


def fee_document() -> BybitFeeDocumentRefV1:
    return BybitFeeDocumentRefV1(
        "https://www.bybit.com/en/help-center/article/Fee",
        "2026-11-03",
        datetime(2026, 11, 3, tzinfo=UTC),
        "a" * 64,
        "Non-VIP derivatives: taker",
        "0.0550",
        "PERCENT",
        "b" * 64,
        "synthetic-fees-v1",
    )


def test_fee_document_exact_percent_and_original_limits() -> None:
    document = fee_document()
    with localcontext() as context:
        context.prec = 1
        for signal in context.traps:
            context.traps[signal] = True
        fee = BybitNonVipFeeBasisV1(Decimal("0.000550"), document)
    assert fee.value.as_tuple() == Decimal("0.000550").as_tuple()
    with pytest.raises(RiskV2Rejected):
        replace(fee, value=Decimal("0.0550"))
    for url in (
        "https://bybit.com.evil/x",
        "https://u:p@bybit.com/x",
        "http://bybit.com/x",
        "https://bybit.com:444/x",
        "https://bybit.com/x#frag",
    ):
        with pytest.raises(RiskV2Rejected):
            replace(document, document_url=url)
    assert replace(document, exact_locator="가" * 600)
    with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
        replace(document, displayed_rate_spelling="1" * 257)


def test_inherited_records_preserve_observed_settings_without_defaults() -> None:
    source = private_source()
    one, zero = number_field(source), number_field(source, "0")
    behaviour = BybitTradeBehaviourV1(source, True, False, None)
    assert behaviour.lpa_perp and behaviour.delta_enable is None
    BybitUtaModeV1(source, 5, "ISOLATED_MARGIN", 1)
    BybitFlatLeverageV1(source, "BTCUSDT", 0, one, "/v5/position/list")
    BybitLinearFeeInputsV1(
        zero, zero, BybitNonVipFeeBasisV1(Decimal(0), zero.reference), "USDT", behaviour, "fee-v1"
    )
    wallet = BybitUsdtWalletV1(
        source,
        number_field(source, "-1"),
        zero,
        zero,
        zero,
        zero,
        zero,
        zero,
        zero,
        zero,
        number_field(source, "-2"),
    )
    assert wallet.wallet_balance.value == Decimal(-1)
    for change in ({"lpa_perp": 0}, {"futures_mnt_fee_deduction": None}, {"delta_enable": 1}):
        with pytest.raises(RiskV2Rejected):
            replace(behaviour, **change)
    with pytest.raises(RiskV2Rejected):
        BybitFlatLeverageV1(source, "bad symbol", 0, one, "/v5/position/list")
    with pytest.raises(RiskV2Rejected):
        replace(wallet, locked=number_field(source, "-1"))


def test_held_and_selected_profile_are_values_not_eligibility_claims() -> None:
    source = private_source()
    one, zero = number_field(source), number_field(source, "0")
    pin = InstrumentPinV2(
        InstrumentKey("BYBIT", "LINEAR", "BTCUSDT"),
        "BTCUSDT-LINEAR.BYBIT",
        "catalog-v1",
        "a" * 64,
        PARSER_REVISION,
        LINEAR_SOURCE_RULE_REVISION,
        "source1",
        "FIXTURE",
        source.received_at,
        source.received_at,
        "b" * 64,
    )
    position = BybitHeldPositionV1(
        source,
        pin,
        1,
        "buy",
        one,
        one,
        one,
        one,
        zero,
        zero,
        zero,
        zero,
        None,
        1,
        0,
        zero,
        "Transitional",
        False,
        -1,
        1,
    )
    assert position.side == "buy" and position.position_status == "Transitional"
    order = BybitHeldOrderV1(
        source,
        "order1",
        "",
        pin,
        1,
        "SELL",
        "Other",
        zero,
        one,
        one,
        "Unknown",
        False,
        True,
        None,
        1,
    )
    assert order.status == "Unknown" and order.order_type == "Other"
    profile = BybitSelectedEntryProfileV1(
        "BYBIT_USDT_LINEAR_ISOLATED_ONE_WAY_NEW_ENTRY_V1",
        "BTCUSDT",
        0,
        one,
        ("order1",),
        (),
        (),
        BybitModelFieldRefV1(source, "/amr", "INTEGER", "1"),
        Decimal(1),
        "NEW_SELECTED_FLAT_NO_ADD_V1",
        (source,),
    )
    assert profile.selected_open_order_ids == ("order1",)
    assert profile.prospective_extra_margin_usdt == Decimal(1)
    with pytest.raises(RiskV2Rejected):
        replace(profile, source_records=())
    with pytest.raises(RiskV2Rejected):
        replace(position, seq=True)
    with pytest.raises(RiskV2Rejected):
        replace(order, order_link_id="bad id")


def test_forged_fraction_lower_rejected_before_comparison() -> None:
    normalized = normalize_bybit_tier_rows_v1((page(),))[0]
    lower = Fraction(0)
    object.__setattr__(lower, "_denominator", 2)
    with pytest.raises(RiskV2Rejected):
        replace(normalized, lower_exclusive_usdt=lower)


@pytest.mark.parametrize(
    "component", ["source_generation", "source_id", "source_environment", "observation_id"]
)
def test_chain_identity_cannot_be_substituted(component: str) -> None:
    pages = captured_pages(2, 2)
    changes: dict[str, Any] = {component: "other"}
    if component == "source_environment":
        changes[component] = "PUBLIC_MAINNET"
    if component == "observation_id":
        changes[component] = pages[0].source.observation_id
    altered = decode_bybit_risk_limit_page_v1(replace(pages[1].envelope, **changes), page_ordinal=2)
    with pytest.raises(RiskV2Rejected, match="SOURCE_MISMATCH"):
        normalize_bybit_tier_rows_v1((pages[0], altered))


def test_cursor_match_uniqueness_and_utf8_byte_limit() -> None:
    first = decode_bybit_risk_limit_page_v1(read(body(cursor="next")), page_ordinal=1)
    wrong = decode_bybit_risk_limit_page_v1(read(body(), 2, "wrong"), page_ordinal=2)
    with pytest.raises(RiskV2Rejected, match="SOURCE_MISMATCH"):
        normalize_bybit_tier_rows_v1((first, wrong))
    middle = decode_bybit_risk_limit_page_v1(read(body(cursor="next"), 2, "next"), page_ordinal=2)
    terminal = decode_bybit_risk_limit_page_v1(read(body(), 3, "next"), page_ordinal=3)
    with pytest.raises(RiskV2Rejected):
        normalize_bybit_tier_rows_v1((first, middle, terminal))
    assert decode_bybit_risk_limit_page_v1(read(body(cursor="a" * 1024)), page_ordinal=1)
    with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
        decode_bybit_risk_limit_page_v1(read(body(cursor="가" * 342)), page_ordinal=1)


def test_exact_deduction_recurrence_and_upper_ties() -> None:
    second = ROW.replace('"id":1', '"id":2').replace('"100"', '"200"')
    second = second.replace('"maintenanceMargin":0.1', '"maintenanceMargin":0.3')
    second = second.replace('"mmDeduction":"0"', '"mmDeduction":"20.0000000001"')
    second = second.replace('Risk":1', 'Risk":0')
    with pytest.raises(RiskV2Rejected, match="RISK_TIER_UNVERIFIED"):
        normalize_bybit_tier_rows_v1((page(ROW + "," + second),))
    tie = ROW.replace('"id":1', '"id":2').replace('Risk":1', 'Risk":0')
    with pytest.raises(RiskV2Rejected, match="RISK_TIER_UNVERIFIED"):
        normalize_bybit_tier_rows_v1((page(ROW + "," + tie),))


def test_fee_document_empty_fragment_and_port_are_not_absent() -> None:
    for url in ("https://bybit.com/x#", "https://bybit.com:/x"):
        with pytest.raises(RiskV2Rejected):
            replace(fee_document(), document_url=url)


# A1 expected payloads below are handwritten maps, encoded independently by the
# standard test-only JSON encoder. No production projection/digest is an oracle.
STAMP = "1970-01-01T00:00:00.001000+00:00"


def expected_hash(record: str, payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            {"schema": "RISK_V2_CANONICAL_V1", "record_type": record, "fields": payload},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    ).hexdigest()


def a1_pin(symbol: str = "BTCUSDT") -> InstrumentPinV2:
    stamp = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=1)
    return InstrumentPinV2(
        InstrumentKey("BYBIT", "LINEAR", symbol),
        symbol + "-LINEAR.BYBIT",
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


def a1_capture(rows: str = ROW) -> a1.BybitRiskLimitCaptureV1:
    return a1.parse_bybit_risk_limit_table_v1(
        (read(body(rows)),), instrument_pin=a1_pin(), observation_id="table1"
    )


def expected_pin() -> dict[str, object]:
    return {
        "key": {"venue": "BYBIT", "product": "LINEAR", "symbol": "BTCUSDT"},
        "native_instrument_id": "BTCUSDT-LINEAR.BYBIT",
        "catalog_revision": "catalog-v1",
        "specification_revision": "a" * 64,
        "parser_revision": PARSER_REVISION,
        "source_rule_revision": LINEAR_SOURCE_RULE_REVISION,
        "source_id": "instruments",
        "source_environment": "FIXTURE",
        "response_time": STAMP,
        "received_at": STAMP,
        "raw_row_sha256": "b" * 64,
    }


def expected_source(raw_hash: str, private: bool = False) -> dict[str, object]:
    return {
        "observation_id": "obs1",
        "source_id": "source1",
        "source_generation": "gen1",
        "account_scope": {"account_id": "synthetic-account", "environment": "DEMO"}
        if private
        else None,
        "source_environment": "FIXTURE",
        "origin": {"scheme": "https", "host": "api.bybit.com", "port": 443},
        "path": "/v5/account/wallet-balance" if private else "/v5/market/risk-limit",
        "query": () if private else (("category", "linear"), ("symbol", "BTCUSDT")),
        "request_started_at": STAMP,
        "received_at": STAMP,
        "envelope_time_ms": 1,
        "raw_body_sha256": raw_hash,
        "page_ordinal": 1,
        "accepted_attempt": 1,
    }


def rational(n: str, d: str = "1") -> dict[str, object]:
    return {"numerator": n, "denominator": d}


def test_number_spelling_raw_diff_semantic_equal() -> None:
    first = a1_capture(ROW.replace('"maintenanceMargin":0.1', '"maintenanceMargin":0.0050'))
    second = a1_capture(ROW.replace('"maintenanceMargin":0.1', '"maintenanceMargin":5e-3'))
    left, right = first.table.rows[0], second.table.rows[0]
    assert left.maintenance_margin_rate.reference.wire_spelling == "0.0050"
    assert right.maintenance_margin_rate.reference.wire_spelling == "5e-3"
    assert decimal_fraction_v2(left.maintenance_margin_rate.value) == Fraction(1, 200)
    assert decimal_fraction_v2(right.maintenance_margin_rate.value) == Fraction(1, 200)
    assert first.table.raw_manifest_sha256 != second.table.raw_manifest_sha256
    assert first.table.full_table_sha256 == second.table.full_table_sha256
    assert a1.bybit_tier_table_sha256_v1(first.table) != a1.bybit_tier_table_sha256_v1(second.table)
    assert first.table.reference.payload_sha256 != second.table.reference.payload_sha256


def test_complete_table_survives_admission_subset() -> None:
    first = ROW.replace('"100"', '"1050"').replace(":0.1", ":0.01")
    second = ROW.replace('"id":1', '"id":2').replace('"100"', '"2000"')
    second = second.replace(":0.1", ":0.02").replace('"0"', '"10.5"').replace('Risk":1', 'Risk":0')
    capture = a1_capture(first + "," + second)
    assert tuple(row.risk_id for row in capture.table.rows) == (1, 2)
    assert capture.table.rows[0].upper_inclusive_usdt.value > Decimal(1000)
    assert capture.table.rows[1].lower_exclusive_usdt == Fraction(1050)
    assert capture.raw_pages == (read(body(first + "," + second)),)


def test_a1_nonmonotonic_continuity() -> None:
    second = ROW.replace('"id":1', '"id":2').replace('"100"', '"200"')
    second = second.replace(":0.1", ":0.3").replace('"0"', '"20"').replace('Risk":1', 'Risk":0')
    third = second.replace('"id":2', '"id":3').replace('"200"', '"300"')
    third = third.replace(":0.3", ":0.25").replace('"20"', '"10"')
    assert tuple(
        r.mm_deduction_usdt.value for r in a1_capture(third + "," + ROW + "," + second).table.rows
    ) == (Decimal(0), Decimal(20), Decimal(10))


@pytest.mark.parametrize(
    "old,new",
    [
        ('"initialMargin":0.5', '"initialMargin":0.5,"initial\\u004dargin":0.5'),
        ('"mmDeduction":"0",', ""),
        ('"mmDeduction":"0"', '"mmDeduction":"1"'),
        ('"initialMargin":0.5', '"initialMargin":"0.5"'),
        ('"id":1', '"id":true'),
    ],
)
def test_final_parse_replays_raw_schema(old: str, new: str) -> None:
    with pytest.raises(RiskV2Rejected):
        a1_capture(ROW.replace(old, new))


def test_final_reference_has_exact_rule_and_actual_supplied_maxima() -> None:
    p1 = read(body(cursor="next"))
    row2 = ROW.replace('"id":1', '"id":2').replace('"100"', '"200"').replace('Risk":1', 'Risk":0')
    raw2 = body(row2).replace(b'"time":1', b'"time":2')
    p2 = replace(
        read(body(row2), 2, "next"),
        decoded_body_bytes=raw2,
        decoded_body_sha256=hashlib.sha256(raw2).hexdigest(),
        received_at=p1.received_at + timedelta(milliseconds=9),
        response_time=p1.response_time + timedelta(milliseconds=1),
    )
    c = a1.parse_bybit_risk_limit_table_v1(
        (p1, p2), instrument_pin=a1_pin(), observation_id="explicit-table"
    )
    ref = c.table.reference
    assert (
        ref.observation_id,
        ref.source_id,
        ref.source_environment,
        ref.source_rule_revision,
    ) == ("explicit-table", "source1", "FIXTURE", "BYBIT_LINEAR_RISK_LIMIT_TABLE_V1")
    assert ref.server_observed_at == datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=2)
    assert ref.received_at == datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=10)
    assert ref.server_observed_at != ref.received_at
    assert ref.payload_sha256 == a1.bybit_tier_source_payload_sha256_v1(c.table)
    assert c.table.pages[0].received_at == p1.received_at


def test_a1_exact_source_field_decimal_row_and_pin_recipes() -> None:
    source = page().source
    source_hash = expected_hash("BybitModelSourceRefV1", expected_source(source.raw_body_sha256))
    assert a1.bybit_source_ref_sha256_v1(source) == source_hash
    assert a1.bybit_instrument_pin_sha256_v1(a1_pin()) == expected_hash(
        "BybitInstrumentPinV1", expected_pin()
    )
    field = page().rows[0].maintenance_margin_rate
    field_hash = expected_hash(
        "BybitModelFieldRefV1",
        {
            "source": source_hash,
            "json_pointer": "/result/list/0/maintenanceMargin",
            "wire_kind": "NUMBER",
            "wire_spelling": "0.1",
        },
    )
    assert a1.bybit_field_ref_sha256_v1(field.reference) == field_hash
    assert a1.bybit_decimal_field_sha256_v1(field) == expected_hash(
        "BybitDecimalFieldV1", {"value": "0.1", "reference": field_hash}
    )
    row_fields: dict[str, object] = {
        "risk_id": 1,
        "lower_exclusive_usdt": rational("0"),
        "is_lowest_risk": 1,
    }
    for name, pointer, kind, spelling in (
        ("upper_inclusive_usdt", "riskLimitValue", "STRING", "100"),
        ("initial_margin_rate", "initialMargin", "NUMBER", "0.5"),
        ("maintenance_margin_rate", "maintenanceMargin", "NUMBER", "0.1"),
        ("max_leverage", "maxLeverage", "STRING", "2"),
        ("mm_deduction_usdt", "mmDeduction", "STRING", "0"),
    ):
        h = expected_hash(
            "BybitModelFieldRefV1",
            {
                "source": source_hash,
                "json_pointer": "/result/list/0/" + pointer,
                "wire_kind": kind,
                "wire_spelling": spelling,
            },
        )
        row_fields[name] = expected_hash("BybitDecimalFieldV1", {"value": spelling, "reference": h})
    assert a1.bybit_tier_row_sha256_v1(normalize_bybit_tier_rows_v1((page(),))[0]) == expected_hash(
        "BybitLinearTierRowV1", row_fields
    )


def test_a1_inherited_recipes_preserve_every_field_and_null() -> None:
    source = private_source()
    sh = expected_hash("BybitModelSourceRefV1", expected_source(source.raw_body_sha256, True))
    one = number_field(source)
    fh = expected_hash(
        "BybitModelFieldRefV1",
        {
            "source": sh,
            "json_pointer": "/result/value",
            "wire_kind": "STRING",
            "wire_spelling": "1",
        },
    )
    dh = expected_hash("BybitDecimalFieldV1", {"value": "1", "reference": fh})
    bh = expected_hash(
        "BybitTradeBehaviourV1",
        {"source": sh, "lpa_perp": True, "futures_mnt_fee_deduction": False, "delta_enable": None},
    )
    behaviour = BybitTradeBehaviourV1(source, True, False, None)
    assert a1.bybit_trade_behaviour_sha256_v1(behaviour) == bh
    assert a1.bybit_source_ref_sha256_v1(source) == sh
    assert a1.bybit_uta_mode_sha256_v1(
        BybitUtaModeV1(source, 5, "ISOLATED_MARGIN", 1)
    ) == expected_hash(
        "BybitUtaModeV1",
        {
            "source": sh,
            "unified_margin_status": 5,
            "margin_mode": "ISOLATED_MARGIN",
            "updated_time_ms": 1,
        },
    )
    assert a1.bybit_flat_leverage_sha256_v1(
        BybitFlatLeverageV1(source, "BTCUSDT", 0, one, "/v5/position/list")
    ) == expected_hash(
        "BybitFlatLeverageV1",
        {
            "source": sh,
            "symbol": "BTCUSDT",
            "position_idx": 0,
            "leverage": dh,
            "read_path": "/v5/position/list",
        },
    )
    nh = expected_hash(
        "BybitNonVipFeeBasisV1", {"value": "1", "reference_kind": "JSON_FIELD", "reference": fh}
    )
    assert a1.bybit_nonvip_fee_sha256_v1(BybitNonVipFeeBasisV1(Decimal(1), one.reference)) == nh
    assert a1.bybit_fee_inputs_sha256_v1(
        BybitLinearFeeInputsV1(
            one, one, BybitNonVipFeeBasisV1(Decimal(1), one.reference), "USDT", behaviour, "fee-v1"
        )
    ) == expected_hash(
        "BybitLinearFeeInputsV1",
        {
            "account_taker_rate": dh,
            "account_maker_rate": dh,
            "closing_order_reserve_nonvip_rate": nh,
            "fee_currency": "USDT",
            "behaviour": bh,
            "nonvip_applicability_revision": "fee-v1",
        },
    )
    assert a1.bybit_wallet_sha256_v1(
        BybitUsdtWalletV1(source, one, one, one, one, one, one, one, one, one, one)
    ) == expected_hash(
        "BybitUsdtWalletV1",
        {
            "source": sh,
            "wallet_balance": dh,
            "equity": dh,
            "total_position_im": dh,
            "total_order_im": dh,
            "locked": dh,
            "bonus": dh,
            "borrow_amount": dh,
            "spot_borrow": dh,
            "accrued_interest": dh,
            "unrealised_pnl": dh,
        },
    )
    pin_hash = expected_hash("BybitInstrumentPinV1", expected_pin())
    position = BybitHeldPositionV1(
        source,
        a1_pin(),
        1,
        "buy",
        one,
        one,
        one,
        one,
        one,
        one,
        one,
        one,
        None,
        1,
        0,
        one,
        "Transitional",
        False,
        -1,
        1,
    )
    assert a1.bybit_held_position_sha256_v1(position) == expected_hash(
        "BybitHeldPositionV1",
        {
            "source": sh,
            "instrument_pin": pin_hash,
            "position_idx": 1,
            "side": "buy",
            "size": dh,
            "avg_price": dh,
            "mark_price": dh,
            "leverage": dh,
            "position_im": dh,
            "position_im_by_mp": dh,
            "position_mm": dh,
            "position_mm_by_mp": dh,
            "liq_price": None,
            "auto_add_margin": 1,
            "risk_id": 0,
            "risk_limit_value": dh,
            "position_status": "Transitional",
            "is_reduce_only": False,
            "seq": -1,
            "updated_time_ms": 1,
        },
    )
    order = BybitHeldOrderV1(
        source,
        "order1",
        "",
        a1_pin(),
        1,
        "SELL",
        "Other",
        one,
        one,
        one,
        "Unknown",
        False,
        True,
        None,
        1,
    )
    assert a1.bybit_held_order_sha256_v1(order) == expected_hash(
        "BybitHeldOrderV1",
        {
            "source": sh,
            "order_id": "order1",
            "order_link_id": "",
            "instrument_pin": pin_hash,
            "position_idx": 1,
            "side": "SELL",
            "order_type": "Other",
            "price": dh,
            "quantity": dh,
            "leaves_quantity": dh,
            "status": "Unknown",
            "reduce_only": False,
            "close_on_trigger": True,
            "trigger_price": None,
            "updated_time_ms": 1,
        },
    )
    amr = BybitModelFieldRefV1(source, "/amr", "INTEGER", "1")
    ah = expected_hash(
        "BybitModelFieldRefV1",
        {"source": sh, "json_pointer": "/amr", "wire_kind": "INTEGER", "wire_spelling": "1"},
    )
    profile = BybitSelectedEntryProfileV1(
        "BYBIT_USDT_LINEAR_ISOLATED_ONE_WAY_NEW_ENTRY_V1",
        "BTCUSDT",
        0,
        one,
        ("order1",),
        (),
        (),
        amr,
        Decimal(1),
        "NEW_SELECTED_FLAT_NO_ADD_V1",
        (source,),
    )
    assert a1.bybit_selected_profile_sha256_v1(profile) == expected_hash(
        "BybitSelectedEntryProfileV1",
        {
            "revision": "BYBIT_USDT_LINEAR_ISOLATED_ONE_WAY_NEW_ENTRY_V1",
            "symbol": "BTCUSDT",
            "position_idx": 0,
            "selected_position_quantity": dh,
            "selected_open_order_ids": ("order1",),
            "selected_conditional_order_ids": (),
            "local_unresolved_entry_ids": (),
            "auto_add_margin": ah,
            "prospective_extra_margin_usdt": "1",
            "extra_margin_basis": "NEW_SELECTED_FLAT_NO_ADD_V1",
            "source_records": (sh,),
        },
    )
    for ids in (("z", "a"), ("a", "a")):
        with pytest.raises(RiskV2Rejected):
            a1.bybit_selected_profile_sha256_v1(replace(profile, selected_open_order_ids=ids))
    for sources in ((replace(source, observation_id="z"), source), (source, source)):
        with pytest.raises(RiskV2Rejected):
            a1.bybit_selected_profile_sha256_v1(replace(profile, source_records=sources))


def test_a1_fee_document_and_nonvip_document_recipes() -> None:
    document = fee_document()
    h = expected_hash(
        "BybitFeeDocumentRefV1",
        {
            "document_url": "https://www.bybit.com/en/help-center/article/Fee",
            "document_updated_at_text": "2026-11-03",
            "captured_at": "2026-11-03T00:00:00.000000+00:00",
            "raw_document_sha256": "a" * 64,
            "exact_locator": "Non-VIP derivatives: taker",
            "displayed_rate_spelling": "0.0550",
            "displayed_unit": "PERCENT",
            "account_region_profile_sha256": "b" * 64,
            "applicability_revision": "synthetic-fees-v1",
        },
    )
    assert a1.bybit_fee_document_ref_sha256_v1(document) == h
    assert a1.bybit_nonvip_fee_sha256_v1(
        BybitNonVipFeeBasisV1(Decimal("0.000550"), document)
    ) == expected_hash(
        "BybitNonVipFeeBasisV1",
        {"value": "0.000550", "reference_kind": "FEE_DOCUMENT", "reference": h},
    )


def test_a1_source_and_semantic_codec_goldens() -> None:
    time = datetime(2000, 1, 1, tzinfo=UTC)
    value = BybitModelSourceRefV1(
        "p1",
        "fixture",
        "g1",
        None,
        "FIXTURE",
        HttpsOrigin("https", "api.bybit.com", 443),
        "/v5/market/risk-limit",
        (("category", "linear"), ("symbol", "XAAUSDT")),
        time,
        time,
        946684800000,
        "0" * 64,
        1,
        1,
    )
    assert (
        a1.bybit_source_ref_sha256_v1(value)
        == "d5c899b3ef1e4dde4548a555a4180ee91f65cee584743d0e974bdc32c09c4b95"
    )
    raw = body(
        ROW.replace("BTCUSDT", "XAAUSDT")
        .replace('"100"', '"10"')
        .replace(":0.5", ":0.01")
        .replace(":0.1", ":0.005")
    )
    envelope = replace(
        read(raw),
        key=InstrumentKey("BYBIT", "LINEAR", "XAAUSDT"),
        params=(("category", "linear"), ("symbol", "XAAUSDT")),
    )
    # Codec-only zero pin value: never assemble it into a claimed full graph.
    pin = replace(a1_pin("XAAUSDT"), specification_revision="0" * 64)
    rows = normalize_bybit_tier_rows_v1(
        (decode_bybit_risk_limit_page_v1(envelope, page_ordinal=1),)
    )
    payload = a1._semantic_table_payload_v1(pin, rows)
    assert (
        _canonical_sha256_v2("BybitLinearTierTableSemanticV1", payload)
        == "728be44d0457dd75d8b6c153631f4d615eb209ab765848e8640280e529b056c6"
    )


@pytest.mark.parametrize("name", ["raw_manifest_sha256", "full_table_sha256"])
def test_a1_table_rejects_hash_substitution(name: str) -> None:
    value = a1_capture().table
    with pytest.raises(RiskV2Rejected):
        changes: dict[str, Any] = {name: "a" * 64}
        replace(value, **changes)


def test_a1_capture_replays_bytes_and_all_associations() -> None:
    capture = a1_capture()
    other = a1_capture(ROW.replace('"100"', '"101"'))
    with pytest.raises(RiskV2Rejected, match="SOURCE_MISMATCH"):
        replace(capture, raw_pages=other.raw_pages)
    # The Table lacks a raw-manifest preimage: a coherent claim can be a value.
    # Only Capture can reject this self-consistent but false raw-manifest claim.
    value = object.__new__(a1.BybitLinearTierTableV1)
    for f in fields(a1.BybitLinearTierTableV1):
        object.__setattr__(value, f.name, getattr(capture.table, f.name))
    object.__setattr__(value, "raw_manifest_sha256", "a" * 64)
    with pytest.raises(RiskV2Rejected):
        a1.bybit_risk_limit_capture_sha256_v1(a1.BybitRiskLimitCaptureV1(value, capture.raw_pages))
    for field_name, changed in (
        ("source_rule_revision", "instrument-v1"),
        ("source_id", "other"),
        ("payload_sha256", "a" * 64),
    ):
        ref_changes: dict[str, Any] = {field_name: changed}
        ref = replace(capture.table.reference, **ref_changes)
        with pytest.raises(RiskV2Rejected):
            replace(capture.table, reference=ref)
    with pytest.raises(RiskV2Rejected):
        replace(capture.table, instrument_pin=a1_pin("ETHUSDT"))
    row = capture.table.rows[0]
    for change in ({"lower_exclusive_usdt": Fraction(1)}, {"is_lowest_risk": 0}, {"risk_id": 2}):
        with pytest.raises(RiskV2Rejected):
            replace(capture.table, rows=(replace(row, **change),))
    with pytest.raises(RiskV2Rejected):
        replace(capture.table, pages=(replace(capture.table.pages[0], source_generation="other"),))


def test_a1_composed_canonical_limit_rejects_complete_large_capture() -> None:
    pages = captured_pages(64, 1024)
    assert len(normalize_bybit_tier_rows_v1(pages)) == 1024
    with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
        a1.parse_bybit_risk_limit_table_v1(
            tuple(p.envelope for p in pages), instrument_pin=a1_pin(), observation_id="large"
        )


def test_a1_digests_revalidate_forged_records_without_hooks() -> None:
    class Hostile:
        def __eq__(self, other: object) -> bool:
            raise AssertionError("foreign equality hook")

    source = private_source()
    object.__setattr__(source, "source_id", Hostile())
    with pytest.raises(RiskV2Rejected):
        a1.bybit_source_ref_sha256_v1(source)
    partial = object.__new__(a1.BybitLinearTierTableV1)
    with pytest.raises(RiskV2Rejected):
        a1.bybit_tier_table_sha256_v1(partial)
    capture = a1_capture()
    object.__setattr__(capture.table.rows[0].upper_inclusive_usdt.reference, "source", Hostile())
    with pytest.raises(RiskV2Rejected):
        a1.bybit_full_table_sha256_v1(capture.table)


def test_a1_ambient_context_and_current_labels_do_not_issue_authority() -> None:
    digit_limit = sys.get_int_max_str_digits()
    with localcontext() as ctx:
        ctx.prec = 1
        ctx.rounding = "ROUND_DOWN"
        ctx.capitals = 0
        for signal in ctx.traps:
            ctx.traps[signal] = True
        before = ctx.copy()
        value = a1_capture()
        assert len(a1.bybit_risk_limit_capture_sha256_v1(value)) == 64
        assert ctx == before or (ctx.prec, ctx.rounding, ctx.capitals, ctx.flags, ctx.traps) == (
            before.prec,
            before.rounding,
            before.capitals,
            before.flags,
            before.traps,
        )
    assert sys.get_int_max_str_digits() == digit_limit
    # Structurally valid fixture source labels remain plain closed values.
    assert value.table.reference.source_environment == "FIXTURE"
    assert not hasattr(value, "qualified")


def test_a1_raw_manifest_source_payload_table_capture_literal_maps() -> None:
    capture = a1_capture()
    raw = body()
    body_hash = hashlib.sha256(raw).hexdigest()
    source_hash = expected_hash("BybitModelSourceRefV1", expected_source(body_hash))
    raw_map: dict[str, object] = {
        "pages": (
            {
                "key": {"venue": "BYBIT", "product": "LINEAR", "symbol": "BTCUSDT"},
                "method": "GET",
                "endpoint": "/v5/market/risk-limit",
                "params": (("category", "linear"), ("symbol", "BTCUSDT")),
                "sent_origin": {"scheme": "https", "host": "api.bybit.com", "port": 443},
                "response_origin": {"scheme": "https", "host": "api.bybit.com", "port": 443},
                "response_status": 200,
                "request_started_at": STAMP,
                "received_at": STAMP,
                "started_mono_hex": "0x0.0p+0",
                "received_mono_hex": "0x1.0000000000000p+0",
                "response_time": STAMP,
                "decoded_body_sha256": body_hash,
                "decoded_body_length": len(raw),
                "accepted_attempt": 1,
                "physical_start": 0,
                "physical_end": 1,
                "logical_ordinal": 1,
                "observation_id": "obs1",
                "source_id": "source1",
                "source_generation": "gen1",
                "source_environment": "FIXTURE",
                "page_ordinal": 1,
                "cursor_in": "",
                "cursor_out": "",
            },
        )
    }
    raw_hash = expected_hash("BybitRiskLimitRawManifestV1", raw_map)
    semantic_map: dict[str, object] = {
        "instrument": {
            "key": {"venue": "BYBIT", "product": "LINEAR", "symbol": "BTCUSDT"},
            "native_instrument_id": "BTCUSDT-LINEAR.BYBIT",
            "specification_revision": "a" * 64,
        },
        "rate_encoding_revision": "BYBIT_LINEAR_RISK_NUMBER_FRACTION_V1",
        "deduction_rule_revision": "EXACT_CONTINUOUS_MM_DEDUCTION_V1",
        "rows": (
            {
                "tier_id": "1",
                "lower_exclusive_usdt": rational("0"),
                "upper_inclusive_usdt": rational("100"),
                "initial_margin_rate": rational("1", "2"),
                "maintenance_margin_rate": rational("1", "10"),
                "max_leverage": rational("2"),
                "mm_deduction_usdt": rational("0"),
                "is_lowest_risk": 1,
            },
        ),
    }
    semantic_hash = expected_hash("BybitLinearTierTableSemanticV1", semantic_map)
    source_payload_hash = expected_hash(
        "BybitLinearTierSourcePayloadV1",
        {
            "instrument_pin": expected_pin(),
            "source_rule_revision": "BYBIT_LINEAR_RISK_LIMIT_TABLE_V1",
            "raw_manifest_sha256": raw_hash,
            "full_table_sha256": semantic_hash,
        },
    )
    row_map: dict[str, object] = {
        "risk_id": 1,
        "lower_exclusive_usdt": rational("0"),
        "is_lowest_risk": 1,
    }
    for name, pointer, kind, spelling in (
        ("upper_inclusive_usdt", "riskLimitValue", "STRING", "100"),
        ("initial_margin_rate", "initialMargin", "NUMBER", "0.5"),
        ("maintenance_margin_rate", "maintenanceMargin", "NUMBER", "0.1"),
        ("max_leverage", "maxLeverage", "STRING", "2"),
        ("mm_deduction_usdt", "mmDeduction", "STRING", "0"),
    ):
        field_hash = expected_hash(
            "BybitModelFieldRefV1",
            {
                "source": source_hash,
                "json_pointer": "/result/list/0/" + pointer,
                "wire_kind": kind,
                "wire_spelling": spelling,
            },
        )
        row_map[name] = expected_hash(
            "BybitDecimalFieldV1", {"value": spelling, "reference": field_hash}
        )
    row_hash = expected_hash("BybitLinearTierRowV1", row_map)
    table_hash = expected_hash(
        "BybitLinearTierTableV1",
        {
            "instrument_pin": expected_hash("BybitInstrumentPinV1", expected_pin()),
            "reference": {
                "observation_id": "table1",
                "source_id": "source1",
                "source_environment": "FIXTURE",
                "source_rule_revision": "BYBIT_LINEAR_RISK_LIMIT_TABLE_V1",
                "server_observed_at": STAMP,
                "received_at": STAMP,
                "payload_sha256": source_payload_hash,
            },
            "pages": (source_hash,),
            "rows": (row_hash,),
            "raw_manifest_sha256": raw_hash,
            "full_table_sha256": semantic_hash,
            "rate_encoding_revision": "BYBIT_LINEAR_RISK_NUMBER_FRACTION_V1",
            "deduction_rule_revision": "EXACT_CONTINUOUS_MM_DEDUCTION_V1",
        },
    )
    assert a1.bybit_raw_manifest_sha256_v1((read(raw),)) == raw_hash
    assert a1.bybit_full_table_sha256_v1(table=capture.table) == semantic_hash
    assert a1.bybit_tier_source_payload_sha256_v1(table=capture.table) == source_payload_hash
    assert a1.bybit_tier_table_sha256_v1(capture.table) == table_hash
    assert a1.bybit_risk_limit_capture_sha256_v1(capture) == expected_hash(
        "BybitRiskLimitCaptureV1", {"table": table_hash, "raw_pages": raw_hash}
    )


def test_a1_coherent_false_raw_manifest_claim_is_rejected_by_capture() -> None:
    capture = a1_capture()
    # A syntactically valid independently rehashed Table has no raw manifest
    # preimage. It remains only a claim until Capture checks the supplied bytes.
    payload = expected_hash(
        "BybitLinearTierSourcePayloadV1",
        {
            "instrument_pin": expected_pin(),
            "source_rule_revision": "BYBIT_LINEAR_RISK_LIMIT_TABLE_V1",
            "raw_manifest_sha256": "a" * 64,
            "full_table_sha256": capture.table.full_table_sha256,
        },
    )
    false_claim = replace(
        capture.table,
        raw_manifest_sha256="a" * 64,
        reference=replace(capture.table.reference, payload_sha256=payload),
    )
    assert len(a1.bybit_tier_table_sha256_v1(false_claim)) == 64
    with pytest.raises(RiskV2Rejected, match="SOURCE_MISMATCH"):
        a1.BybitRiskLimitCaptureV1(false_claim, capture.raw_pages)
    forged = object.__new__(a1.BybitRiskLimitCaptureV1)
    object.__setattr__(forged, "table", false_claim)
    object.__setattr__(forged, "raw_pages", capture.raw_pages)
    with pytest.raises(RiskV2Rejected, match="SOURCE_MISMATCH"):
        a1.bybit_risk_limit_capture_sha256_v1(forged)


@pytest.mark.parametrize(
    "name,changed",
    [
        ("decoded_body_bytes", b"{}"),
        ("decoded_body_sha256", "a" * 64),
        ("source_generation", "copied-generation"),
        ("source_id", "copied-source"),
        ("response_origin", HttpsOrigin("https", "api-testnet.bybit.com", 443)),
        ("key", InstrumentKey("BYBIT", "LINEAR", "ETHUSDT")),
        ("response_time", datetime(1970, 1, 1, tzinfo=UTC)),
        ("received_mono", float("nan")),
        ("request_started_at", datetime(2026, 11, 3, tzinfo=UTC)),
        ("params", (("category", "linear"), ("symbol", "BTCUSDT"), ("cursor", "unseen"))),
    ],
)
def test_a1_capture_hash_replays_raw_metadata_tampering(name: str, changed: object) -> None:
    capture = a1_capture()
    object.__setattr__(capture.raw_pages[0], name, changed)
    with pytest.raises(RiskV2Rejected):
        a1.bybit_risk_limit_capture_sha256_v1(capture)


def test_a1_final_parse_rejects_duplicate_rows_and_incomplete_captured_chain() -> None:
    with pytest.raises(RiskV2Rejected):
        a1_capture(ROW + "," + ROW)
    for pages in ((read(body(cursor="remaining")),), (read(body(), 2, "unexpected"),)):
        with pytest.raises(RiskV2Rejected):
            a1.parse_bybit_risk_limit_table_v1(pages, instrument_pin=a1_pin(), observation_id="t1")
    p1 = read(body(cursor="next"))
    p2 = read(body(), 2, "other")
    with pytest.raises(RiskV2Rejected):
        a1.parse_bybit_risk_limit_table_v1((p1, p2), instrument_pin=a1_pin(), observation_id="t1")


def test_a1_raw_total_exact_limit_and_plus_one_independent_of_canonical() -> None:
    exact = tuple(p.envelope for p in captured_pages(16, 16, padded=True))
    assert sum(len(p.decoded_body_bytes) for p in exact) == 1048576
    assert (
        len(
            a1.parse_bybit_risk_limit_table_v1(
                exact, instrument_pin=a1_pin(), observation_id="exact"
            ).table.rows
        )
        == 16
    )
    pages = []
    for i, p in enumerate(captured_pages(17, 17)):
        size = 65536 if i < 15 else 32768 + int(i == 16)
        raw = p.envelope.decoded_body_bytes + b" " * (size - len(p.envelope.decoded_body_bytes))
        pages.append(
            replace(
                p.envelope,
                decoded_body_bytes=raw,
                decoded_body_sha256=hashlib.sha256(raw).hexdigest(),
            )
        )
    assert sum(len(p.decoded_body_bytes) for p in pages) == 1048577
    assert all(len(p.decoded_body_bytes) <= 65536 for p in pages)
    with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
        a1.parse_bybit_risk_limit_table_v1(
            tuple(pages), instrument_pin=a1_pin(), observation_id="plus-one"
        )


def test_a1_profile_canonical_exact_65536_and_plus_one() -> None:
    source = private_source()
    one = number_field(source)
    source_hash = expected_hash(
        "BybitModelSourceRefV1", expected_source(source.raw_body_sha256, True)
    )
    field_hash = expected_hash(
        "BybitModelFieldRefV1",
        {
            "source": source_hash,
            "json_pointer": "/result/value",
            "wire_kind": "STRING",
            "wire_spelling": "1",
        },
    )
    amount_hash = expected_hash("BybitDecimalFieldV1", {"value": "1", "reference": field_hash})
    amr = BybitModelFieldRefV1(source, "/amr", "INTEGER", "1")
    amr_hash = expected_hash(
        "BybitModelFieldRefV1",
        {
            "source": source_hash,
            "json_pointer": "/amr",
            "wire_kind": "INTEGER",
            "wire_spelling": "1",
        },
    )
    ids = [f"i{i:04d}" for i in range(700)]
    payload: dict[str, object] = {
        "revision": "BYBIT_USDT_LINEAR_ISOLATED_ONE_WAY_NEW_ENTRY_V1",
        "symbol": "BTCUSDT",
        "position_idx": 0,
        "selected_position_quantity": amount_hash,
        "selected_open_order_ids": tuple(ids),
        "selected_conditional_order_ids": (),
        "local_unresolved_entry_ids": (),
        "auto_add_margin": amr_hash,
        "prospective_extra_margin_usdt": "1",
        "extra_margin_basis": "NEW_SELECTED_FLAT_NO_ADD_V1",
        "source_records": (source_hash,),
    }

    def encoded_size() -> int:
        return len(
            json.dumps(
                {
                    "schema": "RISK_V2_CANONICAL_V1",
                    "record_type": "BybitSelectedEntryProfileV1",
                    "fields": payload,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        )

    remaining = 65536 - encoded_size()
    for i, identifier in enumerate(ids):
        extra = min(96 - len(identifier), remaining)
        ids[i] += "x" * extra
        remaining -= extra
    assert remaining == 0
    payload["selected_open_order_ids"] = tuple(ids)
    assert encoded_size() == 65536
    profile = BybitSelectedEntryProfileV1(
        "BYBIT_USDT_LINEAR_ISOLATED_ONE_WAY_NEW_ENTRY_V1",
        "BTCUSDT",
        0,
        one,
        tuple(ids),
        (),
        (),
        amr,
        Decimal(1),
        "NEW_SELECTED_FLAT_NO_ADD_V1",
        (source,),
    )
    assert a1.bybit_selected_profile_sha256_v1(profile) == expected_hash(
        "BybitSelectedEntryProfileV1", payload
    )
    # The last identifier has spare source-ID capacity; increase canonical bytes
    # by exactly one without violating ID count/order/individual length limits.
    ids[-1] += "x"
    payload["selected_open_order_ids"] = tuple(ids)
    assert encoded_size() == 65537
    with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
        a1.bybit_selected_profile_sha256_v1(replace(profile, selected_open_order_ids=tuple(ids)))


@pytest.mark.parametrize("spelling", ["1" * 256, "1e1000", "1e-1000"])
def test_a1_final_source_numeric_maxima_preserved(spelling: str) -> None:
    value = a1_capture(ROW.replace('"100"', '"' + spelling + '"'))
    assert value.table.rows[0].upper_inclusive_usdt.reference.wire_spelling == spelling
    assert value.table.rows[0].upper_inclusive_usdt.value.as_tuple() == Decimal(spelling).as_tuple()


@pytest.mark.parametrize("spelling", ["1" * 257, "1e1001", "1e-1001"])
def test_a1_final_source_numeric_overflow_rejected(spelling: str) -> None:
    with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
        a1_capture(ROW.replace('"100"', '"' + spelling + '"'))


def test_a1_every_public_digest_rejects_partial_exact_type_and_subclasses() -> None:
    entries: tuple[tuple[Any, type[Any]], ...] = (
        (a1.bybit_instrument_pin_sha256_v1, InstrumentPinV2),
        (a1.bybit_source_ref_sha256_v1, BybitModelSourceRefV1),
        (a1.bybit_field_ref_sha256_v1, BybitModelFieldRefV1),
        (a1.bybit_decimal_field_sha256_v1, BybitDecimalFieldV1),
        (a1.bybit_fee_document_ref_sha256_v1, BybitFeeDocumentRefV1),
        (a1.bybit_nonvip_fee_sha256_v1, BybitNonVipFeeBasisV1),
        (a1.bybit_uta_mode_sha256_v1, BybitUtaModeV1),
        (a1.bybit_trade_behaviour_sha256_v1, BybitTradeBehaviourV1),
        (a1.bybit_flat_leverage_sha256_v1, BybitFlatLeverageV1),
        (a1.bybit_fee_inputs_sha256_v1, BybitLinearFeeInputsV1),
        (a1.bybit_wallet_sha256_v1, BybitUsdtWalletV1),
        (a1.bybit_held_position_sha256_v1, BybitHeldPositionV1),
        (a1.bybit_held_order_sha256_v1, BybitHeldOrderV1),
        (a1.bybit_tier_row_sha256_v1, a1.BybitLinearTierRowV1),
        (a1.bybit_full_table_sha256_v1, a1.BybitLinearTierTableV1),
        (a1.bybit_tier_source_payload_sha256_v1, a1.BybitLinearTierTableV1),
        (a1.bybit_tier_table_sha256_v1, a1.BybitLinearTierTableV1),
        (a1.bybit_risk_limit_capture_sha256_v1, a1.BybitRiskLimitCaptureV1),
        (a1.bybit_selected_profile_sha256_v1, BybitSelectedEntryProfileV1),
    )
    for digest, cls in entries:
        with pytest.raises(RiskV2Rejected):
            digest(object.__new__(cls))
        subclass = type("ForeignSubclass", (cls,), {})
        with pytest.raises(RiskV2Rejected):
            digest(object.__new__(subclass))
    bad_inputs: tuple[Any, ...] = ([], (), (object(),))
    for bad in bad_inputs:
        with pytest.raises(RiskV2Rejected):
            a1.bybit_raw_manifest_sha256_v1(bad)


def test_a1_canonical_source_and_semantic_byte_literals() -> None:
    source = b'{"fields":{"accepted_attempt":1,"account_scope":null,"envelope_time_ms":946684800000,"observation_id":"p1","origin":{"host":"api.bybit.com","port":443,"scheme":"https"},"page_ordinal":1,"path":"/v5/market/risk-limit","query":[["category","linear"],["symbol","XAAUSDT"]],"raw_body_sha256":"0000000000000000000000000000000000000000000000000000000000000000","received_at":"2000-01-01T00:00:00.000000+00:00","request_started_at":"2000-01-01T00:00:00.000000+00:00","source_environment":"FIXTURE","source_generation":"g1","source_id":"fixture"},"record_type":"BybitModelSourceRefV1","schema":"RISK_V2_CANONICAL_V1"}'
    semantic = b'{"fields":{"deduction_rule_revision":"EXACT_CONTINUOUS_MM_DEDUCTION_V1","instrument":{"key":{"product":"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"native_instrument_id":"XAAUSDT-LINEAR.BYBIT","specification_revision":"0000000000000000000000000000000000000000000000000000000000000000"},"rate_encoding_revision":"BYBIT_LINEAR_RISK_NUMBER_FRACTION_V1","rows":[{"initial_margin_rate":{"denominator":"100","numerator":"1"},"is_lowest_risk":1,"lower_exclusive_usdt":{"denominator":"1","numerator":"0"},"maintenance_margin_rate":{"denominator":"200","numerator":"1"},"max_leverage":{"denominator":"1","numerator":"2"},"mm_deduction_usdt":{"denominator":"1","numerator":"0"},"tier_id":"1","upper_inclusive_usdt":{"denominator":"1","numerator":"10"}}]},"record_type":"BybitLinearTierTableSemanticV1","schema":"RISK_V2_CANONICAL_V1"}'
    assert (len(source), hashlib.sha256(source).hexdigest()) == (
        609,
        "d5c899b3ef1e4dde4548a555a4180ee91f65cee584743d0e974bdc32c09c4b95",
    )
    assert (len(semantic), hashlib.sha256(semantic).hexdigest()) == (
        825,
        "728be44d0457dd75d8b6c153631f4d615eb209ab765848e8640280e529b056c6",
    )
    # These bytes pin the codec. The preceding actual typed-graph golden test
    # separately proves production hashing consumes the same closed recipes.


def test_a1_field_source_pointer_association_and_capture_order() -> None:
    capture = a1_capture()
    row = capture.table.rows[0]
    foreign = page(ROW.replace('"100"', '"101"')).rows[0].initial_margin_rate
    with pytest.raises(RiskV2Rejected, match="SOURCE_MISMATCH"):
        replace(capture.table, rows=(replace(row, initial_margin_rate=foreign),))
    wrong_pointer = replace(
        row.initial_margin_rate.reference, json_pointer="/result/list/0/maintenanceMargin"
    )
    with pytest.raises(RiskV2Rejected, match="SOURCE_MISMATCH"):
        replace(
            capture.table,
            rows=(
                replace(
                    row,
                    initial_margin_rate=replace(row.initial_margin_rate, reference=wrong_pointer),
                ),
            ),
        )
    two = captured_pages(2, 2)
    envelopes = tuple(p.envelope for p in two)
    good = a1.parse_bybit_risk_limit_table_v1(
        envelopes, instrument_pin=a1_pin(), observation_id="table1"
    )
    with pytest.raises(RiskV2Rejected):
        replace(good, raw_pages=tuple(reversed(envelopes)))
    with pytest.raises(RiskV2Rejected):
        replace(good.table, pages=tuple(reversed(good.table.pages)))
    with pytest.raises(RiskV2Rejected):
        replace(good.table, rows=tuple(reversed(good.table.rows)))


def test_a1_raw_whitespace_and_aggregate_id_exclusion_scopes() -> None:
    capture = a1_capture()
    envelope = read(body() + b" ")
    spaced = a1.parse_bybit_risk_limit_table_v1(
        (envelope,), instrument_pin=a1_pin(), observation_id="table1"
    )
    assert spaced.table.full_table_sha256 == capture.table.full_table_sha256
    assert spaced.table.raw_manifest_sha256 != capture.table.raw_manifest_sha256
    assert a1.bybit_tier_table_sha256_v1(spaced.table) != a1.bybit_tier_table_sha256_v1(
        capture.table
    )
    renamed = a1.parse_bybit_risk_limit_table_v1(
        capture.raw_pages, instrument_pin=a1_pin(), observation_id="another-id"
    )
    assert renamed.table.raw_manifest_sha256 == capture.table.raw_manifest_sha256
    assert renamed.table.full_table_sha256 == capture.table.full_table_sha256
    assert renamed.table.reference.payload_sha256 == capture.table.reference.payload_sha256
    assert a1.bybit_tier_table_sha256_v1(renamed.table) != a1.bybit_tier_table_sha256_v1(
        capture.table
    )


def test_a1_derived_decimal_text_exceeds_global_int_string_limit_without_mutation() -> None:
    source = private_source()
    one = number_field(source)
    amr = BybitModelFieldRefV1(source, "/amr", "INTEGER", "1")
    profile = BybitSelectedEntryProfileV1(
        "BYBIT_USDT_LINEAR_ISOLATED_ONE_WAY_NEW_ENTRY_V1",
        "BTCUSDT",
        0,
        one,
        (),
        (),
        (),
        amr,
        Decimal("1" * 5001),
        "NEW_SELECTED_FLAT_NO_ADD_V1",
        (source,),
    )
    before = sys.get_int_max_str_digits()
    assert len(a1.bybit_selected_profile_sha256_v1(profile)) == 64
    with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
        a1.bybit_selected_profile_sha256_v1(
            replace(profile, prospective_extra_margin_usdt=Decimal("1" * 65536))
        )
    assert sys.get_int_max_str_digits() == before


def test_a1_capture_replays_full_rows_even_with_coherent_shortened_table_hashes() -> None:
    # Flat-rate second row has exact continuous D=0. A false Table that omits
    # this otherwise unused row can rehash all its available fields coherently.
    second = ROW.replace('"id":1', '"id":2').replace('"100"', '"200"').replace('Risk":1', 'Risk":0')
    capture = a1_capture(ROW + "," + second)
    shortened_hash = expected_hash(
        "BybitLinearTierTableSemanticV1",
        {
            "instrument": {
                "key": {"venue": "BYBIT", "product": "LINEAR", "symbol": "BTCUSDT"},
                "native_instrument_id": "BTCUSDT-LINEAR.BYBIT",
                "specification_revision": "a" * 64,
            },
            "rate_encoding_revision": "BYBIT_LINEAR_RISK_NUMBER_FRACTION_V1",
            "deduction_rule_revision": "EXACT_CONTINUOUS_MM_DEDUCTION_V1",
            "rows": (
                {
                    "tier_id": "1",
                    "lower_exclusive_usdt": rational("0"),
                    "upper_inclusive_usdt": rational("100"),
                    "initial_margin_rate": rational("1", "2"),
                    "maintenance_margin_rate": rational("1", "10"),
                    "max_leverage": rational("2"),
                    "mm_deduction_usdt": rational("0"),
                    "is_lowest_risk": 1,
                },
            ),
        },
    )
    payload = expected_hash(
        "BybitLinearTierSourcePayloadV1",
        {
            "instrument_pin": expected_pin(),
            "source_rule_revision": "BYBIT_LINEAR_RISK_LIMIT_TABLE_V1",
            "raw_manifest_sha256": capture.table.raw_manifest_sha256,
            "full_table_sha256": shortened_hash,
        },
    )
    shortened = replace(
        capture.table,
        rows=capture.table.rows[:1],
        full_table_sha256=shortened_hash,
        reference=replace(capture.table.reference, payload_sha256=payload),
    )
    assert len(shortened.rows) == 1
    with pytest.raises(RiskV2Rejected, match="SOURCE_MISMATCH"):
        a1.BybitRiskLimitCaptureV1(shortened, capture.raw_pages)
