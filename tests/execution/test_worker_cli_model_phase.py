"""CLI resource ownership only; native phase admission is tested separately on pinned Linux."""
import argparse
import asyncio
import importlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_bybit_native import FakeFactory, FakeStrategy

from multi_exchange_trader.account.bybit_public_mark_source_v1 import BybitPublicMarkSessionV1
from multi_exchange_trader.execution import worker_cli
from multi_exchange_trader.execution.bybit_native import NativeBindings, NativeBybitBridge
from multi_exchange_trader.execution.model_observation_phase_v2 import ModelObservationPhaseV2
from multi_exchange_trader.execution.native_node import NativeChannel, NativeNodeAssembly
from multi_exchange_trader.execution.runtime import RuntimeBlocked
from multi_exchange_trader.execution.worker import ExecutionWorker
from multi_exchange_trader.market_data.instrument_producer import BybitInstrumentCatalogProducer


@pytest.mark.parametrize("fail_new_stop", [False, True])
async def test_cli_tracks_replacement_session_public_sources_and_signal_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_new_stop: bool,
) -> None:
    h: Any = importlib.import_module("test_worker").harness(tmp_path)
    phase = ModelObservationPhaseV2(h.worker)
    events: list[str] = []
    stops: list[asyncio.Event] = []
    retained = asyncio.Event()
    loop = asyncio.get_running_loop()
    session_close = h.session.aclose
    replacement: ExecutionWorker | None = None
    replacement_stop: Any = None
    source_closes: list[Any] = []

    async def authenticate(**_kwargs: Any) -> Any:
        return h.binding

    async def close_session() -> None:
        events.append("session-close")
        await session_close()

    def install(_loop: Any, stop: asyncio.Event) -> None:
        events.append("signal-install")
        stops.append(stop)

    def remove(_signal: int) -> bool:
        events.append("signal-remove")
        return True

    def publish(_directory: Any, _expectations: Any, worker: Any, **_kwargs: Any) -> Any:
        label = "old" if worker is h.worker else "new"
        events.append("publisher-" + label)
        return SimpleNamespace(observe=lambda: events.append(label + "-OBSERVE"),
            finish=lambda state: events.append(label + "-" + state))

    async def lifetime_only(stop: asyncio.Event) -> AsyncIterator[str]:
        nonlocal replacement, replacement_stop
        await phase.stop_current()  # Actual old Worker.stop; no public completion flag.
        events.append("old-stop-returned")
        yield "OLD_STOPPED"
        strategy = FakeStrategy()
        bridge = NativeBybitBridge(scope=h.worker.scope, native_account_id="BYBIT-UNIFIED",
            factory=FakeFactory(), strategy=strategy, bindings=NativeBindings(str, str, str, str, str))
        assembly = NativeNodeAssembly(SimpleNamespace(), strategy, object(), bridge,
            NativeChannel(h.worker.scope, "BYBIT-UNIFIED"))
        replacement = ExecutionWorker(directory=h.worker.directory, lock_directory=h.worker.lock_directory,
            session=h.session, binding=h.binding, scope=h.worker.scope, symbols=h.worker.symbols,
            trader_id=h.worker.trader_id, policy_book=h.book, registry=h.worker.registry,
            entry_risk=h.worker.entry_risk, clock=h.worker.clock, max_age=h.worker.max_age,
            assembly_factory=lambda _sink: assembly)
        phase.worker = replacement  # Synthetic lifecycle seam only; not phase/finance admission.
        phase._stop_attempted = phase._stop_complete = False
        await replacement.start_passive()
        replacement_stop = replacement.stop
        phase._marks = BybitPublicMarkSessionV1(source_environment="PUBLIC_MAINNET", symbols=("BTCUSDT",))
        phase._catalog = BybitInstrumentCatalogProducer(source_environment="PUBLIC_MAINNET")
        for label, source in (("catalog-close", phase._catalog), ("marks-close", phase._marks)):
            original = source.aclose
            source_closes.append(original)

            async def closed(name: str = label, actual: Any = original) -> None:
                events.append(name)
                await actual()

            monkeypatch.setattr(source, "aclose", closed)

        async def stop_replacement() -> None:
            events.append("new-stop-attempt")
            if fail_new_stop:
                raise RuntimeBlocked("TEST_NEW_STOP_UNCONFIRMED")
            await replacement_stop()
            events.append("new-stop-returned")

        monkeypatch.setattr(replacement, "stop", stop_replacement)
        yield "NEW_STARTED"
        stop.set()  # Same asyncio.Event used by actual CLI signal handlers.
        yield "MODEL_OBSERVING"  # Delayed milestone must not produce positive status after signal.

    def diagnostic(message: str, **_kwargs: Any) -> None:
        assert json.loads(message)["error"] == "WORKER_STOP_UNCONFIRMED_RETAINS_RESOURCES"
        retained.set()

    monkeypatch.setattr(phase, "enter", lifetime_only)
    monkeypatch.setattr(h.session, "authenticate", authenticate)
    monkeypatch.setattr(h.session, "aclose", close_session)
    monkeypatch.setattr(worker_cli, "BybitDemoAccountSession", lambda **_kwargs: h.session)
    monkeypatch.setattr(worker_cli, "ExecutionWorker", lambda **_kwargs: h.worker)
    monkeypatch.setattr(worker_cli, "ModelObservationPhaseV2", lambda worker: phase)
    monkeypatch.setattr(worker_cli, "_load_shared_policy_target_v2", lambda _path: None)
    monkeypatch.setattr(worker_cli, "resolve_worker_expectations", lambda **_kwargs: SimpleNamespace(
        _uid=h.worker.scope.account_id, _parent_uid="0", _is_master=False))
    monkeypatch.setattr(worker_cli.CredentialResolver, "from_environment", lambda **_kwargs: object())
    monkeypatch.setattr(worker_cli, "require_demo_credentials", lambda **_kwargs: ("synthetic", "synthetic"))
    monkeypatch.setattr(worker_cli, "NativeMonitorPublisher", publish)
    monkeypatch.setattr(worker_cli, "install_signal_handlers", install)
    monkeypatch.setattr(worker_cli, "print", diagnostic, raising=False)
    monkeypatch.setattr(loop, "remove_signal_handler", remove)
    args = argparse.Namespace(uid=None, parent_uid=None, is_master=None, symbol=["BTCUSDT"],
        directory=str(tmp_path), lock_directory=None, policy_db=str(h.book.path),
        connect_monitoring=True, model_observation=True)
    task = asyncio.create_task(worker_cli._run(args))
    try:
        if fail_new_stop:
            await asyncio.wait_for(retained.wait(), timeout=3)
            assert not task.done() and replacement is not None
            assert replacement._lock is not None and replacement._lock.acquired
            assert not any(event in events for event in ("session-close", "catalog-close", "marks-close", "signal-remove"))
            assert events.count("new-stop-attempt") == 1  # No automatic retry or stale-old-stop proof.
        else:
            await asyncio.wait_for(task, timeout=3)
            assert events.index("new-stop-returned") < events.index("catalog-close") < events.index("session-close")
            assert events.index("new-stop-returned") < events.index("marks-close") < events.index("session-close")
            assert "signal-remove" in events
        assert events.count("signal-install") == 3  # Initial loop + each actual worker start.
        assert events.index("old-STOPPED") < events.index("publisher-new")
        assert events.count("new-OBSERVE") == 1
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        if replacement is not None and not replacement._stopped:
            # Explicit fixture teardown after observing retained production lifetime.
            await replacement_stop()
        for close in source_closes:
            await close()
        await session_close()
