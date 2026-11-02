"""Real pinned ActorExecutor ownership, without claiming a Linux node lifecycle."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from nautilus_trader.common.executor import ActorExecutor
from test_monitoring_receipts import session  # noqa: F401
from test_native_monitor_client import client  # noqa: F401

from multi_exchange_trader.execution.native_node import NativeNodeAssembly


def assembly(pool):
    stopped = SimpleNamespace(check_disconnected=lambda: True, evt_qsize=lambda: 0,
                              cmd_qsize=lambda: 0)
    kernel = SimpleNamespace(executor=pool, loop=asyncio.get_running_loop(),
                             is_running=lambda: False, data_engine=stopped, exec_engine=stopped,
                             trader=SimpleNamespace(actors=list, strategies=lambda: [object()],
                                                    exec_algorithms=list))
    result = NativeNodeAssembly(SimpleNamespace(kernel=kernel), None, None, None, None)
    result._owned_executor = pool
    return result


async def test_settle_owned_actor_replacement_preserves_foreign_executor_work():
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool, ThreadPoolExecutor() as foreign_pool:
        own = ActorExecutor(loop, pool)
        foreign = ActorExecutor(loop, foreign_pool)
        node = assembly(pool)
        try:
            own.cancel_all_tasks()  # Exact pinned Actor._stop behavior creates a NEW worker.
            replacement = own._worker_task
            assert not replacement.done()
            assert hasattr(node, "settle_owned_tasks"), "owned SDK workers need explicit shutdown"
            await node.settle_owned_tasks()
            assert replacement.done() and not foreign._worker_task.done()
            assert await loop.run_in_executor(foreign_pool, lambda: 37) == 37
            assert await asyncio.to_thread(lambda: 41) == 41
            node.require_owned_tasks_quiesced()
        finally:
            await own.shutdown()
            await foreign.shutdown()


async def test_owned_actor_nonempty_queue_refuses_cleanup_without_cancelling_work():
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        own = ActorExecutor(loop, pool)
        node = assembly(pool)
        try:
            own.queue_for_executor(lambda: 7)
            task = own._worker_task
            assert hasattr(node, "settle_owned_tasks"), "must refuse unproven actor quiescence"
            with pytest.raises(RuntimeError, match="NATIVE_ACTOR_WORK_NOT_QUIESCED"):
                await node.settle_owned_tasks()
            assert not task.done() and not task.cancelling()
        finally:
            await own.shutdown()


async def test_settle_awaits_exact_sdk_disconnect_wrapper(client):  # noqa: F811
    loop = asyncio.get_running_loop()
    await client._repay_task
    with ThreadPoolExecutor() as pool:
        own = ActorExecutor(loop, pool)
        node = assembly(pool)
        node.execution_client = client
        before = asyncio.all_tasks()
        client.disconnect()
        disconnects = asyncio.all_tasks() - before
        assert len(disconnects) == 1
        try:
            await node.settle_owned_tasks()
            assert node._disconnect_workers == disconnects
            assert all(task.done() and task.exception() is None for task in disconnects)
            node.require_monitoring_tasks_quiesced()
        finally:
            await own.shutdown()


async def test_worker_interrupts_only_owned_start_wait_after_proven_stop():
    from multi_exchange_trader.execution.worker import ExecutionWorker

    calls = []
    async def run():
        try:
            await asyncio.Event().wait()  # Pinned startup waits despite concurrent stop.
        except asyncio.CancelledError:
            calls.append("run-ended")
    async def stop():
        calls.append("real-stop")
    async def settle():
        calls.append("settled")
    def prove():
        calls.append("disconnected")
    worker = object.__new__(ExecutionWorker)
    worker._assembly = SimpleNamespace(node=SimpleNamespace(stop_async=stop),
                                       require_quiesced=prove, settle_owned_tasks=settle)
    worker._monitoring = None
    worker._factory = None
    worker._node_stop_task = None
    worker._node_task = asyncio.create_task(run())
    await asyncio.sleep(0)
    try:
        await asyncio.wait_for(worker._quiesce(), 0.2)
        assert calls.index("real-stop") < calls.index("disconnected") < calls.index("run-ended")
        assert "settled" in calls and worker._node_task.done()
    finally:
        worker._node_task.cancel()
        await worker._node_task


@pytest.mark.parametrize("change", ["pool", "running", "missing-actor"])
async def test_unproven_owner_or_lifecycle_never_cancels_owned_actor(change):
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool, ThreadPoolExecutor() as other:
        own = ActorExecutor(loop, pool)
        node = assembly(pool)
        if change == "pool":
            node.node.kernel.executor = other
        elif change == "running":
            node.node.kernel.is_running = lambda: True
        else:
            node.node.kernel.trader.strategies = lambda: [object(), object()]
        try:
            with pytest.raises(RuntimeError, match="NATIVE_"):
                await node.settle_owned_tasks()
            assert not own._worker_task.done() and not own._worker_task.cancelling()
        finally:
            await own.shutdown()


async def test_cancelled_cleanup_caller_does_not_cancel_node_before_real_stop():
    from multi_exchange_trader.execution.worker import ExecutionWorker

    stop_started, release_stop = asyncio.Event(), asyncio.Event()
    async def stop():
        stop_started.set()
        await release_stop.wait()
    async def run():
        await asyncio.Event().wait()
    worker = object.__new__(ExecutionWorker)
    worker._assembly = SimpleNamespace(node=SimpleNamespace(stop_async=stop))
    worker._monitoring = None
    worker._factory = None
    worker._node_stop_task = None
    worker._node_task = asyncio.create_task(run())
    task = asyncio.create_task(worker._quiesce())
    await stop_started.wait()
    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not worker._node_task.done() and not worker._node_task.cancelling()
        assert not worker._node_stop_task.cancelled()
    finally:
        release_stop.set()
        await worker._node_stop_task
        worker._node_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker._node_task


@pytest.mark.parametrize("replacement", ["run", "stop", "assembly", "node", "controller"])
async def test_stop_await_owner_replacement_never_cancels_replacement_work(replacement):
    # Inert lifecycle shells exercise real worker scheduling/identity fences.
    # They are not evidence of native stop, connection or Linux readiness.
    from multi_exchange_trader.execution.runtime import RuntimeBlocked
    from multi_exchange_trader.execution.worker import ExecutionWorker

    entered, release = asyncio.Event(), asyncio.Event()
    effects = []
    async def run(label):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            effects.append(label)
    async def stop():
        entered.set()
        await release.wait()
    async def settle():
        effects.append("settle")
    async def drain():
        effects.append("drain")
        return SimpleNamespace(received=0, delivered=0)
    worker = object.__new__(ExecutionWorker)
    original_node = SimpleNamespace(stop_async=stop)
    original_assembly = SimpleNamespace(node=original_node,
        require_quiesced=lambda: effects.append("proof"), settle_owned_tasks=settle,
        drain_monitoring=drain, require_monitoring_tasks_quiesced=lambda: None)
    worker._assembly, worker._monitoring, worker._factory = original_assembly, None, None
    original = worker._node_task = asyncio.create_task(run("original-cancelled"))
    foreign = asyncio.create_task(run("foreign-cancelled"))
    worker._node_stop_task = None
    cleanup = asyncio.create_task(worker._quiesce())
    await entered.wait()
    stopped = worker._node_stop_task
    if replacement == "run":
        worker._node_task = foreign
    elif replacement == "stop":
        worker._node_stop_task = foreign
    elif replacement == "assembly":
        worker._assembly = SimpleNamespace(**vars(original_assembly))
    elif replacement == "node":
        original_assembly.node = SimpleNamespace(stop_async=stop)
    elif replacement == "controller":
        worker._monitoring = SimpleNamespace(require_cleanup_owner=lambda: None,
                                             state=SimpleNamespace(retained_stamp=None))
    release.set()
    try:
        with pytest.raises(RuntimeBlocked, match="^MONITORING_CLEANUP_OWNER_CHANGED$"):
            await asyncio.wait_for(cleanup, 0.5)
        assert effects == []
        assert stopped.done() and stopped.exception() is None
        assert not original.done() and not original.cancelling()
        assert not foreign.done() and not foreign.cancelling()
    finally:
        for task in (cleanup, original, foreign, stopped):
            task.cancel()
        await asyncio.gather(cleanup, original, foreign, stopped, return_exceptions=True)


@pytest.mark.parametrize("phase", ["run", "settle", "drain"])
async def test_later_quiesce_await_rechecks_task_owner_before_further_effects(phase):
    from multi_exchange_trader.execution.runtime import RuntimeBlocked
    from multi_exchange_trader.execution.worker import ExecutionWorker

    entered, release = asyncio.Event(), asyncio.Event()
    effects = []
    async def boundary(label):
        effects.append(label)
        if phase == label:
            entered.set()
            await release.wait()
    async def run():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await boundary("run")
    async def stop():
        return None
    async def settle():
        await boundary("settle")
    async def drain():
        await boundary("drain")
        return SimpleNamespace(received=0, delivered=0)
    worker = object.__new__(ExecutionWorker)
    worker._assembly = SimpleNamespace(node=SimpleNamespace(stop_async=stop),
        require_quiesced=lambda: None, settle_owned_tasks=settle, drain_monitoring=drain,
        require_monitoring_tasks_quiesced=lambda: effects.append("final-proof"))
    worker._monitoring = SimpleNamespace(require_cleanup_owner=lambda: None,
                                         state=SimpleNamespace(retained_stamp=None))
    worker._factory, worker._node_stop_task = None, None
    original = worker._node_task = asyncio.create_task(run())
    foreign = asyncio.create_task(asyncio.Event().wait())
    await asyncio.sleep(0)
    cleanup = asyncio.create_task(worker._quiesce())
    await entered.wait()
    worker._node_task = foreign
    release.set()
    try:
        with pytest.raises(RuntimeBlocked, match="^MONITORING_CLEANUP_OWNER_CHANGED$"):
            await asyncio.wait_for(cleanup, 0.5)
        assert effects == {"run": ["run"], "settle": ["run", "settle"],
                           "drain": ["run", "settle", "drain"]}[phase]
        assert original.done() and not foreign.done() and not foreign.cancelling()
    finally:
        for task in (cleanup, original, foreign):
            task.cancel()
        await asyncio.gather(cleanup, original, foreign, return_exceptions=True)
