"""First genuine empty native monitor startup, separate from restored authority.

Only the original worker, Session, lease, provider and cache can issue this
origin. Metadata is installed once; no restore generation or trading permission
is manufactured. Active crash recovery and existing restored contracts stay
with their original validators.
"""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal, NoReturn, Self, SupportsIndex
from uuid import uuid4
from weakref import WeakValueDictionary

from ..account.bybit_readonly import BybitReadOnlyClient, _account_info_capture_data_v1
from ..account.config import allowed_host
from .bybit_linear_model_inputs_v1 import BybitModelSourceRefV1
from .history import EPOCH, HistoryWindow, _order, _symbol, _timestamp, amount, utc
from .monitoring_budget import MonitoringReadBudget
from .monitoring_models import (
    ApplicationMonitoringSummary,
    MonitoringAssessment,
    MonitoringError,
    MonitoringObservation,
    MonitoringReceiptView,
    MonitoringStatus,
    NativeMonitoringSummary,
    StreamStamp,
    WorkerMonitoringStatus,
    _MonitoringTargetInvalidated,
)
from .monitoring_readback import BybitMonitoringReader
from .native_restart import NativeRestartCoordinator, _empty_application
from .native_restart_codec import capture_native_cache
from .native_restart_store import NativeRestartIdentity, canonical_bytes
from .observation_instrument_v1 import (
    SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
    require_instrument_metadata_profile_v1,
)
from .protection import ProtectionScope
from .runtime import RuntimeBlocked

if TYPE_CHECKING:
    from .account_binding import AuthenticatedAccountBinding, BybitDemoAccountSession
    from .native_node import NativeChannel, NativeNodeAssembly
    from .ownership import AccountWorkerLock
    from .runtime import NativeExecutionRuntime
    from .worker import ExecutionWorker

_ISSUER = object()
_WITNESSES: WeakValueDictionary[int, FreshMonitoringWitnessV1] = WeakValueDictionary()
_TARGETS: WeakValueDictionary[int, FreshMonitoringTargetV1] = WeakValueDictionary()

type _WholeScopeSourcesV1 = tuple[
    tuple[Literal["POSITIONS", "ORDERS"], tuple[BybitModelSourceRefV1, ...], object], ...]


def _whole_scope_activity_v1(
    endpoint: Literal["POSITIONS", "ORDERS"], rows: list[dict[str, object]], *,
    selected_symbols: tuple[str, ...], allow_unmanaged_other_symbols: bool,
    server_time_ms: int, positions_seen: set[tuple[str, int]], orders_seen: set[str],
) -> bool:
    """Classify data only. Neither this bool nor raw rows prove ownership or account flatness."""
    if (endpoint not in ("POSITIONS", "ORDERS") or type(allow_unmanaged_other_symbols) is not bool
            or type(server_time_ms) is not int or not 0 < server_time_ms < 253402300800000):
        raise MonitoringError("FRESH_MONITORING_ACCOUNT_SOURCE_REQUIRED")
    if not allow_unmanaged_other_symbols and (
        endpoint == "ORDERS" and rows or endpoint == "POSITIONS"
        and any(amount(row.get("size"), nonnegative=True) != 0 for row in rows)
    ):
        raise MonitoringError("FRESH_MONITORING_EXTERNAL_ACTIVITY")
    unmanaged = False
    for row in rows:
        symbol = _symbol(row.get("symbol"))
        if endpoint == "POSITIONS":
            size, index = amount(row.get("size"), nonnegative=True), row.get("positionIdx")
            if (type(index) is not int or index not in (0, 1, 2)
                    or row.get("side") not in ("", "Buy", "Sell")
                    or size > 0 and row.get("side") not in ("Buy", "Sell")
                    or (symbol, index) in positions_seen):
                raise MonitoringError("FRESH_MONITORING_ACCOUNT_POSITION_INVALID")
            positions_seen.add((symbol, index))
            if size != 0:
                if not allow_unmanaged_other_symbols or symbol in selected_symbols:
                    raise MonitoringError("FRESH_MONITORING_EXTERNAL_ACTIVITY")
                unmanaged = True
        else:
            if not allow_unmanaged_other_symbols or symbol in selected_symbols:
                raise MonitoringError("FRESH_MONITORING_EXTERNAL_ACTIVITY")
            created = _timestamp(row.get("createdTime"))
            # A current still-active order may predate seven days. The original
            # parser checks its complete row; the authenticated page bounds now.
            parsed = _order(row, symbol, HistoryWindow(created, created))
            if parsed.updated_ms > server_time_ms or parsed.venue_order_id in orders_seen:
                raise MonitoringError("FRESH_MONITORING_ACCOUNT_ORDER_INVALID")
            orders_seen.add(parsed.venue_order_id)
            unmanaged = True
    return unmanaged


def _instrument_metadata_bytes(instrument: Any) -> bytes:
    from .native_monitor_client import instrument_semantics

    value = instrument_semantics(instrument)
    # The original semantics fixes each currency as a five-string tuple. Use
    # JSON arrays for these three fields; all other values keep strict checks.
    for name in ("base_currency", "quote_currency", "settlement_currency"):
        value[name] = list(value[name])
    return canonical_bytes(value)


class _OpaqueFresh:
    __slots__ = ()

    def __new__(cls) -> Self:
        raise MonitoringError("FRESH_MONITORING_ORIGINAL_ISSUER_REQUIRED")

    def __copy__(self) -> NoReturn:
        raise MonitoringError("FRESH_MONITORING_ORIGINAL_ISSUER_REQUIRED")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        raise MonitoringError("FRESH_MONITORING_ORIGINAL_ISSUER_REQUIRED")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise MonitoringError("FRESH_MONITORING_ORIGINAL_ISSUER_REQUIRED")

    def __repr__(self) -> str:
        return "FreshMonitoringHandleV1(opaque=True)"


class FreshMonitoringWitnessV1(_OpaqueFresh):
    __slots__ = (
        "__weakref__",
        "_assembly",
        "_channel",
        "_lease",
        "_lock",
        "_loop",
        "_origin",
        "_runtime",
        "_session",
        "_trader_id",
        "_worker",
    )
    _origin: FreshMonitoringBootstrapV1
    _worker: ExecutionWorker
    _runtime: NativeExecutionRuntime
    _assembly: NativeNodeAssembly
    _session: BybitDemoAccountSession
    _channel: NativeChannel
    _lock: AccountWorkerLock
    _lease: int
    _loop: asyncio.AbstractEventLoop
    _trader_id: str

    def require(self) -> None:
        if _WITNESSES.get(id(self)) is not self:
            raise MonitoringError("FRESH_MONITORING_ORIGINAL_ISSUER_REQUIRED")
        self._origin.owner_guard()


class FreshMonitoringReceiptV1(_OpaqueFresh):
    __slots__ = ()


@dataclass(frozen=True, repr=False)
class FreshMonitoringTargetV1:
    scope: ProtectionScope
    symbols: tuple[str, ...]
    startup_id: str
    origin: str
    source_generation_id: str | None
    metadata_profile: str
    policy_revision: str
    native: NativeMonitoringSummary
    application: ApplicationMonitoringSummary
    stream_stamp: StreamStamp
    _witness: FreshMonitoringWitnessV1

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            repr(
                (
                    "FRESH_NATIVE_MONITOR_BOOTSTRAP_V1",
                    self.scope,
                    self.symbols,
                    self.startup_id,
                    self.origin,
                    self.source_generation_id,
                    self.metadata_profile,
                    self.policy_revision,
                    self.native.digest,
                    self.application.digest,
                    self.stream_stamp,
                )
            ).encode()
        ).hexdigest()


@dataclass(frozen=True)
class _FreshReceiptSlot:
    target: FreshMonitoringTargetV1
    binding: AuthenticatedAccountBinding
    attestation: object
    phase: str
    started_at: Any
    started_mono: float
    view: MonitoringReceiptView
    whole_sources: tuple[
        tuple[Literal["POSITIONS", "ORDERS"], tuple[BybitModelSourceRefV1, ...], object], ...
    ]
    unattributed_history_sha256: str
    unmanaged_usdt_linear_activity: bool


def require_fresh_target_v1(target: object, session: object, phase: str) -> None:
    if type(target) is not FreshMonitoringTargetV1 or _TARGETS.get(id(target)) is not target:
        raise MonitoringError("FRESH_MONITORING_CAPTURED_TARGET_REQUIRED")
    witness = target._witness
    if (
        type(witness) is not FreshMonitoringWitnessV1
        or witness._session is not session
        or phase not in ("PRE", "POST")
    ):
        raise MonitoringError("FRESH_MONITORING_TARGET_AUTHORITY_REQUIRED")
    witness.require()
    stamp = target.stream_stamp
    if (
        phase == "PRE"
        and stamp != StreamStamp(0, "", 0, 0)
        or phase == "POST"
        and (stamp.epoch <= 0 or not stamp.conn_id or stamp.received != stamp.delivered)
    ):
        raise MonitoringError("FRESH_MONITORING_PHASE_REQUIRED")
    current = witness._origin.capture()
    if (
        current.fingerprint != target.fingerprint
        or current.native != target.native
        or current.application != target.application
    ):
        raise _MonitoringTargetInvalidated("FRESH_MONITORING_TARGET_CHANGED")


def compare_fresh_observation_v1(
    target: FreshMonitoringTargetV1, observation: MonitoringObservation
) -> MonitoringAssessment:
    from .monitoring_unattributed_history import unattributed_history_valid

    if (
        type(target) is not FreshMonitoringTargetV1
        or type(observation) is not MonitoringObservation
        or observation.scope != target.scope
        or observation.symbols != target.symbols
        or observation.orders
        or observation.executions
        or not unattributed_history_valid(target, observation)
        or len(observation.positions) != len(target.symbols)
        or {p.symbol for p in observation.positions} != set(target.symbols)
        or any(p.signed_quantity != 0 for p in observation.positions)
    ):
        return MonitoringAssessment(
            MonitoringStatus.BLOCKED, ("FRESH_MONITORING_EXTERNAL_ACTIVITY",)
        )
    return MonitoringAssessment(MonitoringStatus.MATCHED)


def empty_sealed_observation_candidate_v1(worker: ExecutionWorker) -> bool:
    """Routing only; construction rechecks every original owner independently."""
    status, assembly, restart = worker.native_restart_status, worker._assembly, worker._restart
    if (worker._factory is not None or assembly is None or type(restart) is not NativeRestartCoordinator
            or restart.status is not status or status.status != "RESTORED_LOCAL_CACHE"
            or not status.generation_id or any((status.orders, status.positions, status.missing_owned_orders))):
        return False
    cache = assembly.node.cache
    return (not any((cache.instruments(), cache.accounts(), cache.orders(), cache.positions(),
                     cache.client_order_ids(), cache.position_ids(), cache.position_snapshot_ids()))
            and _empty_application(worker.directory))


class FreshMonitoringBootstrapV1:
    """Explicit first-start controller; no reconnect, restoration or financial rights."""

    def __init__(self, worker: ExecutionWorker) -> None:
        from .account_binding import BybitDemoAccountSession
        from .native_node import NativeNodeAssembly, _native_modules
        from .worker import ExecutionWorker

        if (
            type(worker) is not ExecutionWorker
            or worker._factory is not None
            or type(worker._assembly) is not NativeNodeAssembly
            or worker._lock is None
            or type(worker.session) is not BybitDemoAccountSession
            or type(worker._restart) is not NativeRestartCoordinator
            or worker._restart.status is not worker.native_restart_status
            or not (worker.native_restart_status.status == "FRESH_EMPTY_CACHE"
                    and worker.native_restart_status.generation_id is None
                    or empty_sealed_observation_candidate_v1(worker))
            or worker._node_task is not None
            or worker._monitoring is not None
        ):
            raise RuntimeBlocked("FRESH_MONITORING_NEW_ORIGINAL_WORKER_REQUIRED")
        if type(worker._assembly.node) is not _native_modules()["node"].TradingNode:
            raise RuntimeBlocked("FRESH_MONITORING_GENUINE_NATIVE_NODE_REQUIRED")
        self.worker, self.runtime, self.assembly = worker, worker.runtime, worker._assembly
        self.session, self.binding = worker.session, worker.binding
        self._read_client = self.session._client
        self._read_http = self._read_client._client
        self._credential_pair = self.session._credential_pair
        self._read_base_url = allowed_host("demo")
        self.lock, self.lease = worker._lock, worker._lock.acquisition_id
        self.loop, self.task = asyncio.get_running_loop(), asyncio.current_task()
        self.node, self.cache = self.assembly.node, self.assembly.node.cache
        self.client = self.assembly._monitor_client()
        self.provider, self.adapter = (
            self.client.bybit_instrument_provider,
            self.client._http_client,
        )
        self.strategy, self.channel, self.bridge = (
            self.assembly.strategy,
            self.assembly.channel,
            self.assembly.bridge,
        )
        self.journal, self.store, self.history = (
            self.runtime.journal,
            self.runtime.store,
            self.runtime.history_store,
        )
        self.scope, self.symbols, self.trader_id = worker.scope, worker.symbols, worker.trader_id
        self.restart, self.restart_status = worker._restart, worker.native_restart_status
        self.origin = ("FRESH_NATIVE_MONITOR_BOOTSTRAP_V1" if self.restart_status.status == "FRESH_EMPTY_CACHE"
                       else "EMPTY_SEALED_OBSERVATION_BOOTSTRAP_V1")
        self.source_generation_id = self.restart_status.generation_id
        self.metadata_profile = worker.metadata_profile
        self.policy_book, self.policy = worker.policy_book, worker.policy_book.read()
        self._session_clock, self._monotonic, self._clock = (
            self.session._clock,
            self.session._monotonic,
            worker.clock,
        )
        self.startup_id, self._issuer = uuid4().hex, _ISSUER
        self._attached = False
        self.attestation: object = None
        self.in_progress, self.armed = True, False
        self.metadata_phase = "EMPTY"
        self.metadata: tuple[tuple[str, bytes], ...] = ()
        self.state = WorkerMonitoringStatus("RECONCILING", self.scope, self.symbols)
        self.checkpoint: FreshMonitoringTargetV1 | None = None
        self._receipts: dict[FreshMonitoringReceiptV1, _FreshReceiptSlot] = {}
        self.witness = object.__new__(FreshMonitoringWitnessV1)
        for name, value in (
            ("_origin", self),
            ("_worker", worker),
            ("_runtime", self.runtime),
            ("_assembly", self.assembly),
            ("_session", self.session),
            ("_channel", self.channel),
            ("_lock", self.lock),
            ("_lease", self.lease),
            ("_loop", self.loop),
            ("_trader_id", self.trader_id),
        ):
            object.__setattr__(self.witness, name, value)
        _WITNESSES[id(self.witness)] = self.witness
        self.budget = MonitoringReadBudget(guard=self.owner_guard)
        self.owner_guard()
        if self.cache.accounts() or self.cache.instruments():
            raise MonitoringError("FRESH_MONITORING_EMPTY_NATIVE_CACHE_REQUIRED")
        self._attached = True

    def _require_session_origin(self) -> None:
        # The original Session guard's credential/origin meaning, using only
        # retained values after the final genuine clock/monotonic callbacks.
        session, client = self.session, self._read_client
        if (
            session._closed
            or session._client is not client
            or session._credential_client is not client
            or type(client) is not BybitReadOnlyClient
            or client._client is not self._read_http
            or self._read_http.is_closed
            or session._credential_pair is not self._credential_pair
            or (session._api_key, session._api_secret) != self._credential_pair
            or client._api_key != self._credential_pair[0]
            or client._api_secret != self._credential_pair[1]
            or client.environment != "demo"
            or client.base_url != self._read_base_url
        ):
            raise MonitoringError("FRESH_MONITORING_SESSION_ORIGIN_CHANGED")

    def _original_links(self, *, cleanup: bool = False) -> None:
        w = self.worker
        if (
            self._issuer is not _ISSUER
            or _WITNESSES.get(id(self.witness)) is not self.witness
            or asyncio.get_running_loop() is not self.loop
            or w._loop is not self.loop
            or w._runtime is not self.runtime
            or w._assembly is not self.assembly
            or w.session is not self.session
            or w.binding is not self.binding
            or w._lock is not self.lock
            or self.runtime.worker_lock is not self.lock
            or not self.lock.acquired
            or self.lock.acquisition_id != self.lease
            or self.node is not self.assembly.node
            or self.node.cache is not self.cache
            or self.assembly.execution_client is not self.client
            or self.client.bybit_instrument_provider is not self.provider
            or self.client._http_client is not self.adapter
            or self._attached
            and w._monitoring is not self
            or self.assembly.strategy is not self.strategy
            or self.assembly.bridge is not self.bridge
            or self.assembly.channel is not self.channel
            or self.runtime.bridge is not self.bridge
            or w._journal is not self.journal
            or w._store is not self.store
            or w._history_store is not self.history
            or self.runtime.journal is not self.journal
            or self.runtime.store is not self.store
            or self.runtime.history_store is not self.history
            or self.runtime.receipt_session is not self.session
            or w.scope != self.scope
            or w.symbols != self.symbols
            or w.trader_id != self.trader_id
            or w._restart is not self.restart
            or w.native_restart_status is not self.restart_status
            or self.restart.status is not self.restart_status
            or w.native_restart_status.generation_id != self.source_generation_id
            or w.metadata_profile != self.metadata_profile
            or self.restart.metadata_profile != self.metadata_profile
            or self.client._metadata_profile != self.metadata_profile
            or self.adapter._metadata_profile != self.metadata_profile
            or w.clock is not self._clock
            or self.session._clock is not self._session_clock
            or self.session._monotonic is not self._monotonic
            or not cleanup
            and (w._stopped or w._stopping or w._channel_closed)
        ):
            raise MonitoringError("FRESH_MONITORING_OWNER_CHANGED")

    def owner_guard(self) -> None:
        self._original_links()
        if (
            self.state.phase == "ATTENTION"
            or self.client.monitoring_fault
            or self.worker.policy_book is not self.policy_book
            or self.policy_book.read() != self.policy
            or self.policy["desired_state"] != "STOPPED"
            or self.in_progress
            and self.task is not None
            and self.task.cancelling()
            or self.in_progress
            and (self.worker._operations != 1 or self.worker._staging)
        ):
            raise MonitoringError("FRESH_MONITORING_POLICY_OR_TASK_CHANGED")
        self.runtime._guard()
        self.session._guard()
        if self.attestation is not None and (
            self.session._attested is not self.attestation
            or self.session._binding is not self.binding
        ):
            raise MonitoringError("FRESH_MONITORING_SESSION_REPLACED")
        if not _empty_application(self.worker.directory):
            raise MonitoringError("FRESH_MONITORING_EMPTY_APPLICATION_REQUIRED")
        if (
            self.cache.orders()
            or self.cache.positions()
            or self.cache.client_order_ids()
            or self.cache.position_ids()
            or self.cache.position_snapshot_ids()
        ):
            raise MonitoringError("FRESH_MONITORING_EMPTY_NATIVE_CACHE_REQUIRED")
        accounts, event = self.cache.accounts(), self.client._monitor_account_event
        if event is None:
            if accounts:
                raise MonitoringError("FRESH_MONITORING_ORIGINAL_ACCOUNT_ACK_REQUIRED")
        else:
            account = self.cache.account(self.client.account_id)
            if len(accounts) != 1 or accounts[0] is not account or account.last_event is not event:
                raise MonitoringError("FRESH_MONITORING_ORIGINAL_ACCOUNT_ACK_REQUIRED")
        self._original_links()

    def require_cleanup_owner(self) -> None:
        self._original_links(cleanup=True)
        self.runtime._guard()

    def fresh_guard(self) -> None:
        self.owner_guard()
        self.budget.check()
        self.session.require_observation_binding(self.binding, scope=self.scope)
        self._original_links()

    def begin_metadata(self, client: object) -> None:
        from .native_monitor_client import MonitoringBybitExecutionClient

        self.fresh_guard()
        if (
            type(client) is not MonitoringBybitExecutionClient
            or client is not self.client
            or self.metadata_phase != "EMPTY"
            or self.cache.instruments()
            or self.cache.accounts()
            or self.provider.get_all()
            or client._monitor_target is not None
            or client.is_connected
        ):
            raise MonitoringError("FRESH_MONITORING_INITIAL_METADATA_REQUIRED")
        self.metadata_phase = "LOADING"

    def complete_metadata(self, client: object) -> None:
        self.fresh_guard()
        expected = {s + "-LINEAR.BYBIT" for s in self.symbols}
        instruments = self.provider.get_all()
        if (
            client is not self.client
            or self.metadata_phase != "LOADING"
            or {str(k) for k in instruments} != expected
            or self.cache.instruments()
            or self.cache.accounts()
        ):
            raise MonitoringError("FRESH_MONITORING_INITIAL_METADATA_REQUIRED")
        for instrument in instruments.values():
            require_instrument_metadata_profile_v1(instrument, self.metadata_profile)
        metadata = tuple(sorted((str(k), _instrument_metadata_bytes(v)) for k, v in instruments.items()))
        self.fresh_guard()
        # Genuine provider definitions only; the same original fresh cache is used.
        for instrument in instruments.values():
            self.cache.add_instrument(instrument)
        self.metadata, self.metadata_phase = metadata, "LOADED"
        self.capture()

    def capture(self) -> FreshMonitoringTargetV1:
        from .monitoring_reconciliation import _native_summary

        self.owner_guard()
        if self.metadata_phase != "LOADED":
            raise MonitoringError("FRESH_MONITORING_METADATA_NOT_LOADED")
        actual = tuple(
            sorted((str(i.id), _instrument_metadata_bytes(i)) for i in self.cache.instruments())
        )
        if actual != self.metadata:
            raise MonitoringError("FRESH_MONITORING_INSTRUMENT_DRIFT")
        identity = NativeRestartIdentity(
            self.scope,
            self.channel.native_account_id,
            str(self.strategy.trader_id),
            str(self.strategy.id),
            tuple(sorted(s + "-LINEAR.BYBIT" for s in self.symbols)),
        )
        payload = capture_native_cache(self.cache, identity=identity, owners=())
        native = _native_summary(
            self.cache, self.symbols, hashlib.sha256(canonical_bytes(payload)).hexdigest()
        )
        application = self.runtime.monitoring_state()
        if any(
            (
                application.owned,
                application.commands,
                application.positions,
                application.candidates,
                application.credits,
                application.history_credits,
                application.conflicts,
            )
        ):
            raise MonitoringError("FRESH_MONITORING_EMPTY_APPLICATION_REQUIRED")
        self.owner_guard()
        target = FreshMonitoringTargetV1(
            self.scope,
            self.symbols,
            self.startup_id,
            self.origin,
            self.source_generation_id,
            self.metadata_profile,
            str(self.policy["revision"]),
            native,
            application,
            self.client.monitoring_stamp(),
            self.witness,
        )
        _TARGETS[id(target)] = target
        return target

    async def _whole_account_flat(self) -> _WholeScopeSourcesV1:
        sources, _unmanaged = await self._capture_whole_scope_v1(allow_unmanaged_other_symbols=False)
        return sources

    async def _whole_account_observation_v1(self) -> tuple[_WholeScopeSourcesV1, bool]:
        if self.metadata_profile != SDK1231_OBSERVATION_UNKNOWN_FEES_V1:
            raise MonitoringError("FRESH_MONITORING_OBSERVATION_PROFILE_REQUIRED")
        return await self._capture_whole_scope_v1(allow_unmanaged_other_symbols=True)

    async def _capture_whole_scope_v1(
        self, *, allow_unmanaged_other_symbols: bool,
    ) -> tuple[_WholeScopeSourcesV1, bool]:
        from .bybit_position_order_projections_v1 import _page

        if allow_unmanaged_other_symbols and self.metadata_profile != SDK1231_OBSERVATION_UNKNOWN_FEES_V1:
            raise MonitoringError("FRESH_MONITORING_OBSERVATION_PROFILE_REQUIRED")
        self.fresh_guard()
        unmanaged = False
        retained: list[
            tuple[Literal["POSITIONS", "ORDERS"], tuple[BybitModelSourceRefV1, ...], object]
        ] = []
        endpoints: tuple[Literal["POSITIONS", "ORDERS"], ...] = ("POSITIONS", "ORDERS")
        for endpoint in endpoints:
            positions_seen: set[tuple[str, int]] = set()
            orders_seen: set[str] = set()
            sources = await (
                self.session._capture_linear_positions_v1
                if endpoint == "POSITIONS"
                else self.session._capture_linear_orders_v1
            )(self.binding, scope=self.scope, budget=self.budget, owner_guard=self.owner_guard)
            self.session._require_linear_held_v1(
                endpoint, sources, binding=self.binding, scope=self.scope
            )
            slot = self.session._linear_private_state_v1(endpoint)[1]
            if slot is None:
                raise MonitoringError("FRESH_MONITORING_ACCOUNT_SOURCE_REQUIRED")
            for page in slot.pages:
                rows, _cursor = _page(
                    page.source, page.capture.raw_body, orders=endpoint == "ORDERS"
                )
                if page.source.envelope_time_ms is None:
                    raise MonitoringError("FRESH_MONITORING_ACCOUNT_SOURCE_REQUIRED")
                unmanaged = _whole_scope_activity_v1(endpoint, rows,
                    selected_symbols=self.symbols, allow_unmanaged_other_symbols=allow_unmanaged_other_symbols,
                    server_time_ms=page.source.envelope_time_ms, positions_seen=positions_seen,
                    orders_seen=orders_seen) or unmanaged
            self.session._require_linear_held_v1(
                endpoint, sources, binding=self.binding, scope=self.scope
            )
            self.fresh_guard()
            retained.append((endpoint, sources, slot))
        return tuple(retained), unmanaged

    async def read_receipt(
        self, target: FreshMonitoringTargetV1, phase: Literal["PRE", "POST"]
    ) -> FreshMonitoringReceiptV1:
        require_fresh_target_v1(target, self.session, phase)
        self.fresh_guard()
        if len(self._receipts) >= 4:
            raise MonitoringError("FRESH_MONITORING_RECEIPT_CAPACITY")
        started, mono = utc(self.session._clock()), self.session._mono()
        binding, attestation = self.binding, self.session._attested
        if self.metadata_profile == SDK1231_OBSERVATION_UNKNOWN_FEES_V1:
            whole_sources, unmanaged = await self._whole_account_observation_v1()
        else:
            whole_sources, unmanaged = await self._whole_account_flat(), False
        reader = BybitMonitoringReader(
            self.session,
            binding,
            target=target,
            guard=lambda: require_fresh_target_v1(target, self.session, phase),
            owner_guard=self.owner_guard,
        )
        observation = await reader.read()
        require_fresh_target_v1(target, self.session, phase)
        self.fresh_guard()
        assessment = compare_fresh_observation_v1(target, observation)
        from .monitoring_unattributed_history import unattributed_history_sha256

        receipt = object.__new__(FreshMonitoringReceiptV1)
        self._receipts[receipt] = _FreshReceiptSlot(
            target,
            binding,
            attestation,
            phase,
            started,
            mono,
            MonitoringReceiptView(observation, assessment, target.fingerprint, phase),
            whole_sources,
            unattributed_history_sha256(observation),
            unmanaged,
        )
        try:
            self.require_receipt(receipt, target, phase)
            return receipt
        except BaseException:
            self._receipts.pop(receipt, None)
            raise

    def require_receipt(
        self, receipt: FreshMonitoringReceiptV1, target: FreshMonitoringTargetV1, phase: str
    ) -> MonitoringReceiptView:
        if type(receipt) is not FreshMonitoringReceiptV1:
            raise MonitoringError("FRESH_MONITORING_ORIGINAL_RECEIPT_REQUIRED")
        slot = self._receipts.get(receipt)
        if (
            slot is None
            or slot.target is not target
            or slot.binding is not self.binding
            or slot.attestation is not self.session._attested
            or slot.phase != phase
        ):
            raise MonitoringError("FRESH_MONITORING_ORIGINAL_RECEIPT_REQUIRED")
        require_fresh_target_v1(target, self.session, phase)
        self.fresh_guard()
        for endpoint, sources, _original_slot in slot.whole_sources:
            self.session._require_linear_held_v1(
                endpoint, sources, binding=slot.binding, scope=self.scope
            )
        now, mono = utc(self.session._clock()), self.session._mono()
        maximum = min(self.session._max_age, timedelta(seconds=30))
        if (
            not timedelta(0) <= now - slot.started_at <= maximum
            or not 0 <= mono - slot.started_mono <= maximum.total_seconds()
            or any(
                not timedelta(0) <= now - (EPOCH + timedelta(milliseconds=t)) <= maximum
                for t in (
                    *slot.view.observation.response_times_ms,
                    *slot.view.observation.local_response_times_ms,
                )
            )
            or any(
                source.envelope_time_ms is None
                or not timedelta(0)
                <= now - (EPOCH + timedelta(milliseconds=source.envelope_time_ms))
                <= maximum
                for _kind, sources, _original in slot.whole_sources
                for source in sources
            )
        ):
            self._receipts.pop(receipt, None)
            raise MonitoringError("FRESH_MONITORING_RECEIPT_EXPIRED")
        # Pure original links after the final guard/clock/monotonic callbacks.
        from .monitoring_unattributed_history import unattributed_history_sha256

        try:
            from .account_binding import _linear_source_data_v1
            from .bybit_position_order_projections_v1 import _page

            unmanaged = False
            for endpoint, sources, original_slot in slot.whole_sources:
                current_slot = self.session._linear_private_state_v1(endpoint)[1]
                if current_slot is None or current_slot is not original_slot:
                    raise MonitoringError("FRESH_MONITORING_ACCOUNT_OBSERVATION_CHANGED")
                positions_seen: set[tuple[str, int]] = set()
                orders_seen: set[str] = set()
                for source, page in zip(sources, current_slot.pages, strict=True):
                    if (page.source is not source or source.envelope_time_ms is None
                            or _linear_source_data_v1(source, BybitModelSourceRefV1) != page.source_preimage
                            or _account_info_capture_data_v1(page.capture) != page.capture_preimage):
                        raise MonitoringError("FRESH_MONITORING_ACCOUNT_OBSERVATION_CHANGED")
                    rows, _cursor = _page(source, page.capture.raw_body, orders=endpoint == "ORDERS")
                    unmanaged = _whole_scope_activity_v1(endpoint, rows, selected_symbols=self.symbols,
                        allow_unmanaged_other_symbols=self.metadata_profile == SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
                        server_time_ms=source.envelope_time_ms, positions_seen=positions_seen,
                        orders_seen=orders_seen) or unmanaged
            if type(slot.unmanaged_usdt_linear_activity) is not bool or unmanaged != slot.unmanaged_usdt_linear_activity:
                raise MonitoringError("FRESH_MONITORING_ACCOUNT_OBSERVATION_CHANGED")
        except (ValueError, TypeError, AttributeError):
            self._receipts.pop(receipt, None)
            raise MonitoringError("FRESH_MONITORING_ACCOUNT_OBSERVATION_CHANGED") from None
        try:
            if unattributed_history_sha256(slot.view.observation) != slot.unattributed_history_sha256:
                raise MonitoringError("MONITORING_UNATTRIBUTED_HISTORY_CHANGED")
        except (ValueError, TypeError, AttributeError):
            self._receipts.pop(receipt, None)
            raise MonitoringError("MONITORING_UNATTRIBUTED_HISTORY_CHANGED") from None
        try:
            self._require_session_origin()
        except MonitoringError:
            self._receipts.clear()
            raise
        if (
            self._receipts.get(receipt) is not slot
            or self.worker._monitoring is not self
            or self.worker._stopped
            or self.worker._stopping
            or self.worker._channel_closed
            or not self.lock.acquired
            or _TARGETS.get(id(target)) is not target
            or _WITNESSES.get(id(self.witness)) is not self.witness
            or self._issuer is not _ISSUER
            or self.state.phase == "ATTENTION"
            or self.session._binding is not slot.binding
            or self.session._closed
            or self.session._attested is not slot.attestation
            or self.session._clock is not self._session_clock
            or self.session._monotonic is not self._monotonic
            or self.worker.binding is not self.binding
            or self.worker.session is not self.session
            or self.worker.clock is not self._clock
            or self.worker._runtime is not self.runtime
            or self.runtime.worker_lock is not self.lock
            or self.worker._lock is not self.lock
            or self.lock.acquisition_id != self.lease
            or self.worker._restart is not self.restart
            or self.worker.native_restart_status is not self.restart_status
            or self.restart.status is not self.restart_status
            or self.worker.metadata_profile != self.metadata_profile
            or self.restart.metadata_profile != self.metadata_profile
            or self.client._metadata_profile != self.metadata_profile
            or self.adapter._metadata_profile != self.metadata_profile
            or self.worker._assembly is not self.assembly
            or self.assembly.node is not self.node
            or self.assembly.channel is not self.channel
            or self.assembly.bridge is not self.bridge
            or self.assembly.strategy is not self.strategy
            or self.runtime.bridge is not self.bridge
            or self.worker._journal is not self.journal
            or self.worker._store is not self.store
            or self.worker._history_store is not self.history
            or self.assembly.node.cache is not self.cache
            or self.assembly.execution_client is not self.client
            or self.client._instrument_provider is not self.provider
            or self.client._http_client is not self.adapter
            or self.client._monitor_binding is not slot.binding
            or self.client._monitor_attestation is not slot.attestation
            or self.client._monitor_fault is not None
            or self.adapter._session is not self.session
            or self.adapter._binding is not slot.binding
            or self.adapter._cancelled
            or any(
                (
                    self.session._linear_positions_current
                    if endpoint == "POSITIONS"
                    else self.session._linear_orders_current
                )
                is not original
                for endpoint, _sources, original in slot.whole_sources
            )
        ):
            raise MonitoringError("FRESH_MONITORING_ORIGINAL_RECEIPT_REQUIRED")
        return slot.view

    def event_completed(self) -> None:
        # A fresh origin has no owned financial event to credit or accept.
        self.retire("FRESH_MONITORING_UNEXPECTED_NATIVE_EVENT")

    def retire(self, reason: str) -> None:
        self._receipts.clear()
        self.client.retire_monitoring(reason)
        self.state = replace(
            self.state,
            phase="ATTENTION",
            reconciliation_current=False,
            fresh_read_authority=False,
            fault=self.client.monitoring_fault or reason,
        )
        with suppress(RuntimeBlocked):
            self.runtime.transport_lost()

    def snapshot(self) -> WorkerMonitoringStatus:
        if self.state.phase == "MONITORING":
            try:
                state = self.state
                current = self.capture()
                self.assembly.require_monitoring_ready()
                if (
                    self.checkpoint is None
                    or current.native != self.checkpoint.native
                    or current.application != self.checkpoint.application
                ):
                    raise MonitoringError("FRESH_MONITORING_UNEXPLAINED_MUTATION")
                fresh = True
                try:
                    self.session.require_observation_binding(self.binding, scope=self.scope)
                except ValueError:
                    fresh = False
                self._require_session_origin()
                if self.state is not state:
                    raise MonitoringError("FRESH_MONITORING_OWNER_CHANGED")
                self.state = replace(
                    state, stream_stamp=current.stream_stamp, fresh_read_authority=fresh
                )
            except Exception:  # noqa: BLE001 - fixed state only, never provider diagnostics.
                self.retire("FRESH_MONITORING_CURRENT_STATE_INVALID")
        return self.state

    async def _run_node(self) -> None:
        try:
            await self.node.run_async()
        except Exception:  # noqa: BLE001 - retrieve original task errors safely.
            self.retire("FRESH_MONITORING_NODE_FAILED")
        finally:
            if not self.worker._stopped:
                self.retire("FRESH_MONITORING_NODE_ENDED")

    async def run(self) -> WorkerMonitoringStatus:
        try:
            with self.session._monitoring_operation(self.budget):
                async with asyncio.timeout_at(self.budget.deadline):
                    fresh = await self.session.refresh_monitoring_binding(
                        self.binding, scope=self.scope
                    )
                    self.owner_guard()
                    self.worker.binding = self.binding = fresh
                    self.attestation = self.session._attested
                    await self.client.prepare_fresh_metadata_v1(self.witness)
                    target = self.capture()
                    pre = await self.read_receipt(target, "PRE")
                    try:
                        if (
                            self.require_receipt(pre, target, "PRE").assessment.status
                            is MonitoringStatus.BLOCKED
                        ):
                            raise MonitoringError("FRESH_MONITORING_PRE_MISMATCH")
                        self._receipts.pop(pre)
                        self.assembly.arm_fresh_monitoring_v1(target)
                        self.armed = True
                        self.state = replace(self.state, phase="CONNECTING")
                        self.worker._node_task = self.loop.create_task(self._run_node())
                    finally:
                        self._receipts.pop(pre, None)
                    await self.assembly.wait_monitoring_ready()
                    await self.assembly.monitoring_barrier()
                    self.fresh_guard()
                    for _ in range(3):
                        target = self.capture()
                        post: FreshMonitoringReceiptV1 | None = None
                        try:
                            post = await self.read_receipt(target, "POST")
                            await self.assembly.monitoring_barrier()
                            self.assembly.require_monitoring_ready()
                            current = self.capture()
                            state = WorkerMonitoringStatus(
                                "MONITORING",
                                self.scope,
                                self.symbols,
                                reconciled_at=self.worker.clock().isoformat(),
                                reconciliation_fingerprint=current.fingerprint,
                                current_fingerprint=current.fingerprint,
                                current_revision=current.application.revision,
                                reconciliation_current=True,
                                fresh_read_authority=True,
                                stream_stamp=current.stream_stamp,
                                unmanaged_usdt_linear_activity=self._receipts[post].unmanaged_usdt_linear_activity,
                            )
                            view = self.require_receipt(post, target, "POST")
                            if view.assessment.status is MonitoringStatus.BLOCKED:
                                raise MonitoringError("FRESH_MONITORING_POST_MISMATCH")
                            self._receipts.pop(post)
                            self.checkpoint, self.state = current, state
                            return self.state
                        except _MonitoringTargetInvalidated:
                            await self.assembly.monitoring_barrier()
                            self.fresh_guard()
                        finally:
                            if post is not None:
                                self._receipts.pop(post, None)
                    raise MonitoringError("FRESH_MONITORING_RETARGET_LIMIT")
        except BaseException:
            self.retire("FRESH_MONITORING_ADMISSION_FAILED")
            self.worker._stopping = True
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

    async def _cleanup(self) -> None:
        try:
            await self.worker._finish_stop(self.runtime)
        except BaseException:  # noqa: BLE001 - retain original resources on any cleanup failure.
            self.state = replace(
                self.state,
                phase="ATTENTION",
                resources_retained=True,
                stream_stamp=self.client.monitoring_stamp(),
            )
