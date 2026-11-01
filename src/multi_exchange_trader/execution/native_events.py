"""Scoped native evidence, separate from transport and owned-state reduction.

The caller binds a trusted native event channel to an already verified account
scope. ``BYBIT-UNIFIED`` is an adapter label, never an authenticated Bybit UID.
These objects prove neither protection nor complete account reconciliation.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from importlib.metadata import version
from types import MappingProxyType
from typing import Literal

from .protection import FillEvent, OwnedOrder, ProtectionScope, Side

EvidenceKind = Literal["SUBMITTED", "ACCEPTED", "DENIED", "REJECTED", "CANCELED",
                       "EXPIRED", "FILL", "STATUS"]
_KINDS: dict[str, EvidenceKind] = {
    "OrderSubmitted": "SUBMITTED", "OrderAccepted": "ACCEPTED", "OrderDenied": "DENIED",
    "OrderRejected": "REJECTED", "OrderCanceled": "CANCELED", "OrderExpired": "EXPIRED",
    "OrderFilled": "FILL", "FillReport": "FILL", "OrderStatusReport": "STATUS",
}
_STATUSES = frozenset({"INITIALIZED", "DENIED", "EMULATED", "RELEASED", "SUBMITTED",
    "ACCEPTED", "REJECTED", "CANCELED", "EXPIRED", "TRIGGERED", "PENDING_UPDATE",
    "PENDING_CANCEL", "PARTIALLY_FILLED", "FILLED"})


def bounded_decimal(value: object, *, positive: bool = True) -> Decimal:
    """Keep exact native decimal values; never use binary floating point."""
    try:
        if isinstance(value, Decimal):
            result = value
        elif isinstance(value, str) and len(value) <= 2048:
            result = Decimal(value)
        else:
            raise ValueError("NATIVE_EVENT_DECIMAL_REQUIRED")
        if (not result.is_finite() or len(result.as_tuple().digits) > 256
                or abs(int(result.as_tuple().exponent)) > 1000
                or (result != 0 and abs(result.adjusted()) > 1000)
                or (positive and result <= 0)):
            raise ValueError("NATIVE_EVENT_DECIMAL_REQUIRED")
        return result
    except (InvalidOperation, ValueError):
        raise ValueError("NATIVE_EVENT_DECIMAL_REQUIRED") from None


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("NATIVE_EVENT_ID_REQUIRED")
    return value


def _ns(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 253402300799999999999:
        raise ValueError("NATIVE_EVENT_TIMESTAMP_REQUIRED")
    return value


def _native_text(value: object) -> str:
    return _text(str(value)) if value is not None else _text(None)


def _decimal_object(value: object, *, positive: bool = True) -> Decimal:
    if isinstance(value, Decimal):
        return bounded_decimal(value, positive=positive)
    method = getattr(value, "as_decimal", None)
    return bounded_decimal(method() if callable(method) else str(value), positive=positive)


def _side(value: object) -> Side:
    name = getattr(value, "name", value)
    if name == "BUY":
        return "BUY"
    if name == "SELL":
        return "SELL"
    raise ValueError("NATIVE_EVENT_SIDE_REQUIRED")


@dataclass(frozen=True)
class NativeEventContext:
    """Provenance supplied by an isolated caller, not inferred from label text."""

    scope: ProtectionScope
    native_account_id: str
    received_at_ns: int

    def __post_init__(self) -> None:
        if type(self.scope) is not ProtectionScope or self.scope.environment != "DEMO":
            raise ValueError("NATIVE_EVENT_DEMO_SCOPE_REQUIRED")
        _text(self.scope.account_id)
        _text(self.native_account_id)
        _ns(self.received_at_ns)


@dataclass(frozen=True)
class NativeFillEvidence:
    scope: ProtectionScope
    execution_id: str
    client_order_id: str
    position_id: str
    instrument_id: str
    side: Side
    quantity: Decimal
    price: Decimal
    commission: Decimal
    commission_currency: str
    ts_event_ns: int
    ts_init_ns: int
    received_at_ns: int

    def to_owned_fill(self) -> FillEvent:
        # Owned projection uses microseconds; the full native ns remain above.
        occurred_at = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            microseconds=self.ts_event_ns // 1000)
        return FillEvent(self.scope, self.execution_id, self.client_order_id,
            self.position_id, self.instrument_id, self.side, self.quantity, occurred_at)


@dataclass(frozen=True)
class NativeOrderEvidence:
    scope: ProtectionScope
    client_order_id: str
    instrument_id: str
    kind: EvidenceKind
    native_account_id: str
    venue_order_id: str | None
    ts_event_ns: int
    ts_init_ns: int
    received_at_ns: int
    fill: NativeFillEvidence | None = None
    native_status: str | None = None
    # A status report cannot establish all executions, residual exposure or protection.
    terminal_confirmed: Literal[False] = False


@dataclass(frozen=True)
class NativeReportBatch:
    evidence: tuple[NativeOrderEvidence, ...]
    complete: bool
    flat_verified: Literal[False] = False
    leverage_verified: Literal[False] = False
    protection_verified: Literal[False] = False


class NativeEventDecoder:
    """Exact type registry; a look-alike class name is never sufficient.

    Portable tests explicitly inject their fake registry. Production callers
    use ``load_native_event_decoder`` for the pinned native types.
    """

    def __init__(self, *, event_types: Mapping[type[object], str]) -> None:
        self._types = MappingProxyType(dict(event_types))

    def decode(self, event: object, context: NativeEventContext,
               owned: OwnedOrder) -> NativeOrderEvidence:
        try:
            return self._decode(event, context, owned)
        except Exception:  # noqa: BLE001 - untrusted native errors/reason text stay private.
            raise ValueError("NATIVE_EVENT_INVALID_OR_UNOWNED") from None

    def _decode(self, event: object, context: NativeEventContext,
                owned: OwnedOrder) -> NativeOrderEvidence:
        name = self._types.get(type(event))
        if name not in _KINDS:
            raise ValueError("NATIVE_EVENT_UNSUPPORTED")
        client_id = _native_text(getattr(event, "client_order_id", None))
        instrument = _native_text(getattr(event, "instrument_id", None))
        if (client_id, instrument) != (owned.client_order_id, owned.instrument_id):
            raise ValueError("NATIVE_EVENT_OWNERSHIP_MISMATCH")
        account = getattr(event, "account_id", None)
        # OrderDenied is local pre-submit evidence and has no native account field.
        if ((name != "OrderDenied" or account is not None)
                and _native_text(account) != context.native_account_id):
            raise ValueError("NATIVE_EVENT_ACCOUNT_MISMATCH")
        kind = _KINDS[name]
        event_ns = _ns(getattr(event, "ts_last" if name == "OrderStatusReport"
            else "ts_event", None))
        init_ns = _ns(getattr(event, "ts_init", None))
        side = getattr(event, "order_side", None)
        if side is not None and _side(side) != owned.side:
            raise ValueError("NATIVE_EVENT_SIDE_MISMATCH")
        fill: NativeFillEvidence | None = None
        status: str | None = None
        if kind == "FILL":
            if _side(side) != owned.side:
                raise ValueError("NATIVE_EVENT_SIDE_MISMATCH")
            commission = getattr(event, "commission", None)
            currency = getattr(commission, "currency", None)
            currency_text = _native_text(getattr(currency, "code", currency))
            if re.fullmatch(r"[A-Za-z0-9]{1,32}", currency_text) is None:
                raise ValueError("NATIVE_EVENT_CURRENCY_REQUIRED")
            fill = NativeFillEvidence(context.scope,
                _native_text(getattr(event, "trade_id", None)), client_id, owned.position_id,
                instrument, owned.side, _decimal_object(getattr(event, "last_qty", None)),
                _decimal_object(getattr(event, "last_px", None)),
                _decimal_object(commission, positive=False), currency_text, event_ns,
                init_ns, context.received_at_ns)
        elif kind == "STATUS":
            value = getattr(event, "order_status", None)
            status = _native_text(getattr(value, "name", value))
            if status not in _STATUSES or _side(side) != owned.side:
                raise ValueError("NATIVE_EVENT_STATUS_REQUIRED")
            # Cumulative filled_qty is deliberately not converted to an execution.
            quantity = _decimal_object(getattr(event, "quantity", None))
            if quantity != owned.quantity:
                raise ValueError("NATIVE_EVENT_ORDER_CHANGED")
        venue = getattr(event, "venue_order_id", None)
        return NativeOrderEvidence(context.scope, client_id, instrument, kind,
            context.native_account_id, None if venue is None else _native_text(venue),
            event_ns, init_ns, context.received_at_ns, fill, status)


def load_native_event_decoder() -> NativeEventDecoder:
    """Lazy exact installed native event/report types; never opens a transport."""
    if version("nautilus-trader") != "1.231.0":
        raise ValueError("PINNED_NATIVE_VERSION_REQUIRED")
    order_events = importlib.import_module("nautilus_trader.model.events.order")
    reports = importlib.import_module("nautilus_trader.execution.reports")
    event_types: dict[type[object], str] = {}
    for name in _KINDS:
        module = reports if name.endswith("Report") else order_events
        native_type = getattr(module, name)
        event_types[native_type] = name
    return NativeEventDecoder(event_types=event_types)
