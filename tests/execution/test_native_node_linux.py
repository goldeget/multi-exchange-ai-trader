"""Actual node/factory wiring, under the same mandatory OS network isolation."""

import asyncio
import signal
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from test_bybit_native_linux import isolated_native_runtime, module  # noqa: F401
from test_risk import NOW
from test_worker import harness, harness_existing, quarantines, stage

from multi_exchange_trader.execution import worker_cli
from multi_exchange_trader.execution.account_binding import BybitDemoAccountSession
from multi_exchange_trader.execution.bybit_native import build_demo_exec_config
from multi_exchange_trader.execution.native_node import build_native_node, build_node_config
from multi_exchange_trader.execution.ownership import AccountWorkerLock
from multi_exchange_trader.execution.protection import ProtectionScope


def test_actual_node_config_filters_manual_exposure_and_instruments() -> None:
    config: Any = build_node_config(build_demo_exec_config(api_key="fixture", api_secret="fixture"),
                                    symbols=("BTCUSDT",), trader_id="MONITOR-001")
    engine = config.exec_engine
    assert engine.filter_unclaimed_external_orders is True
    assert engine.filter_position_reports is True
    assert engine.generate_missing_orders is False
    assert list(map(str, engine.reconciliation_instrument_ids)) == ["BTCUSDT-LINEAR.BYBIT"]
    execution = config.exec_clients["BYBIT"]
    assert execution.instrument_provider.load_all is False
    assert set(map(str, execution.instrument_provider.load_ids)) == {"BTCUSDT-LINEAR.BYBIT"}
    assert config.data_clients == {}
    assert execution.position_mode is execution.margin_mode is execution.futures_leverages is None
    assert execution.auto_repay_spot_borrows is False


async def test_actual_node_registers_strategy_factory_and_exact_client_without_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = BybitDemoAccountSession(api_key="fixture", api_secret="fixture")
    delivered: list[object] = []
    received: list[object] = []

    def install(_binding: object, *, scope: object, install: Any) -> None:
        delivered.append(scope)
        install(build_demo_exec_config(api_key="fixture", api_secret="fixture"))

    monkeypatch.setattr(session, "configure_native_execution", install)
    scope = ProtectionScope("12345", "DEMO")
    assembly = build_native_node(session=session, binding=object(), scope=scope,
        symbols=("BTCUSDT",), trader_id="MONITOR-001",
        on_event=lambda channel, event: received.append((channel, event)))
    try:
        assert delivered == [scope]
        assert type(assembly.node) is module("live.node").TradingNode
        assert type(assembly.execution_client) is module("adapters.bybit.execution").BybitExecutionClient
        assert isinstance(assembly.strategy, module("trading.strategy").Strategy)
        assert assembly.node.get_event_loop() is asyncio.get_running_loop()
        assert assembly.strategy.cache is assembly.node.cache
        assert assembly.node.is_built() and not assembly.node.is_running()
        assert assembly.strategy.config.external_order_claims is None
        assert assembly.strategy.config.oms_type == "NETTING"
        assert assembly.strategy.config.manage_stop is False
        assert assembly.order_subscription is not None
        # Strategy is not the durable callback route (its state guard drops late events).
        assert received == []
        assert assembly.bridge._strategy is assembly.strategy
    finally:
        # node.dispose() would stop the shared pytest/worker loop. Dispose only kernel resources.
        assembly.dispose_local()
        await session.aclose()


def test_actual_builder_rejects_mutating_or_implicit_account_configuration() -> None:
    structs = __import__("msgspec.structs", fromlist=["replace"])
    original = build_demo_exec_config(api_key="fixture", api_secret="fixture")
    for changes in ({"position_mode": {}}, {"futures_leverages": {"BTCUSDT": 2}},
                    {"auto_repay_spot_borrows": True}, {"api_key": None}):
        with pytest.raises(ValueError, match="NONMUTATING"):
            build_node_config(structs.replace(original, **changes), symbols=("BTCUSDT",),
                              trader_id="MONITOR-001")


def native_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    h = harness(tmp_path)
    session = BybitDemoAccountSession(api_key="fixture", api_secret="fixture")

    def install(_binding: object, *, scope: object, install: Any) -> None:
        install(build_demo_exec_config(api_key="fixture", api_secret="fixture"))

    monkeypatch.setattr(session, "configure_native_execution", install)

    def build(sink: Any) -> Any:
        h.assembly = build_native_node(session=session, binding=object(), scope=h.worker.scope,
            symbols=("BTCUSDT",), trader_id="MONITOR-001", on_event=sink)
        return h.assembly

    h.worker._factory = build
    h.worker._decoder = None
    h.native_session = session
    return h


@pytest.mark.parametrize("connect", [False, True])
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
async def test_actual_node_signal_replacement_is_reclaimed_by_monitor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, connect: bool, sig: int,
) -> None:
    h = native_worker(tmp_path, monkeypatch)
    loop, stop, done = asyncio.get_running_loop(), asyncio.Event(), asyncio.Event()
    worker_cli.install_signal_handlers(loop, stop)
    original = loop._signal_handlers[sig]  # type: ignore[attr-defined]
    observed: list[bool] = []

    async def run() -> None:
        await done.wait()

    async def shutdown() -> None:
        done.set()

    def after_start() -> None:
        # Real pinned construction has replaced the previously installed owner handler.
        observed.append(loop._signal_handlers[sig] is not original)  # type: ignore[attr-defined]
        monkeypatch.setattr(h.assembly.node, "run_async", run)
        monkeypatch.setattr(h.assembly.node, "stop_async", shutdown)
        worker_cli.install_signal_handlers(loop, stop)

    async def refresh() -> None:
        loop._signal_handlers[sig]._run()  # type: ignore[attr-defined]

    try:
        await asyncio.wait_for(worker_cli.serve_monitor(h.worker, stop=stop, refresh=refresh,
            interval=0.001, connect=connect, on_started=after_start), 2)
        assert observed == [True] and stop.is_set() and h.worker._stopped
        lock = AccountWorkerLock(tmp_path, account_id=h.worker.scope.account_id, environment="DEMO")
        lock.acquire()
        lock.release()
    finally:
        if h.worker._assembly is not None:
            h.assembly.dispose_local()
        for item in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(item)
        await h.native_session.aclose()


async def test_actual_bus_delivers_owned_fill_during_strategy_stop_and_disconnect_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = native_worker(tmp_path, monkeypatch)
    native_strategy = module("trading.strategy")
    states: list[str] = []
    strategy_callbacks: list[object] = []

    class ProbeStrategy(native_strategy.Strategy):
        def on_order_event(self, event: object) -> None:
            strategy_callbacks.append(event)

        def on_stop(self) -> None:
            states.append(self.state.name)
            publish()

    monkeypatch.setattr(native_strategy, "Strategy", ProbeStrategy)
    await h.worker.start_passive()
    record = await stage(h)
    h.worker.runtime.journal.claim("entry")
    strategy, kernel = h.assembly.strategy, h.assembly.node.kernel
    ids, enums, objects = module("model.identifiers"), module("model.enums"), module("model.objects")
    events = [module("model.events.order").OrderFilled(
        trader_id=strategy.trader_id, strategy_id=strategy.id,
        instrument_id=ids.InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT"),
        client_order_id=ids.ClientOrderId(record.client_order_id),
        account_id=ids.AccountId("BYBIT-UNIFIED"), venue_order_id=ids.VenueOrderId("venue-1"),
        trade_id=ids.TradeId(f"fill-{index}"), position_id=None, order_side=enums.OrderSide.BUY,
        order_type=enums.OrderType.LIMIT, last_qty=objects.Quantity.from_str("0.2"),
        last_px=objects.Price.from_str("100"), currency=module("model.currencies").USDT,
        commission=objects.Money(Decimal("0.01"), module("model.currencies").USDT),
        liquidity_side=enums.LiquiditySide.MAKER, event_id=module("core.uuid").UUID4(),
        ts_event=int(NOW.timestamp()) * 10**9, ts_init=int(NOW.timestamp()) * 10**9)
        for index in range(3)]
    published: list[object] = []

    def publish() -> None:
        # Real MessageBus invokes actual Strategy.handle_event AND the independent sink.
        event = events[min(len(published), 2)]
        published.append(event)
        kernel.msgbus.publish(topic=f"events.order.{strategy.id}", msg=event)

    done = asyncio.Event()

    async def run() -> None:
        kernel.trader.start()
        await done.wait()

    async def residuals() -> None:
        await asyncio.sleep(0)

    def disconnect() -> None:
        states.append(strategy.state.name)
        asyncio.get_running_loop().call_soon(publish)

    async def disconnected() -> None:
        await asyncio.sleep(0)
        publish()  # Replay the final fill while the channel and SQLite are still open.
        done.set()

    monkeypatch.setattr(h.assembly.node, "run_async", run)
    monkeypatch.setattr(kernel, "_await_trader_residuals", residuals)
    monkeypatch.setattr(kernel, "_stop_clients", lambda: None)
    monkeypatch.setattr(kernel, "_disconnect_clients", disconnect)
    monkeypatch.setattr(kernel, "_await_engines_disconnected", disconnected)
    try:
        await h.worker.connect_monitoring()
        publish()
        await asyncio.sleep(0)
        assert strategy_callbacks == [events[0]]
        await h.worker.stop()  # Actual TradingNode.stop_async / kernel lifecycle.
        assert "STOPPING" in states[0] and "STOPPED" in states[1]
        assert strategy_callbacks == [events[0]]  # Real handler dropped the late deliveries.
        assert published == [*events, events[-1]]
        assert h.worker.callback_failures == 0
        h.assembly.require_quiesced()
        restarted = harness_existing(tmp_path, h)
        await restarted.start_passive()
        assert restarted.runtime.projection("position-1").owned_quantity == 0
        assert restarted.runtime.projection("position-1").executions == ()
        assert restarted.runtime.projection("position-1").entries_blocked
        assert {row.candidate.fill.execution_id for row in quarantines(restarted)} == {
            "fill-0", "fill-1", "fill-2"}
        assert sum(row.candidate.fill.quantity for row in quarantines(restarted)) == Decimal("0.6")
        await restarted.stop()
    finally:
        h.assembly.dispose_local()
        for item in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().remove_signal_handler(item)
        await h.native_session.aclose()
