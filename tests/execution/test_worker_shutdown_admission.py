"""Async shutdown admission and liability ownership, with isolated fake effects."""

import asyncio
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest
from test_native_controls import controls
from test_runtime import queue, risk_check
from test_worker import fill, harness, quarantines

from multi_exchange_trader.execution import worker as worker_module
from multi_exchange_trader.execution.journal import CommandState
from multi_exchange_trader.execution.modes import AutomaticMode
from multi_exchange_trader.execution.native_controls import NativeLeverageControl
from multi_exchange_trader.execution.ownership import AccountWorkerLock, WorkerAlreadyRunning
from multi_exchange_trader.execution.runtime import RuntimeBlocked


def assert_lease_held(h: Any) -> None:
    other = AccountWorkerLock(h.worker.lock_directory, account_id=h.worker.scope.account_id,
                              environment="DEMO")
    with pytest.raises(WorkerAlreadyRunning):
        other.acquire()


async def test_stop_closes_paid_selection_admission_before_native_shutdown_await(
    tmp_path: Path,
) -> None:
    h = harness(tmp_path, mode="ALL_IN_ONE")
    h.core.permit_fake_monitoring()
    await h.worker.start_passive()
    entered, release, done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls: list[str] = []

    async def run() -> None:
        await done.wait()

    async def shutdown() -> None:
        entered.set()
        await release.wait()
        done.set()

    def dispatch(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("paid")
        return SimpleNamespace(failure="fixture", selection=None)

    h.assembly.node = SimpleNamespace(run_async=run, stop_async=shutdown)
    await h.worker.connect_monitoring()
    stopping = asyncio.create_task(h.worker.stop())
    await entered.wait()
    try:
        with pytest.raises(RuntimeBlocked, match="WORKER_STOPPING"):
            await h.worker.stage(command_key="late", position_id="position-1",
                mode=AutomaticMode.ALL_IN_ONE, request=h.request,
                dispatcher=SimpleNamespace(dispatch=dispatch), quote=object(), maximum_cost_usd="1")
        assert not calls
        assert_lease_held(h)
        for operation in (h.worker.apply_leverage("late-control", "entry"),
                          h.worker.connect_monitoring(), h.worker.poll_position("position-1"),
                          h.worker.read_history("BTCUSDT", start_ms=0, end_ms=1)):
            with pytest.raises(RuntimeBlocked, match="WORKER_STOPPING"):
                await operation
        with pytest.raises(RuntimeBlocked, match="STOP_ALREADY_RUNNING"):
            await h.worker.stop()
    finally:
        release.set()
        await stopping
    await h.worker.stop()  # Successful stop is idempotent on the owner loop.


@pytest.mark.parametrize("cancel", [False, True])
async def test_admitted_leverage_prevents_lease_release_until_accounting_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool,
) -> None:
    h = harness(tmp_path)
    h.core.permit_fake_monitoring()
    h.worker.entry_risk = risk_check
    await h.worker.start_passive()
    runtime = h.worker.runtime
    original = queue(runtime)
    monkeypatch.setattr(runtime, "_require_qualification", lambda: None)
    controller, adapter, _calls = controls(runtime, runtime.journal, monkeypatch)
    monkeypatch.setattr(worker_module, "NativeLeverageControl", lambda **_kwargs: controller)
    monkeypatch.setattr(h.worker, "_readback", lambda: adapter)
    entered, release = asyncio.Event(), asyncio.Event()

    async def effect(_self: Any, _symbol: str, _leverage: Any) -> None:
        entered.set()
        await release.wait()
        raise TimeoutError("fixture ambiguous effect")

    monkeypatch.setattr(NativeLeverageControl, "apply", effect)
    operation = asyncio.create_task(h.worker.apply_leverage("leverage", "entry"))
    await entered.wait()
    try:
        if cancel:
            operation.cancel()
            await asyncio.sleep(0)
            operation.cancel()
            await asyncio.sleep(0)
            assert not operation.done()
        with pytest.raises(RuntimeBlocked, match="INFLIGHT_OPERATION"):
            await h.worker.stop()
        assert not h.worker._stopped
        assert_lease_held(h)
        with pytest.raises(RuntimeBlocked, match="WORKER_STOPPING"):
            await h.worker.apply_leverage("late", "entry")
    finally:
        release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await operation
        else:
            with pytest.raises(RuntimeBlocked, match="AMBIGUOUS_OR_UNVERIFIED"):
                await operation
        if not h.worker._stopped:
            assert runtime.journal.get("leverage").state is CommandState.UNKNOWN
            assert runtime.journal.get("entry").reservation == original.reservation
            await h.worker.stop()


@pytest.mark.parametrize("cancel", [False, True])
async def test_busy_selection_stop_retains_callback_owner_until_paid_accounting_finishes(
    tmp_path: Path, cancel: bool,
) -> None:
    h = harness(tmp_path, mode="ALL_IN_ONE")
    await h.worker.start_passive()
    registered = h.registry.resolve(h.request.candidates[0])
    prior = h.worker.runtime.queue_entry("prior", "position-1", plan_id=registered.plan_id,
        plan=registered.plan, protection_policy=registered.protection,
        mode=AutomaticMode.FULL_AUTO, selection_request=h.request)
    h.worker.runtime.journal.claim("prior")
    entered, release, accounted = Event(), Event(), Event()

    def dispatch(*_args: Any, **_kwargs: Any) -> Any:
        entered.set()
        assert release.wait(5)
        accounted.set()
        return SimpleNamespace(failure="fixture", selection=None)

    operation = asyncio.create_task(h.worker.stage(command_key="next", position_id="position-2",
        mode=AutomaticMode.ALL_IN_ONE, request=h.request,
        dispatcher=SimpleNamespace(dispatch=dispatch), quote=object(), maximum_cost_usd="1"))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        if cancel:
            operation.cancel()
            await asyncio.sleep(0)
            operation.cancel()
            await asyncio.sleep(0)
            assert not operation.done()
        with pytest.raises(RuntimeBlocked, match="INFLIGHT_SELECTION"):
            await h.worker.stop()
        assert_lease_held(h)
        with pytest.raises(RuntimeBlocked, match="WORKER_STOPPING"):
            await h.worker.stage(command_key="late", position_id="position-3",
                mode=AutomaticMode.FULL_AUTO, request=h.request)
        h.worker.post_native(h.assembly.channel, fill(prior.client_order_id))
        await asyncio.sleep(0)
        assert h.worker.callback_failures == 0
        assert h.worker.runtime.projection("position-1").owned_quantity == 0
        assert len(quarantines(h.worker)) == 1
        with pytest.raises(RuntimeBlocked, match="INFLIGHT_SELECTION"):
            await h.worker.stop()
    finally:
        release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await operation
        else:
            assert await operation is None
        assert accounted.is_set()
        await h.worker.stop()
    other = AccountWorkerLock(tmp_path, account_id=h.worker.scope.account_id, environment="DEMO")
    other.acquire()
    other.release()


async def test_failed_or_cancelled_stop_keeps_admission_closed_and_allows_retry(
    tmp_path: Path,
) -> None:
    h = harness(tmp_path)
    h.core.permit_fake_monitoring()
    await h.worker.start_passive()
    entered, done = asyncio.Event(), asyncio.Event()

    async def run() -> None:
        await done.wait()

    async def waiting_stop() -> None:
        entered.set()
        await asyncio.Event().wait()

    h.assembly.node = SimpleNamespace(run_async=run, stop_async=waiting_stop)
    await h.worker.connect_monitoring()
    stopping = asyncio.create_task(h.worker.stop())
    await entered.wait()
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    assert not h.worker._stopped and not h.worker._channel_closed
    assert_lease_held(h)
    with pytest.raises(RuntimeBlocked, match="WORKER_STOPPING"):
        await h.worker.connect_monitoring()

    async def complete_stop() -> None:
        done.set()

    h.assembly.node.stop_async = complete_stop
    await h.worker.stop()


async def test_admitted_readback_retains_store_until_its_continuation_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = harness(tmp_path)
    await h.worker.start_passive()
    entered, release = asyncio.Event(), asyncio.Event()
    marker = object()

    async def readback(_adapter: object, _position_id: str) -> Any:
        entered.set()
        await release.wait()
        assert h.worker.runtime.status.entries_paused
        assert_lease_held(h)
        return marker

    monkeypatch.setattr(h.worker, "_readback", lambda: object())
    monkeypatch.setattr(h.worker.runtime, "poll_readback", readback)
    operation = asyncio.create_task(h.worker.poll_position("position-1"))
    await entered.wait()
    try:
        with pytest.raises(RuntimeBlocked, match="INFLIGHT_OPERATION"):
            await h.worker.stop()
        with pytest.raises(RuntimeBlocked, match="WORKER_STOPPING"):
            await h.worker.poll_position("position-1")
    finally:
        release.set()
        assert await operation is marker
        await h.worker.stop()
