"""One asyncio/SQLite owner, passive recovery, and explicit staged automation.

No import, constructor, policy save, or passive start runs a native connection or
paid generation. Staging is durable intent only; the runtime's fixed qualification
gate still blocks every native write. A deployment/service entrypoint and qualified
strategy/evidence producers remain separate work, not invented here.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Concatenate

from ..ai.dispatch import AIDispatcher
from ..ai.generation import GenerationPriceQuote
from . import modes
from .account_binding import (
    AuthenticatedAccountBinding,
    BybitDemoAccountSession,
)
from .account_ledger import DailyCashBaseline, DailyRealizedLoss, daily_realized_loss
from .execution_credit import ExecutionCreditResult, candidate_identity
from .history import BybitHistoryReader, HistorySnapshot, HistoryWindow
from .history_coverage import IngestionReceipt
from .history_receipts import AuthenticatedHistoryReceipt
from .history_recovery import HistoryRecoveryResult
from .history_store import HistoryStore
from .journal import CommandJournal, CommandRecord, ExecutionScope
from .monitoring_models import WorkerMonitoringStatus
from .monitoring_resume import MonitoringResume
from .native_controls import NativeLeverageControl
from .native_events import NativeEventContext, NativeEventDecoder, NativeOrderEvidence
from .native_node import NativeChannel, NativeNodeAssembly, build_native_node
from .native_restart import NativeRestartCoordinator, WorkerNativeRestartStatus
from .ownership import AccountWorkerLock
from .protection import ProtectionAssessment, ProtectionPolicy, ProtectionScope
from .risk import EntryDecision
from .runtime import NativeExecutionRuntime, RiskCheck, RuntimeBlocked
from .runtime_policy import RuntimePolicyBook
from .state_store import RuntimeStateStore
from .venue_readback import BybitVenueReadback

LEGACY_JOURNAL_BLOCKER = "LEGACY_WORKER_JOURNAL_REQUIRES_OPERATOR_RESTORE"


@dataclass(frozen=True)
class RegisteredPlan:
    plan_id: str
    strategy: modes.StrategyReference
    plan: EntryDecision
    protection: ProtectionPolicy


class TrustedPlanRegistry:
    """Explicit server-owned approved plans; provider text cannot populate this registry."""

    def __init__(self, plans: tuple[RegisteredPlan, ...]) -> None:
        if (type(plans) is not tuple or any(type(p) is not RegisteredPlan for p in plans)
                or len({p.plan_id for p in plans}) != len(plans)):
            raise ValueError("UNIQUE_TRUSTED_PLANS_REQUIRED")
        self._plans = {p.plan_id: p for p in plans}

    def resolve(self, candidate: modes.Candidate) -> RegisteredPlan:
        registered = self._plans.get(candidate.plan_id)
        if registered is None or registered.strategy != candidate.strategy:
            raise RuntimeBlocked("APPROVED_STRATEGY_PLAN_REQUIRED")
        scope = registered.plan.scope
        if ((scope.account_id, scope.environment, scope.symbol, scope.strategy_id,
             registered.plan.policy.revision) != (candidate.account_id, candidate.environment,
             candidate.symbol, candidate.strategy.strategy_id, candidate.policy_revision)):
            raise RuntimeBlocked("TRUSTED_PLAN_CANDIDATE_MISMATCH")
        return registered


AssemblyFactory = Callable[[Callable[[NativeChannel, object], None]], NativeNodeAssembly]


def _admitted[**P, T](
    method: Callable[Concatenate[ExecutionWorker, P], Awaitable[T]],
) -> Callable[Concatenate[ExecutionWorker, P], Awaitable[T]]:
    """Owner-loop admission and lifetime, separate from native callback admission."""
    @wraps(method)
    async def invoke(worker: ExecutionWorker, /, *args: P.args, **kwargs: P.kwargs) -> T:
        worker._require_admission()
        worker._operations += 1
        try:
            return await method(worker, *args, **kwargs)
        finally:
            worker._operations -= 1
    return invoke


async def _settle_effect[T](effect: Coroutine[Any, Any, T]) -> T:
    """Cancellation cannot abandon a native effect or a paid thread's accounting.

    Repeated cancellation requests still wait for the protected task to finish.
    Its result/exception is retrieved before cancellation propagates to the caller.
    """
    task = asyncio.create_task(effect)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:  # noqa: BLE001 - propagate the settled task exception below.
            break  # Retrieve the settled exception below.
    if cancelled:
        if not task.cancelled():
            task.exception()  # Retrieve failure after the effect's durable unknown accounting.
        raise asyncio.CancelledError
    return task.result()


class ExecutionWorker:
    """Explicit lifecycle on one running event loop; injected assembly is test-only.

    Restart means constructing a new worker and a new native channel. Sessions are
    caller-owned so shutdown never closes a session used elsewhere. All DB handles
    are created, used, and closed on the same worker thread.
    """

    def __init__(self, *, directory: Path, scope: ProtectionScope,
                 session: BybitDemoAccountSession, binding: AuthenticatedAccountBinding,
                 symbols: tuple[str, ...], trader_id: str, policy_book: RuntimePolicyBook,
                 registry: TrustedPlanRegistry, entry_risk: RiskCheck,
                 clock: Callable[[], datetime], max_age: timedelta,
                 assembly_factory: AssemblyFactory | None = None,
                 decoder: NativeEventDecoder | None = None,
                 lock_directory: Path | None = None) -> None:
        self.directory, self.scope = directory, scope
        self.lock_directory = directory if lock_directory is None else lock_directory
        self.session, self.binding = session, binding
        self.symbols, self.trader_id = symbols, trader_id
        self.policy_book, self.registry = policy_book, registry
        self.entry_risk, self.clock, self.max_age = entry_risk, clock, max_age
        self._factory, self._decoder = assembly_factory, decoder
        self._loop: asyncio.AbstractEventLoop | None = None
        self._assembly: NativeNodeAssembly | None = None
        self._runtime: NativeExecutionRuntime | None = None
        self._journal: CommandJournal | None = None
        self._store: RuntimeStateStore | None = None
        self._history_store: HistoryStore | None = None
        self._lock: AccountWorkerLock | None = None
        self._node_task: asyncio.Task[None] | None = None
        self._node_stop_task: asyncio.Task[None] | None = None
        self._monitoring: MonitoringResume | None = None
        self._restart: NativeRestartCoordinator | None = None
        self._native_restart_status = WorkerNativeRestartStatus("BLOCKED")
        self._stopped = False
        self._stopping = False
        self._stop_in_progress = False
        self._operations = 0
        self._staging = False
        self._channel_closed = False
        self.callback_failures = 0
        self.rejected_channels = 0

    @property
    def native_restart_status(self) -> WorkerNativeRestartStatus:
        """Diagnostic only; callers cannot turn restoration into connect authority."""
        return self._native_restart_status

    @property
    def runtime(self) -> NativeExecutionRuntime:
        if (self._runtime is None or self._loop is not asyncio.get_running_loop()
                or self._stopped):
            raise RuntimeBlocked("WORKER_OWNER_LOOP_REQUIRED")
        return self._runtime

    @property
    def monitoring_status(self) -> WorkerMonitoringStatus:
        if self._monitoring is not None:
            return self._monitoring.snapshot()
        return WorkerMonitoringStatus("PASSIVE", self.scope, self.symbols)

    def _require_admission(self) -> None:
        _ = self.runtime
        if self._monitoring is not None and self._monitoring.in_progress:
            raise RuntimeBlocked("MONITORING_RESUME_IN_PROGRESS")
        if self._stopping:
            raise RuntimeBlocked("WORKER_STOPPING")

    async def start_passive(self) -> None:
        if self._loop is not None or self._stopped:
            raise RuntimeBlocked("NEW_WORKER_INSTANCE_REQUIRED")
        self._loop = asyncio.get_running_loop()
        try:
            self._lock = AccountWorkerLock(self.lock_directory, account_id=self.scope.account_id,
                                           environment=self.scope.environment)
            self._lock.acquire()
            # Never start a fresh canonical journal beside an older generation.
            # Operators must preserve/reconcile the legacy pair explicitly.
            for name in ("journal.sqlite3", "journal.sqlite3-wal", "journal.sqlite3-shm",
                         "journal.sqlite3-journal"):
                legacy = self.directory / name
                if legacy.exists() or legacy.is_symlink():
                    raise RuntimeBlocked(LEGACY_JOURNAL_BLOCKER)
            if self._factory is None:
                self._assembly = build_native_node(session=self.session, binding=self.binding,
                    scope=self.scope, symbols=self.symbols, trader_id=self.trader_id,
                    on_event=self.post_native, restart_safe=True, monitor_only=True)
            else:
                self._assembly = self._factory(self.post_native)
            channel = self._assembly.channel
            if (channel.scope != self.scope or channel.venue != "BYBIT"
                    or channel.native_account_id != self._assembly.bridge.native_account_id):
                raise RuntimeBlocked("WORKER_NATIVE_CHANNEL_MISMATCH")
            scope = ExecutionScope(self.scope.account_id, self.scope.environment)
            self._journal = CommandJournal(self.directory / "execution.sqlite3", scope)
            self._store = RuntimeStateStore(self.directory / "runtime-state.sqlite3", scope)
            self._history_store = HistoryStore(self.directory / "history.sqlite3", self.scope)
            if self._factory is None:
                self._restart = NativeRestartCoordinator(self.directory, self._assembly,
                    self.scope, self.symbols, self.trader_id)
            else:
                self._native_restart_status = WorkerNativeRestartStatus("TEST_ASSEMBLY")
            self._runtime = NativeExecutionRuntime(journal=self._journal, store=self._store,
                worker_lock=self._lock, lock_directory=self.lock_directory,
                bridge=self._assembly.bridge,
                entry_risk=self.entry_risk, clock=self.clock, max_evidence_age=self.max_age,
                receipt_session=self.session, history_store=self._history_store,
                _pre_recovery=None if self._restart is None else self._restart.restore)
            if self._restart is not None:
                self._native_restart_status = self._restart.status
        except BaseException:
            try:
                if self._assembly is not None and self._factory is None:
                    self._assembly.dispose_local()
            finally:
                self._stopped = True
                self._close_stores()
            raise

    def post_native(self, channel: NativeChannel, event: object) -> None:
        """Foreign native thread entrypoint. Never touch SQLite or TTL from this thread."""
        loop = self._loop
        if loop is not None and not loop.is_closed() and not self._channel_closed:
            try:
                loop.call_soon_threadsafe(self._consume_native, channel, event, time.time_ns())
            except RuntimeError:
                pass  # Loop closed during process shutdown; restart must reconcile.

    def _consume_native(self, channel: NativeChannel, event: object, received_ns: int) -> None:
        # Object identity prevents equal/copied generation records from impersonating a channel.
        if (self._stopped or self._assembly is None or channel is not self._assembly.channel):
            self.rejected_channels += 1
            return
        try:
            if self._monitoring is not None and self._monitoring.armed:
                self._monitoring.require_cleanup_owner()
                self._assembly.require_monitoring_delivery(event)
            evidence = self.runtime.consume_native(event, NativeEventContext(channel.scope,
                channel.native_account_id, received_ns), decoder=self._decoder)
            if self._monitoring is not None and self._monitoring.armed:
                if type(evidence) is not NativeOrderEvidence:
                    raise RuntimeBlocked("MONITORING_APPLICATION_EVIDENCE_REQUIRED")
                self._assembly.acknowledge_monitoring(event, evidence=evidence)
                self._monitoring.event_completed()
        except Exception:  # noqa: BLE001 - keep monitoring alive without leaking provider payload.
            self.callback_failures += 1
            self.runtime.pause_entries()
            if self._monitoring is not None:
                self._monitoring.retire("MONITORING_APPLICATION_CALLBACK_FAILED")

    def _fresh_policy(self, request: modes.SelectionRequest,
                      mode: modes.AutomaticMode) -> None:
        _ = self.runtime  # Enforce owner-loop and lifecycle before any provider work.
        self.session.require_binding(self.binding, scope=self.scope)
        snapshot = self.policy_book.read()
        policy = snapshot["policy"]
        scope = request.scope
        if (snapshot["missing_fields"] or snapshot["desired_state"] != "STOPPED"
                or str(snapshot["revision"]) != request.policy.revision
                or (scope.account_id, scope.environment) != (
                    self.scope.account_id, self.scope.environment)
                or policy["environment"] != self.scope.environment or policy["mode"] != mode.value
                or set(scope.allowed_symbols) != set(policy["allowed_symbols"])
                or {s.strategy_id for s in scope.allowed_strategies}
                    != set(policy["allowed_strategies"])
                or set(scope.selected_symbols) != set(policy["selected_symbols"] or ())
                or {s.strategy_id for s in scope.selected_strategies}
                    != set(policy["selected_strategies"] or ())
                or not set(scope.allowed_symbols) <= set(self.symbols)):
            raise RuntimeBlocked("CURRENT_STOPPED_POLICY_REQUIRED")
        modes.public_candidate_request(request, now=self.clock())
        for candidate in request.candidates:
            registered = self.registry.resolve(candidate)
            if self.policy_book.risk_policy(registered.plan.scope) != registered.plan.policy:
                raise RuntimeBlocked("CURRENT_RISK_POLICY_REQUIRED")

    @_admitted
    async def stage(self, *, command_key: str, position_id: str,
                    mode: modes.AutomaticMode, request: modes.SelectionRequest,
                    priority_candidate_ids: tuple[str, ...] | None = None,
                    dispatcher: AIDispatcher | None = None,
                    quote: GenerationPriceQuote | None = None,
                    maximum_cost_usd: str | None = None) -> CommandRecord | None:
        """Explicit staging; ALL_IN_ONE spending requires dispatcher + quote + ceiling.

        Blocking provider dispatch runs off-loop so fills continue to be persisted.
        Every request is revalidated after that await, including policy revision.
        """
        self._fresh_policy(request, mode)
        if self._staging:
            raise RuntimeBlocked("WORKER_SELECTION_ALREADY_RUNNING")
        self._staging = True
        try:
            response = None
            if mode is modes.AutomaticMode.FULL_AUTO:
                selected = modes.select_full_auto(scope=request.scope, policy=request.policy,
                    snapshot=request.snapshot, candidates=request.candidates, limits=request.limits,
                    priority_candidate_ids=priority_candidate_ids, now=self.clock())
            elif mode is modes.AutomaticMode.ALL_IN_ONE:
                if dispatcher is None or quote is None or maximum_cost_usd is None:
                    raise RuntimeBlocked("EXPLICIT_PAID_DISPATCH_INPUTS_REQUIRED")
                result = await _settle_effect(asyncio.to_thread(dispatcher.dispatch, request,
                    maximum_cost_usd=maximum_cost_usd, quote=quote, enabled=True))
                self._fresh_policy(request, mode)
                if result.failure is not None or result.selection is None:
                    return None
                selected = result.selection
                if selected.ai_selection is None:
                    return None
                response = json.dumps(asdict(selected.ai_selection))
                selected = modes.select_all_in_one(request=request, response=response, now=self.clock())
            else:
                raise RuntimeBlocked("EXPLICIT_AUTOMATIC_MODE_REQUIRED")
            if selected.candidate is None:
                return None
            registered = self.registry.resolve(selected.candidate)
            self._fresh_policy(request, mode)
            return self.runtime.queue_entry(command_key, position_id, plan_id=registered.plan_id,
                plan=registered.plan, protection_policy=registered.protection, mode=mode,
                selection_request=request, ai_response=response,
                priority_candidate_ids=priority_candidate_ids)
        finally:
            self._staging = False

    def _readback(self) -> BybitVenueReadback:
        client = self.session.read_client(self.binding, scope=self.scope)
        return BybitVenueReadback(client=client, scope=self.scope, clock=self.clock,
                                 max_evidence_age=self.max_age)

    @_admitted
    async def resume_restored_monitoring(self) -> WorkerMonitoringStatus:
        return await self._resume_restored_monitoring()

    async def _resume_restored_monitoring(self) -> WorkerMonitoringStatus:
        if self._operations != 1 or self._staging:
            raise RuntimeBlocked("MONITORING_OPERATION_IN_PROGRESS")
        self.runtime.pause_entries()
        controller = MonitoringResume(self)
        self._monitoring = controller
        return await controller.run()

    @_admitted
    async def connect_monitoring(self) -> None:
        """Explicit network start for the passive node, never entry activation.

        The registered strategy has no start/stop commands or external claims.
        This call is not made by app startup, saved policy, or start_passive.
        """
        self.runtime.pause_entries()
        if self.native_restart_status.status == "RESTORED_LOCAL_CACHE":
            await self._resume_restored_monitoring()
            return
        if self._factory is None:
            raise RuntimeBlocked("MONITORING_RESTORED_CACHE_REQUIRED")
        self.session.require_execution_binding(self.binding, scope=self.scope)
        if self._node_task is not None:
            raise RuntimeBlocked("NATIVE_MONITORING_ALREADY_STARTED")
        assert self._assembly is not None and self._loop is not None
        assembly = self._assembly

        async def run() -> None:
            try:
                await assembly.node.run_async()
            finally:
                if not self._stopped:
                    self.runtime.transport_lost()

        self._node_task = self._loop.create_task(run())
        await asyncio.sleep(0)
        if self._node_task.done():
            await self._node_task
            raise RuntimeBlocked("NATIVE_MONITORING_ENDED")

    @_admitted
    async def poll_position(self, position_id: str) -> ProtectionAssessment:
        return await self.runtime.poll_readback(self._readback(), position_id)

    @_admitted
    async def read_history(self, symbol: str, *, start_ms: int, end_ms: int) -> HistorySnapshot:
        _ = self.runtime
        if symbol not in self.symbols:
            raise RuntimeBlocked("WORKER_SYMBOL_SCOPE_REQUIRED")
        reader = BybitHistoryReader(self.session, self.binding, scope=self.scope,
                                    clock=self.clock, max_age=self.max_age)
        return await reader.read_history(symbol, start_ms=start_ms, end_ms=end_ms)

    @_admitted
    async def collect_history(self, symbol: str, *, start_ms: int, end_ms: int) -> IngestionReceipt:
        """Persist one explicit bounded read under the same owner and lease.

        The GET yields to native callbacks. Its result has no storage authority
        after stop, binding change or lease loss, even when that GET succeeds.
        """
        runtime = self.runtime
        _ = runtime.status  # Run the runtime's exact scoped lease guard before GET.
        lock, scope = self._lock, self.scope
        session, binding = self.session, self.binding
        if (lock is None or lock is not runtime.worker_lock
                or runtime.journal.scope != ExecutionScope(scope.account_id, scope.environment)):
            raise RuntimeBlocked("WORKER_HISTORY_OWNER_CHANGED")
        if symbol not in self.symbols:
            raise RuntimeBlocked("WORKER_SYMBOL_SCOPE_REQUIRED")
        window = HistoryWindow(start_ms, end_ms)
        session.require_binding(binding, scope=scope)
        epoch = lock.acquisition_id
        snapshot = await self.read_history(symbol, start_ms=start_ms, end_ms=end_ms)

        # The existing sticky stop fence wins before any history database access.
        self._require_admission()
        if (self.runtime is not runtime or self._lock is not lock
                or lock.acquisition_id != epoch or self.scope != scope
                or self.session is not session or self.binding is not binding):
            raise RuntimeBlocked("WORKER_HISTORY_OWNER_CHANGED")
        _ = runtime.status
        if symbol not in self.symbols:
            raise RuntimeBlocked("WORKER_SYMBOL_SCOPE_REQUIRED")
        session.require_binding(binding, scope=scope)
        if (type(snapshot) is not HistorySnapshot or snapshot.scope != scope
                or snapshot.symbol != symbol or snapshot.window != window):
            raise RuntimeBlocked("WORKER_HISTORY_REQUEST_MISMATCH")
        assert self._history_store is not None
        # No await or thread handoff between final guards and the atomic commit.
        return self._history_store.ingest(snapshot)

    @asynccontextmanager
    async def _history_accounting_receipt(self, symbol: str, *, start_ms: int,
            end_ms: int) -> AsyncIterator[tuple[NativeExecutionRuntime,
                AuthenticatedHistoryReceipt, AuthenticatedAccountBinding,
                HistorySnapshot, IngestionReceipt, Callable[[], None]]]:
        """One captured issuer, exact snapshot, and sticky owner fences for accounting."""
        runtime = self.runtime
        lock, scope = self._lock, self.scope
        session, binding, history = self.session, self.binding, self._history_store
        if lock is None or history is None:
            raise RuntimeBlocked("WORKER_HISTORY_OWNER_CHANGED")
        epoch = lock.acquisition_id
        window = HistoryWindow(start_ms, end_ms)

        def require_owner() -> None:
            # Sticky stop wins before status or any history/runtime SQL read.
            self._require_admission()
            if (self.runtime is not runtime or self._lock is not lock
                    or lock is not runtime.worker_lock or lock.acquisition_id != epoch
                    or self.scope != scope or self.session is not session
                    or self.binding is not binding or self._history_store is not history
                    or runtime.receipt_session is not session or runtime.history_store is not history
                    or history.scope != scope
                    or runtime.journal.scope != ExecutionScope(scope.account_id, scope.environment)):
                raise RuntimeBlocked("WORKER_HISTORY_OWNER_CHANGED")
            if symbol not in self.symbols:
                raise RuntimeBlocked("WORKER_SYMBOL_SCOPE_REQUIRED")
            session.require_observation_binding(binding, scope=scope)
            _ = runtime.status

        require_owner()
        receipt = await session.read_history_receipt(binding, scope=scope, symbol=symbol,
            start_ms=start_ms, end_ms=end_ms)
        try:
            require_owner()
            view = session.require_history_receipt(receipt, binding=binding, scope=scope)
            snapshot = view.snapshot
            if (type(snapshot) is not HistorySnapshot or snapshot.scope != scope
                    or snapshot.symbol != symbol or snapshot.window != window):
                raise RuntimeBlocked("WORKER_HISTORY_REQUEST_MISMATCH")
            # No await or thread handoff between final guards and history commit.
            observation = history.ingest(snapshot)
            def require_authority() -> None:
                require_owner()
                session.require_history_receipt(receipt, binding=binding, scope=scope)

            yield runtime, receipt, binding, snapshot, observation, require_authority
        finally:
            session.discard_history_receipt(receipt)

    @_admitted
    async def corroborate_native_fills(self, symbol: str, *, start_ms: int,
                                       end_ms: int) -> tuple[ExecutionCreditResult, ...]:
        """Explicit accounting for persisted native candidates; each commits independently."""
        async with self._history_accounting_receipt(symbol, start_ms=start_ms,
                end_ms=end_ms) as (runtime, receipt, binding, snapshot, observation, require_owner):
            execution_ids = {row.execution_id for row in snapshot.executions}
            candidates = sorted((candidate for candidate in runtime.persisted_candidates(symbol=symbol)
                if (start_ms <= candidate.fill.ts_event_ns // 1_000_000 <= end_ms
                    or candidate.fill.execution_id in execution_ids)), key=candidate_identity)
            results = []
            for candidate in candidates:
                require_owner()
                results.append(runtime.corroborate_execution(receipt=receipt, binding=binding,
                    observation_key=observation.observation_key,
                    candidate_id=candidate_identity(candidate)))
            return tuple(results)

    @_admitted
    async def recover_history_fills(self, symbol: str, *, start_ms: int,
                                    end_ms: int) -> tuple[HistoryRecoveryResult, ...]:
        """Recover owned history rows in event order without inventing native evidence.

        History and each execution commit separately. Failure may follow durable
        earlier credits; neither an empty nor completed batch proves completeness.
        """
        async with self._history_accounting_receipt(symbol, start_ms=start_ms,
                end_ms=end_ms) as (runtime, receipt, binding, snapshot, observation, require_owner):
            results = []
            for row in sorted(snapshot.executions,
                    key=lambda row: (row.time_ms, row.execution_id, row.venue_order_id)):
                require_owner()
                results.append(runtime.recover_history_execution(receipt=receipt, binding=binding,
                    observation_key=observation.observation_key, execution_id=row.execution_id))
            return tuple(results)

    def realized_loss(self, history: HistorySnapshot, baseline: DailyCashBaseline) -> DailyRealizedLoss:
        _ = self.runtime
        if history.scope != self.scope:
            raise RuntimeBlocked("WORKER_HISTORY_SCOPE_REQUIRED")
        return daily_realized_loss(history, baseline, now=self.clock(), max_age=self.max_age)

    @_admitted
    async def apply_leverage(self, command_key: str, entry_key: str) -> CommandRecord:
        runtime = self.runtime
        self.session.require_execution_binding(self.binding, scope=self.scope)
        assert self._assembly is not None
        control = NativeLeverageControl(client=self._assembly.execution_client,
            scope=self.scope, native_account_id=self._assembly.channel.native_account_id)
        return await _settle_effect(runtime.apply_leverage(command_key, entry_key, control=control,
                                                           adapter=self._readback()))

    async def stop(self) -> None:
        if self._stopped and self._loop is asyncio.get_running_loop():
            return
        runtime = self.runtime
        # Sticky admission fence is set before any await, even when this attempt
        # refuses because a previously admitted operation still owns liabilities.
        self._stopping = True
        runtime.pause_entries()
        if self._monitoring is not None:
            self._monitoring.retire("MONITORING_STOPPED")
        if self._stop_in_progress:
            raise RuntimeBlocked("WORKER_STOP_ALREADY_RUNNING")
        if self._staging:
            raise RuntimeBlocked("AWAIT_INFLIGHT_SELECTION_BEFORE_STOP")
        if self._operations:
            raise RuntimeBlocked("AWAIT_INFLIGHT_OPERATION_BEFORE_STOP")
        self._stop_in_progress = True
        try:
            await self._finish_stop(runtime)
        finally:
            self._stop_in_progress = False

    async def _quiesce(self) -> None:
        assert self._assembly is not None
        if self._monitoring is not None:
            self._monitoring.require_cleanup_owner()
        if self._node_task is not None:
            if (self._node_stop_task is None or self._node_stop_task.done()
                    and (self._node_stop_task.cancelled() or self._node_stop_task.exception() is not None)):
                self._node_stop_task = asyncio.create_task(self._assembly.node.stop_async())
            await asyncio.shield(self._node_stop_task)
            await asyncio.shield(self._node_task)
        if self._monitoring is not None:
            stamp = await self._assembly.drain_monitoring()
            self._monitoring.require_cleanup_owner()
            self._assembly.require_monitoring_tasks_quiesced()
            retained = self._monitoring.state.retained_stamp
            if (stamp.received != stamp.delivered or retained is not None and (
                    stamp.received < retained.received or stamp.delivered < retained.delivered
                    or retained.conn_id and (retained.epoch, retained.conn_id) != (stamp.epoch, stamp.conn_id))):
                raise RuntimeBlocked("MONITORING_DELIVERY_GAP_RETAINS_RESOURCES")
        if self._factory is None:
            self._assembly.require_quiesced()

    async def _finish_stop(self, runtime: NativeExecutionRuntime) -> None:
        try:
            async with asyncio.timeout(10):
                await self._quiesce()
            assert self._assembly is not None
            self._assembly.close_event_channel()
            self._channel_closed = True
            # Already accepted owner-loop callbacks settle before SQLite closes.
            await asyncio.sleep(0)
        except BaseException:
            # Stop tasks remain owned even when a bounded attempt times out.
            # Never close the callback sink or stores without producer proof.
            with suppress(RuntimeBlocked):
                runtime.transport_lost()
            if self._monitoring is not None:
                self._monitoring.state = replace(self._monitoring.state,
                    resources_retained=True, stream_stamp=self._monitoring.client.monitoring_stamp())
            raise
        try:
            runtime.transport_lost()
            try:
                if self._restart is not None:
                    self._restart.seal(runtime.native_owned_requests)
            finally:
                try:
                    runtime.shutdown()
                finally:
                    if self._factory is None:
                        self._assembly.dispose_local()
        finally:
            self._stopped = True
            self._close_stores()
            if self._monitoring is not None:
                self._monitoring.state = replace(self._monitoring.state,
                    phase="STOPPED", resources_retained=False,
                    stream_stamp=self._assembly.monitoring_stamp())

    def _close_stores(self) -> None:
        try:
            if self._history_store is not None:
                self._history_store.close()
        finally:
            try:
                if self._store is not None:
                    self._store.close()
            finally:
                try:
                    if self._journal is not None:
                        self._journal.close()
                finally:
                    if self._lock is not None:
                        self._lock.release()
