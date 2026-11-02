"""Offline replay of original native objects into a detached, bounded Cache.

No execution engine, bus, application reducer or connection is involved. A caller
must consume its seal after preparation and before installation, while holding
the scoped lease. Failed installation requires disposal of the target assembly.
"""
from __future__ import annotations

import base64
import importlib
from dataclasses import dataclass, field
from decimal import Decimal
from importlib.metadata import version
from typing import Any, Literal

from .native_recovery import OwnedCacheExpectation
from .native_restart_store import (
    MAX_BYTES,
    MAX_EVENTS,
    MAX_OBJECTS,
    NativeRestartError,
    NativeRestartIdentity,
    _require,
    canonical_bytes,
    decode_document,
    validate_payload_shape,
)

_ORDER_EVENTS = (
    "OrderInitialized", "OrderDenied", "OrderSubmitted", "OrderRejected", "OrderAccepted",
    "OrderPendingUpdate", "OrderPendingCancel", "OrderModifyRejected", "OrderCancelRejected",
    "OrderCanceled", "OrderExpired", "OrderFilled",
)
# Successful OrderUpdated histories are unsupported in this first release. An
# initial/final match cannot authorize intermediate quantity, price, trigger or
# quote-quantity changes. Reject the original stream rather than normalize it.


@dataclass(frozen=True)
class NativeRestartResult:
    orders: int
    positions: int
    missing_owned_orders: int
    status: Literal["RESTORED_LOCAL_CACHE"] = field(default="RESTORED_LOCAL_CACHE", init=False)
    production_supported: Literal[False] = field(default=False, init=False)


@dataclass(frozen=True)
class PreparedNativeRestart:
    # Bytes prevent the caller mutating staged native objects between validation
    # and installation. Installation repeats detached decoding before any writes.
    _payload: bytes = field(repr=False)
    _identity: NativeRestartIdentity = field(repr=False)
    _owners: tuple[OwnedCacheExpectation, ...] = field(repr=False)
    _allowed_missing_ids: frozenset[str] = field(repr=False)
    result: NativeRestartResult
    venue_order_ids: tuple[tuple[str, str], ...]


def _sdk() -> dict[str, Any]:
    _require(version("nautilus-trader") == "1.231.0")
    result: dict[str, Any] = {}
    for path, names in (
        ("cache.cache", ("Cache",)),
        ("model.orders", ("MarketOrder", "LimitOrder", "OrderUnpacker")),
        ("model.position", ("Position",)),
        ("model.instruments", ("CryptoPerpetual",)),
        ("model.events.order", _ORDER_EVENTS),
        ("model.identifiers", ("ClientOrderId", "ClientId", "PositionId", "AccountId",
                               "TraderId", "StrategyId", "InstrumentId", "VenueOrderId")),
        ("model.enums", ("OmsType",)),
    ):
        module = importlib.import_module("nautilus_trader." + path)
        result.update({name: getattr(module, name) for name in names})
    msgpack = importlib.import_module("msgspec").msgpack
    serializer_type = importlib.import_module(
        "nautilus_trader.serialization.serializer").MsgSpecSerializer
    result["serializer"] = serializer_type(msgpack, timestamps_as_str=True)
    result["msgpack"] = msgpack
    return result


def _encode(obj: Any, sdk: dict[str, Any]) -> str:
    data = sdk["serializer"].serialize(obj)
    _require(len(data) <= MAX_BYTES)
    return base64.b64encode(data).decode("ascii")


def _decode(value: object, names: tuple[str, ...], sdk: dict[str, Any]) -> Any:
    _require(type(value) is str and 0 < len(value) <= MAX_BYTES)
    assert isinstance(value, str)
    data = base64.b64decode(value, validate=True)
    # Inspect only inert data before invoking the SDK's registered type decoder.
    mapping = sdk["msgpack"].decode(data)
    _require(type(mapping) is dict and mapping.get("type") in names)
    canonical_bytes(mapping)
    obj = sdk["serializer"].deserialize(data)
    _require(type(obj) is sdk[mapping["type"]])
    _require(_encode(obj, sdk) == value)
    return obj


def _owners(identity: NativeRestartIdentity, owners: tuple[OwnedCacheExpectation, ...],
            allowed: frozenset[str]) -> dict[str, OwnedCacheExpectation]:
    _require(type(identity) is NativeRestartIdentity and type(owners) is tuple
             and len(owners) <= MAX_OBJECTS and type(allowed) is frozenset)
    result: dict[str, OwnedCacheExpectation] = {}
    lifetime: dict[str, str] = {}
    for owner in owners:
        _require(type(owner) is OwnedCacheExpectation)
        request, owned = owner.request, owner.request.owned
        _require(request.scope == identity.scope and owner.native_account_id == identity.native_account_id
                 and owner.trader_id == identity.trader_id and owner.strategy_id == identity.strategy_id
                 and owned.instrument_id in identity.instrument_ids)
        _require(owned.client_order_id not in result)
        _require(owner.expected_native_position_id in (
            None, owned.instrument_id + "-" + identity.strategy_id))
        _require(owned.instrument_id not in lifetime or lifetime[owned.instrument_id] == owned.position_id)
        lifetime[owned.instrument_id] = owned.position_id
        result[owned.client_order_id] = owner
    _require(allowed <= result.keys())
    return result


def _same_id(value: Any, name: str, expected: str, sdk: dict[str, Any]) -> None:
    _require(type(value) is sdk[name] and str(value) == expected)


def _validate_order(order: Any, owner: OwnedCacheExpectation, sdk: dict[str, Any]) -> None:
    request, owned = owner.request, owner.request.owned
    _require(type(order) is sdk["MarketOrder" if request.order_type == "MARKET" else "LimitOrder"])
    for attr, native_type, expected in (
        ("client_order_id", "ClientOrderId", owned.client_order_id),
        ("instrument_id", "InstrumentId", owned.instrument_id),
        ("trader_id", "TraderId", owner.trader_id),
        ("strategy_id", "StrategyId", owner.strategy_id),
    ):
        _same_id(getattr(order, attr), native_type, expected, sdk)
    _require(order.side.name == owned.side and order.order_type.name == request.order_type
             and order.quantity.as_decimal() == owned.quantity
             and order.is_reduce_only is owned.reduce_only)
    _require((request.limit_price is None and getattr(order, "price", None) is None)
             or (request.limit_price is not None and order.price.as_decimal() == request.limit_price))
    if order.account_id is None:
        _require(order.status.name in {"INITIALIZED", "DENIED"})
    else:
        _same_id(order.account_id, "AccountId", owner.native_account_id, sdk)
    _require(order.venue_order_id is None or type(order.venue_order_id) is sdk["VenueOrderId"])
    if owner.venue_order_id is not None:
        _same_id(order.venue_order_id, "VenueOrderId", owner.venue_order_id, sdk)
    init = order.events[0]
    _require(type(init) is sdk["OrderInitialized"] and init.order_type.name == request.order_type)
    _require(init.side.name == owned.side and init.quantity.as_decimal() == owned.quantity
             and init.reduce_only is owned.reduce_only)
    _require(init.emulation_trigger.name == "NO_TRIGGER"
             and init.contingency_type.name == "NO_CONTINGENCY"
             and init.order_list_id is None and not init.linked_order_ids
             and init.parent_order_id is None and init.exec_algorithm_id is None
             and init.exec_spawn_id is None and not init.quote_quantity)
    # These immutable requests do not authorize order transformation or price edits.
    if request.limit_price is not None:
        _require(Decimal(init.options["price"]) == request.limit_price)
    for event in order.events:
        _require(type(event) in tuple(sdk[name] for name in _ORDER_EVENTS))
        for attr in ("trader_id", "strategy_id", "instrument_id", "client_order_id"):
            _require(getattr(event, attr) == getattr(order, attr))
        account = getattr(event, "account_id", None)
        if account is not None:
            _same_id(account, "AccountId", owner.native_account_id, sdk)
        venue = getattr(event, "venue_order_id", None)
        if venue is not None:
            _require(venue == order.venue_order_id)
        if type(event) is sdk["OrderFilled"]:
            _require(event.order_side == order.side and event.order_type == order.order_type
                     and event.last_qty.as_decimal() > 0 and event.last_px.as_decimal() > 0)


def _capture(cache: Any, sdk: dict[str, Any]) -> dict[str, object]:
    _require(type(cache) is sdk["Cache"])
    _require(cache.orders_total_count() <= MAX_OBJECTS
             and cache.positions_total_count() <= MAX_OBJECTS)
    instruments, orders, positions = cache.instruments(), cache.orders(), cache.positions()
    _require(all(len(items) <= MAX_OBJECTS for items in (instruments, orders, positions)))
    _require(not cache.position_snapshot_ids())
    _require(sum(len(obj.events) for obj in orders + positions) <= MAX_EVENTS)
    _require(cache.check_integrity() is True)
    _require(cache.client_order_ids() == {order.client_order_id for order in orders})
    _require(cache.position_ids() == {position.id for position in positions})
    instrument_rows: list[object] = []
    for instrument in instruments:
        _require(type(instrument) is sdk["CryptoPerpetual"])
        instrument_rows.append(_encode(instrument, sdk))
    order_rows: list[object] = []
    for order in orders:
        _require(type(order) in (sdk["MarketOrder"], sdk["LimitOrder"]))
        _require(all(type(event) in tuple(sdk[name] for name in _ORDER_EVENTS)
                     for event in order.events))
        _require(cache.order(order.client_order_id) is order)
        link, client = cache.position_id(order.client_order_id), cache.client_id(order.client_order_id)
        _require(link is None or type(link) is sdk["PositionId"])
        _require(client is None or type(client) is sdk["ClientId"])
        order_rows.append({"events": [_encode(event, sdk) for event in order.events],
            "state": order.to_dict(), "position_id": None if link is None else str(link),
            "client_id": None if client is None else str(client)})
    position_rows: list[object] = []
    for position in positions:
        _require(type(position) is sdk["Position"] and not position.adjustments)
        _require(cache.position(position.id) is position)
        position_rows.append({"events": [_encode(event, sdk) for event in position.events],
                              "state": position.to_dict()})
    return {"schema": 1, "encoding": "msgpack-base64", "instruments": instrument_rows,
            "orders": order_rows, "positions": position_rows}


def _populate(cache: Any, instruments: list[Any], orders: list[tuple[Any, Any, Any]],
              positions: list[Any], sdk: dict[str, Any]) -> None:
    for instrument in instruments:
        cache.add_instrument(instrument)
    for order, position_id, client_id in orders:
        cache.add_order(order, position_id=position_id, client_id=client_id)
    for position in positions:
        cache.add_position(position, sdk["OmsType"].NETTING)
        cache.update_position(position)
    cache.build_index()
    _require(cache.check_integrity() is True)
    _require(len(cache.orders()) == len(orders) and len(cache.positions()) == len(positions))
    for order, link, client in orders:
        _require(cache.order(order.client_order_id) is order
                 and cache.position_id(order.client_order_id) == link
                 and cache.client_id(order.client_order_id) == client)
        if link is not None:
            _require(cache.position_for_order(order.client_order_id) is cache.position(link)
                     and cache.position(link) is not None)
    for position in positions:
        _require(cache.position(position.id) is position)
        _require(cache.is_position_closed(position.id) is position.is_closed)


def _stage(payload: dict[str, object], identity: NativeRestartIdentity,
           owners: tuple[OwnedCacheExpectation, ...], allowed: frozenset[str],
           sdk: dict[str, Any]) -> tuple[Any, NativeRestartResult]:
    validate_payload_shape(payload)
    expected = _owners(identity, owners, allowed)
    instruments: dict[str, Any] = {}
    # Shape validation bounds inventory and total events before any native decode.
    assert isinstance(payload["instruments"], list)
    assert isinstance(payload["orders"], list)
    assert isinstance(payload["positions"], list)
    for raw in payload["instruments"]:
        instrument = _decode(raw, ("CryptoPerpetual",), sdk)
        key = str(instrument.id)
        _require(key in identity.instrument_ids and key not in instruments
                 and instrument.is_inverse is False)
        instruments[key] = instrument
    orders: dict[str, tuple[Any, Any, Any]] = {}
    all_events: set[str] = set()
    fills: dict[str, tuple[Any, str]] = {}
    trades: set[tuple[str, str]] = set()
    for row in payload["orders"]:
        _require(set(row) == {"events", "state", "position_id", "client_id"} and row["events"])
        events = [_decode(raw, _ORDER_EVENTS, sdk) for raw in row["events"]]
        _require(type(events[0]) is sdk["OrderInitialized"])
        order = sdk["OrderUnpacker"].from_init(events[0])
        key = str(order.client_order_id)
        _require(key in expected and key not in orders)
        for index, event in enumerate(events):
            identifier = str(event.id)
            _require(identifier not in all_events)
            all_events.add(identifier)
            if index:
                _require(type(event) is not sdk["OrderInitialized"])
                order.apply(event)
            if type(event) is sdk["OrderFilled"]:
                trade = (str(event.instrument_id), str(event.trade_id))
                _require(trade not in trades)
                trades.add(trade)
                fills[identifier] = (event, row["events"][index])
        _validate_order(order, expected[key], sdk)
        _require(canonical_bytes(order.to_dict()) == canonical_bytes(row["state"])
                 and str(order.instrument_id) in instruments)
        instrument = instruments[str(order.instrument_id)]
        for event in events:
            if type(event) is sdk["OrderFilled"]:
                _require(event.currency == instrument.quote_currency
                         and event.last_qty.precision == instrument.size_precision
                         and event.last_px.precision == instrument.price_precision)
        link_text, client_text = row["position_id"], row["client_id"]
        _require(link_text is None or (type(link_text) is str and len(link_text) <= 256))
        _require(client_text is None or (type(client_text) is str and 0 < len(client_text) <= 256))
        permitted = str(order.instrument_id) + "-" + identity.strategy_id
        _require(link_text in (None, permitted))
        _require(expected[key].expected_native_position_id is None
                 or link_text == expected[key].expected_native_position_id)
        _require((order.position_id is None and link_text is None)
                 or str(order.position_id) == link_text)
        link = None if link_text is None else sdk["PositionId"](link_text)
        client = None if client_text is None else sdk["ClientId"](client_text)
        orders[key] = (order, link, client)
    missing = expected.keys() - orders.keys()
    _require(missing <= allowed)
    positions: dict[str, Any] = {}
    position_fill_ids: set[str] = set()
    for row in payload["positions"]:
        _require(set(row) == {"events", "state"} and row["events"])
        events = [_decode(raw, ("OrderFilled",), sdk) for raw in row["events"]]
        first = events[0]
        _require(str(first.instrument_id) in instruments and first.position_id is not None)
        position_key = str(first.position_id)
        _require(position_key not in positions
                 and position_key == str(first.instrument_id) + "-" + identity.strategy_id)
        signed = Decimal(0)
        initial_side = first.order_side.name
        for index, event in enumerate(events):
            identifier, order_key = str(event.id), str(event.client_order_id)
            _require(identifier in fills and identifier not in position_fill_ids
                     and order_key in orders and fills[identifier][1] == row["events"][index])
            position_fill_ids.add(identifier)
            _require(event.position_id == first.position_id and event.instrument_id == first.instrument_id
                     and event.account_id == first.account_id and event.strategy_id == first.strategy_id
                     and event.trader_id == first.trader_id and orders[order_key][1] == first.position_id)
            if index == 0:
                _require(expected[order_key].request.owned.role == "ENTRY")
            _require((expected[order_key].request.owned.role == "ENTRY")
                     == (event.order_side.name == initial_side))
            # Refuse netting flips, flat/reopen cycles and split effective events.
            signed += event.last_qty.as_decimal() * (1 if event.order_side.name == initial_side else -1)
            _require(signed >= 0 and (signed > 0 or index == len(events) - 1))
        for order_key in {str(event.client_order_id) for event in events}:
            effective_ids = [str(event.id) for event in events
                             if str(event.client_order_id) == order_key]
            original_ids = [str(event.id) for event in orders[order_key][0].events
                            if type(event) is sdk["OrderFilled"]]
            _require(effective_ids == original_ids)
        position = sdk["Position"](instruments[str(first.instrument_id)], first)
        for event in events[1:]:
            position.apply(event)
        _require(canonical_bytes(position.to_dict()) == canonical_bytes(row["state"])
                 and not position.adjustments
                 and position.opening_order_id == first.client_order_id)
        positions[position_key] = position
    _require(position_fill_ids == fills.keys())
    for _, link, _ in orders.values():
        _require(link is None or str(link) in positions)
    candidate = sdk["Cache"]()
    _populate(candidate, list(instruments.values()), list(orders.values()), list(positions.values()), sdk)
    return candidate, NativeRestartResult(len(orders), len(positions), len(missing))


def capture_native_cache(cache: Any, *, identity: NativeRestartIdentity,
                         owners: tuple[OwnedCacheExpectation, ...],
                         allowed_missing_ids: frozenset[str] = frozenset()) -> dict[str, object]:
    try:
        sdk = _sdk()
        payload = _capture(cache, sdk)
        _stage(payload, identity, owners, allowed_missing_ids, sdk)
        return payload
    except Exception:  # noqa: BLE001 - native/path/provider details must stay redacted.
        raise NativeRestartError() from None


def prepare_native_cache(payload: dict[str, object], *, identity: NativeRestartIdentity,
                         owners: tuple[OwnedCacheExpectation, ...],
                         allowed_missing_ids: frozenset[str] = frozenset()) -> PreparedNativeRestart:
    try:
        candidate, result = _stage(payload, identity, owners, allowed_missing_ids, _sdk())
        # Derive only after complete native type/event/immutable/cache validation;
        # raw JSON summary fields are never ownership lookup seeds.
        venues = tuple(sorted((str(order.client_order_id), str(order.venue_order_id))
            for order in candidate.orders() if order.venue_order_id is not None))
        return PreparedNativeRestart(canonical_bytes(payload), identity, owners,
            allowed_missing_ids, result, venues)
    except Exception:  # noqa: BLE001 - native/path/provider details must stay redacted.
        raise NativeRestartError() from None


def install_native_cache(cache: Any, prepared: PreparedNativeRestart) -> NativeRestartResult:
    try:
        _require(type(prepared) is PreparedNativeRestart)
        sdk = _sdk()
        _require(type(cache) is sdk["Cache"] and not cache.orders() and not cache.positions()
                 and not cache.instruments() and not cache.client_order_ids()
                 and not cache.position_ids() and not cache.position_snapshot_ids()
                 and not cache.accounts())
        candidate, result = _stage(decode_document(prepared._payload), prepared._identity,
            prepared._owners, prepared._allowed_missing_ids, sdk)
        _require(result == prepared.result)
        orders = [(order, candidate.position_id(order.client_order_id),
                   candidate.client_id(order.client_order_id)) for order in candidate.orders()]
        _populate(cache, candidate.instruments(), orders, candidate.positions(), sdk)
        return result
    except Exception:  # noqa: BLE001 - native/path/provider details must stay redacted.
        raise NativeRestartError() from None
