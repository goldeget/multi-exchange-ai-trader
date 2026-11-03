"""Two real-wheel feasibility cases, not prepared entry or financial authority.

The imported autouse prerequisite must pass before lazy SDK imports. Synthetic
requests/accounts are unqualified. The installed passive node stays unstarted;
its original risk handler only queues the command. A labelled forwarding tap
observes that route without replacing SDK submit, factory or owner resolution.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_bybit_fixed_limit_linux import _assert_payload, _harness, _payload, _request, _stop
from test_bybit_native_linux import isolated_native_runtime, module  # noqa: F401
from test_native_restart_linux import actual_worker, no_effects, start

from multi_exchange_trader.execution import bybit_native as n
from multi_exchange_trader.execution.monitoring_models import MonitoringError


def _prepare(strategy: Any, request: n.LinearFixedLimitNativeOrderRequestV2) -> Any:
    """Build once using the genuine registered factory; publish/cache nothing."""
    base = module("trading.strategy").Strategy
    assert type(strategy) is base
    factory = strategy.order_factory
    assert type(factory) is module("common.factories").OrderFactory
    assert factory.trader_id == strategy.trader_id and factory.strategy_id == strategy.id
    bindings = n.load_native_bindings(include_fixed_limit_v2=True)
    binding = n._fixed_limit_bindings_v2(bindings.fixed_limit_v2)
    ids, objects, enums = module("model.identifiers"), module("model.objects"), module("model.enums")
    owned = request.owned
    order = factory.limit(
        instrument_id=ids.InstrumentId.from_str(owned.instrument_id),
        order_side=getattr(enums.OrderSide, owned.side),
        quantity=objects.Quantity.from_str(format(owned.quantity, "f")),
        price=objects.Price.from_str(format(request.limit_price, "f")),
        client_order_id=ids.ClientOrderId(owned.client_order_id),
        reduce_only=False, time_in_force=binding.time_in_force_ioc,
    )
    n._verify_fixed_limit_order_v2(order, request, binding, strategy.id, building=True)
    seed = order.events[0]
    for native_value, expected in ((order.quantity, owned.quantity),
                                   (seed.quantity, request.envelope.base_quantity),
                                   (order.price, request.limit_price)):
        actual = native_value.as_decimal()
        text = format(expected, "f")
        assert type(actual) is Decimal and actual == expected
        assert native_value.precision == len(text.partition(".")[2])
        assert str(native_value) == text
    assert str(seed.options["price"]) == format(request.limit_price, "f")
    for value in (request.attached.stop_loss, request.attached.take_profit):
        assert objects.Price.from_str(format(value, "f")).as_decimal() == value
    params = n._fixed_limit_params_v2(request)
    assert params == {
        "position_idx": 0, "take_profit": format(request.attached.take_profit, "f"),
        "stop_loss": format(request.attached.stop_loss, "f"),
        "tp_trigger_by": "MarkPrice", "sl_trigger_by": "MarkPrice",
        "tp_order_type": "Market", "sl_order_type": "Market", "tpsl_mode": "Full",
    }
    submit_impl = base.__dict__["submit_order"]
    assert callable(submit_impl)
    args = (strategy, order, None, None, params)
    return SimpleNamespace(strategy=strategy, factory=factory, cache=strategy.cache,
        msgbus=strategy.msgbus, clock=strategy.clock, order=order, seed=seed,
        params=params, submit_impl=submit_impl, args=args)


def _assert_private(prepared: Any, commands: list[Any], initialized: list[Any]) -> None:
    assert commands == initialized == []
    assert prepared.cache.orders() == []
    assert prepared.cache.order(prepared.order.client_order_id) is None
    assert prepared.order.events == [prepared.seed]
    assert type(prepared.order) is module("model.orders").LimitOrder
    assert type(prepared.seed) is module("model.events.order").OrderInitialized


def _assert_route(prepared: Any, commands: list[Any], initialized: list[Any]) -> Any:
    assert len(commands) == len(initialized) == 1
    command = commands[0]
    assert type(command) is module("execution.messages").SubmitOrder
    assert command.order is prepared.order and initialized[0] is prepared.seed
    assert command.trader_id == prepared.strategy.trader_id
    assert command.strategy_id == prepared.strategy.id
    assert command.position_id is command.client_id is None
    # Pinned Strategy copies params into its command; wrapper identity is not promised.
    assert command.params == prepared.params and command.params is not prepared.params
    assert prepared.cache.order(command.order.client_order_id) is prepared.order
    assert prepared.strategy.order_factory is prepared.factory
    assert prepared.strategy.cache is prepared.cache
    assert prepared.strategy.msgbus is prepared.msgbus
    assert prepared.strategy.clock is prepared.clock
    assert prepared.args[0] is prepared.strategy and prepared.args[1] is prepared.order
    assert prepared.args[4] is prepared.params
    assert prepared.submit_impl is module("trading.strategy").Strategy.__dict__["submit_order"]
    return command


async def test_sdk_base_submit_preserves_private_order_and_original_route() -> None:
    h, request = _harness(), _request()
    initialized: list[Any] = []
    topic = f"events.order.{h.strategy.id}"
    h.msgbus.subscribe(topic=topic, handler=initialized.append)
    try:
        prepared = _prepare(h.strategy, request)
        assert prepared.cache is h.cache and prepared.msgbus is h.msgbus
        assert prepared.clock is h.clock and prepared.strategy.trader_id == h.trader
        _assert_private(prepared, h.commands, initialized)
        # Exact actual BASE class-dictionary entry, SAME self, prebuilt five args.
        prepared.submit_impl(*prepared.args)
        command = _assert_route(prepared, h.commands, initialized)
        assert command.order.events == [prepared.seed]
        _assert_payload(payload := await _payload(h), request)
        protection = payload["native_tp_sl"]
        assert type(protection) is module("core.nautilus_pyo3").BybitNativeTpSlParams
        assert (protection.stop_loss, protection.take_profit) == (
            format(request.attached.stop_loss, "f"), format(request.attached.take_profit, "f"))
        assert (protection.tp_trigger_by, protection.sl_trigger_by) == ("MarkPrice", "MarkPrice")
        assert h.commands == [command] and initialized == [prepared.seed]
    finally:
        h.msgbus.unsubscribe(topic=topic, handler=initialized.append)


async def test_installed_node_owns_the_same_base_submit_route(tmp_path: Path) -> None:
    h = actual_worker(tmp_path)
    try:
        await start(h)
        no_effects(h)
        assembly, strategy = h.assembly, h.assembly.strategy
        node, kernel = assembly.node, assembly.node.kernel
        factory, cache, bus, clock = strategy.order_factory, node.cache, kernel.msgbus, strategy.clock
        assert h.worker._factory is None and h.worker._assembly is assembly
        assert assembly.bridge._strategy is strategy and assembly.bridge._factory is factory
        assert kernel.trader is node.trader
        assert len(kernel.trader.strategies()) == 1 and kernel.trader.strategies()[0] is strategy
        assert strategy.cache is cache is kernel.cache
        assert strategy.msgbus is bus and strategy.portfolio is kernel.portfolio
        # Trader.add_strategy creates one LiveClock per component, distinct from kernel.clock.
        assert type(clock) is type(kernel.clock) is module("common.component").LiveClock
        assert clock is not kernel.clock
        assert type(bus) is module("common.component").MessageBus
        from multi_exchange_trader.execution.native_monitor_client import (
            MonitoringBybitExecutionClient,
        )

        client = assembly.execution_client
        assert type(client) is MonitoringBybitExecutionClient
        assert client.account_id.value == assembly.channel.native_account_id
        assert assembly.bridge.native_account_id == assembly.channel.native_account_id
        request = _request(suffix=2)
        commands: list[Any] = []
        initialized: list[Any] = []
        topic, endpoint = f"events.order.{strategy.id}", "RiskEngine.execute"
        original_handler = kernel.risk_engine.execute
        assert original_handler.__self__ is kernel.risk_engine
        assert original_handler.__func__ is module("live.risk_engine").LiveRiskEngine.execute
        assert kernel.risk_engine.cmd_qsize() == 0

        def observational_forwarding_tap(command: Any) -> None:
            # Instrumentation ONLY: observe once, then call the actual original handler once.
            commands.append(command)
            original_handler(command)

        # The supported deregister API verifies this is the existing registered handler.
        bus.deregister(endpoint=endpoint, handler=original_handler)
        bus.register(endpoint=endpoint, handler=observational_forwarding_tap)
        bus.subscribe(topic=topic, handler=initialized.append)
        try:
            prepared = _prepare(strategy, request)
            _assert_private(prepared, commands, initialized)
            prepared.submit_impl(*prepared.args)
            command = _assert_route(prepared, commands, initialized)
            assert kernel.risk_engine.cmd_qsize() == 1
            assert kernel.risk_engine.command_count == 0  # Queued, never processed/accepted.
            assert kernel.exec_engine.command_count == kernel.exec_engine.event_count == 0
            assert kernel.exec_engine.cmd_qsize() == kernel.exec_engine.evt_qsize() == 0
            assert kernel.risk_engine.get_cmd_queue_task() is None
            assert kernel.trader.strategies()[0] is strategy and assembly.bridge._factory is factory
            assert not node.is_running() and h.worker._node_task is None
            assert not client.is_connected and h.core.reads == []
            with pytest.raises(MonitoringError, match="^MONITORING_CAPABILITY_DENIED$"):
                client.submit_order(command)
            assert client.monitoring_fault == "MONITORING_CAPABILITY_DENIED"
            assert commands == [command] and initialized == [prepared.seed]
            assert not assembly.bridge._submissions and h.worker.callback_failures == 0
        finally:
            bus.unsubscribe(topic=topic, handler=initialized.append)
            bus.deregister(endpoint=endpoint, handler=observational_forwarding_tap)
            bus.register(endpoint=endpoint, handler=original_handler)
            # Remove only this synthetic fixture's unjournaled native cache order
            # before the genuine worker shutdown/seal. This is not recovery evidence.
            cache.reset()
    finally:
        await _stop(h)
