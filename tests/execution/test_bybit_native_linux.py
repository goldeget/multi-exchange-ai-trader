"""Pinned real-wheel contracts inside an OS network namespace only.

Ordinary portable runs skip this module. REQUIRE_NATIVE_CONTRACTS=1 turns every
missing prerequisite into a failure. No node/client is connected or started.
The real Strategy and adapter submit method run at fake command/transport/event
boundaries; the fake HTTP transport can never reach an exchange.
"""

from __future__ import annotations

import importlib
import os
import socket
import sys
from dataclasses import replace
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

import pytest

from multi_exchange_trader.execution.bybit_native import (
    AttachedProtection,
    NativeBybitBridge,
    NativeOrderRequest,
    build_demo_exec_config,
    load_native_bindings,
)
from multi_exchange_trader.execution.native_events import NativeEventContext
from multi_exchange_trader.execution.protection import OwnedOrder, ProtectionScope

SCOPE = ProtectionScope("fixture-account", "DEMO")
CLIENT_ID = "ec_0123456789abcdef0123456789abcdef"
INSTRUMENT = "BTCUSDT-LINEAR.BYBIT"
TS_EVENT = 1700000000000000100
TS_INIT = 1700000000000000200
TS_RECEIVED = 1700000000000000900


@pytest.fixture(scope="module", autouse=True)
def isolated_native_runtime() -> None:
    if os.environ.get("REQUIRE_NATIVE_CONTRACTS") != "1":
        pytest.skip("explicit isolated Linux native-contract job required")
    assert sys.platform == "linux", "native contract requires Linux"
    assert os.environ.get("NATIVE_NETWORK_ISOLATED") == "1"
    assert Path("/proc/self/ns/net").readlink() != Path("/proc/1/ns/net").readlink()
    assert sorted(path.name for path in Path("/sys/class/net").iterdir()) == ["lo"]
    # Check both IP families. Unlike socket monkeypatching this protects Rust too.
    for family, address in [(socket.AF_INET, ("192.0.2.1", 9)),
                            (socket.AF_INET6, ("2001:db8::1", 9))]:
        with pytest.raises(OSError), socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.1)
            probe.connect(address)
    assert version("nautilus-trader") == "1.231.0"


def module(path: str) -> Any:
    # Never import the native dependency until the OS boundary fixture has passed.
    return importlib.import_module("nautilus_trader." + path)


def request(*, market: bool = False, exit_order: bool = False) -> NativeOrderRequest:
    owned = OwnedOrder(CLIENT_ID, "position-1", INSTRUMENT,
        "SELL" if exit_order else "BUY", "EXIT" if exit_order else "ENTRY",
        Decimal("0.010"), exit_order, 0, "policy-1")
    return NativeOrderRequest(SCOPE, owned, "MARKET" if market else "LIMIT", Decimal(50000),
        limit_price=None if market else Decimal("50000.0"), attached=None if exit_order else
        AttachedProtection(Decimal(49000), Decimal(52000), "MarkPrice"))


@pytest.fixture
def native_harness() -> Any:
    clock = module("common.component").TestClock()
    clock.set_time(TS_INIT)
    trader = module("model.identifiers").TraderId("TRADER-001")
    msgbus = module("common.component").MessageBus(trader_id=trader, clock=clock)
    cache = module("cache.cache").Cache()
    portfolio = module("portfolio.portfolio").Portfolio(msgbus=msgbus, cache=cache, clock=clock)
    strategy = module("trading.strategy").Strategy()
    strategy.register(trader_id=trader, portfolio=portfolio, msgbus=msgbus, cache=cache, clock=clock)
    commands: list[Any] = []
    # The real Strategy creates/routs a native SubmitOrder. No engine dispatch runs.
    msgbus.register(endpoint="RiskEngine.execute", handler=commands.append)
    bridge = NativeBybitBridge(scope=SCOPE, native_account_id="BYBIT-UNIFIED",
        factory=strategy.order_factory, strategy=strategy, bindings=load_native_bindings())
    return SimpleNamespace(clock=clock, trader=trader, msgbus=msgbus, cache=cache,
        strategy=strategy, commands=commands, bridge=bridge,
        context=NativeEventContext(SCOPE, "BYBIT-UNIFIED", TS_RECEIVED))


def test_actual_native_config_is_demo_linear_and_never_mutates_account_settings() -> None:
    config = build_demo_exec_config(api_key="fixture-only", api_secret="fixture-only")
    native = module("core.nautilus_pyo3")
    assert config.environment == native.BybitEnvironment.DEMO
    assert config.product_types == (native.BybitProductType.LINEAR,)
    assert config.futures_leverages is config.margin_mode is config.position_mode is None
    assert config.max_retries == 1
    assert config.retry_delay_initial_ms == 500
    assert config.retry_delay_max_ms == 1000
    assert config.base_url_http is config.base_url_ws_private is config.base_url_ws_trade is None
    assert config.auto_repay_spot_borrows is False
    # msgspec validates the actual pinned PositiveInt schema; direct Struct init
    # alone does not establish that zero is valid.
    msgspec = importlib.import_module("msgspec")
    encoded = config.json()
    assert type(config).parse(encoded).max_retries == 1
    payload = msgspec.json.decode(encoded)
    payload["max_retries"] = 0
    with pytest.raises(msgspec.ValidationError):
        type(config).parse(msgspec.json.encode(payload))
    with pytest.raises(ValueError, match="POSITIVE_RETRY"):
        build_demo_exec_config(api_key="fixture-only", api_secret="fixture-only", max_retries=0)


@pytest.mark.parametrize("market,exit_order", [(False, False), (True, False), (True, True)])
def test_actual_order_factory_and_strategy_forward_exact_id_and_command_params(
    native_harness: Any, market: bool, exit_order: bool,
) -> None:
    harness = native_harness
    result = harness.bridge.submit(request(market=market, exit_order=exit_order))
    assert result.state == "SUBMITTING"
    assert len(harness.commands) == 1
    command = harness.commands[0]
    assert type(command) is module("execution.messages").SubmitOrder
    assert command.order.client_order_id.value == CLIENT_ID
    assert command.order.instrument_id.value == INSTRUMENT
    assert command.order.quantity.as_decimal() == Decimal("0.010")
    assert command.order.is_reduce_only == exit_order
    assert command.order.side == module("model.enums").OrderSide.SELL if exit_order else (
        command.order.side == module("model.enums").OrderSide.BUY)
    assert command.order.order_type == (module("model.enums").OrderType.MARKET if market
        else module("model.enums").OrderType.LIMIT)
    if not market:
        assert command.order.price.as_decimal() == Decimal(50000)
    assert command.params == ({"position_idx": 0} if exit_order else {
        "position_idx": 0, "take_profit": "52000", "stop_loss": "49000",
        "tp_trigger_by": "MarkPrice", "sl_trigger_by": "MarkPrice",
        "tp_order_type": "Market", "sl_order_type": "Market", "tpsl_mode": "Full"})
    assert harness.cache.order(command.order.client_order_id) is command.order
    assert harness.bridge.submit(request(market=market, exit_order=exit_order)) == result
    assert len(harness.commands) == 1


class FakeHttpBoundary:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def submit_order(self, **arguments: Any) -> None:
        self.calls.append(arguments)


def adapter_boundary(harness: Any) -> Any:
    """Run the real Python adapter helpers; replace only HTTP/event emission."""
    adapter = module("adapters.bybit.execution").BybitExecutionClient
    native = module("core.nautilus_pyo3")
    order_events = module("model.events.order")
    ids = module("model.identifiers")
    uuid = module("core.uuid").UUID4
    emitted: list[object] = []
    receiver = SimpleNamespace(_is_demo=True, _position_mode=None, _order_position_ids={},
        _clock=harness.clock, _http_client=FakeHttpBoundary(),
        pyo3_account_id=native.AccountId("BYBIT-UNIFIED"), emitted=emitted,
        _log=SimpleNamespace(error=lambda *_args: None, warning=lambda *_args: None))
    for helper in ("_check_order_validity", "_resolve_position_idx", "_cache_order_position_id"):
        setattr(receiver, helper, MethodType(getattr(adapter, helper), receiver))

    def submitted(**fields: Any) -> None:
        emitted.append(order_events.OrderSubmitted(trader_id=harness.trader,
            account_id=ids.AccountId("BYBIT-UNIFIED"), event_id=uuid(),
            ts_init=TS_INIT, **fields))

    def denied(**fields: Any) -> None:
        fields.pop("ts_event")
        emitted.append(order_events.OrderDenied(trader_id=harness.trader,
            event_id=uuid(), ts_init=TS_INIT, **fields))

    def rejected(**fields: Any) -> None:
        emitted.append(order_events.OrderRejected(trader_id=harness.trader,
            account_id=ids.AccountId("BYBIT-UNIFIED"), event_id=uuid(),
            ts_init=TS_INIT, **fields))

    receiver.generate_order_submitted = submitted
    receiver.generate_order_denied = denied
    receiver.generate_order_rejected = rejected
    return receiver


@pytest.mark.asyncio
async def test_real_demo_adapter_converts_native_tp_sl_and_emits_submitted_only(
    native_harness: Any,
) -> None:
    harness = native_harness
    harness.bridge.submit(request())
    receiver = adapter_boundary(harness)
    await module("adapters.bybit.execution").BybitExecutionClient._submit_order(
        receiver, harness.commands[0])
    assert len(receiver._http_client.calls) == 1
    payload = receiver._http_client.calls[0]
    native = module("core.nautilus_pyo3")
    assert str(payload["client_order_id"]) == CLIENT_ID
    assert str(payload["instrument_id"]) == INSTRUMENT
    assert str(payload["quantity"]) == "0.010"
    assert str(payload["price"]) == "50000.0"
    assert payload["product_type"] == native.BybitProductType.LINEAR
    assert payload["position_idx"] == native.BybitPositionIdx.from_str(0)
    assert payload["reduce_only"] is False
    protection = payload["native_tp_sl"]
    assert type(protection) is native.BybitNativeTpSlParams
    assert (protection.take_profit, protection.stop_loss, protection.tp_trigger_by,
        protection.sl_trigger_by, protection.tp_order_type, protection.sl_order_type,
        protection.tpsl_mode) == (
            "52000", "49000", "MarkPrice", "MarkPrice", "Market", "Market", "Full")
    assert len(receiver.emitted) == 1
    evidence = harness.bridge.consume(receiver.emitted[0], harness.context)
    assert evidence.kind == "SUBMITTED"
    assert evidence.fill is None
    assert evidence.terminal_confirmed is False


@pytest.mark.asyncio
async def test_real_demo_adapter_denies_forbidden_custom_trigger_before_http(
    native_harness: Any,
) -> None:
    harness = native_harness
    harness.bridge.submit(request())
    command = harness.commands[0]
    command.params["tp_trigger_price"] = "52000"
    receiver = adapter_boundary(harness)
    await module("adapters.bybit.execution").BybitExecutionClient._submit_order(receiver, command)
    assert not receiver._http_client.calls
    assert len(receiver.emitted) == 1
    evidence = harness.bridge.consume(receiver.emitted[0], harness.context)
    assert evidence.kind == "DENIED"
    assert evidence.fill is None


def test_real_events_preserve_partial_duplicate_fill_fee_and_cancel_separately(
    native_harness: Any,
) -> None:
    harness = native_harness
    harness.bridge.submit(request())
    order = harness.commands[0].order
    ids, enums, objects = module("model.identifiers"), module("model.enums"), module("model.objects")
    events = module("model.events.order")
    uuid = module("core.uuid").UUID4
    base = {"trader_id": order.trader_id, "strategy_id": order.strategy_id,
        "instrument_id": order.instrument_id, "client_order_id": order.client_order_id,
        "account_id": ids.AccountId("BYBIT-UNIFIED"), "ts_event": TS_EVENT, "ts_init": TS_INIT}
    accepted = events.OrderAccepted(**base, venue_order_id=ids.VenueOrderId("venue-1"),
        event_id=uuid())
    assert harness.bridge.consume(accepted, harness.context).kind == "ACCEPTED"
    rejected = events.OrderRejected(**base, reason="untrusted-fixture-reason", event_id=uuid())
    evidence = harness.bridge.consume(rejected, harness.context)
    assert evidence.kind == "REJECTED"
    assert "untrusted-fixture" not in repr(evidence)
    filled = events.OrderFilled(**base, venue_order_id=ids.VenueOrderId("venue-1"),
        trade_id=ids.TradeId("exec-1"), position_id=None, order_side=enums.OrderSide.BUY,
        order_type=enums.OrderType.LIMIT, last_qty=objects.Quantity.from_str("0.004"),
        last_px=objects.Price.from_str("50001.5"), currency=module("model.currencies").USDT,
        commission=objects.Money(Decimal("-0.01"), module("model.currencies").USDT),
        liquidity_side=enums.LiquiditySide.MAKER, event_id=uuid())
    evidence = harness.bridge.consume(filled, harness.context)
    duplicate = harness.bridge.consume(filled, harness.context)
    assert evidence == duplicate
    assert evidence.kind == "FILL"
    assert evidence.fill.quantity == Decimal("0.004")
    assert evidence.fill.price == Decimal("50001.5")
    assert evidence.fill.commission == Decimal("-0.01")
    assert evidence.fill.commission_currency == "USDT"
    assert evidence.fill.execution_id == "exec-1"
    assert (evidence.ts_event_ns, evidence.ts_init_ns, evidence.received_at_ns) == (
        TS_EVENT, TS_INIT, TS_RECEIVED)
    canceled = events.OrderCanceled(**base, venue_order_id=ids.VenueOrderId("venue-1"),
        event_id=uuid())
    assert harness.bridge.consume(canceled, harness.context).kind == "CANCELED"
    # Cancellation/status notification cannot erase or manufacture executions.
    assert evidence.fill.quantity == Decimal("0.004")
    assert evidence.terminal_confirmed is False
    with pytest.raises(ValueError, match="NATIVE_EVENT_"):
        harness.bridge.consume(filled, replace(harness.context,
            scope=ProtectionScope("other-account", "DEMO")))


def test_real_native_reports_never_promote_cumulative_status_or_empty_position_to_flat(
    native_harness: Any,
) -> None:
    harness = native_harness
    harness.bridge.register_owned(request())
    ids, enums, objects = module("model.identifiers"), module("model.enums"), module("model.objects")
    reports, uuid = module("execution.reports"), module("core.uuid").UUID4
    common = {"account_id": ids.AccountId("BYBIT-UNIFIED"),
        "instrument_id": ids.InstrumentId.from_str(INSTRUMENT), "ts_init": TS_INIT}
    report = reports.OrderStatusReport(**common, client_order_id=ids.ClientOrderId(CLIENT_ID),
        venue_order_id=ids.VenueOrderId("venue-1"), order_side=enums.OrderSide.BUY,
        order_type=enums.OrderType.LIMIT, time_in_force=enums.TimeInForce.GTC,
        order_status=enums.OrderStatus.FILLED, quantity=objects.Quantity.from_str("0.010"),
        filled_qty=objects.Quantity.from_str("0.010"), report_id=uuid(),
        ts_accepted=TS_EVENT, ts_last=TS_EVENT)
    evidence = harness.bridge.consume(report, harness.context)
    assert evidence.kind == "STATUS"
    assert evidence.native_status == "FILLED"
    assert evidence.fill is None
    fill = reports.FillReport(**common, client_order_id=ids.ClientOrderId(CLIENT_ID),
        venue_order_id=ids.VenueOrderId("venue-1"), trade_id=ids.TradeId("exec-report-1"),
        order_side=enums.OrderSide.BUY, last_qty=objects.Quantity.from_str("0.004"),
        last_px=objects.Price.from_str("50001.5"),
        commission=objects.Money(Decimal("0.10"), module("model.currencies").USDT),
        liquidity_side=enums.LiquiditySide.TAKER, report_id=uuid(), ts_event=TS_EVENT)
    assert harness.bridge.consume(fill, harness.context).fill.execution_id == "exec-report-1"
    position = reports.PositionStatusReport(**common, position_side=enums.PositionSide.FLAT,
        quantity=objects.Quantity.from_str("0.000"), report_id=uuid(), ts_last=TS_EVENT)
    assert not hasattr(position, "leverage")
    assert not hasattr(position, "take_profit")
    assert not hasattr(position, "stop_loss")
    for values, success, complete in [((), True, True), ((), False, True),
            ((report,), True, False), ((position,), True, True)]:
        result = harness.bridge.consume_reports(values, harness.context,
            query_succeeded=success, query_complete=complete)
        assert result.complete is False
        assert result.flat_verified is result.leverage_verified is result.protection_verified is False
