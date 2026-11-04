"""Worker-owned approved built-in signals; never reservation or financial authority."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NoReturn, Self, SupportsIndex, cast

from ..market_data.closed_bars import ClosedBarScope, ClosedBarSnapshot, ClosedBarSnapshotStore
from ..market_data.service import MarketDataService
from . import builtin_sma_atr_v1 as builtin
from . import risk_v2_contracts as c
from .policy_sections import catalog_from_wire, eligible_references
from .runtime_policy import RuntimePolicyBook
from .strategy_catalog import ApprovalEnvironment, require_strategy_references
from .strategy_publication_v1 import (
    StrategyArtifactBytesV1,
    StrategyPublicationOwnerV1,
    _publication_read_v1,
)

if TYPE_CHECKING:
    from .worker import ExecutionWorker


def _reject(code: str = "BUILTIN_STRATEGY_SOURCE_REQUIRED") -> NoReturn:
    raise ValueError(code)


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        _reject()
    return value.astimezone(UTC)


def _encoded(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _snapshot_key(snapshot: ClosedBarSnapshot) -> str:
    return _encoded({"scope": snapshot.scope.key, "snapshot_id": snapshot.snapshot_id,
        "watermark": snapshot.watermark.isoformat(), "collected_at": snapshot.collected_at.isoformat(),
        "cutoff": snapshot.cutoff.isoformat(),
        "bars": [[bar.timestamp.isoformat(), *(str(getattr(bar, name)) for name in
            ("open", "high", "low", "close", "volume")), bar.is_closed] for bar in snapshot.candles]})


class WorkerStrategySignalV1:
    """Only the original worker's retained issuer can consume this handle."""
    __slots__ = ()

    def __new__(cls) -> Self:
        _reject()

    def __copy__(self) -> NoReturn:
        _reject()

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        _reject()

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        _reject()


@dataclass(frozen=True)
class WorkerStrategySignalDataV1:
    scope: c.RiskScopeV2
    signal_id: str
    policy_revision: int
    publication_generation: int
    implementation_sha256: str
    configuration_sha256: str
    approval_sha256: str
    snapshot_id: str
    observed_at: datetime
    bar_watermark: datetime
    valid_until: datetime
    direction: builtin.BuiltinDirectionV1
    financial_authority: Literal[False] = field(default=False, init=False)
    trading_enabled: Literal[False] = field(default=False, init=False)


def _data_key(value: WorkerStrategySignalDataV1) -> str:
    if type(value) is not WorkerStrategySignalDataV1 or type(value.direction) is not builtin.BuiltinDirectionV1:
        _reject()
    direction = value.direction
    ratios = []
    for ratio in (direction.entry_reference_price, direction.stop_price, direction.take_profit_price):
        if ratio is not None and (type(ratio) is not Fraction or type(ratio.numerator) is not int
                                   or type(ratio.denominator) is not int or ratio.denominator <= 0):
            _reject()
        ratios.append(None if ratio is None else (ratio.numerator, ratio.denominator))
    return _encoded({"scope": c._scope_payload_v2(value.scope), "signal_id": value.signal_id,
        "policy_revision": value.policy_revision, "publication_generation": value.publication_generation,
        "implementation": value.implementation_sha256, "configuration": value.configuration_sha256,
        "approval": value.approval_sha256, "snapshot_id": value.snapshot_id,
        "observed_at": value.observed_at.isoformat(), "bar_watermark": value.bar_watermark.isoformat(),
        "valid_until": value.valid_until.isoformat(), "side": direction.side, "prices": ratios,
        "financial_authority": value.financial_authority, "trading_enabled": value.trading_enabled})


@dataclass(frozen=True)
class _Issued:
    handle: WorkerStrategySignalV1
    value: WorkerStrategySignalDataV1
    value_key: str
    snapshot_key: str
    policy_key: str
    artifact: StrategyArtifactBytesV1
    roots: tuple[object, ...]
    epoch: object


class WorkerStrategySignalOwnerV1:
    """An explicit read-only signal lane. No strategy/config/approval is seeded."""

    def __init__(self, worker: ExecutionWorker, market: MarketDataService) -> None:
        from .worker import ExecutionWorker

        if (type(worker) is not ExecutionWorker or type(market) is not MarketDataService
                or type(worker.policy_book) is not RuntimePolicyBook):
            _reject()
        worker._require_admission()
        self._worker, self._market = worker, market
        self._book = worker.policy_book
        self._publication = StrategyPublicationOwnerV1(self._book)
        self._path = Path(worker.directory) / "strategy-market.sqlite3"
        self._clock = worker.clock
        self._original = self._roots()
        self._current: _Issued | None = None
        self._busy: object | None = None
        self._epoch = object()
        self._derive = builtin.derive_direction_v1
        self._derive_code = self._derive.__code__

    def _roots(self) -> tuple[object, ...]:
        import asyncio

        worker = self._worker
        session = worker.session
        identity = session._identity
        phase = worker._model_observation_phase_v2
        if phase is None:
            phase_stop_event = phase_original_stop_event = None
        else:
            from .model_observation_phase_v2 import ModelObservationPhaseV2

            if type(phase) is not ModelObservationPhaseV2:
                _reject("MODEL_OBSERVATION_STOP_REQUESTED")
            phase_stop_event = phase._stop_event
            phase_original_stop_event = phase._original_stop_event_v2
            if (type(phase_stop_event) is not asyncio.Event
                    or type(phase_original_stop_event) is not asyncio.Event):
                _reject("MODEL_OBSERVATION_STOP_REQUESTED")
        return (worker, worker._loop, worker.scope, worker.session, worker.binding, worker.policy_book,
            phase, phase_stop_event, phase_original_stop_event,
            worker._journal, worker._store, worker._history_store, worker._runtime, worker._assembly,
            worker._lock, worker.clock, worker.directory, session._binding, identity,
            None if identity is None else identity.uid, None if identity is None else identity.observed_at,
            None if identity is None else identity.server_observed_at, session._clock, type(session)._mono,
            session.__dict__.get("_mono"), self._market, self._book, self._publication, self._path, self._publication._book, self._book.path)

    def retire(self) -> None:
        self._current = None
        self._epoch = object()

    def _closed_guard(self, token: object, epoch: object, roots: tuple[object, ...]) -> None:
        worker = self._worker
        try:
            worker._require_model_observation_not_stopped_v2()
        except BaseException:
            self.retire()
            raise
        if (self._busy is not token or self._epoch is not epoch
                or worker._strategy_signal_owner_v1 is not self
                or self._book is not worker.policy_book or self._clock is not worker.clock
                or self._derive is not builtin.derive_direction_v1 or self._derive.__code__ is not self._derive_code
                or any(a is not b for a, b in zip(self._roots(), roots, strict=True))
                or any(a is not b for a, b in zip(self._roots(), self._original, strict=True))
                or worker._stopped or worker._stopping or worker._channel_closed
                or worker._entry_binding_refresh_v2 is not None):
            _reject()

    def _guard(self) -> None:
        worker = self._worker
        try:
            worker._require_model_observation_not_stopped_v2()
        except BaseException:
            self.retire()
            raise
        worker._require_admission()
        worker.runtime._guard()
        worker.session.require_binding(worker.binding, scope=worker.scope)

    def _context(self, scope: c.RiskScopeV2, db: Any) -> tuple[int, int, str, StrategyArtifactBytesV1, builtin.BuiltinConfigurationV1]:
        c._scope_payload_v2(scope)
        worker = self._worker
        if (scope.product_scope.account != worker.scope or scope.product_scope.product != "LINEAR"
                or scope.instrument.symbol not in worker.symbols):
            _reject("BUILTIN_STRATEGY_SCOPE_MISMATCH")
        snapshot = self._book._read(db)
        if snapshot["schema_version"] != 2 or snapshot["desired_state"] != "STOPPED":
            _reject("BUILTIN_STRATEGY_APPROVAL_REQUIRED")
        policy = snapshot["policy"]
        selected_scope, strategy = policy["scope"], policy["strategy"]
        publication = _publication_read_v1(db, policy, snapshot["revision"])
        if selected_scope is None or strategy is None or publication is None:
            _reject("BUILTIN_STRATEGY_APPROVAL_REQUIRED")
        if (selected_scope["account"] != {"account_id": worker.scope.account_id, "environment": worker.scope.environment}
                or selected_scope["selected_product"] != "LINEAR"):
            _reject("BUILTIN_STRATEGY_SCOPE_MISMATCH")
        universe = selected_scope["selected_instruments" if selected_scope["mode"] == "FULL_AUTO" else "allowed_instruments"]
        instrument = {"venue": scope.instrument.venue, "product": scope.instrument.product, "symbol": scope.instrument.symbol}
        if instrument not in (universe or []):
            _reject("BUILTIN_STRATEGY_SCOPE_MISMATCH")
        allowed, selected = eligible_references(selected_scope, strategy, scope.instrument)
        references = selected if selected_scope["mode"] == "FULL_AUTO" else allowed
        if scope.strategy not in references:
            _reject("BUILTIN_STRATEGY_APPROVAL_REQUIRED")
        catalog = catalog_from_wire(strategy["catalog"])
        selection = require_strategy_references(catalog, expected_catalog_revision=catalog.revision,
            allowed=allowed, selected=selected, instrument=scope.instrument, environment=cast(ApprovalEnvironment, worker.scope.environment))
        descriptor = next((entry for entry in selection.allowed if entry.reference == scope.strategy), None)
        if descriptor is None or descriptor.engine_contract_version != builtin.ENGINE_CONTRACT_V1 or descriptor.config_schema_version != 1:
            _reject("BUILTIN_STRATEGY_APPROVAL_REQUIRED")
        generation, revision = publication["generation"], snapshot["revision"]
        artifact = self._publication._require_artifact_in_operation_v1(db, scope.strategy,
            generation=generation, policy_revision=revision)
        if artifact.implementation != builtin.implementation_bytes_v1():
            _reject("BUILTIN_STRATEGY_APPROVAL_REQUIRED")
        return revision, generation, _encoded(snapshot), artifact, builtin.parse_configuration_v1(artifact.configuration)

    async def capture(self, scope: c.RiskScopeV2) -> WorkerStrategySignalV1:
        if self._busy is not None:
            self.retire()
            _reject()
        self._current = None
        token, epoch = object(), self._epoch
        self._busy = token
        roots = self._roots()
        try:
            self._guard()
            with self._book._operation_v2() as db:
                db.execute("BEGIN")
                _revision, _generation, policy_key, artifact, configuration = self._context(scope, db)
            await MarketDataService.get_closed_snapshot(self._market, "bybit", scope.instrument.symbol,
                "perpetual", configuration.interval, configuration.required_bars + 1,
                path=self._path, clock=self._clock)
            self._guard()
            store = ClosedBarSnapshotStore(self._path)
            # Short write-reserved read transactions serialize policy/bar changes;
            # they commit no settings, approval, bars or journal mutations.
            with self._book._operation_v2() as db, store._db() as bars:
                db.execute("BEGIN IMMEDIATE")
                bars.execute("BEGIN IMMEDIATE")
                revision, generation, current_key, current_artifact, current_config = self._context(scope, db)
                if current_key != policy_key or current_artifact != artifact or current_config != configuration:
                    _reject()
                snapshot = self._snapshot(store, bars, ClosedBarScope(scope.instrument.symbol, configuration.interval))
                direction = self._derive(snapshot.candles, configuration)
                expiry = min(snapshot.collected_at + timedelta(seconds=configuration.signal_ttl_seconds),
                    snapshot.watermark + timedelta(seconds=configuration.max_bar_age_seconds))
                digest = hashlib.sha256((_snapshot_key(snapshot) + current_key).encode()).hexdigest()
                value = WorkerStrategySignalDataV1(scope, digest, revision, generation,
                    hashlib.sha256(artifact.implementation).hexdigest(), hashlib.sha256(artifact.configuration).hexdigest(),
                    hashlib.sha256(artifact.approval_record).hexdigest(), snapshot.snapshot_id,
                    snapshot.collected_at, snapshot.watermark, expiry, direction)
                value_key, snapshot_key = _data_key(value), _snapshot_key(snapshot)
                self._guard()
                now = _utc(self._clock())
                self._closed_guard(token, epoch, roots)
                self._fresh(value, now)
                if _data_key(value) != value_key or _snapshot_key(snapshot) != snapshot_key:
                    _reject()
                handle = object.__new__(WorkerStrategySignalV1)
                issued = _Issued(handle, value, value_key, snapshot_key, current_key, artifact, roots, epoch)
            self._closed_guard(token, epoch, roots)
            if _data_key(value) != value_key:
                _reject()
            self._current = issued
            return handle
        except BaseException:
            self.retire()
            raise
        finally:
            if self._busy is token:
                self._busy = None

    @staticmethod
    def _snapshot(store: ClosedBarSnapshotStore, db: Any, scope: ClosedBarScope) -> ClosedBarSnapshot:
        state = db.execute("SELECT * FROM closed_bar_state WHERE scope=?", (scope.key,)).fetchone()
        if state is None or state["watermark_ms"] is None or state["gap_from_ms"] is not None:
            _reject()
        row = db.execute("SELECT * FROM closed_bar_snapshots WHERE scope=? AND watermark_ms=?",
            (scope.key, state["watermark_ms"])).fetchone()
        if row is None:
            _reject()
        return ClosedBarSnapshotStore._snapshot(scope, row)

    @staticmethod
    def _fresh(value: WorkerStrategySignalDataV1, now: datetime) -> None:
        if not value.observed_at <= now < value.valid_until or now < value.bar_watermark:
            _reject("BUILTIN_STRATEGY_SIGNAL_EXPIRED")

    def require(self, handle: WorkerStrategySignalV1) -> WorkerStrategySignalDataV1:
        if self._busy is not None:
            self.retire()
            _reject()
        issued = self._current
        if type(handle) is not WorkerStrategySignalV1 or issued is None or issued.handle is not handle:
            _reject()
        token, epoch = object(), self._epoch
        self._busy = token
        try:
            self._guard()
            store = ClosedBarSnapshotStore(self._path)
            with self._book._operation_v2() as db, store._db() as bars:
                db.execute("BEGIN IMMEDIATE")
                bars.execute("BEGIN IMMEDIATE")
                revision, generation, policy_key, artifact, configuration = self._context(issued.value.scope, db)
                snapshot = self._snapshot(store, bars, ClosedBarScope(issued.value.scope.instrument.symbol, configuration.interval))
                if (revision != issued.value.policy_revision or generation != issued.value.publication_generation
                        or policy_key != issued.policy_key or artifact != issued.artifact
                        or _snapshot_key(snapshot) != issued.snapshot_key
                        or self._derive(snapshot.candles, configuration) != issued.value.direction):
                    _reject()
                self._guard()
                now = _utc(self._clock())
                self._closed_guard(token, epoch, issued.roots)
                self._fresh(issued.value, now)
                if self._current is not issued or _data_key(issued.value) != issued.value_key:
                    _reject()
            self._closed_guard(token, epoch, issued.roots)
            if self._current is not issued or _data_key(issued.value) != issued.value_key:
                _reject()
            return issued.value
        except BaseException:
            self.retire()
            raise
        finally:
            if self._busy is token:
                self._busy = None
