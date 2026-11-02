"""Default command is inert; explicit monitor lifecycle uses isolated fakes only."""

import asyncio
import json
import signal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_worker import harness

from multi_exchange_trader.execution import worker_cli
from multi_exchange_trader.execution.ownership import AccountWorkerLock


def test_default_dry_check_never_reads_credentials_or_starts_worker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("default must remain local and inert")

    monkeypatch.setattr(worker_cli, "os", SimpleNamespace(environ=SimpleNamespace(get=forbidden)))
    monkeypatch.setattr(worker_cli, "_run", forbidden)
    assert worker_cli.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "DRY_CHECK" and result["orders_enabled"] is False
    assert "NATIVE_WORKER_QUALIFICATION_REQUIRED" in result["blockers"]


def test_connect_flag_without_run_is_rejected_before_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_cli, "_run", lambda _args: pytest.fail("must not run"))
    with pytest.raises(SystemExit):
        worker_cli.main(["--connect-monitoring"])


async def test_signal_shutdown_releases_actual_sqlite_owner_lease(tmp_path: Path) -> None:
    h = harness(tmp_path)
    stop = asyncio.Event()
    handlers: dict[int, Any] = {}
    worker_cli.install_signal_handlers(SimpleNamespace(
        add_signal_handler=lambda sig, handler: handlers.update({sig: handler})), stop)

    async def refresh() -> None:
        handlers[signal.SIGTERM]()

    await worker_cli.serve_monitor(h.worker, stop=stop, refresh=refresh, interval=0.001)
    assert h.worker._stopped
    lock = AccountWorkerLock(tmp_path, account_id=h.worker.scope.account_id, environment="DEMO")
    lock.acquire()
    lock.release()
    assert not h.assembly.strategy.commands


async def test_monitor_connect_is_explicit_and_shutdown_stops_native_producer(tmp_path: Path) -> None:
    h = harness(tmp_path)
    stop = asyncio.Event()
    native_stop = asyncio.Event()
    calls: list[str] = []

    async def run() -> None:
        calls.append("run")
        stop.set()
        await native_stop.wait()

    async def shutdown() -> None:
        calls.append("stop")
        native_stop.set()

    async def refresh() -> None:
        pytest.fail("not needed before stop")

    h.assembly.node = SimpleNamespace(run_async=run, stop_async=shutdown)
    await worker_cli.serve_monitor(h.worker, stop=stop, refresh=refresh, connect=True)
    assert calls == ["run", "stop"]
    assert not h.assembly.strategy.commands
