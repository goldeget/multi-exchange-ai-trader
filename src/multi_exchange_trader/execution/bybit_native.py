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
from typing import Any, Literal, Protocol, TypedDict, cast

from . import risk_v2_contracts as c
from .native_events import (
    NativeEventContext,
    NativeEventDecoder,
    NativeOrderEvidence,
    NativeReportBatch,
    bounded_decimal,
    load_native_event_decoder,
)
from .product_scope import ProductScope, _validate_account, _validate_scope
from .protection import OwnedOrder, ProtectionScope

NATIVE_VERSION = "1.231.0"


class NativeOrderFactory(Protocol):
    def market(self, *, instrument_id: object, order_side: object, quantity: object,
               client_order_id: object, reduce_only: bool) -> object: ...

    def limit(self, *, instrument_id: object, order_side: object, quantity: object,
              client_order_id: object, reduce_only: bool, price: object) -> object: ...


class _FixedLimitFactoryV2(Protocol):
    def limit(self, *, instrument_id: object, order_side: object, quantity: object,
              client_order_id: object, reduce_only: bool, price: object,
              time_in_force: object) -> object: ...


class NativeStrategy(Protocol):
    def submit_order(self, order: object, *, params: dict[str, object]) -> None: ...

    def cancel_order(self, order: object, *, params: dict[str, object]) -> None: ...


class _FactoryArguments(TypedDict):
    instrument_id: object
    order_side: object
    quantity: object
    client_order_id: object
    reduce_only: bool


@dataclass(frozen=True)
class FixedLimitBindingsV2:
    limit_order_type: type[object]
    initialized_event_type: type[object]
    updated_event_type: type[object]
    order_type_limit: object
    time_in_force_ioc: object


def _fixed_limit_bindings_v2(value: object) -> FixedLimitBindingsV2:
    if type(value) is not FixedLimitBindingsV2:
        raise ValueError("NATIVE_FIXED_LIMIT_BINDINGS_REQUIRED")
    try:
        classes = (value.limit_order_type, value.initialized_event_type, value.updated_event_type)
        kind, tif = value.order_type_limit, value.time_in_force_ioc
    except AttributeError:
        raise ValueError("NATIVE_FIXED_LIMIT_BINDINGS_REQUIRED") from None
    if (any(not isinstance(cls, type) or cls is object for cls in classes)
            or len(set(classes)) != 3):
        raise ValueError("NATIVE_FIXED_LIMIT_BINDINGS_REQUIRED")
    if (type(kind) is type(tif)
            or getattr(kind, "name", None) != "LIMIT"
            or getattr(tif, "name", None) != "IOC"
            or getattr(type(kind), "LIMIT", None) is not kind
            or getattr(type(tif), "IOC", None) is not tif):
        raise ValueError("NATIVE_FIXED_LIMIT_BINDINGS_REQUIRED")
    return value


@dataclass(frozen=True)
class NativeBindings:
    instrument_id: Callable[[str], object]
    client_order_id: Callable[[str], object]
    quantity: Callable[[str], object]
    price: Callable[[str], object]
    order_side: Callable[[str], object]
    fixed_limit_v2: FixedLimitBindingsV2 | None = None


@dataclass(frozen=True)
class _FixedLimitContextV2:
    """One bridge's original associations; structural labels grant no authority."""
    bindings: NativeBindings
    fixed: FixedLimitBindingsV2
    selection: tuple[object, ...]
    converters: tuple[Callable[[str], object], ...]
    scope_values: tuple[str, str]
    native_account_id: str
    factory: NativeOrderFactory
    strategy: NativeStrategy

    def verifier_binding(self) -> FixedLimitBindingsV2:
        # A fresh verifier view never reads the original mutable record's fields.
        return FixedLimitBindingsV2(cast(type[object], self.selection[0]),
            cast(type[object], self.selection[1]), cast(type[object], self.selection[2]),
            self.selection[3], self.selection[4])


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
class LinearFixedLimitNativeOrderRequestV2:
    product_scope: ProductScope
    owned: OwnedOrder
    envelope: c.FixedLimitEnvelopeV2
    attached: AttachedProtection
    position_idx: Literal[0]

    def __post_init__(self) -> None:
        _fixed_limit_identity_v2(self)

    @property
    def scope(self) -> ProtectionScope:
        return self.product_scope.account

    @property
    def order_type(self) -> Literal["LIMIT"]:
        return "LIMIT"

    @property
    def limit_price(self) -> Decimal:
        return self.envelope.limit_price


NativeRequest = NativeOrderRequest | LinearFixedLimitNativeOrderRequestV2


def _fixed_limit_identity_v2(request: LinearFixedLimitNativeOrderRequestV2) -> tuple[object, ...]:
    """Detached process-local identity, never an issuer or a durable claim."""
    if type(request) is not LinearFixedLimitNativeOrderRequestV2:
        raise ValueError("UNSUPPORTED_NATIVE_DEMO_COMMAND")
    c._fields_v2(request, LinearFixedLimitNativeOrderRequestV2)
    digest = c.envelope_sha256_v2(request.envelope)
    scope, owned, envelope, attached = (
        request.product_scope, request.owned, request.envelope, request.attached)
    _validate_scope(scope)
    c._fields_v2(owned, OwnedOrder)
    c._fields_v2(attached, AttachedProtection)
    if (type(scope) is not ProductScope or scope != envelope.scope.product_scope
            or scope.product != "LINEAR" or scope.account.environment != "DEMO"
            or type(owned) is not OwnedOrder or type(attached) is not AttachedProtection
            or type(request.position_idx) is not int or request.position_idx != 0):
        raise ValueError("UNSUPPORTED_NATIVE_DEMO_COMMAND")
    c._identifier_v2(owned.position_id)
    c._identifier_v2(owned.policy_revision)
    if (type(owned.client_order_id) is not str
            or re.fullmatch(r"ec_[0-9a-f]{32}", owned.client_order_id) is None
            or type(owned.instrument_id) is not str
            or owned.instrument_id != envelope.instrument_pin.native_instrument_id
            or type(owned.side) is not str or owned.side != envelope.side
            or type(owned.role) is not str or owned.role != "ENTRY"
            or owned.reduce_only is not False
            or type(owned.generation) is not int or owned.generation < 0
            or owned.generation.bit_length() > 63
            or owned.trigger_price is not None or owned.trigger_source is not None
            or owned.policy_revision != envelope.policy_revision
            or type(owned.quantity) is not Decimal or owned.quantity != envelope.base_quantity):
        raise ValueError("UNSUPPORTED_NATIVE_DEMO_COMMAND")
    for number in (owned.quantity, envelope.limit_price, attached.stop_loss, attached.take_profit):
        if type(number) is not Decimal:
            raise ValueError("NATIVE_EVENT_DECIMAL_REQUIRED")
        bounded_decimal(number)
    if (type(attached.trigger_source) is not str
            or attached.trigger_source not in ("MarkPrice", "LastPrice", "IndexPrice")):
        raise ValueError("NATIVE_TRIGGER_SOURCE_REQUIRED")
    if ((owned.side == "BUY" and not attached.stop_loss < envelope.limit_price < attached.take_profit)
            or (owned.side == "SELL"
                and not attached.take_profit < envelope.limit_price < attached.stop_loss)):
        raise ValueError("NATIVE_PROTECTION_DIRECTION_INVALID")
    return (scope.account.account_id, scope.account.environment, scope.product,
        owned.client_order_id, owned.position_id, owned.instrument_id, owned.side, owned.role,
        owned.quantity.as_tuple(), owned.reduce_only, owned.generation, owned.policy_revision,
        owned.trigger_price, owned.trigger_source, digest, envelope.base_quantity.as_tuple(),
        envelope.limit_price.as_tuple(), attached.stop_loss.as_tuple(), attached.take_profit.as_tuple(),
        attached.trigger_source, request.position_idx)


def _fixed_limit_params_v2(request: LinearFixedLimitNativeOrderRequestV2) -> dict[str, object]:
    attached = request.attached
    return {"position_idx": 0, "take_profit": format(attached.take_profit, "f"),
        "stop_loss": format(attached.stop_loss, "f"), "tp_trigger_by": attached.trigger_source,
        "sl_trigger_by": attached.trigger_source, "tp_order_type": "Market",
        "sl_order_type": "Market", "tpsl_mode": "Full"}


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


def _verify_fixed_limit_order_v2(order: Any, request: LinearFixedLimitNativeOrderRequestV2,
                               binding: FixedLimitBindingsV2, strategy: NativeStrategy, *,
                               building: bool) -> None:
    """Finite current/seed check; no whole-history or native-channel proof."""
    if type(order) is not binding.limit_order_type:
        raise ValueError("NATIVE_ORDER_CHANGED")
    order = cast(Any, order)  # Exact injected native class has no portable SDK annotation.
    events = order.events
    if (type(events) not in (list, tuple) or not events
            or type(events[0]) is not binding.initialized_event_type
            or (building and len(events) != 1)
            or any(type(event) is binding.updated_event_type for event in events)):
        raise ValueError("NATIVE_ORDER_CHANGED")
    seed: Any = events[0]
    for current in (order, seed):
        current = cast(Any, current)
        if (str(current.client_order_id) != request.owned.client_order_id
                or str(current.instrument_id) != request.owned.instrument_id
                or current.strategy_id is None
                or current.strategy_id != getattr(strategy, "id", None)
                or current.trader_id is None or current.trader_id != order.trader_id
                or getattr(current.side, "name", current.side) != request.owned.side
                or bounded_decimal(str(current.quantity)) != request.owned.quantity):
            raise ValueError("NATIVE_ORDER_CHANGED")
        for actual, expected in ((current.order_type, binding.order_type_limit),
                                 (current.time_in_force, binding.time_in_force_ioc)):
            if type(actual) is not type(expected) or actual != expected:
                raise ValueError("NATIVE_ORDER_CHANGED")
        if (current.emulation_trigger.name != "NO_TRIGGER"
                or current.contingency_type.name != "NO_CONTINGENCY"
                or current.order_list_id is not None
                or not (current.linked_order_ids is None
                        or (type(current.linked_order_ids) is list and not current.linked_order_ids))
                or current.parent_order_id is not None or current.exec_algorithm_id is not None
                or current.exec_spawn_id is not None):
            raise ValueError("NATIVE_ORDER_CHANGED")
    if (order.is_post_only is not False or order.is_reduce_only is not False
            or order.is_quote_quantity is not False or seed.post_only is not False
            or seed.reduce_only is not False or seed.quote_quantity is not False
            or order.display_qty is not None or seed.options["display_qty"] is not None
            or type(order.expire_time_ns) is not int or order.expire_time_ns != 0
            or type(seed.options["expire_time_ns"]) is not int
            or seed.options["expire_time_ns"] != 0
            or bounded_decimal(str(order.price)) != request.limit_price
            or bounded_decimal(str(seed.options["price"])) != request.limit_price):
        raise ValueError("NATIVE_ORDER_CHANGED")
    if building and (order.status.name != "INITIALIZED" or order.account_id is not None
                     or order.venue_order_id is not None):
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
        self._requests: dict[str, NativeRequest] = {}
        self._submissions: dict[str, NativeSubmission] = {}
        self._cancellations: dict[str, NativeSubmission] = {}
        self._identities_v2: dict[str, tuple[object, ...]] = {}
        self._building_v2: set[str] = set()
        self._context_v2: _FixedLimitContextV2 | None = None
        self._lock = RLock()

    def register_owned(self, request: NativeOrderRequest) -> None:
        """Restore an exact externally persisted mapping without submitting."""
        with self._lock:
            if type(request) is not NativeOrderRequest or request.scope != self.scope:
                raise ValueError("MATCHING_NATIVE_SCOPE_REQUIRED")
            key = request.owned.client_order_id
            previous = self._requests.get(key)
            if previous is not None and (type(previous) is not NativeOrderRequest
                                         or previous != request):
                raise ValueError("IMMUTABLE_NATIVE_COMMAND_CONFLICT")
            self._requests[key] = request

    def _current_context_v2(self) -> _FixedLimitContextV2:
        native = self._bindings
        c._fields_v2(native, NativeBindings)
        fixed = _fixed_limit_bindings_v2(native.fixed_limit_v2)
        converters = (native.instrument_id, native.client_order_id, native.quantity,
                      native.price, native.order_side)
        if not all(callable(converter) for converter in converters):
            raise ValueError("NATIVE_FIXED_LIMIT_BINDINGS_REQUIRED")
        _validate_account(self.scope)
        if type(self.native_account_id) is not str:
            raise ValueError("NATIVE_EVENT_ID_REQUIRED")
        NativeEventContext(self.scope, self.native_account_id, 0)
        return _FixedLimitContextV2(native, fixed,
            (fixed.limit_order_type, fixed.initialized_event_type, fixed.updated_event_type,
             fixed.order_type_limit, fixed.time_in_force_ioc), converters,
            (self.scope.account_id, self.scope.environment), self.native_account_id,
            self._factory, self._strategy)

    def _checked_context_v2(self) -> _FixedLimitContextV2:
        original = self._context_v2
        current = self._current_context_v2()
        if (original is None or current.bindings is not original.bindings
                or current.fixed is not original.fixed
                or any(actual is not expected for actual, expected in
                       zip(current.selection, original.selection, strict=True))
                or any(actual is not expected for actual, expected in
                       zip(current.converters, original.converters, strict=True))
                or current.scope_values != original.scope_values
                or current.native_account_id != original.native_account_id
                or current.factory is not original.factory or current.strategy is not original.strategy):
            raise ValueError("IMMUTABLE_NATIVE_COMMAND_CONFLICT")
        return original

    def _registered_identity_v2(self, key: str) -> None:
        self._checked_context_v2()
        request = self._requests[key]
        if type(request) is not LinearFixedLimitNativeOrderRequestV2:
            raise ValueError("IMMUTABLE_NATIVE_COMMAND_CONFLICT")
        try:
            identity = _fixed_limit_identity_v2(request)
            if request.owned.client_order_id != key or identity != self._identities_v2[key]:
                raise ValueError("IMMUTABLE_NATIVE_COMMAND_CONFLICT")
            self._checked_context_v2()
        except Exception:  # noqa: BLE001 - mutable/forged request details stay private.
            raise ValueError("IMMUTABLE_NATIVE_COMMAND_CONFLICT") from None

    def register_fixed_limit_v2(self, request: LinearFixedLimitNativeOrderRequestV2) -> None:
        """Mapping only; the durable caller still owns admission and claim."""
        with self._lock:
            identity = _fixed_limit_identity_v2(request)
            current_context = self._current_context_v2()
            if request.scope != self.scope:
                raise ValueError("MATCHING_NATIVE_SCOPE_REQUIRED")
            if self._context_v2 is not None:
                self._checked_context_v2()
            key = request.owned.client_order_id
            for original_key, original in self._requests.items():
                if original is request and original_key != key:
                    raise ValueError("IMMUTABLE_NATIVE_COMMAND_CONFLICT")
            previous = self._requests.get(key)
            if previous is not None:
                self._registered_identity_v2(key)
                if identity != self._identities_v2[key]:
                    raise ValueError("IMMUTABLE_NATIVE_COMMAND_CONFLICT")
                return  # Keep the original owner; clones never hide its later mutation.
            if self._context_v2 is None:
                self._context_v2 = current_context
            self._requests[key] = request
            self._identities_v2[key] = identity

    def submit_fixed_limit_v2(self, request: LinearFixedLimitNativeOrderRequestV2) -> NativeSubmission:
        with self._lock:
            self.register_fixed_limit_v2(request)
            key = request.owned.client_order_id
            if key in self._submissions:
                return self._submissions[key]
            if key in self._building_v2:
                raise ValueError("NATIVE_ORDER_BUILD_IN_PROGRESS")
            self._building_v2.add(key)
            try:
                try:
                    context, owned = self._checked_context_v2(), request.owned
                    binding = context.verifier_binding()
                    instrument_id, client_id, quantity, price, side = context.converters
                    arguments: _FactoryArguments = {
                        "instrument_id": instrument_id(owned.instrument_id),
                        "order_side": side(owned.side),
                        "quantity": quantity(format(owned.quantity, "f")),
                        "client_order_id": client_id(key), "reduce_only": False}
                    order = cast(_FixedLimitFactoryV2, context.factory).limit(**arguments,
                        price=price(format(request.limit_price, "f")),
                        time_in_force=binding.time_in_force_ioc)
                    for value in (request.attached.stop_loss, request.attached.take_profit):
                        if bounded_decimal(str(price(format(value, "f")))) != value:
                            raise ValueError("NATIVE_PROTECTION_PRECISION_CHANGED")
                    self._registered_identity_v2(key)
                    if _fixed_limit_identity_v2(request) != self._identities_v2[key]:
                        raise ValueError("IMMUTABLE_NATIVE_COMMAND_CONFLICT")
                    params = _fixed_limit_params_v2(request)
                    _verify_fixed_limit_order_v2(order, request, binding, context.strategy,
                                                building=True)
                    self._registered_identity_v2(key)
                    if _fixed_limit_identity_v2(request) != self._identities_v2[key]:
                        raise ValueError("IMMUTABLE_NATIVE_COMMAND_CONFLICT")
                except Exception:  # noqa: BLE001 - build never implies an invocation.
                    raise ValueError("NATIVE_ORDER_BUILD_FAILED") from None
                result = NativeSubmission(key, "SUBMITTING", "NATIVE_SUBMIT_INVOKED")
                self._submissions[key] = result
                try:
                    context.strategy.submit_order(order, params=params)
                except Exception:  # noqa: BLE001 - outer failure supersedes reentrant observation.
                    result = NativeSubmission(key, "UNKNOWN", "NATIVE_SUBMIT_AMBIGUOUS")
                    self._submissions[key] = result
                return result
            finally:
                self._building_v2.remove(key)

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
            if type(request) is LinearFixedLimitNativeOrderRequestV2:
                self._registered_identity_v2(key)
            evidence = (decoder or load_native_event_decoder()).decode(event, context, request.owned)
            if type(request) is LinearFixedLimitNativeOrderRequestV2:
                self._registered_identity_v2(key)
            return evidence

    def cached_owned_order(self, client_order_id: str) -> object:
        """Resolve the actual strategy cache; never reconstruct a cancel target."""
        with self._lock:
            request = self._requests.get(client_order_id)
            if request is None:
                raise ValueError("NATIVE_CANCEL_EXACT_OWNERSHIP_REQUIRED")
            context = None
            if type(request) is LinearFixedLimitNativeOrderRequestV2:
                self._registered_identity_v2(client_order_id)
                context = self._checked_context_v2()
            strategy = self._strategy if context is None else context.strategy
            converter = self._bindings.client_order_id if context is None else context.converters[1]
            cache = getattr(strategy, "cache", None)
            order = cache.order(converter(client_order_id)) if cache else None
            if context is not None:
                self._checked_context_v2()
            if order is None:
                raise ValueError("NATIVE_CANCEL_CACHED_ORDER_REQUIRED")
            if type(request) is LinearFixedLimitNativeOrderRequestV2:
                try:
                    assert context is not None
                    _verify_fixed_limit_order_v2(order, request,
                        context.verifier_binding(), context.strategy,
                        building=False)
                except Exception:  # noqa: BLE001 - native fields are untrusted.
                    raise ValueError("NATIVE_ORDER_CHANGED") from None
                self._registered_identity_v2(client_order_id)
            else:
                _verify_built_order(order, cast(NativeOrderRequest, request))
                strategy = self._strategy
            if (str(getattr(order, "account_id", None)) != self.native_account_id
                    or getattr(order, "strategy_id", None) != getattr(strategy, "id", None)
                    or getattr(order, "strategy_id", None) is None
                    or getattr(order, "is_closed", None) is not False
                    or getattr(order, "is_pending_cancel", None) is not False):
                raise ValueError("NATIVE_CANCEL_ACTIVE_SCOPED_ORDER_REQUIRED")
            if type(request) is LinearFixedLimitNativeOrderRequestV2:
                self._registered_identity_v2(client_order_id)
            return order

    def cancel_owned(self, client_order_id: str) -> NativeSubmission:
        """Caller must durably claim CANCEL first. Invocation is never no-fill proof."""
        with self._lock:
            request = self._requests.get(client_order_id)
            if type(request) is LinearFixedLimitNativeOrderRequestV2:
                self._registered_identity_v2(client_order_id)
            previous = self._cancellations.get(client_order_id)
            if previous is not None:
                return previous
            order = self.cached_owned_order(client_order_id)
            strategy = self._strategy
            if type(request) is LinearFixedLimitNativeOrderRequestV2:
                self._registered_identity_v2(client_order_id)
                strategy = self._checked_context_v2().strategy
                # A converter/cache callback may have completed the same cancellation.
                previous = self._cancellations.get(client_order_id)
                if previous is not None:
                    return previous
            result = NativeSubmission(client_order_id, "SUBMITTING", "NATIVE_SUBMIT_INVOKED")
            self._cancellations[client_order_id] = result
            try:
                strategy.cancel_order(order, params={})
            except Exception:  # noqa: BLE001 - a cancel can race a fill or a disconnect.
                result = NativeSubmission(client_order_id, "UNKNOWN", "NATIVE_SUBMIT_AMBIGUOUS")
                self._cancellations[client_order_id] = result
            return result

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


def load_native_bindings(*, include_fixed_limit_v2: bool = False) -> NativeBindings:
    """Lazy imports keep Windows/public/offline imports independent of native wheel."""
    if type(include_fixed_limit_v2) is not bool:
        raise ValueError("NATIVE_FIXED_LIMIT_OPTION_REQUIRED")
    _require_native_version()
    identifiers = importlib.import_module("nautilus_trader.model.identifiers")
    objects = importlib.import_module("nautilus_trader.model.objects")
    enums = importlib.import_module("nautilus_trader.model.enums")
    fixed = None
    if include_fixed_limit_v2:
        orders = importlib.import_module("nautilus_trader.model.orders")
        events = importlib.import_module("nautilus_trader.model.events.order")
        fixed = FixedLimitBindingsV2(orders.LimitOrder, events.OrderInitialized, events.OrderUpdated,
                                    enums.OrderType.LIMIT, enums.TimeInForce.IOC)
    return NativeBindings(identifiers.InstrumentId.from_str, identifiers.ClientOrderId,
        objects.Quantity.from_str, objects.Price.from_str,
        lambda side: getattr(enums.OrderSide, side), fixed)


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
