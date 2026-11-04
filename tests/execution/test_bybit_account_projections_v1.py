"""Data-only offline projections; constructed sources never prove Session authority."""
import hashlib
import importlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from decimal import Decimal
from types import ModuleType

import pytest

from multi_exchange_trader.account.bybit_readonly import BybitReadError
from multi_exchange_trader.account.instrument_envelope import HttpsOrigin
from multi_exchange_trader.execution import bybit_linear_model_inputs_v1 as a
from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected

WALLET_PATH = "/v5/account/wallet-balance"
WALLET_QUERY = (("accountType", "UNIFIED"), ("coin", "USDT"))
FEE_PATH = "/v5/account/fee-rate"
FEE_QUERY = (("category", "linear"), ("symbol", "BTCUSDT"))
SETTINGS_PATH = "/v5/account/user-setting-config"
COIN = {"coin": "USDT", "walletBalance": "100.00", "equity": "99.0",
        "totalPositionIM": "5.000", "totalOrderIM": "2", "locked": "1", "bonus": "0.0",
        "borrowAmount": "3", "spotBorrow": "0.5", "accruedInterest": "0.01", "unrealisedPnl": "-1"}


def api() -> ModuleType:
    return importlib.import_module("multi_exchange_trader.execution.bybit_account_projections_v1")


def body(result: object) -> bytes:
    return json.dumps({"retCode": 0, "result": result}, separators=(",", ":")).encode()


def source(raw: bytes, path: str, query: tuple[tuple[str, str], ...]) -> a.BybitModelSourceRefV1:
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    return a.BybitModelSourceRefV1("observation", "source", "generation",
        ProtectionScope("12345", "DEMO"), "DEMO", HttpsOrigin("https", "api-demo.bybit.com", 443),
        path, query, stamp, stamp, None, hashlib.sha256(raw).hexdigest(), 1, 1)


def test_wallet_preserves_all_ten_fields_and_actual_usdt_pointer() -> None:
    raw = body({"list": [{"accountType": "UNIFIED", "coin": [{"coin": "BTC"}, COIN]}]})
    ref = source(raw, WALLET_PATH, WALLET_QUERY)
    result = api().project_bybit_usdt_wallet_v1(ref, raw)
    assert type(result) is a.BybitUsdtWalletV1 and result.source is ref
    for attr, wire in (
        ("wallet_balance", "walletBalance"), ("equity", "equity"),
        ("total_position_im", "totalPositionIM"), ("total_order_im", "totalOrderIM"),
        ("locked", "locked"), ("bonus", "bonus"), ("borrow_amount", "borrowAmount"),
        ("spot_borrow", "spotBorrow"), ("accrued_interest", "accruedInterest"),
        ("unrealised_pnl", "unrealisedPnl"),
    ):
        field = getattr(result, attr)
        assert field.value.as_tuple() == Decimal(COIN[wire]).as_tuple()
        assert field.reference.source is ref and field.reference.wire_kind == "STRING"
        assert field.reference.json_pointer == "/result/list/0/coin/1/" + wire
        assert field.reference.wire_spelling == COIN[wire]


def test_fee_preserves_matching_row_rate_spelling_without_nonvip_invention() -> None:
    raw = body({"list": [{"symbol": "ETHUSDT"}, {"symbol": "BTCUSDT",
        "takerFeeRate": "0.00055", "makerFeeRate": "-0.00010"}]})
    ref = source(raw, FEE_PATH, FEE_QUERY)
    taker, maker = api().project_bybit_linear_fee_rates_v1(ref, raw, symbol="BTCUSDT")
    assert type(taker) is type(maker) is a.BybitDecimalFieldV1
    assert taker.reference.source is maker.reference.source is ref
    assert (taker.reference.json_pointer, maker.reference.json_pointer) == (
        "/result/list/1/takerFeeRate", "/result/list/1/makerFeeRate")
    assert taker.reference.wire_spelling == "0.00055"
    assert maker.reference.wire_spelling == "-0.00010"
    assert taker.value == Decimal("0.00055") and maker.value.as_tuple() == Decimal("-0.00010").as_tuple()


def test_trade_behaviour_preserves_boolean_values_and_missing_delta() -> None:
    raw = body({"lpaPerp": False, "fmsef": True, "lpaSpot": True})
    ref = source(raw, SETTINGS_PATH, ())
    result = api().project_bybit_trade_behaviour_v1(ref, raw)
    assert type(result) is a.BybitTradeBehaviourV1 and result.source is ref
    assert result.lpa_perp is False and result.futures_mnt_fee_deduction is True
    assert result.delta_enable is None


@pytest.mark.parametrize("query", [(("accountType", "UNIFIED"),), WALLET_QUERY])
def test_wallet_supports_whole_wallet_or_explicit_usdt_query_without_coverage_claim(
    query: tuple[tuple[str, str], ...],
) -> None:
    raw = body({"list": [{"accountType": "UNIFIED", "coin": [COIN]}]})
    ref = source(raw, WALLET_PATH, query)
    wallet = api().project_bybit_usdt_wallet_v1(ref, raw)
    assert wallet.source is ref and wallet.borrow_amount.value == Decimal(3)
    assert wallet.spot_borrow.value == Decimal("0.5")
    assert wallet.wallet_balance.reference.json_pointer == "/result/list/0/coin/0/walletBalance"
    before = a.bybit_source_ref_sha256_v1(ref)
    with pytest.raises(FrozenInstanceError):
        wallet.wallet_balance.value = Decimal(0)
    assert a.bybit_source_ref_sha256_v1(ref) == before
    assert wallet.wallet_balance.value == Decimal("100.00")


@pytest.mark.parametrize("field", list(COIN)[1:])
@pytest.mark.parametrize("failure", ["missing", "empty"])
def test_wallet_never_defaults_any_required_amount_to_zero(field: str, failure: str) -> None:
    coin = dict(COIN)
    if failure == "missing":
        coin.pop(field)
    else:
        coin[field] = ""
    raw = body({"list": [{"accountType": "UNIFIED", "coin": [coin]}]})
    with pytest.raises(RiskV2Rejected):
        api().project_bybit_usdt_wallet_v1(source(raw, WALLET_PATH, WALLET_QUERY), raw)


@pytest.mark.parametrize("value", [None, False, 1, 1.5, "NaN", "Infinity", "1e9999", "1 ", "\u0661"])
def test_wallet_uses_existing_strict_decimal_wire_validator(value: object) -> None:
    raw = body({"list": [{"accountType": "UNIFIED", "coin": [{**COIN, "walletBalance": value}]}]})
    with pytest.raises(RiskV2Rejected):
        api().project_bybit_usdt_wallet_v1(source(raw, WALLET_PATH, WALLET_QUERY), raw)


@pytest.mark.parametrize("accounts", [
    [], [{"accountType": "UNIFIED", "coin": [COIN]}] * 2,
    [{"accountType": "CONTRACT", "coin": [COIN]}], [None],
    [{"accountType": "UNIFIED", "coin": None}],
    [{"accountType": "UNIFIED", "coin": [{"coin": "BTC"}]}],
    [{"accountType": "UNIFIED", "coin": [COIN, COIN]}],
    [{"accountType": "UNIFIED", "coin": [None, COIN]}],
    [{"accountType": "UNIFIED", "coin": [{"coin": False}, COIN]}],
    [{"accountType": "UNIFIED", "coin": [{**COIN, "locked": "-1"}]}],
])
def test_wallet_rejects_wrong_account_duplicate_usdt_or_invalid_rows(accounts: object) -> None:
    raw = body({"list": accounts})
    with pytest.raises(RiskV2Rejected):
        api().project_bybit_usdt_wallet_v1(source(raw, WALLET_PATH, WALLET_QUERY), raw)


@pytest.mark.parametrize("rows", [
    [], [{"symbol": "ETHUSDT"}],
    [{"symbol": "BTCUSDT", "takerFeeRate": "0.001", "makerFeeRate": "0"}] * 2,
    [None], [{"symbol": True}],
    [{"symbol": "BTCUSDT", "makerFeeRate": "0"}],
    [{"symbol": "BTCUSDT", "takerFeeRate": "0.001"}],
    [{"symbol": "BTCUSDT", "takerFeeRate": 0.001, "makerFeeRate": "0"}],
    [{"symbol": "BTCUSDT", "takerFeeRate": "", "makerFeeRate": "0"}],
    [{"symbol": "BTCUSDT", "takerFeeRate": "0.001", "makerFeeRate": "NaN"}],
])
def test_fee_requires_exactly_one_matching_symbol_and_both_wire_rates(rows: object) -> None:
    raw = body({"list": rows})
    with pytest.raises(RiskV2Rejected):
        api().project_bybit_linear_fee_rates_v1(source(raw, FEE_PATH, FEE_QUERY), raw, symbol="BTCUSDT")


@pytest.mark.parametrize("category", [None, True, "spot", "inverse"])
def test_fee_rejects_an_explicit_disagreeing_response_category(category: object) -> None:
    raw = body({"category": category, "list": [{"symbol": "BTCUSDT",
        "takerFeeRate": "0.001", "makerFeeRate": "0"}]})
    with pytest.raises(RiskV2Rejected):
        api().project_bybit_linear_fee_rates_v1(source(raw, FEE_PATH, FEE_QUERY), raw, symbol="BTCUSDT")


@pytest.mark.parametrize("symbol", ["ETHUSDT", "btcusdt", "", "BTC/USDT"])
def test_fee_request_symbol_must_match_the_validated_query(symbol: str) -> None:
    raw = body({"list": [{"symbol": "BTCUSDT", "takerFeeRate": "0.001", "makerFeeRate": "0"}]})
    with pytest.raises(RiskV2Rejected):
        api().project_bybit_linear_fee_rates_v1(source(raw, FEE_PATH, FEE_QUERY), raw, symbol=symbol)


@pytest.mark.parametrize("delta", [True, False])
def test_settings_preserves_explicit_delta_boolean(delta: bool) -> None:
    raw = body({"lpaPerp": True, "fmsef": False, "deltaEnable": delta})
    ref = source(raw, SETTINGS_PATH, ())
    result = api().project_bybit_trade_behaviour_v1(ref, raw)
    assert result.source is ref and result.lpa_perp is True
    assert result.futures_mnt_fee_deduction is False and result.delta_enable is delta


@pytest.mark.parametrize("field", ["lpaPerp", "fmsef"])
@pytest.mark.parametrize("value", [None, 0, 1, "false", {}])
def test_settings_never_coerces_or_defaults_required_boolean(field: str, value: object) -> None:
    result: dict[str, object] = {"lpaPerp": False, "fmsef": False}
    result[field] = value
    raw = body(result)
    with pytest.raises(RiskV2Rejected):
        api().project_bybit_trade_behaviour_v1(source(raw, SETTINGS_PATH, ()), raw)


@pytest.mark.parametrize("missing", ["lpaPerp", "fmsef"])
def test_settings_requires_both_named_fields(missing: str) -> None:
    result: dict[str, object] = {"lpaPerp": False, "fmsef": False}
    result.pop(missing)
    raw = body(result)
    with pytest.raises(RiskV2Rejected):
        api().project_bybit_trade_behaviour_v1(source(raw, SETTINGS_PATH, ()), raw)


@pytest.mark.parametrize("delta", [None, 0, "false", []])
def test_settings_distinguishes_omitted_delta_from_invalid_present_value(delta: object) -> None:
    raw = body({"lpaPerp": False, "fmsef": False, "deltaEnable": delta})
    with pytest.raises(RiskV2Rejected):
        api().project_bybit_trade_behaviour_v1(source(raw, SETTINGS_PATH, ()), raw)


def invoke(kind: str, ref: a.BybitModelSourceRefV1, raw: object) -> None:
    if kind == "wallet":
        api().project_bybit_usdt_wallet_v1(ref, raw)
    elif kind == "fee":
        api().project_bybit_linear_fee_rates_v1(ref, raw, symbol="BTCUSDT")
    else:
        api().project_bybit_trade_behaviour_v1(ref, raw)


@pytest.mark.parametrize("kind,path,query", [
    ("wallet", WALLET_PATH, WALLET_QUERY), ("fee", FEE_PATH, FEE_QUERY),
    ("settings", SETTINGS_PATH, ()),
])
@pytest.mark.parametrize("change", ["hash", "path", "query", "environment", "scope", "bytes"])
def test_every_projection_checks_raw_source_path_query_and_demo_scope(
    kind: str, path: str, query: tuple[tuple[str, str], ...], change: str,
) -> None:
    if kind == "wallet":
        raw = body({"list": [{"accountType": "UNIFIED", "coin": [COIN]}]})
    elif kind == "fee":
        raw = body({"list": [{"symbol": "BTCUSDT", "takerFeeRate": "0.001", "makerFeeRate": "0"}]})
    else:
        raw = body({"lpaPerp": False, "fmsef": False})
    ref = source(raw, path, query)
    invoke(kind, ref, raw)  # Each refusal starts from an otherwise successful raw projection.
    candidate: object = raw
    if change == "hash":
        ref = replace(ref, raw_body_sha256="0" * 64)
    elif change == "path":
        ref = replace(ref, path="/v5/account/info")
    elif change == "query":
        ref = replace(ref, query=(("unknown", "value"),))
    elif change == "environment":
        ref = replace(ref, account_scope=ProtectionScope("12345", "LIVE"), source_environment="LIVE",
            origin=HttpsOrigin("https", "api.bybit.com", 443))
    elif change == "scope":
        object.__setattr__(ref, "account_scope", None)  # Negative data corruption only.
    else:
        candidate = bytearray(raw)
    with pytest.raises(RiskV2Rejected):
        invoke(kind, ref, candidate)


@pytest.mark.parametrize("kind,path,query", [
    ("wallet", WALLET_PATH, WALLET_QUERY), ("fee", FEE_PATH, FEE_QUERY),
    ("settings", SETTINGS_PATH, ()),
])
@pytest.mark.parametrize("raw", [
    b'[]', b'{"retCode":false,"result":{}}', b'{"retCode":0.0,"result":{}}',
    b'{"retCode":1,"result":{}}', b'{"retCode":0,"result":[]}', b'{"result":{}}',
    b'{"retCode":0,"result":{},"extra":1,"extra":2}',
    b'{"retCode":0,"result":{"extra":NaN}}', b'{broken',
])
def test_every_projection_uses_the_original_strict_decoder_and_envelope(
    kind: str, path: str, query: tuple[tuple[str, str], ...], raw: bytes,
) -> None:
    with pytest.raises((RiskV2Rejected, BybitReadError)):
        invoke(kind, source(raw, path, query), raw)


@pytest.mark.parametrize("query", [
    (), (("accountType", "UNIFIED"), ("coin", "BTC")),
    (("accountType", "UNIFIED"), ("category", "linear")),
])
def test_wallet_rejects_mismatched_or_extra_request_query(query: tuple[tuple[str, str], ...]) -> None:
    raw = body({"list": [{"accountType": "UNIFIED", "coin": [COIN]}]})
    with pytest.raises(RiskV2Rejected):
        api().project_bybit_usdt_wallet_v1(source(raw, WALLET_PATH, query), raw)
