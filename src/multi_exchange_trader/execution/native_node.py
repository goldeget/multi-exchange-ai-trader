"""Explicit, lazy Linux bootstrap for the pinned node; construction never connects.

The adapter account label is bound to the UID by the credential-owning session,
not by interpreting ``BYBIT-UNIFIED``. A node generation stays valid for owned
callbacks even when refreshable API-key metadata expires.
"""

from __future__ import annotations

import asyncio
import importlib
import re
import sys
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from types import CodeType
from typing import Any
from uuid import uuid4

from .account_binding import AuthenticatedAccountBinding, BybitDemoAccountSession
from .bybit_native import NativeBybitBridge, _require_native_version, load_native_bindings
from .monitoring_models import MonitoringError, MonitoringTarget, StreamStamp
from .protection import ProtectionScope


@dataclass(frozen=True)
class NativeChannel:
    scope: ProtectionScope
    native_account_id: str
    generation: str = field(default_factory=lambda: uuid4().hex)
    venue: str = "BYBIT"


@dataclass(repr=False)
class NativeNodeAssembly:
    node: Any
    strategy: Any
    execution_client: Any
    bridge: NativeBybitBridge
    channel: NativeChannel
    order_subscription: NativeOrderSubscription | None = None
    _owned_executor: Any = field(default=None, repr=False)
    _actor_workers: dict[Any, asyncio.Task[Any]] = field(default_factory=dict, repr=False)
    _disconnect_workers: set[asyncio.Task[Any]] = field(default_factory=set, repr=False)
    _engine_queues: Any = field(default=None, repr=False)

    def _owned_sdk_tasks(self) -> tuple[list[tuple[Any, asyncio.Task[Any]]], list[asyncio.Task[Any]]]:
        """Pinned 1.231.0 hides Actor._executor and does not retain disconnect tasks.

        Coroutines expose their exact owners. Identity with this kernel's newly
        allocated pool/loop is required; task names alone never grant ownership.
        """
        from nautilus_trader.common.executor import ActorExecutor
        from nautilus_trader.live.execution_client import LiveExecutionClient

        _require_native_version()
        kernel, loop = self.node.kernel, asyncio.get_running_loop()
        if self._owned_executor is None or kernel.executor is not self._owned_executor or kernel.loop is not loop:
            raise RuntimeError("NATIVE_EXECUTOR_OWNER_CHANGED")
        disconnect_codes = tuple(code for code in LiveExecutionClient.disconnect.__code__.co_consts
                                 if isinstance(code, CodeType) and code.co_name == "_disconnect_with_cleanup")
        if len(disconnect_codes) != 1:
            raise RuntimeError("NATIVE_DISCONNECT_TASK_SHAPE_UNSUPPORTED")
        actors, disconnects = [], []
        for task in asyncio.all_tasks(loop):
            coroutine = task.get_coro()
            frame, code = getattr(coroutine, "cr_frame", None), getattr(coroutine, "cr_code", None)
            if frame is None:
                continue
            owner = frame.f_locals.get("self")
            if code is ActorExecutor._worker.__code__ and type(owner) is ActorExecutor and owner._executor is self._owned_executor:
                if owner._loop is not loop:
                    raise RuntimeError("NATIVE_EXECUTOR_OWNER_CHANGED")
                actors.append((owner, task))
            elif code is disconnect_codes[0] and owner is self.execution_client:
                disconnects.append(task)
        return actors, disconnects

    def capture_engine_queues(self, require_owner: Callable[[], None]) -> None:
        """Bind live coroutine owners before stop can erase their frames."""
        from nautilus_trader.live.data_engine import LiveDataEngine
        from nautilus_trader.live.execution_engine import LiveExecutionEngine
        from nautilus_trader.live.risk_engine import LiveRiskEngine

        _require_native_version()
        require_owner()
        if self._engine_queues is not None:
            self._check_engine_queues(require_owner, stopped=False)
            return
        node, loop = self.node, asyncio.get_running_loop()
        kernel = node.kernel
        engines = (kernel.data_engine, kernel.risk_engine, kernel.exec_engine)
        shapes = ((LiveDataEngine, ("cmd", "req", "res", "data")),
                  (LiveRiskEngine, ("cmd", "evt")),
                  (LiveExecutionEngine, ("cmd", "evt")))
        entries = []
        if kernel.loop is not loop:
            raise RuntimeError("NATIVE_QUEUE_OWNER_CHANGED")
        for engine, (cls, names) in zip(engines, shapes, strict=True):
            if engine._loop is not loop:
                raise RuntimeError("NATIVE_QUEUE_OWNER_CHANGED")
            tasks = tuple(getattr(engine, f"get_{name}_queue_task")() for name in names)
            if any(getattr(engine, f"_{name}_queue_task") is not task
                   for name, task in zip(names, tasks, strict=True)):
                raise RuntimeError("NATIVE_QUEUE_TASK_SHAPE_UNSUPPORTED")
            if any(task is None for task in tasks) and not all(task is None for task in tasks):
                raise RuntimeError("NATIVE_QUEUE_TASK_SHAPE_UNSUPPORTED")
            for name, task in zip(names, tasks, strict=True):
                if task is not None:
                    if (not isinstance(task, asyncio.Task) or task.get_loop() is not loop
                            or task.cancelling()):
                        raise RuntimeError("NATIVE_QUEUE_TASK_SHAPE_UNSUPPORTED")
                    coro = task.get_coro()
                    frame = getattr(coro, "cr_frame", None)
                    if (getattr(coro, "cr_code", None) is not getattr(cls, f"_run_{name}_queue").__code__
                            or frame is None or frame.f_locals.get("self") is not engine):
                        raise RuntimeError("NATIVE_QUEUE_TASK_SHAPE_UNSUPPORTED")
                entries.append((engine, name, task))
        self._engine_queues = node, kernel, loop, engines, tuple(entries)
        self._check_engine_queues(require_owner, stopped=False)

    def _check_engine_queues(self, require_owner: Callable[[], None], *, stopped: bool) -> None:
        require_owner()
        node, kernel, loop, engines, entries = self._engine_queues
        if (self.node is not node or node.kernel is not kernel or kernel.loop is not loop
                or asyncio.get_running_loop() is not loop or stopped and kernel.is_running()
                or any(current is not original for current, original in zip(
                    (kernel.data_engine, kernel.risk_engine, kernel.exec_engine), engines, strict=True))
                or any(engine._loop is not loop for engine in engines)):
            raise RuntimeError("NATIVE_QUEUE_OWNER_CHANGED")
        for engine, name, task in entries:
            if (getattr(engine, f"_{name}_queue_task") is not task
                    or getattr(engine, f"get_{name}_queue_task")() is not task):
                raise RuntimeError("NATIVE_QUEUE_OWNER_CHANGED")
            if task is not None and task.cancelling():
                raise RuntimeError("NATIVE_QUEUE_TASK_CANCELLED")

    async def settle_engine_queues(self, require_owner: Callable[[], None]) -> None:
        """Await the exact captured stopped-node consumers, without cancelling them."""
        self.capture_engine_queues(require_owner)
        snapshot = self._engine_queues
        _, _, _, _, entries = snapshot

        def check() -> None:
            if self._engine_queues is not snapshot:
                raise RuntimeError("NATIVE_QUEUE_OWNER_CHANGED")
            self._check_engine_queues(require_owner, stopped=True)

        check()
        for _, _, task in entries:
            if task is not None:
                await asyncio.shield(task)
                check()
                if task.cancelled() or task.cancelling():
                    raise RuntimeError("NATIVE_QUEUE_TASK_CANCELLED")
        check()

    async def settle_owned_tasks(self) -> None:
        """After genuine stop, await disconnect and retire only idle owned actors."""
        kernel = self.node.kernel
        if kernel.is_running():
            raise RuntimeError("NATIVE_NODE_STILL_RUNNING")
        self.require_quiesced()
        actors, disconnects = self._owned_sdk_tasks()
        self._disconnect_workers.update(disconnects)
        for task in self._disconnect_workers:
            await asyncio.shield(task)
        self.require_quiesced()
        current = {owner: task for owner, task in actors if owner._worker_task is task}
        expected = sum(len(items()) for items in (kernel.trader.actors, kernel.trader.strategies,
                                                  kernel.trader.exec_algorithms))
        if len(current | self._actor_workers) != expected:
            raise RuntimeError("NATIVE_ACTOR_TASK_SHAPE_UNSUPPORTED")
        for owner in current:
            if (not owner._queue.empty() or owner._queued_tasks
                    or any(not future.done() for future in owner._active_tasks.values())):
                raise RuntimeError("NATIVE_ACTOR_WORK_NOT_QUIESCED")
        self._actor_workers.update(current)
        for owner, task in actors:
            if owner._worker_task is task:
                task.cancel()
            # Actor.stop may have cancelled the predecessor and created a new
            # worker. Both exact owned tasks must finish before the sink closes.
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                caller = asyncio.current_task()
                if caller is None or caller.cancelling() or not task.cancelled():
                    raise
        self.require_owned_tasks_quiesced()

    def require_owned_tasks_quiesced(self) -> None:
        actors, disconnects = self._owned_sdk_tasks()
        if (actors or disconnects or any(not task.done() for task in self._actor_workers.values())
                or any(not task.done() for task in self._disconnect_workers)):
            raise RuntimeError("NATIVE_OWNED_TASKS_NOT_QUIESCED")

    def _monitor_client(self) -> Any:
        from .native_monitor_client import MonitoringBybitExecutionClient

        if type(self.execution_client) is not MonitoringBybitExecutionClient:
            raise MonitoringError("MONITORING_CLIENT_REQUIRED")
        return self.execution_client

    @property
    def monitoring_fault(self) -> str | None:
        value = self._monitor_client().monitoring_fault
        return value if isinstance(value, str) else None

    def monitoring_stamp(self) -> StreamStamp:
        stamp: StreamStamp = self._monitor_client().monitoring_stamp()
        return stamp

    def arm_monitoring(self, target: MonitoringTarget) -> None:
        self._monitor_client().arm_monitoring(target)

    def require_monitoring_ready(self) -> None:
        client, kernel = self._monitor_client(), self.node.kernel
        client._event_guard()
        if (not client.is_connected or not kernel.is_running()
                or not kernel.data_engine.is_running or not kernel.risk_engine.is_running
                or not kernel.exec_engine.is_running or not kernel.trader.is_running
                or not self.strategy.is_running or not kernel.portfolio.initialized
                or not kernel.data_engine.check_connected() or not kernel.exec_engine.check_connected()):
            raise MonitoringError("MONITORING_NATIVE_LIFECYCLE_NOT_READY")
        client._monitor_stream.require_ready()

    async def wait_monitoring_ready(self) -> None:
        await self._monitor_client().wait_monitoring_ready()
        while True:
            try:
                self.require_monitoring_ready()
                return
            except MonitoringError as error:
                if str(error) != "MONITORING_NATIVE_LIFECYCLE_NOT_READY":
                    raise
            await asyncio.sleep(0.01)

    async def monitoring_barrier(self) -> StreamStamp:
        await self.wait_monitoring_ready()
        stamp: StreamStamp = await self._monitor_client().monitoring_barrier()
        self.require_monitoring_ready()
        return stamp

    def require_monitoring_delivery(self, event: object) -> None:
        """Only already-noted exact originals may enter app consumption, even at stop."""
        client = self._monitor_client()
        reports = client._monitor_reports
        expected = None if reports is None else reports._expected.get(id(event))
        if (expected is None or expected["event"] is not event
                or not expected["native"] or expected["future"].done()):
            raise MonitoringError("MONITORING_ACCEPTED_EVENT_REQUIRED")
        reports._accepted_guard()

    def acknowledge_monitoring(self, event: object, *, evidence: object) -> None:
        self._monitor_client().acknowledge_monitoring(event, evidence=evidence)

    def retire_monitoring(self, reason: str) -> None:
        self._monitor_client().retire_monitoring(reason)

    async def drain_monitoring(self) -> StreamStamp:
        stamp: StreamStamp = await self._monitor_client().drain_monitoring()
        return stamp

    def require_monitoring_tasks_quiesced(self) -> None:
        client = self._monitor_client()
        stream = client._monitor_stream
        if (any(not task.done() for task in client._tasks)
                or not client._repay_task.done()
                or any(not task.done() for task in client._ws_client_futures)
                or stream is not None and (any(not task.done() for task in stream._tasks)
                    or stream._socket is not None and not stream._socket.is_closed())):
            raise MonitoringError("MONITORING_CLIENT_TASKS_NOT_QUIESCED")
        if self._owned_executor is not None:
            self.require_owned_tasks_quiesced()

    def close_event_channel(self) -> None:
        if self.order_subscription is not None:
            self.order_subscription.close()

    def require_quiesced(self) -> None:
        """Pinned stop logs disconnect timeout rather than raising it."""
        kernel = self.node.kernel
        if (not kernel.data_engine.check_disconnected()
                or not kernel.exec_engine.check_disconnected()
                or kernel.exec_engine.evt_qsize() or kernel.exec_engine.cmd_qsize()):
            raise RuntimeError("NATIVE_FEED_NOT_QUIESCED")

    def dispose_local(self) -> None:
        """Release an already stopped kernel without closing the shared owner loop."""
        self.close_event_channel()
        self.node.kernel.dispose()
        if self.node.kernel.executor:
            self.node.kernel.executor.shutdown(wait=True, cancel_futures=True)


class NativeOrderSubscription:
    """Single callback route independent of Strategy's RUNNING-only handler.

    The exact strategy topic and native identities fence routing; the owner-loop
    runtime decoder still requires a persisted owned order before any mutation.
    Strategy.on_order_event remains its no-op default, so normal delivery has
    no second path. Durable execution identity handles exchange duplicates.
    """

    def __init__(self, *, msgbus: Any, strategy: Any, channel: NativeChannel,
                 event_types: tuple[type[object], ...],
                 on_event: Callable[[NativeChannel, object], None],
                 on_native: Callable[[object], None] | None = None) -> None:
        self._bus, self._strategy, self._channel = msgbus, strategy, channel
        self._types, self._sink = event_types, on_event
        self._on_native = on_native
        self._topic = f"events.order.{strategy.id}"
        self._closed = False
        msgbus.subscribe(topic=self._topic, handler=self.handle)

    def handle(self, event: object) -> None:
        if (self._closed or type(event) not in self._types
                or getattr(event, "trader_id", None) != self._strategy.trader_id
                or getattr(event, "strategy_id", None) != self._strategy.id):
            return
        if self._on_native is not None:
            self._on_native(event)
        self._sink(self._channel, event)

    def close(self) -> None:
        if not self._closed:
            self._bus.unsubscribe(topic=self._topic, handler=self.handle)
            self._closed = True


def _native_modules() -> dict[str, Any]:
    if sys.platform != "linux":
        raise ValueError("NATIVE_NODE_LINUX_REQUIRED")
    _require_native_version()
    return {name: importlib.import_module(path) for name, path in {
        "config": "nautilus_trader.config",
        "bybit": "nautilus_trader.adapters.bybit.config",
        "factories": "nautilus_trader.adapters.bybit.factories",
        "node": "nautilus_trader.live.node",
        "strategy": "nautilus_trader.trading.strategy",
        "events": "nautilus_trader.model.events.order",
        "identifiers": "nautilus_trader.model.identifiers",
        "native": "nautilus_trader.core.nautilus_pyo3",
        "structs": "msgspec.structs",
    }.items()}


def build_node_config(exec_config: object, *, symbols: tuple[str, ...],
                      trader_id: str, restart_safe: bool = False,
                      monitor_only: bool = False) -> object:
    """Build the actual pinned config without importing native modules on Windows."""
    if (type(symbols) is not tuple or not symbols or len(set(symbols)) != len(symbols)
            or any(type(s) is not str or re.fullmatch(r"[A-Z0-9]{2,48}", s) is None
                   for s in symbols)):
        raise ValueError("EXPLICIT_NATIVE_INSTRUMENTS_REQUIRED")
    modules = _native_modules()
    cfg, ids = modules["config"], modules["identifiers"]
    if type(exec_config) is not modules["bybit"].BybitExecClientConfig:
        raise ValueError("PINNED_BYBIT_CONFIG_REQUIRED")
    native = modules["native"]
    if (exec_config.environment != native.BybitEnvironment.DEMO  # type: ignore[attr-defined]
            or exec_config.product_types != (native.BybitProductType.LINEAR,)  # type: ignore[attr-defined]
            or any(getattr(exec_config, name) is not None for name in (
                "futures_leverages", "position_mode", "margin_mode", "base_url_http",
                "base_url_ws_private", "base_url_ws_trade", "proxy_url"))
            or exec_config.auto_repay_spot_borrows is not False  # type: ignore[attr-defined]
            or exec_config.use_spot_position_reports is not False  # type: ignore[attr-defined]
            or not exec_config.api_key or not exec_config.api_secret):  # type: ignore[attr-defined]
        raise ValueError("NONMUTATING_EXPLICIT_DEMO_CONFIG_REQUIRED")
    instruments = [ids.InstrumentId.from_str(s + "-LINEAR.BYBIT") for s in symbols]
    provider = cfg.InstrumentProviderConfig(load_all=False, load_ids=frozenset(instruments))
    execution = modules["structs"].replace(exec_config, instrument_provider=provider)
    settings = {"exec_engine": cfg.LiveExecEngineConfig(
        load_cache=not restart_safe, reconciliation=not restart_safe,
        reconciliation_instrument_ids=instruments,
        filter_unclaimed_external_orders=True, filter_position_reports=True,
        generate_missing_orders=False)}
    if monitor_only:
        from .native_monitor_client import passive_node_settings, validate_passive_config

        validate_passive_config(execution)
        settings = passive_node_settings(instruments)
        # Pinned kernel.stop_async unconditionally sleeps this value before
        # check_residuals. Its ordinary 10s default exhausts our entire cleanup
        # attempt before disconnect begins; passive drain still proves quiescence.
        settings["timeout_post_stop"] = 0.05
    return cfg.TradingNodeConfig(
        trader_id=trader_id,
        logging=cfg.LoggingConfig(log_level="ERROR"),
        **settings,
        data_clients={}, exec_clients={"BYBIT": execution},
    )


def build_native_node(*, session: BybitDemoAccountSession,
                      binding: AuthenticatedAccountBinding, scope: ProtectionScope,
                      symbols: tuple[str, ...], trader_id: str,
                      on_event: Callable[[NativeChannel, object], None],
                      restart_safe: bool = False,
                      monitor_only: bool = False) -> NativeNodeAssembly:
    """Register real Strategy/factory/client on the current worker loop, without run.

    Only session.configure_native_execution delivers credentials into the builder.
    No settings, environment keys, external claims, or default instruments are read.
    """
    loop = asyncio.get_running_loop()
    modules = _native_modules()
    result: list[NativeNodeAssembly] = []

    def install(config: object) -> None:
        node_config = build_node_config(config, symbols=symbols, trader_id=trader_id,
                                        restart_safe=restart_safe, monitor_only=monitor_only)
        from .native_loop_ownership import preserve_default_executor

        ownership = preserve_default_executor(loop) if monitor_only else nullcontext()
        with ownership:
            native_node = modules["node"].TradingNode(config=node_config, loop=loop)
        try:
            captured: list[Any] = []
            channel = NativeChannel(scope, "BYBIT-UNIFIED")

            def create(**kwargs: Any) -> Any:
                if monitor_only:
                    from .native_monitor_client import create_monitor_client

                    client = create_monitor_client(session=session, binding=binding, scope=scope,
                                                   symbols=symbols, **kwargs)
                else:
                    client = modules["factories"].BybitLiveExecClientFactory.create(**kwargs)
                captured.append(client)
                return client

            factory = type("BoundBybitFactory", (modules["factories"].BybitLiveExecClientFactory,),
                           {"create": staticmethod(create)})

            strategy = modules["strategy"].Strategy(config=modules["config"].StrategyConfig(
                strategy_id="OwnedExecution", order_id_tag="001", oms_type="NETTING",
                external_order_claims=None, manage_contingent_orders=False,
                manage_gtd_expiry=False, manage_stop=False))
            native_node.trader.add_strategy(strategy)
            native_node.add_exec_client_factory("BYBIT", factory)
            native_node.build()
            if len(captured) != 1 or str(captured[0].account_id) != channel.native_account_id:
                raise ValueError("EXACT_NATIVE_CLIENT_ACCOUNT_REQUIRED")
            bridge = NativeBybitBridge(scope=scope, native_account_id=channel.native_account_id,
                factory=strategy.order_factory, strategy=strategy, bindings=load_native_bindings())
            subscription = NativeOrderSubscription(msgbus=native_node.kernel.msgbus,
                strategy=strategy, channel=channel, on_event=on_event,
                event_types=tuple(getattr(modules["events"], name) for name in (
                    "OrderSubmitted", "OrderAccepted", "OrderDenied", "OrderRejected",
                    "OrderCanceled", "OrderExpired", "OrderFilled")),
                on_native=captured[0].note_monitoring_native if monitor_only else None)
            result.append(NativeNodeAssembly(native_node, strategy, captured[0], bridge, channel,
                                             subscription, native_node.kernel.executor if monitor_only else None))
        except BaseException:
            native_node.kernel.dispose()
            if native_node.kernel.executor:
                native_node.kernel.executor.shutdown(wait=True, cancel_futures=True)
            raise

    installer = session.configure_native_monitoring if monitor_only else session.configure_native_execution
    installer(binding, scope=scope, install=install)
    if len(result) != 1:
        raise ValueError("SESSION_NATIVE_INSTALLATION_REQUIRED")
    return result[0]
