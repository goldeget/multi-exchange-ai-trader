"""Held-page data only, without Session issuance, metadata or account-wide authority.

Wire: https://bybit-exchange.github.io/docs/v5/position and /v5/order/open-order.
Pins retain intrinsic identity; actual contract kind/settle metadata still require
the original catalog/specification and financial-model producer validation.
"""

from ..account.risk_limit_envelope import _count_v1, _query_v1, _reject_v1, _text_v1
from .bybit_account_projections_v1 import _decimal, _result, _rows
from .bybit_linear_model_inputs_v1 import (
    BybitDecimalFieldV1,
    BybitHeldOrderV1,
    BybitHeldPositionV1,
    BybitModelSourceRefV1,
    _symbol_v1,
)
from .risk_v2_contracts import InstrumentPinV2, _pin_payload_v2


def _page(
    source: BybitModelSourceRefV1, raw_body: bytes, *, orders: bool,
) -> tuple[list[dict[str, object]], str]:
    if type(source) is not BybitModelSourceRefV1:
        _reject_v1()
    query = _query_v1(source.query)
    fixed = {"category": "linear", "settleCoin": "USDT"}
    if orders:
        fixed["openOnly"] = "0"
    limit = "50" if orders else "200"
    params: dict[str, str] = {}
    for key, value in query:
        if key in params or key not in (*fixed, "limit", "cursor"):
            _reject_v1("RISK_V2_SOURCE_MISMATCH")
        if key in fixed and value != fixed[key] or key == "limit" and value != limit:
            _reject_v1("RISK_V2_SOURCE_MISMATCH")
        if key == "cursor":
            _text_v1(value, 1024, empty=False)
        params[key] = value
    if any(params.get(key) != value for key, value in fixed.items()):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    result = _result(source, raw_body,
        path="/v5/order/realtime" if orders else "/v5/position/list", queries=(query,))
    if type(result.get("category")) is not str or result["category"] != "linear":
        _reject_v1()
    cursor = _text_v1(result.get("nextPageCursor"), 1024)
    rows = _rows(result.get("list"))
    if len(rows) > int(params.get("limit", "20")):
        _reject_v1("RISK_V2_RESOURCE_LIMIT")
    return rows, cursor


def _pins(instruments: tuple[InstrumentPinV2, ...]) -> dict[str, InstrumentPinV2]:
    if type(instruments) is not tuple:
        _reject_v1()
    by_symbol: dict[str, InstrumentPinV2] = {}
    for pin in instruments:
        _pin_payload_v2(pin)
        if pin.key.venue != "BYBIT" or pin.key.product != "LINEAR" or pin.key.symbol in by_symbol:
            _reject_v1("RISK_V2_SCOPE_MISMATCH")
        by_symbol[pin.key.symbol] = pin
    return by_symbol


def _pin(row: dict[str, object], pins: dict[str, InstrumentPinV2]) -> InstrumentPinV2:
    symbol = _text_v1(row.get("symbol"), 48, empty=False)
    _symbol_v1(symbol)
    if symbol not in pins:
        _reject_v1("RISK_V2_SCOPE_MISMATCH")
    return pins[symbol]


def _literal(row: dict[str, object], name: str, choices: tuple[str, ...]) -> str:
    value = _text_v1(row.get(name), 1024, empty=False)
    if value not in choices:
        _reject_v1()
    return value


def _boolean(row: dict[str, object], name: str) -> bool:
    value = row.get(name)
    if type(value) is not bool:
        _reject_v1()
    return value


def _updated_time(row: dict[str, object]) -> int:
    value = _text_v1(row.get("updatedTime"), 15, empty=False)
    if not value.isascii() or not value.isdigit():
        _reject_v1()
    return _count_v1(int(value), 1, 253402300799999)


def _optional_decimal(
    row: dict[str, object], name: str, source: BybitModelSourceRefV1, pointer: str,
) -> BybitDecimalFieldV1 | None:
    value = row.get(name)
    if type(value) is not str:
        _reject_v1()
    return None if value == "" else _decimal(row, name, source, pointer)


def project_bybit_linear_held_positions_page_v1(
    source: BybitModelSourceRefV1, raw_body: bytes, *, instruments: tuple[InstrumentPinV2, ...],
) -> tuple[tuple[BybitHeldPositionV1, ...], str]:
    """Project every held-page row; no issuer, metadata or full-pagination proof."""
    rows, cursor = _page(source, raw_body, orders=False)
    pins = _pins(instruments)
    result: list[BybitHeldPositionV1] = []
    seen: set[tuple[str, int]] = set()
    for index, row in enumerate(rows):
        pin = _pin(row, pins)
        position_idx = _count_v1(row.get("positionIdx"), 0, 2)
        key = pin.key.symbol, position_idx
        if key in seen:
            _reject_v1()
        seen.add(key)
        pointer = f"/result/list/{index}"
        result.append(BybitHeldPositionV1(source, pin, position_idx,
            _literal(row, "side", ("Buy", "Sell")),
            _decimal(row, "size", source, pointer), _decimal(row, "avgPrice", source, pointer),
            _decimal(row, "markPrice", source, pointer), _decimal(row, "leverage", source, pointer),
            _decimal(row, "positionIM", source, pointer), _decimal(row, "positionIMByMp", source, pointer),
            _decimal(row, "positionMM", source, pointer), _decimal(row, "positionMMByMp", source, pointer),
            _optional_decimal(row, "liqPrice", source, pointer),
            _count_v1(row.get("autoAddMargin"), 0, 1), _count_v1(row.get("riskId"), 0, 2**63 - 1),
            _decimal(row, "riskLimitValue", source, pointer),
            _literal(row, "positionStatus", ("Normal", "Liq", "Adl")),
            _boolean(row, "isReduceOnly"), _count_v1(row.get("seq"), -(2**63), 2**63 - 1),
            _updated_time(row)))
    return tuple(result), cursor


def project_bybit_linear_held_orders_page_v1(
    source: BybitModelSourceRefV1, raw_body: bytes, *, instruments: tuple[InstrumentPinV2, ...],
) -> tuple[tuple[BybitHeldOrderV1, ...], str]:
    """Project every open-page row; no issuer, metadata or full-pagination proof."""
    rows, cursor = _page(source, raw_body, orders=True)
    pins = _pins(instruments)
    result: list[BybitHeldOrderV1] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        pin = _pin(row, pins)
        order_id = _text_v1(row.get("orderId"), 96, empty=False)
        if order_id in seen:
            _reject_v1()
        seen.add(order_id)
        pointer = f"/result/list/{index}"
        result.append(BybitHeldOrderV1(source, order_id, _text_v1(row.get("orderLinkId"), 96), pin,
            _count_v1(row.get("positionIdx"), 0, 2), _literal(row, "side", ("Buy", "Sell")),
            _literal(row, "orderType", ("Limit", "Market")),
            _decimal(row, "price", source, pointer), _decimal(row, "qty", source, pointer),
            _decimal(row, "leavesQty", source, pointer),
            _literal(row, "orderStatus", ("New", "PartiallyFilled", "Untriggered")),
            _boolean(row, "reduceOnly"), _boolean(row, "closeOnTrigger"),
            _optional_decimal(row, "triggerPrice", source, pointer), _updated_time(row)))
    return tuple(result), cursor
