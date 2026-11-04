"""Constructed page data never establishes original source issuance or eligibility."""

import hashlib
import importlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import ModuleType

import pytest

from multi_exchange_trader.account.bybit_readonly import BybitReadError
from multi_exchange_trader.account.instrument_envelope import HttpsOrigin
from multi_exchange_trader.execution import bybit_linear_model_inputs_v1 as a
from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected

QUERY = (("category", "linear"), ("symbol", "BTCUSDT"))
STAMP = 1767225600000
FLAT = {"symbol": "BTCUSDT", "positionIdx": 0, "side": "", "size": "0.000",
        "leverage": "7.500", "autoAddMargin": 0, "positionStatus": "Normal"}
MARK = {"symbol": "BTCUSDT", "markPrice": "91234.5000", "lastPrice": "99999.0"}


def api() -> ModuleType:
    return importlib.import_module("multi_exchange_trader.execution.bybit_entry_market_projections_v1")


def body(row: object) -> bytes:
    return json.dumps({"retCode": 0, "result": {"category": "linear", "list": [row]},
        "time": STAMP}, separators=(",", ":")).encode()


def source(raw: bytes, *, public: bool) -> a.BybitModelSourceRefV1:
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    return a.BybitModelSourceRefV1("observation", "source", "generation",
        None if public else ProtectionScope("12345", "DEMO"),
        "PUBLIC_MAINNET" if public else "DEMO",
        HttpsOrigin("https", "api.bybit.com" if public else "api-demo.bybit.com", 443),
        "/v5/market/tickers" if public else "/v5/position/list", QUERY,
        stamp, stamp, STAMP, hashlib.sha256(raw).hexdigest(), 1, 1)


def test_flat_preserves_original_leverage_size_and_auto_add_margin_references() -> None:
    raw = body(FLAT)
    ref = source(raw, public=False)
    flat, size, auto = api().project_bybit_flat_leverage_v1(ref, raw, symbol="BTCUSDT")
    assert type(flat) is a.BybitFlatLeverageV1 and flat.source is ref
    assert (flat.symbol, flat.position_idx, flat.read_path) == ("BTCUSDT", 0, "/v5/position/list")
    for field, name in ((flat.leverage, "leverage"), (size, "size")):
        assert type(field) is a.BybitDecimalFieldV1
        assert field.value.as_tuple() == Decimal(str(FLAT[name])).as_tuple()
        assert field.reference.source is ref and field.reference.wire_kind == "STRING"
        assert field.reference.json_pointer == "/result/list/0/" + name
        assert field.reference.wire_spelling == FLAT[name]
    assert type(auto) is a.BybitModelFieldRefV1 and auto.source is ref
    assert (auto.json_pointer, auto.wire_kind, auto.wire_spelling) == (
        "/result/list/0/autoAddMargin", "INTEGER", "0")


def test_mark_preserves_original_wire_and_uses_mark_not_last_price() -> None:
    raw = body(MARK)
    ref = source(raw, public=True)
    mark = api().project_bybit_mark_price_v1(ref, raw, symbol="BTCUSDT")
    assert type(mark) is a.BybitDecimalFieldV1
    assert mark.value.as_tuple() == Decimal("91234.5000").as_tuple()
    assert mark.reference.source is ref and ref.envelope_time_ms == STAMP
    assert (mark.reference.json_pointer, mark.reference.wire_kind, mark.reference.wire_spelling) == (
        "/result/list/0/markPrice", "STRING", "91234.5000")


def invoke(ref: a.BybitModelSourceRefV1, raw: object, *, public: bool) -> object:
    if public:
        return api().project_bybit_mark_price_v1(ref, raw, symbol="BTCUSDT")
    return api().project_bybit_flat_leverage_v1(ref, raw, symbol="BTCUSDT")


def test_testnet_origin_and_auto_add_one_remain_data_without_mode_approval() -> None:
    raw = body(MARK)
    ref = replace(source(raw, public=True), source_environment="PUBLIC_TESTNET",
        origin=HttpsOrigin("https", "api-testnet.bybit.com", 443))
    assert api().project_bybit_mark_price_v1(ref, raw, symbol="BTCUSDT").reference.source is ref
    raw = body({**FLAT, "autoAddMargin": 1})
    ref = source(raw, public=False)
    _, _, auto = api().project_bybit_flat_leverage_v1(ref, raw, symbol="BTCUSDT")
    assert auto.source is ref and auto.wire_spelling == "1"


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("change", ["path", "query", "raw"])
def test_original_path_query_and_exact_raw_body_are_required(public: bool, change: str) -> None:
    raw = body(MARK if public else FLAT)
    ref = source(raw, public=public)
    if change == "path":
        ref = replace(ref, path="/v5/market/orderbook" if public else "/v5/account/info")
    elif change == "query":
        ref = replace(ref, query=QUERY + (("settleCoin", "USDT"),))
    else:
        raw += b" "
    with pytest.raises(RiskV2Rejected):
        invoke(ref, raw, public=public)


@pytest.mark.parametrize("change", ["fixture", "private", "scope", "host", "live-flat"])
def test_source_environment_scope_and_origin_are_revalidated(change: str) -> None:
    public = change != "live-flat"
    raw = body(MARK if public else FLAT)
    ref = source(raw, public=public)
    if change == "fixture":
        ref = replace(ref, source_environment="FIXTURE")
    elif change == "live-flat":
        ref = replace(ref, source_environment="LIVE", account_scope=ProtectionScope("12345", "LIVE"),
            origin=HttpsOrigin("https", "api.bybit.com", 443))
    elif change == "private":
        object.__setattr__(ref, "source_environment", "DEMO")
    elif change == "scope":
        object.__setattr__(ref, "account_scope", ProtectionScope("12345", "DEMO"))
    else:
        object.__setattr__(ref, "origin", HttpsOrigin("https", "api-testnet.bybit.com", 443))
    with pytest.raises(RiskV2Rejected):
        invoke(ref, raw, public=public)


@pytest.mark.parametrize("field,value", [
    ("positionIdx", True), ("positionIdx", 1), ("size", "1"), ("size", 0),
    ("leverage", "0"), ("leverage", None), ("autoAddMargin", True),
    ("autoAddMargin", None), ("side", "Buy"), ("positionStatus", "Liq"),
])
def test_flat_requires_original_flat_one_way_wire_and_positive_leverage(
    field: str, value: object,
) -> None:
    row: dict[str, object] = dict(FLAT)
    if value is None:
        row.pop(field)
    else:
        row[field] = value
    raw = body(row)
    with pytest.raises(RiskV2Rejected):
        invoke(source(raw, public=False), raw, public=False)


@pytest.mark.parametrize("value", ["0", True, None, "NaN"])
def test_mark_requires_its_own_positive_numeric_string_without_last_price_fallback(value: object) -> None:
    row: dict[str, object] = dict(MARK)
    if value is None:
        row.pop("markPrice")
    else:
        row["markPrice"] = value
    raw = body(row)
    with pytest.raises(RiskV2Rejected):
        invoke(source(raw, public=True), raw, public=True)


@pytest.mark.parametrize("value", [None, True, str(STAMP), STAMP - 1])
def test_mark_requires_actual_integer_envelope_time_equal_to_original_source(value: object) -> None:
    envelope = {"retCode": 0, "result": {"category": "linear", "list": [MARK]}, "time": value}
    if value is None:
        envelope.pop("time")
    raw = json.dumps(envelope).encode()
    with pytest.raises(RiskV2Rejected):
        invoke(source(raw, public=True), raw, public=True)


@pytest.mark.parametrize("public,change", [
    (False, "empty"), (False, "duplicate"), (False, "symbol"),
    (True, "empty"), (True, "duplicate"), (True, "category"),
])
def test_selected_response_requires_exactly_one_linear_matching_row(public: bool, change: str) -> None:
    row = dict(MARK if public else FLAT)
    if change == "symbol":
        row["symbol"] = "ETHUSDT"
    rows = [] if change == "empty" else [row, row] if change == "duplicate" else [row]
    raw = json.dumps({"retCode": 0, "time": STAMP, "result": {
        "category": "spot" if change == "category" else "linear", "list": rows}}).encode()
    with pytest.raises(RiskV2Rejected):
        invoke(source(raw, public=public), raw, public=public)


@pytest.mark.parametrize("raw", [
    b'{"retCode":0,"result":{"category":"linear","list":[]},"time":1,"time":2}',
    b'{"retCode":false,"result":{"category":"linear","list":[]},"time":1767225600000}',
])
def test_mark_reuses_strict_duplicate_and_retcode_validation(raw: bytes) -> None:
    with pytest.raises((BybitReadError, RiskV2Rejected)):
        invoke(source(raw, public=True), raw, public=True)
