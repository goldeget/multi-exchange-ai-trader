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
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Concatenate, Literal

from ..account.bybit_public_mark_source_v1 import BybitPublicMarkSessionV1
from ..account.bybit_readonly import BybitReadOnlyClient
from ..ai.dispatch import AIDispatcher
from ..ai.generation import GenerationPriceQuote
from ..market_data.adapters.base import MarketDataUnavailable
from ..market_data.instrument_producer import BybitInstrumentCatalogProducer
from ..market_data.service import MarketDataService
from . import account_reconciliation_v1 as account_reconciliation
from . import bybit_linear_model_inputs_v1 as model_inputs_v1
from . import bybit_linear_model_v1 as model_v1
from . import linear_risk_evidence_v2 as evidence_v2
from . import modes
from . import risk_domain_v2 as domain_v2
from . import risk_v2_contracts as contracts_v2
from .account_binding import (
    AuthenticatedAccountBinding,
    BybitAccountIdentity,
    BybitDemoAccountSession,
)
from .account_ledger import DailyCashBaseline, DailyRealizedLoss, daily_realized_loss
from .bybit_linear_entry_producer_v1 import BybitLinearPreparedEntryV1
from .bybit_linear_source_assembler_v1 import BybitLinearVenueReadV1
from .execution_credit import ExecutionCreditResult, candidate_identity
from .fresh_monitoring_v1 import FreshMonitoringBootstrapV1, empty_sealed_observation_candidate_v1
from .history import BybitHistoryReader, HistorySnapshot, HistoryWindow
from .history_coverage import IngestionReceipt
from .history_receipts import AuthenticatedHistoryReceipt
from .history_recovery import HistoryRecoveryResult
from .history_store import HistoryStore
from .journal import CommandJournal, CommandRecord, ExecutionScope
from .monitoring_budget import MonitoringReadBudget
from .monitoring_models import WorkerMonitoringStatus
from .monitoring_resume import MonitoringResume
from .native_controls import NativeLeverageControl
from .native_events import NativeEventContext, NativeEventDecoder, NativeOrderEvidence
from .native_node import NativeChannel, NativeNodeAssembly, build_native_node
from .native_restart import NativeRestartCoordinator, WorkerNativeRestartStatus
from .observation_instrument_v1 import ACTUAL_ACCOUNT_FEE_V1, validate_metadata_profile_v1
from .ownership import AccountWorkerLock
from .protection import ProtectionAssessment, ProtectionPolicy, ProtectionScope
from .risk import EntryDecision
from .runtime import NativeExecutionRuntime, RiskCheck, RuntimeBlocked
from .runtime_policy import RuntimePolicyBook
from .state_store import RuntimeStateStore
from .venue_readback import BybitVenueReadback
from .worker_entry_model_v2 import WorkerEntryModelOwnerV2, WorkerModelPlanV2
from .worker_signal_instrument_pair_v1 import WorkerSignalRiskInstrumentPairV1
from .worker_signal_risk_plan_v1 import (
    WorkerSignalRiskPlanV1,
    bind_worker_signal_risk_plan_v1,
)
from .worker_strategy_signal_v1 import (
    WorkerStrategySignalDataV1,
    WorkerStrategySignalOwnerV1,
    WorkerStrategySignalV1,
)

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
                 lock_directory: Path | None = None,
                 metadata_profile: str = ACTUAL_ACCOUNT_FEE_V1) -> None:
        self.directory, self.scope = directory, scope
        self.lock_directory = directory if lock_directory is None else lock_directory
        self.session, self.binding = session, binding
        self.symbols, self.trader_id = symbols, trader_id
        self.policy_book, self.registry = policy_book, registry
        self.entry_risk, self.clock, self.max_age = entry_risk, clock, max_age
        self._factory, self._decoder = assembly_factory, decoder
        self.metadata_profile = validate_metadata_profile_v1(metadata_profile)
        if self.metadata_profile != ACTUAL_ACCOUNT_FEE_V1 and assembly_factory is not None:
            raise RuntimeBlocked("OBSERVATION_PROFILE_REQUIRES_ORIGINAL_MONITOR")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._assembly: NativeNodeAssembly | None = None
        self._runtime: NativeExecutionRuntime | None = None
        self._journal: CommandJournal | None = None
        self._store: RuntimeStateStore | None = None
        self._history_store: HistoryStore | None = None
        self._lock: AccountWorkerLock | None = None
        self._node_task: asyncio.Task[None] | None = None
        self._node_stop_task: asyncio.Task[None] | None = None
        self._monitoring: MonitoringResume | FreshMonitoringBootstrapV1 | None = None
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
        self._entry_publication_generation_v2 = object()
        self._entry_publication_frame_v2: list[bool] | None = None
        self._entry_binding_refresh_v2: object | None = None
        self._entry_model_owner_v2: WorkerEntryModelOwnerV2 | None = None
        self._entry_model_market_v1: MarketDataService | None = None
        self._strategy_signal_owner_v1: WorkerStrategySignalOwnerV1 | None = None
        self._model_phase_transition_v2: object | None = None
        self._model_observation_phase_v2: object | None = None

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

    def _require_model_observation_not_stopped_v2(self) -> None:
        """Negative observation cancellation only; never financial admission."""
        phase = self._model_observation_phase_v2
        if phase is not None:
            from .model_observation_phase_v2 import ModelObservationPhaseV2

            if type(phase) is not ModelObservationPhaseV2:
                raise RuntimeBlocked("MODEL_OBSERVATION_STOP_REQUESTED")
            phase.require_not_stopped(self)

    def _require_admission(self) -> None:
        _ = self.runtime
        if self._monitoring is not None and self._monitoring.in_progress:
            raise RuntimeBlocked("MONITORING_RESUME_IN_PROGRESS")
        if self._stopping:
            raise RuntimeBlocked("WORKER_STOPPING")

    def _retire_entry_publications_v2(self) -> None:
        """Withdraw local attempts; cleanup must never refuse because of this hook."""
        self._retire_entry_model_plan_v2()
        signal_owner = self._strategy_signal_owner_v1
        if type(signal_owner) is WorkerStrategySignalOwnerV1:
            WorkerStrategySignalOwnerV1.retire(signal_owner)

    def _retire_entry_model_plan_v2(self) -> None:
        """Withdraw model plans while preserving the independently checked signal."""
        self._entry_publication_generation_v2 = object()
        frame = self._entry_publication_frame_v2
        if frame is not None:
            frame[0] = True
        owner = self._entry_model_owner_v2
        if type(owner) is WorkerEntryModelOwnerV2:
            WorkerEntryModelOwnerV2.retire(owner)

    def _retire_entry_model_plan_preserving_read_v2(
            self, read: BybitLinearVenueReadV1) -> None:
        """Withdraw prior plan handles while retaining one captured source read."""
        owner = self._entry_model_owner_v2
        if (type(owner) is not WorkerEntryModelOwnerV2
                or self._entry_publication_frame_v2 is not None
                or self._entry_binding_refresh_v2 is not None
                or self._operations != 1 or self._staging
                or type(read) is not BybitLinearVenueReadV1):
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_OPERATION_IN_PROGRESS")
        owner.retire_plan_preserving_read_v1(read)
        self._entry_publication_generation_v2 = object()

    def _entry_publication_links_v2(self, *,
            fresh_binding: AuthenticatedAccountBinding | None = None) -> tuple[object, ...]:
        """Callbackful local lifetime checks, never an after-clock or finance proof."""
        session, scope = self.session, self.scope
        if (type(session) is not BybitDemoAccountSession or type(scope) is not ProtectionScope
                or type(scope.account_id) is not str or type(scope.environment) is not str):
            raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_OWNER_CHANGED")
        client, credential_client, pair = session._client, session._credential_client, session._credential_pair
        if (type(client) is not BybitReadOnlyClient or credential_client is not client
                or type(pair) is not tuple or len(pair) != 2
                or any(type(value) is not str for value in pair)
                or type(client.environment) is not str or type(client.base_url) is not str):
            raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_OWNER_CHANGED")
        http_client = client._client
        scope_values = scope.account_id, scope.environment
        client_values = client.environment, client.base_url
        identity, identity_uid = session._identity, None
        if fresh_binding is not None:
            if type(identity) is not BybitAccountIdentity or type(identity.uid) is not str:
                raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_OWNER_CHANGED")
            identity_uid = identity.uid

        def require_owner_values() -> None:
            # Local, closed comparisons; no callback or renewed authority.
            if (self.session is not session or self.scope is not scope
                    or session._client is not client or session._credential_client is not credential_client
                    or session._credential_pair is not pair or client._client is not http_client
                    or session._closed is not False
                    or type(scope.account_id) is not str or type(scope.environment) is not str
                    or (scope.account_id, scope.environment) != scope_values
                    or type(client.environment) is not str or type(client.base_url) is not str
                    or (client.environment, client.base_url) != client_values
                    or type(session._api_key) is not str or type(session._api_secret) is not str
                    or (session._api_key, session._api_secret) != pair
                    or type(client._api_key) is not str or type(client._api_secret) is not str
                    or (client._api_key, client._api_secret) != pair):
                raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_OWNER_CHANGED")
            if fresh_binding is not None and (
                    session._identity is not identity or type(identity) is not BybitAccountIdentity
                    or type(identity.uid) is not str or identity.uid != identity_uid):
                raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_OWNER_CHANGED")

        self._require_admission()
        runtime, assembly = self.runtime, self._assembly
        journal, store, history, lock = self._journal, self._store, self._history_store, self._lock
        binding, monitoring = self.binding, self._monitoring
        if (type(self) is not ExecutionWorker or type(assembly) is not NativeNodeAssembly
                or type(runtime) is not NativeExecutionRuntime or type(journal) is not CommandJournal
                or type(store) is not RuntimeStateStore or type(history) is not HistoryStore
                or type(lock) is not AccountWorkerLock or type(session) is not BybitDemoAccountSession
                or type(binding) is not AuthenticatedAccountBinding
                or self._channel_closed is not False or runtime._closed is not False or lock._fd is None
                or runtime.journal is not journal or runtime.store is not store
                or runtime.history_store is not history or runtime.receipt_session is not session
                or runtime.worker_lock is not lock or runtime.bridge is not assembly.bridge
                or runtime._lease != lock._acquisition_id
                or monitoring is not None and (type(monitoring) is not MonitoringResume
                    or monitoring.state.phase != "MONITORING" or monitoring.state.resources_retained)):
            raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_OWNER_CHANGED")

        def links() -> tuple[object, ...]:
            return (self, self._loop, self.session, self.binding, self._assembly, self._runtime,
                self._journal, self._store, self._history_store, self._lock, lock._fd,
                lock._acquisition_id, assembly.node, assembly.channel, assembly.strategy,
                assembly.bridge, assembly.execution_client, self._factory, self._decoder,
                self._monitoring, None if monitoring is None else monitoring.state,
                self._node_task, self._node_stop_task, self.scope, self.policy_book, self.registry,
                session._client, session._credential_client, session._credential_pair, client._client,
                scope.account_id, scope.environment, self._entry_model_owner_v2,
                self._entry_model_market_v1)

        original = links()
        runtime._guard()
        session._guard()
        if monitoring is not None:
            monitoring.owner_guard()
        self._require_admission()
        require_owner_values()
        if fresh_binding is not None:
            # Finish callbackful binding validation after the file/owner guards.
            returned_identity = session.require_binding(fresh_binding, scope=scope)
            require_owner_values()
            if (returned_identity is not identity or type(returned_identity) is not BybitAccountIdentity
                    or type(returned_identity.uid) is not str
                    or returned_identity.uid != scope_values[0] or scope_values[1] != "DEMO"):
                raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_OWNER_CHANGED")
        if (any(actual is not expected for actual, expected in zip(links(), original, strict=True))
                or self._stopping is not False or self._stopped is not False
                or self._channel_closed is not False or runtime._closed is not False
                or monitoring is not None and monitoring.in_progress is not False):
            raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_OWNER_CHANGED")
        return original

    @contextmanager
    def _entry_publication_section_v2(self) -> Iterator[None]:
        """Synchronous worker-local exclusion only; no entry approval is issued."""
        if self._entry_publication_frame_v2 is not None:
            self._retire_entry_publications_v2()
            raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_REENTRANT")
        self._require_admission()
        if self._entry_binding_refresh_v2 is not None:
            raise RuntimeBlocked("WORKER_BINDING_REFRESH_IN_PROGRESS")
        if self._operations or self._staging:
            raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_OPERATION_IN_PROGRESS")
        frame = [False]
        self._entry_publication_frame_v2 = frame
        generation = self._entry_publication_generation_v2
        try:
            original = self._entry_publication_links_v2()
            if (frame[0] or self._entry_publication_generation_v2 is not generation
                    or self._entry_publication_frame_v2 is not frame):
                raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_RETIRED")
            if self.session._binding is not self.binding:
                raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_BINDING_CHANGED")
            yield
            if (frame[0] or self._entry_publication_generation_v2 is not generation
                    or self._entry_publication_frame_v2 is not frame):
                raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_RETIRED")
            current = self._entry_publication_links_v2()
            if (frame[0] or self._entry_publication_generation_v2 is not generation
                    or self._entry_publication_frame_v2 is not frame
                    or self._entry_binding_refresh_v2 is not None or self._operations or self._staging
                    or self.session._binding is not self.binding
                    or any(actual is not expected for actual, expected in zip(current, original, strict=True))):
                raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_RETIRED")
        except BaseException:
            self._retire_entry_publications_v2()
            raise
        finally:
            if self._entry_publication_frame_v2 is frame:
                self._entry_publication_frame_v2 = None

    async def capture_builtin_signal_v1(self, *, scope: contracts_v2.RiskScopeV2,
                                        market: MarketDataService) -> WorkerStrategySignalV1:
        """Explicit approved closed-bar signal; never stages or dispatches an order."""
        self._require_admission()
        if type(market) is not MarketDataService or market is not self._entry_model_market_v1:
            raise MarketDataUnavailable("public_source_required", "The installed public market owner is required")
        if (self._operations or self._staging or self._entry_publication_frame_v2 is not None
                or self._entry_binding_refresh_v2 is not None):
            self._retire_entry_publications_v2()
            raise RuntimeBlocked("WORKER_STRATEGY_SIGNAL_OPERATION_IN_PROGRESS")
        self._operations += 1
        try:
            self._require_model_observation_not_stopped_v2()
            owner = WorkerStrategySignalOwnerV1(self, market)
            entry_catalog = owner._entry_catalog_for_scope_v1(scope)
            if entry_catalog is None:
                # Standalone signals refresh their own narrow worker-symbol universe.
                self._retire_entry_publications_v2()
            else:
                # The entry owner's full dynamic universe is already current. Retire
                # any old plan/signal handles without discarding that exact read.
                self._retire_entry_model_plan_preserving_read_v2(entry_catalog[2])
                signal_owner = self._strategy_signal_owner_v1
                if type(signal_owner) is WorkerStrategySignalOwnerV1:
                    WorkerStrategySignalOwnerV1.retire(signal_owner)
            self._strategy_signal_owner_v1 = owner
            return await owner.capture(scope)
        except BaseException:
            self._retire_entry_publications_v2()
            raise
        finally:
            self._operations -= 1

    def consume_builtin_signal_v1(self, handle: WorkerStrategySignalV1) -> WorkerStrategySignalDataV1:
        """Consume the same current original signal data, never financial admission."""
        self._require_admission()
        try:
            self._require_model_observation_not_stopped_v2()
        except BaseException:
            self._retire_entry_publications_v2()
            raise
        owner = self._strategy_signal_owner_v1
        if (type(owner) is not WorkerStrategySignalOwnerV1 or self._operations or self._staging
                or self._entry_publication_frame_v2 is not None):
            self._retire_entry_publications_v2()
            raise RuntimeBlocked("WORKER_STRATEGY_SIGNAL_OPERATION_IN_PROGRESS")
        return owner.require(handle)

    def install_entry_model_v2(self, *, public_marks: BybitPublicMarkSessionV1,
                               catalog_producer: BybitInstrumentCatalogProducer,
                               market: MarketDataService) -> None:
        """Explicit original model/source installation; no network, entry or paid call."""
        self._retire_entry_publications_v2()
        self._require_admission()
        if self._entry_model_owner_v2 is not None:
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_ALREADY_INSTALLED")
        if type(market) is not MarketDataService:
            raise RuntimeBlocked("WORKER_PUBLIC_MARKET_OWNER_REQUIRED")
        try:
            market._require_bybit_public_owner_v1()
        except (AttributeError, MarketDataUnavailable):
            raise RuntimeBlocked("WORKER_PUBLIC_MARKET_OWNER_REQUIRED") from None
        with self._entry_publication_section_v2():
            owner = WorkerEntryModelOwnerV2(self, public_marks=public_marks, catalog_producer=catalog_producer)
        self._entry_model_market_v1 = market
        self._entry_model_owner_v2 = owner

    async def capture_entry_sources_v2(self, *, scope: contracts_v2.RiskScopeV2,
            profile: Literal["UTA2_V1", "DEMO_UTA1_V1"],
            nonvip_fee_basis: model_inputs_v1.BybitNonVipFeeBasisV1,
            nonvip_applicability_revision: str, budget: MonitoringReadBudget) -> BybitLinearVenueReadV1:
        """Explicit real source read; fees and all original profile gates still apply."""
        self._require_admission()
        owner = self._entry_model_owner_v2
        if (type(owner) is not WorkerEntryModelOwnerV2 or self._operations or self._staging
                or self._entry_publication_frame_v2 is not None):
            self._retire_entry_publications_v2()
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_OPERATION_IN_PROGRESS")
        self._operations += 1
        try:
            return await owner.capture(scope=scope, profile=profile, nonvip_fee_basis=nonvip_fee_basis,
                nonvip_applicability_revision=nonvip_applicability_revision, budget=budget)
        finally:
            self._operations -= 1

    def prepare_entry_plan_v2(self, *, inputs: evidence_v2.LinearRiskInputsV2,
            domain: domain_v2.LinearRiskDomainCoverageV2, policy_revision: int,
            publication_generation: int) -> WorkerModelPlanV2 | model_v1.BybitModelRejectedV1:
        """Consume the worker's own retained read; supplied economic data is not authority."""
        self._require_admission()
        owner = self._entry_model_owner_v2
        if type(owner) is not WorkerEntryModelOwnerV2 or self._operations or self._staging:
            self._retire_entry_publications_v2()
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_OPERATION_IN_PROGRESS")
        return owner.prepare(inputs=inputs, domain=domain, policy_revision=policy_revision,
            publication_generation=publication_generation)

    @contextmanager
    def entry_model_publication_v2(self, handle: WorkerModelPlanV2) -> Iterator[BybitLinearPreparedEntryV1]:
        """Original model-data consumer; this is not a complete U3 or SUBMIT seal."""
        owner = self._entry_model_owner_v2
        if type(owner) is not WorkerEntryModelOwnerV2:
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED")
        with self._entry_publication_section_v2():
            yield owner.require(handle)
            owner.require(handle)

    def consume_builtin_signal_risk_plan_v1(
            self, signal_handle: WorkerStrategySignalV1, plan_handle: WorkerModelPlanV2,
    ) -> WorkerSignalRiskPlanV1:
        """Bind one current accepted signal to one current Bybit DEMO LINEAR PLAN.

        This returns an expiring evaluation snapshot only. It does not stage an
        entry, mutate the journal, claim J, or call the native execution path.
        """
        try:
            self._require_admission()
            signal = self.consume_builtin_signal_v1(signal_handle)
            with self.entry_model_publication_v2(plan_handle) as prepared:
                result = bind_worker_signal_risk_plan_v1(signal, prepared, now=self.clock())
            if self.consume_builtin_signal_v1(signal_handle) is not signal:
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_SIGNAL_REPLACED")
            if contracts_v2._utc_v2(self.clock()) >= result.valid_until:
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_EXPIRED")
            return result
        except BaseException:
            # A failed joint check must not leave a model handle independently
            # reusable after the signal that was supposed to qualify it failed.
            self._retire_entry_publications_v2()
            raise

    def snapshot_builtin_signal_risk_instrument_pair_v1(
            self, signal_handle: WorkerStrategySignalV1, plan_handle: WorkerModelPlanV2,
    ) -> WorkerSignalRiskInstrumentPairV1:
        """Observe one signal/plan key beside separate current-owner snapshots.

        The sources are deliberately left unmapped. Their checks are sequential,
        not one atomic freshness result, financial qualification, or permission.
        """
        try:
            self._require_admission()
            signal = self.consume_builtin_signal_v1(signal_handle)
            with self.entry_model_publication_v2(plan_handle) as prepared:
                bind_worker_signal_risk_plan_v1(signal, prepared, now=self.clock())
                pair = self._snapshot_signal_risk_instrument_pair_v1(signal, prepared)
                entry_owner = self._entry_model_owner_v2
                signal_owner = self._strategy_signal_owner_v1
                if (type(entry_owner) is not WorkerEntryModelOwnerV2
                        or type(signal_owner) is not WorkerStrategySignalOwnerV1):
                    raise RuntimeBlocked("WORKER_SIGNAL_RISK_INSTRUMENT_PAIR_REPLACED")
                entry_assembler = entry_owner._assembler
                plan_slot = entry_owner._current
                read_slot = entry_assembler._current
                catalog_producer = entry_assembler.catalog_producer
                catalog_slot = catalog_producer._current
                signal_slot = signal_owner._current
            with self.entry_model_publication_v2(plan_handle) as final_prepared:
                if final_prepared is not prepared:
                    raise RuntimeBlocked("WORKER_SIGNAL_RISK_PLAN_REPLACED")
                bind_worker_signal_risk_plan_v1(signal, prepared, now=self.clock())
            if self.consume_builtin_signal_v1(signal_handle) is not signal:
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_SIGNAL_REPLACED")
            final_native = self._require_current_signal_risk_native_receipt_v1(
                signal.scope.instrument
            )
            from .monitoring_resume import MonitoringResume

            monitoring_owner = self._monitoring
            if type(monitoring_owner) is not MonitoringResume:
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_INSTRUMENT_PAIR_REPLACED")
            # The public-currentness clock is an injected callback. Pin the
            # complete worker/MonitoringResume owner links on both sides so a
            # callback cannot replace the session, binding, assembly, lock, or
            # monitoring owner after the native receipt guard has run.
            pre_public_owner_links = self._entry_publication_links_v2()
            final_public, final_observation = self._require_current_signal_risk_public_catalog_v1(
                signal, prepared
            )
            monitoring_owner.owner_guard()
            post_public_owner_links = self._entry_publication_links_v2()
            if any(actual is not expected for actual, expected in zip(
                    post_public_owner_links, pre_public_owner_links, strict=True)):
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_INSTRUMENT_PAIR_REPLACED")
            # These are separate owner-local freshness checks, not one atomic
            # shared-clock/source-mapping assertion. Only identity reads follow.
            evidence = final_native[3]
            if (final_native[0].fingerprint != pair.demo_target_fingerprint
                    or final_native[2].issued_at != pair.demo_receipt_issued_at
                    or evidence.projection.specification_revision
                        != pair.demo_native_specification_revision
                    or evidence.native_semantics_sha256 != pair.demo_native_semantics_sha256
                    or evidence.envelope.response_origin != pair.demo_origin
                    or final_public.source_id != pair.public_source_id
                    or final_public.catalog.revision != pair.public_catalog_revision
                    or final_observation.raw_row_sha256 != pair.public_raw_row_sha256
                    or self._entry_model_owner_v2 is not entry_owner
                    or entry_owner._current is not plan_slot
                    or entry_owner._read is not prepared.source_read
                    or entry_assembler is not entry_owner._assembler
                    or entry_assembler._current is not read_slot
                    or catalog_producer._current is not catalog_slot
                    or catalog_slot is None
                    or self._strategy_signal_owner_v1 is not signal_owner
                    or signal_owner._current is not signal_slot
                    or signal_slot is None or signal_slot.value is not signal):
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_INSTRUMENT_PAIR_REPLACED")
            assembly = self._assembly
            if type(assembly) is not NativeNodeAssembly:
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_INSTRUMENT_PAIR_REPLACED")
            client = assembly.execution_client
            registry_slot = self.session._native_instrument_receipts_v1._slot
            if (registry_slot is None or registry_slot.view is not final_native[2]
                    or registry_slot.handle is not final_native[1]
                    or registry_slot.target is not final_native[0]
                    or client._native_instrument_capture_v1 is not registry_slot
                    or client._native_instrument_receipt_v1 is not final_native[1]
                    or client._monitor_target is not final_native[0]):
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_INSTRUMENT_PAIR_REPLACED")
            return pair
        except BaseException:
            self._retire_entry_publications_v2()
            raise

    def _require_current_signal_risk_public_catalog_v1(
            self, signal: WorkerStrategySignalDataV1,
            prepared: BybitLinearPreparedEntryV1,
    ) -> tuple[Any, Any]:
        from ..account.instrument_envelope import CatalogReceiptView
        from ..market_data.instrument_catalog import require_catalog_selection

        owner = self._entry_model_owner_v2
        if type(owner) is not WorkerEntryModelOwnerV2:
            raise RuntimeBlocked("WORKER_SIGNAL_RISK_PUBLIC_CATALOG_REQUIRED")
        assembler = owner._assembler
        slot = assembler._current
        producer = assembler.catalog_producer
        if (slot is None or slot.result is not prepared.source_read
                or owner._read is not prepared.source_read
                or type(producer) is not BybitInstrumentCatalogProducer):
            raise RuntimeBlocked("WORKER_SIGNAL_RISK_PUBLIC_CATALOG_REQUIRED")
        try:
            view = producer._risk_current(
                slot.catalog_receipt, expected_revision=signal.catalog_revision
            )
            if (type(view) is not CatalogReceiptView
                    or view.source_environment != "PUBLIC_MAINNET"
                    or view.catalog is not prepared.source_read.catalog):
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_PUBLIC_CATALOG_REQUIRED")
            # The current signal owner already checked this key against policy.
            observation, = require_catalog_selection(
                view.catalog,
                allowed=(signal.scope.instrument,),
                selected=(signal.scope.instrument,),
                expected_product="LINEAR",
                expected_source_environment="PUBLIC_MAINNET",
                expected_revision=signal.catalog_revision,
                now=self.clock(),
            )
            if (observation.key != signal.scope.instrument
                    or observation.specification_revision
                        != prepared.inputs.risk_inputs.instrument.specification_revision
                    or producer._current is not slot.catalog_current
                    or assembler._current is not slot or owner._read is not prepared.source_read):
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_PUBLIC_CATALOG_REPLACED")
            return view, observation
        except RuntimeBlocked:
            raise
        except (AttributeError, TypeError, ValueError, ArithmeticError):
            raise RuntimeBlocked("WORKER_SIGNAL_RISK_PUBLIC_CATALOG_REQUIRED") from None

    def _snapshot_signal_risk_instrument_pair_v1(
            self, signal: WorkerStrategySignalDataV1,
            prepared: BybitLinearPreparedEntryV1,
    ) -> WorkerSignalRiskInstrumentPairV1:
        from ..trading_product import InstrumentKey

        key = signal.scope.instrument
        owner = self._entry_model_owner_v2
        if type(owner) is not WorkerEntryModelOwnerV2:
            raise RuntimeBlocked("WORKER_SIGNAL_RISK_INSTRUMENT_PAIR_REQUIRED")
        assembler = owner._assembler
        slot = assembler._current
        producer = assembler.catalog_producer
        if (slot is None or slot.result is not prepared.source_read
                or owner._read is not prepared.source_read
                or type(producer) is not BybitInstrumentCatalogProducer
                or type(key) is not InstrumentKey
                or key.venue != "BYBIT" or key.product != "LINEAR"
                or prepared.inputs.risk_inputs.instrument.key != key
                or signal.catalog_revision != prepared.source_read.catalog.revision
                or prepared.inputs.risk_inputs.catalog_revision != signal.catalog_revision):
            raise RuntimeBlocked("WORKER_SIGNAL_RISK_INSTRUMENT_PAIR_MISMATCH")
        public_view, public_observation = self._require_current_signal_risk_public_catalog_v1(
            signal, prepared
        )
        if (public_observation.key != key
                or public_observation.specification_revision
                    != prepared.inputs.risk_inputs.instrument.specification_revision):
            raise RuntimeBlocked("WORKER_SIGNAL_RISK_INSTRUMENT_PAIR_MISMATCH")

        _target, _receipt, view, evidence = self._require_current_signal_risk_native_receipt_v1(key)
        pair = WorkerSignalRiskInstrumentPairV1(
            signal.signal_id,
            signal.catalog_revision,
            prepared.inputs.risk_inputs.catalog_revision,
            public_view.catalog.revision,
            signal.policy_revision,
            signal.publication_generation,
            key,
            public_view.source_environment,
            public_view.source_id,
            public_observation.source.endpoint,
            public_observation.specification_revision,
            public_observation.raw_row_sha256,
            public_observation.response_time,
            public_observation.source.received_at,
            evidence.envelope.response_origin,
            view.target_fingerprint,
            evidence.projection.specification_revision,
            evidence.native_semantics_sha256,
            view.issued_at,
        )
        return pair

    def _require_current_signal_risk_native_receipt_v1(
            self, key: Any,
    ) -> tuple[Any, Any, Any, Any]:
        from .monitoring_models import MonitoringTarget
        from .monitoring_resume import MonitoringResume
        from .native_instrument_receipts import (
            NativeInstrumentEvidenceV1,
            NativeInstrumentReceiptV1,
            NativeInstrumentReceiptViewV1,
            _PendingNativeInstrumentCaptureV1,
        )
        from .native_monitor_client import MonitoringBybitExecutionClient

        monitoring = self._monitoring
        assembly = self._assembly
        if (type(monitoring) is not MonitoringResume or monitoring.state.phase != "MONITORING"
                or monitoring.state.resources_retained or monitoring.in_progress
                or monitoring.state.reconciliation_current is not True
                or type(assembly) is not NativeNodeAssembly):
            raise RuntimeBlocked("WORKER_SIGNAL_RISK_NATIVE_RECEIPT_REQUIRED")
        try:
            self._entry_publication_links_v2()
            monitoring.owner_guard()
            client = assembly.execution_client
            target = getattr(client, "_monitor_target", None)
            capture = getattr(client, "_native_instrument_capture_v1", None)
            receipt = getattr(client, "_native_instrument_receipt_v1", None)
            if (type(client) is not MonitoringBybitExecutionClient
                    or client._monitor_session is not self.session
                    or client._monitor_binding is not self.binding
                    or client._metadata_profile != ACTUAL_ACCOUNT_FEE_V1
                    or self.metadata_profile != ACTUAL_ACCOUNT_FEE_V1
                    or client.monitoring_fault is not None
                    or not client._native_instrument_group_closed_v1.is_set()
                    or type(target) is not MonitoringTarget or target.scope != self.scope
                    or key.symbol not in target.symbols
                    or type(capture) is not _PendingNativeInstrumentCaptureV1
                    or capture.phase != "ISSUED" or capture.startup_open is not False
                    or capture.binding is not self.binding or capture.target is not target
                    or capture.scope != self.scope or capture.metadata_profile != ACTUAL_ACCOUNT_FEE_V1
                    or capture.target_fingerprint != target.fingerprint or capture.initializations != 1
                    or type(receipt) is not NativeInstrumentReceiptV1):
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_NATIVE_RECEIPT_REQUIRED")
            client._event_guard()
            view = self.session.require_native_instrument_receipt_v1(
                receipt, binding=self.binding, scope=self.scope, target=target
            )
            if (type(view) is not NativeInstrumentReceiptViewV1
                    or view.target_fingerprint != target.fingerprint
                    or key.symbol not in view.symbols):
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_NATIVE_RECEIPT_REQUIRED")
            evidence_items = tuple(item for item in view.evidence
                if type(item) is NativeInstrumentEvidenceV1 and item.key == key)
            if len(evidence_items) != 1:
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_NATIVE_RECEIPT_REQUIRED")
            evidence = evidence_items[0]
            monitoring.owner_guard()
            client._event_guard()
            current = self.session.require_native_instrument_receipt_v1(
                receipt, binding=self.binding, scope=self.scope, target=target
            )
            registry_slot = self.session._native_instrument_receipts_v1._slot
            if (current is not view or registry_slot is not capture or capture.handle is not receipt
                    or capture.view is not view or capture.target is not target
                    or capture.owner is not self.session
                    or capture.assembly is not assembly or capture.channel is not assembly.channel
                    or capture.client is not client or capture.adapter is not client._http_client
                    or capture.provider is not client.bybit_instrument_provider
                    or capture.cache is not assembly.node.cache
                    or client._native_instrument_capture_v1 is not capture
                    or client._native_instrument_receipt_v1 is not receipt
                    or client._monitor_target is not target
                    or assembly.execution_client is not client):
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_NATIVE_RECEIPT_REPLACED")
            return target, receipt, view, evidence
        except RuntimeBlocked:
            raise
        except (AttributeError, TypeError, ValueError):
            raise RuntimeBlocked("WORKER_SIGNAL_RISK_NATIVE_RECEIPT_REQUIRED") from None

    async def _refresh_binding_v2(self, *, expected_uid: str, expected_parent_uid: str,
                                  expected_is_master: bool) -> None:
        # Retire BEFORE admission can raise into an inner caller that catches it.
        self._retire_entry_publications_v2()
        if self._entry_publication_frame_v2 is not None:
            raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_REENTRANT")
        self._require_admission()
        if self._monitoring is not None:
            raise RuntimeBlocked("MONITORING_BINDING_REFRESH_REQUIRES_CONTROLLER")
        if self._entry_binding_refresh_v2 is not None:
            raise RuntimeBlocked("WORKER_BINDING_REFRESH_IN_PROGRESS")
        phase = object()
        self._entry_binding_refresh_v2 = phase
        generation = self._entry_publication_generation_v2
        session = self.session
        # Same lifetime accounting as _admitted; retirement must precede its guard.
        self._operations += 1
        try:
            original = self._entry_publication_links_v2()
            if (self._entry_publication_generation_v2 is not generation
                    or self._entry_binding_refresh_v2 is not phase):
                raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_RETIRED")
            fresh = await session.authenticate(expected_uid=expected_uid,
                expected_parent_uid=expected_parent_uid, expected_is_master=expected_is_master)
            if (self._entry_publication_generation_v2 is not generation
                    or self._entry_binding_refresh_v2 is not phase):
                raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_RETIRED")
            current = self._entry_publication_links_v2(fresh_binding=fresh)
            if (self._entry_publication_generation_v2 is not generation
                    or self._entry_binding_refresh_v2 is not phase
                    or type(fresh) is not AuthenticatedAccountBinding or session._binding is not fresh
                    or any(actual is not expected for actual, expected in zip(current, original, strict=True))):
                raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_OWNER_CHANGED")
            self.binding = fresh
        finally:
            self._operations -= 1
            if self._entry_binding_refresh_v2 is phase:
                self._entry_binding_refresh_v2 = None

    async def start_passive(self) -> None:
        if self._loop is not None or self._stopped:
            raise RuntimeBlocked("NEW_WORKER_INSTANCE_REQUIRED")
        self._loop = asyncio.get_running_loop()
        try:
            self._lock = AccountWorkerLock(self.lock_directory, account_id=self.scope.account_id,
                                           environment=self.scope.environment)
            self._lock.acquire()
            if self._model_phase_transition_v2 is not None:
                from .model_observation_phase_v2 import ModelObservationPhaseV2

                if type(self._model_phase_transition_v2) is not ModelObservationPhaseV2:
                    raise RuntimeBlocked("MODEL_OBSERVATION_SEALED_OWNER_REQUIRED")
                self._model_phase_transition_v2.require_new_lease(self)
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
                    on_event=self.post_native, restart_safe=True, monitor_only=True,
                    metadata_profile=self.metadata_profile)
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
                    self.scope, self.symbols, self.trader_id, metadata_profile=self.metadata_profile)
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
            self._retire_entry_publications_v2()
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
        # Any admitted native update invalidates a previously captured model graph.
        self._retire_entry_publications_v2()
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
            self._retire_entry_publications_v2()
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
        self._retire_entry_publications_v2()
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
        self._retire_entry_publications_v2()
        self.runtime.pause_entries()
        if self.native_restart_status.status == "RESTORED_LOCAL_CACHE":
            if empty_sealed_observation_candidate_v1(self):
                if self._operations != 1 or self._staging:
                    raise RuntimeBlocked("MONITORING_OPERATION_IN_PROGRESS")
                controller = FreshMonitoringBootstrapV1(self)
                self._monitoring = controller
                await controller.run()
                return
            await self._resume_restored_monitoring()
            return
        if self.native_restart_status.status == "FRESH_EMPTY_CACHE" and self._factory is None:
            if self._operations != 1 or self._staging:
                raise RuntimeBlocked("MONITORING_OPERATION_IN_PROGRESS")
            controller = FreshMonitoringBootstrapV1(self)
            self._monitoring = controller
            await controller.run()
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
                    self._retire_entry_publications_v2()
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

    def _account_observation_guard_v1(self, symbol: str) -> Callable[[], None]:
        # A nested attempt invalidates the outer operation even if caught inside a callback.
        self._retire_entry_publications_v2()
        if (self._operations != 1 or self._staging or self._entry_binding_refresh_v2 is not None
                or self._entry_publication_frame_v2 is not None or symbol not in self.symbols):
            raise RuntimeBlocked("ACCOUNT_OBSERVATION_EXCLUSIVE_OPERATION_REQUIRED")
        binding, session, scope = self.binding, self.session, self.scope
        generation = self._entry_publication_generation_v2
        original = self._entry_publication_links_v2(fresh_binding=binding)

        def require_owner() -> None:
            self._require_admission()
            if (self._operations != 1 or self._staging or self._entry_binding_refresh_v2 is not None
                    or self._entry_publication_generation_v2 is not generation):
                raise RuntimeBlocked("ACCOUNT_OBSERVATION_OWNER_CHANGED")
            current = self._entry_publication_links_v2(fresh_binding=binding)
            if any(actual is not expected for actual, expected in zip(current, original, strict=True)):
                raise RuntimeBlocked("ACCOUNT_OBSERVATION_OWNER_CHANGED")
            session.require_observation_binding(binding, scope=scope)

        require_owner()
        return require_owner

    async def _account_observation_sources_v1(self, symbol: str, guard: Callable[[], None]
            ) -> tuple[Callable[[], account_reconciliation._AccountObservationV1], Callable[[], None]]:
        from .monitoring_budget import MonitoringReadBudget

        session, binding, scope = self.session, self.binding, self.scope
        budget = MonitoringReadBudget(guard=guard)
        info = await session._capture_linear_account_info_v1(binding, scope=scope, budget=budget, owner_guard=guard)
        wallet = await session._capture_linear_wallet_v1(binding, scope=scope, budget=budget, owner_guard=guard)
        fee = await session._capture_linear_fee_rate_v1(binding, scope=scope, symbol=symbol, budget=budget, owner_guard=guard)
        settings = await session._capture_linear_settings_v1(binding, scope=scope, budget=budget, owner_guard=guard)
        flat = await session._capture_linear_flat_leverage_v1(binding, scope=scope, symbol=symbol, budget=budget, owner_guard=guard)
        positions = await session._capture_linear_positions_v1(binding, scope=scope, budget=budget, owner_guard=guard)
        orders = await session._capture_linear_orders_v1(binding, scope=scope, budget=budget, owner_guard=guard)

        def require_sources() -> None:
            guard()
            session._require_linear_account_info_v1(info, binding=binding, scope=scope)
            session._require_linear_wallet_v1(wallet, binding=binding, scope=scope)
            session._require_linear_fee_rate_v1(fee, symbol=symbol, binding=binding, scope=scope)
            session._require_linear_settings_v1(settings, binding=binding, scope=scope)
            session._require_linear_flat_leverage_v1(flat, symbol=symbol, binding=binding, scope=scope)
            session._require_linear_positions_v1(positions, binding=binding, scope=scope)
            session._require_linear_orders_v1(orders, binding=binding, scope=scope)
            guard()

        require_sources()
        wallet_slot, position_slot, order_slot = (session._linear_wallet_current,
            session._linear_positions_current, session._linear_orders_current)
        assert wallet_slot is not None and wallet_slot.capture is not None
        assert position_slot is not None and order_slot is not None
        wallet_capture, position_pages, order_pages = wallet_slot.capture, position_slot.pages, order_slot.pages
        raw_wallet = wallet_capture.raw_body
        raw_positions = tuple(page.capture.raw_body for page in position_pages)
        raw_orders = tuple(page.capture.raw_body for page in order_pages)

        def observe() -> account_reconciliation._AccountObservationV1:
            require_sources()
            value = account_reconciliation.observe_account_sources_v1(scope=scope, symbol=symbol,
                sources=(info, wallet, fee, settings, flat, *positions, *orders),
                wallet_source=wallet, wallet_raw=raw_wallet, position_raw=raw_positions, order_raw=raw_orders,
                application=self.runtime.monitoring_state(), now=self.clock())
            require_sources()
            if (session._linear_wallet_current is not wallet_slot or wallet_slot.capture is not wallet_capture
                    or wallet_capture.raw_body is not raw_wallet or position_slot.pages is not position_pages
                    or order_slot.pages is not order_pages):
                raise RuntimeBlocked("ACCOUNT_OBSERVATION_OWNER_CHANGED")
            return value

        return observe, require_sources

    @_admitted
    async def begin_account_observation_v1(self, symbol: str
            ) -> account_reconciliation.AccountObservationCheckpointV1:
        """Explicit post-start observation; never a UTC day or trading baseline."""
        guard = self._account_observation_guard_v1(symbol)
        journal = self.runtime.journal
        if journal.get_projection(account_reconciliation.PROJECTION_KEY) is not None:
            raise RuntimeBlocked("ACCOUNT_OBSERVATION_ALREADY_STARTED")
        observe, require_sources = await self._account_observation_sources_v1(symbol, guard)
        checkpoint = account_reconciliation.begin_account_checkpoint_v1(observe())
        payload = account_reconciliation.checkpoint_payload_v1(checkpoint)
        require_sources()
        journal.apply_event("account-start-" + checkpoint.checkpoint_id, payload,
            account_reconciliation.PROJECTION_KEY, 0,
            {"schema_version": 1, "checkpoint": payload, "latest_report": None})
        return checkpoint

    @_admitted
    async def collect_account_reconciliation_v1(self
            ) -> account_reconciliation.AccountReconciliationReportV1:
        """Collect source-bound diagnostics; unknown risk values never become zero."""
        journal = self.runtime.journal
        saved = journal.get_projection(account_reconciliation.PROJECTION_KEY)
        if saved is None:
            raise RuntimeBlocked("ACCOUNT_OBSERVATION_NOT_STARTED")
        try:
            document = json.loads(saved.payload_json)
            if (type(document) is not dict or set(document) != {"schema_version", "checkpoint", "latest_report"}
                    or type(document["schema_version"]) is not int or document["schema_version"] != 1):
                raise ValueError
            checkpoint = account_reconciliation.decode_account_checkpoint_v1(document["checkpoint"], scope=self.scope)
        except Exception:  # noqa: BLE001 - stored account details must not escape the fixed code.
            raise RuntimeBlocked("ACCOUNT_OBSERVATION_CHECKPOINT_INVALID") from None
        guard = self._account_observation_guard_v1(checkpoint.symbol)
        start_ms = int(checkpoint.wallet_request_started_at.timestamp() * 1000)
        through_ms = int(self.clock().timestamp() * 1000)
        observe, require_sources = await self._account_observation_sources_v1(checkpoint.symbol, guard)
        snapshots: list[HistorySnapshot] = []
        observation_keys: list[str] = []
        conflicts: set[str] = set()
        for symbol in self.symbols:
            async with self._history_accounting_receipt(symbol, start_ms=start_ms,
                    end_ms=through_ms) as (_, _, _, history, ingestion, require_history):
                guard()
                require_history()
                snapshots.append(history)
                observation_keys.append(ingestion.observation_key)
                conflicts.update(ingestion.conflict_keys)
                assert self._history_store is not None
                coverage = self._history_store.coverage(symbol, start_ms=start_ms, end_ms=through_ms)
                conflicts.update(item.conflict_key for item in coverage.conflicts)
        report = account_reconciliation.reconcile_account_observation_v1(checkpoint, observe(),
            histories=tuple(snapshots), observation_keys=tuple(observation_keys),
            conflict_keys=tuple(sorted(conflicts)), start_ms=start_ms, through_ms=through_ms)
        require_sources()
        payload = account_reconciliation.report_payload_v1(report)
        guard()
        journal.apply_event("account-report-" + report.report_id, payload,
            account_reconciliation.PROJECTION_KEY, saved.revision,
            {"schema_version": 1, "checkpoint": document["checkpoint"], "latest_report": payload})
        return report

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
        self._retire_entry_publications_v2()
        if self._stopped and self._loop is asyncio.get_running_loop():
            return
        runtime = self.runtime
        # Sticky admission fence is set before any await, even when this attempt
        # refuses because a previously admitted operation still owns liabilities.
        self._stopping = True
        if self._entry_publication_frame_v2 is not None:
            raise RuntimeBlocked("WORKER_ENTRY_PUBLICATION_REENTRANT")
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
        assembly, monitoring, factory = self._assembly, self._monitoring, self._factory
        assert assembly is not None
        node, run_task, stop_task = assembly.node, self._node_task, self._node_stop_task

        def require_owner() -> None:
            if (self._assembly is not assembly or assembly.node is not node
                    or self._monitoring is not monitoring or self._factory is not factory
                    or self._node_task is not run_task or self._node_stop_task is not stop_task):
                raise RuntimeBlocked("MONITORING_CLEANUP_OWNER_CHANGED")
            if monitoring is not None:
                monitoring.require_cleanup_owner()

        require_owner()
        if run_task is not None:
            if factory is None:
                assembly.capture_engine_queues(require_owner)
            if (stop_task is None or stop_task.done()
                    and (stop_task.cancelled() or stop_task.exception() is not None)):
                stop_task = self._node_stop_task = asyncio.create_task(node.stop_async())
            await asyncio.shield(stop_task)
            require_owner()
            if factory is None:
                await assembly.settle_engine_queues(require_owner)
                require_owner()
                assembly.require_quiesced()
                # Pinned startup's connection wait ignores concurrent stop.
                # Bind the waiter before the stop await: a replacement task
                # is never evidence for, or owned by, this stopped node.
                if not run_task.done():
                    run_task.cancel()
            await asyncio.shield(run_task)
            require_owner()
            if factory is None:
                await assembly.settle_owned_tasks()
                require_owner()
        if monitoring is not None:
            stamp = await assembly.drain_monitoring()
            require_owner()
            assembly.require_monitoring_tasks_quiesced()
            retained = monitoring.state.retained_stamp
            if (stamp.received != stamp.delivered or retained is not None and (
                    stamp.received < retained.received or stamp.delivered < retained.delivered
                    or retained.conn_id and (retained.epoch, retained.conn_id) != (stamp.epoch, stamp.conn_id))):
                raise RuntimeBlocked("MONITORING_DELIVERY_GAP_RETAINS_RESOURCES")
        if factory is None:
            assembly.require_quiesced()

    async def _finish_stop(self, runtime: NativeExecutionRuntime) -> None:
        self._retire_entry_publications_v2()
        monitoring, assembly, factory = self._monitoring, self._assembly, self._factory
        try:
            assert assembly is not None
            node, run_task = assembly.node, self._node_task
            async with asyncio.timeout(10):
                await self._quiesce()
            stop_task = self._node_stop_task
            # Yield before closing the sink. A queued owner/lease replacement
            # must retain every resource, even after successful producer quiescence.
            await asyncio.sleep(0)
            if (self._monitoring is not monitoring or self._assembly is not assembly
                    or assembly.node is not node or self._factory is not factory
                    or self._node_task is not run_task or self._node_stop_task is not stop_task):
                raise RuntimeBlocked("MONITORING_CLEANUP_OWNER_CHANGED")
            if monitoring is not None:
                monitoring.require_cleanup_owner()
            self._retire_entry_publications_v2()
            assembly.close_event_channel()
            self._channel_closed = True
            # No await from this final exact-owner check through seal/dispose/close.
        except BaseException:
            # Stop tasks remain owned even when a bounded attempt times out.
            # Never close the callback sink or stores without producer proof.
            self._retire_entry_publications_v2()
            with suppress(RuntimeBlocked):
                runtime.transport_lost()
            if monitoring is not None:
                monitoring.state = replace(monitoring.state,
                    resources_retained=True, stream_stamp=monitoring.client.monitoring_stamp())
            raise
        try:
            self._retire_entry_publications_v2()
            runtime.transport_lost()
            try:
                if self._restart is not None:
                    self._restart.seal(runtime.native_owned_requests)
            finally:
                try:
                    self._retire_entry_publications_v2()
                    runtime.shutdown()
                finally:
                    if self._factory is None:
                        self._retire_entry_publications_v2()
                        self._assembly.dispose_local()
        finally:
            self._stopped = True
            self._close_stores()
            if self._monitoring is not None:
                self._monitoring.state = replace(self._monitoring.state,
                    phase="STOPPED", resources_retained=False,
                    stream_stamp=self._assembly.monitoring_stamp())

    def _close_stores(self) -> None:
        self._retire_entry_publications_v2()
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
