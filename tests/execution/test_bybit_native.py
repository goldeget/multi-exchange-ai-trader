"""Portable contracts: explicit fake native boundaries, never a connected node."""

from __future__ import annotations

import importlib
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from multi_exchange_trader.execution.journal import CommandJournal, CommandKind, ExecutionScope
from multi_exchange_trader.execution.protection import OwnedOrder, ProtectionScope

SCOPE = ProtectionScope("fixture-account", "DEMO")
CLIENT_ID = "ec_0123456789abcdef0123456789abcdef"
INSTRUMENT = "BTCUSDT-LINEAR.BYBIT"


def api() -> Any:
    name = "multi_exchange_trader.execution.bybit_native"
    assert importlib.util.find_spec(name) is not None, "native command bridge is missing"
    return importlib.import_module(name)


def events_api() -> Any:
    name = "multi_exchange_trader.execution.native_events"
    assert importlib.util.find_spec(name) is not None, "native event bridge is missing"
    return importlib.import_module(name)


class FakeFactory:
    """Matches the native keyword signature; rejects factory-level params."""

    def __init__(self) -> None:
        self.orders: list[SimpleNamespace] = []

    def market(self, *, instrument_id: object, order_side: object, quantity: object,
               client_order_id: object, reduce_only: bool) -> object:
        order = SimpleNamespace(instrument_id=instrument_id, side=order_side,
            quantity=quantity, client_order_id=client_order_id, is_reduce_only=reduce_only,
            price=None)
        self.orders.append(order)
        return order

    def limit(self, *, instrument_id: object, order_side: object, quantity: object,
              client_order_id: object, reduce_only: bool, price: object) -> object:
        order = self.market(instrument_id=instrument_id, order_side=order_side,
            quantity=quantity, client_order_id=client_order_id, reduce_only=reduce_only)
        order.price = price  # type: ignore[attr-defined]
        return order


class FakeStrategy:
    def __init__(self, *, fail: bool = False) -> None:
        self.commands: list[tuple[object, dict[str, object]]] = []
        self.fail = fail

    def submit_order(self, order: object, *, params: dict[str, object]) -> None:
        self.commands.append((order, params))
        if self.fail:
            raise TimeoutError("UNTRUSTED_PROVIDER_TEXT_MUST_NOT_ESCAPE")


def bindings() -> Any:
    return api().NativeBindings(instrument_id=str, client_order_id=str, quantity=str,
        price=str, order_side=str)


def request(**changes: object) -> Any:
    native = api()
    owned = OwnedOrder(CLIENT_ID, "position-1", INSTRUMENT, "BUY", "ENTRY",
        Decimal("0.010"), False, 0, "policy-1")
    result = native.NativeOrderRequest(SCOPE, owned, "LIMIT", Decimal(50000),
        limit_price=Decimal("50000.0"), attached=native.AttachedProtection(
            Decimal(49000), Decimal(52000), "MarkPrice"))
    return replace(result, **changes)


def bridge(strategy: FakeStrategy | None = None) -> Any:
    return api().NativeBybitBridge(scope=SCOPE, native_account_id="BYBIT-UNIFIED",
        factory=FakeFactory(), strategy=strategy or FakeStrategy(), bindings=bindings())


def context(**changes: object) -> Any:
    result = events_api().NativeEventContext(SCOPE, "BYBIT-UNIFIED", 1700000000000000900)
    return replace(result, **changes)


def native_event(name: str, **changes: object) -> object:
    # Fakes are explicit; decoder receives the exact installed native type registry in Linux tests.
    fields: dict[str, object] = {"account_id": "BYBIT-UNIFIED", "client_order_id": CLIENT_ID,
        "instrument_id": INSTRUMENT, "ts_event": 1700000000000000100,
        "ts_init": 1700000000000000200, "venue_order_id": "venue-1"}
    fields.update(changes)
    return SimpleNamespace(**fields)


def decoder() -> Any:
    return events_api().NativeEventDecoder(event_types={SimpleNamespace: "OrderFilled"})


def test_factory_and_strategy_keep_exact_durable_identity_and_tp_sl_placement(tmp_path: Path) -> None:
    native = api()
    with CommandJournal(tmp_path / "intent.sqlite", ExecutionScope(SCOPE.account_id, "DEMO")) as db:
        record = db.register("entry-a", CommandKind.ENTRY, {"quantity": "0.010"})
    with CommandJournal(tmp_path / "intent.sqlite", ExecutionScope(SCOPE.account_id, "DEMO")) as db:
        assert db.get("entry-a").client_order_id == record.client_order_id
    strategy = FakeStrategy()
    instance = bridge(strategy)
    submitted = instance.submit(replace(request(), owned=replace(request().owned,
        client_order_id=record.client_order_id)))
    assert submitted.state == "SUBMITTING"
    order, params = strategy.commands[0]
    assert len(order.client_order_id) == 35
    assert order.client_order_id == record.client_order_id
    assert (order.instrument_id, order.side, order.quantity, order.price) == (
        INSTRUMENT, "BUY", "0.010", "50000.0")
    assert params == {"position_idx": 0, "take_profit": "52000", "stop_loss": "49000",
        "tp_trigger_by": "MarkPrice", "sl_trigger_by": "MarkPrice",
        "tp_order_type": "Market", "sl_order_type": "Market", "tpsl_mode": "Full"}
    assert native.NativeBybitBridge.production_supported is False


def test_ambiguous_submit_is_unknown_and_never_replayed_or_fake_acknowledged() -> None:
    strategy = FakeStrategy(fail=True)
    instance = bridge(strategy)
    result = instance.submit(request())
    assert result.state == "UNKNOWN"
    assert result.code == "NATIVE_SUBMIT_AMBIGUOUS"
    assert "UNTRUSTED" not in repr(result)
    assert instance.submit(request()) == result
    assert len(strategy.commands) == 1
    with pytest.raises(ValueError, match="IMMUTABLE_NATIVE_COMMAND_CONFLICT"):
        instance.submit(replace(request(), reference_price=Decimal(50001)))


@pytest.mark.parametrize("changes", [
    {"scope": ProtectionScope("fixture-account", "LIVE")},
    {"order_type": "STOP_MARKET"}, {"position_idx": 1}, {"position_idx": False},
    {"tp_trigger_price": Decimal(52000)}, {"sl_trigger_price": Decimal(49000)},
    {"limit_price": None}, {"reference_price": Decimal("NaN")},
    {"limit_price": Decimal("1e1001")},
])
def test_unsupported_demo_contract_is_rejected_before_any_command(changes: dict[str, object]) -> None:
    strategy = FakeStrategy()
    with pytest.raises(ValueError):
        bridge(strategy).submit(request(**changes))
    assert not strategy.commands


def test_invalid_owned_mapping_and_attached_exit_never_reach_strategy() -> None:
    strategy = FakeStrategy()
    for changes in [{"client_order_id": "ec_prefix_only"},
                    {"instrument_id": "BTCUSDT.BYBIT"},
                    {"role": "EXIT", "reduce_only": True},
                    {"quantity": Decimal("1e1001")}]:
        with pytest.raises(ValueError):
            owned = replace(request().owned, **changes)
            bridge(strategy).submit(request(owned=owned))
    assert not strategy.commands


@pytest.mark.parametrize("stop,tp,trigger", [
    ("51000", "52000", "MarkPrice"), ("49000", "48000", "MarkPrice"),
    ("49000", "52000", "Unrecognized"), ("0", "52000", "LastPrice"),
])
def test_protection_direction_and_trigger_are_validated(stop: str, tp: str, trigger: str) -> None:
    with pytest.raises(ValueError):
        attached = api().AttachedProtection(Decimal(stop), Decimal(tp), trigger)
        bridge().submit(request(attached=attached))


def test_reduce_only_market_exit_has_no_attached_protection() -> None:
    strategy = FakeStrategy()
    owned = replace(request().owned, role="EXIT", side="SELL", reduce_only=True)
    result = bridge(strategy).submit(request(owned=owned, order_type="MARKET",
        limit_price=None, attached=None))
    assert result.state == "SUBMITTING"
    assert strategy.commands[0][1] == {"position_idx": 0}
    assert strategy.commands[0][0].is_reduce_only is True


def test_distinct_event_kinds_and_fill_details_preserve_nanosecond_timestamps() -> None:
    instance = bridge()
    instance.submit(request())
    for native_kind, want in [("OrderSubmitted", "SUBMITTED"), ("OrderAccepted", "ACCEPTED"),
                              ("OrderRejected", "REJECTED"), ("OrderCanceled", "CANCELED")]:
        decode = events_api().NativeEventDecoder(event_types={SimpleNamespace: native_kind})
        evidence = instance.consume(native_event(native_kind), context(), decoder=decode)
        assert evidence.kind == want
        assert evidence.fill is None
        assert (evidence.ts_event_ns, evidence.ts_init_ns, evidence.received_at_ns) == (
            1700000000000000100, 1700000000000000200, 1700000000000000900)
    filled = native_event("OrderFilled", trade_id="exec-1", order_side="BUY",
        last_qty=Decimal("0.004"), last_px=Decimal("50001.5"),
        commission=SimpleNamespace(as_decimal=lambda: Decimal("0.1"), currency="USDT"))
    evidence = instance.consume(filled, context(), decoder=decoder())
    assert evidence.kind == "FILL"
    assert (evidence.fill.execution_id, evidence.fill.quantity, evidence.fill.price,
            evidence.fill.commission, evidence.fill.commission_currency) == (
        "exec-1", Decimal("0.004"), Decimal("50001.5"), Decimal("0.1"), "USDT")
    assert evidence.fill.to_owned_fill().client_order_id == CLIENT_ID
    assert evidence.fill.to_owned_fill().quantity == Decimal("0.004")
    assert not evidence.terminal_confirmed


@pytest.mark.parametrize("event_changes,context_changes", [
    ({"client_order_id": "ec_0123456789abcdef0123456789abcdee"}, {}),
    ({"account_id": "OTHER-UNIFIED"}, {}), ({"instrument_id": "ETHUSDT-LINEAR.BYBIT"}, {}),
    ({}, {"scope": ProtectionScope("other-account", "DEMO")}),
    ({}, {"scope": ProtectionScope("fixture-account", "LIVE")}),
    ({}, {"native_account_id": "OTHER-UNIFIED"}),
    ({"order_side": "SELL"}, {}),
])
def test_event_needs_exact_owned_mapping_and_explicit_scoped_context(
    event_changes: dict[str, object], context_changes: dict[str, object],
) -> None:
    instance = bridge()
    instance.submit(request())
    fields = {"trade_id": "exec-1", "order_side": "BUY", "last_qty": Decimal("0.004"),
        "last_px": Decimal(50000), "commission": SimpleNamespace(
            as_decimal=lambda: Decimal("0.1"), currency="USDT")}
    fields.update(event_changes)
    with pytest.raises(ValueError, match="NATIVE_EVENT_"):
        instance.consume(native_event("OrderFilled", **fields), context(**context_changes),
            decoder=decoder())


def test_unrecognized_empty_error_partial_and_position_reports_never_prove_flat_or_protection() -> None:
    instance = bridge()
    instance.submit(request())
    decode = events_api().NativeEventDecoder(event_types={SimpleNamespace: "PositionStatusReport"})
    for reports, succeeded, complete in [((), True, True), ((), False, True),
            ((native_event("PositionStatusReport"),), True, False),
            ((native_event("PositionStatusReport"),), True, True)]:
        batch = instance.consume_reports(reports, context(), query_succeeded=succeeded,
            query_complete=complete, decoder=decode)
        assert batch.complete is False
        assert batch.flat_verified is batch.protection_verified is batch.leverage_verified is False


def test_status_report_filled_is_not_an_execution_and_reject_reason_is_not_copied() -> None:
    instance = bridge()
    instance.submit(request())
    decode = events_api().NativeEventDecoder(event_types={SimpleNamespace: "OrderStatusReport"})
    report = native_event("OrderStatusReport", order_status="FILLED", order_side="BUY",
        quantity=Decimal("0.010"), filled_qty=Decimal("0.010"),
        ts_last=1700000000000000100, reject_reason="UNTRUSTED_SECRET_TEXT")
    result = instance.consume(report, context(), decoder=decode)
    assert result.kind == "STATUS"
    assert result.native_status == "FILLED"
    assert result.fill is None
    assert not result.terminal_confirmed
    assert "UNTRUSTED" not in repr(result)


@pytest.mark.parametrize("field,converted", [
    ("quantity", "0.011"), ("price", "50000.1"),
    ("client_order_id", "ec_ffffffffffffffffffffffffffffffff"),
    ("instrument_id", "ETHUSDT-LINEAR.BYBIT"), ("order_side", "SELL"),
])
def test_native_conversion_cannot_silently_change_the_immutable_order(
    field: str, converted: str,
) -> None:
    strategy = FakeStrategy()
    constructors = replace(bindings(), **{field: lambda _value: converted})
    instance = api().NativeBybitBridge(scope=SCOPE, native_account_id="BYBIT-UNIFIED",
        factory=FakeFactory(), strategy=strategy, bindings=constructors)
    with pytest.raises(ValueError, match="NATIVE_ORDER_BUILD_FAILED"):
        instance.submit(request())
    assert not strategy.commands


def test_portable_import_never_imports_native_dependency_or_loads_environment() -> None:
    script = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    assert not name.startswith('nautilus_trader'), 'native import on portable path'
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import multi_exchange_trader.execution.bybit_native
import multi_exchange_trader.execution.native_events
"""
    result = subprocess.run([sys.executable, "-c", script], check=False,
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("retry", [0, -1, True, 1.5, 11])
def test_invalid_retry_is_refused_before_loading_native_or_credential_fallback(retry: object) -> None:
    with pytest.raises(ValueError, match="EXPLICIT_NATIVE_CREDENTIALS_AND_POSITIVE_RETRY_REQUIRED"):
        api().build_demo_exec_config(api_key="fixture-only", api_secret="fixture-only",
            max_retries=retry)


def test_untrusted_native_identity_exception_is_sanitized() -> None:
    class BrokenNativeIdentity:
        @property
        def client_order_id(self) -> str:
            raise RuntimeError("UNTRUSTED_NATIVE_IDENTITY_TEXT")

    with pytest.raises(ValueError, match="NATIVE_EVENT_INVALID_OR_UNOWNED") as caught:
        bridge().consume(BrokenNativeIdentity(), context())
    assert "UNTRUSTED" not in str(caught.value)


@pytest.mark.parametrize("installed_version", ["1.230.0", "1.232.0"])
def test_standalone_event_decoder_rejects_unpinned_version_before_native_import(
    monkeypatch: pytest.MonkeyPatch, installed_version: str,
) -> None:
    events = events_api()
    monkeypatch.setattr(events, "version", lambda _package: installed_version, raising=False)

    def forbidden_import(_name: str) -> object:
        raise AssertionError("native import occurred before version rejection")

    monkeypatch.setattr(events.importlib, "import_module", forbidden_import)
    with pytest.raises(ValueError, match="PINNED_NATIVE_VERSION_REQUIRED"):
        events.load_native_event_decoder()
