"""Explicit read-only model phase through genuine stop/seal/restore/reconciliation.

No fresh-controller condition is weakened. A new native stream is admitted by
original MonitoringResume; model data and its J epoch grant no financial rights.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Literal

from ..account.bybit_public_mark_source_v1 import BybitPublicMarkSessionV1
from ..market_data.adapters.bybit import BybitAdapter
from ..market_data.instrument_producer import BybitInstrumentCatalogProducer
from ..market_data.public_client import PublicMarketClient
from ..market_data.service import MarketDataService
from .fresh_monitoring_v1 import FreshMonitoringBootstrapV1
from .monitoring_resume import MonitoringResume
from .native_restart import NativeRestartCoordinator
from .native_restart_store import NativeRestartGeneration
from .runtime import RuntimeBlocked

if TYPE_CHECKING:
    from .worker import ExecutionWorker


type ModelPhaseEventV2 = Literal["OLD_STOPPED", "NEW_STARTED", "MODEL_OBSERVING"]


class ModelObservationPhaseV2:
    """CLI's concrete current resource owner, including every failed handoff."""

    def __init__(self, worker: ExecutionWorker) -> None:
        from .worker import ExecutionWorker

        if type(worker) is not ExecutionWorker:
            raise RuntimeBlocked("MODEL_OBSERVATION_ORIGINAL_WORKER_REQUIRED")
        self.worker = worker
        self._origin = worker
        self._origin_restart: NativeRestartCoordinator | None = None
        self._sealed: NativeRestartGeneration | None = None
        self._old_stop_confirmed = False
        self._stop_attempted = False
        self._stop_complete = False
        self._entered = False
        self._stop_event: asyncio.Event | None = None
        self._original_stop_event_v2: asyncio.Event | None = None
        self._marks: BybitPublicMarkSessionV1 | None = None
        self._catalog: BybitInstrumentCatalogProducer | None = None
        self._market_client: PublicMarketClient | None = None
        self._market: MarketDataService | None = None

    def require_not_stopped(self, worker: ExecutionWorker) -> None:
        if (worker is not self.worker or worker._model_observation_phase_v2 is not self
                or self._stop_event is not self._original_stop_event_v2
                or self._original_stop_event_v2 is None or self._original_stop_event_v2.is_set()):
            raise RuntimeBlocked("MODEL_OBSERVATION_STOP_REQUESTED")

    async def stop_current(self) -> None:
        """Only successful await of the original stop proves Session may close."""
        if self._stop_complete:
            return
        if self._stop_attempted:
            raise RuntimeBlocked("MODEL_OBSERVATION_CLEANUP_UNCONFIRMED")
        worker = self.worker
        self._stop_attempted = True
        await worker.stop()
        if (self.worker is not worker or worker._stopped is not True
                or worker._lock is not None and worker._lock.acquired):
            raise RuntimeBlocked("MODEL_OBSERVATION_CLEANUP_UNCONFIRMED")
        self._stop_complete = True

    def require_new_lease(self, worker: ExecutionWorker) -> None:
        """Negative startup constraint under the new UID lease, before any restore."""
        original, restart, expected = self._origin, self._origin_restart, self._sealed
        if (worker is not self.worker or worker is original or worker._model_phase_transition_v2 is not self
                or not self._old_stop_confirmed or original._stopped is not True
                or original._lock is None or original._lock.acquired
                or type(restart) is not NativeRestartCoordinator or original._restart is not restart
                or type(expected) is not NativeRestartGeneration
                or restart._last_sealed_generation_v2 is not expected
                or worker.scope is not original.scope or worker.session is not original.session
                or worker.directory != original.directory or worker.lock_directory != original.lock_directory
                or worker.symbols != original.symbols or worker.trader_id != original.trader_id
                or worker.policy_book is not original.policy_book or worker.registry is not original.registry
                or worker.entry_risk is not original.entry_risk or worker.clock is not original.clock
                or worker.metadata_profile != original.metadata_profile or worker._factory is not None
                or worker._loop is not asyncio.get_running_loop() or worker._loop is not original._loop
                or worker._lock is None or worker._lock is original._lock
                or not worker._lock.matches_scope(account_id=original.scope.account_id,
                    environment=original.scope.environment)):
            raise RuntimeBlocked("MODEL_OBSERVATION_SEALED_OWNER_REQUIRED")
        if restart.store.load(restart.identity) != expected:
            raise RuntimeBlocked("MODEL_OBSERVATION_SEALED_GENERATION_CHANGED")

    def _fresh_origin(self) -> None:
        worker = self.worker
        controller = worker._monitoring
        assembly = worker._assembly
        if (worker is not self._origin or type(controller) is not FreshMonitoringBootstrapV1
                or controller.worker is not worker or assembly is None or worker._factory is not None
                or type(worker._restart) is not NativeRestartCoordinator or worker.scope.environment != "DEMO"
                or controller.in_progress or worker._operations or worker._staging):
            raise RuntimeBlocked("MODEL_OBSERVATION_FRESH_OWNER_REQUIRED")
        state = controller.snapshot()
        controller.owner_guard()
        assembly.require_monitoring_ready()
        current = controller.capture()
        if (worker._monitoring is not controller or worker._assembly is not assembly or controller.state is not state
                or state.phase != "MONITORING" or state.resources_retained
                or controller.checkpoint is None or current.native != controller.checkpoint.native
                or current.application != controller.checkpoint.application
                or state.trading_enabled or state.credit_authorized or state.production_supported):
            raise RuntimeBlocked("MODEL_OBSERVATION_FRESH_OWNER_REQUIRED")
        self._origin_restart = worker._restart

    def _install_sources(self) -> None:
        worker = self.worker
        self._marks = BybitPublicMarkSessionV1(source_environment="PUBLIC_MAINNET", symbols=worker.symbols)
        self._catalog = BybitInstrumentCatalogProducer(source_environment="PUBLIC_MAINNET")
        self._market_client = PublicMarketClient()
        self._market = MarketDataService({"bybit": BybitAdapter(self._market_client)})
        worker.install_entry_model_v2(public_marks=self._marks, catalog_producer=self._catalog,
            market=self._market)

    def _require_model_observing(self) -> None:
        worker = self.worker
        controller = worker._monitoring
        if (type(controller) is not MonitoringResume or controller.worker is not worker
                or controller.in_progress or worker._entry_model_owner_v2 is None):
            raise RuntimeBlocked("MODEL_OBSERVATION_RECONCILIATION_REQUIRED")
        controller.owner_guard()
        status = worker.monitoring_status
        if (status.phase != "MONITORING" or not status.reconciliation_current
                or status.resources_retained or status.trading_enabled or status.credit_authorized
                or status.production_supported or status.venue_event_complete):
            raise RuntimeBlocked("MODEL_OBSERVATION_RECONCILIATION_REQUIRED")
        worker._entry_model_owner_v2._guard()

    async def _connect_current(self) -> bool:
        stop = self._original_stop_event_v2
        if stop is None or self._stop_event is not stop or stop.is_set():
            return False
        try:
            await self.worker.connect_monitoring()
        except BaseException as error:
            controller = self.worker._monitoring
            if (isinstance(controller, (FreshMonitoringBootstrapV1, MonitoringResume))
                    and type(controller) in (FreshMonitoringBootstrapV1, MonitoringResume)
                    and controller.state.resources_retained):
                # The original admission already attempted and failed cleanup.
                # Finally must retain that owner, not start an automatic second stop.
                self._stop_attempted = True
            elif stop.is_set() and isinstance(error, (ValueError, RuntimeError)):
                return False
            raise
        return self._stop_event is stop and not stop.is_set()

    async def enter(self, stop: asyncio.Event) -> AsyncIterator[ModelPhaseEventV2]:
        """Yield lifecycle facts so CLI updates its real owner/status between awaits."""
        from .worker import ExecutionWorker

        if self._entered:
            raise RuntimeBlocked("MODEL_OBSERVATION_TRANSITION_ALREADY_USED")
        self._entered = True
        worker = self.worker
        worker._require_admission()
        if type(stop) is not asyncio.Event or worker._model_observation_phase_v2 is not None:
            raise RuntimeBlocked("MODEL_OBSERVATION_ORIGINAL_STOP_EVENT_REQUIRED")
        self._stop_event = self._original_stop_event_v2 = stop
        worker._model_observation_phase_v2 = self
        if stop.is_set():
            return
        # A previous model phase can restart through the original restored owner.
        # The real seal was already verified by start_passive; J metadata is not permission.
        restored_model = (worker.native_restart_status.status == "RESTORED_LOCAL_CACHE"
            and worker._journal is not None and worker._journal._v2_meta(required=False) is not None)
        if restored_model and (type(worker._restart) is not NativeRestartCoordinator
                or worker._factory is not None or worker.scope.environment != "DEMO"
                or worker._monitoring is not None or worker._node_task is not None
                or not worker.native_restart_status.generation_id):
            raise RuntimeBlocked("MODEL_OBSERVATION_SEALED_OWNER_REQUIRED")
        if not restored_model:
            if not await self._connect_current():
                return
            if stop.is_set():
                return
            self._fresh_origin()
            await self.stop_current()
            self._old_stop_confirmed = True
            restart = self._origin_restart
            if type(restart) is not NativeRestartCoordinator:
                raise RuntimeBlocked("MODEL_OBSERVATION_SEALED_OWNER_REQUIRED")
            self._sealed = restart._last_sealed_generation_v2
            if type(self._sealed) is not NativeRestartGeneration:
                raise RuntimeBlocked("MODEL_OBSERVATION_SEALED_OWNER_REQUIRED")
            yield "OLD_STOPPED"
            if stop.is_set():
                return
            # Adopt synchronously before its first startup await: failure never loses this owner.
            replacement = ExecutionWorker(directory=worker.directory, scope=worker.scope,
                session=worker.session, binding=worker.binding, symbols=worker.symbols,
                trader_id=worker.trader_id, policy_book=worker.policy_book, registry=worker.registry,
                entry_risk=worker.entry_risk, clock=worker.clock, max_age=worker.max_age,
                decoder=worker._decoder, lock_directory=worker.lock_directory,
                metadata_profile=worker.metadata_profile)
            replacement._model_phase_transition_v2 = self
            replacement._model_observation_phase_v2 = self
            self.worker = replacement
            self._stop_attempted = self._stop_complete = False
            await replacement.start_passive()
            if (replacement.native_restart_status.status != "RESTORED_LOCAL_CACHE"
                    or replacement.native_restart_status.generation_id != self._sealed.generation_id):
                raise RuntimeBlocked("MODEL_OBSERVATION_SEALED_GENERATION_CHANGED")
            yield "NEW_STARTED"
            if stop.is_set():
                return
        if stop.is_set():
            return
        self._install_sources()
        if stop.is_set():
            return
        # Model J epoch/audit is genuine local ownership, not a synthetic order.
        # Generic connect therefore selects the original non-empty restored PRE/POST path.
        if not await self._connect_current() or stop.is_set():
            return
        self._require_model_observing()
        if stop.is_set():
            return
        yield "MODEL_OBSERVING"

    async def close_sources(self) -> None:
        if self._catalog is None and self._marks is None and self._market_client is None:
            return
        if not self._stop_complete:
            raise RuntimeBlocked("MODEL_OBSERVATION_CLEANUP_UNCONFIRMED")
        try:
            try:
                if self._catalog is not None:
                    await self._catalog.aclose()
            finally:
                if self._market_client is not None:
                    await self._market_client.aclose()
        finally:
            if self._marks is not None:
                await self._marks.aclose()
