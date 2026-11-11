"""Worker-owned original V2 model plans; observations, never entry approval.

Reuses the genuine source assembler, producer, policy/strategy publication and J.
No candidate, economic-input issuer, reservation admission or SUBMIT seal is made.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from fractions import Fraction
from typing import TYPE_CHECKING, Literal, NoReturn, Self, SupportsIndex
from uuid import uuid4

from ..account.bybit_public_mark_source_v1 import BybitPublicMarkSessionV1
from ..market_data.instrument_producer import BybitInstrumentCatalogProducer
from . import bybit_linear_model_inputs_v1 as a
from . import bybit_linear_model_v1 as m
from . import linear_risk_evidence_v2 as e
from . import risk_domain_v2 as d
from . import risk_v2_contracts as c
from .bybit_linear_entry_producer_v1 import (
    BybitLinearEntryProducerV1,
    BybitLinearPreparedEntryV1,
)
from .bybit_linear_source_assembler_v1 import BybitLinearSourceAssemblerV1, BybitLinearVenueReadV1
from .monitoring_budget import MonitoringReadBudget
from .runtime import RuntimeBlocked
from .worker_risk_assessment_v2 import (
    WORKER_RISK_ASSESSMENT_BLOCKERS_V2,
    WorkerRiskAssessmentHandleV2,
    WorkerRiskAssessmentPlanV2,
)

if TYPE_CHECKING:
    from .worker import ExecutionWorker


class WorkerModelPlanV2:
    """Opaque reference to one original worker/producer publication, not permission."""

    __slots__ = ()

    def __new__(cls) -> Self:
        raise RuntimeBlocked("WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED")

    def __copy__(self) -> NoReturn:
        raise RuntimeBlocked("WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        raise RuntimeBlocked("WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise RuntimeBlocked("WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED")


class WorkerEntryModelOwnerV2:
    """One actual worker lifetime, original source owners and fresh J epoch nonce."""

    def __init__(self, worker: ExecutionWorker, *, public_marks: BybitPublicMarkSessionV1,
                 catalog_producer: BybitInstrumentCatalogProducer) -> None:
        from .worker import ExecutionWorker

        if (type(worker) is not ExecutionWorker or type(public_marks) is not BybitPublicMarkSessionV1
                or type(catalog_producer) is not BybitInstrumentCatalogProducer):
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED")
        worker._entry_publication_links_v2()
        if worker._journal is None:
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED")
        journal = worker._journal
        if any(not record.terminal_confirmed for record in journal.list_commands()):
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_RECONCILIATION_REQUIRED")
        previous = journal._v2_meta(required=False)
        # acquisition_id is only a local lock counter. It is NEVER the J epoch.
        epoch = uuid4().hex
        revision = journal.bind_owner_epoch_v2(epoch,
            expected_previous_owner_epoch=None if previous is None else previous[0],
            expected_journal_revision=0 if previous is None else previous[1])
        worker._entry_publication_links_v2()
        if (journal._v2_meta(required=True) != (epoch, revision)
                or any(not record.terminal_confirmed for record in journal.list_commands())):
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_RECONCILIATION_REQUIRED")
        self._worker = worker
        self._journal_epoch = epoch
        self._original_epoch = epoch
        self._original = self
        self._original_roots = self._roots()
        self._source_guard = self._guard
        self._assembler = BybitLinearSourceAssemblerV1(session=worker.session, public_marks=public_marks,
            catalog_producer=catalog_producer, journal=journal, clock=worker.clock, owner_guard=self._source_guard)
        self._producer = BybitLinearEntryProducerV1(assembler=self._assembler, policy_book=worker.policy_book)
        self._original_components = self._assembler, self._producer, public_marks, catalog_producer, self._source_guard
        self._read: BybitLinearVenueReadV1 | None = None
        self._current: tuple[WorkerModelPlanV2, BybitLinearPreparedEntryV1, object, object] | None = None
        self._busy: object | None = None
        self._operation_generation = object()
        self._operation_birth: tuple[object, object] | None = None
        self._risk_assessment_current: tuple[object, ...] | None = None

    def _roots(self) -> tuple[object, ...]:
        worker = self._worker
        return (worker, worker._loop, worker.session, worker.scope, worker.policy_book,
            worker._journal, worker._store, worker._history_store, worker._lock, worker._runtime,
            worker._assembly, worker.clock)

    def retire(self) -> None:
        """No callback or refusal: worker cleanup always withdraws old handles."""
        self._current = None
        self._read = None
        self._risk_assessment_current = None
        self._operation_generation = object()

    def retire_plan_preserving_read_v1(self, read: BybitLinearVenueReadV1) -> None:
        """Withdraw prior plan handles while preserving one exact source read for signal binding."""
        if self._busy is not None or self._read is not read or type(read) is not BybitLinearVenueReadV1:
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED")
        self._current = None
        self._risk_assessment_current = None

    def _guard(self) -> None:
        worker = self._worker
        if (self._original is not self or worker is not self._original_roots[0]
                or self._journal_epoch != self._original_epoch):
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED")
        worker._entry_publication_links_v2()
        assembler, producer, marks, catalog, guard = self._original_components
        if (self._assembler is not assembler or self._producer is not producer
                or assembler.session is not worker.session or assembler.journal is not worker._journal
                or assembler.public_marks is not marks or assembler.catalog_producer is not catalog
                or assembler.clock is not worker.clock or assembler.owner_guard is not guard
                or producer.assembler is not assembler or producer.policy_book is not worker.policy_book
                or worker._entry_model_owner_v2 is not self
                or worker._loop is not asyncio.get_running_loop()
                or any(actual is not old for actual, old in zip(self._roots(), self._original_roots, strict=True))
                or worker._journal is None or worker.session._binding is not worker.binding
                or worker._journal._v2_meta(required=True) is None):
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED")
        meta = worker._journal._v2_meta(required=True)
        if meta is None or meta[0] != self._journal_epoch:
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED")
        birth = self._operation_birth
        if self._busy is not None and (birth is None or birth[0] is not self._operation_generation
                or birth[1] is not worker._entry_publication_generation_v2):
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_OPERATION_RETIRED")

    async def capture(self, *, scope: c.RiskScopeV2, profile: Literal["UTA2_V1", "DEMO_UTA1_V1"],
                      nonvip_fee_basis: a.BybitNonVipFeeBasisV1, nonvip_applicability_revision: str,
                      budget: MonitoringReadBudget) -> BybitLinearVenueReadV1:
        worker = self._worker
        worker._retire_entry_model_plan_v2()
        if self._busy is not None or worker._entry_publication_frame_v2 is not None:
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_OPERATION_IN_PROGRESS")
        self._guard()
        if scope.product_scope.account != worker.scope or scope.instrument.symbol not in worker.symbols:
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED")
        if profile not in ("UTA2_V1", "DEMO_UTA1_V1"):
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED")
        policy = worker.policy_book.risk_policy_v2(scope)
        quote_us = Fraction(policy.max_quote_age_seconds) * 1_000_000
        if quote_us.denominator != 1:
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_TIME_RESOLUTION_UNSUPPORTED")
        max_quote_age = min(worker.max_age, timedelta(microseconds=quote_us.numerator))
        self._read = None
        token = object()
        self._busy = token
        self._operation_birth = self._operation_generation, worker._entry_publication_generation_v2
        try:
            capture = self._assembler.capture_v1 if profile == "UTA2_V1" else self._assembler.capture_demo_uta1_v1
            read = await capture(worker.binding, scope=scope, owner_generation=self._journal_epoch,
                nonvip_fee_basis=nonvip_fee_basis, nonvip_applicability_revision=nonvip_applicability_revision,
                budget=budget, max_age=worker.max_age, max_quote_age=max_quote_age)
            self._guard()
            if self._busy is not token:
                raise RuntimeBlocked("WORKER_ENTRY_MODEL_OPERATION_IN_PROGRESS")
            self._read = read
            return read
        finally:
            if self._busy is token:
                self._busy = None
                self._operation_birth = None

    def prepare(self, *, inputs: e.LinearRiskInputsV2, domain: d.LinearRiskDomainCoverageV2,
                policy_revision: int, publication_generation: int) -> WorkerModelPlanV2 | m.BybitModelRejectedV1:
        worker = self._worker
        if self._busy is not None or worker._entry_publication_frame_v2 is not None:
            worker._retire_entry_publications_v2()
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_OPERATION_IN_PROGRESS")
        self._guard()
        if self._read is None:
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED")
        # Normal capture→prepare keeps that read. Every external retirement drops it.
        self._current = None
        self._risk_assessment_current = None
        token = object()
        self._busy = token
        self._operation_birth = self._operation_generation, worker._entry_publication_generation_v2
        try:
            with worker._entry_publication_section_v2():
                result = self._producer.prepare_plan_v1(read=self._read, inputs=inputs, domain=domain,
                    policy_revision=policy_revision, publication_generation=publication_generation)
                if type(result) is m.BybitModelRejectedV1:
                    return result
                if type(result) is not BybitLinearPreparedEntryV1:
                    raise RuntimeBlocked("WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED")
                self._guard()
                handle = object.__new__(WorkerModelPlanV2)
                self._current = (handle, result, worker._entry_publication_generation_v2, worker.binding)
                self.require(handle)
                return handle
        except BaseException:
            self._current = None
            raise
        finally:
            if self._busy is token:
                self._busy = None
                self._operation_birth = None

    def require(self, handle: WorkerModelPlanV2) -> BybitLinearPreparedEntryV1:
        self._guard()
        worker, slot = self._worker, self._current
        frame = worker._entry_publication_frame_v2
        if (type(handle) is not WorkerModelPlanV2 or slot is None or slot[0] is not handle
                or frame is None or frame[0] or slot[2] is not worker._entry_publication_generation_v2
                or slot[3] is not worker.binding):
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED")
        result = self._producer.require_current_v1(slot[1])
        # This is model/source currentness, never the complete U3/final-clock seal.
        if (self._current is not slot or slot[1] is not result or worker._entry_model_owner_v2 is not self
                or worker._entry_publication_frame_v2 is not frame or frame[0]
                or slot[2] is not worker._entry_publication_generation_v2 or slot[3] is not worker.binding
                or self._producer._current is None or self._producer._current.result is not result
                or self._assembler._current is None or self._assembler._current.result is not self._read
                or any(actual is not old for actual, old in zip(self._roots(), self._original_roots, strict=True))):
            raise RuntimeBlocked("WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED")
        return result

    def issue_risk_assessment_v2(self, signal_handle: object) -> WorkerRiskAssessmentHandleV2:
        """Pin one current owner-issued signal/read as a blocker-only PLAN observation."""
        from . import bybit_demo_uta1_profile_v1 as demo
        from . import bybit_linear_model_inputs_v1 as a
        from .worker_strategy_signal_v1 import WorkerStrategySignalOwnerV1, WorkerStrategySignalV1

        worker = self._worker
        self._guard()
        signal_owner = worker._strategy_signal_owner_v1
        if (type(signal_handle) is not WorkerStrategySignalV1
                or type(signal_owner) is not WorkerStrategySignalOwnerV1
                or worker._entry_publication_frame_v2 is not None or worker._operations or worker._staging):
            raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_SOURCE_REQUIRED")
        clock = worker.clock
        self._risk_assessment_current = None
        try:
            with worker._entry_publication_section_v2():
                self._guard()
                issued_signal = signal_owner._current
                signal = signal_owner.require(signal_handle)
                if (issued_signal is None or signal_owner._current is not issued_signal
                        or issued_signal.handle is not signal_handle
                        or issued_signal.entry_read is None):
                    raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_SOURCE_REQUIRED")
                read = issued_signal.entry_read
                slot = self._assembler._current
                if (type(read) is not BybitLinearVenueReadV1 or self._read is not read
                        or slot is None or slot.result is not read or slot.scope != signal.scope
                        or slot.owner_generation != self._journal_epoch):
                    raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_SOURCE_REQUIRED")
                self._assembler.require_current_v1(read)
                policy = worker.policy_book.risk_policy_v2(signal.scope)
                policy_sha256 = c.risk_policy_sha256_v2(policy)
                if policy.revision != str(signal.policy_revision):
                    raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_POLICY_CHANGED")
                max_age_microseconds = Fraction(policy.max_evidence_age_seconds) * 1_000_000
                if max_age_microseconds.denominator != 1 or max_age_microseconds.numerator <= 0:
                    raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_POLICY_INVALID")
                if type(read.venue) is demo.BybitDemoUta1VenueStateV1:
                    venue_sha256 = demo.bybit_demo_uta1_venue_sha256_v1(read.venue)
                    profile: Literal["UTA2_V1", "DEMO_UTA1_V1"] = "DEMO_UTA1_V1"
                elif type(read.venue) is a.BybitVenueStateV1:
                    venue_sha256 = a.bybit_venue_state_sha256_v1(read.venue)
                    profile = "UTA2_V1"
                else:
                    raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_SOURCE_REQUIRED")
                reservation_sha256 = c.reservation_snapshot_sha256_v2(read.full_reservations)
                evaluation_reservation_sha256 = c.reservation_snapshot_sha256_v2(
                    read.evaluation_reservations
                )
                mark_sha256 = c.mark_set_sha256_v2(read.marks)
                current_policy = worker.policy_book.risk_policy_v2(signal.scope)
                if (current_policy.revision != policy.revision
                        or c.risk_policy_sha256_v2(current_policy) != policy_sha256):
                    raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_POLICY_CHANGED")
                self._guard()
                if (signal_owner._current is not issued_signal or self._read is not read
                        or self._assembler._current is not slot
                        or worker._entry_model_owner_v2 is not self):
                    raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_SOURCE_CHANGED")
                worker_generation = worker._entry_publication_generation_v2
                binding = worker.binding
                catalog_current = slot.catalog_current
                journal_epoch = self._journal_epoch

            # Pin the read only after all hashes and the publication context's final
            # owner callbacks. The source expiries remain hard upper bounds.
            observed_at = c._utc_v2(clock())
            policy_expiry = observed_at + timedelta(microseconds=max_age_microseconds.numerator)
            valid_until = min(signal.valid_until, read.venue.valid_until, policy_expiry)
            if observed_at >= valid_until:
                raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_EXPIRED")
            plan = WorkerRiskAssessmentPlanV2(
                scope=signal.scope,
                signal_id=signal.signal_id,
                signal_policy_revision=signal.policy_revision,
                strategy_publication_generation=signal.publication_generation,
                policy_revision=policy.revision,
                policy_sha256=policy_sha256,
                source_profile=profile,
                source_generation=slot.owner_generation,
                catalog_revision=read.catalog.revision,
                venue_source_sha256=venue_sha256,
                mark_set_sha256=mark_sha256,
                reservation_snapshot_id=read.full_reservations.snapshot_id,
                reservation_sha256=reservation_sha256,
                evaluation_reservation_snapshot_id=read.evaluation_reservations.snapshot_id,
                evaluation_reservation_sha256=evaluation_reservation_sha256,
                observed_at=observed_at,
                valid_until=valid_until,
            )
            if plan.blockers != WORKER_RISK_ASSESSMENT_BLOCKERS_V2:
                raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_BLOCKERS_CHANGED")
            if (worker.clock is not clock or worker._entry_publication_frame_v2 is not None
                    or worker._entry_publication_generation_v2 is not worker_generation
                    or worker.binding is not binding or worker.policy_book is not self._original_roots[4]
                    or worker._journal is not self._original_roots[5]
                    or worker._entry_model_owner_v2 is not self
                    or worker._strategy_signal_owner_v1 is not signal_owner
                    or self._read is not read or self._assembler._current is not slot
                    or slot.result is not read or slot.catalog_current is not catalog_current
                    or signal_owner._current is not issued_signal or issued_signal.handle is not signal_handle
                    or self._journal_epoch != journal_epoch):
                raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_SOURCE_CHANGED")
            handle = object.__new__(WorkerRiskAssessmentHandleV2)
            self._risk_assessment_current = (
                handle, plan, signal_handle, signal_owner, issued_signal, read, slot,
                worker_generation, binding, policy_sha256, catalog_current, journal_epoch,
                observed_at, valid_until,
            )
            return handle
        except BaseException:
            self._risk_assessment_current = None
            raise

    def require_risk_assessment_v2(
        self, handle: WorkerRiskAssessmentHandleV2,
    ) -> WorkerRiskAssessmentPlanV2:
        """Revalidate every pinned source before returning its read-only PLAN view."""
        from . import bybit_demo_uta1_profile_v1 as demo
        from . import bybit_linear_model_inputs_v1 as a
        from .worker_strategy_signal_v1 import WorkerStrategySignalOwnerV1

        worker = self._worker
        current = self._risk_assessment_current
        if (type(handle) is not WorkerRiskAssessmentHandleV2 or current is None
                or current[0] is not handle or worker._entry_publication_frame_v2 is not None
                or worker._operations or worker._staging):
            raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_HANDLE_REQUIRED")
        clock = worker.clock
        try:
            with worker._entry_publication_section_v2():
                self._guard()
                (_issued_handle, plan, signal_handle, signal_owner, issued_signal, read, slot,
                    worker_generation, binding, policy_sha256, catalog_current,
                    journal_epoch, observed_at, valid_until) = current
                if (type(signal_owner) is not WorkerStrategySignalOwnerV1
                        or worker._strategy_signal_owner_v1 is not signal_owner
                        or worker_generation is not worker._entry_publication_generation_v2
                        or binding is not worker.binding or journal_epoch != self._journal_epoch
                        or self._read is not read or self._assembler._current is not slot
                        or slot.result is not read or slot.catalog_current is not catalog_current
                        or worker._entry_model_owner_v2 is not self):
                    raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_SOURCE_CHANGED")
                signal = signal_owner.require(signal_handle)
                if (signal_owner._current is not issued_signal or issued_signal.handle is not signal_handle
                        or issued_signal.entry_read is not read or signal.scope != plan.scope
                        or signal.signal_id != plan.signal_id
                        or signal.policy_revision != plan.signal_policy_revision
                        or signal.publication_generation != plan.strategy_publication_generation):
                    raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_SIGNAL_CHANGED")
                self._assembler.require_current_v1(read)
                policy = worker.policy_book.risk_policy_v2(plan.scope)
                if (policy.revision != plan.policy_revision
                        or c.risk_policy_sha256_v2(policy) != policy_sha256
                        or policy_sha256 != plan.policy_sha256
                        or str(signal.policy_revision) != policy.revision):
                    raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_POLICY_CHANGED")
                if type(read.venue) is demo.BybitDemoUta1VenueStateV1:
                    venue_sha256 = demo.bybit_demo_uta1_venue_sha256_v1(read.venue)
                    profile = "DEMO_UTA1_V1"
                elif type(read.venue) is a.BybitVenueStateV1:
                    venue_sha256 = a.bybit_venue_state_sha256_v1(read.venue)
                    profile = "UTA2_V1"
                else:
                    raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_SOURCE_REQUIRED")
                policy_window = Fraction(policy.max_evidence_age_seconds) * 1_000_000
                if policy_window.denominator != 1 or policy_window.numerator <= 0:
                    raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_POLICY_INVALID")
                policy_expiry = plan.observed_at + timedelta(microseconds=policy_window.numerator)
                if (plan.observed_at != observed_at or plan.valid_until != valid_until
                        or plan.valid_until > signal.valid_until or plan.valid_until > read.venue.valid_until
                        or plan.valid_until > policy_expiry
                        or venue_sha256 != plan.venue_source_sha256 or profile != plan.source_profile
                        or read.catalog.revision != plan.catalog_revision
                        or c.mark_set_sha256_v2(read.marks) != plan.mark_set_sha256
                        or read.full_reservations.snapshot_id != plan.reservation_snapshot_id
                        or c.reservation_snapshot_sha256_v2(read.full_reservations)
                            != plan.reservation_sha256
                        or read.evaluation_reservations.snapshot_id
                            != plan.evaluation_reservation_snapshot_id
                        or c.reservation_snapshot_sha256_v2(read.evaluation_reservations)
                            != plan.evaluation_reservation_sha256
                        or slot.owner_generation != plan.source_generation
                        or plan.blockers != WORKER_RISK_ASSESSMENT_BLOCKERS_V2
                        or plan.stage != "PLAN" or plan.numeric_assessment is not None
                        or plan.financial_authority is not False or plan.trading_enabled is not False):
                    raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_SOURCE_CHANGED")
                self._guard()
                if (self._risk_assessment_current is not current
                        or worker._entry_publication_generation_v2 is not worker_generation
                        or self._assembler._current is not slot or signal_owner._current is not issued_signal):
                    raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_SOURCE_CHANGED")
            # This is deliberately after the context manager's owner-link callbacks,
            # signal/source verification, and all hashing above. The suffix below is
            # restricted to direct identity/scalar checks so expiry is the final read.
            now = c._utc_v2(clock())
            if now >= valid_until:
                raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_EXPIRED")
            if (self._risk_assessment_current is not current
                    or worker._entry_publication_generation_v2 is not worker_generation
                    or worker.binding is not binding or worker._entry_model_owner_v2 is not self
                    or worker._strategy_signal_owner_v1 is not signal_owner
                    or worker.session is not self._original_roots[2]
                    or worker.policy_book is not self._original_roots[4]
                    or worker._journal is not self._original_roots[5]
                    or worker.clock is not clock
                    or self._assembler is not self._original_components[0]
                    or self._producer is not self._original_components[1]
                    or self._assembler.public_marks is not self._original_components[2]
                    or self._assembler.catalog_producer is not self._original_components[3]
                    or worker._entry_publication_frame_v2 is not None
                    or self._read is not read or self._assembler._current is not slot
                    or slot.result is not read or slot.catalog_current is not catalog_current
                    or signal_owner._current is not issued_signal or issued_signal.handle is not signal_handle
                    or self._journal_epoch != journal_epoch):
                raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_SOURCE_CHANGED")
            return plan
        except BaseException:
            self._risk_assessment_current = None
            raise

    def retire_risk_assessment_v2(self, handle: WorkerRiskAssessmentHandleV2) -> None:
        current = self._risk_assessment_current
        if (type(handle) is not WorkerRiskAssessmentHandleV2 or current is None or current[0] is not handle):
            raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_HANDLE_REQUIRED")
        self._risk_assessment_current = None
