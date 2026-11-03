"""Bounded raw risk values. No constructor or source label grants authority.

There is one lexer/parser here: JSON numbers are original tokens, never floats.
The existing instrument source route and its attempt numbering are unchanged.
"""

import hashlib
import math
import re
from _json import scanstring
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, NoReturn, cast

from multi_exchange_trader.account.instrument_envelope import (
    HttpsOrigin,
    InstrumentSourceError,
    _validate_origin,
)
from multi_exchange_trader.execution.risk_v2_contracts import (
    _digest_v2,
    _fields_v2,
    _identifier_v2,
    _key_payload_v2,
    _utc_v2,
)
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected
from multi_exchange_trader.trading_product import InstrumentKey

_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", re.ASCII)
_INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)", re.ASCII)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _reject_v1(code: str = "RISK_V2_INPUT_INVALID") -> NoReturn:
    raise RiskV2Rejected(code) from None


def _count_v1(value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        _reject_v1()
    return value


def _text_v1(value: object, maximum: int, *, empty: bool = True) -> str:
    if type(value) is not str or (not empty and not value):
        _reject_v1()
    # Length first, so UTF-8 allocation cannot exceed four times the bound.
    if len(value) > maximum:
        _reject_v1("RISK_V2_RESOURCE_LIMIT")
    try:
        length = len(value.encode("utf-8"))
    except UnicodeError:
        _reject_v1()
    if length > maximum:
        _reject_v1("RISK_V2_RESOURCE_LIMIT")
    return value


def _number_spelling_v1(value: object) -> str:
    """Check original source grammar and allocation bounds before Decimal/int."""
    if type(value) is not str or not value:
        _reject_v1()
    if len(value) > 1024:
        _reject_v1("RISK_V2_RESOURCE_LIMIT")
    if _NUMBER.fullmatch(value) is None:
        _reject_v1()
    mantissa, separator, exponent_text = value.lower().partition("e")
    # Strip leading zeroes just as Decimal's coefficient does, retaining zero.
    coefficient = mantissa.lstrip("-").replace(".", "").lstrip("0") or "0"
    if len(coefficient) > 256:
        _reject_v1("RISK_V2_RESOURCE_LIMIT")
    exponent = 0
    if separator:
        significant = exponent_text.lstrip("+-").lstrip("0") or "0"
        if len(significant) > 4:
            _reject_v1("RISK_V2_RESOURCE_LIMIT")
        exponent = int(significant) * (-1 if exponent_text.startswith("-") else 1)
    fractional_digits = len(mantissa.partition(".")[2])
    exponent -= fractional_digits
    if abs(exponent) > 1000 or abs(exponent + len(coefficient) - 1) > 1000:
        _reject_v1("RISK_V2_RESOURCE_LIMIT")
    return value


@dataclass(frozen=True)
class JsonNumberTokenV1:
    spelling: str

    def __post_init__(self) -> None:
        _fields_v2(self, JsonNumberTokenV1)
        _number_spelling_v1(self.spelling)


def _integer_token_v1(value: object, low: int, high: int) -> int:
    token = _fields_v2(value, JsonNumberTokenV1)["spelling"]
    if type(token) is not str:
        _reject_v1()
    if len(token) > 20:
        _reject_v1("RISK_V2_RESOURCE_LIMIT")
    if _INTEGER.fullmatch(token) is None:
        _reject_v1()
    return _count_v1(int(token), low, high)


def _origin_v1(value: HttpsOrigin) -> None:
    try:
        _validate_origin(value)
    except InstrumentSourceError:
        _reject_v1("RISK_V2_SOURCE_MISMATCH")


def _query_v1(value: object) -> tuple[tuple[str, str], ...]:
    if type(value) is not tuple:
        _reject_v1()
    if len(value) > 64:
        _reject_v1("RISK_V2_RESOURCE_LIMIT")
    for pair in value:
        if type(pair) is not tuple or len(pair) != 2:
            _reject_v1()
        _text_v1(pair[0], 1024, empty=False)
        _text_v1(pair[1], 1024)
    return cast(tuple[tuple[str, str], ...], value)


@dataclass(frozen=True)
class BybitRiskLimitEnvelopeReadV1:
    key: InstrumentKey
    method: Literal["GET"]
    endpoint: Literal["/v5/market/risk-limit"]
    params: tuple[tuple[str, str], ...]
    sent_origin: HttpsOrigin
    response_origin: HttpsOrigin
    response_status: int
    request_started_at: datetime
    received_at: datetime
    started_mono: float
    received_mono: float
    response_time: datetime
    decoded_body_bytes: bytes = field(repr=False)
    decoded_body_sha256: str
    accepted_attempt: int
    physical_start: int
    physical_end: int
    logical_ordinal: int
    observation_id: str
    source_id: str
    source_generation: str
    source_environment: Literal["PUBLIC_MAINNET", "PUBLIC_TESTNET", "FIXTURE"]

    def __post_init__(self) -> None:
        validate_bybit_risk_limit_envelope_v1(self)
        for name in ("request_started_at", "received_at", "response_time"):
            object.__setattr__(self, name, _utc_v2(object.__getattribute__(self, name)))


def validate_bybit_risk_limit_envelope_v1(value: BybitRiskLimitEnvelopeReadV1) -> None:
    _validated_risk_body_v1(value)


def _validated_risk_body_v1(value: BybitRiskLimitEnvelopeReadV1) -> dict[str, object]:
    _fields_v2(value, BybitRiskLimitEnvelopeReadV1)
    key = _key_payload_v2(value.key)
    if key["product"] != "LINEAR":
        _reject_v1("RISK_V2_UNSUPPORTED_PRODUCT")
    if (
        type(value.method) is not str
        or value.method != "GET"
        or type(value.endpoint) is not str
        or value.endpoint != "/v5/market/risk-limit"
    ):
        _reject_v1()
    query = _query_v1(value.params)
    if (
        len(query) not in (2, 3)
        or query[:2] != (("category", "linear"), ("symbol", key["symbol"]))
        or (len(query) == 3 and (query[2][0] != "cursor" or not query[2][1]))
    ):
        _reject_v1()
    _count_v1(value.response_status, 200, 200)
    _origin_v1(value.sent_origin)
    _origin_v1(value.response_origin)
    if value.sent_origin != value.response_origin:
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    for item in (value.observation_id, value.source_id, value.source_generation):
        _identifier_v2(item)
    if type(value.source_environment) is not str or value.source_environment not in (
        "PUBLIC_MAINNET",
        "PUBLIC_TESTNET",
        "FIXTURE",
    ):
        _reject_v1()
    hosts = {"PUBLIC_MAINNET": "api.bybit.com", "PUBLIC_TESTNET": "api-testnet.bybit.com"}
    if value.response_origin.host not in ("api.bybit.com", "api-testnet.bybit.com") or (
        value.source_environment != "FIXTURE"
        and value.response_origin.host != hosts[value.source_environment]
    ):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    start, received, response = (
        _utc_v2(t) for t in (value.request_started_at, value.received_at, value.response_time)
    )
    for mono in (value.started_mono, value.received_mono):
        if type(mono) is not float or not math.isfinite(mono) or mono < 0:
            _reject_v1()
    if start > received or response > received or value.started_mono > value.received_mono:
        _reject_v1("RISK_V2_CAUSAL_ORDER_INVALID")
    for count in (value.physical_start, value.physical_end, value.logical_ordinal):
        _count_v1(count, 0, 2**31 - 1)
    _count_v1(value.accepted_attempt, 1, 2**31 - 1)
    if value.physical_end < value.physical_start:
        _reject_v1()
    body = _decode_risk_limit_json_v1(value.decoded_body_bytes)
    _digest_v2(value.decoded_body_sha256)
    if hashlib.sha256(value.decoded_body_bytes).hexdigest() != value.decoded_body_sha256:
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    if (
        not {"retCode", "result", "time"}
        <= body.keys()
        <= {"retCode", "retMsg", "result", "retExtInfo", "time"}
    ):
        _reject_v1()
    if ("retMsg" in body and type(body["retMsg"]) is not str) or (
        "retExtInfo" in body and type(body["retExtInfo"]) is not dict
    ):
        _reject_v1()
    _integer_token_v1(body["retCode"], 0, 0)
    stamp = _integer_token_v1(body["time"], 1, 253402300799999)
    if _EPOCH + timedelta(milliseconds=stamp) != response:
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    result = body["result"]
    if type(result) is not dict or result.keys() != {"category", "list", "nextPageCursor"}:
        _reject_v1()
    if type(result["category"]) is not str or result["category"] != "linear":
        _reject_v1()
    if type(result["list"]) is not list:
        _reject_v1()
    _text_v1(result["nextPageCursor"], 1024)
    return body


def _decode_risk_limit_json_v1(raw: bytes) -> dict[str, object]:
    """One bounded JSON lexer/parser. No callbacks, number reconstruction or I/O."""
    if type(raw) is not bytes or not raw:
        _reject_v1()
    if len(raw) > 65536:
        _reject_v1("RISK_V2_RESOURCE_LIMIT")
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        _reject_v1()
    length = len(text)
    position = 0
    nodes = 0

    def whitespace() -> None:
        nonlocal position
        while position < length and text[position] in " \t\r\n":
            position += 1

    def string() -> str:
        nonlocal position
        # scanstring is only the standard JSON string unescaper, not a second
        # JSON tree decoder; input allocation is already bounded by raw length.
        try:
            result, position = scanstring(text, position + 1, True)
        except ValueError:
            _reject_v1()
        _text_v1(result, 65536)
        return result

    def parse(depth: int) -> object:
        nonlocal nodes, position
        if nodes >= 8192:
            _reject_v1("RISK_V2_RESOURCE_LIMIT")
        nodes += 1
        whitespace()
        if position >= length:
            _reject_v1()
        char = text[position]
        if char == '"':
            return string()
        if char in "{[":
            if depth >= 32:
                _reject_v1("RISK_V2_RESOURCE_LIMIT")
            position += 1
            whitespace()
            end = "}" if char == "{" else "]"
            mapping: dict[str, object] = {}
            items: list[object] = []
            if position < length and text[position] == end:
                position += 1
                return mapping if char == "{" else items
            while True:
                whitespace()
                if char == "{":
                    if len(mapping) >= 64:
                        _reject_v1("RISK_V2_RESOURCE_LIMIT")
                    if position >= length or text[position] != '"':
                        _reject_v1()
                    key = string()
                    if key in mapping:
                        _reject_v1()
                    whitespace()
                    if position >= length or text[position] != ":":
                        _reject_v1()
                    position += 1
                    mapping[key] = parse(depth + 1)
                else:
                    items.append(parse(depth + 1))
                whitespace()
                if position >= length:
                    _reject_v1()
                separator = text[position]
                position += 1
                if separator == end:
                    return mapping if char == "{" else items
                if separator != ",":
                    _reject_v1()
        for literal, value in (("true", True), ("false", False), ("null", None)):
            if text.startswith(literal, position):
                position += len(literal)
                return value
        start = position
        # Bound token scan before a regex match/slice or numeric allocation.
        while position < length and text[position] in "0123456789.eE+-":
            position += 1
            if position - start > 1024:
                _reject_v1("RISK_V2_RESOURCE_LIMIT")
        if position == start:
            _reject_v1()
        return JsonNumberTokenV1(text[start:position])

    tree = parse(0)
    whitespace()
    if position != length or type(tree) is not dict:
        _reject_v1()
    return cast(dict[str, object], tree)
