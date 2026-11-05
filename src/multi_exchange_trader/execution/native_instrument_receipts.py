"""Current, owner-bound evidence for restored DEMO LINEAR definitions.

This receipt records native/source agreement after the wallet ACK. It does not
grant order permission. Fresh-monitor bootstrap is intentionally not an issuer.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, NoReturn, Self

from multi_exchange_trader.trading_product import InstrumentKey

from ..account.instrument_envelope import (
    HttpsOrigin,
    InstrumentEnvelopeRead,
    InstrumentSourceError,
    validate_instrument_envelope_read,
)
from ..market_data.instrument_definition import CatalogRejected, LinearInstrumentSpec
from ..market_data.instrument_source_projection import (
    SupportedInstrumentProjection,
    validate_supported_instrument_projection,
)
from .monitoring_budget import MonitoringReadBudget, _ReceiptBudget
from .monitoring_models import MonitoringError, MonitoringTarget
from .observation_instrument_v1 import (
    ACTUAL_ACCOUNT_FEE_V1,
    SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
)
from .protection import ProtectionScope

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_DEMO = HttpsOrigin("https", "api-demo.bybit.com", 443)


def _deny(code: str = "MONITORING_INSTRUMENT_RECEIPT_REQUIRED") -> NoReturn:
    raise MonitoringError(code)


def _utc(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        _deny("MONITORING_INSTRUMENT_RECEIPT_CLOCK_INVALID")
    try:
        return value.astimezone(UTC)
    except Exception:  # noqa: BLE001 - clock errors are fixed at the issuer boundary.
        _deny("MONITORING_INSTRUMENT_RECEIPT_CLOCK_INVALID")


@dataclass(frozen=True, repr=False)
class NativeFeeResponseV1:
    """Selected account-fee values and accepted-response metadata; never the body."""

    symbol: str
    scope: ProtectionScope
    maker_fee_rate: str
    taker_fee_rate: str
    body_sha256: str
    path: str
    query: tuple[tuple[str, str], ...]
    signed: bool
    origin: HttpsOrigin
    response_origin: HttpsOrigin
    response_status: int
    request_started_at: datetime
    received_at: datetime
    started_mono: float
    received_mono: float
    accepted_attempt: int
    logical_ordinal: int
    physical_start: int
    physical_end: int
    envelope_time_ms: int | None

    def __post_init__(self) -> None:
        if (
            type(self.symbol) is not str
            or not self.symbol
            or type(self.scope) is not ProtectionScope
            or type(self.maker_fee_rate) is not str
            or type(self.taker_fee_rate) is not str
            or type(self.body_sha256) is not str
            or _DIGEST.fullmatch(self.body_sha256) is None
            or type(self.path) is not str
            or self.path != "/v5/account/fee-rate"
            or type(self.query) is not tuple
            or self.query != (("category", "linear"), ("symbol", self.symbol))
            or self.signed is not True
            or self.origin != _DEMO
            or self.response_origin != _DEMO
            or type(self.response_status) is not int
            or self.response_status != 200
            or type(self.accepted_attempt) is not int
            or not 0 <= self.accepted_attempt <= 2
            or type(self.logical_ordinal) is not int
            or not 1 <= self.logical_ordinal <= 32
            or type(self.physical_start) is not int
            or type(self.physical_end) is not int
            or not 0 <= self.physical_start < self.physical_end <= 200
            or not 1 <= self.physical_end - self.physical_start <= 3
            or self.envelope_time_ms is not None
            and (type(self.envelope_time_ms) is not int or self.envelope_time_ms <= 0)
        ):
            _deny("MONITORING_INSTRUMENT_FEE_SOURCE_INVALID")
        start, receive = _utc(self.request_started_at), _utc(self.received_at)
        if start > receive:
            _deny("MONITORING_INSTRUMENT_RECEIPT_CLOCK_INVALID")
        for clock in (self.started_mono, self.received_mono):
            if type(clock) is not float or not math.isfinite(clock) or clock < 0:
                _deny("MONITORING_INSTRUMENT_RECEIPT_CLOCK_INVALID")
        if self.started_mono > self.received_mono:
            _deny("MONITORING_INSTRUMENT_RECEIPT_CLOCK_INVALID")


@dataclass(frozen=True, repr=False)
class NativeInstrumentEvidenceV1:
    """Bounded exact DEMO response, source projection, selected fee and native digest."""

    key: InstrumentKey
    envelope: InstrumentEnvelopeRead
    projection: SupportedInstrumentProjection
    fee: NativeFeeResponseV1
    native_semantics_sha256: str

    def __post_init__(self) -> None:
        try:
            validate_instrument_envelope_read(self.envelope)
            validate_supported_instrument_projection(self.projection)
        except (InstrumentSourceError, CatalogRejected):
            _deny("MONITORING_INSTRUMENT_SOURCE_INVALID")
        if (
            type(self.key) is not InstrumentKey
            or self.key.venue != "BYBIT"
            or self.key.product != "LINEAR"
            or self.envelope.key != self.key
            or self.envelope.sent_origin != _DEMO
            or self.envelope.response_origin != _DEMO
            or self.projection.key != self.key
            or type(self.fee) is not NativeFeeResponseV1
            or self.fee.symbol != self.key.symbol
            or self.fee.scope.environment != "DEMO"
            or type(self.native_semantics_sha256) is not str
            or _DIGEST.fullmatch(self.native_semantics_sha256) is None
            or type(self.projection.spec) is not LinearInstrumentSpec
        ):
            _deny("MONITORING_INSTRUMENT_SOURCE_INVALID")


@dataclass(frozen=True, repr=False)
class NativeUnknownFeeInstrumentEvidenceV1:
    """Public DEMO definition evidence with explicitly unknown account fees."""

    key: InstrumentKey
    envelope: InstrumentEnvelopeRead
    projection: SupportedInstrumentProjection
    metadata_profile: str
    native_semantics_sha256: str

    def __post_init__(self) -> None:
        try:
            validate_instrument_envelope_read(self.envelope)
            validate_supported_instrument_projection(self.projection)
        except (InstrumentSourceError, CatalogRejected):
            _deny("MONITORING_INSTRUMENT_SOURCE_INVALID")
        if (
            type(self.key) is not InstrumentKey
            or self.key.venue != "BYBIT"
            or self.key.product != "LINEAR"
            or self.envelope.key != self.key
            or self.envelope.sent_origin != _DEMO
            or self.envelope.response_origin != _DEMO
            or self.projection.key != self.key
            or type(self.metadata_profile) is not str
            or self.metadata_profile != SDK1231_OBSERVATION_UNKNOWN_FEES_V1
            or type(self.native_semantics_sha256) is not str
            or _DIGEST.fullmatch(self.native_semantics_sha256) is None
            or type(self.projection.spec) is not LinearInstrumentSpec
        ):
            _deny("MONITORING_INSTRUMENT_SOURCE_INVALID")


@dataclass(frozen=True, repr=False)
class NativeInstrumentReceiptViewV1:
    scope: ProtectionScope
    target_fingerprint: str
    symbols: tuple[str, ...]
    evidence: tuple[NativeInstrumentEvidenceV1, ...]
    armed_at: datetime
    issued_at: datetime

    def __post_init__(self) -> None:
        if (
            type(self.scope) is not ProtectionScope
            or type(self.target_fingerprint) is not str
            or not self.target_fingerprint
            or type(self.symbols) is not tuple
            or not 1 <= len(self.symbols) <= 15
            or any(type(symbol) is not str or not symbol for symbol in self.symbols)
            or len(set(self.symbols)) != len(self.symbols)
            or type(self.evidence) is not tuple
            or any(type(item) is not NativeInstrumentEvidenceV1 for item in self.evidence)
            or tuple(item.key.symbol for item in self.evidence) != self.symbols
        ):
            _deny("MONITORING_INSTRUMENT_RECEIPT_INVALID")
        armed, issued = _utc(self.armed_at), _utc(self.issued_at)
        if armed > issued:
            _deny("MONITORING_INSTRUMENT_RECEIPT_CLOCK_INVALID")


@dataclass(frozen=True, repr=False)
class NativeUnknownFeeInstrumentReceiptViewV1:
    """Profile-tagged observation evidence; never actual-fee evidence."""

    metadata_profile: str
    scope: ProtectionScope
    target_fingerprint: str
    symbols: tuple[str, ...]
    evidence: tuple[NativeUnknownFeeInstrumentEvidenceV1, ...]
    armed_at: datetime
    issued_at: datetime

    def __post_init__(self) -> None:
        if (
            type(self.metadata_profile) is not str
            or self.metadata_profile != SDK1231_OBSERVATION_UNKNOWN_FEES_V1
            or type(self.scope) is not ProtectionScope
            or type(self.target_fingerprint) is not str
            or not self.target_fingerprint
            or type(self.symbols) is not tuple
            or not 1 <= len(self.symbols) <= 15
            or any(type(symbol) is not str or not symbol for symbol in self.symbols)
            or len(set(self.symbols)) != len(self.symbols)
            or type(self.evidence) is not tuple
            or any(
                type(item) is not NativeUnknownFeeInstrumentEvidenceV1
                or item.metadata_profile != self.metadata_profile
                for item in self.evidence
            )
            or tuple(item.key.symbol for item in self.evidence) != self.symbols
        ):
            _deny("MONITORING_INSTRUMENT_RECEIPT_INVALID")
        armed, issued = _utc(self.armed_at), _utc(self.issued_at)
        if armed > issued:
            _deny("MONITORING_INSTRUMENT_RECEIPT_CLOCK_INVALID")


class NativeInstrumentReceiptV1:
    """Opaque current-handle identity. Only the owning session registry issues it."""

    __slots__ = ()

    def __new__(cls) -> Self:
        _deny()

    def __copy__(self) -> NoReturn:
        _deny()

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        _deny()

    def __reduce_ex__(self, protocol: int) -> NoReturn:
        _deny()

    def __repr__(self) -> str:
        return "NativeInstrumentReceiptV1(opaque=True)"


class NativeUnknownFeeInstrumentReceiptV1:
    """Opaque observation-only receipt distinct from actual account-fee proof."""

    __slots__ = ()

    def __new__(cls) -> Self:
        _deny()

    def __copy__(self) -> NoReturn:
        _deny()

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        _deny()

    def __reduce_ex__(self, protocol: int) -> NoReturn:
        _deny()

    def __repr__(self) -> str:
        return "NativeUnknownFeeInstrumentReceiptV1(opaque=True)"


class _PendingNativeInstrumentCaptureV1:
    owner: Any
    generation: object
    session_generation: object
    binding: Any
    attestation: object
    scope: ProtectionScope
    assembly: Any
    channel: Any
    client: Any
    provider: Any
    adapter: Any
    cache: Any
    target: MonitoringTarget
    target_fingerprint: str
    witness: Any
    symbols: tuple[str, ...]
    budget: MonitoringReadBudget
    group: Any
    owner_guard: Callable[[], None]
    armed_at: datetime
    armed_mono: float
    last_wall: datetime
    last_mono: float
    metadata_profile: str
    physical_start: int
    logical_start: int
    phase: str
    initializations: int
    inflight: set[str]
    fees: dict[str, NativeFeeResponseV1]
    evidence: dict[str, NativeInstrumentEvidenceV1]
    unknown_fee_evidence: dict[str, NativeUnknownFeeInstrumentEvidenceV1]
    view: NativeInstrumentReceiptViewV1 | NativeUnknownFeeInstrumentReceiptViewV1 | None
    handle: NativeInstrumentReceiptV1 | NativeUnknownFeeInstrumentReceiptV1 | None
    startup_open: bool
    wallet_reads: int
    last_logical: int
    last_physical: int

    __slots__ = (
        "adapter",
        "armed_at",
        "armed_mono",
        "assembly",
        "attestation",
        "binding",
        "budget",
        "cache",
        "channel",
        "client",
        "evidence",
        "fees",
        "generation",
        "group",
        "handle",
        "inflight",
        "initializations",
        "last_logical",
        "last_mono",
        "last_physical",
        "last_wall",
        "logical_start",
        "metadata_profile",
        "owner",
        "owner_guard",
        "phase",
        "physical_start",
        "provider",
        "scope",
        "session_generation",
        "startup_open",
        "symbols",
        "target",
        "target_fingerprint",
        "unknown_fee_evidence",
        "view",
        "wallet_reads",
        "witness",
    )

    def __init__(self, **parts: Any) -> None:
        for name, value in parts.items():
            setattr(self, name, value)
        self.phase = "ARMED"
        self.initializations = 0
        self.inflight: set[str] = set()
        self.fees: dict[str, NativeFeeResponseV1] = {}
        self.evidence: dict[str, NativeInstrumentEvidenceV1] = {}
        self.unknown_fee_evidence: dict[str, NativeUnknownFeeInstrumentEvidenceV1] = {}
        self.view: (
            NativeInstrumentReceiptViewV1 | NativeUnknownFeeInstrumentReceiptViewV1 | None
        ) = None
        self.handle: NativeInstrumentReceiptV1 | NativeUnknownFeeInstrumentReceiptV1 | None = None
        self.startup_open = True
        self.wallet_reads = 0
        self.last_logical = self.logical_start
        self.last_physical = self.physical_start


class NativeInstrumentReceiptRegistryV1:
    """Single-assembly pending/issued slot owned by one exact DEMO session."""

    def __init__(self, owner: object) -> None:
        self._owner = owner
        self._generation = object()
        self._slot: _PendingNativeInstrumentCaptureV1 | None = None
        self._last_clock: tuple[datetime, float] | None = None

    def _clock(self) -> tuple[datetime, float]:
        try:
            owner = self._owner
            now = _utc(owner._clock())  # type: ignore[attr-defined]
            mono = owner._mono()  # type: ignore[attr-defined]
            if type(mono) not in (int, float) or not math.isfinite(mono) or mono < 0:
                _deny("MONITORING_INSTRUMENT_RECEIPT_CLOCK_INVALID")
            mono = float(mono)
            prior = self._last_clock
            if prior is not None and (now < prior[0] or mono < prior[1]):
                _deny("MONITORING_INSTRUMENT_RECEIPT_CLOCK_INVALID")
            self._last_clock = now, mono
            return now, mono
        except BaseException as error:
            self.revoke()
            if isinstance(error, asyncio.CancelledError):
                raise
            _deny("MONITORING_INSTRUMENT_RECEIPT_CLOCK_INVALID")

    def arm(
        self,
        *,
        binding: object,
        scope: ProtectionScope,
        assembly: object,
        channel: object,
        client: object,
        provider: object,
        adapter: object,
        cache: object,
        target: MonitoringTarget,
        budget: MonitoringReadBudget,
        owner_guard: Callable[[], None],
    ) -> _PendingNativeInstrumentCaptureV1:
        from nautilus_trader.adapters.bybit.providers import BybitInstrumentProvider

        from .account_binding import AuthenticatedAccountBinding, BybitDemoAccountSession
        from .native_monitor_client import MonitoringBybitExecutionClient
        from .native_monitor_http import MonitoringHttpAdapter

        if self._slot is not None:
            self.revoke()
            _deny("MONITORING_INSTRUMENT_RECEIPT_OVERLAP")
        owner = self._owner
        try:
            if (
                type(owner) is not BybitDemoAccountSession
                or type(binding) is not AuthenticatedAccountBinding
                or binding is not owner._binding
                or type(scope) is not ProtectionScope
                or type(target) is not MonitoringTarget
                or target.scope != scope
                or type(client) is not MonitoringBybitExecutionClient
                or client._monitor_session is not owner
                or client._monitor_binding is not binding
                or client._monitor_target is not target
                or client._monitor_attestation is not owner._attested
                or type(provider) is not BybitInstrumentProvider
                or client.bybit_instrument_provider is not provider
                or type(adapter) is not MonitoringHttpAdapter
                or client._http_client is not adapter
                or client._metadata_profile not in (
                    ACTUAL_ACCOUNT_FEE_V1,
                    SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
                )
                or adapter._metadata_profile != client._metadata_profile
                or adapter._session is not owner
                or adapter._binding is not binding
                or adapter._scope != scope
                or adapter._symbols != target.symbols
                or adapter._cancelled
                or adapter._native_capture_slot is not None
                or client._cache is not cache
                or client._native_instrument_capture_v1 is not None
                or not callable(owner_guard)
                or type(budget) is not MonitoringReadBudget
                or owner._client._operation.get() is not budget
            ):
                _deny("MONITORING_INSTRUMENT_RECEIPT_ARM_REQUIRED")
            witness = target._witness
            if (
                witness is None
                or witness._assembly is not assembly
                or witness._channel is not channel
                or witness._worker.binding is not binding
                or witness._assembly.execution_client is not client
                or type(target.symbols) is not tuple
                or not 1 <= len(target.symbols) <= 15
                or len(set(target.symbols)) != len(target.symbols)
                or any(type(symbol) is not str or not symbol for symbol in target.symbols)
            ):
                _deny("MONITORING_INSTRUMENT_RECEIPT_ARM_REQUIRED")
            receipt = budget._receipt.get()
            if type(receipt) is not _ReceiptBudget:
                _deny("MONITORING_INSTRUMENT_RECEIPT_CAPACITY")
            token = receipt.token
            view = budget.require_active_group(token)
            actual_fees = client._metadata_profile == ACTUAL_ACCOUNT_FEE_V1
            required = (2 if actual_fees else 1) * len(target.symbols) + 1
            if view.remaining_gets < required:
                _deny("MONITORING_INSTRUMENT_RECEIPT_CAPACITY")
            max_physical = (6 if actual_fees else 3) * len(target.symbols) + 3
            if 200 - budget.physical_attempts < max_physical:
                _deny("MONITORING_INSTRUMENT_RECEIPT_CAPACITY")
            budget.check()
            identity = owner.require_observation_binding(binding, scope=scope)
            if identity.scope != scope:
                _deny("MONITORING_INSTRUMENT_RECEIPT_ARM_REQUIRED")
            owner_guard()
            now, mono = self._clock()
            if self._slot is not None or self._generation is None:
                _deny("MONITORING_INSTRUMENT_RECEIPT_OVERLAP")
            slot = _PendingNativeInstrumentCaptureV1(
                owner=owner,
                generation=self._generation,
                session_generation=owner._monitoring_generation,
                binding=binding,
                attestation=owner._attested,
                scope=scope,
                assembly=assembly,
                channel=channel,
                client=client,
                provider=provider,
                adapter=adapter,
                cache=cache,
                target=target,
                target_fingerprint=target.fingerprint,
                witness=witness,
                symbols=target.symbols,
                budget=budget,
                group=token,
                owner_guard=owner_guard,
                armed_at=now,
                armed_mono=mono,
                last_wall=now,
                last_mono=mono,
                metadata_profile=client._metadata_profile,
                physical_start=budget.physical_attempts,
                logical_start=budget.require_active_group(token).logical_gets,
            )
            owner_guard()
            budget.require_active_group(token)
            self._slot = slot
            return slot
        except BaseException as error:
            self.revoke()
            if isinstance(error, asyncio.CancelledError):
                raise
            if isinstance(error, MonitoringError):
                raise
            _deny("MONITORING_INSTRUMENT_RECEIPT_ARM_REQUIRED")

    def require(
        self,
        slot: _PendingNativeInstrumentCaptureV1,
        *,
        active_group: bool = True,
        phases: tuple[str, ...] = ("ARMED", "INITIALIZING", "METADATA", "CACHED", "ISSUED"),
    ) -> None:
        from .account_binding import BybitDemoAccountSession
        from .native_monitor_client import MonitoringBybitExecutionClient

        try:
            owner = self._owner
            if (
                type(slot) is not _PendingNativeInstrumentCaptureV1
                or self._slot is not slot
                or slot.owner is not owner
                or slot.generation is not self._generation
                or slot.phase not in phases
                or type(owner) is not BybitDemoAccountSession
                or owner._closed
                or owner._attested is not slot.attestation
                or owner._binding is not slot.binding
                or owner._monitoring_generation is not slot.session_generation
                or type(slot.client) is not MonitoringBybitExecutionClient
                or slot.client._monitor_session is not owner
                or slot.client._monitor_target is not slot.target
                or slot.client._monitor_binding is not slot.binding
                or slot.client._monitor_attestation is not slot.attestation
                or slot.client._http_client is not slot.adapter
                or slot.client.bybit_instrument_provider is not slot.provider
                or slot.client._cache is not slot.cache
                or slot.client._metadata_profile != slot.metadata_profile
                or slot.adapter._binding is not slot.binding
                or slot.adapter._scope != slot.scope
                or slot.adapter._symbols != slot.symbols
                or slot.adapter._metadata_profile != slot.metadata_profile
                or slot.adapter._cancelled
                or slot.target.scope != slot.scope
                or slot.target.symbols != slot.symbols
                or slot.target.fingerprint != slot.target_fingerprint
                or slot.target._witness is not slot.witness
                or slot.witness._assembly is not slot.assembly
                or slot.witness._channel is not slot.channel
                or slot.witness._worker.binding is not slot.binding
                or slot.assembly.execution_client is not slot.client
            ):
                _deny("MONITORING_INSTRUMENT_RECEIPT_REVOKED")
            owner._guard()
            owner.require_observation_binding(slot.binding, scope=slot.scope)
            slot.witness.require()
            slot.owner_guard()
            if active_group:
                if owner._client._operation.get() is not slot.budget:
                    _deny("MONITORING_INSTRUMENT_RECEIPT_REVOKED")
                slot.budget.require_active_group(slot.group)
                slot.budget.check()
            now, mono = self._clock()
            maximum = min(owner._max_age, timedelta(seconds=30))
            if (
                not timedelta(0) <= now - slot.armed_at <= maximum
                or not 0 <= mono - slot.armed_mono <= maximum.total_seconds()
                or (slot.last_wall is not None and now < slot.last_wall)
                or (slot.last_mono is not None and mono < slot.last_mono)
            ):
                _deny("MONITORING_INSTRUMENT_RECEIPT_EXPIRED")
            slot.last_wall, slot.last_mono = now, mono
            if slot.view is not None:
                for item in slot.view.evidence:
                    if not timedelta(0) <= now - item.envelope.received_at <= maximum:
                        _deny("MONITORING_INSTRUMENT_RECEIPT_EXPIRED")
                    if (
                        slot.metadata_profile == SDK1231_OBSERVATION_UNKNOWN_FEES_V1
                        and not timedelta(0)
                        <= now - item.envelope.response_time
                        <= maximum
                    ):
                        _deny("MONITORING_INSTRUMENT_RECEIPT_EXPIRED")
                    fee_received = getattr(item, "fee", None)
                    if (
                        fee_received is not None
                        and not timedelta(0)
                        <= now - fee_received.received_at
                        <= maximum
                    ):
                        _deny("MONITORING_INSTRUMENT_RECEIPT_EXPIRED")
            elif slot.metadata_profile == SDK1231_OBSERVATION_UNKNOWN_FEES_V1:
                for item in slot.unknown_fee_evidence.values():
                    if (
                        not timedelta(0) <= now - item.envelope.received_at <= maximum
                        or not timedelta(0) <= now - item.envelope.response_time <= maximum
                    ):
                        _deny("MONITORING_INSTRUMENT_RECEIPT_EXPIRED")
            if self._slot is not slot or slot.generation is not self._generation:
                _deny("MONITORING_INSTRUMENT_RECEIPT_REVOKED")
        except BaseException as error:
            self.revoke(slot)
            if isinstance(error, asyncio.CancelledError):
                raise
            if isinstance(error, MonitoringError):
                raise
            _deny("MONITORING_INSTRUMENT_RECEIPT_REVOKED")

    def begin_initialization(self, slot: _PendingNativeInstrumentCaptureV1) -> None:
        self.require(slot, phases=("ARMED",))
        if slot.initializations != 0:
            self.revoke(slot)
            _deny("MONITORING_INSTRUMENT_PROVIDER_REINITIALIZED")
        slot.initializations = 1
        slot.phase = "INITIALIZING"

    def begin_symbol(self, slot: _PendingNativeInstrumentCaptureV1, symbol: str) -> None:
        self.require(slot, phases=("INITIALIZING",))
        if (
            type(symbol) is not str
            or symbol not in slot.symbols
            or slot.inflight
            or symbol in slot.inflight
            or symbol in slot.evidence
            or symbol in slot.unknown_fee_evidence
        ):
            self.revoke(slot)
            _deny("MONITORING_INSTRUMENT_DUPLICATE_OR_SCOPE")
        slot.inflight.add(symbol)

    def record_fee(self, slot: _PendingNativeInstrumentCaptureV1, fee: NativeFeeResponseV1) -> None:
        self.require(slot, phases=("INITIALIZING",))
        if (
            slot.metadata_profile != ACTUAL_ACCOUNT_FEE_V1
            or fee.symbol not in slot.inflight
            or fee.symbol in slot.fees
            or fee.scope != slot.scope
            or fee.logical_ordinal != slot.last_logical + 1
            or fee.physical_start != slot.last_physical
            or fee.physical_end > slot.budget.physical_attempts
        ):
            self.revoke(slot)
            _deny("MONITORING_INSTRUMENT_FEE_SOURCE_INVALID")
        slot.fees[fee.symbol] = fee
        slot.last_logical = fee.logical_ordinal
        slot.last_physical = fee.physical_end

    def record_instrument(
        self,
        slot: _PendingNativeInstrumentCaptureV1,
        evidence: NativeInstrumentEvidenceV1,
    ) -> None:
        self.require(slot, phases=("INITIALIZING",))
        symbol = evidence.key.symbol
        if (
            slot.metadata_profile != ACTUAL_ACCOUNT_FEE_V1
            or symbol not in slot.inflight
            or symbol in slot.evidence
            or slot.fees.get(symbol) != evidence.fee
            or evidence.key != InstrumentKey("BYBIT", "LINEAR", symbol)
            or evidence.envelope.logical_ordinal != slot.last_logical + 1
            or evidence.envelope.physical_start != slot.last_physical
            or evidence.envelope.physical_end > slot.budget.physical_attempts
        ):
            self.revoke(slot)
            _deny("MONITORING_INSTRUMENT_SOURCE_INVALID")
        slot.evidence[symbol] = evidence
        slot.inflight.remove(symbol)
        slot.last_logical = evidence.envelope.logical_ordinal
        slot.last_physical = evidence.envelope.physical_end

    def record_unknown_fee_instrument(
        self,
        slot: _PendingNativeInstrumentCaptureV1,
        evidence: NativeUnknownFeeInstrumentEvidenceV1,
    ) -> None:
        self.require(slot, phases=("INITIALIZING",))
        symbol = evidence.key.symbol
        if (
            slot.metadata_profile != SDK1231_OBSERVATION_UNKNOWN_FEES_V1
            or symbol not in slot.inflight
            or symbol in slot.unknown_fee_evidence
            or slot.fees
            or slot.evidence
            or evidence.metadata_profile != slot.metadata_profile
            or evidence.key != InstrumentKey("BYBIT", "LINEAR", symbol)
            or evidence.envelope.logical_ordinal != slot.last_logical + 1
            or evidence.envelope.physical_start != slot.last_physical
            or evidence.envelope.physical_end > slot.budget.physical_attempts
        ):
            self.revoke(slot)
            _deny("MONITORING_INSTRUMENT_SOURCE_INVALID")
        slot.unknown_fee_evidence[symbol] = evidence
        slot.inflight.remove(symbol)
        slot.last_logical = evidence.envelope.logical_ordinal
        slot.last_physical = evidence.envelope.physical_end

    def complete_initialization(self, slot: _PendingNativeInstrumentCaptureV1) -> None:
        self.require(slot, phases=("INITIALIZING",))
        unknown_fees = slot.metadata_profile == SDK1231_OBSERVATION_UNKNOWN_FEES_V1
        current_evidence = slot.unknown_fee_evidence if unknown_fees else slot.evidence
        if (
            slot.inflight
            or len(current_evidence) != len(slot.symbols)
            or set(current_evidence) != set(slot.symbols)
            or (unknown_fees and bool(slot.fees))
            or (not unknown_fees and set(slot.fees) != set(slot.symbols))
            or (not unknown_fees and slot.unknown_fee_evidence)
        ):
            self.revoke(slot)
            _deny("MONITORING_INSTRUMENT_INCOMPLETE")
        slot.phase = "METADATA"

    def require_provider_semantics(
        self,
        slot: _PendingNativeInstrumentCaptureV1,
        instruments: object,
        semantics_sha256: Callable[[Any], str],
    ) -> None:
        self.require(slot, phases=("METADATA", "CACHED"))
        if type(instruments) is not dict:
            self.revoke(slot)
            _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")
        expected = {symbol + "-LINEAR.BYBIT" for symbol in slot.symbols}
        if {str(key) for key in instruments} != expected:
            self.revoke(slot)
            _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")
        try:
            for key, instrument in instruments.items():
                symbol = str(key).removesuffix("-LINEAR.BYBIT")
                evidence = (
                    slot.unknown_fee_evidence[symbol]
                    if slot.metadata_profile == SDK1231_OBSERVATION_UNKNOWN_FEES_V1
                    else slot.evidence[symbol]
                )
                require_native_metadata_profile_v1(instrument, slot.metadata_profile)
                if semantics_sha256(instrument) != evidence.native_semantics_sha256:
                    self.revoke(slot)
                    _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")
        except BaseException as error:
            self.revoke(slot)
            if isinstance(error, asyncio.CancelledError):
                raise
            if isinstance(error, MonitoringError):
                raise
            _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")

    def cache_updated(self, slot: _PendingNativeInstrumentCaptureV1) -> None:
        self.require(slot, phases=("METADATA",))
        slot.phase = "CACHED"

    def require_cached_native_matches(self, slot: _PendingNativeInstrumentCaptureV1) -> None:
        self.require(slot, phases=("CACHED",))
        from .native_monitor_client import native_instrument_semantics_sha256

        fresh = slot.provider.get_all()
        self.require_provider_semantics(slot, fresh, native_instrument_semantics_sha256)
        for key, instrument in fresh.items():
            cached = slot.cache.instrument(key)
            if (
                cached is None
                or _native_metadata_profile_mismatch(cached, slot.metadata_profile)
                or native_instrument_semantics_sha256(cached)
                != native_instrument_semantics_sha256(instrument)
            ):
                self.revoke(slot)
                _deny("MONITORING_INSTRUMENT_CACHE_DRIFT")
        self.require(slot, phases=("CACHED",))

    def issue_after_wallet_ack(
        self,
        slot: _PendingNativeInstrumentCaptureV1,
        *,
        wallet_event: object,
    ) -> NativeInstrumentReceiptV1 | NativeUnknownFeeInstrumentReceiptV1:
        self.require(slot, phases=("CACHED",))
        unknown_fees = slot.metadata_profile == SDK1231_OBSERVATION_UNKNOWN_FEES_V1
        reads_per_symbol = 1 if unknown_fees else 2
        expected_gets = reads_per_symbol * len(slot.symbols) + 1
        max_physical = (3 if unknown_fees else 6) * len(slot.symbols) + 3
        current_evidence = slot.unknown_fee_evidence if unknown_fees else slot.evidence
        group = slot.budget.require_active_group(slot.group)
        physical = slot.budget.physical_attempts - slot.physical_start
        if (
            type(wallet_event) is not type(slot.client._monitor_account_event)
            or wallet_event is not slot.client._monitor_account_event
            or group.logical_gets - slot.logical_start != expected_gets
            or not expected_gets <= physical <= max_physical
            or slot.inflight
            or slot.wallet_reads != 1
            or len(current_evidence) != len(slot.symbols)
            or set(current_evidence) != set(slot.symbols)
            or (unknown_fees and (slot.fees or slot.evidence))
            or (not unknown_fees and (
                set(slot.fees) != set(slot.symbols) or slot.unknown_fee_evidence
            ))
        ):
            self.revoke(slot)
            _deny("MONITORING_INSTRUMENT_WALLET_ACK_REQUIRED")
        self.require_cached_native_matches(slot)
        account = slot.cache.account(slot.client.account_id)
        if account is None or account.last_event is not wallet_event:
            self.revoke(slot)
            _deny("MONITORING_INSTRUMENT_WALLET_ACK_REQUIRED")
        self.require(slot, phases=("CACHED",))
        now, _mono = self._clock()
        if unknown_fees:
            evidence = tuple(
                slot.unknown_fee_evidence[symbol] for symbol in slot.symbols
            )
            view = NativeUnknownFeeInstrumentReceiptViewV1(
                slot.metadata_profile,
                slot.scope,
                slot.target_fingerprint,
                slot.symbols,
                evidence,
                slot.armed_at,
                now,
            )
            handle = object.__new__(NativeUnknownFeeInstrumentReceiptV1)
        else:
            evidence = tuple(slot.evidence[symbol] for symbol in slot.symbols)
            view = NativeInstrumentReceiptViewV1(
                slot.scope, slot.target_fingerprint, slot.symbols, evidence, slot.armed_at, now
            )
            handle = object.__new__(NativeInstrumentReceiptV1)
        self.require(slot, phases=("CACHED",))
        slot.view, slot.handle, slot.phase = view, handle, "ISSUED"
        return handle

    def require_current(
        self,
        receipt: NativeInstrumentReceiptV1 | NativeUnknownFeeInstrumentReceiptV1,
        *,
        binding: object,
        scope: ProtectionScope,
        target: MonitoringTarget,
    ) -> NativeInstrumentReceiptViewV1 | NativeUnknownFeeInstrumentReceiptViewV1:
        slot = self._slot
        unknown_fees = (
            slot is not None
            and slot.metadata_profile == SDK1231_OBSERVATION_UNKNOWN_FEES_V1
        )
        if (
            type(receipt) is not (
                NativeUnknownFeeInstrumentReceiptV1
                if unknown_fees
                else NativeInstrumentReceiptV1
            )
            or slot is None
            or slot.handle is not receipt
            or slot.binding is not binding
            or slot.scope != scope
            or slot.target is not target
            or slot.view is None
            or unknown_fees
            and type(slot.view) is not NativeUnknownFeeInstrumentReceiptViewV1
            or not unknown_fees
            and type(slot.view) is not NativeInstrumentReceiptViewV1
        ):
            self.revoke(slot)
            _deny("MONITORING_INSTRUMENT_RECEIPT_REQUIRED")
        self.require(slot, active_group=False, phases=("ISSUED",))
        return slot.view

    def finish_startup(self, slot: _PendingNativeInstrumentCaptureV1, *, success: bool) -> None:
        if self._slot is not slot:
            return
        if not success or slot.phase != "ISSUED":
            self.revoke(slot)
        slot.startup_open = False
        if slot.phase == "REVOKED" and self._slot is slot:
            self._slot = None

    def revoke(self, slot: _PendingNativeInstrumentCaptureV1 | None = None) -> None:
        current = self._slot
        if current is None or slot is not None and current is not slot:
            return
        current.phase = "REVOKED"
        current.fees.clear()
        current.evidence.clear()
        current.unknown_fee_evidence.clear()
        current.inflight.clear()
        current.view = None
        current.handle = None
        self._generation = object()
        self._last_clock = None
        if not current.startup_open:
            self._slot = None


def native_instrument_semantics_sha256(instrument: Any) -> str:
    """Hash only the pinned semantic whitelist; acquisition time and info are excluded."""
    try:
        from .native_monitor_client import instrument_semantics

        semantic = instrument_semantics(instrument)
        semantic.pop("info", None)
        for key in ("base_currency", "quote_currency", "settlement_currency"):
            value = semantic.get(key)
            if type(value) is not tuple or len(value) != 5:
                _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")
            semantic[key] = list(value)
        encoded = json.dumps(
            semantic, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        if len(encoded) > 65536:
            _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")
        return hashlib.sha256(encoded).hexdigest()
    except BaseException as error:
        if isinstance(error, asyncio.CancelledError):
            raise
        if isinstance(error, MonitoringError):
            raise
        _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")


def _native_metadata_profile_mismatch(instrument: Any, profile: str) -> bool:
    try:
        from .observation_instrument_v1 import require_instrument_metadata_profile_v1

        require_instrument_metadata_profile_v1(instrument, profile)
        return False
    except BaseException as error:
        if isinstance(error, asyncio.CancelledError):
            raise
        return True


def require_native_metadata_profile_v1(instrument: Any, profile: str) -> None:
    if _native_metadata_profile_mismatch(instrument, profile):
        _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")


def require_native_matches_projection(
    projection: SupportedInstrumentProjection,
    instrument: Any,
    fee: NativeFeeResponseV1,
) -> str:
    """Compare exact T3 fields to SDK semantics without inventing venue limits."""
    try:
        if type(fee) is not NativeFeeResponseV1:
            _deny("MONITORING_INSTRUMENT_FEE_SOURCE_INVALID")
        return _require_native_projection_fields(
            projection,
            instrument,
            maker_fee=Decimal(fee.maker_fee_rate),
            taker_fee=Decimal(fee.taker_fee_rate),
        )
    except BaseException as error:
        if isinstance(error, asyncio.CancelledError):
            raise
        if isinstance(error, MonitoringError):
            raise
        _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")


def require_native_observation_matches_projection(
    projection: SupportedInstrumentProjection,
    instrument: Any,
) -> str:
    """Compare public source geometry while preserving UNKNOWN fee semantics."""
    try:
        from .observation_instrument_v1 import (
            SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
            require_instrument_metadata_profile_v1,
        )

        require_instrument_metadata_profile_v1(
            instrument, SDK1231_OBSERVATION_UNKNOWN_FEES_V1
        )
        return _require_native_projection_fields(
            projection,
            instrument,
            maker_fee=Decimal("0.001"),
            taker_fee=Decimal("0.001"),
        )
    except BaseException as error:
        if isinstance(error, asyncio.CancelledError):
            raise
        if isinstance(error, MonitoringError):
            raise
        _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")


def _require_native_projection_fields(
    projection: SupportedInstrumentProjection,
    instrument: Any,
    *,
    maker_fee: Decimal,
    taker_fee: Decimal,
) -> str:
    try:
        from nautilus_trader.core import nautilus_pyo3 as p
        from nautilus_trader.model.instruments import CryptoPerpetual, instruments_from_pyo3

        validate_supported_instrument_projection(projection)
        spec = projection.spec
        if type(spec) is not LinearInstrumentSpec:
            _deny("MONITORING_INSTRUMENT_SOURCE_INVALID")
        if type(instrument) is not p.CryptoPerpetual:
            _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")
        converted = instruments_from_pyo3([instrument])
        if (
            type(converted) is not list
            or len(converted) != 1
            or type(converted[0]) is not CryptoPerpetual
        ):
            _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")
        model_instrument = converted[0]
        raw = model_instrument.to_dict(model_instrument)
        if type(raw) is not dict:
            _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")
        currencies = {
            "base_currency": spec.base_coin,
            "quote_currency": spec.quote_coin,
            "settlement_currency": spec.settle_coin,
        }
        decimal_fields = {
            "price_increment": spec.price_tick.value,
            "size_increment": spec.quantity_step.value,
            "lot_size": spec.quantity_step.value,
            "max_quantity": spec.max_limit_quantity.value,
            "min_quantity": spec.min_quantity.value,
            "min_price": spec.min_price.value,
            "max_price": spec.max_price.value,
            "maker_fee": maker_fee,
            "taker_fee": taker_fee,
            "multiplier": Decimal(1),
            "margin_init": Decimal("0.1"),
            "margin_maint": Decimal("0.1"),
        }
        exact_fields = {
            "id": projection.native_instrument_id,
            "raw_symbol": projection.key.symbol,
            "is_inverse": False,
            "max_notional": None,
            "tick_scheme_name": None,
        }
        if any(raw.get(name) != value for name, value in exact_fields.items()):
            _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")
        for name, expected in decimal_fields.items():
            value = raw.get(name)
            if type(value) is not str or Decimal(value) != expected:
                _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")
        if raw.get("price_precision") != spec.price_tick.scale:
            _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")
        if raw.get("size_precision") != spec.quantity_step.scale:
            _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")
        for field_name, expected in currencies.items():
            if raw.get(field_name) != expected:
                _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")
        notional = raw.get("min_notional")
        if type(notional) is not str or notional.split(" ", 1)[-1] != spec.quote_coin:
            _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")
        try:
            if Decimal(notional.split(" ", 1)[0]) != spec.min_quote_amount.value:
                _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")
        except Exception:  # noqa: BLE001 - malformed SDK representation is fixed failure.
            _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")
        # Market cap, priceScale, leverage and KnownNotPublished are retained and
        # revalidated above in the source projection; they have no native field mapping.
        return native_instrument_semantics_sha256(model_instrument)
    except BaseException as error:
        if isinstance(error, asyncio.CancelledError):
            raise
        if isinstance(error, MonitoringError):
            raise
        _deny("MONITORING_INSTRUMENT_NATIVE_MISMATCH")
