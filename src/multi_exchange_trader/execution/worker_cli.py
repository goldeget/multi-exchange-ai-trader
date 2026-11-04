"""Dormant-by-default headless DEMO monitor. No strategy loading or order activation.

``python -m multi_exchange_trader.execution.worker_cli`` only prints fixed blockers.
Credentials are read solely inside the explicitly requested --run-monitor path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys as _sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn

from ..account.config import require_demo_credentials
from ..credential_resolution import CredentialResolver as _CredentialResolver
from ..monitoring_status import NativeMonitorPublisher
from ..worker_account_selection import resolve_worker_expectations
from .account_binding import BybitDemoAccountSession
from .model_observation_phase_v2 import ModelObservationPhaseV2
from .observation_instrument_v1 import ACTUAL_ACCOUNT_FEE_V1, SDK1231_OBSERVATION_UNKNOWN_FEES_V1
from .protection import ProtectionScope
from .risk import EntryDecision, ReservationTotals
from .runtime import QUALIFICATION_BLOCKERS, RuntimeBlocked
from .runtime_policy import RuntimePolicyBook
from .worker import LEGACY_JOURNAL_BLOCKER, ExecutionWorker, TrustedPlanRegistry

# Keep the existing CLI adapter module attributes explicit for typed callers.
sys = _sys
CredentialResolver = _CredentialResolver

# Configured validation stays bound to the genuine class, independently of the
# passive constructor seam used by callers and offline integrations.
_load_shared_policy_target_v2 = RuntimePolicyBook._from_shared_target_v2


def _no_entry_risk(_plan: EntryDecision, _now: datetime,
                   _reservations: ReservationTotals) -> EntryDecision:
    raise RuntimeBlocked("MONITOR_ONLY_NO_APPROVED_RISK_PRODUCER")


def install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    """Linux service termination schedules orderly shutdown on the database owner loop."""
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)


async def serve_monitor(worker: ExecutionWorker, *, stop: asyncio.Event,
                        refresh: Callable[[], Awaitable[None]],
                        interval: float = 10.0, connect: bool = False,
                        on_started: Callable[[], None] | None = None,
                        on_stopped: Callable[[], None] | None = None,
                        on_status: Callable[[], None] | None = None,
                        model_phase: ModelObservationPhaseV2 | None = None,
                        on_phase_stopped: Callable[[], None] | None = None) -> None:
    """Run passive GET polling or supervise one explicitly admitted native stream."""
    try:
        await worker.start_passive()
        # TradingNode construction replaces asyncio's signal handlers.
        if on_started is not None:
            on_started()
        if model_phase is not None:
            if model_phase.worker is not worker or not connect:
                raise RuntimeBlocked("MODEL_OBSERVATION_CONNECTED_OWNER_REQUIRED")
            async for event in model_phase.enter(stop):
                worker = model_phase.worker
                if event == "OLD_STOPPED":
                    if on_phase_stopped is not None:
                        on_phase_stopped()
                elif event == "NEW_STARTED":
                    if on_started is not None:
                        on_started()
                elif not stop.is_set() and on_status is not None:
                    on_status()
            worker = model_phase.worker
        elif connect and not stop.is_set():
            await worker.connect_monitoring()
        while not stop.is_set():
            if connect and worker.monitoring_status.phase != "MONITORING":
                raise RuntimeBlocked("NATIVE_MONITORING_RETIRED")
            if on_status is not None:
                on_status()
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                # Reauthentication revokes an admitted stream's exact provenance.
                # Connected observation keeps that owner and lets REST authority expire.
                if not connect:
                    try:
                        await refresh()
                        for position in worker.runtime.store.list_positions():
                            if stop.is_set():
                                break
                            await worker.poll_position(position.position_id)
                    except Exception:  # noqa: BLE001 - provider bodies never reach daemon logs.
                        worker.runtime.pause_entries()
            if not stop.is_set() and worker._node_task is not None and worker._node_task.done():
                raise RuntimeBlocked("NATIVE_MONITORING_ENDED")
    except (ValueError, RuntimeError):
        if model_phase is None or not stop.is_set():
            raise
    finally:
        if model_phase is None:
            await worker.stop()
        else:
            await model_phase.stop_current()
        if on_stopped is not None:
            on_stopped()


async def _run(args: argparse.Namespace) -> None:
    if getattr(args, "model_observation", False) and not args.connect_monitoring:
        raise RuntimeBlocked("MODEL_OBSERVATION_CONNECTED_OWNER_REQUIRED")
    # Validate a present shared target before any credential/account work. A
    # missing target keeps the old passive constructor at its original point.
    explicit_policy_path = getattr(args, "policy_db", None)
    shared_policy = _load_shared_policy_target_v2(
        Path(explicit_policy_path) if explicit_policy_path is not None else None)
    # Resolve and freeze provenance before credentials. This private immutable
    # context stays alive for the worker and every refresh; files never hot-switch it.
    expectations = resolve_worker_expectations(uid=args.uid, parent_uid=args.parent_uid,
        is_master=args.is_master, account_profile=getattr(args, "account_profile", None),
        profile_store=getattr(args, "profile_store", None), environ=os.environ)
    context = CredentialResolver.from_environment(environ=os.environ)
    key, secret = require_demo_credentials(resolver=context)
    session = BybitDemoAccountSession(api_key=key, api_secret=secret)
    serve_attempted, stop_complete = False, False
    publisher: NativeMonitorPublisher | None = None
    model_phase: ModelObservationPhaseV2 | None = None

    def current_worker() -> ExecutionWorker:
        return worker if model_phase is None else model_phase.worker

    def publish_status(state: str | None = None) -> None:
        nonlocal publisher
        try:
            if publisher is None:
                publisher = NativeMonitorPublisher(Path(args.directory), expectations, current_worker(),
                                                   connected=args.connect_monitoring)
            if state == "STOPPED":
                publisher.finish("STOPPED")
            elif state == "CLEANUP_UNCONFIRMED":
                publisher.finish("CLEANUP_UNCONFIRMED")
            else:
                publisher.observe()
        except Exception:  # noqa: BLE001 - optional UI observations never change worker cleanup.
            return

    def phase_stopped() -> None:
        nonlocal publisher
        # The old lease is released here, before another worker is constructed.
        publish_status("STOPPED")
        publisher = None

    def mark_stopped() -> None:
        nonlocal stop_complete
        stop_complete = True
        publish_status("STOPPED")

    try:
        # Authenticate directly for the typed opaque binding; no caller flags can create it.
        binding = await session.authenticate(expected_uid=expectations._uid,
            expected_parent_uid=expectations._parent_uid, expected_is_master=expectations._is_master)
        worker = ExecutionWorker(directory=Path(args.directory),
            lock_directory=Path(args.lock_directory) if args.lock_directory is not None else None,
            scope=ProtectionScope(expectations._uid, "DEMO"), session=session, binding=binding,
            symbols=tuple(args.symbol), trader_id="MONITOR-001",
            policy_book=shared_policy if shared_policy is not None else RuntimePolicyBook(Path(args.policy_db)),
            registry=TrustedPlanRegistry(()),
            entry_risk=_no_entry_risk, clock=lambda: datetime.now(UTC),
            max_age=timedelta(seconds=30),
            metadata_profile=getattr(args, "observation_fee_profile", ACTUAL_ACCOUNT_FEE_V1))
        if getattr(args, "model_observation", False):
            model_phase = ModelObservationPhaseV2(worker)

        async def refresh() -> None:
            await current_worker()._refresh_binding_v2(expected_uid=expectations._uid,
                expected_parent_uid=expectations._parent_uid,
                expected_is_master=expectations._is_master)

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        install_signal_handlers(loop, stop)

        def started() -> None:
            install_signal_handlers(loop, stop)
            if not stop.is_set():
                publish_status()

        try:
            serve_attempted = True
            if model_phase is None:
                await serve_monitor(worker, stop=stop, refresh=refresh, connect=args.connect_monitoring,
                                    on_started=started, on_stopped=mark_stopped,
                                    on_status=publish_status)
            else:
                await serve_monitor(worker, stop=stop, refresh=refresh, connect=True,
                                    on_started=started, on_stopped=mark_stopped, on_status=publish_status,
                                    model_phase=model_phase, on_phase_stopped=phase_stopped)
        finally:
            if stop_complete:
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.remove_signal_handler(sig)
    finally:
        if serve_attempted and not stop_complete:
            # The genuine stop may retain its sink/stores/lease and native tasks.
            # Keep their Session and owner loop alive; no automatic stop retry or
            # restart seal is inferred from a failed or interrupted await.
            print(json.dumps({"mode": "MONITOR_ONLY", "orders_enabled": False,
                "error": "WORKER_STOP_UNCONFIRMED_RETAINS_RESOURCES"}),
                file=sys.stderr, flush=True)
            publish_status("CLEANUP_UNCONFIRMED")
            while True:
                await asyncio.sleep(10)
                publish_status("CLEANUP_UNCONFIRMED")
        else:
            try:
                if model_phase is not None:
                    await model_phase.close_sources()
            finally:
                await session.aclose()


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse otherwise includes raw values/paths in unknown-option/choice errors.
        super().error("INVALID_ARGUMENTS")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _SafeParser(description="DEMO monitor only; native trading stays blocked")
    parser.add_argument("--run-monitor", action="store_true")
    parser.add_argument("--connect-monitoring", action="store_true")
    parser.add_argument("--model-observation", action="store_true",
                        help="explicit source/model observation; orders remain blocked")
    parser.add_argument("--observation-fee-profile", choices=(ACTUAL_ACCOUNT_FEE_V1,
        SDK1231_OBSERVATION_UNKNOWN_FEES_V1), default=ACTUAL_ACCOUNT_FEE_V1)
    parser.add_argument("--uid")
    parser.add_argument("--parent-uid")
    parser.add_argument("--is-master", choices=("true", "false"))
    parser.add_argument("--account-profile", metavar="UUID")
    parser.add_argument("--profile-store", metavar="ABSOLUTE_PATH")
    parser.add_argument("--symbol", action="append")
    parser.add_argument("--directory")
    parser.add_argument("--lock-directory",
                        help="separate private lease directory required for the backup layout")
    parser.add_argument("--policy-db")
    args = parser.parse_args(argv)
    if args.model_observation and (not args.run_monitor or not args.connect_monitoring):
        parser.error("model observation requires an explicit connected monitor")
    if not args.run_monitor:
        if args.connect_monitoring:
            parser.error("--connect-monitoring requires explicit --run-monitor")
        print(json.dumps({"mode": "DRY_CHECK", "orders_enabled": False,
                          "blockers": QUALIFICATION_BLOCKERS}))
        return 0
    if sys.platform != "linux":
        parser.error("monitor requires the pinned Linux runtime")
    if any(getattr(args, name) is None for name in (
        "symbol", "directory", "policy_db")):
        parser.error("explicit symbols, directory and policy-db are required")
    try:
        asyncio.run(_run(args))
    except Exception as exc:  # noqa: BLE001 - output only allowlisted fixed diagnostics.
        error = (LEGACY_JOURNAL_BLOCKER
                 if isinstance(exc, RuntimeBlocked) and str(exc) == LEGACY_JOURNAL_BLOCKER
                 else "WORKER_STOPPED_REQUIRES_REVIEW")
        print(json.dumps({"mode": "MONITOR_ONLY", "error": error}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
