"""Page data only: synthetic source/pins confer no Session or account authority."""
import importlib
from dataclasses import replace
from decimal import Decimal
from types import ModuleType
from typing import Any

import pytest
from test_bybit_account_projections_v1 import body, source
from test_bybit_linear_model_inputs_v1 import a1_pin

from multi_exchange_trader.execution import bybit_linear_model_inputs_v1 as a
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected
from multi_exchange_trader.trading_product import InstrumentKey

POSITION_PATH = "/v5/position/list"
POSITION_QUERY = (("category", "linear"), ("settleCoin", "USDT"))
ORDER_PATH = "/v5/order/realtime"
ORDER_QUERY = (("category", "linear"), ("settleCoin", "USDT"), ("openOnly", "0"))
POSITION = {"symbol": "BTCUSDT", "positionIdx": 0, "side": "Buy", "size": "2.000",
    "avgPrice": "30000.00", "markPrice": "30100.0", "leverage": "10", "positionIM": "6000.00",
    "positionIMByMp": "6020.0", "positionMM": "300", "positionMMByMp": "301", "liqPrice": "",
    "autoAddMargin": 0, "riskId": 1, "riskLimitValue": "1000000.00", "positionStatus": "Normal",
    "isReduceOnly": False, "seq": 123, "updatedTime": "1767225600000"}
ORDER = {"symbol": "BTCUSDT", "orderId": "order-btc-1", "orderLinkId": "", "positionIdx": 0,
    "side": "Sell", "orderType": "Limit", "price": "30200.000", "qty": "2.0000",
    "leavesQty": "1.2500", "orderStatus": "PartiallyFilled", "reduceOnly": False,
    "closeOnTrigger": False, "triggerPrice": "", "updatedTime": "1767225600000"}


def api() -> ModuleType:
    return importlib.import_module("multi_exchange_trader.execution.bybit_position_order_projections_v1")


def test_positions_preserve_every_decimal_reference_and_all_original_rows() -> None:
    raw = body({"category": "linear", "nextPageCursor": "next-position", "list": [POSITION,
        {**POSITION, "symbol": "ETHUSDT", "side": "Sell", "liqPrice": "10.000"}]})
    ref = source(raw, POSITION_PATH, POSITION_QUERY)
    pins = (a1_pin(), a1_pin("ETHUSDT"))
    rows, cursor = api().project_bybit_linear_held_positions_page_v1(ref, raw, instruments=pins)
    assert cursor == "next-position" and len(rows) == 2
    assert type(rows[0]) is a.BybitHeldPositionV1 and rows[0].source is ref
    assert rows[0].instrument_pin is pins[0] and rows[1].instrument_pin is pins[1]
    for attr, wire in (("size", "size"), ("avg_price", "avgPrice"), ("mark_price", "markPrice"),
            ("leverage", "leverage"), ("position_im", "positionIM"), ("position_im_by_mp", "positionIMByMp"),
            ("position_mm", "positionMM"), ("position_mm_by_mp", "positionMMByMp"),
            ("risk_limit_value", "riskLimitValue")):
        field = getattr(rows[0], attr)
        assert field.value.as_tuple() == Decimal(str(POSITION[wire])).as_tuple()
        assert field.reference.source is ref and field.reference.wire_kind == "STRING"
        assert field.reference.json_pointer == "/result/list/0/" + wire
        assert field.reference.wire_spelling == POSITION[wire]
    assert rows[0].liq_price is None and rows[1].liq_price.reference.json_pointer == "/result/list/1/liqPrice"
    assert rows[1].liq_price.reference.wire_spelling == "10.000"
    assert (rows[0].position_idx, rows[0].side, rows[0].auto_add_margin, rows[0].risk_id,
        rows[0].position_status, rows[0].is_reduce_only, rows[0].seq, rows[0].updated_time_ms) == (
        0, "Buy", 0, 1, "Normal", False, 123, 1767225600000)


def test_orders_preserve_every_field_and_numeric_zero_trigger_spelling() -> None:
    raw = body({"category": "linear", "nextPageCursor": "", "list": [ORDER,
        {**ORDER, "orderId": "order-btc-2", "orderLinkId": "link-2", "triggerPrice": "0.00"}]})
    ref = source(raw, ORDER_PATH, ORDER_QUERY)
    pin = a1_pin()
    rows, cursor = api().project_bybit_linear_held_orders_page_v1(ref, raw, instruments=(pin,))
    assert cursor == "" and len(rows) == 2
    assert type(rows[0]) is a.BybitHeldOrderV1 and rows[0].source is ref and rows[0].instrument_pin is pin
    for attr, wire in (("price", "price"), ("quantity", "qty"), ("leaves_quantity", "leavesQty")):
        field = getattr(rows[0], attr)
        assert field.value.as_tuple() == Decimal(str(ORDER[wire])).as_tuple()
        assert field.reference.source is ref and field.reference.wire_kind == "STRING"
        assert field.reference.json_pointer == "/result/list/0/" + wire
        assert field.reference.wire_spelling == ORDER[wire]
    assert rows[0].trigger_price is None and rows[1].trigger_price.value.as_tuple() == Decimal("0.00").as_tuple()
    assert rows[1].trigger_price.reference.json_pointer == "/result/list/1/triggerPrice"
    assert rows[1].trigger_price.reference.wire_spelling == "0.00"
    assert (rows[0].order_id, rows[0].order_link_id, rows[0].position_idx, rows[0].side,
        rows[0].order_type, rows[0].status, rows[0].reduce_only, rows[0].close_on_trigger,
        rows[0].updated_time_ms) == ("order-btc-1", "", 0, "Sell", "Limit", "PartiallyFilled", False, False, 1767225600000)


def project(kind: str, result: object, *, query: tuple[tuple[str, str], ...] | None = None,
            instruments: object | None = None) -> Any:
    raw = body(result)
    path, default_query = (POSITION_PATH, POSITION_QUERY) if kind == "positions" else (ORDER_PATH, ORDER_QUERY)
    ref = source(raw, path, default_query if query is None else query)
    method = getattr(api(), f"project_bybit_linear_held_{kind}_page_v1")
    return method(ref, raw, instruments=(a1_pin(),) if instruments is None else instruments)


@pytest.mark.parametrize("kind", ["positions", "orders"])
def test_empty_page_is_data_and_returns_cursor_without_claiming_flat_account(kind: str) -> None:
    assert project(kind, {"category": "linear", "nextPageCursor": "", "list": []}, instruments=()) == ((), "")


@pytest.mark.parametrize("kind", ["positions", "orders"])
def test_explicit_fixed_limit_and_nonempty_cursor_keep_page_source_query(kind: str) -> None:
    query = (POSITION_QUERY if kind == "positions" else ORDER_QUERY) + (
        ("limit", "200" if kind == "positions" else "50"), ("cursor", "previous-page"))
    row = POSITION if kind == "positions" else ORDER
    rows, cursor = project(kind, {"category": "linear", "nextPageCursor": "next", "list": [row]}, query=query)
    assert len(rows) == 1 and cursor == "next" and rows[0].source.query == query


@pytest.mark.parametrize("kind", ["positions", "orders"])
@pytest.mark.parametrize("change", ["symbol", "category", "settle", "duplicate", "cursor", "limit"])
def test_query_cannot_narrow_whole_set_or_change_closed_values(kind: str, change: str) -> None:
    query: tuple[tuple[str, str], ...] = POSITION_QUERY if kind == "positions" else ORDER_QUERY
    if change == "symbol":
        query += (("symbol", "BTCUSDT"),)
    elif change == "category":
        query = tuple((key, "inverse" if key == "category" else value) for key, value in query)
    elif change == "settle":
        query = tuple((key, "USDC" if key == "settleCoin" else value) for key, value in query)
    elif change == "duplicate":
        query += (("category", "linear"),)
    elif change == "cursor":
        query += (("cursor", ""),)
    else:
        query += (("limit", "50" if kind == "positions" else "200"),)
    with pytest.raises(RiskV2Rejected):
        project(kind, {"category": "linear", "nextPageCursor": "", "list": []}, query=query)


def test_required_query_fields_and_open_only_zero_cannot_be_omitted_or_changed() -> None:
    result = {"category": "linear", "nextPageCursor": "", "list": []}
    with pytest.raises(RiskV2Rejected):
        project("positions", result, query=(("settleCoin", "USDT"),))
    with pytest.raises(RiskV2Rejected):
        project("orders", result, query=POSITION_QUERY + (("openOnly", "1"),))


@pytest.mark.parametrize("kind", ["positions", "orders"])
@pytest.mark.parametrize("change", ["category", "cursor", "later-row"])
def test_every_response_row_and_required_page_header_is_validated(kind: str, change: str) -> None:
    row = POSITION if kind == "positions" else ORDER
    result: dict[str, object] = {"category": "linear", "nextPageCursor": "", "list": [row]}
    if change == "category":
        result["category"] = "spot"
    elif change == "cursor":
        result["nextPageCursor"] = None
    else:
        result["list"] = [row, None]
    with pytest.raises(RiskV2Rejected):
        project(kind, result)


@pytest.mark.parametrize("kind", ["positions", "orders"])
def test_duplicate_position_key_or_order_id_is_not_silently_deduplicated(kind: str) -> None:
    row = POSITION if kind == "positions" else ORDER
    with pytest.raises(RiskV2Rejected):
        project(kind, {"category": "linear", "nextPageCursor": "", "list": [row, dict(row)]})


@pytest.mark.parametrize("kind", ["positions", "orders"])
@pytest.mark.parametrize("ambiguous", [False, True])
def test_each_row_requires_exactly_one_original_matching_pin(kind: str, ambiguous: bool) -> None:
    row = POSITION if kind == "positions" else ORDER
    pin = a1_pin()
    pins = (pin, replace(pin)) if ambiguous else (a1_pin("ETHUSDT"),)
    with pytest.raises(RiskV2Rejected):
        project(kind, {"category": "linear", "nextPageCursor": "", "list": [row]}, instruments=pins)


def test_pin_intrinsic_validation_is_reused_without_inventing_contract_metadata() -> None:
    pin = a1_pin()
    object.__setattr__(pin, "key", InstrumentKey("BYBIT", "SPOT", "BTCUSDT"))
    with pytest.raises(RiskV2Rejected):
        project("positions", {"category": "linear", "nextPageCursor": "", "list": [POSITION]}, instruments=(pin,))


@pytest.mark.parametrize("field,value", [
    ("positionIdx", True), ("isReduceOnly", "false"), ("seq", "123"), ("updatedTime", "\u0661"),
    ("size", "0"), ("markPrice", "NaN"), ("positionIM", None), ("liqPrice", None),
])
def test_positions_never_coerce_types_default_missing_fields_or_drop_zero_size(field: str, value: object) -> None:
    row = dict(POSITION)
    if value is None:
        row.pop(field)
    else:
        row[field] = value
    with pytest.raises(RiskV2Rejected):
        project("positions", {"category": "linear", "nextPageCursor": "", "list": [row]})


@pytest.mark.parametrize("field,value", [
    ("positionIdx", "0"), ("reduceOnly", 0), ("updatedTime", 1767225600000), ("qty", 1.25),
    ("price", ""), ("orderLinkId", None), ("orderStatus", "Filled"), ("triggerPrice", None),
])
def test_orders_never_coerce_types_or_default_missing_or_non_open_rows(field: str, value: object) -> None:
    row = dict(ORDER)
    if value is None:
        row.pop(field)
    else:
        row[field] = value
    with pytest.raises(RiskV2Rejected):
        project("orders", {"category": "linear", "nextPageCursor": "", "list": [row]})


@pytest.mark.parametrize("kind", ["positions", "orders"])
def test_raw_hash_must_match_the_original_source(kind: str) -> None:
    raw = body({"category": "linear", "nextPageCursor": "", "list": []})
    path, query = (POSITION_PATH, POSITION_QUERY) if kind == "positions" else (ORDER_PATH, ORDER_QUERY)
    ref = source(raw, path, query)
    method = getattr(api(), f"project_bybit_linear_held_{kind}_page_v1")
    with pytest.raises(RiskV2Rejected):
        method(ref, raw + b" ", instruments=())
