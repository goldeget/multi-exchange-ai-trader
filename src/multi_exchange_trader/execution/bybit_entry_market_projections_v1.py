"""Selected-flat/mark data only; no original issuer, metadata or financial authority.

Wire: https://bybit-exchange.github.io/docs/v5/position and /v5/market/tickers.
The original source publisher and financial consumer must separately establish
issuance, currentness and eligibility; these constructible DTOs grant none.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ..account.bybit_readonly import _parse_json
from ..account.risk_limit_envelope import _count_v1, _number_spelling_v1, _reject_v1
from .bybit_account_projections_v1 import _decimal, _result, _rows
from .bybit_linear_model_inputs_v1 import (
    BybitDecimalFieldV1,
    BybitFlatLeverageV1,
    BybitModelFieldRefV1,
    BybitModelSourceRefV1,
    _symbol_v1,
    bybit_source_ref_sha256_v1,
)
from .risk_v2_math import decimal_fraction_v2


@dataclass(frozen=True)
class BybitLinearFundingObservationV1:
    """One public funding quote tied to the original ticker capture."""

    source: BybitModelSourceRefV1
    symbol: str
    funding_rate: BybitDecimalFieldV1
    next_funding_time: BybitModelFieldRefV1
    next_funding_at: datetime


def project_bybit_linear_funding_v1(
    source: BybitModelSourceRefV1, raw_body: bytes, *, symbol: str,
) -> BybitLinearFundingObservationV1:
    """Project a fixed-source ticker funding rate and its exact next event time."""
    _symbol_v1(symbol)
    bybit_source_ref_sha256_v1(source)
    if (type(raw_body) is not bytes or source.account_scope is not None
            or source.source_environment not in ("PUBLIC_MAINNET", "PUBLIC_TESTNET")
            or source.path != "/v5/market/tickers"
            or source.query != (("category", "linear"), ("symbol", symbol))
            or hashlib.sha256(raw_body).hexdigest() != source.raw_body_sha256):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    envelope = _parse_json(raw_body)
    if (type(envelope) is not dict or type(envelope.get("retCode")) is not int
            or envelope["retCode"] != 0 or type(envelope.get("result")) is not dict):
        _reject_v1()
    stamp = _count_v1(envelope.get("time"), 1, 253402300799999)
    if source.envelope_time_ms != stamp:
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    result: dict[str, object] = envelope["result"]
    row = _selected_row(result, symbol)
    pointer = "/result/list/0"
    funding_rate = _decimal(row, "fundingRate", source, pointer)
    spelling = _number_spelling_v1(row.get("nextFundingTime"))
    if (type(row.get("nextFundingTime")) is not str
            or re.fullmatch(r"[0-9]{1,15}", spelling, re.ASCII) is None):
        _reject_v1()
    next_ms = int(spelling)
    if next_ms <= stamp or next_ms > 253402300799999:
        _reject_v1()
    next_funding_at = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=next_ms)
    return BybitLinearFundingObservationV1(
        source,
        symbol,
        funding_rate,
        BybitModelFieldRefV1(source, pointer + "/nextFundingTime", "STRING", spelling),
        next_funding_at,
    )


def _selected_row(result: dict[str, object], symbol: str) -> dict[str, object]:
    if type(result.get("category")) is not str or result["category"] != "linear":
        _reject_v1()
    rows = _rows(result.get("list"))
    if (len(rows) != 1 or type(rows[0].get("symbol")) is not str
            or rows[0]["symbol"] != symbol):
        _reject_v1()
    return rows[0]


def project_bybit_flat_leverage_v1(
    source: BybitModelSourceRefV1, raw_body: bytes, *, symbol: str,
) -> tuple[BybitFlatLeverageV1, BybitDecimalFieldV1, BybitModelFieldRefV1]:
    """Return actual one-way flat row data; an empty response supplies no defaults."""
    _symbol_v1(symbol)
    result = _result(source, raw_body, path="/v5/position/list",
        queries=((("category", "linear"), ("symbol", symbol)),))
    row = _selected_row(result, symbol)
    _count_v1(row.get("positionIdx"), 0, 0)
    if (type(row.get("side")) is not str or row["side"] != ""
            or type(row.get("positionStatus")) is not str or row["positionStatus"] != "Normal"):
        _reject_v1()
    pointer = "/result/list/0"
    size = _decimal(row, "size", source, pointer)
    if decimal_fraction_v2(size.value) != 0:
        _reject_v1()
    auto = _count_v1(row.get("autoAddMargin"), 0, 1)
    return (BybitFlatLeverageV1(source, symbol, 0, _decimal(row, "leverage", source, pointer),
            "/v5/position/list"), size,
        BybitModelFieldRefV1(source, pointer + "/autoAddMargin", "INTEGER", str(auto)))


def project_bybit_mark_price_v1(
    source: BybitModelSourceRefV1, raw_body: bytes, *, symbol: str,
) -> BybitDecimalFieldV1:
    """Return the public ticker mark and its original envelope time; no freshness claim."""
    _symbol_v1(symbol)
    bybit_source_ref_sha256_v1(source)
    if (type(raw_body) is not bytes or source.account_scope is not None
            or source.source_environment not in ("PUBLIC_MAINNET", "PUBLIC_TESTNET")
            or source.path != "/v5/market/tickers"
            or source.query != (("category", "linear"), ("symbol", symbol))
            or hashlib.sha256(raw_body).hexdigest() != source.raw_body_sha256):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    envelope = _parse_json(raw_body)
    if (type(envelope) is not dict or type(envelope.get("retCode")) is not int
            or envelope["retCode"] != 0 or type(envelope.get("result")) is not dict):
        _reject_v1()
    stamp = _count_v1(envelope.get("time"), 1, 253402300799999)
    if source.envelope_time_ms != stamp:
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    result: dict[str, object] = envelope["result"]
    field = _decimal(_selected_row(result, symbol), "markPrice", source, "/result/list/0")
    if decimal_fraction_v2(field.value) <= 0:
        _reject_v1()
    return field


def project_bybit_last_trade_price_v1(
    source: BybitModelSourceRefV1, raw_body: bytes, *, symbol: str,
) -> BybitDecimalFieldV1:
    """Return the public ticker lastPrice tied to its original capture."""
    _symbol_v1(symbol)
    bybit_source_ref_sha256_v1(source)
    if (type(raw_body) is not bytes or source.account_scope is not None
            or source.source_environment not in ("PUBLIC_MAINNET", "PUBLIC_TESTNET")
            or source.path != "/v5/market/tickers"
            or source.query != (("category", "linear"), ("symbol", symbol))
            or hashlib.sha256(raw_body).hexdigest() != source.raw_body_sha256):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    envelope = _parse_json(raw_body)
    if (type(envelope) is not dict or type(envelope.get("retCode")) is not int
            or envelope["retCode"] != 0 or type(envelope.get("result")) is not dict):
        _reject_v1()
    stamp = _count_v1(envelope.get("time"), 1, 253402300799999)
    if source.envelope_time_ms != stamp:
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    result: dict[str, object] = envelope["result"]
    field = _decimal(_selected_row(result, symbol), "lastPrice", source, "/result/list/0")
    if decimal_fraction_v2(field.value) <= 0:
        _reject_v1()
    return field
