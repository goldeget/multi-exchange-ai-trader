"""Hand-calculated raw-source and nonmonotonic continuity fixtures."""

import hashlib
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Any

import pytest

from multi_exchange_trader.account.instrument_envelope import HttpsOrigin
from multi_exchange_trader.account.risk_limit_envelope import BybitRiskLimitEnvelopeReadV1
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
from multi_exchange_trader.execution.risk_v2_contracts import InstrumentPinV2
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
