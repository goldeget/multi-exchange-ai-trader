"""One worker's PRE/start/POST admission and immutable monitoring checkpoint.

All mutation authority stays with the session issuer and genuine native path.
This controller owns no reconnect policy and never manufactures native evidence.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace
from typing import TYPE_CHECKING

from .monitoring_budget import MonitoringReadBudget
from .monitoring_models import (
    MonitoringError,
    MonitoringStatus,
    MonitoringTarget,
    MonitoringWitness,
    WorkerMonitoringStatus,
    _issue_monitoring_witness,
    _MonitoringTargetInvalidated,
)
from .monitoring_receipts import AuthenticatedMonitoringReceipt
from .monitoring_reconciliation import capture_monitoring_target, compare_restored_state
from .runtime import RuntimeBlocked

if TYPE_CHECKING:
    from .worker import ExecutionWorker


class MonitoringResume:
    def __init__(self, worker: ExecutionWorker) -> None:
        if (worker._factory is not None or worker._assembly is None or worker._lock is None
                or worker.native_restart_status.status != "RESTORED_LOCAL_CACHE"
                or worker._node_task is not None or worker._monitoring is not None):
            raise RuntimeBlocked("MONITORING_NEW_RESTORED_WORKER_REQUIRED")
        self.worker = worker
        self.runtime, self.assembly = worker.runtime, worker._assembly
        self.session, self.binding = worker.session, worker.binding
        self.lock, self.lease = worker._lock, worker._lock.acquisition_id
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.current_task()
        self.policy_book, self.policy = worker.policy_book, worker.policy_book.read()
        self.node, self.cache = self.assembly.node, self.assembly.node.cache
        self.client, self.strategy = self.assembly._monitor_client(), self.assembly.strategy
        self.channel, self.bridge = self.assembly.channel, self.assembly.bridge
        self.journal, self.store = self.runtime.journal, self.runtime.store
        self.history = self.runtime.history_store
        self.scope, self.symbols, self.trader_id = worker.scope, worker.symbols, worker.trader_id
        self.generation = worker.native_restart_status.generation_id
        self.attestation: object = None
        self.in_progress = True
        self.armed = False
        self.witness: MonitoringWitness | None = None
        self.accepted: MonitoringTarget | None = None
        self.checkpoint: MonitoringTarget | None = None
        self.state = WorkerMonitoringStatus("RECONCILING", self.scope, self.symbols)
        self.budget = MonitoringReadBudget(guard=self.owner_guard)
        self.owner_guard()

    def owner_guard(self) -> None:
        self.worker._require_model_observation_not_stopped_v2()
        w = self.worker
        if (asyncio.get_running_loop() is not self.loop or w._loop is not self.loop
                or w._runtime is not self.runtime or w._assembly is not self.assembly
                or w.session is not self.session or w.binding is not self.binding
                or w._lock is not self.lock or self.runtime.worker_lock is not self.lock
                or not self.lock.acquired or self.lock.acquisition_id != self.lease
                or w.scope != self.scope or w.symbols != self.symbols or w.trader_id != self.trader_id
                or self.assembly.channel is not self.channel or self.assembly.node is not self.node
                or self.node.cache is not self.cache or self.assembly.execution_client is not self.client
                or self.assembly.strategy is not self.strategy or self.assembly.bridge is not self.bridge
                or self.runtime.bridge is not self.bridge
                or self.runtime.journal is not self.journal or self.runtime.store is not self.store
                or w._journal is not self.journal or w._store is not self.store
                or w._history_store is not self.history
                or self.runtime.history_store is not self.history
                or self.runtime.receipt_session is not self.session
                or w.policy_book is not self.policy_book or self.policy_book.read() != self.policy
                or self.policy["desired_state"] != "STOPPED"
                or w.native_restart_status.generation_id != self.generation
                or w._stopped or w._stopping or w._channel_closed
                or self.in_progress and self.task is not None and self.task.cancelling()):
            raise MonitoringError("MONITORING_OWNER_CHANGED")
        self.runtime._guard()
        self.session._guard()
        if self.attestation is not None and (
                self.session._attested is not self.attestation
                or self.session._binding is not self.binding):
            raise MonitoringError("MONITORING_SESSION_REPLACED")
        self.worker._require_model_observation_not_stopped_v2()

    def require_cleanup_owner(self) -> None:
        # Revocation/STOPPING cannot block stopping the original producer. But a
        # replaced resource or lease must never be sealed/disposed as this owner.
        w = self.worker
        if (asyncio.get_running_loop() is not self.loop or w._loop is not self.loop
                or w._runtime is not self.runtime or w._assembly is not self.assembly
                or w._lock is not self.lock or not self.lock.acquired
                or self.lock.acquisition_id != self.lease
                or self.assembly.node is not self.node or self.node.cache is not self.cache
                or self.assembly.execution_client is not self.client
                or self.assembly.strategy is not self.strategy or self.assembly.channel is not self.channel
                or self.runtime.journal is not self.journal or self.runtime.store is not self.store
                or w._journal is not self.journal or w._store is not self.store
                or w._history_store is not self.history
                or self.runtime.history_store is not self.history):
            raise MonitoringError("MONITORING_CLEANUP_OWNER_CHANGED")
        self.runtime._guard()

    def fresh_guard(self) -> None:
        self.owner_guard()
        self.budget.check()
        self.observation_guard()

    def observation_guard(self) -> None:
        self.session.require_observation_binding(self.binding, scope=self.scope)

    def capture(self) -> MonitoringTarget:
        assert self.witness is not None and self.generation is not None
        return capture_monitoring_target(self.runtime, self.assembly,
            restore_generation_id=self.generation, policy_revision=str(self.policy["revision"]),
            witness=self.witness, stream_stamp=self.client.monitoring_stamp())

    def event_completed(self) -> None:
        # Called only after the exact native event acquired its genuine app ACK.
        # During stop, accepted completion remains permitted but no new target is issued.
        if not self.worker._stopping:
            self.accepted = self.capture()
            if self.state.phase == "MONITORING":
                self.state = replace(self.state, reconciliation_current=False,
                    current_fingerprint=self.accepted.fingerprint,
                    current_revision=self.accepted.application.revision)

    def retire(self, reason: str) -> None:
        if self.state.retained_stamp is None:
            self.state = replace(self.state, retained_stamp=self.client.monitoring_stamp())
        self.client.retire_monitoring(reason)
        self.state = replace(self.state, phase="ATTENTION", reconciliation_current=False,
            fresh_read_authority=False, fault=self.client.monitoring_fault or reason)
        with suppress(RuntimeBlocked):
            self.runtime.transport_lost()

    def snapshot(self) -> WorkerMonitoringStatus:
        if self.state.phase != "MONITORING":
            return self.state
        try:
            self.owner_guard()
            self.assembly.require_monitoring_ready()
            current = self.capture()
            if self.checkpoint is None or not self.legitimate_delta(self.checkpoint, current):
                raise MonitoringError("MONITORING_UNEXPLAINED_MUTATION")
            fresh = True
            try:
                self.session.require_observation_binding(self.binding, scope=self.scope)
            except ValueError:
                fresh = False
            self.state = replace(self.state, stream_stamp=current.stream_stamp,
                current_revision=current.application.revision, current_fingerprint=current.fingerprint,
                reconciliation_current=self.checkpoint is not None
                    and current.native == self.checkpoint.native
                    and current.application == self.checkpoint.application,
                fresh_read_authority=fresh)
        except Exception:  # noqa: BLE001 - fixed diagnostic, never provider contents.
            self.retire("MONITORING_CURRENT_STATE_INVALID")
        return self.state

    def legitimate_delta(self, before: MonitoringTarget, current: MonitoringTarget) -> bool:
        # Handshake/pong watermark movement alone changes a fingerprint. Economic
        # changes must equal a checkpoint recorded AFTER genuine native + app ACK.
        return ((before.native == current.native and before.application == current.application)
            or self.accepted is not None and self.accepted.native == current.native
                and self.accepted.application == current.application)

    async def run(self) -> WorkerMonitoringStatus:
        try:
            with self.session._monitoring_operation(self.budget):
                async with asyncio.timeout_at(self.budget.deadline):
                    fresh = await self.session.refresh_monitoring_binding(self.binding, scope=self.scope)
                    # owner guard intentionally checks the old worker handle before replacing it.
                    self.owner_guard()
                    self.worker.binding = self.binding = fresh
                    self.attestation = self.session._attested
                    self.fresh_guard()
                    self.witness = _issue_monitoring_witness(self.worker,
                        stream_stamp=self.assembly.monitoring_stamp, guard=self.owner_guard)
                    target = self.capture()
                    pre_receipt = await self.session.read_monitoring_receipt(
                        self.binding, target=target, phase="PRE")
                    try:
                        self.fresh_guard()
                        view = self.session.require_monitoring_receipt(pre_receipt,
                            binding=self.binding, target=target, phase="PRE")
                        if compare_restored_state(target, view.observation).status is MonitoringStatus.BLOCKED:
                            raise MonitoringError("MONITORING_PRE_MISMATCH")
                        self.session.consume_monitoring_receipt(pre_receipt,
                            binding=self.binding, target=target, phase="PRE")
                        # Native metadata, cache and wallet ACK share one fresh
                        # bounded group. The child task inherits this exact token.
                        native_capture = None
                        try:
                            with self.budget.receipt(
                                guard=self.observation_guard, owner_guard=self.owner_guard
                            ):
                                self.fresh_guard()
                                self.assembly.arm_monitoring(target)
                                native_capture = self.client._native_instrument_capture_v1
                                if native_capture is None:
                                    raise MonitoringError(
                                        "MONITORING_INSTRUMENT_RECEIPT_REQUIRED"
                                    )
                                self.armed = True
                                self.state = replace(self.state, phase="CONNECTING")
                                self.worker._node_task = self.loop.create_task(self._run_node())
                                issued = (
                                    await self.client.wait_native_instrument_receipt_ready_v1()
                                )
                                self.client._event_guard()
                                if not issued:
                                    raise MonitoringError(
                                        self.client.monitoring_fault
                                        or "MONITORING_INSTRUMENT_RECEIPT_REQUIRED"
                                    )
                        finally:
                            if native_capture is not None and native_capture.startup_open:
                                self.session._native_instrument_receipts_v1.finish_startup(
                                    native_capture, success=False
                                )
                            self.client._native_instrument_group_closed_v1.set()
                        # The stream handshake uses its existing timeout after the
                        # exact HTTP read group has ended.
                        await self.assembly.wait_monitoring_ready()
                    finally:
                        self.session.discard_monitoring_receipt(pre_receipt)
                    self.fresh_guard()
                    await self.assembly.monitoring_barrier()
                    self.fresh_guard()
                    for _ in range(3):
                        target = self.capture()
                        receipt: AuthenticatedMonitoringReceipt | None = None
                        try:
                            try:
                                receipt = await self.session.read_monitoring_receipt(
                                    self.binding, target=target, phase="POST")
                            except _MonitoringTargetInvalidated:
                                # Only the issuer's fixed capture-invalidation class
                                # can retarget. Terminal/malformed/read errors abort.
                                await self.assembly.monitoring_barrier()
                                self.fresh_guard()
                                current = self.capture()
                                if current.fingerprint != target.fingerprint and self.legitimate_delta(target, current):
                                    continue
                                raise
                            await self.assembly.monitoring_barrier()
                            self.fresh_guard()
                            current = self.capture()
                            if current.fingerprint != target.fingerprint:
                                if not self.legitimate_delta(target, current):
                                    raise MonitoringError("MONITORING_UNEXPLAINED_MUTATION")
                                continue
                            # Final publication is synchronous. The issued target object
                            # remains B; equal recapture C never substitutes for its slot.
                            self.assembly.require_monitoring_ready()
                            view = self.session.require_monitoring_receipt(receipt,
                                binding=self.binding, target=target, phase="POST")
                            if compare_restored_state(current, view.observation).status is MonitoringStatus.BLOCKED:
                                raise MonitoringError("MONITORING_POST_MISMATCH")
                            self.session.consume_monitoring_receipt(receipt,
                                binding=self.binding, target=target, phase="POST")
                            self.checkpoint = current
                            self.state = WorkerMonitoringStatus("MONITORING", self.scope, self.symbols,
                                reconciled_at=self.worker.clock().isoformat(),
                                reconciliation_fingerprint=current.fingerprint,
                                current_fingerprint=current.fingerprint,
                                current_revision=current.application.revision,
                                reconciliation_current=True, fresh_read_authority=True,
                                stream_stamp=current.stream_stamp)
                            return self.state
                        finally:
                            if receipt is not None:
                                self.session.discard_monitoring_receipt(receipt)
                    raise MonitoringError("MONITORING_RETARGET_LIMIT")
        except BaseException:
            self.retire("MONITORING_ADMISSION_FAILED")
            self.worker._stopping = True
            # Settle cleanup despite repeated caller cancellation. A bounded cleanup
            # failure retains exact stores/channel/lease for an explicit retry-stop.
            cleanup = self.loop.create_task(self._cleanup())
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()
            raise
        finally:
            self.in_progress = False

    async def _run_node(self) -> None:
        try:
            await self.node.run_async()
        except Exception:  # noqa: BLE001 - retrieve native failure without leaking payloads.
            self.retire("MONITORING_NODE_FAILED")
        finally:
            self.client._native_instrument_receipt_ready_v1.set()
            if not self.worker._stopped:
                self.retire("MONITORING_NODE_ENDED")

    async def _cleanup(self) -> None:
        try:
            await self.worker._finish_stop(self.runtime)
        except BaseException:  # noqa: BLE001 - failure is retained, original error propagates.
            self.state = replace(self.state, phase="ATTENTION", resources_retained=True,
                stream_stamp=self.client.monitoring_stamp())
