"""Synthetic HTTP-boundary tests; actual signing and issuer checks stay enabled."""

import asyncio
import copy
import importlib
import pickle
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from multi_exchange_trader.execution.account_binding import BybitDemoAccountSession
from multi_exchange_trader.execution.protection import ProtectionScope

NOW = datetime(2026, 11, 2, 1, tzinfo=UTC)
MS = int(NOW.timestamp() * 1000)
SCOPE = ProtectionScope("12345", "DEMO")


@pytest.fixture
async def session(monkeypatch, request):
    wall, mono, requests = [NOW], [10.0], []

    async def send(client, request, **kwargs):
        assert request.url.host == "api-demo.bybit.com"
        requests.append(request)
        result = {}
        if request.url.path == "/v5/user/query-api":
            assert request.headers["X-BAPI-SIGN"]
            result = {
                "apiKey": "synthetic-key",
                "secret": "",
                "userID": 12345,
                "isMaster": False,
                "parentUid": "0",
                "readOnly": 1,
                "permissions": {"Wallet": []},
            }
        return httpx.Response(
            200,
            json={"retCode": 0, "time": int(wall[0].timestamp() * 1000), "result": result},
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    owner = BybitDemoAccountSession(
        api_key="synthetic-key",
        api_secret="synthetic-secret",
        clock=lambda: wall[0],
        monotonic=lambda: mono[0],
        max_age=timedelta(seconds=getattr(request, "param", 30)),
    )
    binding = await owner.authenticate(
        expected_uid="12345", expected_parent_uid="0", expected_is_master=False
    )
    yield owner, binding, wall, mono, requests
    await owner.aclose()


async def test_expired_exact_attested_handle_refreshes_but_copy_cannot(session):
    owner, binding, wall, mono, requests = session
    assert hasattr(owner, "refresh_monitoring_binding")
    wall[0] += timedelta(seconds=31)
    mono[0] += 31
    for invalid in (object(), object.__new__(type(binding))):
        with pytest.raises(ValueError):
            await owner.refresh_monitoring_binding(invalid, scope=SCOPE)
    assert len(requests) == 2
    fresh = await owner.refresh_monitoring_binding(binding, scope=SCOPE)
    assert fresh is not binding
    assert owner.require_observation_binding(fresh, scope=SCOPE).read_only
    with pytest.raises(ValueError):
        owner.require_execution_binding(fresh, scope=SCOPE)


async def test_read_only_monitoring_installer_preserves_execution_gate(session):
    owner, binding, *_ = session
    assert hasattr(owner, "configure_native_monitoring")
    installed = []
    owner.configure_native_monitoring(binding, scope=SCOPE, install=installed.append)
    assert len(installed) == 1
    with pytest.raises(ValueError):
        owner.configure_native_execution(binding, scope=SCOPE, install=installed.append)
    assert len(installed) == 1


def test_monitoring_handle_rejects_construction_copy_and_pickle():
    assert importlib.util.find_spec("multi_exchange_trader.execution.monitoring_receipts")
    module = importlib.import_module("multi_exchange_trader.execution.monitoring_receipts")
    handle = object.__new__(module.AuthenticatedMonitoringReceipt)
    for action in (
        module.AuthenticatedMonitoringReceipt,
        lambda: copy.copy(handle),
        lambda: copy.deepcopy(handle),
        lambda: pickle.dumps(handle),
    ):
        with pytest.raises((ValueError, TypeError)):
            action()


@pytest.fixture
async def target_core(session, tmp_path, monkeypatch):
    """Real owner/runtime/SQLite/cache unit capture; no lifecycle/startup claim."""
    from types import SimpleNamespace

    from nautilus_trader.cache.cache import Cache

    from multi_exchange_trader.execution.bybit_native import NativeBindings, NativeBybitBridge
    from multi_exchange_trader.execution.journal import CommandJournal, ExecutionScope
    from multi_exchange_trader.execution.monitoring_models import (
        StreamStamp,
        _issue_monitoring_witness,
    )
    from multi_exchange_trader.execution.monitoring_reconciliation import capture_monitoring_target
    from multi_exchange_trader.execution.native_node import NativeChannel, NativeNodeAssembly
    from multi_exchange_trader.execution.native_restart import WorkerNativeRestartStatus
    from multi_exchange_trader.execution.ownership import AccountWorkerLock
    from multi_exchange_trader.execution.runtime import NativeExecutionRuntime
    from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook
    from multi_exchange_trader.execution.state_store import RuntimeStateStore
    from multi_exchange_trader.execution.worker import ExecutionWorker, TrustedPlanRegistry

    owner, binding, wall, _mono, requests = session
    book = RuntimePolicyBook(tmp_path / "policy.sqlite3")
    lock = AccountWorkerLock(tmp_path, account_id="12345", environment="DEMO")
    lock.acquire()
    scope = ExecutionScope("12345", "DEMO")
    journal, store = (
        CommandJournal(tmp_path / "execution.sqlite3", scope),
        RuntimeStateStore(tmp_path / "runtime-state.sqlite3", scope),
    )
    strategy = SimpleNamespace(trader_id="TRADER-001", id="Strategy-000")
    bridge = NativeBybitBridge(
        scope=SCOPE,
        native_account_id="BYBIT-UNIFIED",
        factory=object(),
        strategy=strategy,
        bindings=NativeBindings(str, str, str, str, str),
    )
    runtime = NativeExecutionRuntime(
        journal=journal,
        store=store,
        worker_lock=lock,
        lock_directory=tmp_path,
        bridge=bridge,
        entry_risk=lambda *args: None,
        clock=lambda: wall[0],
        max_evidence_age=timedelta(seconds=30),
        receipt_session=None,
    )
    assembly = NativeNodeAssembly(
        SimpleNamespace(cache=Cache()),
        strategy,
        object(),
        bridge,
        NativeChannel(SCOPE, "BYBIT-UNIFIED"),
    )
    worker = ExecutionWorker(
        directory=tmp_path,
        scope=SCOPE,
        session=owner,
        binding=binding,
        symbols=("BTCUSDT",),
        trader_id="TRADER-001",
        policy_book=book,
        registry=TrustedPlanRegistry(()),
        entry_risk=lambda *args: None,
        clock=lambda: wall[0],
        max_age=timedelta(seconds=30),
    )
    worker._loop, worker._runtime, worker._assembly, worker._lock = (
        asyncio.get_running_loop(),
        runtime,
        assembly,
        lock,
    )
    worker._native_restart_status = WorkerNativeRestartStatus(
        "RESTORED_LOCAL_CACHE", generation_id="restore-1"
    )
    stamp = [StreamStamp(0, "", 0, 0)]
    witness = _issue_monitoring_witness(worker, stream_stamp=lambda: stamp[0], guard=lambda: None)
    target = capture_monitoring_target(
        runtime,
        assembly,
        restore_generation_id="restore-1",
        policy_revision="0",
        witness=witness,
        stream_stamp=stamp[0],
    )
    original = httpx.AsyncClient.send
    blocked, started, release = [False], asyncio.Event(), asyncio.Event()

    async def send(client, request, **kwargs):
        if request.url.path in ("/v5/user/query-api", "/v5/market/time"):
            if blocked[0] and request.url.path == "/v5/market/time":
                started.set()
                await release.wait()
            return await original(client, request, **kwargs)
        requests.append(request)
        rows = (
            [{"symbol": "BTCUSDT", "positionIdx": 0, "side": "", "size": "0"}]
            if request.url.path == "/v5/position/list"
            else []
        )
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "time": int(wall[0].timestamp() * 1000),
                "result": {"category": "linear", "list": rows, "nextPageCursor": ""},
            },
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    yield session, target, worker, stamp, blocked, started, release
    if lock.acquired:
        runtime.shutdown()
    store.close()
    journal.close()
    lock.release()


async def issue(core):
    owner, binding, *_ = core[0]
    assert hasattr(owner, "read_monitoring_receipt")
    return await owner.read_monitoring_receipt(binding, target=core[1], phase="PRE")


async def test_receipt_exact_target_phase_one_shot_and_not_constructible(target_core):
    from dataclasses import replace

    from multi_exchange_trader.execution.monitoring_receipts import AuthenticatedMonitoringReceipt

    core = target_core
    owner, binding, *_ = core[0]
    receipt = await issue(core)
    for bad in (object.__new__(AuthenticatedMonitoringReceipt), object()):
        with pytest.raises(ValueError):
            owner.require_monitoring_receipt(bad, binding=binding, target=core[1], phase="PRE")
    for target, phase in ((replace(core[1]), "PRE"), (core[1], "POST")):
        with pytest.raises(ValueError):
            owner.require_monitoring_receipt(receipt, binding=binding, target=target, phase=phase)
    assert (
        owner.consume_monitoring_receipt(
            receipt, binding=binding, target=core[1], phase="PRE"
        ).assessment.status.value
        == "MATCHED"
    )
    with pytest.raises(ValueError):
        owner.consume_monitoring_receipt(receipt, binding=binding, target=core[1], phase="PRE")


async def test_pending_capacity_and_cancellation_release_slots(target_core):
    core = target_core
    owner, *_ = core[0]
    assert hasattr(owner, "read_monitoring_receipt")
    core[4][0] = True
    tasks = [asyncio.create_task(issue(core)) for _ in range(16)]
    await core[5].wait()
    await asyncio.sleep(0.1)
    try:
        with pytest.raises(ValueError, match="CAPACITY"):
            await issue(core)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert not owner._monitoring_receipts
    core[4][0] = False
    assert await issue(core)


@pytest.mark.parametrize(
    "change", ["expiry", "rollback", "reauth", "close", "key", "channel", "generation", "lease"]
)
async def test_revocation_and_owner_changes_invalidate_receipt(target_core, change):
    from dataclasses import replace

    core = target_core
    owner, binding, wall, mono, *_ = core[0]
    receipt = await issue(core)
    if change == "expiry":
        wall[0] += timedelta(seconds=31)
        mono[0] += 31
    elif change == "rollback":
        mono[0] -= 1
    elif change == "reauth":
        await owner.authenticate(
            expected_uid="12345", expected_parent_uid="0", expected_is_master=False
        )
    elif change == "close":
        await owner.aclose()
    elif change == "key":
        owner._client._api_key = "replaced"
    elif change == "channel":
        core[2]._assembly.channel = replace(core[2]._assembly.channel)
    elif change == "generation":
        core[2]._native_restart_status = replace(
            core[2]._native_restart_status, generation_id="other"
        )
    else:
        core[2]._lock.release()
    with pytest.raises(ValueError):
        owner.require_monitoring_receipt(receipt, binding=binding, target=core[1], phase="PRE")
    owner.discard_monitoring_receipt(receipt)


async def test_concurrent_refresh_cannot_reuse_prior_handle(session):
    owner, binding, *_ = session
    results = await asyncio.gather(
        *(owner.refresh_monitoring_binding(binding, scope=SCOPE) for _ in range(2)),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ValueError) for result in results) == 1


async def test_binding_expiry_cannot_be_undone_by_clock_reset(target_core):
    owner, binding, wall, mono, *_ = target_core[0]
    receipt = await issue(target_core)
    wall[0] += timedelta(seconds=31)
    mono[0] += 31
    with pytest.raises(ValueError):
        owner.require_observation_binding(binding, scope=SCOPE)
    wall[0], mono[0] = NOW, 10.0
    with pytest.raises(ValueError):
        owner.require_monitoring_receipt(
            receipt, binding=binding, target=target_core[1], phase="PRE"
        )


async def test_pre_stamp_does_not_authorize_actual_connected_post(target_core):
    from multi_exchange_trader.execution.monitoring_models import StreamStamp
    from multi_exchange_trader.execution.monitoring_reconciliation import capture_monitoring_target

    core = target_core
    owner, binding, *_ = core[0]
    pre = await issue(core)
    core[3][0] = StreamStamp(1, "connection-1", 0, 0)
    worker = core[2]
    post_target = capture_monitoring_target(
        worker.runtime,
        worker._assembly,
        restore_generation_id="restore-1",
        policy_revision="0",
        witness=core[1]._witness,
        stream_stamp=core[3][0],
    )
    with pytest.raises(ValueError):
        owner.require_monitoring_receipt(pre, binding=binding, target=post_target, phase="POST")
    post = await owner.read_monitoring_receipt(binding, target=post_target, phase="POST")
    assert owner.consume_monitoring_receipt(post, binding=binding, target=post_target, phase="POST")
    owner.discard_monitoring_receipt(pre)


async def test_refresh_and_reads_share_physical_budget(session):
    from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget

    owner, binding, *_ = session
    budget = MonitoringReadBudget()
    with owner._monitoring_operation(budget):
        fresh = await owner.refresh_monitoring_binding(binding, scope=SCOPE)
        await owner.read_client(fresh, scope=SCOPE).read("/v5/market/time", [], signed=False)
    assert budget.physical_attempts == 3
    assert owner._client._operation.get() is None


async def test_copied_witness_cannot_capture_authority(target_core):
    from dataclasses import replace

    from multi_exchange_trader.execution.monitoring_reconciliation import capture_monitoring_target

    core = target_core
    with pytest.raises(ValueError):
        capture_monitoring_target(
            core[2].runtime,
            core[2]._assembly,
            restore_generation_id="restore-1",
            policy_revision="0",
            witness=replace(core[1]._witness),
            stream_stamp=core[3][0],
        )


@pytest.mark.parametrize("session", [5], indirect=True)
async def test_shorter_binding_ttl_independently_wins(target_core):
    owner, binding, wall, mono, *_ = target_core[0]
    receipt = await issue(target_core)
    wall[0] += timedelta(seconds=6)
    mono[0] += 6
    with pytest.raises(ValueError):
        owner.require_monitoring_receipt(
            receipt, binding=binding, target=target_core[1], phase="PRE"
        )


async def test_public_injected_transport_cannot_issue_monitoring_receipt(target_core):
    def respond(request):
        result = (
            {}
            if request.url.path == "/v5/market/time"
            else {
                "apiKey": "synthetic-key",
                "secret": "",
                "userID": 12345,
                "isMaster": False,
                "parentUid": "0",
                "readOnly": 1,
                "permissions": {"Wallet": []},
            }
        )
        return httpx.Response(200, json={"retCode": 0, "time": MS, "result": result})

    owner = BybitDemoAccountSession(
        api_key="synthetic-key",
        api_secret="synthetic-secret",
        clock=lambda: NOW,
        transport=httpx.MockTransport(respond),
    )
    try:
        binding = await owner.authenticate(
            expected_uid="12345", expected_parent_uid="0", expected_is_master=False
        )
        with pytest.raises(ValueError, match="TRANSPORT"):
            await owner.read_monitoring_receipt(binding, target=target_core[1], phase="PRE")
        assert not owner._monitoring_receipts
    finally:
        await owner.aclose()


async def test_native_cache_mutation_invalidates_previously_issued_target(target_core):
    from test_native_restart_codec import fixture

    core = target_core
    owner, binding, *_ = core[0]
    receipt = await issue(core)
    native = fixture("ACCEPTED")
    core[2]._assembly.node.cache.add_instrument(native.instrument)
    core[2]._assembly.node.cache.add_order(native.order)
    with pytest.raises(ValueError):
        owner.require_monitoring_receipt(receipt, binding=binding, target=core[1], phase="PRE")


async def test_monitoring_installer_failure_does_not_retain_credential_exception(session):
    owner, binding, *_ = session

    def fail(config):
        raise RuntimeError("synthetic-secret")

    with pytest.raises(ValueError) as failure:
        owner.configure_native_monitoring(binding, scope=SCOPE, install=fail)
    assert failure.value.__context__ is None
    assert "synthetic" not in str(failure.value)
