"""Source-bound, read-only Bybit public market observations.

These values preserve transport and instrument-catalog evidence. They are not
financial approval, execution permission, or a substitute for private account
evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, NoReturn, Self, cast

from ..trading_product import InstrumentKey
from .bybit_public_contracts import CANDLE_URL, INTERVALS, TICKER_URL
from .instrument_catalog import InstrumentCatalog, _freshness
from .instrument_definition import InstrumentObservation, LinearInstrumentSpec
from .models import Candle

if TYPE_CHECKING:
    from ..account.instrument_envelope import CatalogReceiptView, CurrentCatalogReceipt
    from .instrument_producer import BybitInstrumentCatalogProducer

_HEX = re.compile(r"[0-9a-f]{64}", re.ASCII)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", re.ASCII)


class PublicObservationRejected(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _reject(reason: str) -> NoReturn:
    raise PublicObservationRejected(reason)


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _reject("public_source_time_invalid")
    return value.astimezone(UTC)


def _time(value: datetime) -> str:
    return _utc(value).isoformat()


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False).encode("ascii")).hexdigest()


def _positive(value: Decimal) -> None:
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        _reject("public_quote_invalid")


def _nonnegative(value: Decimal) -> None:
    if type(value) is not Decimal or not value.is_finite() or value < 0:
        _reject("public_quote_invalid")


class _BybitPublicReadReceiptV1:
    """Opaque in-process evidence that the exact DTO came from one trusted adapter."""

    __slots__ = ("_adapter", "_content_sha256", "_endpoint", "_kind", "_query",
                 "_raw_body_sha256", "_read", "_seal", "_source")

    _adapter: object
    _content_sha256: str
    _endpoint: str
    _kind: str
    _query: tuple[tuple[str, str], ...]
    _raw_body_sha256: str
    _read: object
    _seal: object
    _source: object

    def __new__(cls, *, seal: object, adapter: object, read: object, source: object,
                kind: str, endpoint: str, query: tuple[tuple[str, str], ...],
                raw_body_sha256: str, content_sha256: str) -> Self:
        if seal is not _READ_RECEIPT_SEAL:
            _reject("public_read_receipt_required")
        value = object.__new__(cls)
        object.__setattr__(value, "_adapter", adapter)
        object.__setattr__(value, "_read", read)
        object.__setattr__(value, "_source", source)
        object.__setattr__(value, "_kind", kind)
        object.__setattr__(value, "_endpoint", endpoint)
        object.__setattr__(value, "_query", query)
        object.__setattr__(value, "_raw_body_sha256", raw_body_sha256)
        object.__setattr__(value, "_content_sha256", content_sha256)
        object.__setattr__(value, "_seal", seal)
        return value


_READ_RECEIPT_SEAL = object()


def _read_content_payload_v1(read: object, kind: str) -> object:
    if kind == "quote" and type(read) is BybitTickerQuoteReadV1:
        return (read.symbol, str(read.bid_price), str(read.bid_size),
                str(read.ask_price), str(read.ask_size), source_ref_payload_v1(read.source))
    if kind == "candle" and type(read) is BybitCandleReadV1:
        return (read.symbol, read.interval, read.limit,
                tuple((item.timestamp.isoformat(), str(item.open), str(item.high), str(item.low),
                       str(item.close), str(item.volume), item.is_closed) for item in read.candles),
                source_ref_payload_v1(read.source))
    _reject("public_read_receipt_required")


def _issue_bybit_public_read_receipt_v1(adapter: object, read: object, *, kind: str,
                                        issuer: object) -> None:
    """Adapter-only issuance called after the real fixed-route response is parsed."""
    from .adapters.bybit import BybitAdapter

    if (type(adapter) is not BybitAdapter or issuer is not adapter._public_read_issuer_v1
            or kind not in ("quote", "candle")):
        _reject("public_read_receipt_required")
    if ((kind == "quote" and type(read) is BybitTickerQuoteReadV1)
            or (kind == "candle" and type(read) is BybitCandleReadV1)):
        dto, source = read, read.source
    else:
        _reject("public_read_receipt_required")
    _source_payload(source)
    receipt = _BybitPublicReadReceiptV1(seal=_READ_RECEIPT_SEAL, adapter=adapter, read=dto,
        source=source, kind=kind, endpoint=source.endpoint, query=source.query,
        raw_body_sha256=source.raw_body_sha256, content_sha256=_digest(_read_content_payload_v1(dto, kind)))
    object.__setattr__(dto, "_source_receipt_v1", receipt)


def require_bybit_public_read_receipt_v1(adapter: object, read: object, *, kind: str) -> None:
    """Require a receipt minted by this exact BybitAdapter for this immutable DTO."""
    from .adapters.bybit import BybitAdapter

    if type(adapter) is not BybitAdapter:
        _reject("public_read_receipt_required")
    if ((kind == "quote" and type(read) is BybitTickerQuoteReadV1)
            or (kind == "candle" and type(read) is BybitCandleReadV1)):
        source = read.source
    else:
        _reject("public_read_receipt_required")
    try:
        receipt = object.__getattribute__(read, "_source_receipt_v1")
        if (type(source) is not BybitPublicSourceRefV1
                or type(receipt) is not _BybitPublicReadReceiptV1
                or receipt._seal is not _READ_RECEIPT_SEAL or receipt._adapter is not adapter
                or receipt._read is not read or receipt._source is not source or receipt._kind != kind
                or receipt._endpoint != source.endpoint or receipt._query != source.query
                or receipt._raw_body_sha256 != source.raw_body_sha256
                or receipt._content_sha256 != _digest(_read_content_payload_v1(read, kind))):
            _reject("public_read_receipt_required")
    except (AttributeError, TypeError, ValueError):
        _reject("public_read_receipt_required")


@dataclass(frozen=True)
class BybitPublicSourceRefV1:
    """Immutable custody fields for one keyless public Bybit API response."""

    source_id: str
    source_generation: str
    observation_id: str
    source_environment: Literal["PUBLIC_MAINNET"]
    endpoint: str
    query: tuple[tuple[str, str], ...]
    request_started_at: datetime
    received_at: datetime
    server_observed_at: datetime
    raw_body_sha256: str

    def __post_init__(self) -> None:
        payload = _source_payload(self, check_id=False)
        if (type(self.observation_id) is not str or _HEX.fullmatch(self.observation_id) is None
                or self.observation_id != _digest(payload)):
            _reject("public_source_identity_invalid")
        for name in ("request_started_at", "received_at", "server_observed_at"):
            object.__setattr__(self, name, _utc(object.__getattribute__(self, name)))


def _source_payload(value: BybitPublicSourceRefV1, *, check_id: bool = True) -> dict[str, object]:
    if type(value) is not BybitPublicSourceRefV1:
        _reject("public_source_identity_invalid")
    if (_IDENTIFIER.fullmatch(value.source_id) is None
            or _IDENTIFIER.fullmatch(value.source_generation) is None
            or value.source_environment != "PUBLIC_MAINNET"
            or type(value.endpoint) is not str or not value.endpoint.startswith("https://api.bybit.com/")
            or type(value.query) is not tuple or not value.query
            or any(type(pair) is not tuple or len(pair) != 2
                   or any(type(part) is not str or not part or len(part) > 128 for part in pair)
                   for pair in value.query)
            or value.query != tuple(sorted(set(value.query)))
            or type(value.raw_body_sha256) is not str or _HEX.fullmatch(value.raw_body_sha256) is None):
        _reject("public_source_identity_invalid")
    result: dict[str, object] = {
        "source_id": value.source_id,
        "source_generation": value.source_generation,
        "source_environment": value.source_environment,
        "endpoint": value.endpoint,
        "query": value.query,
        "request_started_at": _time(value.request_started_at),
        "received_at": _time(value.received_at),
        "server_observed_at": _time(value.server_observed_at),
        "raw_body_sha256": value.raw_body_sha256,
    }
    if check_id:
        result["observation_id"] = value.observation_id
    return result


def source_ref_payload_v1(value: BybitPublicSourceRefV1) -> dict[str, object]:
    return _source_payload(value)


def source_ref_from_payload_v1(value: object) -> BybitPublicSourceRefV1:
    if type(value) is not dict:
        _reject("public_source_identity_invalid")
    # The exact-dict check establishes the container shape; the existing source
    # validator still checks every decoded field after this static payload cast.
    payload = cast(dict[str, Any], value)
    if set(payload) != {
            "source_id", "source_generation", "observation_id", "source_environment", "endpoint",
            "query", "request_started_at", "received_at", "server_observed_at", "raw_body_sha256"}:
        _reject("public_source_identity_invalid")
    try:
        query = tuple(tuple(pair) for pair in payload["query"])
        return BybitPublicSourceRefV1(
            payload["source_id"], payload["source_generation"], payload["observation_id"],
            payload["source_environment"], payload["endpoint"], query,
            datetime.fromisoformat(payload["request_started_at"]),
            datetime.fromisoformat(payload["received_at"]),
            datetime.fromisoformat(payload["server_observed_at"]), payload["raw_body_sha256"],
        )
    except (TypeError, ValueError, KeyError):
        _reject("public_source_identity_invalid")


def make_source_ref_v1(*, source_generation: str, endpoint: str, query: tuple[tuple[str, str], ...],
                       request_started_at: datetime, received_at: datetime, server_observed_at: datetime,
                       raw_body_sha256: str) -> BybitPublicSourceRefV1:
    prototype = object.__new__(BybitPublicSourceRefV1)
    for name, value in (
        ("source_id", "BYBIT_PUBLIC_V5"), ("source_generation", source_generation),
        ("source_environment", "PUBLIC_MAINNET"), ("endpoint", endpoint), ("query", query),
        ("request_started_at", _utc(request_started_at)), ("received_at", _utc(received_at)),
        ("server_observed_at", _utc(server_observed_at)), ("raw_body_sha256", raw_body_sha256),
    ):
        object.__setattr__(prototype, name, value)
    identity = _digest(_source_payload(prototype, check_id=False))
    return BybitPublicSourceRefV1("BYBIT_PUBLIC_V5", source_generation, identity, "PUBLIC_MAINNET",
        endpoint, query, request_started_at, received_at, server_observed_at, raw_body_sha256)


@dataclass(frozen=True)
class BybitCandleReadV1:
    symbol: str
    interval: str
    limit: int
    candles: tuple[Candle, ...]
    source: BybitPublicSourceRefV1
    _source_receipt_v1: object = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (self.symbol not in ("BTCUSDT", "ETHUSDT") or self.interval not in INTERVALS
                or type(self.limit) is not int or not 1 <= self.limit <= 500
                or type(self.candles) is not tuple or not 1 <= len(self.candles) <= self.limit
                or any(type(item) is not Candle for item in self.candles)):
            _reject("public_candle_source_invalid")
        query = tuple(sorted((("category", "linear"), ("interval", INTERVALS[self.interval]),
            ("limit", str(self.limit)), ("symbol", self.symbol))))
        if type(self.source) is not BybitPublicSourceRefV1 or self.source.endpoint != CANDLE_URL or self.source.query != query:
            _reject("public_candle_source_mismatch")
        _source_payload(self.source)


@dataclass(frozen=True)
class BybitTickerQuoteReadV1:
    symbol: str
    bid_price: Decimal
    bid_size: Decimal
    ask_price: Decimal
    ask_size: Decimal
    source: BybitPublicSourceRefV1
    _source_receipt_v1: object = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.symbol not in ("BTCUSDT", "ETHUSDT"):
            _reject("public_quote_scope_invalid")
        _positive(self.bid_price)
        _nonnegative(self.bid_size)
        _positive(self.ask_price)
        _nonnegative(self.ask_size)
        if self.bid_price > self.ask_price:
            _reject("public_quote_crossed")
        query = tuple(sorted((("category", "linear"), ("symbol", self.symbol))))
        if type(self.source) is not BybitPublicSourceRefV1 or self.source.endpoint != TICKER_URL or self.source.query != query:
            _reject("public_quote_source_mismatch")
        _source_payload(self.source)


@dataclass(frozen=True)
class BybitPublicQuoteSnapshotV1:
    instrument: InstrumentKey
    catalog_revision: str
    instrument_revision: str
    bid_price: Decimal
    bid_size: Decimal
    ask_price: Decimal
    ask_size: Decimal
    source: BybitPublicSourceRefV1
    valid_until: datetime
    content_sha256: str
    financial_authority: Literal[False] = field(default=False, init=False)
    trading_enabled: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        payload = _quote_snapshot_payload_v1(self)
        if self.content_sha256 != _digest(payload):
            _reject("public_quote_hash_mismatch")
        object.__setattr__(self, "valid_until", _utc(self.valid_until))


def _current_catalog_view_v1(
    producer: object, receipt: object, expected_revision: str, now: datetime
) -> CatalogReceiptView:
    """Resolve catalog data only through the producer's opaque current receipt."""
    from ..account.instrument_envelope import CatalogReceiptView, CurrentCatalogReceipt
    from .instrument_producer import BybitInstrumentCatalogProducer

    if (type(producer) is not BybitInstrumentCatalogProducer
            or type(receipt) is not CurrentCatalogReceipt
            or type(expected_revision) is not str or _HEX.fullmatch(expected_revision) is None):
        _reject("public_catalog_receipt_required")
    producer = cast(BybitInstrumentCatalogProducer, producer)
    receipt = cast(CurrentCatalogReceipt, receipt)
    try:
        view = producer.require_current(receipt, expected_revision=expected_revision)
        if (type(view) is not CatalogReceiptView or view.catalog.revision != expected_revision
                or view.source_environment != "PUBLIC_MAINNET"):
            _reject("public_catalog_invalid")
        _freshness(view.catalog, _utc(now))
    except (AttributeError, TypeError, ValueError):
        _reject("public_catalog_invalid")
    return view


def _catalog_instrument_v1(view: object, symbol: str) -> InstrumentObservation:
    from ..account.instrument_envelope import CatalogReceiptView

    if type(view) is not CatalogReceiptView:
        _reject("public_catalog_required")
    view = cast(CatalogReceiptView, view)
    catalog = view.catalog
    if type(catalog) is not InstrumentCatalog:
        _reject("public_catalog_invalid")
    try:
        if catalog.complete_for_declared_universe is not True:
            _reject("public_catalog_invalid")
    except (AttributeError, TypeError, ValueError):
        _reject("public_catalog_invalid")
    key = InstrumentKey("BYBIT", "LINEAR", symbol)
    observations = tuple(item for item in catalog.observations if item.key == key)
    if len(observations) != 1 or key not in catalog.declared_universe:
        _reject("public_catalog_scope_mismatch")
    item = observations[0]
    if (type(item.spec) is not LinearInstrumentSpec or item.spec.contract_type != "LinearPerpetual"
            or item.spec.quote_coin != "USDT" or item.spec.settle_coin != "USDT"
            or item.spec.status != "Trading"):
        _reject("public_catalog_product_unsupported")
    return item


def _source_freshness_v1(source: BybitPublicSourceRefV1, *, now: datetime,
                         max_age: timedelta) -> datetime:
    now = _utc(now)
    if type(max_age) is not timedelta or not timedelta(0) < max_age <= timedelta(minutes=5):
        _reject("public_source_ttl_invalid")
    started, received, server = (_utc(source.request_started_at), _utc(source.received_at),
        _utc(source.server_observed_at))
    if started > received or received > now or server > now:
        _reject("public_source_future")
    if received - started > timedelta(seconds=10):
        _reject("public_source_window_invalid")
    if now - received > max_age or now - server > max_age:
        _reject("public_source_stale")
    try:
        return min(received + max_age, server + max_age)
    except OverflowError:
        _reject("public_source_ttl_invalid")


def _quote_snapshot_payload_v1(value: BybitPublicQuoteSnapshotV1) -> dict[str, object]:
    if (type(value.instrument) is not InstrumentKey or value.instrument.venue != "BYBIT"
            or value.instrument.product != "LINEAR" or type(value.catalog_revision) is not str
            or _HEX.fullmatch(value.catalog_revision) is None or type(value.instrument_revision) is not str
            or _HEX.fullmatch(value.instrument_revision) is None):
        _reject("public_quote_identity_invalid")
    _positive(value.bid_price)
    _nonnegative(value.bid_size)
    _positive(value.ask_price)
    _nonnegative(value.ask_size)
    if value.bid_price > value.ask_price:
        _reject("public_quote_crossed")
    return {
        "instrument": (value.instrument.venue, value.instrument.product, value.instrument.symbol),
        "catalog_revision": value.catalog_revision,
        "instrument_revision": value.instrument_revision,
        "bid_price": str(value.bid_price), "bid_size": str(value.bid_size),
        "ask_price": str(value.ask_price), "ask_size": str(value.ask_size),
        "source": source_ref_payload_v1(value.source), "valid_until": _time(value.valid_until),
    }


def quote_snapshot_sha256_v1(value: BybitPublicQuoteSnapshotV1) -> str:
    return _digest(_quote_snapshot_payload_v1(value))


def bind_bybit_quote_snapshot_v1(read: BybitTickerQuoteReadV1, catalog_producer: object,
                                catalog_receipt: object, expected_catalog_revision: str, *,
                                adapter: object, now: datetime,
                                max_age: timedelta) -> BybitPublicQuoteSnapshotV1:
    if type(read) is not BybitTickerQuoteReadV1:
        _reject("public_quote_source_required")
    require_bybit_public_read_receipt_v1(adapter, read, kind="quote")
    BybitTickerQuoteReadV1.__post_init__(read)
    now = _utc(now)
    view = _current_catalog_view_v1(catalog_producer, catalog_receipt, expected_catalog_revision, now)
    catalog = view.catalog
    instrument = _catalog_instrument_v1(view, read.symbol)
    source_expiry = _source_freshness_v1(read.source, now=now, max_age=max_age)
    expiry = min(source_expiry, catalog.expires_at)
    if now >= expiry:
        _reject("public_source_stale")
    value = object.__new__(BybitPublicQuoteSnapshotV1)
    fields = {
        "instrument": instrument.key, "catalog_revision": catalog.revision,
        "instrument_revision": instrument.specification_revision, "bid_price": read.bid_price,
        "bid_size": read.bid_size, "ask_price": read.ask_price, "ask_size": read.ask_size,
        "source": read.source, "valid_until": expiry,
    }
    for name, child in fields.items():
        object.__setattr__(value, name, child)
    digest = _digest(_quote_snapshot_payload_v1(value))
    return BybitPublicQuoteSnapshotV1(instrument.key, catalog.revision, instrument.specification_revision,
        read.bid_price, read.bid_size, read.ask_price, read.ask_size, read.source, expiry, digest)


def validate_candle_source_binding_v1(read: BybitCandleReadV1, catalog_producer: object,
                                     catalog_receipt: object, expected_catalog_revision: str, *,
                                     adapter: object, now: datetime,
                                     max_age: timedelta) -> tuple[str, str, datetime]:
    if type(read) is not BybitCandleReadV1:
        _reject("public_candle_source_required")
    require_bybit_public_read_receipt_v1(adapter, read, kind="candle")
    BybitCandleReadV1.__post_init__(read)
    now = _utc(now)
    view = _current_catalog_view_v1(catalog_producer, catalog_receipt, expected_catalog_revision, now)
    catalog = view.catalog
    instrument = _catalog_instrument_v1(view, read.symbol)
    source_expiry = _source_freshness_v1(read.source, now=now, max_age=max_age)
    expiry = min(source_expiry, catalog.expires_at)
    if now >= expiry:
        _reject("public_source_stale")
    return catalog.revision, instrument.specification_revision, expiry
