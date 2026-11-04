"""Four native-only owner contracts; synthetic configuration is unqualified for finance.

The imported mandatory Linux/netns/version prerequisite precedes every lazy SDK
import. Real builder, factory, base submit and original local RiskEngine route
run with the node unstarted. No account/UID/source/clock/J/financial authority.
"""
from __future__ import annotations

import asyncio
import copy
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from test_bybit_fixed_limit_linux import _request, _stop
from test_bybit_native_linux import isolated_native_runtime, module  # noqa: F401
from test_native_restart_linux import actual_worker, start

from multi_exchange_trader.execution.account_binding import (
    AuthenticatedAccountBinding,
    BybitDemoAccountSession,
)
from multi_exchange_trader.execution.bybit_native import NativeOrderRequest, build_demo_exec_config
from multi_exchange_trader.execution.native_node import build_native_node
from multi_exchange_trader.execution.native_restart import NativeRestartCoordinator


@asynccontextmanager
async def _entry(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    request = _request()
    session = BybitDemoAccountSession(api_key="fixture", api_secret="fixture")

    def synthetic_configuration(binding: object, *, scope: object, install: Any) -> None:
        # Only configuration admission is synthetic, exactly as existing native-node contracts.
        # The original SDK builder/factory/client/Strategy/submit route stay untouched.
        install(build_demo_exec_config(api_key="fixture", api_secret="fixture"))

    monkeypatch.setattr(session, "configure_native_execution", synthetic_configuration)
    assembly = None
    try:
        assembly = build_native_node(session=session, binding=cast(AuthenticatedAccountBinding, object()), scope=request.scope,
            symbols=("BTCUSDT",), trader_id="MONITOR-001", on_event=lambda channel, event: None)
        node, kernel, strategy = assembly.node, assembly.node.kernel, assembly.strategy
        assert not node.is_running() and type(strategy) is module("trading.strategy").Strategy
        assert type(strategy.order_factory) is module("common.factories").OrderFactory
        assert type(assembly.execution_client) is module("adapters.bybit.execution").BybitExecutionClient
        assert strategy.clock is not kernel.clock
        assert type(strategy.clock) is type(kernel.clock) is module("common.component").LiveClock
        assert kernel.trader.strategies() == [strategy]
        assert strategy.cache is node.cache is kernel.cache and strategy.msgbus is kernel.msgbus
        yield assembly, request
    finally:
        if assembly is not None:
            # Only synthetic fixture orders; this is not journal recovery evidence.
            assembly.node.cache.reset()
            assembly.dispose_local()
        await session.aclose()


@asynccontextmanager
async def _route(assembly: Any) -> AsyncIterator[Any]:
    kernel, strategy = assembly.node.kernel, assembly.strategy
    bus = kernel.msgbus
    endpoint, topic = "RiskEngine.execute", f"events.order.{strategy.id}"
    original = kernel.risk_engine.execute
    assert original.__self__ is kernel.risk_engine
    assert original.__func__ is module("live.risk_engine").LiveRiskEngine.execute
    commands: list[Any] = []
    initialized: list[Any] = []

    def observational_forwarding_tap(command: Any) -> None:
        commands.append(command)
        original(command)  # Exactly one forwarding call to the original SDK handler.

    bus.deregister(endpoint=endpoint, handler=original)
    bus.register(endpoint=endpoint, handler=observational_forwarding_tap)
    bus.subscribe(topic=topic, handler=initialized.append)
    try:
        yield commands, initialized
    finally:
        bus.unsubscribe(topic=topic, handler=initialized.append)
        bus.deregister(endpoint=endpoint, handler=observational_forwarding_tap)
        bus.register(endpoint=endpoint, handler=original)


async def test_actual_entry_installation_prepares_a_private_order(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _entry(monkeypatch) as (assembly, request), _route(assembly) as (commands, initialized):
        bridge, cache = assembly.bridge, assembly.node.cache
        with bridge._prepare_fixed_limit_handoff_v2(request) as data:
            assert type(data.order) is module("model.orders").LimitOrder
            assert type(data.seed) is module("model.events.order").OrderInitialized
            assert data.order.events == [data.seed]
            assert cache.orders() == commands == initialized == []
            assert cache.order(data.order.client_order_id) is None
            assert data.order.quantity.as_decimal() == request.owned.quantity
            assert data.order.price.as_decimal() == request.limit_price
            assert data.params == {"position_idx": 0, "take_profit": "120.000", "stop_loss": "90.00",
                "tp_trigger_by": "MarkPrice", "sl_trigger_by": "MarkPrice", "tp_order_type": "Market",
                "sl_order_type": "Market", "tpsl_mode": "Full"}
            assert bridge._seal_prepared_fixed_limit_v2(data) is None
        with pytest.raises(ValueError):
            bridge._handoff_prepared_fixed_limit_v2(data)
        with (
            pytest.raises(ValueError),
            bridge._prepare_fixed_limit_handoff_v2(request) as copied_data,
            pytest.raises(ValueError),
        ):
            bridge._seal_prepared_fixed_limit_v2(copy.copy(copied_data))
        original = bridge._prepared_installation_v2
        bridge._prepared_installation_v2 = copy.copy(original)
        try:
            with pytest.raises(ValueError), bridge._prepare_fixed_limit_handoff_v2(request):
                pytest.fail("copied installation must not issue")
        finally:
            bridge._prepared_installation_v2 = original
        with pytest.raises(ValueError), copy.copy(bridge)._prepare_fixed_limit_handoff_v2(request):
            pytest.fail("copied bridge must not issue")
        assert cache.orders() == commands == initialized == [] and not bridge._submissions


async def test_actual_prepared_handoff_uses_one_original_sdk_route(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _entry(monkeypatch) as (assembly, request), _route(assembly) as (commands, initialized):
        bridge, kernel = assembly.bridge, assembly.node.kernel
        with bridge._prepare_fixed_limit_handoff_v2(request) as data:
            order, seed, params = data.order, data.seed, data.params
            assert data.args[0] is assembly.strategy and data.args[1] is order
            assert data.args[2] is data.args[3] is None and data.args[4] is params
            assert data.frame.layout[3][16] is module("trading.strategy").Strategy.__dict__["submit_order"]
            assert commands == initialized == [] and assembly.node.cache.orders() == []
            bridge._seal_prepared_fixed_limit_v2(data)
            result = bridge._handoff_prepared_fixed_limit_v2(data)
            assert result.state == "SUBMITTING"
            with pytest.raises(ValueError):
                bridge._handoff_prepared_fixed_limit_v2(data)
        assert len(commands) == len(initialized) == 1
        command = commands[0]
        assert type(command) is module("execution.messages").SubmitOrder
        assert command.order is order and initialized[0] is seed
        assert command.position_id is command.client_id is None and command.params == params
        assert command.params is not params
        assert assembly.node.cache.order(order.client_order_id) is order
        # Fixed observation turn AFTER the effect; no engine is started or processed.
        await asyncio.sleep(0)
        assert kernel.risk_engine.cmd_qsize() == 1 and kernel.risk_engine.command_count == 0
        assert kernel.risk_engine.get_cmd_queue_task() is None
        assert kernel.exec_engine.command_count == kernel.exec_engine.event_count == 0
        assert kernel.exec_engine.cmd_qsize() == kernel.exec_engine.evt_qsize() == 0
        assert not assembly.node.is_running() and not assembly.execution_client.is_connected
        assert bridge._submissions[request.owned.client_order_id] is result


async def test_actual_prepared_scope_rejects_cross_key_callback_reentry(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _entry(monkeypatch) as (assembly, request), _route(assembly) as (commands, initialized):
        bridge, bus = assembly.bridge, assembly.node.kernel.msgbus
        other = _request(suffix=2)
        legacy = NativeOrderRequest(other.scope, other.owned, "LIMIT", other.limit_price,
            other.limit_price, other.attached)
        observed: list[str] = []

        def subscriber(event: object) -> None:
            operations: tuple[tuple[str, Callable[[], object]], ...] = (
                ("same-submit", lambda: bridge.submit_fixed_limit_v2(request)),
                ("other-submit", lambda: bridge.submit_fixed_limit_v2(other)),
                ("other-legacy-submit", lambda: bridge.submit(legacy)),
                ("other-register", lambda: bridge.register_fixed_limit_v2(other)),
                ("other-legacy-register", lambda: bridge.register_owned(legacy)),
                ("other-cancel", lambda: bridge.cancel_owned(other.owned.client_order_id)))
            for name, invoke in operations:
                with pytest.raises(ValueError, match="NATIVE_PREPARED"):
                    invoke()
                observed.append(name)

        topic = f"events.order.{assembly.strategy.id}"
        bus.subscribe(topic=topic, handler=subscriber)
        try:
            with bridge._prepare_fixed_limit_handoff_v2(request) as data:
                bridge._seal_prepared_fixed_limit_v2(data)
                result = bridge._handoff_prepared_fixed_limit_v2(data)
                assert result.state == "UNKNOWN"
            await asyncio.sleep(0)
            assert observed == ["same-submit", "other-submit", "other-legacy-submit",
                "other-register", "other-legacy-register", "other-cancel"]
            assert len(commands) == len(initialized) == 1
            assert bridge._submissions[request.owned.client_order_id] is result
            assert bridge.submit_fixed_limit_v2(request) is result
            assert other.owned.client_order_id not in bridge._requests
            assert assembly.node.kernel.risk_engine.cmd_qsize() == 1
            assert assembly.node.kernel.risk_engine.command_count == 0
        finally:
            bus.unsubscribe(topic=topic, handler=subscriber)


async def test_actual_prepared_owner_retirement_and_monitor_denial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    async with _entry(monkeypatch) as (assembly, request):
        bridge = assembly.bridge
        # Negative early-boundary check; no store/resolver can grant authority here.
        coordinator = object.__new__(NativeRestartCoordinator)
        coordinator.assembly = assembly
        resolved: list[object] = []

        def forbidden_resolver(value: object) -> Any:
            resolved.append(value)
            raise AssertionError("restore resolver ran before private retirement")

        with pytest.raises(ValueError), bridge._prepare_fixed_limit_handoff_v2(request):
            with pytest.raises(ValueError):
                coordinator.restore(forbidden_resolver)
            assert resolved == [] and assembly.node.cache.orders() == []
        with pytest.raises(ValueError), bridge._prepare_fixed_limit_handoff_v2(request):
            with pytest.raises(ValueError):
                assembly.close_event_channel()
            assert not assembly.order_subscription._closed
        assembly.close_event_channel()
        with pytest.raises(ValueError), bridge._prepare_fixed_limit_handoff_v2(request):
            pytest.fail("closed owner cannot prepare")
        assert not bridge._submissions and assembly.node.cache.orders() == []
    h = actual_worker(tmp_path / "monitor")
    try:
        await start(h)
        assert h.worker._factory is None
        with pytest.raises(ValueError, match="NATIVE_PREPARED_INSTALLATION_REQUIRED"), h.assembly.bridge._prepare_fixed_limit_handoff_v2(_request()):
            pytest.fail("default monitor must never prepare entry")
        assert h.worker._node_task is None and not h.assembly.node.is_running()
        assert not h.assembly.bridge._submissions and h.core.reads == []
    finally:
        await _stop(h)
