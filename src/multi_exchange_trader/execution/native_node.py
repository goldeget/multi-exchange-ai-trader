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
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .account_binding import AuthenticatedAccountBinding, BybitDemoAccountSession
from .bybit_native import NativeBybitBridge, _require_native_version, load_native_bindings
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
                 on_event: Callable[[NativeChannel, object], None]) -> None:
        self._bus, self._strategy, self._channel = msgbus, strategy, channel
        self._types, self._sink = event_types, on_event
        self._topic = f"events.order.{strategy.id}"
        self._closed = False
        msgbus.subscribe(topic=self._topic, handler=self.handle)

    def handle(self, event: object) -> None:
        if (self._closed or type(event) not in self._types
                or getattr(event, "trader_id", None) != self._strategy.trader_id
                or getattr(event, "strategy_id", None) != self._strategy.id):
            return
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
                      trader_id: str, restart_safe: bool = False) -> object:
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
    return cfg.TradingNodeConfig(
        trader_id=trader_id,
        logging=cfg.LoggingConfig(log_level="ERROR"),
        exec_engine=cfg.LiveExecEngineConfig(
            load_cache=not restart_safe, reconciliation=not restart_safe,
            reconciliation_instrument_ids=instruments,
            filter_unclaimed_external_orders=True, filter_position_reports=True,
            generate_missing_orders=False),
        data_clients={}, exec_clients={"BYBIT": execution},
    )


def build_native_node(*, session: BybitDemoAccountSession,
                      binding: AuthenticatedAccountBinding, scope: ProtectionScope,
                      symbols: tuple[str, ...], trader_id: str,
                      on_event: Callable[[NativeChannel, object], None],
                      restart_safe: bool = False) -> NativeNodeAssembly:
    """Register real Strategy/factory/client on the current worker loop, without run.

    Only session.configure_native_execution delivers credentials into the builder.
    No settings, environment keys, external claims, or default instruments are read.
    """
    loop = asyncio.get_running_loop()
    modules = _native_modules()
    result: list[NativeNodeAssembly] = []

    def install(config: object) -> None:
        node_config = build_node_config(config, symbols=symbols, trader_id=trader_id,
                                        restart_safe=restart_safe)
        native_node = modules["node"].TradingNode(config=node_config, loop=loop)
        try:
            captured: list[Any] = []
            channel = NativeChannel(scope, "BYBIT-UNIFIED")

            def create(**kwargs: Any) -> Any:
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
                    "OrderCanceled", "OrderExpired", "OrderFilled")))
            result.append(NativeNodeAssembly(native_node, strategy, captured[0], bridge, channel,
                                             subscription))
        except BaseException:
            native_node.kernel.dispose()
            if native_node.kernel.executor:
                native_node.kernel.executor.shutdown(wait=True, cancel_futures=True)
            raise

    session.configure_native_execution(binding, scope=scope, install=install)
    if len(result) != 1:
        raise ValueError("SESSION_NATIVE_INSTALLATION_REQUIRED")
    return result[0]
