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
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ..ai.dispatch import AIDispatcher
from ..ai.generation import GenerationPriceQuote
from . import modes
from .account_binding import AuthenticatedAccountBinding, BybitDemoAccountSession
from .account_ledger import DailyCashBaseline, DailyRealizedLoss, daily_realized_loss
from .history import BybitHistoryReader, HistorySnapshot
from .journal import CommandJournal, CommandRecord, ExecutionScope
from .native_controls import NativeLeverageControl
from .native_events import NativeEventContext, NativeEventDecoder
from .native_node import NativeChannel, NativeNodeAssembly, build_native_node
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
        self._lock: AccountWorkerLock | None = None
        self._node_task: asyncio.Task[None] | None = None
        self._stopped = False
        self._staging = False
        self._channel_closed = False
        self.callback_failures = 0
        self.rejected_channels = 0

    @property
    def runtime(self) -> NativeExecutionRuntime:
        if (self._runtime is None or self._loop is not asyncio.get_running_loop()
                or self._stopped):
            raise RuntimeBlocked("WORKER_OWNER_LOOP_REQUIRED")
        return self._runtime

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
                    on_event=self.post_native)
            else:
                self._assembly = self._factory(self.post_native)
            channel = self._assembly.channel
            if (channel.scope != self.scope or channel.venue != "BYBIT"
                    or channel.native_account_id != self._assembly.bridge.native_account_id):
                raise RuntimeBlocked("WORKER_NATIVE_CHANNEL_MISMATCH")
            scope = ExecutionScope(self.scope.account_id, self.scope.environment)
            self._journal = CommandJournal(self.directory / "execution.sqlite3", scope)
            self._store = RuntimeStateStore(self.directory / "runtime-state.sqlite3", scope)
            self._runtime = NativeExecutionRuntime(journal=self._journal, store=self._store,
                worker_lock=self._lock, lock_directory=self.lock_directory,
                bridge=self._assembly.bridge,
                entry_risk=self.entry_risk, clock=self.clock, max_evidence_age=self.max_age)
        except BaseException:
            try:
                if self._assembly is not None and self._factory is None:
                    self._assembly.dispose_local()
            finally:
                self._close_stores()
                self._stopped = True
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
            self.runtime.consume_native(event, NativeEventContext(channel.scope,
                channel.native_account_id, received_ns), decoder=self._decoder)
        except Exception:  # noqa: BLE001 - keep monitoring alive without leaking provider payload.
            self.callback_failures += 1
            self.runtime.pause_entries()

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
                dispatch_task = asyncio.create_task(asyncio.to_thread(dispatcher.dispatch, request,
                    maximum_cost_usd=maximum_cost_usd, quote=quote, enabled=True))
                try:
                    result = await asyncio.shield(dispatch_task)
                except asyncio.CancelledError:
                    # A Python thread cannot be canceled. Keep the selection lock until
                    # the durable paid dispatcher finishes its liability bookkeeping.
                    await dispatch_task
                    raise
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

    async def connect_monitoring(self) -> None:
        """Explicit network start for the passive node, never entry activation.

        The registered strategy has no start/stop commands or external claims.
        This call is not made by app startup, saved policy, or start_passive.
        """
        self.runtime.pause_entries()
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

    async def poll_position(self, position_id: str) -> ProtectionAssessment:
        return await self.runtime.poll_readback(self._readback(), position_id)

    async def read_history(self, symbol: str, *, start_ms: int, end_ms: int) -> HistorySnapshot:
        _ = self.runtime
        if symbol not in self.symbols:
            raise RuntimeBlocked("WORKER_SYMBOL_SCOPE_REQUIRED")
        reader = BybitHistoryReader(self.session, self.binding, scope=self.scope,
                                    clock=self.clock, max_age=self.max_age)
        return await reader.read_history(symbol, start_ms=start_ms, end_ms=end_ms)

    def realized_loss(self, history: HistorySnapshot, baseline: DailyCashBaseline) -> DailyRealizedLoss:
        _ = self.runtime
        if history.scope != self.scope:
            raise RuntimeBlocked("WORKER_HISTORY_SCOPE_REQUIRED")
        return daily_realized_loss(history, baseline, now=self.clock(), max_age=self.max_age)

    async def apply_leverage(self, command_key: str, entry_key: str) -> CommandRecord:
        runtime = self.runtime
        self.session.require_execution_binding(self.binding, scope=self.scope)
        assert self._assembly is not None
        control = NativeLeverageControl(client=self._assembly.execution_client,
            scope=self.scope, native_account_id=self._assembly.channel.native_account_id)
        return await runtime.apply_leverage(command_key, entry_key, control=control,
                                             adapter=self._readback())

    async def stop(self) -> None:
        runtime = self.runtime
        if self._staging:
            raise RuntimeBlocked("AWAIT_INFLIGHT_SELECTION_BEFORE_STOP")
        runtime.pause_entries()
        try:
            if self._node_task is not None:
                assert self._assembly is not None
                await self._assembly.node.stop_async()
                await self._node_task
                if self._factory is None:
                    self._assembly.require_quiesced()
            assert self._assembly is not None
            self._assembly.close_event_channel()
            self._channel_closed = True
            # Process callbacks already posted by the stopped producer before closing SQLite.
            await asyncio.sleep(0)
        except BaseException:
            # No proof of producer quiescence: keep ownership and the callback sink
            # alive for retry/reconciliation, and never call this a clean shutdown.
            runtime.transport_lost()
            raise
        try:
            runtime.transport_lost()
            runtime.shutdown()
            if self._factory is None:
                self._assembly.dispose_local()
        finally:
            self._stopped = True
            self._close_stores()

    def _close_stores(self) -> None:
        if self._store is not None:
            self._store.close()
        if self._journal is not None:
            self._journal.close()
        if self._lock is not None:
            self._lock.release()
