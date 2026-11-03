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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Literal, NoReturn

import httpx

from ..account.bybit_readonly import BybitReadOnlyClient
from ..account.instrument_envelope import (
    CatalogReceiptView,
    CurrentCatalogReceipt,
    InstrumentEnvelopeRead,
    InstrumentSourceError,
    _new_current_catalog_receipt,
    catalog_error_code,
    decode_instrument_envelope,
    validate_catalog_receipt_view,
    validate_instrument_envelope_read,
)
from ..execution.monitoring_budget import (
    MonitoringBudgetError,
    MonitoringReadBudget,
    ReadGroupToken,
    ReadGroupView,
)
from ..trading_product import InstrumentKey
from .instrument_catalog import _freshness, build_instrument_catalog
from .instrument_definition import (
    INSTRUMENT_ENDPOINT,
    CatalogRejected,
    CatalogSource,
    InstrumentObservation,
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
                    self._check_pending(slot)
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
