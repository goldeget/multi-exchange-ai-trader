"""Frozen reconciliation data. Data and stream stamps are never capabilities."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Literal
from weakref import WeakValueDictionary

from .protection import ProtectionScope

if TYPE_CHECKING:
    from .account_binding import BybitDemoAccountSession
    from .execution_credit import CorroboratedExecutionCredit
    from .execution_provenance import NativeExecutionCandidate
    from .history_recovery import HistoryExecutionCredit
    from .journal import CommandRecord
    from .monitoring_unattributed_history import (
        UnattributedHistoryExecution,
        UnattributedTerminalHistoryOrder,
    )
    from .native_node import NativeChannel, NativeNodeAssembly
    from .ownership import AccountWorkerLock
    from .protection import ProtectionProjection
    from .runtime import NativeExecutionRuntime, NativeOwnedRequest
    from .worker import ExecutionWorker


class MonitoringError(ValueError):
    """Fixed local reconciliation blocker."""


class _MonitoringTargetInvalidated(MonitoringError):
    """Internal capture invalidation, distinct from a failed venue read."""


class MonitoringStatus(str, Enum):
    MATCHED = "MATCHED"
    MATCHED_NATIVE_WITH_UNCREDITED_FILLS = "MATCHED_NATIVE_WITH_UNCREDITED_FILLS"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class MonitoringAssessment:
    status: MonitoringStatus
    codes: tuple[str, ...] = ()
    credit_authorized: Literal[False] = field(default=False, init=False)


@dataclass(frozen=True)
class StreamStamp:
    epoch: int
    conn_id: str
    received: int
    delivered: int


@dataclass(frozen=True, repr=False)
class MonitoringWitness:
    """Internal worker-issued owner binding; not an authenticated flag."""

    _worker: ExecutionWorker
    _runtime: NativeExecutionRuntime
    _assembly: NativeNodeAssembly
    _session: BybitDemoAccountSession
    _channel: NativeChannel
    _lock: AccountWorkerLock
    _lease: int
    _loop: asyncio.AbstractEventLoop
    _generation: str
    _policy: str
    _symbols: tuple[str, ...]
    _trader_id: str
    _strategy: object
    _stamp: Callable[[], StreamStamp]
    _guard: Callable[[], None]
    _issuer: object

    def __repr__(self) -> str:
        return "MonitoringWitness(opaque=True)"

    def require(self) -> None:
        w = self._worker
        if (
            _ISSUED_WITNESSES.get(id(w)) is not self
            or self._issuer is not _WITNESS_ISSUER
            or asyncio.get_running_loop() is not self._loop
            or w.runtime is not self._runtime
            or w._assembly is not self._assembly
            or w.session is not self._session
            or w._lock is not self._lock
            or self._assembly.channel is not self._channel
            or self._lock.acquisition_id != self._lease
            or not self._lock.acquired
            or w.scope != self._channel.scope
            or w.trader_id != self._trader_id
            or self._assembly.strategy is not self._strategy
            or w.symbols != self._symbols
            or w._stopping
            or w._channel_closed
            or w.native_restart_status.generation_id != self._generation
            or str(w.policy_book.read()["revision"]) != self._policy
        ):
            raise MonitoringError("MONITORING_OWNER_CHANGED")
        self._runtime._guard()
        self._guard()


_WITNESS_ISSUER = object()
_ISSUED_WITNESSES: WeakValueDictionary[int, MonitoringWitness] = WeakValueDictionary()


def _issue_monitoring_witness(
    worker: ExecutionWorker, *, stream_stamp: Callable[[], StreamStamp], guard: Callable[[], None]
) -> MonitoringWitness:
    from .native_node import NativeNodeAssembly
    from .runtime import NativeExecutionRuntime
    from .worker import ExecutionWorker

    if (
        type(worker) is not ExecutionWorker
        or type(worker.runtime) is not NativeExecutionRuntime
        or type(worker._assembly) is not NativeNodeAssembly
        or worker._lock is None
        or worker.native_restart_status.status != "RESTORED_LOCAL_CACHE"
        or not worker.native_restart_status.generation_id
    ):
        raise MonitoringError("MONITORING_RESTORED_OWNER_REQUIRED")
    witness = MonitoringWitness(
        worker,
        worker.runtime,
        worker._assembly,
        worker.session,
        worker._assembly.channel,
        worker._lock,
        worker._lock.acquisition_id,
        asyncio.get_running_loop(),
        worker.native_restart_status.generation_id,
        str(worker.policy_book.read()["revision"]),
        worker.symbols,
        worker.trader_id,
        worker._assembly.strategy,
        stream_stamp,
        guard,
        _WITNESS_ISSUER,
    )
    _ISSUED_WITNESSES[id(worker)] = witness
    witness.require()
    return witness


@dataclass(frozen=True)
class MonitoringOrder:
    client_order_id: str
    venue_order_id: str
    symbol: str
    side: str
    order_type: str
    time_in_force: str
    quantity: Decimal
    price: Decimal | None
    reduce_only: bool
    status: str
    cumulative: Decimal
    leaves: Decimal
    created_ms: int = 0
    updated_ms: int = 0
    source: str = "native"


@dataclass(frozen=True)
class MonitoringExecution:
    execution_id: str
    client_order_id: str
    venue_order_id: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    fee_currency: str
    ts_event_ns: int
    liquidity: str
    execution_type: str = "Trade"
    seq: int | None = None
    value: Decimal | None = None
    fee_rate: Decimal | None = None
    closed_quantity: Decimal | None = None


@dataclass(frozen=True)
class MonitoringPosition:
    symbol: str
    signed_quantity: Decimal


@dataclass(frozen=True, repr=False)
class NativeMonitoringSummary:
    orders: tuple[MonitoringOrder, ...]
    executions: tuple[MonitoringExecution, ...]
    positions: tuple[MonitoringPosition, ...]
    digest: str


@dataclass(frozen=True, repr=False)
class ApplicationMonitoringSummary:
    owned: tuple[NativeOwnedRequest, ...]
    commands: tuple[CommandRecord, ...]
    positions: tuple[ProtectionProjection, ...]
    candidates: tuple[NativeExecutionCandidate, ...]
    credits: tuple[CorroboratedExecutionCredit, ...]
    history_credits: tuple[HistoryExecutionCredit, ...]
    conflicts: tuple[str, ...]
    digest: str

    @property
    def revision(self) -> str:
        return self.digest


@dataclass(frozen=True, repr=False)
class MonitoringTarget:
    scope: ProtectionScope
    symbols: tuple[str, ...]
    restore_generation_id: str
    policy_revision: str
    native: NativeMonitoringSummary
    application: ApplicationMonitoringSummary
    stream_stamp: StreamStamp
    _witness: MonitoringWitness | None = field(repr=False)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            repr(
                (
                    self.scope,
                    self.symbols,
                    self.restore_generation_id,
                    self.policy_revision,
                    self.native.digest,
                    self.application.digest,
                    self.stream_stamp,
                )
            ).encode()
        ).hexdigest()

    def __repr__(self) -> str:
        return "MonitoringTarget(frozen=True, authority=False)"


@dataclass(frozen=True, repr=False)
class MonitoringObservation:
    scope: ProtectionScope
    symbols: tuple[str, ...]
    orders: tuple[MonitoringOrder, ...]
    executions: tuple[MonitoringExecution, ...]
    positions: tuple[MonitoringPosition, ...]
    response_times_ms: tuple[int, ...]
    windows: tuple[tuple[str, int, int], ...]
    local_response_times_ms: tuple[int, ...] = ()
    unattributed_history_orders: tuple[UnattributedTerminalHistoryOrder, ...] = ()
    unattributed_history_executions: tuple[UnattributedHistoryExecution, ...] = ()


@dataclass(frozen=True, repr=False)
class MonitoringReceiptView:
    observation: MonitoringObservation
    assessment: MonitoringAssessment
    target_fingerprint: str
    phase: Literal["PRE", "POST"]


@dataclass(frozen=True, repr=False)
class WorkerMonitoringStatus:
    """Readonly lifecycle checkpoint, not a reusable admission capability."""

    phase: str
    scope: ProtectionScope
    symbols: tuple[str, ...]
    reconciled_at: str | None = None
    reconciliation_fingerprint: str | None = None
    current_fingerprint: str | None = None
    current_revision: str | None = None
    reconciliation_current: bool = False
    fresh_read_authority: bool = False
    stream_stamp: StreamStamp = StreamStamp(0, "", 0, 0)
    retained_stamp: StreamStamp | None = None
    fault: str | None = None
    resources_retained: bool = False
    unmanaged_usdt_linear_activity: bool | None = None
    trading_enabled: Literal[False] = field(default=False, init=False)
    credit_authorized: Literal[False] = field(default=False, init=False)
    production_supported: Literal[False] = field(default=False, init=False)
    venue_event_complete: Literal[False] = field(default=False, init=False)

    def __repr__(self) -> str:
        return f"WorkerMonitoringStatus(phase={self.phase!r}, trading_enabled=False)"
