"""Real SQLite, owner loop, fake native boundary; no venue or provider network."""

import asyncio
import json
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from typing import Any

import pytest
from test_bybit_native import FakeFactory, FakeStrategy
from test_risk import NOW, inputs
from test_runtime import SCOPE, selection
from worker_receipt_support import SyntheticSession, synthetic_session

from multi_exchange_trader.execution import codec
from multi_exchange_trader.execution.bybit_native import NativeBindings, NativeBybitBridge
from multi_exchange_trader.execution.execution_provenance import FillQuarantineRecord
from multi_exchange_trader.execution.journal import CommandState
from multi_exchange_trader.execution.modes import AutomaticMode, select_all_in_one
from multi_exchange_trader.execution.native_events import NativeEventDecoder
from multi_exchange_trader.execution.native_node import NativeChannel, NativeNodeAssembly
from multi_exchange_trader.execution.protection import ProtectionPolicy
from multi_exchange_trader.execution.risk import assess_entry
from multi_exchange_trader.execution.runtime import RuntimeBlocked
from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook
from multi_exchange_trader.execution.worker import (
    ExecutionWorker,
    RegisteredPlan,
    TrustedPlanRegistry,
)


def fresh_assembly() -> NativeNodeAssembly:
    strategy = FakeStrategy()
    bridge = NativeBybitBridge(scope=SCOPE, native_account_id="BYBIT-UNIFIED",
        factory=FakeFactory(), strategy=strategy, bindings=NativeBindings(str, str, str, str, str))
    return NativeNodeAssembly(SimpleNamespace(), strategy, object(), bridge,
                              NativeChannel(SCOPE, "BYBIT-UNIFIED"))


def harness(tmp_path: Path, *, mode: str = "FULL_AUTO",
            lock_directory: Path | None = None, core: SyntheticSession | None = None) -> Any:
    book = RuntimePolicyBook(tmp_path / "runtime-policy.sqlite3")
    data = inputs()
    draft = book.read()["policy"]
    for key, value in asdict(data["policy"]).items():
        if key in draft:
            draft[key] = str(value) if isinstance(value, Decimal) else value
    draft.update(capital_usdt="1000", mode=mode, environment="DEMO",
        allowed_symbols=["BTCUSDT"], selected_symbols=["BTCUSDT"],
        allowed_strategies=["strategy-1"], selected_strategies=["strategy-1"])
    book.save(0, draft)
    data["policy"] = book.risk_policy(data["policy"].scope)
    plan = assess_entry(**data)
    request = selection()
    request = replace(request, policy=replace(request.policy, revision="1"),
        candidates=tuple(replace(c, policy_revision="1") for c in request.candidates))
    protection = ProtectionPolicy("1", 0, Decimal(90), Decimal(120), "MarkPrice")
    registry = TrustedPlanRegistry((RegisteredPlan("plan-1", request.candidates[0].strategy,
                                                  plan, protection),))
    assembly = fresh_assembly()
    core = synthetic_session(SCOPE, NOW) if core is None else core
    session, binding = core.session, core.binding
    worker = ExecutionWorker(directory=tmp_path, scope=SCOPE, session=session, binding=binding,
        symbols=("BTCUSDT",), trader_id="WORKER-001", policy_book=book, registry=registry,
        entry_risk=lambda _plan, _now, _reservations: plan, clock=lambda: core.wall[0],
        max_age=timedelta(seconds=10), assembly_factory=lambda _sink: assembly,
        decoder=NativeEventDecoder(event_types={SimpleNamespace: "OrderFilled"}),
        lock_directory=lock_directory)
    return SimpleNamespace(worker=worker, request=request, assembly=assembly, session=session,
                           binding=binding, core=core, book=book, registry=registry)


async def stage(h: Any) -> Any:
    return await h.worker.stage(command_key="entry", position_id="position-1",
        mode=AutomaticMode.FULL_AUTO, request=h.request)


def fill(client_id: str) -> SimpleNamespace:
    return SimpleNamespace(account_id="BYBIT-UNIFIED", client_order_id=client_id,
        instrument_id="BTCUSDT-LINEAR.BYBIT", venue_order_id="venue-1",
        ts_event=int(NOW.timestamp()) * 10**9, ts_init=int(NOW.timestamp()) * 10**9,
        order_side="BUY", reconciliation=False, trade_id="fill-1", last_qty=Decimal("0.4"), last_px=Decimal(100),
        commission=SimpleNamespace(as_decimal=lambda: Decimal("0.01"), currency="USDT"))


def quarantines(worker: Any) -> tuple[FillQuarantineRecord, ...]:
    return tuple(value for item in worker.runtime.store.list_position_events("position-1")
                 if type(value := codec.decode(item.payload_json)) is FillQuarantineRecord)


async def test_passive_start_and_stage_never_activate_or_send(tmp_path: Path) -> None:
    h = harness(tmp_path)
    assert not (tmp_path / "execution.sqlite3").exists()
    await h.worker.start_passive()
    record = await stage(h)
    assert record.state is CommandState.PENDING
    with pytest.raises(RuntimeBlocked, match="QUALIFICATION"):
        h.worker.runtime.start()
    assert h.book.read()["desired_state"] == "STOPPED"
    assert not h.assembly.strategy.commands
    await h.worker.stop()


async def test_foreign_thread_fill_survives_expired_metadata_and_restart(tmp_path: Path) -> None:
    h = harness(tmp_path)
    await h.worker.start_passive()
    record = await stage(h)
    h.worker.runtime.journal.claim("entry")  # Simulate an interrupted native handoff.

    def stale(*_args: Any, **_kwargs: Any) -> None:
        raise ValueError("ACCOUNT_BINDING_STALE")

    h.session.require_binding = stale
    thread = Thread(target=h.worker.post_native, args=(h.assembly.channel, fill(record.client_order_id)))
    thread.start()
    thread.join()
    await asyncio.sleep(0)
    assert h.worker.callback_failures == 0
    assert h.worker.runtime.projection("position-1").owned_quantity == 0
    assert len(quarantines(h.worker)) == 1
    assert h.worker.runtime.journal.get("entry").state is CommandState.SENDING
    with pytest.raises(ValueError, match="STALE"):
        await stage(h)
    await h.worker.stop()
    restarted = harness_existing(tmp_path, h)
    await restarted.start_passive()
    assert restarted.runtime.projection("position-1").owned_quantity == 0
    assert len(quarantines(restarted)) == 1
    assert restarted.runtime.projection("position-1").entries_blocked
    restarted.post_native(h.assembly.channel, fill(record.client_order_id))
    await asyncio.sleep(0)
    assert restarted.rejected_channels == 1
    assert not restarted.runtime.bridge._submissions
    await restarted.stop()


def harness_existing(tmp_path: Path, h: Any) -> ExecutionWorker:
    assembly = replace(h.assembly, channel=NativeChannel(SCOPE, "BYBIT-UNIFIED"))
    return ExecutionWorker(directory=tmp_path, scope=SCOPE, session=h.session, binding=h.binding,
        symbols=("BTCUSDT",), trader_id="WORKER-001", policy_book=h.book, registry=h.registry,
        entry_risk=h.worker.entry_risk, clock=lambda: NOW, max_age=timedelta(seconds=10),
        assembly_factory=lambda _sink: assembly)


async def test_equal_channel_copy_is_rejected_and_unknown_order_does_not_crash(tmp_path: Path) -> None:
    h = harness(tmp_path)
    await h.worker.start_passive()
    h.worker.post_native(replace(h.assembly.channel), fill("ec_" + "0" * 32))
    h.worker.post_native(h.assembly.channel, fill("ec_" + "0" * 32))
    await asyncio.sleep(0)
    assert h.worker.rejected_channels == h.worker.callback_failures == 1
    assert h.worker.runtime.status.entries_paused
    await h.worker.stop()


async def test_stale_policy_and_unregistered_strategy_block_before_staging(tmp_path: Path) -> None:
    h = harness(tmp_path)
    await h.worker.start_passive()
    h.worker.registry = TrustedPlanRegistry(())
    with pytest.raises(RuntimeBlocked, match="APPROVED"):
        await stage(h)
    h.worker.registry = h.registry
    h.book.save(1, h.book.read()["policy"])
    with pytest.raises(RuntimeBlocked, match="CURRENT_STOPPED"):
        await stage(h)
    assert h.worker.runtime.journal.list_commands() == ()
    await h.worker.stop()


async def test_all_in_one_rechecks_policy_after_dispatch_and_no_fallback(tmp_path: Path) -> None:
    h = harness(tmp_path, mode="ALL_IN_ONE")
    await h.worker.start_passive()

    def dispatch(*_args: Any, **_kwargs: Any) -> Any:
        h.book.save(1, h.book.read()["policy"])
        return SimpleNamespace(failure=None, selection=select_all_in_one(
            request=h.request, response=None, now=NOW))

    with pytest.raises(RuntimeBlocked, match="CURRENT_STOPPED"):
        await h.worker.stage(command_key="entry", position_id="position-1",
            mode=AutomaticMode.ALL_IN_ONE, request=h.request,
            dispatcher=SimpleNamespace(dispatch=dispatch), quote=object(), maximum_cost_usd="1")
    assert h.worker.runtime.journal.list_commands() == ()
    await h.worker.stop()


async def test_owner_loop_enforced_and_sending_recovers_without_replay(tmp_path: Path) -> None:
    h = harness(tmp_path)
    await h.worker.start_passive()
    await stage(h)
    h.worker.runtime.journal.claim("entry")

    async def wrong_loop() -> None:
        with pytest.raises(RuntimeBlocked, match="OWNER_LOOP"):
            _ = h.worker.runtime

    await asyncio.to_thread(lambda: asyncio.run(wrong_loop()))
    await h.worker.stop()
    restarted = harness_existing(tmp_path, h)
    await restarted.start_passive()
    assert restarted.runtime.journal.get("entry").state is CommandState.UNKNOWN
    assert not h.assembly.strategy.commands
    await restarted.stop()


async def test_all_in_one_uses_only_registered_candidate_plan(tmp_path: Path) -> None:
    h = harness(tmp_path, mode="ALL_IN_ONE")
    await h.worker.start_passive()
    response = json.dumps({"schema_version": 1, "request_id": h.request.request_id,
        "snapshot_id": h.request.snapshot.snapshot_id, "policy_revision": "1",
        "decision": "SELECT", "candidate_id": "candidate-1", "reason": "fixture"})

    def dispatch(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(failure=None, selection=select_all_in_one(
            request=h.request, response=response, now=NOW))

    record = await h.worker.stage(command_key="entry", position_id="position-1",
        mode=AutomaticMode.ALL_IN_ONE, request=h.request,
        dispatcher=SimpleNamespace(dispatch=dispatch), quote=object(), maximum_cost_usd="1")
    assert record.state is CommandState.PENDING
    assert json.loads(record.payload_json)["plan_id"] == "plan-1"
    assert not h.assembly.strategy.commands
    await h.worker.stop()


async def test_paid_dispatch_does_not_block_owned_fill_callbacks(tmp_path: Path) -> None:
    h = harness(tmp_path, mode="ALL_IN_ONE")
    await h.worker.start_passive()
    registered = h.registry.resolve(h.request.candidates[0])
    record = h.worker.runtime.queue_entry("prior", "position-1", plan_id="plan-1",
        plan=registered.plan, protection_policy=registered.protection,
        mode=AutomaticMode.FULL_AUTO, selection_request=h.request)
    h.worker.runtime.journal.claim("prior")
    entered, release = Event(), Event()

    def dispatch(*_args: Any, **_kwargs: Any) -> Any:
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(failure="PROVIDER_UNAVAILABLE", selection=None)

    task = asyncio.create_task(h.worker.stage(command_key="next", position_id="position-2",
        mode=AutomaticMode.ALL_IN_ONE, request=h.request,
        dispatcher=SimpleNamespace(dispatch=dispatch), quote=object(), maximum_cost_usd="1"))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        h.worker.post_native(h.assembly.channel, fill(record.client_order_id))
        await asyncio.sleep(0)
        assert h.worker.runtime.projection("position-1").owned_quantity == 0
        assert len(quarantines(h.worker)) == 1
        with pytest.raises(RuntimeBlocked, match="INFLIGHT_SELECTION"):
            await h.worker.stop()
    finally:
        release.set()
        assert await task is None
        await h.worker.stop()
