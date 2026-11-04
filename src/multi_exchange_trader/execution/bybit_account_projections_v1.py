"""Raw-to-model data only; an actual Session consumer must verify source authority."""

import hashlib
from decimal import Decimal

from ..account.bybit_readonly import _parse_json
from ..account.risk_limit_envelope import _number_spelling_v1, _reject_v1
from .bybit_linear_model_inputs_v1 import (
    BybitDecimalFieldV1,
    BybitModelFieldRefV1,
    BybitModelSourceRefV1,
    BybitTradeBehaviourV1,
    BybitUsdtWalletV1,
    _symbol_v1,
    bybit_source_ref_sha256_v1,
)
from .protection import ProtectionScope


def _result(
    source: BybitModelSourceRefV1, raw_body: bytes, *, path: str,
    queries: tuple[tuple[tuple[str, str], ...], ...],
) -> dict[str, object]:
    # Reuse complete source14 validation; a valid shape/hash does not prove issuance.
    bybit_source_ref_sha256_v1(source)
    if (type(raw_body) is not bytes or source.source_environment != "DEMO"
            or type(source.account_scope) is not ProtectionScope
            or source.account_scope.environment != "DEMO" or source.path != path
            or source.query not in queries
            or hashlib.sha256(raw_body).hexdigest() != source.raw_body_sha256):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    envelope = _parse_json(raw_body)
    if (type(envelope) is not dict or type(envelope.get("retCode")) is not int
            or envelope["retCode"] != 0 or type(envelope.get("result")) is not dict):
        _reject_v1()
    result: dict[str, object] = envelope["result"]
    return result


def _rows(value: object) -> list[dict[str, object]]:
    if type(value) is not list:
        _reject_v1()
    rows: list[dict[str, object]] = []
    for row in value:
        if type(row) is not dict:
            _reject_v1()
        rows.append(row)
    return rows


def _decimal(
    row: dict[str, object], key: str, source: BybitModelSourceRefV1, pointer: str,
) -> BybitDecimalFieldV1:
    spelling = _number_spelling_v1(row.get(key))
    return BybitDecimalFieldV1(Decimal(spelling),
        BybitModelFieldRefV1(source, pointer + "/" + key, "STRING", spelling))


def project_bybit_usdt_wallet_v1(
    source: BybitModelSourceRefV1, raw_body: bytes,
) -> BybitUsdtWalletV1:
    """Select USDT data, without claiming whole-account coverage or absent liabilities."""
    result = _result(source, raw_body, path="/v5/account/wallet-balance",
        queries=((("accountType", "UNIFIED"),), (("accountType", "UNIFIED"), ("coin", "USDT"))))
    accounts = _rows(result.get("list"))
    if (len(accounts) != 1 or type(accounts[0].get("accountType")) is not str
            or accounts[0]["accountType"] != "UNIFIED"):
        _reject_v1()
    selected: list[tuple[int, dict[str, object]]] = []
    for index, row in enumerate(_rows(accounts[0].get("coin"))):
        if type(row.get("coin")) is not str:
            _reject_v1()
        if row["coin"] == "USDT":
            selected.append((index, row))
    if len(selected) != 1:
        _reject_v1()
    index, row = selected[0]
    pointer = f"/result/list/0/coin/{index}"
    return BybitUsdtWalletV1(source,
        _decimal(row, "walletBalance", source, pointer), _decimal(row, "equity", source, pointer),
        _decimal(row, "totalPositionIM", source, pointer), _decimal(row, "totalOrderIM", source, pointer),
        _decimal(row, "locked", source, pointer), _decimal(row, "bonus", source, pointer),
        _decimal(row, "borrowAmount", source, pointer), _decimal(row, "spotBorrow", source, pointer),
        _decimal(row, "accruedInterest", source, pointer), _decimal(row, "unrealisedPnl", source, pointer))


def project_bybit_linear_fee_rates_v1(
    source: BybitModelSourceRefV1, raw_body: bytes, *, symbol: str,
) -> tuple[BybitDecimalFieldV1, BybitDecimalFieldV1]:
    """Return account taker/maker data, without inventing a non-VIP document basis."""
    _symbol_v1(symbol)
    result = _result(source, raw_body, path="/v5/account/fee-rate",
        queries=((("category", "linear"), ("symbol", symbol)),))
    # Bybit documents no result.category for derivatives; if supplied, it must agree.
    if "category" in result and (type(result["category"]) is not str or result["category"] != "linear"):
        _reject_v1()
    selected: list[tuple[int, dict[str, object]]] = []
    for index, row in enumerate(_rows(result.get("list"))):
        if type(row.get("symbol")) is not str:
            _reject_v1()
        if row["symbol"] == symbol:
            selected.append((index, row))
    if len(selected) != 1:
        _reject_v1()
    index, row = selected[0]
    pointer = f"/result/list/{index}"
    return (_decimal(row, "takerFeeRate", source, pointer),
            _decimal(row, "makerFeeRate", source, pointer))


def project_bybit_trade_behaviour_v1(
    source: BybitModelSourceRefV1, raw_body: bytes,
) -> BybitTradeBehaviourV1:
    """Project exact wire booleans; an omitted deltaEnable stays unknown."""
    result = _result(source, raw_body, path="/v5/account/user-setting-config", queries=((),))
    lpa, deduction = result.get("lpaPerp"), result.get("fmsef")
    if type(lpa) is not bool or type(deduction) is not bool:
        _reject_v1()
    delta: bool | None = None
    if "deltaEnable" in result:
        supplied_delta = result["deltaEnable"]
        if type(supplied_delta) is not bool:
            _reject_v1()
        delta = supplied_delta
    return BybitTradeBehaviourV1(source, lpa, deduction, delta)
