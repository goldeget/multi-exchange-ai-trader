"""Unactivated DEMO/LINEAR native command contracts, with no HTTP order path.

This bridge is not a durable worker, risk gate, native node or account verifier.
The caller must persist immutable intent/ownership and claim it before invoking
submit, and reconcile ambiguous results under the same ID after a restart.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from importlib.metadata import version
from threading import RLock
from typing import Literal, Protocol, TypedDict

from .native_events import (
    NativeEventContext,
    NativeEventDecoder,
    NativeOrderEvidence,
    NativeReportBatch,
    bounded_decimal,
    load_native_event_decoder,
)
from .protection import OwnedOrder, ProtectionScope

NATIVE_VERSION = "1.231.0"


class NativeOrderFactory(Protocol):
    def market(self, *, instrument_id: object, order_side: object, quantity: object,
               client_order_id: object, reduce_only: bool) -> object: ...

    def limit(self, *, instrument_id: object, order_side: object, quantity: object,
              client_order_id: object, reduce_only: bool, price: object) -> object: ...


class NativeStrategy(Protocol):
    def submit_order(self, order: object, *, params: dict[str, object]) -> None: ...


class _FactoryArguments(TypedDict):
    instrument_id: object
    order_side: object
    quantity: object
    client_order_id: object
    reduce_only: bool


@dataclass(frozen=True)
class NativeBindings:
    instrument_id: Callable[[str], object]
    client_order_id: Callable[[str], object]
    quantity: Callable[[str], object]
    price: Callable[[str], object]
    order_side: Callable[[str], object]


@dataclass(frozen=True)
class AttachedProtection:
    stop_loss: Decimal
    take_profit: Decimal
    trigger_source: str

    def __post_init__(self) -> None:
        bounded_decimal(self.stop_loss)
        bounded_decimal(self.take_profit)
        if self.trigger_source not in ("MarkPrice", "LastPrice", "IndexPrice"):
            raise ValueError("NATIVE_TRIGGER_SOURCE_REQUIRED")


@dataclass(frozen=True)
class NativeOrderRequest:
    scope: ProtectionScope
    owned: OwnedOrder
    order_type: Literal["MARKET", "LIMIT"]
    reference_price: Decimal
    limit_price: Decimal | None = None
    attached: AttachedProtection | None = None
    position_idx: Literal[0] = 0
    # Explicitly rejected fields prevent accidental DEMO custom-trigger loss.
    tp_trigger_price: Decimal | None = None
    sl_trigger_price: Decimal | None = None

    def __post_init__(self) -> None:
        if (type(self.scope) is not ProtectionScope or self.scope.environment != "DEMO"
                or type(self.owned) is not OwnedOrder):
            raise ValueError("EXPLICIT_NATIVE_DEMO_OWNERSHIP_REQUIRED")
        owned = self.owned
        if (re.fullmatch(r"ec_[0-9a-f]{32}", owned.client_order_id) is None
                or re.fullmatch(r"[A-Z0-9]{2,48}-LINEAR\.BYBIT", owned.instrument_id) is None
                or owned.role not in ("ENTRY", "EXIT")
                or owned.reduce_only != (owned.role == "EXIT")
                or self.order_type not in ("MARKET", "LIMIT")
                or type(self.position_idx) is not int or self.position_idx != 0
                or self.tp_trigger_price is not None or self.sl_trigger_price is not None):
            raise ValueError("UNSUPPORTED_NATIVE_DEMO_COMMAND")
        bounded_decimal(owned.quantity)
        bounded_decimal(self.reference_price)
        if self.order_type == "LIMIT":
            bounded_decimal(self.limit_price)
        elif self.limit_price is not None:
            raise ValueError("NATIVE_MARKET_PRICE_FORBIDDEN")
        if self.attached is not None:
            if type(self.attached) is not AttachedProtection or owned.reduce_only:
                raise ValueError("NATIVE_ATTACHED_EXIT_FORBIDDEN")
            stop, tp = self.attached.stop_loss, self.attached.take_profit
            reference = self.reference_price
            if ((owned.side == "BUY" and not stop < reference < tp)
                    or (owned.side == "SELL" and not tp < reference < stop)):
                raise ValueError("NATIVE_PROTECTION_DIRECTION_INVALID")
            if self.limit_price is not None and (
                (owned.side == "BUY" and not stop < self.limit_price < tp)
                or (owned.side == "SELL" and not tp < self.limit_price < stop)
            ):
                raise ValueError("NATIVE_PROTECTION_DIRECTION_INVALID")

    def command_params(self) -> dict[str, object]:
        result: dict[str, object] = {"position_idx": 0}
        if self.attached is not None:
            attached = self.attached
            result.update(take_profit=format(attached.take_profit, "f"),
                stop_loss=format(attached.stop_loss, "f"),
                tp_trigger_by=attached.trigger_source, sl_trigger_by=attached.trigger_source,
                tp_order_type="Market", sl_order_type="Market", tpsl_mode="Full")
        return result


@dataclass(frozen=True)
class NativeSubmission:
    client_order_id: str
    state: Literal["SUBMITTING", "UNKNOWN"]
    code: Literal["NATIVE_SUBMIT_INVOKED", "NATIVE_SUBMIT_AMBIGUOUS"]


def _verify_built_order(order: object, request: NativeOrderRequest) -> None:
    """Native precision/conversions must not silently change immutable intent."""
    owned = request.owned
    side = getattr(order, "side", None)
    if (str(getattr(order, "client_order_id", None)) != owned.client_order_id
            or str(getattr(order, "instrument_id", None)) != owned.instrument_id
            or getattr(side, "name", side) != owned.side
            or bounded_decimal(str(getattr(order, "quantity", None))) != owned.quantity
            or getattr(order, "is_reduce_only", None) is not owned.reduce_only):
        raise ValueError("NATIVE_ORDER_CHANGED")
    price = getattr(order, "price", None)
    if request.order_type == "LIMIT":
        if bounded_decimal(str(price)) != request.limit_price:
            raise ValueError("NATIVE_ORDER_CHANGED")
    elif price is not None:
        raise ValueError("NATIVE_ORDER_CHANGED")


class NativeBybitBridge:
    """Process-local async handoff; no acceptance is inferred from a return value.

    A fresh bridge never authorizes replay after a crash. The durable caller owns
    recovery and the exact account/environment binding to the native channel.
    """

    production_supported: Literal[False] = False

    def __init__(self, *, scope: ProtectionScope, native_account_id: str,
                 factory: NativeOrderFactory, strategy: NativeStrategy,
                 bindings: NativeBindings) -> None:
        NativeEventContext(scope, native_account_id, 0)
        self.scope = scope
        self.native_account_id = native_account_id
        self._factory = factory
        self._strategy = strategy
        self._bindings = bindings
        self._requests: dict[str, NativeOrderRequest] = {}
        self._submissions: dict[str, NativeSubmission] = {}
        self._lock = RLock()

    def register_owned(self, request: NativeOrderRequest) -> None:
        """Restore an exact externally persisted mapping without submitting."""
        with self._lock:
            if type(request) is not NativeOrderRequest or request.scope != self.scope:
                raise ValueError("MATCHING_NATIVE_SCOPE_REQUIRED")
            key = request.owned.client_order_id
            previous = self._requests.get(key)
            if previous is not None and previous != request:
                raise ValueError("IMMUTABLE_NATIVE_COMMAND_CONFLICT")
            self._requests[key] = request

    def submit(self, request: NativeOrderRequest) -> NativeSubmission:
        with self._lock:
            self.register_owned(request)
            key = request.owned.client_order_id
            if key in self._submissions:
                return self._submissions[key]
            owned, native = request.owned, self._bindings
            try:
                arguments: _FactoryArguments = {
                    "instrument_id": native.instrument_id(owned.instrument_id),
                    "order_side": native.order_side(owned.side),
                    "quantity": native.quantity(format(owned.quantity, "f")),
                    "client_order_id": native.client_order_id(key),
                    "reduce_only": owned.reduce_only}
                if request.order_type == "LIMIT":
                    assert request.limit_price is not None
                    order = self._factory.limit(**arguments,
                        price=native.price(format(request.limit_price, "f")))
                else:
                    order = self._factory.market(**arguments)
                _verify_built_order(order, request)
                if request.attached is not None:
                    for value in (request.attached.stop_loss, request.attached.take_profit):
                        if bounded_decimal(str(native.price(format(value, "f")))) != value:
                            raise ValueError("NATIVE_PROTECTION_PRECISION_CHANGED")
            except Exception:  # noqa: BLE001 - invalid native conversions never leak input.
                raise ValueError("NATIVE_ORDER_BUILD_FAILED") from None
            # Set before invocation, including reentrant callbacks. This is not a venue ACK.
            result = NativeSubmission(key, "SUBMITTING", "NATIVE_SUBMIT_INVOKED")
            self._submissions[key] = result
            try:
                self._strategy.submit_order(order, params=request.command_params())
            except Exception:  # noqa: BLE001 - an invocation failure can have sent a command.
                result = NativeSubmission(key, "UNKNOWN", "NATIVE_SUBMIT_AMBIGUOUS")
                self._submissions[key] = result
            return result

    def consume(self, event: object, context: NativeEventContext, *,
                decoder: NativeEventDecoder | None = None) -> NativeOrderEvidence:
        with self._lock:
            if (type(context) is not NativeEventContext or context.scope != self.scope
                    or context.native_account_id != self.native_account_id):
                raise ValueError("NATIVE_EVENT_CONTEXT_MISMATCH")
            try:
                key = str(getattr(event, "client_order_id", ""))
            except Exception:  # noqa: BLE001 - property/string conversion errors are untrusted.
                raise ValueError("NATIVE_EVENT_INVALID_OR_UNOWNED") from None
            request = self._requests.get(key)
            if request is None:
                raise ValueError("NATIVE_EVENT_EXACT_OWNERSHIP_REQUIRED")
            return (decoder or load_native_event_decoder()).decode(event, context, request.owned)

    def consume_reports(self, reports: tuple[object, ...], context: NativeEventContext, *,
                        query_succeeded: bool, query_complete: bool,
                        decoder: NativeEventDecoder | None = None) -> NativeReportBatch:
        evidence: list[NativeOrderEvidence] = []
        valid = type(reports) is tuple and bool(reports)
        for report in reports:
            try:
                evidence.append(self.consume(report, context, decoder=decoder))
            except ValueError:
                valid = False
        return NativeReportBatch(tuple(evidence),
            valid and query_succeeded is True and query_complete is True)


def _require_native_version() -> None:
    if version("nautilus-trader") != NATIVE_VERSION:
        raise ValueError("PINNED_NATIVE_VERSION_REQUIRED")


def load_native_bindings() -> NativeBindings:
    """Lazy imports keep Windows/public/offline imports independent of native wheel."""
    _require_native_version()
    identifiers = importlib.import_module("nautilus_trader.model.identifiers")
    objects = importlib.import_module("nautilus_trader.model.objects")
    enums = importlib.import_module("nautilus_trader.model.enums")
    return NativeBindings(identifiers.InstrumentId.from_str, identifiers.ClientOrderId,
        objects.Quantity.from_str, objects.Price.from_str,
        lambda side: getattr(enums.OrderSide, side))


def build_demo_exec_config(*, api_key: str, api_secret: str, max_retries: int = 1) -> object:
    """Build only; never create a client/node, connect, or read environment credentials.

    PositiveInt excludes zero in the pinned config. One adapter HTTP retry is
    explicit; no claim of zero retransmissions or application-level retry is made.
    """
    if (not isinstance(api_key, str) or not api_key.strip()
            or not isinstance(api_secret, str) or not api_secret.strip()
            or type(max_retries) is not int or not 1 <= max_retries <= 10):
        raise ValueError("EXPLICIT_NATIVE_CREDENTIALS_AND_POSITIVE_RETRY_REQUIRED")
    _require_native_version()
    config = importlib.import_module("nautilus_trader.adapters.bybit.config")
    native = importlib.import_module("nautilus_trader.core.nautilus_pyo3")
    return config.BybitExecClientConfig(api_key=api_key, api_secret=api_secret,
        environment=native.BybitEnvironment.DEMO, product_types=(native.BybitProductType.LINEAR,),
        max_retries=max_retries, retry_delay_initial_ms=500, retry_delay_max_ms=1000,
        recv_window_ms=5000, futures_leverages=None, margin_mode=None, position_mode=None,
        auto_repay_spot_borrows=False, use_spot_position_reports=False,
        use_ws_execution_fast=False, base_url_http=None, base_url_ws_private=None,
        base_url_ws_trade=None, proxy_url=None)
