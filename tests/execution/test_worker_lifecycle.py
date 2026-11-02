"""Shutdown keeps the callback owner alive until the native feed has drained."""

import asyncio
import signal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_worker import fill, harness, harness_existing, quarantines, stage

from multi_exchange_trader.execution import worker_cli
from multi_exchange_trader.execution.journal import CommandState
from multi_exchange_trader.execution.native_node import NativeOrderSubscription
from multi_exchange_trader.execution.ownership import AccountWorkerLock, WorkerAlreadyRunning


@pytest.mark.parametrize("connect", [False, True])
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
async def test_monitor_reclaims_handlers_after_native_construction(
    tmp_path: Path, connect: bool, sig: int,
) -> None:
    h, stop = harness(tmp_path), asyncio.Event()
    if connect:
        h.core.permit_fake_monitoring()
    handlers: dict[int, Any] = {}
    loop = SimpleNamespace(add_signal_handler=lambda sig, cb: handlers.update({sig: cb}))
    worker_cli.install_signal_handlers(loop, stop)

    def build(_sink: Any) -> Any:
        handlers.update({signal.SIGTERM: lambda: None, signal.SIGINT: lambda: None})
        return h.assembly

    h.worker._factory = build
    finished = asyncio.Event()

    async def run() -> None:
        await finished.wait()

    async def shutdown() -> None:
        finished.set()

    h.assembly.node = SimpleNamespace(run_async=run, stop_async=shutdown)

    async def refresh() -> None:
        handlers[sig]()

    await asyncio.wait_for(worker_cli.serve_monitor(h.worker, stop=stop, refresh=refresh,
        interval=0.001, connect=connect,
        on_started=lambda: worker_cli.install_signal_handlers(loop, stop)), 1)
    assert stop.is_set() and h.worker._stopped
    lock = AccountWorkerLock(tmp_path, account_id=h.worker.scope.account_id, environment="DEMO")
    lock.acquire()
    lock.release()


async def test_failed_native_stop_preserves_sink_stores_lease_and_unknown_liability(
    tmp_path: Path,
) -> None:
    h = harness(tmp_path)
    h.core.permit_fake_monitoring()
    await h.worker.start_passive()
    record = await stage(h)
    h.worker.runtime.journal.claim("entry")
    finished = asyncio.Event()

    async def run() -> None:
        await finished.wait()

    async def failed_stop() -> None:
        raise RuntimeError("disconnect incomplete")

    h.assembly.node = SimpleNamespace(run_async=run, stop_async=failed_stop)
    await h.worker.connect_monitoring()
    with pytest.raises(RuntimeError, match="disconnect incomplete"):
        await h.worker.stop()
    assert not h.worker._stopped
    assert h.worker.runtime.journal.get("entry").state is CommandState.UNKNOWN
    lock = AccountWorkerLock(tmp_path, account_id=h.worker.scope.account_id, environment="DEMO")
    with pytest.raises(WorkerAlreadyRunning):
        lock.acquire()
    h.worker.post_native(h.assembly.channel, fill(record.client_order_id))
    await asyncio.sleep(0)
    assert h.worker.callback_failures == 0

    async def shutdown() -> None:
        # Queue a duplicate at the final transport boundary; drain before SQLite close.
        h.worker.post_native(h.assembly.channel, fill(record.client_order_id))
        finished.set()

    h.assembly.node.stop_async = shutdown
    await h.worker.stop()
    restarted = harness_existing(tmp_path, h)
    await restarted.start_passive()
    assert restarted.runtime.projection("position-1").owned_quantity == 0
    assert len(quarantines(restarted)) == 1
    assert restarted.runtime.projection("position-1").entries_blocked
    await restarted.stop()


def test_independent_subscription_exact_topic_identity_and_close(tmp_path: Path) -> None:
    h = harness(tmp_path)
    handlers: dict[str, Any] = {}
    received: list[object] = []
    bus = SimpleNamespace(
        subscribe=lambda topic, handler: handlers.update({topic: handler}),
        unsubscribe=lambda topic, handler: handlers.pop(topic))
    strategy = SimpleNamespace(id="OwnedExecution-001", trader_id="MONITOR-001", state="RUNNING")
    subscription = NativeOrderSubscription(msgbus=bus, strategy=strategy,
        channel=h.assembly.channel, event_types=(SimpleNamespace,),
        on_event=lambda channel, event: received.append((channel, event)))
    assert list(handlers) == ["events.order.OwnedExecution-001"]
    event = SimpleNamespace(strategy_id=strategy.id, trader_id=strategy.trader_id)
    for state in ("RUNNING", "STOPPING", "STOPPED"):
        strategy.state = state
        handlers["events.order.OwnedExecution-001"](event)
    assert received == [(h.assembly.channel, event)] * 3
    subscription.handle(SimpleNamespace(strategy_id="EXTERNAL", trader_id=strategy.trader_id))
    subscription.handle(SimpleNamespace(strategy_id=strategy.id, trader_id="OTHER-001"))
    subscription.handle(object())
    assert len(received) == 3
    subscription.close()
    subscription.handle(event)
    assert handlers == {} and len(received) == 3


async def test_handler_reinstall_failure_cleans_up_passive_worker(tmp_path: Path) -> None:
    h = harness(tmp_path)

    def failure() -> None:
        raise RuntimeError("signal registration failed")

    async def refresh() -> None:
        pytest.fail("startup failed")

    with pytest.raises(RuntimeError, match="signal registration failed"):
        await worker_cli.serve_monitor(h.worker, stop=asyncio.Event(), refresh=refresh,
                                       on_started=failure)
    assert h.worker._stopped
    lock = AccountWorkerLock(tmp_path, account_id=h.worker.scope.account_id, environment="DEMO")
    lock.acquire()
    lock.release()


@pytest.mark.parametrize("connected,queued", [(True, 0), (False, 1)])
def test_native_stop_timeout_or_undrained_queue_cannot_close_channel(
    tmp_path: Path, connected: bool, queued: int,
) -> None:
    h = harness(tmp_path)
    h.assembly.node = SimpleNamespace(kernel=SimpleNamespace(
        data_engine=SimpleNamespace(check_disconnected=lambda: True),
        exec_engine=SimpleNamespace(check_disconnected=lambda: not connected,
                                    evt_qsize=lambda: queued, cmd_qsize=lambda: 0)))
    with pytest.raises(RuntimeError, match="NATIVE_FEED_NOT_QUIESCED"):
        h.assembly.require_quiesced()
