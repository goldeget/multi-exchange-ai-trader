"""Persistent public closed bars; observations grant no execution or financial authority."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

from .adapters.base import INTERVAL_SECONDS, SYMBOLS
from .adapters.bybit import CANDLE_URL
from .models import Candle

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class ClosedBarRejected(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ClosedBarRejected("closed_bar_clock")
    return value.astimezone(UTC)


def _ms(value: datetime) -> int:
    delta = _utc(value) - EPOCH
    return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000


def _at(value: int) -> datetime:
    return EPOCH + timedelta(milliseconds=value)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _number(value: Decimal) -> str:
    if value == 0:
        return "0"
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


@dataclass(frozen=True)
class ClosedBarScope:
    symbol: str
    interval: str

    @property
    def key(self) -> str:
        if self.symbol not in SYMBOLS or self.interval not in INTERVAL_SECONDS:
            raise ClosedBarRejected("closed_bar_scope")
        return _json({"exchange": "bybit", "product": "USDT_LINEAR_PERPETUAL",
            "symbol": self.symbol, "interval": self.interval, "source": CANDLE_URL})


@dataclass(frozen=True)
class ClosedBarSnapshot:
    scope: ClosedBarScope
    snapshot_id: str
    candles: tuple[Candle, ...]
    watermark: datetime
    collected_at: datetime
    cutoff: datetime
    is_new_input: bool
    financial_authority: Literal[False] = field(default=False, init=False)
    trading_enabled: Literal[False] = field(default=False, init=False)


def _rows(scope: ClosedBarScope, values: Sequence[Candle], cutoff: datetime) -> list[dict[str, Any]]:
    _ = scope.key
    interval = INTERVAL_SECONDS[scope.interval] * 1000
    if not values or len(values) > 500:
        raise ClosedBarRejected("closed_bar_empty")
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    active = 0
    for value in values:
        if type(value) is not Candle:
            raise ClosedBarRejected("closed_bar_shape")
        candle = Candle.model_validate(value.model_dump())
        stamp = _ms(candle.timestamp)
        if candle.timestamp.microsecond or stamp < 0 or stamp % interval:
            raise ClosedBarRejected("closed_bar_alignment")
        if stamp in seen:
            raise ClosedBarRejected("closed_bar_duplicate")
        seen.add(stamp)
        if candle.timestamp > cutoff or candle.is_closed and _at(stamp + interval) > cutoff:
            raise ClosedBarRejected("closed_bar_future")
        if not candle.is_closed:
            if _at(stamp + interval) <= cutoff:
                raise ClosedBarRejected("closed_bar_unclosed")
            active += 1
            continue
        result.append({"timestamp_ms": stamp, **{key: _number(getattr(candle, key))
            for key in ("open", "high", "low", "close", "volume")}})
    if active > 1 or not result:
        raise ClosedBarRejected("closed_bar_unclosed")
    return sorted(result, key=lambda row: int(row["timestamp_ms"]))


class ClosedBarSnapshotStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        if self.path.name == ":memory:" or self.path.is_symlink() or self.path.is_junction():
            raise ClosedBarRejected("closed_bar_storage")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS closed_bar_state (
                    scope TEXT PRIMARY KEY, watermark_ms INTEGER, gap_from_ms INTEGER, gap_to_ms INTEGER);
                CREATE TABLE IF NOT EXISTS closed_bars (
                    scope TEXT NOT NULL, timestamp_ms INTEGER NOT NULL, body TEXT NOT NULL,
                    digest TEXT NOT NULL, PRIMARY KEY(scope,timestamp_ms));
                CREATE TABLE IF NOT EXISTS closed_bar_snapshots (
                    scope TEXT NOT NULL, watermark_ms INTEGER NOT NULL, body TEXT NOT NULL,
                    digest TEXT NOT NULL, collected_at TEXT NOT NULL, cutoff TEXT NOT NULL,
                    PRIMARY KEY(scope,watermark_ms));
                CREATE TABLE IF NOT EXISTS closed_bar_snapshot_windows (
                    scope TEXT NOT NULL, watermark_ms INTEGER NOT NULL, body TEXT NOT NULL,
                    digest TEXT NOT NULL, collected_at TEXT NOT NULL, cutoff TEXT NOT NULL,
                    PRIMARY KEY(scope,watermark_ms,digest));
            """)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA trusted_schema=OFF")
        try:
            yield db
        finally:
            db.close()

    def watermark(self, scope: ClosedBarScope) -> datetime | None:
        with self._db() as db:
            row = db.execute("SELECT watermark_ms FROM closed_bar_state WHERE scope=?", (scope.key,)).fetchone()
        return None if row is None or row[0] is None else _at(int(row[0]))

    @staticmethod
    def _snapshot(scope: ClosedBarScope, row: sqlite3.Row) -> ClosedBarSnapshot:
        body = str(row["body"])
        if _hash(body) != row["digest"]:
            raise ClosedBarRejected("closed_bar_storage")
        payload = json.loads(body)
        if payload["v"] != 1 or payload["scope"] != json.loads(scope.key) or payload["watermark_ms"] != row["watermark_ms"]:
            raise ClosedBarRejected("closed_bar_storage")
        candles = tuple(Candle(timestamp=_at(item["timestamp_ms"]), is_closed=True,
            **{key: item[key] for key in ("open", "high", "low", "close", "volume")}) for item in payload["bars"])
        cutoff = _utc(datetime.fromisoformat(str(row["cutoff"])))
        if _rows(scope, candles, cutoff) != payload["bars"]:
            raise ClosedBarRejected("closed_bar_storage")
        return ClosedBarSnapshot(scope, str(row["digest"]), candles, _at(int(row["watermark_ms"])),
            _utc(datetime.fromisoformat(str(row["collected_at"]))), cutoff, False)

    def latest(self, scope: ClosedBarScope) -> ClosedBarSnapshot:
        with self._db() as db:
            state = db.execute("SELECT * FROM closed_bar_state WHERE scope=?", (scope.key,)).fetchone()
            if state is None or state["watermark_ms"] is None or state["gap_from_ms"] is not None:
                raise ClosedBarRejected("closed_bar_gap")
            row = db.execute("SELECT * FROM closed_bar_snapshots WHERE scope=? AND watermark_ms=?",
                (scope.key, state["watermark_ms"])).fetchone()
            if row is None:
                raise ClosedBarRejected("closed_bar_storage")
            return self._snapshot(scope, row)

    def capture(self, scope: ClosedBarScope, values: Sequence[Candle], *, collected_at: datetime,
                cutoff: datetime) -> ClosedBarSnapshot:
        collected_at, cutoff = _utc(collected_at), _utc(cutoff)
        if collected_at != cutoff:
            raise ClosedBarRejected("closed_bar_clock")
        rows = _rows(scope, values, cutoff)
        interval = INTERVAL_SECONDS[scope.interval] * 1000
        end = int(rows[-1]["timestamp_ms"]) + interval
        payload = _json({"v": 1, "scope": json.loads(scope.key), "watermark_ms": end, "bars": rows})
        digest = _hash(payload)
        gap: tuple[int, int] | None = None
        snapshot = None
        with self._db() as db, db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT * FROM closed_bar_state WHERE scope=?", (scope.key,)).fetchone()
            previous = None if state is None else state["watermark_ms"]
            if previous is not None and end < previous:
                raise ClosedBarRejected("closed_bar_regression")
            if state is not None and state["gap_from_ms"] is not None and (
                    int(rows[0]["timestamp_ms"]) > state["gap_from_ms"]
                    or int(rows[-1]["timestamp_ms"]) < state["gap_to_ms"]):
                raise ClosedBarRejected("closed_bar_gap")
            for item in rows:
                old = db.execute("SELECT body,digest FROM closed_bars WHERE scope=? AND timestamp_ms=?",
                    (scope.key, item["timestamp_ms"])).fetchone()
                if old is not None and (old["body"] != _json(item) or old["digest"] != _hash(str(old["body"]))):
                    raise ClosedBarRejected("closed_bar_conflict")
            for left, right in pairwise(rows):
                expected = int(left["timestamp_ms"]) + interval
                if int(right["timestamp_ms"]) != expected:
                    gap = expected, int(right["timestamp_ms"])
                    break
            if gap is None and previous is not None and end > previous:
                new = [item for item in rows if int(item["timestamp_ms"]) >= previous]
                if not new or int(new[0]["timestamp_ms"]) != previous:
                    gap = previous, int(new[0]["timestamp_ms"]) if new else end
            if gap is not None:
                db.execute("INSERT INTO closed_bar_state VALUES (?,?,?,?) ON CONFLICT(scope) DO UPDATE SET gap_from_ms=excluded.gap_from_ms,gap_to_ms=excluded.gap_to_ms",
                    (scope.key, previous, *gap))
            else:
                for item in rows:
                    body = _json(item)
                    db.execute("INSERT OR IGNORE INTO closed_bars VALUES (?,?,?,?)",
                        (scope.key, item["timestamp_ms"], body, _hash(body)))
                old_snapshot = db.execute("SELECT * FROM closed_bar_snapshots WHERE scope=? AND watermark_ms=?",
                    (scope.key, end)).fetchone()
                if old_snapshot is not None:
                    snapshot = self._snapshot(scope, old_snapshot)
                    if old_snapshot["digest"] != digest or old_snapshot["body"] != payload:
                        # A different window is the same input; retain its first observation time.
                        window = db.execute("SELECT * FROM closed_bar_snapshot_windows WHERE scope=? AND watermark_ms=? AND digest=?",
                            (scope.key, end, digest)).fetchone()
                        if window is None:
                            db.execute("INSERT INTO closed_bar_snapshot_windows VALUES (?,?,?,?,?,?)",
                                (scope.key, end, payload, digest, old_snapshot["collected_at"], old_snapshot["cutoff"]))
                            window = db.execute("SELECT * FROM closed_bar_snapshot_windows WHERE scope=? AND watermark_ms=? AND digest=?",
                                (scope.key, end, digest)).fetchone()
                        assert window is not None
                        if (window["body"] != payload or window["collected_at"] != old_snapshot["collected_at"]
                                or window["cutoff"] != old_snapshot["cutoff"]):
                            raise ClosedBarRejected("closed_bar_storage")
                        snapshot = self._snapshot(scope, window)
                else:
                    db.execute("INSERT INTO closed_bar_snapshots VALUES (?,?,?,?,?,?)",
                        (scope.key, end, payload, digest, collected_at.isoformat(), cutoff.isoformat()))
                    row = db.execute("SELECT * FROM closed_bar_snapshots WHERE scope=? AND watermark_ms=?",
                        (scope.key, end)).fetchone()
                    assert row is not None
                    snapshot = replace(self._snapshot(scope, row), is_new_input=True)
                db.execute("INSERT INTO closed_bar_state VALUES (?,?,NULL,NULL) ON CONFLICT(scope) DO UPDATE SET watermark_ms=excluded.watermark_ms,gap_from_ms=NULL,gap_to_ms=NULL",
                    (scope.key, end))
        if gap is not None:
            raise ClosedBarRejected("closed_bar_gap")
        assert snapshot is not None
        return snapshot
