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

    def _roots(self) -> tuple[object, ...]:
        worker = self._worker
        return (worker, worker._loop, worker.session, worker.scope, worker.policy_book,
            worker._journal, worker._store, worker._history_store, worker._lock, worker._runtime,
            worker._assembly, worker.clock)

    def retire(self) -> None:
        """No callback or refusal: worker cleanup always withdraws old handles."""
        self._current = None
        self._read = None
        self._operation_generation = object()

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
        worker._retire_entry_publications_v2()
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
