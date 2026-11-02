"""Real installed SDK objects, original fixture events, no engines or connections."""
from __future__ import annotations

import importlib
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from multi_exchange_trader.execution.bybit_native import NativeOrderRequest
from multi_exchange_trader.execution.native_recovery import OwnedCacheExpectation
from multi_exchange_trader.execution.native_restart_store import (
    NativeRestartError,
    NativeRestartIdentity,
)
from multi_exchange_trader.execution.protection import OwnedOrder, ProtectionScope


def api() -> Any:
    return importlib.import_module("multi_exchange_trader.execution.native_restart_codec")


def module(name: str) -> Any:
    return importlib.import_module("nautilus_trader." + name)


def fixture(state: str = "PARTIALLY_FILLED") -> Any:
    ids, enums, obj = module("model.identifiers"), module("model.enums"), module("model.objects")
    events, uuid = module("model.events.order"), module("core.uuid").UUID4
    instrument = module("test_kit.providers").TestInstrumentProvider.xrpusdt_linear_bybit()
    identity = NativeRestartIdentity(ProtectionScope("fixture", "DEMO"), "BYBIT-UNIFIED",
        "TRADER-001", "Strategy-000", (str(instrument.id),))
    order = module("model.orders").LimitOrder(trader_id=ids.TraderId(identity.trader_id),
        strategy_id=ids.StrategyId(identity.strategy_id), instrument_id=instrument.id,
        client_order_id=ids.ClientOrderId("ec_0123456789abcdef0123456789abcdef"),
        order_side=enums.OrderSide.BUY, quantity=obj.Quantity.from_str("10"),
        price=obj.Price.from_str("0.5000"), init_id=uuid(), ts_init=1)
    position_id = ids.PositionId(str(instrument.id) + "-" + identity.strategy_id)
    fields = {"trader_id": order.trader_id, "strategy_id": order.strategy_id,
        "instrument_id": instrument.id, "client_order_id": order.client_order_id,
        "account_id": ids.AccountId(identity.native_account_id), "ts_event": 2, "ts_init": 2}
    position = None
    if state != "INITIALIZED":
        order.apply(events.OrderSubmitted(**fields, event_id=uuid()))
        order.apply(events.OrderAccepted(**fields, venue_order_id=ids.VenueOrderId("venue-1"),
                                         event_id=uuid()))
    if state in {"PARTIALLY_FILLED", "FILLED"}:
        for index, quantity in enumerate(("2", "3") if state == "PARTIALLY_FILLED" else ("5", "5")):
            fill = events.OrderFilled(**{**fields, "ts_event": 3 + index, "ts_init": 3 + index},
                venue_order_id=order.venue_order_id, trade_id=ids.TradeId(f"trade-{index}"),
                position_id=position_id, order_side=order.side, order_type=order.order_type,
                last_qty=obj.Quantity.from_str(quantity), last_px=obj.Price.from_str("0.4900"),
                currency=instrument.quote_currency, commission=obj.Money(0.01, instrument.quote_currency),
                liquidity_side=enums.LiquiditySide.MAKER, event_id=uuid())
            order.apply(fill)
            if position is None:
                position = module("model.position").Position(instrument, fill)
            else:
                position.apply(fill)
    if state in {"PENDING_CANCEL", "CANCELED"}:
        event_type = events.OrderPendingCancel if state == "PENDING_CANCEL" else events.OrderCanceled
        order.apply(event_type(**fields, venue_order_id=order.venue_order_id, event_id=uuid()))
    owned = OwnedOrder(str(order.client_order_id), "position-1", str(instrument.id), "BUY", "ENTRY",
                       Decimal(10), False, 0, "policy-1")
    request = NativeOrderRequest(identity.scope, owned, "LIMIT", Decimal("0.5"),
                                 limit_price=Decimal("0.5000"))
    expected = OwnedCacheExpectation(request, identity.native_account_id, identity.trader_id,
        identity.strategy_id, None if state == "INITIALIZED" else "venue-1",
        str(position_id) if position is not None else None)
    cache = module("cache.cache").Cache()
    cache.add_instrument(instrument)
    cache.add_order(order, position_id=position_id if position is not None else None,
                    client_id=ids.ClientId("BYBIT"))
    if position is not None:
        cache.add_position(position, enums.OmsType.NETTING)
    cache.build_index()
    assert cache.check_integrity() is True
    return SimpleNamespace(identity=identity, owners=(expected,), cache=cache, order=order,
                           position=position, instrument=instrument)


@pytest.mark.parametrize("state", ["INITIALIZED", "ACCEPTED", "PENDING_CANCEL", "CANCELED",
                                    "PARTIALLY_FILLED", "FILLED"])
def test_actual_orders_positions_and_routing_roundtrip(state: str) -> None:
    source = fixture(state)
    payload = api().capture_native_cache(source.cache, identity=source.identity, owners=source.owners)
    prepared = api().prepare_native_cache(payload, identity=source.identity, owners=source.owners)
    target = module("cache.cache").Cache()
    assert target.orders() == []
    result = api().install_native_cache(target, prepared)
    assert result.status == "RESTORED_LOCAL_CACHE" and result.production_supported is False
    assert (result.orders, result.positions, result.missing_owned_orders) == (
        1, int(source.position is not None), 0)
    restored = target.order(source.order.client_order_id)
    assert restored is not source.order
    assert restored.to_dict() == source.order.to_dict()
    assert [e.to_dict(e) for e in restored.events] == [e.to_dict(e) for e in source.order.events]
    assert target.client_id(restored.client_order_id) == source.cache.client_id(restored.client_order_id)
    assert target.check_integrity() is True
    if source.position is not None:
        position = target.position_for_order(restored.client_order_id)
        assert position is not source.position
        assert position.to_dict() == source.position.to_dict()
        assert [e.to_dict(e) for e in position.events] == [e.to_dict(e) for e in source.position.events]
        assert target.position_id(restored.client_order_id) == source.position.id


def test_missing_only_explicitly_unresolved_without_manufactured_order() -> None:
    source = fixture("INITIALIZED")
    empty = module("cache.cache").Cache()
    with pytest.raises(NativeRestartError):
        api().capture_native_cache(empty, identity=source.identity, owners=source.owners)
    allowed = frozenset({str(source.order.client_order_id)})
    payload = api().capture_native_cache(empty, identity=source.identity, owners=source.owners,
                                         allowed_missing_ids=allowed)
    prepared = api().prepare_native_cache(payload, identity=source.identity, owners=source.owners,
                                           allowed_missing_ids=allowed)
    result = api().install_native_cache(empty, prepared)
    assert result.missing_owned_orders == 1 and empty.orders() == []


@pytest.mark.parametrize("change", ["foreign", "wrong-account", "ambiguous", "missing-position",
                                   "missing-source", "duplicate-event", "summary", "split-fill"])
def test_semantic_rejection_before_target_mutation(change: str) -> None:
    source = fixture()
    payload = api().capture_native_cache(source.cache, identity=source.identity, owners=source.owners)
    owners = source.owners
    if change == "foreign":
        owners = ()
    elif change == "wrong-account":
        owners = (replace(owners[0], native_account_id="BYBIT-SECRET"),)
    elif change == "ambiguous":
        other = replace(owners[0], request=replace(owners[0].request, owned=replace(
            owners[0].request.owned, client_order_id="ec_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            position_id="different-lifetime")))
        owners += (other,)
    elif change == "missing-position":
        payload["positions"] = []
    elif change == "missing-source":
        payload["orders"] = []
    elif change == "duplicate-event":
        payload["orders"][0]["events"].append(payload["orders"][0]["events"][-1])
    elif change == "summary":
        payload["orders"][0]["state"]["quantity"] = "100"
    elif change == "split-fill":
        import base64
        serializer = module("serialization.serializer").MsgSpecSerializer(
            importlib.import_module("msgspec").msgpack, timestamps_as_str=True)
        original = source.position.events[0]
        changed = original.to_dict(original)
        changed["last_qty"] = "1"
        split = type(original).from_dict(changed)
        payload["positions"][0]["events"][0] = base64.b64encode(serializer.serialize(split)).decode()
    target = module("cache.cache").Cache()
    with pytest.raises(NativeRestartError, match="^NATIVE_RESTART_BLOCKED$"):
        api().prepare_native_cache(payload, identity=source.identity, owners=owners,
            allowed_missing_ids=frozenset({str(source.order.client_order_id)})
            if change == "missing-source" else frozenset())
    assert target.orders() == target.positions() == []


def test_nonempty_target_refused_before_mutation() -> None:
    source = fixture()
    payload = api().capture_native_cache(source.cache, identity=source.identity, owners=source.owners)
    prepared = api().prepare_native_cache(payload, identity=source.identity, owners=source.owners)
    before = tuple(source.cache.orders())
    with pytest.raises(NativeRestartError):
        api().install_native_cache(source.cache, prepared)
    assert tuple(source.cache.orders()) == before


def closed_fixture() -> Any:
    source = fixture("FILLED")
    ids, enums, obj = module("model.identifiers"), module("model.enums"), module("model.objects")
    events, uuid = module("model.events.order"), module("core.uuid").UUID4
    exit_order = module("model.orders").MarketOrder(trader_id=source.order.trader_id,
        strategy_id=source.order.strategy_id, instrument_id=source.order.instrument_id,
        client_order_id=ids.ClientOrderId("ec_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        order_side=enums.OrderSide.SELL, quantity=obj.Quantity.from_str("10"),
        reduce_only=True, init_id=uuid(), ts_init=5)
    fields = {"trader_id": exit_order.trader_id, "strategy_id": exit_order.strategy_id,
        "instrument_id": exit_order.instrument_id, "client_order_id": exit_order.client_order_id,
        "account_id": source.order.account_id, "ts_event": 6, "ts_init": 6}
    exit_order.apply(events.OrderSubmitted(**fields, event_id=uuid()))
    exit_order.apply(events.OrderAccepted(**fields, venue_order_id=ids.VenueOrderId("venue-exit"),
                                          event_id=uuid()))
    fill = events.OrderFilled(**fields, venue_order_id=exit_order.venue_order_id,
        trade_id=ids.TradeId("trade-exit"), position_id=source.position.id,
        order_side=exit_order.side, order_type=exit_order.order_type,
        last_qty=exit_order.quantity, last_px=obj.Price.from_str("0.5100"),
        currency=source.instrument.quote_currency,
        commission=obj.Money(0.01, source.instrument.quote_currency),
        liquidity_side=enums.LiquiditySide.TAKER, event_id=uuid())
    exit_order.apply(fill)
    source.position.apply(fill)
    source.cache.add_order(exit_order, position_id=source.position.id, client_id=ids.ClientId("BYBIT"))
    source.cache.update_position(source.position)
    source.cache.build_index()
    first = source.owners[0]
    owned = replace(first.request.owned, client_order_id=str(exit_order.client_order_id),
                     role="EXIT", side="SELL", reduce_only=True)
    source.owners += (replace(first, request=NativeOrderRequest(first.request.scope, owned,
        "MARKET", Decimal("0.5")), venue_order_id="venue-exit"),)
    assert source.position.is_closed
    return source


def test_actual_closed_position_restores_closed_indexes_and_original_lifetime() -> None:
    source = closed_fixture()
    payload = api().capture_native_cache(source.cache, identity=source.identity, owners=source.owners)
    prepared = api().prepare_native_cache(payload, identity=source.identity, owners=source.owners)
    target = module("cache.cache").Cache()
    api().install_native_cache(target, prepared)
    restored = target.position(source.position.id)
    assert restored.to_dict() == source.position.to_dict()
    assert restored.opening_order_id == source.order.client_order_id
    assert target.positions_open() == [] and target.positions_closed() == [restored]
    assert target.orders_open() == [] and len(target.orders_closed()) == 2
    assert target.check_integrity() is True


def test_actual_adjusted_position_rejected_without_serializing_adjustment() -> None:
    source = fixture()
    enum = module("model.enums").PositionAdjustmentType
    adjustment = module("model.events.position").PositionAdjusted(
        trader_id=source.position.trader_id, strategy_id=source.position.strategy_id,
        instrument_id=source.position.instrument_id, position_id=source.position.id,
        account_id=source.position.account_id, adjustment_type=next(iter(enum)),
        quantity_change=None, pnl_change=module("model.objects").Money(
            0.01, source.instrument.quote_currency), reason="fixture",
        event_id=module("core.uuid").UUID4(), ts_event=10, ts_init=10)
    source.position.apply_adjustment(adjustment)
    with pytest.raises(NativeRestartError):
        api().capture_native_cache(source.cache, identity=source.identity, owners=source.owners)


def test_capture_foreign_manual_cache_object_is_redacted() -> None:
    source = fixture()
    with pytest.raises(NativeRestartError, match="^NATIVE_RESTART_BLOCKED$"):
        api().capture_native_cache(source.cache, identity=source.identity, owners=())


def test_effective_fill_sequence_must_preserve_original_order_event_sequence() -> None:
    source = fixture()
    payload = api().capture_native_cache(source.cache, identity=source.identity, owners=source.owners)
    reversed_events = list(reversed(source.position.events))
    alternative = module("model.position").Position(source.instrument, reversed_events[0])
    alternative.apply(reversed_events[1])
    payload["positions"][0]["events"].reverse()
    payload["positions"][0]["state"] = alternative.to_dict()
    with pytest.raises(NativeRestartError):
        api().prepare_native_cache(payload, identity=source.identity, owners=source.owners)
