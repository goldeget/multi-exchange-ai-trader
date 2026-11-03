"""Bounded immutable source evidence. Values alone grant no current authority."""

import hashlib
import json
import math
import re
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta, timezone
from typing import NoReturn, Self, cast

from ..market_data.instrument_catalog import InstrumentCatalog, _validate_catalog
from ..market_data.instrument_definition import (
    INSTRUMENT_ENDPOINT,
    CatalogRejected,
    SourceEnvironment,
    _canonical_json_bytes,
    _key_payload,
    parse_bybit_instrument_response,
)
from ..trading_product import InstrumentKey, bybit_category

_PREFIX = "INSTRUMENT_SOURCE_"
_CODES = frozenset(_PREFIX + name for name in (
    "INPUT_INVALID", "RESOURCE_LIMIT", "SOURCE_UNSUPPORTED", "ORIGIN_MISMATCH",
    "RESPONSE_INVALID", "JSON_INVALID", "ENCODING_UNSUPPORTED", "PROVIDER_REJECTED",
    "HTTP_REJECTED", "ACCESS_RESTRICTED", "RATE_LIMITED", "NETWORK", "TIMEOUT",
    "BUDGET_REQUIRED", "BUDGET_EXHAUSTED", "GROUP_REQUIRED", "CLOCK_INVALID", "EXPIRED",
    "RETIRED", "CLOSED", "OVERLAP", "LOOP_REQUIRED", "RECEIPT_REQUIRED", "INJECTED_TRANSPORT",
    "SELECTION_INVALID", "NATIVE_MISMATCH", "NATIVE_SCOPE", "NOT_ARMED", "INCOMPLETE",
    "NATIVE_CAPACITY",
))
_HOSTS = frozenset({"api.bybit.com", "api-testnet.bybit.com", "api-demo.bybit.com"})
_DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)
_SOURCE_ID = re.compile(r"[0-9a-f]{32}", re.ASCII)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_REGION_PHRASES = (
    "restricted location",
    "block access from your country",
    "your region",
    "your country",
    "not available in your",
)


def _region_message(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = value.lower()
    return any(phrase in text for phrase in _REGION_PHRASES)


class InstrumentSourceError(ValueError):
    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _CODES:
            raise ValueError("INVALID_INSTRUMENT_SOURCE_ERROR_CODE")
        self.code = code
        super().__init__(code)


def _reject(name: str) -> NoReturn:
    raise InstrumentSourceError(_PREFIX + name)


def catalog_error_code(error: CatalogRejected) -> str:
    """Translate a frozen fixed catalog rejection without copying exception text."""
    return {
        "CATALOG_INPUT_INVALID": _PREFIX + "INPUT_INVALID",
        "CATALOG_SELECTION_INVALID": _PREFIX + "SELECTION_INVALID",
        "CATALOG_SOURCE_MISMATCH": _PREFIX + "ORIGIN_MISMATCH",
        "CATALOG_TIME_INVALID": _PREFIX + "CLOCK_INVALID",
        "CATALOG_EXPIRED": _PREFIX + "EXPIRED",
        "CATALOG_RESOURCE_LIMIT": _PREFIX + "RESOURCE_LIMIT",
        "CATALOG_PROVIDER_REJECTED": _PREFIX + "PROVIDER_REJECTED",
    }.get(error.code, _PREFIX + "RESPONSE_INVALID")


def _record(value: object, expected: type[object]) -> None:
    if type(value) is not expected:
        _reject("RESPONSE_INVALID")
    missing = False
    try:
        for item in fields(expected):  # type: ignore[arg-type]  # trusted class metadata
            object.__getattribute__(value, item.name)
    except AttributeError:
        missing = True
    if missing:
        _reject("RESPONSE_INVALID")


def _utc(value: datetime) -> datetime:
    if (type(value) is not datetime or type(value.tzinfo) is not timezone
            or value.utcoffset() is None):
        _reject("CLOCK_INVALID")
    normalized: datetime | None = None
    try:
        normalized = value.astimezone(UTC)
    except OverflowError:
        pass
    if normalized is None:
        _reject("CLOCK_INVALID")
    return normalized


def _mono(value: float) -> None:
    if type(value) is not float or not math.isfinite(value) or value < 0:
        _reject("CLOCK_INVALID")


def _integer(value: int, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


@dataclass(frozen=True)
class HttpsOrigin:
    scheme: str
    host: str
    port: int

    def __post_init__(self) -> None:
        _validate_origin(self)


def _validate_origin(value: HttpsOrigin) -> None:
    _record(value, HttpsOrigin)
    if (type(value.scheme) is not str or value.scheme != "https"
            or type(value.host) is not str or value.host not in _HOSTS
            or type(value.port) is not int or value.port != 443):
        _reject("ORIGIN_MISMATCH")


def _decode(raw: bytes) -> tuple[dict[str, object], bytes, datetime]:
    if type(raw) is not bytes or not raw:
        _reject("JSON_INVALID")
    if len(raw) > 65536:
        _reject("RESOURCE_LIMIT")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    def integer(text: str) -> int:
        if len(text) > 20:
            raise ValueError("integer")
        number = int(text)
        if abs(number) > 2**63 - 1:
            raise ValueError("integer")
        return number

    def forbidden(_: str) -> NoReturn:
        raise ValueError("number")

    code: str | None = None
    body: object = None
    try:
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_int=integer,
                          parse_float=forbidden, parse_constant=forbidden)
    except (ValueError, UnicodeError, RecursionError):
        code = _PREFIX + "JSON_INVALID"
    if code is not None:
        raise InstrumentSourceError(code)
    try:
        canonical = _canonical_json_bytes(body)
    except CatalogRejected as error:
        code = (_PREFIX + "RESOURCE_LIMIT" if error.code == "CATALOG_RESOURCE_LIMIT"
                else _PREFIX + "JSON_INVALID")
    if code is not None:
        raise InstrumentSourceError(code)
    if type(body) is not dict:
        _reject("RESPONSE_INVALID")
    envelope = cast(dict[str, object], body)
    ret = envelope.get("retCode")
    if type(ret) is not int:
        _reject("RESPONSE_INVALID")
    if ret == 10006:
        _reject("RATE_LIMITED")
    if ret != 0:
        _reject("PROVIDER_REJECTED")
    if _region_message(envelope.get("retMsg")):
        _reject("ACCESS_RESTRICTED")
    if type(envelope.get("result")) is not dict:
        _reject("RESPONSE_INVALID")
    stamp = envelope.get("time")
    if type(stamp) is not int or not 1 <= stamp <= 253402300799999:
        _reject("RESPONSE_INVALID")
    return envelope, canonical, _EPOCH + timedelta(milliseconds=stamp)


def decode_instrument_envelope(raw: bytes) -> tuple[dict[str, object], bytes, datetime]:
    """Strict original-envelope decoder, not a row decoder or source issuer."""
    return _decode(raw)


@dataclass(frozen=True)
class InstrumentEnvelopeRead:
    key: InstrumentKey
    method: str
    endpoint: str
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
    canonical_envelope_bytes: bytes = field(repr=False)
    accepted_attempt: int
    physical_start: int
    physical_end: int
    logical_ordinal: int

    def __post_init__(self) -> None:
        validate_instrument_envelope_read(self)
        for name in ("request_started_at", "received_at", "response_time"):
            object.__setattr__(self, name, _utc(object.__getattribute__(self, name)))


def validate_instrument_envelope_read(value: InstrumentEnvelopeRead) -> None:
    _validate_envelope(value)


def _validate_envelope(
    value: InstrumentEnvelopeRead,
    decoded: tuple[dict[str, object], bytes, datetime] | None = None,
) -> None:
    _record(value, InstrumentEnvelopeRead)
    code: str | None = None
    try:
        _key_payload(value.key)
    except CatalogRejected as error:
        code = catalog_error_code(error)
    if code is not None:
        raise InstrumentSourceError(code)
    if (type(value.method) is not str or value.method != "GET"
            or type(value.endpoint) is not str or value.endpoint != INSTRUMENT_ENDPOINT
            or type(value.params) is not tuple
            or any(type(pair) is not tuple or len(pair) != 2
                   or any(type(part) is not str for part in pair) for pair in value.params)
            or value.params != (("category", bybit_category(value.key.product)),
                                ("symbol", value.key.symbol))
            or not _integer(value.response_status, 200, 200)):
        _reject("RESPONSE_INVALID")
    _validate_origin(value.sent_origin)
    _validate_origin(value.response_origin)
    if value.sent_origin != value.response_origin:
        _reject("ORIGIN_MISMATCH")
    start, receive, response = map(_utc, (
        value.request_started_at, value.received_at, value.response_time))
    _mono(value.started_mono)
    _mono(value.received_mono)
    if start > receive or response > receive or value.started_mono > value.received_mono:
        _reject("CLOCK_INVALID")
    if (not _integer(value.accepted_attempt, 0, 2)
            or not _integer(value.physical_start, 0, 200)
            or not _integer(value.physical_end, 0, 200)
            or not 1 <= value.physical_end - value.physical_start <= 3
            or not _integer(value.logical_ordinal, 1, 32)):
        _reject("RESPONSE_INVALID")
    if type(value.canonical_envelope_bytes) is bytes and len(value.canonical_envelope_bytes) > 65536:
        _reject("RESOURCE_LIMIT")
    body, canonical, stamp = _decode(value.decoded_body_bytes) if decoded is None else decoded
    if (type(value.decoded_body_sha256) is not str
            or _DIGEST.fullmatch(value.decoded_body_sha256) is None
            or hashlib.sha256(value.decoded_body_bytes).hexdigest() != value.decoded_body_sha256
            or type(value.canonical_envelope_bytes) is not bytes
            or value.canonical_envelope_bytes != canonical or stamp != response
            or cast(dict[str, object], body["result"]).get("category")
            != bybit_category(value.key.product)):
        _reject("RESPONSE_INVALID")


def _accepted_envelope_read(
    *, key: InstrumentKey, sent_origin: HttpsOrigin, response_origin: HttpsOrigin,
    request_started_at: datetime, received_at: datetime, started_mono: float,
    received_mono: float, raw: bytes, accepted_attempt: int, physical_start: int,
    physical_end: int, logical_ordinal: int,
) -> tuple[InstrumentEnvelopeRead, dict[str, object], int]:
    """One strict raw decode shared by the trusted route's immutable/tuple projections."""
    decoded = _decode(raw)
    body, canonical, stamp = decoded
    values = (
        key, "GET", INSTRUMENT_ENDPOINT,
        (("category", bybit_category(key.product)), ("symbol", key.symbol)),
        sent_origin, response_origin, 200, request_started_at, received_at,
        started_mono, received_mono, stamp, raw, hashlib.sha256(raw).hexdigest(),
        canonical, accepted_attempt, physical_start, physical_end, logical_ordinal,
    )
    value = object.__new__(InstrumentEnvelopeRead)
    for item, part in zip(fields(InstrumentEnvelopeRead), values, strict=True):
        object.__setattr__(value, item.name, part)
    _validate_envelope(value, decoded)
    return value, cast(dict[str, object], body["result"]), cast(int, body["time"])


@dataclass(frozen=True)
class CatalogReceiptView:
    catalog: InstrumentCatalog
    source_environment: SourceEnvironment
    source_id: str
    envelopes: tuple[InstrumentEnvelopeRead, ...]
    issued_at: datetime
    issued_mono: float

    def __post_init__(self) -> None:
        validate_catalog_receipt_view(self)
        object.__setattr__(self, "issued_at", _utc(self.issued_at))


def validate_catalog_receipt_view(value: CatalogReceiptView) -> None:
    _record(value, CatalogReceiptView)
    code: str | None = None
    try:
        _validate_catalog(value.catalog)
    except CatalogRejected as error:
        code = catalog_error_code(error)
    if code is not None:
        raise InstrumentSourceError(code)
    if (type(value.source_environment) is not str
            or value.source_environment not in ("PUBLIC_MAINNET", "PUBLIC_TESTNET")
            or type(value.source_id) is not str or _SOURCE_ID.fullmatch(value.source_id) is None
            or type(value.envelopes) is not tuple or not 1 <= len(value.envelopes) <= 16
            or len(value.envelopes) != len(value.catalog.observations)):
        _reject("RESPONSE_INVALID")
    issued = _utc(value.issued_at)
    _mono(value.issued_mono)
    host = ("api.bybit.com" if value.source_environment == "PUBLIC_MAINNET"
            else "api-testnet.bybit.com")
    for envelope, observation in zip(value.envelopes, value.catalog.observations, strict=True):
        validate_instrument_envelope_read(envelope)
        if (envelope.key != observation.key or envelope.response_origin.host != host
                or observation.source.source_environment != value.source_environment
                or observation.source.source_id != value.source_id
                or observation.source.received_at != envelope.received_at
                or observation.response_time != envelope.response_time
                or envelope.received_at > issued or envelope.received_mono > value.issued_mono):
            _reject("RESPONSE_INVALID")
        body, _, _ = _decode(envelope.decoded_body_bytes)
        try:
            derived = parse_bybit_instrument_response(
                body, expected=envelope.key, source=observation.source)
        except CatalogRejected as error:
            code = catalog_error_code(error)
        if code is not None:
            raise InstrumentSourceError(code)
        if derived != observation:
            _reject("RESPONSE_INVALID")


class CurrentCatalogReceipt:
    """Opaque identity. Its issuer's current registry is the only authority."""

    __slots__ = ()

    def __new__(cls) -> Self:
        _reject("RECEIPT_REQUIRED")

    def __repr__(self) -> str:
        return "CurrentCatalogReceipt()"


def _new_current_catalog_receipt() -> CurrentCatalogReceipt:
    """Allocate identity only; no current/source authority is created here."""
    return object.__new__(CurrentCatalogReceipt)
