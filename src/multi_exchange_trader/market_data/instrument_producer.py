"""Single-owner public catalog observations, with no execution authority.

Only the internal real public factory creates production clients. Injected test
owners exercise the same state machine but cannot pass production require_current.
Retained body/canonical/row bytes are bounded by 3 * 65536 * 16 per generation;
HTTPX allocations, transient trees and views retained externally are not included.
Private frozen catalog helpers are intentional reviewed dependencies, not parsers
reimplemented here. Python reflection is outside this in-process trust boundary.
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal, NoReturn

import httpx
from httpx._client import ClientState

from ..account.bybit_readonly import BybitReadOnlyClient
from ..account.instrument_envelope import (
    CatalogReceiptView,
    CurrentCatalogReceipt,
    HttpsOrigin,
    InstrumentEnvelopeRead,
    InstrumentSourceError,
    _new_current_catalog_receipt,
    catalog_error_code,
    decode_instrument_envelope,
    validate_catalog_receipt_view,
    validate_instrument_envelope_read,
)
from ..account.risk_limit_envelope import BybitRiskLimitEnvelopeReadV1
from ..execution.bybit_linear_model_inputs_v1 import (
    BybitRiskLimitCaptureV1,
    decode_bybit_risk_limit_page_v1,
    parse_bybit_risk_limit_table_v1,
)
from ..execution.monitoring_budget import (
    MonitoringBudgetError,
    MonitoringReadBudget,
    ReadGroupToken,
    ReadGroupView,
    _ReceiptBudget,
)
from ..execution.risk_v2_contracts import InstrumentPinV2, _pin_payload_v2, pin_instrument_v2
from ..execution.risk_v2_math import RiskV2Rejected
from ..trading_product import InstrumentKey
from .instrument_catalog import InstrumentCatalog, _freshness, build_instrument_catalog
from .instrument_definition import (
    INSTRUMENT_ENDPOINT,
    CatalogRejected,
    CatalogSource,
    DecimalField,
    InstrumentObservation,
    KnownNotPublished,
    LinearInstrumentSpec,
    SpotInstrumentSpec,
    _key_payload,
    parse_bybit_instrument_response,
)

_Source = Literal["PUBLIC_MAINNET", "PUBLIC_TESTNET"]


def _reject(name: str) -> NoReturn:
    raise InstrumentSourceError("INSTRUMENT_SOURCE_" + name)


def _loop() -> asyncio.AbstractEventLoop:
    result: asyncio.AbstractEventLoop | None = None
    try:
        result = asyncio.get_running_loop()
    except RuntimeError:
        pass
    if result is None:
        _reject("LOOP_REQUIRED")
    return result


def _source(value: _Source) -> _Source:
    if type(value) is not str or value not in ("PUBLIC_MAINNET", "PUBLIC_TESTNET"):
        _reject("SOURCE_UNSUPPORTED")
    return value


def _microseconds(value: timedelta) -> int:
    return (value.days * 86400 + value.seconds) * 1000000 + value.microseconds


def _budget_code(error: MonitoringBudgetError) -> str:
    return (
        "INSTRUMENT_SOURCE_GROUP_REQUIRED"
        if error.args == ("MONITORING_GROUP_REQUIRED",)
        else "INSTRUMENT_SOURCE_BUDGET_EXHAUSTED"
    )


@dataclass
class _Pending:
    generation: object
    started_at: datetime
    started_mono: float
    phase: Literal["VALIDATING", "COLLECTING", "REVOKED"] = "VALIDATING"
    keys: tuple[InstrumentKey, ...] = ()
    ttl: timedelta | None = None
    source_id: str = ""
    budget: MonitoringReadBudget | None = None
    group: ReadGroupToken | None = None
    envelopes: tuple[InstrumentEnvelopeRead, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class _Current:
    handle: CurrentCatalogReceipt
    view: CatalogReceiptView = field(repr=False)
    generation: object


@contextmanager
def _risk_receipt(budget: MonitoringReadBudget) -> Iterator[ReadGroupToken]:
    """Keep a body primary if the existing receipt's ordinary exit also fails."""
    primary: BaseException | None = None
    try:
        with budget.reuse_or_open_receipt() as token:
            try:
                yield token
            except BaseException as error:
                primary = error
                raise
    except BaseException as error:
        if primary is not None and error is not primary and isinstance(error, Exception):
            raise primary from None
        raise


type _RiskRefs = tuple[tuple[object, type[object], str, object], ...]


def _risk_public_refs(current: _Current, instrument: InstrumentObservation) -> _RiskRefs:
    """Preserve the finite actual public catalog layout; no serialization/equality hooks."""
    refs: list[tuple[object, type[object], str, object]] = []

    def record(value: object, kind: type[object], names: str) -> None:
        if type(value) is not kind:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        for name in names.split():
            child = object.__getattribute__(value, name)
            refs.append((value, kind, name, child))

    def key(value: InstrumentKey) -> None:
        record(value, InstrumentKey, "venue product symbol")

    def observation(value: InstrumentObservation) -> None:
        record(value, InstrumentObservation, "key native_instrument_id parser_revision source_rule_revision "
               "specification_revision source response_time raw_row_canonical raw_row_sha256 spec")
        key(value.key)
        record(value.source, CatalogSource, "source_id source_environment endpoint received_at")
        spec = value.spec
        decimals: tuple[DecimalField, ...]
        if type(spec) is LinearInstrumentSpec:
            record(spec, LinearInstrumentSpec, "base_coin quote_coin settle_coin contract_type status source_price_scale "
                   "price_tick min_price max_price quantity_step min_quantity max_limit_quantity max_market_quantity "
                   "min_quote_amount min_leverage max_leverage leverage_step max_quote_amount is_pre_listing")
            decimals = (spec.price_tick, spec.min_price, spec.max_price, spec.quantity_step,
                        spec.min_quantity, spec.max_limit_quantity, spec.max_market_quantity,
                        spec.min_quote_amount, spec.min_leverage, spec.max_leverage, spec.leverage_step)
        elif type(spec) is SpotInstrumentSpec:
            record(spec, SpotInstrumentSpec, "base_coin quote_coin status price_tick base_precision quote_precision "
                   "min_quote_amount max_limit_quantity max_market_quantity max_quote_amount")
            decimals = (spec.price_tick, spec.base_precision, spec.quote_precision, spec.min_quote_amount,
                        spec.max_limit_quantity, spec.max_market_quantity)
        else:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        for decimal in decimals:
            record(decimal, DecimalField, "raw value scale")
        record(spec.max_quote_amount, KnownNotPublished, "field source_rule_revision")

    record(current, _Current, "handle view generation")
    if type(current.handle) is not CurrentCatalogReceipt or type(current.generation) is not object:
        _reject("RECEIPT_REQUIRED")
    view = current.view
    record(view, CatalogReceiptView, "catalog source_environment source_id envelopes issued_at issued_mono")
    catalog = view.catalog
    record(catalog, InstrumentCatalog, "schema_version revision declared_universe observations max_age expires_at")
    if (type(catalog.declared_universe) is not tuple or type(catalog.observations) is not tuple
            or type(view.envelopes) is not tuple or not 1 <= len(view.envelopes) <= 16
            or len(catalog.declared_universe) != len(view.envelopes)
            or len(catalog.observations) != len(view.envelopes)):
        raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
    for item in catalog.declared_universe:
        key(item)
    for observed in (*catalog.observations, instrument):
        observation(observed)
    for envelope in view.envelopes:
        record(envelope, InstrumentEnvelopeRead, "key method endpoint params sent_origin response_origin response_status "
               "request_started_at received_at started_mono received_mono response_time decoded_body_bytes "
               "decoded_body_sha256 canonical_envelope_bytes accepted_attempt physical_start physical_end logical_ordinal")
        key(envelope.key)
        record(envelope.sent_origin, HttpsOrigin, "scheme host port")
        record(envelope.response_origin, HttpsOrigin, "scheme host port")
        if (type(envelope.params) is not tuple or len(envelope.params) != 2
                or any(type(pair) is not tuple or len(pair) != 2
                       or any(type(part) is not str for part in pair) for pair in envelope.params)):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
    allowed = (str, int, bool, float, bytes, timedelta, Decimal, tuple, object,
               CurrentCatalogReceipt, CatalogReceiptView, InstrumentCatalog, InstrumentKey,
               InstrumentObservation, CatalogSource, LinearInstrumentSpec, SpotInstrumentSpec,
               DecimalField, KnownNotPublished, HttpsOrigin)
    for _, _, _, value in refs:
        if type(value) is datetime:
            if value.tzinfo is not UTC:
                raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        elif type(value) not in allowed:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
    return tuple(refs)


class _RiskCaptureState:
    """Operation-local original owner/group fields, with no issued authority."""

    def __init__(self, owner: BybitInstrumentCatalogProducer, instrument: InstrumentObservation,
                 receipt: CurrentCatalogReceipt, revision: str, budget: MonitoringReadBudget,
                 max_age: timedelta, pending: object | None) -> None:
        if type(owner) is not BybitInstrumentCatalogProducer:
            _reject("INPUT_INVALID")
        if type(budget) is not MonitoringReadBudget:
            _reject("BUDGET_REQUIRED")
        client = owner._client
        if type(client) is not BybitReadOnlyClient or type(client._client) is not httpx.AsyncClient:
            _reject("RETIRED")
        self.owner_types = ((owner, type(owner)), (client, type(client)),
                            (client._client, type(client._client)))
        self.budget_type, self.receipt_type = type(budget), _ReceiptBudget
        self.owner, self.instrument, self.handle, self.revision = owner, instrument, receipt, revision
        self.budget, self.max_age, self.pending = budget, max_age, pending
        self.current, self.generation = owner._current, owner._generation
        self.client, self.raw_client = owner._client, owner._client._client
        self.source, self.testing, self.owner_loop = owner._source_environment, owner._testing, owner._owner_loop
        self.wall_clock, self.monotonic_clock = owner._wall_clock, owner._monotonic_clock
        self.risk_id, self.risk_generation = self.client._risk_source_id, self.client._risk_generation
        self.client_refs = tuple((self.client, type(self.client), name, object.__getattribute__(self.client, name))
                                for name in ("environment", "base_url", "_api_key", "_api_secret",
                                             "_credential_check"))
        if (any(type(object.__getattribute__(self.client, name)) is not str
                for name in ("environment", "base_url", "_api_key", "_api_secret"))
                or self.client._api_key != "" or self.client._api_secret != ""
                or self.client._credential_check is not None):
            _reject("ORIGIN_MISMATCH")
        self.operation_context = self.client._operation
        if type(self.operation_context) is not ContextVar:
            _reject("OVERLAP")
        self.operation = self.operation_context.get()
        self.receipt_context = budget._receipt
        if type(self.receipt_context) is not ContextVar:
            _reject("GROUP_REQUIRED")
        self.receipt = self.receipt_context.get()
        self.budget_deadline, self.budget_guard = budget.deadline, budget._guard
        self.receipt_fields: tuple[object, object, object, object] | None = None
        if self.receipt is not None:
            if type(self.receipt) is not self.receipt_type:
                _reject("GROUP_REQUIRED")
            self.receipt_fields = (self.receipt.deadline, self.receipt.guard,
                                   self.receipt.owner_guard, self.receipt.token)
        self.group: ReadGroupToken | None = None
        if self.current is None:
            _reject("RECEIPT_REQUIRED")
        if type(revision) is not str:
            _reject("INPUT_INVALID")
        # Preservation precedes clocks; genuine lookup and full eleven-field pin
        # validation below establish authenticity before this snapshot can be used.
        self.refs = _risk_public_refs(self.current, instrument)
        self.pin: InstrumentPinV2 | None = None
        self.page_refs: _RiskRefs = ()

    def retain_pages(self, pages: tuple[BybitRiskLimitEnvelopeReadV1, ...]) -> None:
        refs: list[tuple[object, type[object], str, object]] = []
        for page in pages:
            if type(page) is not BybitRiskLimitEnvelopeReadV1:
                raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
            for record, kind, names in (
                (page, BybitRiskLimitEnvelopeReadV1,
                 ("key method endpoint params sent_origin response_origin response_status request_started_at "
                 "received_at started_mono received_mono response_time decoded_body_bytes decoded_body_sha256 "
                 "accepted_attempt physical_start physical_end logical_ordinal observation_id source_id "
                 "source_generation source_environment")),
                (page.key, InstrumentKey, "venue product symbol"),
                (page.sent_origin, HttpsOrigin, "scheme host port"),
                (page.response_origin, HttpsOrigin, "scheme host port"),
            ):
                if type(record) is not kind:
                    raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
                for name in names.split():
                    refs.append((record, kind, name, object.__getattribute__(record, name)))
        self.page_refs = tuple(refs)

    def bind_group(self, group: ReadGroupToken) -> None:
        if type(self.budget) is not self.budget_type or self.budget._receipt is not self.receipt_context:
            _reject("GROUP_REQUIRED")
        receipt = self.receipt_context.get()
        if self.receipt is not None and receipt is not self.receipt:
            _reject("GROUP_REQUIRED")
        self.receipt, self.group = receipt, group
        if self.receipt_fields is None and type(receipt) is _ReceiptBudget:
            self.receipt_fields = receipt.deadline, receipt.guard, receipt.owner_guard, receipt.token
        self.raw_group()

    def raw_group(self) -> None:
        receipt = self.receipt
        if (type(self.budget) is not self.budget_type or type(self.receipt_context) is not ContextVar
                or self.budget._receipt is not self.receipt_context
                or type(receipt) is not self.receipt_type or self.receipt_context.get() is not receipt
                or type(self.group) is not ReadGroupToken or receipt.token is not self.group
                or receipt.active is not True or self.receipt_fields is None
                or type(receipt.deadline) is not float or not math.isfinite(receipt.deadline)
                or receipt.deadline is not self.receipt_fields[0]
                or receipt.guard is not self.receipt_fields[1]
                or receipt.owner_guard is not self.receipt_fields[2]
                or receipt.token is not self.receipt_fields[3]
                or self.budget.deadline is not self.budget_deadline or self.budget._guard is not self.budget_guard):
            _reject("GROUP_REQUIRED")

    def raw_owner(self) -> None:
        owner, client, raw = self.owner, self.client, self.raw_client
        # Detached admission precedes every direct read: object.__getattribute__
        # would still dispatch a descriptor on a later substituted class.
        if any(type(record) is not kind for record, kind in self.owner_types):
            _reject("RETIRED")
        refs = (*self.refs, *self.client_refs, *self.page_refs)
        if any(type(record) is not kind for record, kind, _, _ in refs):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        if owner._closed is not False:
            _reject("CLOSED")
        if (type(owner) is not BybitInstrumentCatalogProducer or owner._owner_loop is not self.owner_loop
                or owner._current is not self.current or owner._generation is not self.generation
                or owner._risk_pending is not self.pending or owner._pending is not None
                or owner._client is not client or owner._owned_client is not client
                or type(client) is not BybitReadOnlyClient or type(raw) is not httpx.AsyncClient
                or client._client is not raw or client._risk_http_client is not raw
                or type(raw._state) is not ClientState
                or (raw._state is not ClientState.OPENED and raw._state is not ClientState.UNOPENED)
                or owner._source_environment is not self.source or owner._testing is not self.testing
                or owner._wall_clock is not self.wall_clock or owner._monotonic_clock is not self.monotonic_clock
                or client._public_source_environment is not self.source
                or client._injected_transport is not self.testing
                or client._risk_source_id is not self.risk_id or client._risk_generation is not self.risk_generation):
            _reject("RETIRED")
        for record, _, name, value in refs:
            if object.__getattribute__(record, name) is not value:
                raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        if (self.client._operation is not self.operation_context
                or (self.operation_context.get() is not self.operation
                    and self.operation_context.get() is not self.budget)):
            _reject("OVERLAP")

    def fresh_pages(self, pages: tuple[BybitRiskLimitEnvelopeReadV1, ...],
                    wall: datetime, mono: float) -> None:
        ttl_us = _microseconds(self.max_age)
        current_num, current_den = mono.as_integer_ratio()
        for page in pages:
            if (type(page) is not BybitRiskLimitEnvelopeReadV1
                    or type(page.received_at) is not datetime or page.received_at.tzinfo is not UTC
                    or type(page.response_time) is not datetime or page.response_time.tzinfo is not UTC
                    or type(page.received_mono) is not float or not math.isfinite(page.received_mono)
                    or page.response_time > page.received_at or page.received_at > wall
                    or page.received_mono > mono):
                _reject("CLOCK_INVALID")
            received_num, received_den = page.received_mono.as_integer_ratio()
            denominator = current_den * received_den
            elapsed = current_num * received_den - received_num * current_den
            age_us = _microseconds(page.received_at - page.response_time)
            if (_microseconds(wall - page.response_time) >= ttl_us
                    or _microseconds(wall - page.received_at) >= ttl_us
                    or age_us * denominator + elapsed * 1000000 >= ttl_us * denominator):
                _reject("EXPIRED")

    def fresh_deadline(self, mono: float) -> None:
        """Use an already sampled clock; no budget/owner callback in this proof."""
        if self.receipt_fields is None:
            _reject("GROUP_REQUIRED")
        deadline = self.receipt_fields[0]
        if (type(deadline) is not float or not math.isfinite(deadline)
                or type(self.budget_deadline) is not float or not math.isfinite(self.budget_deadline)
                or mono >= deadline or mono >= self.budget_deadline):
            _reject("BUDGET_EXHAUSTED")

    def check(self, pages: tuple[BybitRiskLimitEnvelopeReadV1, ...] = (),
              owner_guard: Callable[[], None] | None = None) -> None:
        owner = self.owner
        self.raw_owner()
        view = owner._risk_current(self.handle, expected_revision=self.revision)
        self.raw_owner()  # Close original public fields after callbackful lookup, before pin equality.
        # Full pin equality is semantic validation, before all final raw proofs.
        member = next((item for item in view.catalog.observations if item.key == self.instrument.key), None)
        if member is None:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        try:
            pin = pin_instrument_v2(self.instrument, catalog_revision=view.catalog.revision)
            expected = pin_instrument_v2(member, catalog_revision=view.catalog.revision)
        except RiskV2Rejected:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH") from None
        if _pin_payload_v2(pin) != _pin_payload_v2(expected):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        if self.pin is None:
            self.pin = pin
        elif _pin_payload_v2(pin) != _pin_payload_v2(self.pin):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        wall, mono = owner._clock()
        self.fresh_deadline(mono)
        self.fresh_pages(pages, wall, mono)
        if owner_guard is not None:
            owner_guard()
        # No clock/lookup/session/budget callback after this combined suffix.
        self.raw_owner()
        self.raw_group()


class BybitInstrumentCatalogProducer:
    """One event-loop owner, one pending or current bounded generation."""

    def __init__(self, *, source_environment: _Source) -> None:
        owner_loop = _loop()  # Before resource allocation, including TLS client creation.
        source = _source(source_environment)
        client = BybitReadOnlyClient.for_public_instruments(source_environment=source)
        self._initialize(
            owner_loop,
            source,
            client,
            wall_clock=lambda: datetime.now(UTC),
            monotonic_clock=time.monotonic,
            testing=False,
        )

    @classmethod
    def _for_testing(
        cls,
        *,
        source_environment: _Source,
        transport: httpx.MockTransport,
        wall_clock: Callable[[], datetime],
        monotonic_clock: Callable[[], float],
    ) -> BybitInstrumentCatalogProducer:
        """Internal offline harness only; its identities carry no production authority."""
        owner_loop = _loop()
        source = _source(source_environment)
        if type(transport) is not httpx.MockTransport:
            _reject("INJECTED_TRANSPORT")
        client = BybitReadOnlyClient.for_public_instruments(
            source_environment=source, transport=transport
        )
        instance = object.__new__(cls)
        instance._initialize(
            owner_loop,
            source,
            client,
            wall_clock=wall_clock,
            monotonic_clock=monotonic_clock,
            testing=True,
        )
        return instance

    def _initialize(
        self,
        owner_loop: asyncio.AbstractEventLoop,
        source: _Source,
        client: BybitReadOnlyClient,
        *,
        wall_clock: Callable[[], datetime],
        monotonic_clock: Callable[[], float],
        testing: bool,
    ) -> None:
        self._owner_loop = owner_loop
        self._source_environment = source
        self._client = client
        self._owned_client = client  # Actual constructed identity, not a provenance flag.
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._testing = testing
        self._closed = False
        self._generation = object()
        self._current: _Current | None = None
        self._pending: _Pending | None = None
        self._risk_pending: object | None = None
        self._last_wall: datetime | None = None
        self._last_mono: float | None = None
        self._close_task: asyncio.Task[None] | None = None

    def _require_loop(self) -> None:
        if _loop() is not self._owner_loop:
            _reject("LOOP_REQUIRED")

    def _require_open(self) -> None:
        self._require_loop()
        if self._closed:
            _reject("CLOSED")

    def _revoke(self) -> None:
        self._generation = object()
        self._current = None
        if self._pending is not None:
            self._pending.phase = "REVOKED"
            self._pending.envelopes = ()

    def _require_client(self) -> None:
        if (
            type(self._client) is not BybitReadOnlyClient
            or self._client is not self._owned_client
            or self._client._public_source_environment != self._source_environment
            or self._client._injected_transport is not self._testing
            or self._client._client.is_closed
        ):
            self._revoke()
            _reject("RETIRED")

    def _clock(self) -> tuple[datetime, float]:
        wall, mono = self._wall_clock(), self._monotonic_clock()
        if (
            type(wall) is not datetime
            or type(wall.tzinfo) is not timezone
            or wall.tzinfo is not UTC
            or type(mono) is not float
            or not math.isfinite(mono)
            or mono < 0
            or (self._last_wall is not None and wall < self._last_wall)
            or (self._last_mono is not None and mono < self._last_mono)
        ):
            self._revoke()
            _reject("CLOCK_INVALID")
        self._last_wall, self._last_mono = wall, mono
        return wall, mono

    def _fresh_envelopes(
        self,
        envelopes: tuple[InstrumentEnvelopeRead, ...],
        ttl: timedelta,
        wall: datetime,
        mono: float,
    ) -> None:
        ttl_us = _microseconds(ttl)
        current_num, current_den = mono.as_integer_ratio()
        for envelope in envelopes:
            if (
                envelope.received_at > wall
                or envelope.received_mono > mono
                or envelope.response_time > envelope.received_at
            ):
                self._revoke()
                _reject("CLOCK_INVALID")
            age_us = _microseconds(envelope.received_at - envelope.response_time)
            receive_num, receive_den = envelope.received_mono.as_integer_ratio()
            elapsed_num = current_num * receive_den - receive_num * current_den
            denominator = current_den * receive_den
            if (
                _microseconds(wall - envelope.response_time) >= ttl_us
                or _microseconds(wall - envelope.received_at) >= ttl_us
                or age_us * denominator + elapsed_num * 1000000 >= ttl_us * denominator
            ):
                self._revoke()
                _reject("EXPIRED")

    def _active_group(self, slot: _Pending) -> ReadGroupView:
        assert slot.budget is not None and slot.group is not None
        code: str | None = None
        try:
            result = slot.budget.require_active_group(slot.group)
        except MonitoringBudgetError as error:
            code = _budget_code(error)
        if code is not None:
            raise InstrumentSourceError(code)
        if (
            type(result) is not ReadGroupView
            or type(result.deadline) is not float
            or not math.isfinite(result.deadline)
        ):
            _reject("BUDGET_EXHAUSTED")
        # Existing group guards are arbitrary owner callbacks and may retire us.
        # Preserve them unchanged, but recheck our own state after they return.
        self._require_pending(slot)
        return result

    def _require_pending(self, slot: _Pending) -> None:
        self._require_open()
        if (
            self._pending is not slot
            or self._generation is not slot.generation
            or slot.phase == "REVOKED"
        ):
            _reject("RETIRED")
        self._require_client()

    def _check_pending(self, slot: _Pending) -> tuple[datetime, float]:
        self._require_pending(slot)
        self._active_group(slot)
        wall, mono = self._clock()
        self._require_pending(slot)
        assert slot.ttl is not None
        self._fresh_envelopes(slot.envelopes, slot.ttl, wall, mono)
        return wall, mono

    def _validate_inputs(
        self,
        slot: _Pending,
        keys: tuple[InstrumentKey, ...],
        ttl: timedelta,
        budget: MonitoringReadBudget,
    ) -> None:
        if type(keys) is not tuple or not 1 <= len(keys) <= 16:
            _reject("INPUT_INVALID")
        code: str | None = None
        try:
            identities = tuple(tuple(_key_payload(key).values()) for key in keys)
        except CatalogRejected as error:
            code = catalog_error_code(error)
        if code is not None:
            raise InstrumentSourceError(code)
        if len(set(identities)) != len(keys):
            _reject("INPUT_INVALID")
        if type(ttl) is not timedelta or ttl <= timedelta(0):
            _reject("INPUT_INVALID")
        overflow = False
        try:
            slot.started_at + ttl
            slot.started_at - ttl  # The frozen freshness checker also needs a safe cutoff.
        except OverflowError:
            overflow = True
        if overflow:
            _reject("CLOCK_INVALID")
        if type(budget) is not MonitoringReadBudget:
            _reject("BUDGET_REQUIRED")
        slot.keys = tuple(sorted(keys, key=lambda key: (key.venue, key.product, key.symbol)))
        slot.ttl, slot.budget, slot.source_id = ttl, budget, uuid.uuid4().hex

    async def refresh(
        self,
        *,
        declared_universe: tuple[InstrumentKey, ...],
        max_age: timedelta,
        budget: MonitoringReadBudget,
    ) -> CurrentCatalogReceipt:
        self._require_open()
        if self._pending is not None:
            _reject("OVERLAP")
        self._revoke()  # Accepted operation retires current BEFORE caller input validation.
        # Install the marker before clock validation, without retaining caller arguments.
        slot = _Pending(self._generation, datetime.min.replace(tzinfo=UTC), 0.0)
        self._pending = slot
        code: str | None = None
        try:
            self._require_client()
            slot.started_at, slot.started_mono = self._clock()
            self._validate_inputs(slot, declared_universe, max_age, budget)
            with budget.reuse_or_open_receipt() as group:
                slot.group = group
                active = self._active_group(slot)
                if active.remaining_gets < len(slot.keys):
                    _reject("BUDGET_EXHAUSTED")
                slot.phase = "COLLECTING"
                for key in slot.keys:
                    pre_wall, pre_mono = self._check_pending(slot)
                    before = self._active_group(slot)
                    envelope = await self._client.read_instrument_envelope(
                        key, budget=budget, group=group
                    )
                    post_wall, post_mono = self._check_pending(slot)
                    validate_instrument_envelope_read(envelope)
                    host = (
                        "api.bybit.com"
                        if self._source_environment == "PUBLIC_MAINNET"
                        else "api-testnet.bybit.com"
                    )
                    if (
                        envelope.key != key
                        or envelope.response_origin.host != host
                        or envelope.sent_origin.host != host
                    ):
                        _reject("ORIGIN_MISMATCH")
                    after = self._active_group(slot)
                    if (
                        envelope.logical_ordinal != before.logical_gets + 1
                        or after.logical_gets != before.logical_gets + 1
                    ):
                        _reject("RESPONSE_INVALID")
                    if (
                        envelope.request_started_at < slot.started_at
                        or envelope.started_mono < slot.started_mono
                        or envelope.request_started_at < pre_wall
                        or envelope.started_mono < pre_mono
                        or envelope.received_at > post_wall
                        or envelope.received_mono > post_mono
                    ):
                        _reject("CLOCK_INVALID")
                    slot.envelopes += (envelope,)
                    self._check_pending(slot)
                handle = self._publish(slot)
            return handle
        except CatalogRejected as error:
            code = catalog_error_code(error)
        except MonitoringBudgetError as error:
            code = _budget_code(error)
        finally:
            # Retire/close keep the revoked marker until this coroutine actually drains.
            if self._pending is slot:
                self._pending = None
            slot.envelopes = ()
        assert code is not None
        raise InstrumentSourceError(code)

    def _publish(self, slot: _Pending) -> CurrentCatalogReceipt:
        wall, mono = self._check_pending(slot)
        assert slot.ttl is not None
        if len(slot.envelopes) != len(slot.keys):
            _reject("INCOMPLETE")
        observations: list[InstrumentObservation] = []
        for envelope in slot.envelopes:
            body, _, _ = decode_instrument_envelope(envelope.decoded_body_bytes)
            source = CatalogSource(
                slot.source_id, self._source_environment, INSTRUMENT_ENDPOINT, envelope.received_at
            )
            observations.append(
                parse_bybit_instrument_response(body, expected=envelope.key, source=source)
            )
        catalog = build_instrument_catalog(
            tuple(observations), declared_universe=slot.keys, now=wall, max_age=slot.ttl
        )
        result = CatalogReceiptView(
            catalog, self._source_environment, slot.source_id, slot.envelopes, wall, mono
        )
        # Revalidation/parsing can consume time; sample both clocks once more before issue.
        wall, mono = self._check_pending(slot)
        _freshness(catalog, wall)
        handle = _new_current_catalog_receipt()
        self._current = _Current(handle, result, slot.generation)
        return handle

    def require_current(
        self,
        receipt: CurrentCatalogReceipt,
        *,
        expected_revision: str,
    ) -> CatalogReceiptView:
        self._require_open()
        if self._testing:
            _reject("INJECTED_TRANSPORT")
        return self._lookup(receipt, expected_revision=expected_revision)

    def _require_current_for_testing(
        self,
        receipt: CurrentCatalogReceipt,
        *,
        expected_revision: str,
    ) -> CatalogReceiptView:
        """Harness verifier; cannot validate production identities or confer authority."""
        self._require_open()
        if not self._testing:
            _reject("INJECTED_TRANSPORT")
        return self._lookup(receipt, expected_revision=expected_revision)

    def _lookup(
        self, receipt: CurrentCatalogReceipt, *, expected_revision: str
    ) -> CatalogReceiptView:
        self._require_client()
        current = self._current
        if (
            type(receipt) is not CurrentCatalogReceipt
            or current is None
            or current.handle is not receipt
            or current.generation is not self._generation
        ):
            _reject("RECEIPT_REQUIRED")
        wall, mono = self._clock()
        code: str | None = None
        try:
            validate_catalog_receipt_view(current.view)
            if current.view.source_environment != self._source_environment:
                _reject("ORIGIN_MISMATCH")
            if current.view.issued_at > wall or current.view.issued_mono > mono:
                _reject("CLOCK_INVALID")
            # Bounded raw/catalog revalidation itself can cross an expiry boundary.
            wall, mono = self._clock()
            _freshness(current.view.catalog, wall)
            self._fresh_envelopes(current.view.envelopes, current.view.catalog.max_age, wall, mono)
        except CatalogRejected as error:
            code = catalog_error_code(error)
        except InstrumentSourceError as error:
            code = error.code
        if code is not None:
            self._revoke()
            raise InstrumentSourceError(code)
        if type(expected_revision) is not str or expected_revision != current.view.catalog.revision:
            _reject("RESPONSE_INVALID")
        return current.view

    def _risk_current(self, receipt: CurrentCatalogReceipt, *, expected_revision: str) -> CatalogReceiptView:
        """RAW-only genuine fixture lookup; production currentness stays strict."""
        if self._testing:
            return self._require_current_for_testing(receipt, expected_revision=expected_revision)
        return self.require_current(receipt, expected_revision=expected_revision)

    async def capture_linear_risk_limit_v1(
        self, instrument: InstrumentObservation, *, catalog_receipt: CurrentCatalogReceipt,
        expected_catalog_revision: str, budget: MonitoringReadBudget,
        max_age: timedelta, max_pages: int, owner_guard: Callable[[], None] | None = None,
    ) -> BybitRiskLimitCaptureV1:
        self._require_open()
        if self._risk_pending is not None or self._pending is not None:
            _reject("OVERLAP")
        pending = object()
        self._risk_pending = pending  # Before any caller clock, lookup or guard.
        guard_failure: BaseException | None = None
        try:
            if type(budget) is not MonitoringReadBudget:
                _reject("BUDGET_REQUIRED")
            if (type(max_age) is not timedelta or not timedelta(0) < max_age <= timedelta(minutes=5)
                    or type(max_pages) is not int or not 1 <= max_pages <= 100
                    or (owner_guard is not None and not callable(owner_guard))):
                _reject("INPUT_INVALID")
            state = _RiskCaptureState(self, instrument, catalog_receipt, expected_catalog_revision,
                                      budget, max_age, pending)
            pages: tuple[BybitRiskLimitEnvelopeReadV1, ...] = ()
            cursor, cursors, byte_count, row_count = "", set[str](), 0, 0
            operation_id = uuid.uuid4().hex

            def higher() -> None:
                nonlocal guard_failure
                if owner_guard is not None:
                    try:
                        owner_guard()
                    except BaseException as error:
                        guard_failure = error
                        raise

            with _risk_receipt(budget) as group:
                state.bind_group(group)

                def check() -> None:
                    state.check(pages, higher)

                check()
                while True:
                    active = budget.require_active_group(group)
                    check()
                    envelope = await state.client.read_risk_limit_envelope(
                        instrument.key, cursor=cursor, budget=budget, group=group,
                        deadline=active.deadline, owner_guard=check)
                    check()
                    page = decode_bybit_risk_limit_page_v1(envelope, page_ordinal=len(pages) + 1)
                    byte_count += len(envelope.decoded_body_bytes)
                    row_count += len(page.rows)
                    if len(pages) >= min(max_pages, 64) or byte_count > 1048576 or row_count > 1024:
                        raise RiskV2Rejected("RISK_V2_RESOURCE_LIMIT")
                    pages += (envelope,)
                    state.retain_pages(pages)
                    check()
                    cursor = page.next_cursor
                    if not cursor:
                        break
                    if cursor in cursors or not page.rows:
                        raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
                    cursors.add(cursor)
                    if len(pages) >= min(max_pages, 64) or byte_count >= 1048576 or row_count >= 1024:
                        raise RiskV2Rejected("RISK_V2_RESOURCE_LIMIT")
                assert state.pin is not None
                result = parse_bybit_risk_limit_table_v1(
                    pages, instrument_pin=state.pin, observation_id=operation_id)
                check()
                if state.operation_context.get() is not state.operation:
                    _reject("OVERLAP")
            return result
        except CatalogRejected as error:
            if guard_failure is error:
                raise
            raise InstrumentSourceError(catalog_error_code(error)) from None
        except MonitoringBudgetError as error:
            if guard_failure is error:
                raise
            if error.args == ("MONITORING_GROUP_REQUIRED",):
                raise InstrumentSourceError("INSTRUMENT_SOURCE_GROUP_REQUIRED") from None
            if error.args in (("MONITORING_DEADLINE_OR_CLOCK",), ("MONITORING_LOGICAL_GET_LIMIT",),
                               ("MONITORING_PHYSICAL_ATTEMPTS",), ("MONITORING_READ_FAILED",)):
                raise InstrumentSourceError("INSTRUMENT_SOURCE_BUDGET_EXHAUSTED") from None
            raise
        finally:
            if self._risk_pending is pending:
                self._risk_pending = None

    def retire(self) -> None:
        self._require_loop()
        if not self._closed:
            self._revoke()

    async def _cleanup(self) -> None:
        code: str | None = None
        try:
            async with asyncio.timeout(10):
                await self._owned_client.aclose()
        except TimeoutError:
            code = "INSTRUMENT_SOURCE_TIMEOUT"
        except httpx.HTTPError:
            code = "INSTRUMENT_SOURCE_NETWORK"
        if code is not None:
            raise InstrumentSourceError(code)

    async def aclose(self) -> None:
        self._require_loop()
        if not self._closed:
            self._closed = True
            self._revoke()
        if self._close_task is None:
            self._close_task = self._owner_loop.create_task(self._cleanup())
        await asyncio.shield(self._close_task)
