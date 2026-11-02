"""Real pinned ActorExecutor ownership, without claiming a Linux node lifecycle."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from nautilus_trader.common.executor import ActorExecutor
from test_monitoring_receipts import session  # noqa: F401
from test_native_monitor_client import client  # noqa: F401

from multi_exchange_trader.execution.native_node import NativeNodeAssembly


async def _settle_empty(guard):
    guard()


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
                                       capture_engine_queues=lambda guard: guard(),
                                       require_quiesced=prove, settle_owned_tasks=settle,
                                       settle_engine_queues=lambda guard: _settle_empty(guard))
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
    worker._assembly = SimpleNamespace(node=SimpleNamespace(stop_async=stop),
                                       capture_engine_queues=lambda guard: guard())
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
        capture_engine_queues=lambda guard: guard(), require_quiesced=lambda: effects.append("proof"), settle_owned_tasks=settle,
        settle_engine_queues=lambda guard: _settle_empty(guard),
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
        capture_engine_queues=lambda guard: guard(), require_quiesced=lambda: None, settle_owned_tasks=settle, drain_monitoring=drain,
        settle_engine_queues=lambda guard: _settle_empty(guard),
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

async def test_worker_waits_for_real_sdk_queue_sentinels_before_cancelling_startup():
    """Actual pinned execution queue methods, inert engine shell, no native node."""
    from nautilus_trader.live.execution_engine import LiveExecutionEngine

    from multi_exchange_trader.execution.worker import ExecutionWorker

    loop = asyncio.get_running_loop()
    assembly, engine = _queue_shell()
    cmd, evt = engine._cmd_queue_task, engine._evt_queue_task
    async def run():
        try:
            await asyncio.gather(cmd, evt)
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass  # Pinned run_async consumes its startup cancellation.
    async def stop():
        LiveExecutionEngine._enqueue_sentinel(engine)
    def prove():
        assert engine.cmd_qsize() == engine.evt_qsize() == 0
    async def settle():
        prove()
    assembly.node.stop_async = stop
    assembly.require_quiesced = prove
    assembly.settle_owned_tasks = settle
    worker = object.__new__(ExecutionWorker)
    worker._assembly = assembly
    worker._monitoring = worker._factory = None
    worker._node_task = asyncio.create_task(run())
    await asyncio.sleep(0)
    stopped = loop.create_future()
    stopped.set_result(None)
    worker._node_stop_task = stopped
    LiveExecutionEngine._enqueue_sentinel(engine)
    try:
        await worker._quiesce()
        assert cmd.done() and evt.done()
        assert not cmd.cancelled() and not evt.cancelled()
        prove()
    finally:
        for task in (cmd, evt, worker._node_task):
            task.cancel()
        await asyncio.gather(cmd, evt, worker._node_task, return_exceptions=True)


def _queue_shell():
    from nautilus_trader.live.execution_engine import LiveExecutionEngine

    loop = asyncio.get_running_loop()
    log = SimpleNamespace(debug=lambda *args: None, warning=lambda *args: None)
    data = SimpleNamespace(_loop=loop, _cmd_queue_task=None, _req_queue_task=None,
                           _res_queue_task=None, _data_queue_task=None)
    risk = SimpleNamespace(_loop=loop, _cmd_queue_task=None, _evt_queue_task=None)
    engine = SimpleNamespace(_loop=loop, _cmd_queue=asyncio.Queue(), _evt_queue=asyncio.Queue(),
                             _sentinel=None, _log=log)
    engine.cmd_qsize = engine._cmd_queue.qsize
    engine.evt_qsize = engine._evt_queue.qsize
    engine._cmd_queue_task = asyncio.create_task(LiveExecutionEngine._run_cmd_queue(engine))
    engine._evt_queue_task = asyncio.create_task(LiveExecutionEngine._run_evt_queue(engine))
    for owner, names in ((data, ("cmd", "req", "res", "data")),
                         (risk, ("cmd", "evt")), (engine, ("cmd", "evt"))):
        for name in names:
            setattr(owner, f"get_{name}_queue_task",
                    lambda owner=owner, name=name: getattr(owner, f"_{name}_queue_task"))
    kernel = SimpleNamespace(loop=loop, is_running=lambda: False, data_engine=data,
                             risk_engine=risk, exec_engine=engine)
    return NativeNodeAssembly(SimpleNamespace(kernel=kernel), None, None, None, None), engine


async def test_owned_engine_queues_complete_on_real_sdk_sentinels():
    from nautilus_trader.live.execution_engine import LiveExecutionEngine

    node, engine = _queue_shell()
    try:
        LiveExecutionEngine._enqueue_sentinel(engine)
        await node.settle_engine_queues(lambda: None)
        assert engine._cmd_queue_task.done() and engine._evt_queue_task.done()
        assert engine.cmd_qsize() == engine.evt_qsize() == 0
    finally:
        for task in (engine._cmd_queue_task, engine._evt_queue_task):
            task.cancel()
        await asyncio.gather(engine._cmd_queue_task, engine._evt_queue_task, return_exceptions=True)


@pytest.mark.parametrize("change", ["task", "engine", "node", "guard", "caller"])
async def test_queue_await_owner_changes_and_caller_cancel_leave_consumers_untouched(change):
    node, engine = _queue_shell()
    original = engine._cmd_queue_task, engine._evt_queue_task
    foreign = asyncio.create_task(asyncio.Event().wait())
    valid = True
    def guard():
        if not valid:
            raise RuntimeError("GUARD_CHANGED")
    cleanup = asyncio.create_task(node.settle_engine_queues(guard))
    await asyncio.sleep(0)
    if change == "task":
        engine._evt_queue_task = foreign
    elif change == "engine":
        node.node.kernel.exec_engine = SimpleNamespace(_loop=asyncio.get_running_loop())
    elif change == "node":
        node.node = SimpleNamespace(kernel=node.node.kernel)
    elif change == "guard":
        valid = False
    else:
        cleanup.cancel()
    if change != "caller":
        engine._cmd_queue.put_nowait(None)
    try:
        with pytest.raises(asyncio.CancelledError if change == "caller" else RuntimeError):
            await cleanup
        assert not foreign.done() and not foreign.cancelling()
        assert not original[1].done() and not original[1].cancelling()
        if change == "caller":
            assert not original[0].done() and not original[0].cancelling()
    finally:
        for task in (*original, foreign, cleanup):
            task.cancel()
        await asyncio.gather(*original, foreign, cleanup, return_exceptions=True)


async def test_foreign_queue_task_is_rejected_before_wait_or_cancellation():
    node, engine = _queue_shell()
    original = engine._cmd_queue_task, engine._evt_queue_task
    foreign = asyncio.create_task(asyncio.Event().wait())
    engine._cmd_queue_task = foreign
    try:
        with pytest.raises(RuntimeError, match="NATIVE_QUEUE_TASK_SHAPE_UNSUPPORTED"):
            await node.settle_engine_queues(lambda: None)
        assert not foreign.done() and not foreign.cancelling()
        assert all(not task.done() and not task.cancelling() for task in original)
    finally:
        for task in (*original, foreign):
            task.cancel()
        await asyncio.gather(*original, foreign, return_exceptions=True)

async def test_same_sdk_queue_coroutine_from_another_engine_is_rejected():
    node, own = _queue_shell()
    _, foreign = _queue_shell()
    original = own._cmd_queue_task, own._evt_queue_task
    foreign_tasks = foreign._cmd_queue_task, foreign._evt_queue_task
    own._cmd_queue_task = foreign._cmd_queue_task
    try:
        with pytest.raises(RuntimeError, match="NATIVE_QUEUE_TASK_SHAPE_UNSUPPORTED"):
            await node.settle_engine_queues(lambda: None)
        assert all(not task.done() and not task.cancelling() for task in (*original, *foreign_tasks))
    finally:
        for task in (*original, *foreign_tasks):
            task.cancel()
        await asyncio.gather(*original, *foreign_tasks, return_exceptions=True)


async def test_nonterminating_engine_queues_exhaust_existing_caller_budget_without_cancellation():
    node, engine = _queue_shell()
    tasks = engine._cmd_queue_task, engine._evt_queue_task
    try:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.02):
                await node.settle_engine_queues(lambda: None)
        assert all(not task.done() and not task.cancelling() for task in tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

async def test_proven_queue_snapshot_survives_completion_and_retry():
    from nautilus_trader.live.execution_engine import LiveExecutionEngine

    node, engine = _queue_shell()
    tasks = engine._cmd_queue_task, engine._evt_queue_task
    try:
        node.capture_engine_queues(lambda: None)
        LiveExecutionEngine._enqueue_sentinel(engine)
        await node.settle_engine_queues(lambda: None)
        assert all(task.get_coro().cr_frame is None for task in tasks)
        await node.settle_engine_queues(lambda: None)
        assert all(task.done() and not task.cancelling() for task in tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_unproven_completed_queue_task_is_rejected():
    from nautilus_trader.live.execution_engine import LiveExecutionEngine

    node, engine = _queue_shell()
    tasks = engine._cmd_queue_task, engine._evt_queue_task
    try:
        LiveExecutionEngine._enqueue_sentinel(engine)
        await asyncio.gather(*tasks)
        with pytest.raises(RuntimeError, match='NATIVE_QUEUE_TASK_SHAPE_UNSUPPORTED'):
            node.capture_engine_queues(lambda: None)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_initial_none_queue_snapshot_rejects_later_start_without_cancelling():
    node, engine = _queue_shell()
    tasks = engine._cmd_queue_task, engine._evt_queue_task
    engine._cmd_queue_task = engine._evt_queue_task = None
    try:
        node.capture_engine_queues(lambda: None)
        engine._cmd_queue_task, engine._evt_queue_task = tasks
        with pytest.raises(RuntimeError, match='NATIVE_QUEUE_OWNER_CHANGED'):
            await node.settle_engine_queues(lambda: None)
        assert all(not task.done() and not task.cancelling() for task in tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
