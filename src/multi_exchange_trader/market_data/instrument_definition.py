"""Pure, caller-supplied Bybit instrument observations; no execution authority."""

import hashlib
import json
import re
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal, NoReturn, cast

from multi_exchange_trader.trading_product import (
    InstrumentKey,
    bybit_category,
    bybit_native_instrument_id,
)

PARSER_REVISION = "BYBIT_INSTRUMENT_PARSER_V1"
LINEAR_SOURCE_RULE_REVISION = "BYBIT_LINEAR_USDT_PERPETUAL_V1"
SPOT_SOURCE_RULE_REVISION = "BYBIT_SPOT_USDT_REGULAR_V1"
INSTRUMENT_ENDPOINT = "/v5/market/instruments-info"
SourceEnvironment = Literal["PUBLIC_MAINNET", "PUBLIC_TESTNET", "FIXTURE"]
_CODES = frozenset(
    {
        "CATALOG_INPUT_INVALID",
        "CATALOG_RESOURCE_LIMIT",
        "CATALOG_RESPONSE_INVALID",
        "CATALOG_PROVIDER_REJECTED",
        "CATALOG_SCOPE_MISMATCH",
        "CATALOG_INSTRUMENT_UNSUPPORTED",
        "CATALOG_FILTER_INVALID",
        "CATALOG_OBSERVATION_INVALID",
        "CATALOG_COVERAGE_INVALID",
        "CATALOG_SOURCE_MISMATCH",
        "CATALOG_TIME_INVALID",
        "CATALOG_EXPIRED",
        "CATALOG_REVISION_MISMATCH",
        "CATALOG_SELECTION_INVALID",
    }
)
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", re.ASCII)
_COIN = re.compile(r"[A-Z0-9]{1,32}", re.ASCII)
_SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)
_INTEGER = re.compile(r"(?:0|[1-9][0-9]*)", re.ASCII)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class CatalogRejected(ValueError):
    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _CODES:
            raise ValueError("INVALID_CATALOG_REJECTION_CODE")
        self.code = code
        super().__init__(code)


def _reject(code: str) -> NoReturn:
    raise CatalogRejected(code) from None


@dataclass(frozen=True)
class DecimalField:
    raw: str
    value: Decimal
    scale: int

    def __post_init__(self) -> None:
        _validate_decimal(self)


@dataclass(frozen=True)
class CatalogSource:
    source_id: str
    source_environment: SourceEnvironment
    endpoint: str
    received_at: datetime

    def __post_init__(self) -> None:
        _validate_source(self)
        object.__setattr__(self, "received_at", _utc_datetime(self.received_at))


@dataclass(frozen=True)
class KnownNotPublished:
    field: Literal["max_quote_amount"]
    source_rule_revision: str

    def __post_init__(self) -> None:
        _validate_absence(self)


@dataclass(frozen=True)
class LinearInstrumentSpec:
    base_coin: str
    quote_coin: str
    settle_coin: str
    contract_type: str
    status: str
    source_price_scale: int
    price_tick: DecimalField
    min_price: DecimalField
    max_price: DecimalField
    quantity_step: DecimalField
    min_quantity: DecimalField
    max_limit_quantity: DecimalField
    max_market_quantity: DecimalField
    min_quote_amount: DecimalField
    min_leverage: DecimalField
    max_leverage: DecimalField
    leverage_step: DecimalField
    max_quote_amount: KnownNotPublished
    is_pre_listing: bool

    def __post_init__(self) -> None:
        _validate_spec(self)


@dataclass(frozen=True)
class SpotInstrumentSpec:
    base_coin: str
    quote_coin: str
    status: str
    price_tick: DecimalField
    base_precision: DecimalField
    quote_precision: DecimalField
    min_quote_amount: DecimalField
    max_limit_quantity: DecimalField
    max_market_quantity: DecimalField
    max_quote_amount: KnownNotPublished

    def __post_init__(self) -> None:
        _validate_spec(self)


@dataclass(frozen=True)
class InstrumentObservation:
    key: InstrumentKey
    native_instrument_id: str
    parser_revision: str
    source_rule_revision: str
    specification_revision: str
    source: CatalogSource
    response_time: datetime
    raw_row_canonical: bytes = field(repr=False)
    raw_row_sha256: str
    spec: LinearInstrumentSpec | SpotInstrumentSpec

    def __post_init__(self) -> None:
        validate_instrument_observation(self)
        object.__setattr__(self, "response_time", _utc_datetime(self.response_time))


def _canonical_json_bytes(value: object) -> bytes:
    """Bound exact JSON values before serialization, without invoking input hooks."""
    count = 0
    encoded_size = 0
    path: set[int] = set()

    def add(size: int) -> None:
        nonlocal encoded_size
        encoded_size += size
        if encoded_size > 65536:
            _reject("CATALOG_RESOURCE_LIMIT")

    def walk(item: object, depth: int) -> None:
        nonlocal count
        count += 1
        if depth > 8 or count > 4096:
            _reject("CATALOG_RESOURCE_LIMIT")
        kind = type(item)
        if kind is str:
            text = cast(str, item)
            if len(text) > 128:
                _reject("CATALOG_RESOURCE_LIMIT")
            if any(0xD800 <= ord(char) <= 0xDFFF for char in text):
                _reject("CATALOG_INPUT_INVALID")
            add(len(json.dumps(text, ensure_ascii=True)))
        elif kind is bool:
            add(4 if item else 5)
        elif item is None:
            add(4)
        elif kind is int:
            integer = cast(int, item)
            if abs(integer) > 2**63 - 1:
                _reject("CATALOG_RESOURCE_LIMIT")
            add(len(str(integer)))
        elif kind is dict or kind is list:
            container = cast("dict[object, object] | list[object]", item)
            if len(container) > 128:
                _reject("CATALOG_RESOURCE_LIMIT")
            identity = id(item)
            if identity in path:
                _reject("CATALOG_INPUT_INVALID")
            path.add(identity)
            add(2 + max(0, len(container) - 1))
            if kind is dict:
                for key, child in cast(dict[object, object], item).items():
                    if type(key) is not str:
                        _reject("CATALOG_INPUT_INVALID")
                    walk(key, depth + 1)
                    add(1)
                    walk(child, depth + 1)
            else:
                for child in cast(list[object], item):
                    walk(child, depth + 1)
            path.remove(identity)
        else:
            _reject("CATALOG_INPUT_INVALID")

    walk(value, 1)
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _key_payload(key: InstrumentKey) -> dict[str, str]:
    try:
        bybit_native_instrument_id(key)
    except (ValueError, AttributeError):
        _reject("CATALOG_INPUT_INVALID")
    return {"venue": key.venue, "product": key.product, "symbol": key.symbol}


def _utc_datetime(value: datetime) -> datetime:
    if type(value) is not datetime or type(value.tzinfo) is not timezone:
        _reject("CATALOG_TIME_INVALID")
    try:
        return value.astimezone(UTC)
    except (ValueError, OverflowError):
        _reject("CATALOG_TIME_INVALID")


def _exact_fields(value: object, expected: type[object], code: str) -> dict[str, object]:
    if type(value) is not expected:
        _reject(code)
    # Only exact local dataclasses reach attribute lookup; no subclass hooks.
    try:
        return {
            item.name: object.__getattribute__(value, item.name)
            for item in fields(cast(DecimalField, value))
        }
    except AttributeError:
        _reject(code)


def _validate_decimal(value: DecimalField) -> None:
    parts = _exact_fields(value, DecimalField, "CATALOG_FILTER_INVALID")
    raw, number, scale = parts["raw"], parts["value"], parts["scale"]
    if type(raw) is not str or type(number) is not Decimal or type(scale) is not int:
        _reject("CATALOG_FILTER_INVALID")
    if len(raw) > 65:
        _reject("CATALOG_FILTER_INVALID")
    fraction = len(raw.partition(".")[2])
    if (
        _DECIMAL.fullmatch(raw) is None
        or fraction > 32
        or len(raw.replace(".", "")) > 64
        or scale != fraction
    ):
        _reject("CATALOG_FILTER_INVALID")
    if not number.is_finite() or number <= 0 or number.as_tuple() != Decimal(raw).as_tuple():
        _reject("CATALOG_FILTER_INVALID")


def _validate_source(value: CatalogSource) -> None:
    parts = _exact_fields(value, CatalogSource, "CATALOG_SOURCE_MISMATCH")
    source_id, environment, endpoint = (
        parts[name] for name in ("source_id", "source_environment", "endpoint")
    )
    if (
        type(source_id) is not str
        or _SOURCE_ID.fullmatch(source_id) is None
        or type(environment) is not str
        or environment not in ("PUBLIC_MAINNET", "PUBLIC_TESTNET", "FIXTURE")
        or type(endpoint) is not str
        or endpoint != INSTRUMENT_ENDPOINT
    ):
        _reject("CATALOG_SOURCE_MISMATCH")
    try:
        _utc_datetime(cast(datetime, parts["received_at"]))
    except CatalogRejected:
        _reject("CATALOG_SOURCE_MISMATCH")


def _validate_absence(value: KnownNotPublished) -> None:
    parts = _exact_fields(value, KnownNotPublished, "CATALOG_FILTER_INVALID")
    if (
        type(parts["field"]) is not str
        or parts["field"] != "max_quote_amount"
        or type(parts["source_rule_revision"]) is not str
        or parts["source_rule_revision"]
        not in (LINEAR_SOURCE_RULE_REVISION, SPOT_SOURCE_RULE_REVISION)
    ):
        _reject("CATALOG_FILTER_INVALID")


def _validate_spec(spec: LinearInstrumentSpec | SpotInstrumentSpec) -> None:
    kind = type(spec)
    if kind is not LinearInstrumentSpec and kind is not SpotInstrumentSpec:
        _reject("CATALOG_FILTER_INVALID")
    parts = _exact_fields(spec, kind, "CATALOG_FILTER_INVALID")
    text_fields: tuple[str, ...] = ("base_coin", "quote_coin", "status")
    if kind is LinearInstrumentSpec:
        text_fields += ("settle_coin", "contract_type")
    for name in text_fields:
        if type(parts[name]) is not str:
            _reject("CATALOG_FILTER_INVALID")
    if _COIN.fullmatch(cast(str, parts["base_coin"])) is None:
        _reject("CATALOG_FILTER_INVALID")
    if parts["quote_coin"] != "USDT" or parts["status"] != "Trading":
        _reject("CATALOG_INSTRUMENT_UNSUPPORTED")
    if kind is LinearInstrumentSpec:
        if parts["settle_coin"] != "USDT" or parts["contract_type"] != "LinearPerpetual":
            _reject("CATALOG_INSTRUMENT_UNSUPPORTED")
        if type(parts["is_pre_listing"]) is not bool:
            _reject("CATALOG_FILTER_INVALID")
        if parts["is_pre_listing"] is not False:
            _reject("CATALOG_INSTRUMENT_UNSUPPORTED")
        scale = parts["source_price_scale"]
        if type(scale) is not int or not 0 <= scale <= 32:
            _reject("CATALOG_FILTER_INVALID")
    excluded = set(text_fields) | {"max_quote_amount", "source_price_scale", "is_pre_listing"}
    for name, value in parts.items():
        if name not in excluded:
            _validate_decimal(cast(DecimalField, value))
    absence = cast(KnownNotPublished, parts["max_quote_amount"])
    _validate_absence(absence)
    rule = (
        LINEAR_SOURCE_RULE_REVISION if kind is LinearInstrumentSpec else SPOT_SOURCE_RULE_REVISION
    )
    if absence.source_rule_revision != rule:
        _reject("CATALOG_FILTER_INVALID")

    def number(name: str) -> Decimal:
        return cast(DecimalField, parts[name]).value

    caps = (number("max_limit_quantity"), number("max_market_quantity"))
    lower = (
        ("quantity_step", "min_quantity") if kind is LinearInstrumentSpec else ("base_precision",)
    )
    if any(number(name) > cap for name in lower for cap in caps):
        _reject("CATALOG_FILTER_INVALID")
    if kind is LinearInstrumentSpec and (
        number("min_price") > number("max_price")
        or number("min_leverage") > number("max_leverage")
        or number("leverage_step") > number("max_leverage")
    ):
        _reject("CATALOG_FILTER_INVALID")


def _decimal(raw: object) -> DecimalField:
    if type(raw) is not str or len(raw) > 65 or _DECIMAL.fullmatch(raw) is None:
        _reject("CATALOG_FILTER_INVALID")
    return DecimalField(raw, Decimal(raw), len(raw.partition(".")[2]))


def _mapping(value: object, code: str) -> dict[str, object]:
    if type(value) is not dict:
        _reject(code)
    return cast(dict[str, object], value)


def _required(mapping: dict[str, object], name: str, code: str) -> object:
    if name not in mapping:
        _reject(code)
    return mapping[name]


def _parse_spec(
    row: dict[str, object], key: InstrumentKey
) -> LinearInstrumentSpec | SpotInstrumentSpec:
    code = "CATALOG_FILTER_INVALID"

    def text(name: str) -> str:
        result = _required(row, name, code)
        if type(result) is not str:
            _reject(code)
        return result

    price = _mapping(_required(row, "priceFilter", code), code)
    lot = _mapping(_required(row, "lotSizeFilter", code), code)

    def numeric(mapping: dict[str, object], name: str) -> DecimalField:
        return _decimal(_required(mapping, name, code))

    common = (text("baseCoin"), text("quoteCoin"))
    status = text("status")
    if key.product == "SPOT":
        return SpotInstrumentSpec(
            *common,
            status,
            numeric(price, "tickSize"),
            numeric(lot, "basePrecision"),
            numeric(lot, "quotePrecision"),
            numeric(lot, "minOrderAmt"),
            numeric(lot, "maxLimitOrderQty"),
            numeric(lot, "maxMarketOrderQty"),
            KnownNotPublished("max_quote_amount", SPOT_SOURCE_RULE_REVISION),
        )
    scale = _required(row, "priceScale", code)
    if (
        type(scale) is not str
        or len(scale) > 2
        or _INTEGER.fullmatch(scale) is None
        or int(scale) > 32
    ):
        _reject(code)
    prelisting = _required(row, "isPreListing", code)
    if type(prelisting) is not bool:
        _reject(code)
    if row.get("preListingInfo") is not None:
        _reject("CATALOG_INSTRUMENT_UNSUPPORTED")
    leverage = _mapping(_required(row, "leverageFilter", code), code)
    return LinearInstrumentSpec(
        *common,
        text("settleCoin"),
        text("contractType"),
        status,
        int(scale),
        numeric(price, "tickSize"),
        numeric(price, "minPrice"),
        numeric(price, "maxPrice"),
        numeric(lot, "qtyStep"),
        numeric(lot, "minOrderQty"),
        numeric(lot, "maxOrderQty"),
        numeric(lot, "maxMktOrderQty"),
        numeric(lot, "minNotionalValue"),
        numeric(leverage, "minLeverage"),
        numeric(leverage, "maxLeverage"),
        numeric(leverage, "leverageStep"),
        KnownNotPublished("max_quote_amount", LINEAR_SOURCE_RULE_REVISION),
        prelisting,
    )


def _spec_payload(spec: LinearInstrumentSpec | SpotInstrumentSpec) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in fields(spec):
        value = object.__getattribute__(spec, item.name)
        if type(value) is DecimalField:
            result[item.name] = {"raw": value.raw, "value": value.raw, "scale": value.scale}
        elif type(value) is KnownNotPublished:
            result[item.name] = {
                "field": value.field,
                "source_rule_revision": value.source_rule_revision,
            }
        else:
            result[item.name] = value
    return result


def _spec_revision(key: InstrumentKey, spec: LinearInstrumentSpec | SpotInstrumentSpec) -> str:
    rule = LINEAR_SOURCE_RULE_REVISION if key.product == "LINEAR" else SPOT_SOURCE_RULE_REVISION
    payload = {
        "key": _key_payload(key),
        "native_instrument_id": bybit_native_instrument_id(key),
        "parser_revision": PARSER_REVISION,
        "source_rule_revision": rule,
        "spec": _spec_payload(spec),
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def parse_bybit_instrument_response(
    body: object, *, expected: InstrumentKey, source: CatalogSource
) -> InstrumentObservation:
    _key_payload(expected)
    _validate_source(source)
    _canonical_json_bytes(body)
    code = "CATALOG_RESPONSE_INVALID"
    envelope = _mapping(body, code)
    ret_code = _required(envelope, "retCode", code)
    if type(ret_code) is not int:
        _reject(code)
    if ret_code != 0:
        _reject("CATALOG_PROVIDER_REJECTED")
    result = _mapping(_required(envelope, "result", code), code)
    category = _required(result, "category", code)
    if type(category) is not str:
        _reject(code)
    if category != bybit_category(expected.product):
        _reject("CATALOG_SCOPE_MISMATCH")
    rows = _required(result, "list", code)
    if type(rows) is not list or len(rows) != 1:
        _reject(code)
    row = _mapping(rows[0], code)
    symbol = _required(row, "symbol", code)
    if type(symbol) is not str:
        _reject(code)
    if symbol != expected.symbol:
        _reject("CATALOG_SCOPE_MISMATCH")
    if "nextPageCursor" in result:
        cursor = result["nextPageCursor"]
        if type(cursor) is not str or cursor != "":
            _reject(code)
    timestamp = _required(envelope, "time", code)
    if type(timestamp) is not int or not 1 <= timestamp <= 253402300799999:
        _reject(code)
    response_time = _EPOCH + timedelta(milliseconds=timestamp)
    spec = _parse_spec(row, expected)
    raw = _canonical_json_bytes(row)
    rule = (
        LINEAR_SOURCE_RULE_REVISION if expected.product == "LINEAR" else SPOT_SOURCE_RULE_REVISION
    )
    return InstrumentObservation(
        expected,
        bybit_native_instrument_id(expected),
        PARSER_REVISION,
        rule,
        _spec_revision(expected, spec),
        source,
        response_time,
        raw,
        hashlib.sha256(raw).hexdigest(),
        spec,
    )


def _decode_row(raw: bytes) -> dict[str, object]:
    code = "CATALOG_OBSERVATION_INVALID"
    if type(raw) is not bytes or not raw or len(raw) > 65536:
        _reject(code)

    def integer(text: str) -> int:
        if len(text) > 20:
            _reject(code)
        result = int(text)
        if abs(result) > 2**63 - 1:
            _reject(code)
        return result

    def forbidden(text: str) -> NoReturn:
        _reject(code)

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                _reject(code)
            result[key] = value
        return result

    try:
        decoded: object = json.loads(
            raw,
            parse_int=integer,
            parse_float=forbidden,
            parse_constant=forbidden,
            object_pairs_hook=pairs,
        )
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        _reject(code)
    if _canonical_json_bytes(decoded) != raw:
        _reject(code)
    return _mapping(decoded, code)


def validate_instrument_observation(observation: InstrumentObservation) -> None:
    """Revalidate logical integrity; row bytes cannot authenticate envelope receipt."""
    code = "CATALOG_OBSERVATION_INVALID"
    parts = _exact_fields(observation, InstrumentObservation, code)
    try:
        key = cast(InstrumentKey, parts["key"])
        _key_payload(key)
        source = cast(CatalogSource, parts["source"])
        _validate_source(source)
        received_at = _utc_datetime(source.received_at)
        response_time = _utc_datetime(cast(datetime, parts["response_time"]))
        if response_time > received_at:
            _reject("CATALOG_TIME_INVALID")
        spec = cast(LinearInstrumentSpec | SpotInstrumentSpec, parts["spec"])
        _validate_spec(spec)
        expected_type = LinearInstrumentSpec if key.product == "LINEAR" else SpotInstrumentSpec
        rule = LINEAR_SOURCE_RULE_REVISION if key.product == "LINEAR" else SPOT_SOURCE_RULE_REVISION
        if type(spec) is not expected_type:
            _reject(code)
        for name in (
            "native_instrument_id",
            "parser_revision",
            "source_rule_revision",
            "specification_revision",
            "raw_row_sha256",
        ):
            if type(parts[name]) is not str:
                _reject(code)
        if (
            parts["native_instrument_id"] != bybit_native_instrument_id(key)
            or parts["parser_revision"] != PARSER_REVISION
            or parts["source_rule_revision"] != rule
            or _DIGEST.fullmatch(cast(str, parts["specification_revision"])) is None
            or _DIGEST.fullmatch(cast(str, parts["raw_row_sha256"])) is None
        ):
            _reject(code)
        raw = cast(bytes, parts["raw_row_canonical"])
        row = _decode_row(raw)
        if type(row.get("symbol")) is not str or row["symbol"] != key.symbol:
            _reject(code)
        derived = _parse_spec(row, key)
        if (
            _canonical_json_bytes(_spec_payload(derived))
            != _canonical_json_bytes(_spec_payload(spec))
            or parts["raw_row_sha256"] != hashlib.sha256(raw).hexdigest()
            or parts["specification_revision"] != _spec_revision(key, derived)
        ):
            _reject(code)
    except CatalogRejected as error:
        if error.code == "CATALOG_TIME_INVALID":
            raise
        _reject(code)
