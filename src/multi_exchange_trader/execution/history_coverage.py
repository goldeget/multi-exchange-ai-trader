"""Immutable history query contracts; none of these types grants qualification."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from .history import ExecutionRecord, HistoryWindow, OrderHistoryRecord, TransactionRecord
from .protection import ProtectionScope

HistoryKind = Literal["EXECUTION", "TRANSACTION", "ORDER"]
HistoryRecord = ExecutionRecord | TransactionRecord | OrderHistoryRecord


@dataclass(frozen=True)
class QueryProvenance:
    observation_key: str
    snapshot_id: str
    symbol: str
    window: HistoryWindow
    observed_at: datetime
    server_observed_at: datetime
    transport_injected: bool


@dataclass(frozen=True)
class HistoryConflict:
    conflict_key: str
    kind: Literal["EXECUTION", "TRANSACTION", "ORDER", "SNAPSHOT"]
    record_id: str
    left_observation_key: str
    right_observation_key: str
    reason: str


@dataclass(frozen=True)
class IngestionReceipt:
    observation_key: str
    snapshot_id: str
    duplicate: bool
    new_variant_count: int
    new_conflict_count: int
    conflict_keys: tuple[str, ...]


@dataclass(frozen=True)
class StreamCoverage:
    kind: HistoryKind
    covered: tuple[HistoryWindow, ...]
    gaps: tuple[HistoryWindow, ...]
    last_query_end_ms: int | None
    contiguous_through_ms: int | None


@dataclass(frozen=True)
class HistoryCoverage:
    scope: ProtectionScope
    symbol: str
    window: HistoryWindow
    streams: tuple[StreamCoverage, ...]
    provenance: tuple[QueryProvenance, ...]
    conflicts: tuple[HistoryConflict, ...]
    query_complete: bool
    conflict_free: bool
    oldest_observed_at: datetime | None
    oldest_server_observed_at: datetime | None
    venue_event_complete: Literal[False] = field(default=False, init=False)
    flat_verified: Literal[False] = field(default=False, init=False)


@dataclass(frozen=True)
class ObservedVariant:
    kind: HistoryKind
    record_id: str
    variant_hash: str
    record: HistoryRecord
    observation_keys: tuple[str, ...]


@dataclass(frozen=True)
class HistoryWindowView:
    coverage: HistoryCoverage
    variants: tuple[ObservedVariant, ...]


def _stream_coverage(kind: HistoryKind, symbol: str, window: HistoryWindow,
                     provenance: tuple[QueryProvenance, ...]) -> StreamCoverage:
    intervals = sorted((max(window.start_ms, p.window.start_ms),
                        min(window.end_ms, p.window.end_ms))
                       for p in provenance
                       if (kind == "TRANSACTION" or p.symbol == symbol)
                       and p.window.start_ms <= window.end_ms
                       and p.window.end_ms >= window.start_ms)
    merged: list[HistoryWindow] = []
    for start, end in intervals:
        if merged and start <= merged[-1].end_ms + 1:
            merged[-1] = HistoryWindow(merged[-1].start_ms, max(end, merged[-1].end_ms))
        else:
            merged.append(HistoryWindow(start, end))
    gaps: list[HistoryWindow] = []
    cursor = window.start_ms
    for interval in merged:
        if cursor < interval.start_ms:
            gaps.append(HistoryWindow(cursor, interval.start_ms - 1))
        cursor = interval.end_ms + 1
    if cursor <= window.end_ms:
        gaps.append(HistoryWindow(cursor, window.end_ms))
    return StreamCoverage(kind, tuple(merged), tuple(gaps),
        merged[-1].end_ms if merged else None,
        merged[0].end_ms if merged and merged[0].start_ms == window.start_ms else None)


def compose_coverage(scope: ProtectionScope, symbol: str, window: HistoryWindow,
                     provenance: tuple[QueryProvenance, ...],
                     conflicts: tuple[HistoryConflict, ...]) -> HistoryCoverage:
    """Compose original successful observations without a clock or qualification."""
    provenance = tuple(sorted(provenance, key=lambda p: p.observation_key))
    conflicts = tuple(sorted(conflicts, key=lambda c: c.conflict_key))
    kinds: tuple[HistoryKind, ...] = ("EXECUTION", "ORDER", "TRANSACTION")
    streams = tuple(_stream_coverage(kind, symbol, window, provenance) for kind in kinds)
    return HistoryCoverage(scope, symbol, window, streams, provenance, conflicts,
        all(not stream.gaps for stream in streams), not conflicts,
        min((p.observed_at for p in provenance), default=None),
        min((p.server_observed_at for p in provenance), default=None))
