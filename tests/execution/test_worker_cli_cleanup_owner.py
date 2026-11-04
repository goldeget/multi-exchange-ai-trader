"""CLI shutdown must retain the actual worker's owner when native stop fails."""

import argparse
import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from multi_exchange_trader.execution import worker_cli
from multi_exchange_trader.execution.ownership import AccountWorkerLock, WorkerAlreadyRunning


@pytest.mark.parametrize("fail_stop", [False, True])
async def test_cli_closes_session_only_after_original_worker_stop_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_stop: bool,
) -> None:
    # Genuine Worker/SQLite/lease, existing synthetic native/account boundaries.
    h: Any = importlib.import_module("test_worker").harness(tmp_path)
    h.core.permit_fake_monitoring()
    events: list[str] = []
    stops: list[asyncio.Event] = []
    finished, stop_called, retained = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_close = h.session.aclose
    loop = asyncio.get_running_loop()

    async def authenticate(**_: object) -> Any:
        return h.binding

    async def close() -> None:
        events.append("session-close")
        await original_close()

    async def run() -> None:
        stops[-1].set()
        await finished.wait()

    async def shutdown() -> None:
        events.append("producer-stop")
        stop_called.set()
        if fail_stop:
            raise RuntimeError("synthetic native disconnect incomplete")
        finished.set()

    def install(_loop: Any, stop: asyncio.Event) -> None:
        stops.append(stop)

    def remove(_signal: int) -> bool:
        events.append("remove-handler")
        return True

    def diagnostic(message: str, **_: object) -> None:
        assert json.loads(message) == {"mode": "MONITOR_ONLY", "orders_enabled": False,
            "error": "WORKER_STOP_UNCONFIRMED_RETAINS_RESOURCES"}
        retained.set()

    monkeypatch.setattr(h.session, "authenticate", authenticate)
    monkeypatch.setattr(h.session, "aclose", close)
    monkeypatch.setattr(worker_cli, "BybitDemoAccountSession", lambda **_: h.session)
    monkeypatch.setattr(worker_cli, "ExecutionWorker", lambda **_: h.worker)
    monkeypatch.setattr(worker_cli, "_load_shared_policy_target_v2", lambda _: None)
    monkeypatch.setattr(worker_cli, "resolve_worker_expectations", lambda **_: SimpleNamespace(
        _uid=h.worker.scope.account_id, _parent_uid="0", _is_master=False))
    monkeypatch.setattr(worker_cli.CredentialResolver, "from_environment", lambda **_: object())
    monkeypatch.setattr(worker_cli, "require_demo_credentials", lambda **_: ("synthetic", "synthetic"))
    monkeypatch.setattr(worker_cli, "install_signal_handlers", install)
    monkeypatch.setattr(worker_cli, "print", diagnostic, raising=False)
    monkeypatch.setattr(loop, "remove_signal_handler", remove)
    h.assembly.node = SimpleNamespace(run_async=run, stop_async=shutdown)
    args = argparse.Namespace(uid=None, parent_uid=None, is_master=None, symbol=["BTCUSDT"],
        directory=str(tmp_path), lock_directory=None, policy_db=str(h.book.path),
        connect_monitoring=True)
    task = asyncio.create_task(worker_cli._run(args))
    try:
        await asyncio.wait_for(stop_called.wait(), 2)
        if fail_stop:
            # This exact synchronous diagnostic is immediately before the
            # retaining await. Cancelling sooner could enter retention itself.
            await asyncio.wait_for(retained.wait(), 2)
            assert not task.done(), "An incomplete producer must retain its original owner loop"
            assert "session-close" not in events
            assert "remove-handler" not in events
            assert events.count("producer-stop") == 1
            assert not h.worker._stopped
            other = AccountWorkerLock(tmp_path, account_id=h.worker.scope.account_id,
                                      environment="DEMO")
            with pytest.raises(WorkerAlreadyRunning):
                other.acquire()
        else:
            await asyncio.wait_for(task, 2)
            assert not retained.is_set()
            assert h.worker._stopped
            assert events == ["producer-stop", "remove-handler", "remove-handler", "session-close"]
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, RuntimeError):
            pass
        if not h.worker._stopped:
            async def finish_shutdown() -> None:
                finished.set()

            h.assembly.node.stop_async = finish_shutdown
            await h.worker.stop()
        await original_close()

async def test_clean_startup_lease_conflict_reports_completed_cleanup_without_stopping_owner(
    tmp_path: Path,
) -> None:
    harness: Any = importlib.import_module("test_worker").harness
    locks = tmp_path / "locks"
    locks.mkdir(mode=0o700)
    owner = harness(tmp_path / "owner", lock_directory=locks)
    contender = harness(tmp_path / "contender", lock_directory=locks)
    await owner.worker.start_passive()
    completed: list[bool] = []

    async def refresh() -> None:
        pytest.fail("A lease conflict cannot start monitoring")

    try:
        with pytest.raises(WorkerAlreadyRunning):
            await worker_cli.serve_monitor(contender.worker, stop=asyncio.Event(), refresh=refresh,
                                          on_stopped=lambda: completed.append(True))
        assert completed == [True]
        assert contender.worker._stopped
        assert contender.worker._assembly is None and contender.worker._journal is None
        assert owner.worker._lock.acquired and not owner.worker._stopped
    finally:
        await owner.worker.stop()
        await owner.session.aclose()
        await contender.session.aclose()


async def test_failed_startup_cleanup_cannot_publish_a_stopped_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h: Any = importlib.import_module("test_worker").harness(tmp_path)
    original_cleanup = h.worker._close_stores

    def fail_start(_sink: Any) -> Any:
        raise RuntimeError("synthetic startup failed")

    def fail_cleanup() -> None:
        raise RuntimeError("synthetic startup cleanup incomplete")

    monkeypatch.setattr(h.worker, "_factory", fail_start)
    monkeypatch.setattr(h.worker, "_close_stores", fail_cleanup)
    try:
        with pytest.raises(RuntimeError, match="synthetic startup cleanup incomplete"):
            await h.worker.start_passive()
        assert not h.worker._stopped
        assert h.worker._lock.acquired
        other = AccountWorkerLock(tmp_path, account_id=h.worker.scope.account_id, environment="DEMO")
        with pytest.raises(WorkerAlreadyRunning):
            other.acquire()
    finally:
        original_cleanup()
        await h.session.aclose()
