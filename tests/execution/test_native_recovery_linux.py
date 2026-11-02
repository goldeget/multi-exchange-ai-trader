"""Real pinned Cache diagnostics inside the existing OS-isolated native harness.

Fixture construction uses public native order/event/cache APIs only. It is not
an application restore algorithm and creates no database, HTTP client or node.
"""
from __future__ import annotations

import inspect
from typing import Any

from test_bybit_native_linux import (
    CLIENT_ID,
    INSTRUMENT,
    SCOPE,
    TS_EVENT,
    TS_INIT,
    isolated_native_runtime,  # noqa: F401
    module,
    request,
)
from test_bybit_native_linux import native_harness as native_harness_fixture

from multi_exchange_trader.execution.bybit_native import load_native_bindings
from multi_exchange_trader.execution.native_recovery import (
    OwnedCacheExpectation,
    RecoveryCheckpoint,
    inspect_native_recovery,
)

native_harness = native_harness_fixture


def check(harness: Any, cache: Any) -> Any:
    expected = OwnedCacheExpectation(request(), "BYBIT-UNIFIED", str(harness.trader),
        str(harness.strategy.id), "venue-1", INSTRUMENT + "-" + str(harness.strategy.id))
    checkpoint = RecoveryCheckpoint(SCOPE, "fixture-generation", 4, "fixture-ownership-digest",
        "1.231.0", str(harness.trader), str(harness.strategy.id))
    result = inspect_native_recovery(cache=cache, bindings=load_native_bindings(),
        expectations=(expected,), application_checkpoint=checkpoint, cache_checkpoint=checkpoint,
        observed_revision_before=4, observed_revision_after=4)
    assert result.production_supported is False
    assert harness.commands == []
    return result


def test_real_accepted_cache_and_netting_mapping_are_diagnostic_only(native_harness: Any) -> None:
    harness = native_harness
    ids, objects, enums = module("model.identifiers"), module("model.objects"), module("model.enums")
    cache_type = module("cache.cache").Cache
    assert type(harness.cache) is cache_type
    # Pin the actual reader signatures, including default-free required owned IDs.
    for method in (cache_type.order, cache_type.position_id):
        signature = inspect.signature(method)
        assert list(signature.parameters) == ["self", "client_order_id"]
        assert signature.parameters["client_order_id"].default is inspect.Parameter.empty
    inspect.signature(cache_type.orders).bind(harness.cache)
    order = harness.strategy.order_factory.limit(instrument_id=ids.InstrumentId.from_str(INSTRUMENT),
        order_side=enums.OrderSide.BUY, quantity=objects.Quantity.from_str("0.010"),
        price=objects.Price.from_str("50000.0"), client_order_id=ids.ClientOrderId(CLIENT_ID),
        reduce_only=False)
    assert type(order) is module("model.orders").LimitOrder
    assert type(order.last_event) is module("model.events.order").OrderInitialized
    # The initialized fixture is rejected before any accepted event is applied.
    placeholder_cache = cache_type()
    placeholder_cache.add_order(order)
    placeholder = check(harness, placeholder_cache)
    assert "INITIALIZED_CACHED_PLACEHOLDER" in {issue.code for issue in placeholder.issues}
    event_fields = {"trader_id": harness.trader, "strategy_id": harness.strategy.id,
        "instrument_id": order.instrument_id, "client_order_id": order.client_order_id,
        "account_id": ids.AccountId("BYBIT-UNIFIED"), "ts_event": TS_EVENT, "ts_init": TS_INIT}
    events, uuid = module("model.events.order"), module("core.uuid").UUID4
    order.apply(events.OrderSubmitted(**event_fields, event_id=uuid()))
    order.apply(events.OrderAccepted(**event_fields, venue_order_id=ids.VenueOrderId("venue-1"),
        event_id=uuid()))
    position_id = ids.PositionId(INSTRUMENT + "-" + str(harness.strategy.id))
    # A fresh in-memory fixture cache receives an already accepted native object.
    harness.cache.add_order(order, position_id=position_id)
    assert harness.cache.position_id(order.client_order_id) == position_id
    assert harness.cache.order(order.client_order_id) is order
    assert harness.cache.orders() == [order]
    before = (tuple(order.events), tuple(harness.cache.orders()),
        harness.cache.position_id(order.client_order_id))
    result = check(harness, harness.cache)
    assert result.status == "CONSISTENT"
    assert result.issues == ()
    assert before == (tuple(order.events), tuple(harness.cache.orders()),
        harness.cache.position_id(order.client_order_id))
    assert order.status == enums.OrderStatus.ACCEPTED


def test_register_owned_leaves_fresh_real_cache_missing_and_blocked(native_harness: Any) -> None:
    harness = native_harness
    cache = harness.cache
    assert type(cache) is module("cache.cache").Cache
    harness.bridge.register_owned(request())
    result = check(harness, cache)
    assert result.status == "BLOCKED"
    assert {issue.code for issue in result.issues} == {
        "MISSING_CACHED_ORDER", "MISSING_EXPECTED_POSITION_LINK"}
    assert {issue.client_order_id for issue in result.issues} == {CLIENT_ID}
    assert cache.orders() == []
    assert cache.order(module("model.identifiers").ClientOrderId(CLIENT_ID)) is None
    assert cache.position_id(module("model.identifiers").ClientOrderId(CLIENT_ID)) is None
