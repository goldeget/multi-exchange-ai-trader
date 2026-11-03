"""Worker admission races against real session/SQLite/Cache, before node startup.

These tests make no Linux native lifecycle acceptance claim.
"""
# ruff: noqa: F811
import asyncio
from dataclasses import FrozenInstanceError

import pytest
from fixtures.runtime_policy_v1 import commit_policy_change
from test_monitoring_receipts import session, target_core  # noqa: F401
from test_native_monitor_client import client  # noqa: F401

from multi_exchange_trader.execution.runtime import RuntimeBlocked


@pytest.fixture
async def prepared(target_core, client):
    worker = target_core[2]
    worker._assembly.execution_client = client
    worker._runtime._receipt_session = worker.session
    worker._journal, worker._store = worker.runtime.journal, worker.runtime.store
    yield target_core


async def test_refresh_fences_second_resume_history_and_stop_wins(prepared):
    core = prepared
    assert hasattr(core[2], 'resume_restored_monitoring'), 'worker resume protocol is required'
    worker, blocked, started, release = core[2], *core[4:]
    blocked[0] = True
    attempt = asyncio.create_task(worker.resume_restored_monitoring())
    await started.wait()
    assert worker._operations == 1
    for operation in (worker.resume_restored_monitoring(),
                      worker.collect_history('BTCUSDT', start_ms=1, end_ms=2),
                      worker.corroborate_native_fills('BTCUSDT', start_ms=1, end_ms=2),
                      worker.recover_history_fills('BTCUSDT', start_ms=1, end_ms=2),
                      worker.stage()):
        with pytest.raises(RuntimeBlocked, match='MONITORING_RESUME_IN_PROGRESS'):
            await operation
    with pytest.raises(RuntimeBlocked, match='AWAIT_INFLIGHT_OPERATION'):
        await worker.stop()
    release.set()
    with pytest.raises((RuntimeBlocked, ValueError)):
        await attempt
    assert worker._node_task is None
    assert worker._operations == 0
    assert worker.monitoring_status.phase == 'ATTENTION'
    assert worker.monitoring_status.resources_retained
    assert worker._lock.acquired and not worker._channel_closed


@pytest.mark.parametrize('mutation', ['binding', 'client', 'lease', 'policy', 'session_close', 'key', 'store'])
async def test_refresh_owner_change_cannot_reach_native_start(prepared, mutation):
    core = prepared
    assert hasattr(core[2], 'resume_restored_monitoring'), 'worker resume protocol is required'
    worker, blocked, started, release = core[2], *core[4:]
    blocked[0] = True
    attempt = asyncio.create_task(worker.resume_restored_monitoring())
    try:
        await started.wait()
        if mutation == 'binding':
            worker.binding = object()
        elif mutation == 'client':
            worker._assembly.execution_client = object()
        elif mutation == 'lease':
            worker._lock.release()
            worker._lock.acquire()
        elif mutation == 'key':
            worker.session._api_key = 'different-synthetic-key'
        elif mutation == 'store':
            worker._store = object()
        elif mutation == 'policy':
            snap = worker.policy_book.read()
            assert snap['schema_version'] == 2
            changed = commit_policy_change(worker.policy_book, {
                **snap['policy'], 'legacy_v1': {**snap['policy']['legacy_v1'], 'max_positions': 1}})
            assert changed['revision'] == snap['revision'] + 1
            assert worker.policy_book.read() == changed
        else:
            await worker.session.aclose()
        release.set()
        with pytest.raises((RuntimeBlocked, ValueError)):
            await attempt
        assert worker._node_task is None and worker._operations == 0
        assert worker.monitoring_status.phase == 'ATTENTION'
    finally:
        release.set()
        if not attempt.done():
            attempt.cancel()
        await asyncio.gather(attempt, return_exceptions=True)
        if mutation == 'lease':
            worker._lock.release()


async def test_cancel_settles_read_and_retains_unproven_resources(prepared):
    core = prepared
    assert hasattr(core[2], 'resume_restored_monitoring'), 'worker resume protocol is required'
    worker, blocked, started, _ = core[2], *core[4:]
    blocked[0] = True
    attempt = asyncio.create_task(worker.resume_restored_monitoring())
    await started.wait()
    attempt.cancel()
    with pytest.raises(asyncio.CancelledError):
        await attempt
    assert not worker.session._monitoring_receipts
    assert worker._node_task is None and worker._operations == 0
    status = worker.monitoring_status
    assert status.phase == 'ATTENTION' and status.resources_retained
    assert status.trading_enabled is False and status.venue_event_complete is False
    assert status.production_supported is False and status.credit_authorized is False
    with pytest.raises(FrozenInstanceError):
        status.phase = 'MONITORING'
    with pytest.raises((RuntimeBlocked, ValueError)):
        await worker.resume_restored_monitoring()

async def test_readonly_no_capital_pre_matches_then_native_start_fails_closed(prepared):
    # The genuine client is armed by a real PRE receipt. This fixture deliberately
    # has no runnable Linux node; it cannot pass readiness or produce MONITORING.
    worker = prepared[2]
    original_binding = worker.binding
    assert worker.policy_book.read()['policy']['legacy_v1']['capital_usdt'] is None
    with pytest.raises((ValueError, RuntimeBlocked)):
        await worker.connect_monitoring()
    controller = worker._monitoring
    assert controller.armed
    assert worker.binding is not original_binding
    assert controller.client._monitor_binding is worker.binding
    assert controller.budget.physical_attempts >= 6
    assert not worker.session._monitoring_receipts
    assert worker.monitoring_status.phase == 'ATTENTION'
    assert worker._node_task.done()
    # Retrieve failed node result in this negative fixture; never native proof.
    if not worker._node_task.cancelled():
        worker._node_task.exception()


async def test_resume_refuses_other_already_admitted_operation(prepared):
    worker = prepared[2]
    worker._operations = 1
    try:
        with pytest.raises(RuntimeBlocked, match='MONITORING_OPERATION_IN_PROGRESS'):
            await worker.resume_restored_monitoring()
        assert worker._monitoring is None and worker._node_task is None
    finally:
        worker._operations = 0


async def test_resume_deadline_stops_blocked_http_and_releases_pending_receipts(prepared, monkeypatch):
    from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
    original = MonitoringReadBudget.__init__
    def short(self, **kwargs):
        original(self, **kwargs)
        self.deadline = asyncio.get_running_loop().time() + 0.05
    monkeypatch.setattr(MonitoringReadBudget, '__init__', short)
    worker = prepared[2]
    prepared[4][0] = True
    with pytest.raises(TimeoutError):
        await worker.resume_restored_monitoring()
    assert worker._node_task is None and not worker.session._monitoring_receipts
    assert worker.monitoring_status.phase == 'ATTENTION'


async def test_mutation_after_genuine_pre_read_cannot_arm(prepared, monkeypatch):
    import httpx
    original = httpx.AsyncClient.send
    worker = prepared[2]
    before = worker.policy_book.read()
    committed = []
    async def mutate(client, request, **kwargs):
        response = await original(client, request, **kwargs)
        if request.url.path == '/v5/position/list' and not committed:
            snapshot = worker.policy_book.read()
            assert snapshot['schema_version'] == 2
            changed = commit_policy_change(worker.policy_book, {
                **snapshot['policy'], 'legacy_v1': {**snapshot['policy']['legacy_v1'], 'max_positions': 1}})
            committed.append(changed)
        return response
    monkeypatch.setattr(httpx.AsyncClient, 'send', mutate)
    with pytest.raises(ValueError):
        await worker.resume_restored_monitoring()
    # A failed write in the HTTP hook cannot supply the expected owner-fence error.
    assert len(committed) == 1
    assert committed[0]['revision'] == before['revision'] + 1
    assert worker.policy_book.read() == committed[0]
    assert not worker._monitoring.armed and worker._node_task is None
    assert not worker.session._monitoring_receipts

async def test_cleanup_owner_guard_rejects_reacquired_lease(prepared):
    from multi_exchange_trader.execution.monitoring_resume import MonitoringResume
    worker = prepared[2]
    controller = MonitoringResume(worker)
    assert hasattr(controller, 'require_cleanup_owner'), 'cleanup must fence exact resource owner'
    worker._stopping = True
    controller.require_cleanup_owner()  # Stopping is expected during accepted draining.
    worker._lock.release()
    worker._lock.acquire()
    try:
        with pytest.raises(ValueError, match='MONITORING_CLEANUP_OWNER_CHANGED'):
            controller.require_cleanup_owner()
    finally:
        worker._lock.release()


async def test_callback_decode_failure_retires_without_application_ack(prepared):
    from multi_exchange_trader.execution.monitoring_resume import MonitoringResume
    worker = prepared[2]
    controller = MonitoringResume(worker)
    worker._monitoring = controller
    worker._consume_native(worker._assembly.channel, object(), 1)
    assert worker.callback_failures == 1
    assert worker.monitoring_status.fault == 'MONITORING_APPLICATION_CALLBACK_FAILED'
    assert worker._assembly.monitoring_stamp().delivered == 0


async def test_unexplained_target_delta_is_not_a_retarget_permission(prepared):
    from dataclasses import replace

    from multi_exchange_trader.execution.monitoring_resume import MonitoringResume
    worker, target = prepared[2], prepared[1]
    controller = MonitoringResume(worker)
    unknown = replace(target, application=replace(target.application, digest='unknown'))
    assert not controller.legitimate_delta(target, unknown)
    assert controller.legitimate_delta(target, replace(target,
        stream_stamp=replace(target.stream_stamp, received=4, delivered=4)))


async def test_manufactured_monitoring_status_cannot_qualify_readiness(prepared):
    from dataclasses import replace

    from multi_exchange_trader.execution.monitoring_resume import MonitoringResume
    worker = prepared[2]
    controller = MonitoringResume(worker)
    worker._monitoring = controller
    controller.state = replace(controller.state, phase='MONITORING')
    assert worker.monitoring_status.phase == 'ATTENTION'
    assert not worker.monitoring_status.trading_enabled

async def test_unknown_native_event_cannot_enter_application_delivery(prepared):
    assembly = prepared[2]._assembly
    assert hasattr(assembly, 'require_monitoring_delivery'), 'accepted exact event guard is required'
    with pytest.raises(ValueError, match='MONITORING_ACCEPTED_EVENT_REQUIRED'):
        assembly.require_monitoring_delivery(object())

async def test_real_fresh_worker_cannot_launch_unarmed_monitor_client(prepared):
    from multi_exchange_trader.execution.native_restart import WorkerNativeRestartStatus
    worker = prepared[2]
    worker._native_restart_status = WorkerNativeRestartStatus('FRESH_EMPTY_CACHE')
    with pytest.raises(RuntimeBlocked, match='MONITORING_RESTORED_CACHE_REQUIRED'):
        await worker.connect_monitoring()
    assert worker._node_task is None


async def test_incomplete_client_task_prevents_monitor_quiescence(prepared):
    assembly = prepared[2]._assembly
    client = assembly.execution_client
    await client._repay_task
    assert hasattr(assembly, 'require_monitoring_tasks_quiesced'), 'owned tasks must settle before seal'
    started, release = asyncio.Event(), asyncio.Event()
    async def unfinished():
        started.set()
        await release.wait()
    task = client.create_task(unfinished())
    await started.wait()
    try:
        with pytest.raises(ValueError, match='MONITORING_CLIENT_TASKS_NOT_QUIESCED'):
            assembly.require_monitoring_tasks_quiesced()
    finally:
        release.set()
        await task
    assembly.require_monitoring_tasks_quiesced()


async def test_closed_transport_sticky_gap_cannot_seal_or_release_worker(prepared, monkeypatch):
    from test_private_monitor_stream import connected

    from multi_exchange_trader.execution.monitoring_models import MonitoringError
    from multi_exchange_trader.execution.monitoring_resume import MonitoringResume

    worker = prepared[2]
    client = worker._assembly.execution_client
    async def reject_original(*args):
        raise MonitoringError("MONITORING_ORIGINAL_DELIVERY_FAILED")
    stream, wire = await connected(monkeypatch, reject_original)
    client._monitor_stream = stream
    wire.emit({"topic": "execution.linear", "data": [{}]})
    for _ in range(20):
        if stream.fault:
            break
        await asyncio.sleep(0)
    assert stream.stamp().received > stream.stamp().delivered
    await client._disconnect()
    assert wire.is_closed()
    controller = MonitoringResume(worker)
    worker._monitoring = controller
    controller.retire("MONITORING_ORIGINAL_DELIVERY_FAILED")
    before = worker.runtime.monitoring_state()
    for _ in range(2):
        with pytest.raises(MonitoringError, match="^MONITORING_DELIVERY_GAP_RETAINS_RESOURCES$"):
            await worker.stop()
        assert not worker._stopped and not worker._channel_closed
        assert worker._lock.acquired and controller.state.resources_retained
        assert controller.state.phase == "ATTENTION"
        assert worker._runtime.monitoring_state() == before

@pytest.mark.parametrize('replacement', ['store', 'assembly', 'lease', 'runtime', 'controller', 'run', 'stop'])
async def test_final_cleanup_yield_rechecks_owner_before_closing_any_resource(
    prepared, monkeypatch, replacement
):
    # This unit isolates the final continuation after quiescence; the boundary
    # below is not evidence that a Linux native producer has really stopped.
    from dataclasses import replace
    from types import SimpleNamespace

    from multi_exchange_trader.execution.monitoring_resume import MonitoringResume

    worker = prepared[2]
    original_assembly, original_store = worker._assembly, worker._store
    original_runtime = worker._runtime
    controller = MonitoringResume(worker)
    worker._monitoring = controller
    controller.in_progress = False
    traces = []
    worker._restart = SimpleNamespace(seal=lambda _: traces.append('seal'))
    monkeypatch.setattr(original_assembly, 'dispose_local', lambda: traces.append('original-dispose'))
    monkeypatch.setattr(original_assembly, 'close_event_channel', lambda: traces.append('channel-close'))
    for store in (worker._store, worker._journal):
        monkeypatch.setattr(store, 'close', lambda: traces.append('original-store-close'))
    original_lease = worker._lock.acquisition_id
    replacement_task = asyncio.create_task(asyncio.Event().wait())
    def mutate():
        if replacement == 'store':
            worker._store = SimpleNamespace(close=lambda: traces.append('replacement-store-close'))
        elif replacement == 'runtime':
            worker._runtime = object()
        elif replacement == 'controller':
            worker._monitoring = None
        elif replacement == 'assembly':
            worker._assembly = replace(original_assembly)
            worker._assembly.dispose_local = lambda: traces.append('replacement-dispose')
        elif replacement in ('run', 'stop'):
            setattr(worker, '_node_task' if replacement == 'run' else '_node_stop_task', replacement_task)
        else:
            worker._lock.release()
            worker._lock.acquire()
    async def quiesced_boundary():
        controller.require_cleanup_owner()
        asyncio.get_running_loop().call_soon(mutate)
    monkeypatch.setattr(worker, '_quiesce', quiesced_boundary)
    try:
        with pytest.raises((ValueError, RuntimeBlocked)):
            await worker.stop()
        assert traces == []
        assert not worker._channel_closed and not worker._stopped
        assert controller.state.phase == 'ATTENTION'
        assert controller.state.resources_retained
        assert worker._lock.acquired
        assert not replacement_task.done() and not replacement_task.cancelling()
        if replacement == 'lease':
            assert worker._lock.acquisition_id != original_lease
    finally:
        replacement_task.cancel()
        await asyncio.gather(replacement_task, return_exceptions=True)
        worker._node_task = worker._node_stop_task = None
        worker._assembly, worker._store = original_assembly, original_store
        worker._runtime, worker._monitoring = original_runtime, controller
        if replacement == 'lease':
            worker._lock.release()


@pytest.mark.parametrize('replacement', ['run', 'stop', 'assembly', 'node', 'controller'])
async def test_stop_wait_owner_change_retains_real_resources(prepared, monkeypatch, replacement):
    # Real worker/controller/SQLite/lease, with inert run/stop shells only.
    # The Linux contracts independently prove the SDK lifecycle.
    from dataclasses import replace
    from types import SimpleNamespace

    from multi_exchange_trader.execution.monitoring_resume import MonitoringResume

    worker = prepared[2]
    assembly, runtime = worker._assembly, worker._runtime
    node = assembly.node
    controller = MonitoringResume(worker)
    worker._monitoring = controller
    controller.in_progress = False
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
        effects.append('settle')
    monkeypatch.setattr(node, 'stop_async', stop, raising=False)
    monkeypatch.setattr(assembly, 'capture_engine_queues', lambda guard: guard())
    monkeypatch.setattr(assembly, 'require_quiesced', lambda: effects.append('proof'))
    monkeypatch.setattr(assembly, 'settle_owned_tasks', settle)
    monkeypatch.setattr(assembly, 'close_event_channel', lambda: effects.append('channel-close'))
    monkeypatch.setattr(assembly, 'dispose_local', lambda: effects.append('dispose'))
    worker._restart = SimpleNamespace(seal=lambda _: effects.append('seal'))
    for store in (worker._store, worker._journal):
        monkeypatch.setattr(store, 'close', lambda: effects.append('store-close'))
    original = worker._node_task = asyncio.create_task(run('original-cancelled'))
    foreign = asyncio.create_task(run('foreign-cancelled'))
    stopping = asyncio.create_task(worker.stop())
    await entered.wait()
    stopped = worker._node_stop_task
    if replacement == 'run':
        worker._node_task = foreign
    elif replacement == 'stop':
        worker._node_stop_task = foreign
    elif replacement == 'assembly':
        worker._assembly = replace(assembly)
        worker._assembly.require_quiesced = lambda: effects.append('replacement-proof')
        worker._assembly.settle_owned_tasks = settle
    elif replacement == 'node':
        assembly.node = SimpleNamespace(cache=node.cache, stop_async=stop)
    else:
        worker._monitoring = None
    release.set()
    try:
        with pytest.raises((ValueError, RuntimeBlocked), match='^MONITORING_CLEANUP_OWNER_CHANGED$'):
            await asyncio.wait_for(stopping, 0.5)
        assert effects == []
        assert not original.done() and not original.cancelling()
        assert not foreign.done() and not foreign.cancelling()
        assert not worker._stopped and not worker._channel_closed
        assert worker._lock.acquired and controller.state.resources_retained
        assert controller.state.phase == 'ATTENTION'
        assert runtime.monitoring_state().credits == ()
    finally:
        for task in (stopping, stopped, original, foreign):
            task.cancel()
        await asyncio.gather(stopping, stopped, original, foreign, return_exceptions=True)
        assembly.node = node
        worker._assembly, worker._monitoring = assembly, controller
        worker._node_task = worker._node_stop_task = None

@pytest.mark.parametrize('change', ['lease', 'run', 'caller'])
async def test_queue_wait_change_retains_real_worker_resources(prepared, monkeypatch, change):
    # Real worker/controller/stores; this gate only schedules a portable queue wait.
    from types import SimpleNamespace

    from multi_exchange_trader.execution.monitoring_resume import MonitoringResume

    worker = prepared[2]
    assembly, runtime = worker._assembly, worker._runtime
    controller = MonitoringResume(worker)
    worker._monitoring = controller
    controller.in_progress = False
    entered, release = asyncio.Event(), asyncio.Event()
    effects = []
    async def run():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            effects.append('run-cancel')
    async def stop():
        pass
    async def queues(guard):
        entered.set()
        await release.wait()
        guard()
    monkeypatch.setattr(assembly.node, 'stop_async', stop, raising=False)
    monkeypatch.setattr(assembly, 'capture_engine_queues', lambda guard: guard())
    monkeypatch.setattr(assembly, 'settle_engine_queues', queues)
    monkeypatch.setattr(assembly, 'require_quiesced', lambda: effects.append('proof'))
    monkeypatch.setattr(assembly, 'settle_owned_tasks', lambda: effects.append('settle'))
    monkeypatch.setattr(assembly, 'close_event_channel', lambda: effects.append('channel-close'))
    monkeypatch.setattr(assembly, 'dispose_local', lambda: effects.append('dispose'))
    worker._restart = SimpleNamespace(seal=lambda _: effects.append('seal'))
    for store in (worker._store, worker._journal):
        monkeypatch.setattr(store, 'close', lambda: effects.append('store-close'))
    original = worker._node_task = asyncio.create_task(run())
    foreign = asyncio.create_task(asyncio.Event().wait())
    stopping = asyncio.create_task(worker.stop())
    await entered.wait()
    stopped = worker._node_stop_task
    if change == 'run':
        worker._node_task = foreign
    elif change == 'lease':
        worker._lock.release()
        worker._lock.acquire()
    else:
        stopping.cancel()
    release.set()
    try:
        with pytest.raises((ValueError, RuntimeBlocked, asyncio.CancelledError)):
            await stopping
        assert effects == []
        assert not original.done() and not original.cancelling()
        assert not foreign.done() and not foreign.cancelling()
        assert not worker._stopped and not worker._channel_closed
        assert worker._lock.acquired and controller.state.resources_retained
        assert controller.state.phase == 'ATTENTION'
        if change == 'lease':
            with pytest.raises(RuntimeBlocked, match='ACCOUNT_WORKER_LEASE_REQUIRED'):
                runtime.monitoring_state()
        else:
            assert runtime.monitoring_state().credits == ()
    finally:
        for task in (stopping, stopped, original, foreign):
            task.cancel()
        await asyncio.gather(stopping, stopped, original, foreign, return_exceptions=True)
        worker._node_task = worker._node_stop_task = None
        if change == 'lease':
            worker._lock.release()
