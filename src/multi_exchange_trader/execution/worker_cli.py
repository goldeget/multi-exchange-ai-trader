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
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn

from ..account.config import require_demo_credentials
from ..credential_resolution import CredentialResolver
from ..worker_account_selection import resolve_worker_expectations
from .account_binding import BybitDemoAccountSession
from .protection import ProtectionScope
from .risk import EntryDecision, ReservationTotals
from .runtime import QUALIFICATION_BLOCKERS, RuntimeBlocked
from .runtime_policy import RuntimePolicyBook
from .worker import LEGACY_JOURNAL_BLOCKER, ExecutionWorker, TrustedPlanRegistry


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
                        on_started: Callable[[], None] | None = None) -> None:
    """Run passive GET polling or supervise one explicitly admitted native stream."""
    await worker.start_passive()
    try:
        # TradingNode construction replaces asyncio's signal handlers.
        if on_started is not None:
            on_started()
        if connect and not stop.is_set():
            await worker.connect_monitoring()
        while not stop.is_set():
            if connect and worker.monitoring_status.phase != "MONITORING":
                raise RuntimeBlocked("NATIVE_MONITORING_RETIRED")
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
    finally:
        await worker.stop()


async def _run(args: argparse.Namespace) -> None:
    # Resolve and freeze provenance before credentials. This private immutable
    # context stays alive for the worker and every refresh; files never hot-switch it.
    expectations = resolve_worker_expectations(uid=args.uid, parent_uid=args.parent_uid,
        is_master=args.is_master, account_profile=getattr(args, "account_profile", None),
        profile_store=getattr(args, "profile_store", None), environ=os.environ)
    context = CredentialResolver.from_environment(environ=os.environ)
    key, secret = require_demo_credentials(resolver=context)
    session = BybitDemoAccountSession(api_key=key, api_secret=secret)
    try:
        # Authenticate directly for the typed opaque binding; no caller flags can create it.
        binding = await session.authenticate(expected_uid=expectations._uid,
            expected_parent_uid=expectations._parent_uid, expected_is_master=expectations._is_master)
        worker = ExecutionWorker(directory=Path(args.directory),
            lock_directory=Path(args.lock_directory) if args.lock_directory is not None else None,
            scope=ProtectionScope(expectations._uid, "DEMO"), session=session, binding=binding,
            symbols=tuple(args.symbol), trader_id="MONITOR-001",
            policy_book=RuntimePolicyBook(Path(args.policy_db)), registry=TrustedPlanRegistry(()),
            entry_risk=_no_entry_risk, clock=lambda: datetime.now(UTC),
            max_age=timedelta(seconds=30))

        async def refresh() -> None:
            worker.binding = await session.authenticate(expected_uid=expectations._uid,
                expected_parent_uid=expectations._parent_uid,
                expected_is_master=expectations._is_master)

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        install_signal_handlers(loop, stop)
        try:
            await serve_monitor(worker, stop=stop, refresh=refresh, connect=args.connect_monitoring,
                                on_started=lambda: install_signal_handlers(loop, stop))
        finally:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)
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
