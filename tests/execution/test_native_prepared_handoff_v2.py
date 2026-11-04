"""Tainted portable structure only; no native, source, clock or financial qualification."""
from __future__ import annotations

import copy
import sys
import xml.etree.ElementTree as ET
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from test_bybit_fixed_limit import (
    ID,
    Factory,
    Init,
    Kind,
    Limit,
    Tif,
    Updated,
    legacy,
    request,
    setup,
)

from multi_exchange_trader.execution import bybit_native as n
from multi_exchange_trader.execution import native_node
from multi_exchange_trader.execution.account_binding import (
    AuthenticatedAccountBinding,
    BybitDemoAccountSession,
)
from multi_exchange_trader.execution.native_restart import NativeRestartCoordinator


class Cache:
    def __init__(self) -> None:
        self.values: dict[object, Any] = {}

    def order(self, key: object) -> Any:
        return self.values.get(key)

    def orders(self) -> list[Any]:
        return list(self.values.values())


class Bus:
    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}

    def subscribe(self, *, topic: str, handler: Any) -> None:
        self.handlers.setdefault(topic, []).append(handler)

    def unsubscribe(self, *, topic: str, handler: Any) -> None:
        self.handlers[topic].remove(handler)

    def publish(self, topic: str, event: object) -> None:
        for handler in tuple(self.handlers.get(topic, ())):
            handler(event)


class Strategy:
    cache: Cache
    msgbus: Bus

    def __init__(self, *, config: object) -> None:
        self.config = config
        self.id, self.trader_id = "STRATEGY-001", "TRADER-001"
        self.clock = object()  # A component clock, distinct from kernel.clock.
        self.order_factory = Factory()
        self.commands: list[Any] = []
        self.fail: BaseException | None = None
        self.entry_observer: Any = None

    def submit_order(self, order: Any, position_id: object, client_id: object,
                     params: dict[str, object]) -> None:
        if self.entry_observer is not None:
            self.entry_observer()
        self.commands.append((order, position_id, client_id, params))
        self.cache.values[order.client_order_id] = order
        self.msgbus.publish("events.order." + self.id, order.events[0])
        if self.fail is not None:
            raise self.fail

    def cancel_order(self, order: object, *, params: dict[str, object]) -> None:
        self.commands.append(("cancel", order, params))


class Client:
    account_id = "BYBIT-UNIFIED"

    def note_monitoring_native(self, event: object) -> None:
        raise AssertionError("initialized seed is not a monitoring execution")


class AdapterFactory:
    @staticmethod
    def create(**kwargs: Any) -> Client:
        return Client()


class Node:
    def __init__(self, *, config: object, loop: object) -> None:
        cache, bus = Cache(), Bus()
        self.cache = cache
        self.kernel = SimpleNamespace(cache=cache, msgbus=bus, clock=object(), executor=None,
                                      loop=loop, dispose=lambda: None)
        self.strategies: list[Strategy] = []

        def add(strategy: Any) -> None:
            strategy.cache, strategy.msgbus = cache, bus
            self.strategies.append(strategy)

        self.trader = self.kernel.trader = SimpleNamespace(add_strategy=add,
            strategies=lambda: list(self.strategies))

    def add_exec_client_factory(self, venue: str, factory: Any) -> None:
        self.factory = factory

    def build(self) -> None:
        self.factory.create()


async def built(monkeypatch: pytest.MonkeyPatch, *, monitor: bool = False) -> Any:
    """Actual builder, explicitly fake SDK/configuration provenance; never qualification."""
    session = BybitDemoAccountSession(api_key="fixture", api_secret="fixture")
    modules = {
        "node": SimpleNamespace(TradingNode=Node), "strategy": SimpleNamespace(Strategy=Strategy),
        "config": SimpleNamespace(StrategyConfig=lambda **kwargs: SimpleNamespace(**kwargs)),
        "factories": SimpleNamespace(BybitLiveExecClientFactory=AdapterFactory),
        "order_factories": SimpleNamespace(OrderFactory=Factory),
        "bybit_execution": SimpleNamespace(BybitExecutionClient=Client),
        "events": SimpleNamespace(**{name: type(name, (), {}) for name in (
            "OrderSubmitted", "OrderAccepted", "OrderDenied", "OrderRejected", "OrderCanceled",
            "OrderExpired", "OrderFilled")}),
    }
    monkeypatch.setattr(native_node, "_native_modules", lambda: modules)
    monkeypatch.setattr(native_node, "build_node_config", lambda *args, **kwargs: object())
    monkeypatch.setattr(native_node, "load_native_bindings", lambda **kwargs: n.NativeBindings(
        str, str, str, str, str, n.FixedLimitBindingsV2(Limit, Init, Updated, Kind.LIMIT, Tif.IOC)))

    def install(binding: object, *, scope: object, install: Any) -> None:
        install(object())

    monkeypatch.setattr(session, "configure_native_execution", install)
    monkeypatch.setattr(session, "configure_native_monitoring", install)
    if monitor:
        fake = ModuleType("multi_exchange_trader.execution.native_monitor_client")
        fake.create_monitor_client = lambda **kwargs: Client()  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, fake.__name__, fake)
    try:
        assembly = native_node.build_native_node(session=session,
            binding=cast(AuthenticatedAccountBinding, object()),
            scope=request().scope, symbols=("XAAUSDT",), trader_id="TRADER-001",
            on_event=lambda channel, event: None, monitor_only=monitor)
    except BaseException:
        await session.aclose()
        raise
    return SimpleNamespace(assembly=assembly, bridge=assembly.bridge, session=session,
        factory=assembly.strategy.order_factory, strategy=assembly.strategy)


async def close(h: Any) -> None:
    try:
        h.assembly.dispose_local()
    finally:
        await h.session.aclose()


def test_uninstalled_bridge_cannot_prepare_or_read_a_caller_request() -> None:
    bridge, factory, strategy = setup()
    with pytest.raises(ValueError, match="NATIVE_PREPARED_INSTALLATION_REQUIRED"), bridge._prepare_fixed_limit_handoff_v2(object()):
        pytest.fail("a caller-created bridge must not issue prepared data")
    assert factory.calls == [] and strategy.submits == [] and strategy.cancels == []


async def test_builder_preparation_is_private_and_one_original_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    h = await built(monkeypatch)
    events: list[Any] = []
    h.strategy.msgbus.subscribe(topic="events.order." + h.strategy.id, handler=events.append)
    try:
        with h.bridge._prepare_fixed_limit_handoff_v2(request()) as data:
            assert h.strategy.cache.orders() == events == h.strategy.commands == []
            order, seed = data.order, data.seed
            h.bridge._seal_prepared_fixed_limit_v2(data)
            assert h.bridge._handoff_prepared_fixed_limit_v2(data).state == "SUBMITTING"
            assert h.strategy.commands == [(order, None, None, data.params)]
            assert events == [seed] and h.strategy.cache.order(ID) is order
            with pytest.raises(ValueError):
                h.bridge._handoff_prepared_fixed_limit_v2(data)
        with pytest.raises(ValueError):
            h.bridge._handoff_prepared_fixed_limit_v2(data)
        assert len(h.strategy.commands) == 1 and h.bridge.production_supported is False
        with pytest.raises(ValueError), h.bridge._prepare_fixed_limit_handoff_v2(request()):
            pytest.fail("an invoked/cached key must never be prepared again")
    finally:
        await close(h)


@pytest.mark.parametrize("operation", ["submit", "fixed", "cancel", "register", "register_fixed"])
@pytest.mark.parametrize("when", ["factory", "subscriber"])
async def test_caught_cross_key_public_reentry_taints_outer_before_another_effect(
    monkeypatch: pytest.MonkeyPatch, operation: str, when: str,
) -> None:
    h = await built(monkeypatch)
    other = replace(request(), owned=replace(request().owned, client_order_id="ec_" + "f" * 32))

    def reenter(*args: Any) -> None:
        operations = {"submit": lambda: h.bridge.submit(legacy(other)),
            "fixed": lambda: h.bridge.submit_fixed_limit_v2(other),
            "cancel": lambda: h.bridge.cancel_owned(other.owned.client_order_id),
            "register": lambda: h.bridge.register_owned(legacy(other)),
            "register_fixed": lambda: h.bridge.register_fixed_limit_v2(other)}
        with pytest.raises(ValueError, match="NATIVE_PREPARED"):
            operations[operation]()

    if when == "factory":
        h.factory.callback = reenter
    else:
        h.strategy.msgbus.subscribe(topic="events.order." + h.strategy.id, handler=reenter)
    try:
        if when == "factory":
            with pytest.raises(ValueError, match="NATIVE_PREPARED"), h.bridge._prepare_fixed_limit_handoff_v2(request()):
                pytest.fail("caught factory reentry must taint preparation")
            assert not h.strategy.commands and not h.bridge._submissions
        else:
            with h.bridge._prepare_fixed_limit_handoff_v2(request()) as data:
                h.bridge._seal_prepared_fixed_limit_v2(data)
                assert h.bridge._handoff_prepared_fixed_limit_v2(data).state == "UNKNOWN"
            assert len(h.strategy.commands) == 1
            assert h.bridge._submissions[ID].state == "UNKNOWN"
            assert h.bridge.submit_fixed_limit_v2(request()) is h.bridge._submissions[ID]
            assert len(h.strategy.commands) == 1
        assert other.owned.client_order_id not in h.bridge._requests
    finally:
        await close(h)


@pytest.mark.parametrize("mutation", ["copy", "unsealed", "graph", "params", "map", "owner"])
async def test_seal_and_primitive_handoff_refuse_changed_originals_without_invoking(
    monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    h = await built(monkeypatch)
    try:
        with pytest.raises(ValueError), h.bridge._prepare_fixed_limit_handoff_v2(request()) as data:
            if mutation == "copy":
                h.bridge._seal_prepared_fixed_limit_v2(copy.copy(data))
            elif mutation == "unsealed":
                h.bridge._handoff_prepared_fixed_limit_v2(data)
            else:
                h.bridge._seal_prepared_fixed_limit_v2(data)
                if mutation == "graph":
                    object.__setattr__(data.request.owned, "quantity", Decimal(2))
                elif mutation == "params":
                    data.params["position_idx"] = 1
                elif mutation == "map":
                    h.bridge._requests = dict(h.bridge._requests)
                else:
                    h.assembly.strategy = object()
                h.bridge._handoff_prepared_fixed_limit_v2(data)
        assert h.strategy.commands == [] and h.bridge._submissions == {}
    finally:
        await close(h)


@pytest.mark.parametrize("error", [TimeoutError("private provider text"), KeyboardInterrupt()])
async def test_invocation_failure_keeps_unknown_and_never_rearms_private_installation(
    monkeypatch: pytest.MonkeyPatch, error: BaseException,
) -> None:
    h = await built(monkeypatch)
    h.strategy.fail = error
    try:
        with h.bridge._prepare_fixed_limit_handoff_v2(request()) as data:
            h.bridge._seal_prepared_fixed_limit_v2(data)
            if isinstance(error, Exception):
                assert h.bridge._handoff_prepared_fixed_limit_v2(data).state == "UNKNOWN"
            else:
                with pytest.raises(type(error)) as caught:
                    h.bridge._handoff_prepared_fixed_limit_v2(data)
                assert caught.value is error
        assert h.bridge._submissions[ID].state == "UNKNOWN" and len(h.strategy.commands) == 1
        with pytest.raises(ValueError), h.bridge._prepare_fixed_limit_handoff_v2(request()):
            pytest.fail("an ambiguous installation cannot be privately reused")
    finally:
        await close(h)


async def test_native_retirement_taints_before_cleanup_and_monitor_installation_cannot_prepare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await built(monkeypatch)
    try:
        with pytest.raises(ValueError), h.bridge._prepare_fixed_limit_handoff_v2(request()):
            with pytest.raises(ValueError):
                h.assembly.close_event_channel()
            assert not h.assembly.order_subscription._closed
        h.assembly.close_event_channel()
        with pytest.raises(ValueError), h.bridge._prepare_fixed_limit_handoff_v2(request()):
            pytest.fail("closed installation cannot prepare")
    finally:
        await close(h)
    monitor = await built(monkeypatch, monitor=True)
    try:
        with pytest.raises(ValueError, match="NATIVE_PREPARED_INSTALLATION_REQUIRED"), monitor.bridge._prepare_fixed_limit_handoff_v2(request()):
            pytest.fail("monitor-only installation cannot prepare")
    finally:
        await close(monitor)


async def test_body_failure_after_invocation_keeps_unknown_and_original_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await built(monkeypatch)
    marker = RuntimeError("fixture owner unwound after SDK invocation")
    try:
        with pytest.raises(RuntimeError) as caught, h.bridge._prepare_fixed_limit_handoff_v2(request()) as data:
            h.bridge._seal_prepared_fixed_limit_v2(data)
            h.bridge._handoff_prepared_fixed_limit_v2(data)
            raise marker
        assert caught.value is marker and len(h.strategy.commands) == 1
        assert h.bridge._submissions[ID].state == "UNKNOWN"
    finally:
        await close(h)


async def test_nested_prepare_and_restore_refuse_before_resolver_and_preserve_outer_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await built(monkeypatch)
    called: list[object] = []
    # Negative fixture only: no store is supplied because the first callback must never run.
    coordinator = object.__new__(NativeRestartCoordinator)
    coordinator.assembly = h.assembly

    def resolver(value: object) -> Any:
        called.append(value)
        raise AssertionError("restore resolver ran during private preparation")

    try:
        with pytest.raises(ValueError), h.bridge._prepare_fixed_limit_handoff_v2(request()):
            frame = h.bridge._active_preparation_v2
            with pytest.raises(ValueError), h.bridge._prepare_fixed_limit_handoff_v2(object()):
                pytest.fail("nested preparation must not yield")
            assert h.bridge._active_preparation_v2 is frame
        with pytest.raises(ValueError), h.bridge._prepare_fixed_limit_handoff_v2(request()):
            with pytest.raises(ValueError):
                coordinator.restore(resolver)
            assert called == []
        assert h.strategy.commands == [] and not h.bridge._submissions
    finally:
        await close(h)


@pytest.mark.parametrize("change", ["bridge", "installation", "assembly"])
async def test_copied_installation_owners_cannot_be_recaptured_as_original(
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    h = await built(monkeypatch)
    original = h.bridge._prepared_installation_v2
    owners = original.owners
    try:
        if change == "bridge":
            target = copy.copy(h.bridge)
        elif change == "installation":
            target = h.bridge
            target._prepared_installation_v2 = copy.copy(original)
        else:
            target = h.bridge
            original.owners = (copy.copy(h.assembly), *owners[1:])
        with pytest.raises(ValueError), target._prepare_fixed_limit_handoff_v2(request()):
            pytest.fail("copied ownership cannot become an original installation")
        assert h.strategy.commands == []
    finally:
        h.bridge._prepared_installation_v2 = original
        original.owners = owners
        await close(h)


async def test_after_seal_tail_does_not_call_native_or_full_recipe_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await built(monkeypatch)
    order_reads: list[str] = []
    opaque = False
    sdk_entered = False

    class OpaqueLimit(Limit):
        def __getattribute__(self, name: str) -> Any:
            if opaque and name != "__class__" and not sdk_entered:
                order_reads.append(name)
                raise AssertionError("native descriptor read before captured SDK entry")
            return super().__getattribute__(name)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("full proof or native helper called after seal")

    def enter() -> None:
        nonlocal sdk_entered
        sdk_entered = True

    h.strategy.entry_observer = enter
    h.factory.transform = lambda order: setattr(order, "__class__", OpaqueLimit)
    object.__setattr__(h.bridge._bindings.fixed_limit_v2, "limit_order_type", OpaqueLimit)

    try:
        with h.bridge._prepare_fixed_limit_handoff_v2(request()) as data:
            h.bridge._seal_prepared_fixed_limit_v2(data)
            opaque = True
            for name in ("_fixed_limit_identity_v2", "_verify_fixed_limit_order_v2", "_prepared_graph_matches_v2"):
                monkeypatch.setattr(n, name, forbidden)
            monkeypatch.setattr(n.NativeBybitBridge, "_association_fence_v2", forbidden)
            h.bridge._handoff_prepared_fixed_limit_v2(data)
        assert order_reads == [] and len(h.strategy.commands) == 1 and data.frame.layout == ()
    finally:
        await close(h)


@pytest.mark.parametrize("flag", ["sealed", "consumed", "closed", "frame-tainted", "frame-closed"])
async def test_tail_rejects_nonprimitive_phase_without_running_a_boolean_callback(
    monkeypatch: pytest.MonkeyPatch, flag: str,
) -> None:
    h = await built(monkeypatch)
    calls: list[str] = []

    class BooleanCallback:
        def __bool__(self) -> bool:
            calls.append("forbidden boolean callback")
            raise AssertionError("phase must use exact primitive is checks")

    try:
        with pytest.raises(ValueError), h.bridge._prepare_fixed_limit_handoff_v2(request()) as data:
            h.bridge._seal_prepared_fixed_limit_v2(data)
            owner, name = (data.frame, flag.removeprefix("frame-")) if flag.startswith("frame-") else (data, flag)
            setattr(owner, name, BooleanCallback())
            h.bridge._handoff_prepared_fixed_limit_v2(data)
        assert calls == [] and h.strategy.commands == [] and h.bridge._submissions == {}
    finally:
        await close(h)


@pytest.mark.parametrize("case", ["valid", "balanced", "negative", "unicode", "signed", "huge",
    "leading", "missing-tests", "missing-errors", "missing-failures", "missing-skipped",
    "errors", "failures", "skipped", "wrong-module", "duplicate", "unknown", "short",
    "nested-case", "nested-suite", "extra-root", "wrong-root", "nested-error", "nested-failure",
    "nested-skipped", "empty", "malformed", "absent"])
def test_owner_ci_gate_rejects_each_malformed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    workflow = Path(__file__).parents[2] / ".github/workflows/ci.yml"
    block = workflow.read_text(encoding="utf8").split(
        "      - name: Require all four exact prepared owner contracts\n", 1)[1]
    body = block.split("          .venv/bin/python - <<'PY'\n", 1)[1].split("          PY\n", 1)[0]
    code = "\n".join(line[10:] for line in body.splitlines()) + "\n"
    names = ["test_actual_entry_installation_prepares_a_private_order",
        "test_actual_prepared_handoff_uses_one_original_sdk_route",
        "test_actual_prepared_scope_rejects_cross_key_callback_reentry",
        "test_actual_prepared_owner_retirement_and_monitor_denial"]
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", tests="4", errors="0", failures="0", skipped="0")
    for name in names:
        ET.SubElement(suite, "testcase", name=name,
            classname="tests.execution.test_native_prepared_owner_linux")
    if case == "balanced":
        suite.set("errors", "-1"); suite.set("failures", "1")
    elif case in ("negative", "unicode", "signed", "huge", "leading"):
        suite.set("tests", {"negative": "-4", "unicode": "４", "signed": "+4", "huge": "4" * 1000, "leading": "04"}[case])
    elif case.startswith("missing-"):
        del suite.attrib[case.removeprefix("missing-")]
    elif case in ("errors", "failures", "skipped"):
        suite.set(case, "1")
    elif case == "wrong-module": suite[0].set("classname", "wrong")
    elif case == "duplicate": suite[1].set("name", names[0])
    elif case == "unknown": suite[0].set("name", "unknown")
    elif case == "short": suite.remove(suite[3])
    elif case == "nested-case": suite[0].append(suite[1]); suite.remove(suite[1])
    elif case == "nested-suite": ET.SubElement(suite, "testsuite", tests="0", errors="0", failures="0", skipped="0")
    elif case == "extra-root": ET.SubElement(root, "extra")
    elif case == "wrong-root": root.tag = "wrong"
    elif case.startswith("nested-"): ET.SubElement(suite[0], case.removeprefix("nested-"))
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "native-prepared-owner.xml"
    if case == "empty": target.write_text("", encoding="utf8")
    elif case == "malformed": target.write_text("<bad", encoding="utf8")
    elif case != "absent": ET.ElementTree(root).write(target)
    if case == "valid":
        exec(compile(code, str(workflow), "exec"), {})  # noqa: S102 - trusted local CI validator; synthetic XML only.
    else:
        with pytest.raises((SystemExit, ValueError, ET.ParseError, FileNotFoundError)):
            exec(compile(code, str(workflow), "exec"), {})  # noqa: S102 - trusted local CI validator; synthetic XML only.
