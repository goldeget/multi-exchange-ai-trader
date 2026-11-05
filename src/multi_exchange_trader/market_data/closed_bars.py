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
from .bybit_public_contracts import CANDLE_URL, INTERVALS
from .models import Candle
from .public_observations_v1 import (
    BybitPublicSourceRefV1,
    source_ref_from_payload_v1,
    source_ref_payload_v1,
)

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
    source: BybitPublicSourceRefV1 | None = None
    catalog_revision: str | None = None
    instrument_revision: str | None = None
    valid_until: datetime | None = None
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
        if (type(payload["v"]) is not int or payload["v"] not in (1, 2)
                or payload["scope"] != json.loads(scope.key)
                or payload["watermark_ms"] != row["watermark_ms"]):
            raise ClosedBarRejected("closed_bar_storage")
        candles = tuple(Candle(timestamp=_at(item["timestamp_ms"]), is_closed=True,
            **{key: item[key] for key in ("open", "high", "low", "close", "volume")}) for item in payload["bars"])
        cutoff = _utc(datetime.fromisoformat(str(row["cutoff"])))
        if _rows(scope, candles, cutoff) != payload["bars"]:
            raise ClosedBarRejected("closed_bar_storage")
        if not candles or payload["watermark_ms"] != _ms(candles[-1].timestamp) + INTERVAL_SECONDS[scope.interval] * 1000:
            raise ClosedBarRejected("closed_bar_storage")
        source: BybitPublicSourceRefV1 | None = None
        catalog_revision: str | None = None
        instrument_revision: str | None = None
        valid_until: datetime | None = None
        if payload["v"] == 2:
            expected = {"v", "scope", "watermark_ms", "bars", "source", "catalog_revision",
                "instrument_revision", "collected_at", "cutoff", "valid_until"}
            if set(payload) != expected:
                raise ClosedBarRejected("closed_bar_storage")
            try:
                source = source_ref_from_payload_v1(payload["source"])
                catalog_revision = str(payload["catalog_revision"])
                instrument_revision = str(payload["instrument_revision"])
                valid_until = _utc(datetime.fromisoformat(str(payload["valid_until"])))
                if (payload["collected_at"] != _utc(datetime.fromisoformat(str(row["collected_at"]))).isoformat()
                        or payload["cutoff"] != cutoff.isoformat()
                        or source.received_at != _utc(datetime.fromisoformat(str(row["collected_at"])))
                        or source.server_observed_at != cutoff
                        or valid_until <= source.received_at
                        or len(catalog_revision) != 64 or any(ch not in "0123456789abcdef" for ch in catalog_revision)
                        or len(instrument_revision) != 64 or any(ch not in "0123456789abcdef" for ch in instrument_revision)):
                    raise ClosedBarRejected("closed_bar_storage")
                query = tuple(sorted((("category", "linear"), ("interval", INTERVALS[scope.interval]),
                    ("limit", dict(source.query).get("limit", "")), ("symbol", scope.symbol))))
                if (source.endpoint != CANDLE_URL or source.query != query
                        or type(dict(source.query).get("limit")) is not str
                        or not dict(source.query)["limit"].isascii()
                        or not dict(source.query)["limit"].isdigit()
                        or not 1 <= int(dict(source.query)["limit"]) <= 500):
                    raise ClosedBarRejected("closed_bar_storage")
            except (KeyError, TypeError, ValueError):
                raise ClosedBarRejected("closed_bar_storage") from None
        return ClosedBarSnapshot(scope, str(row["digest"]), candles, _at(int(row["watermark_ms"])),
            _utc(datetime.fromisoformat(str(row["collected_at"]))), cutoff, False, source,
            catalog_revision, instrument_revision, valid_until)

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

    def latest_source_v1(self, scope: ClosedBarScope, *, catalog_revision: str,
                         now: datetime, max_bar_age: timedelta) -> ClosedBarSnapshot:
        """Require a durable source-bound snapshot to remain current after restart."""
        snapshot = self.latest(scope)
        now = _utc(now)
        if (snapshot.source is None or snapshot.valid_until is None
                or snapshot.catalog_revision != catalog_revision
                or type(max_bar_age) is not timedelta or not timedelta(0) < max_bar_age <= timedelta(days=7)):
            raise ClosedBarRejected("closed_bar_catalog_conflict")
        if now >= snapshot.valid_until or now < snapshot.watermark or now - snapshot.watermark > max_bar_age:
            raise ClosedBarRejected("closed_bar_stale")
        return snapshot

    def capture(self, scope: ClosedBarScope, values: Sequence[Candle], *, collected_at: datetime,
                cutoff: datetime, source: BybitPublicSourceRefV1 | None = None,
                catalog_revision: str | None = None, instrument_revision: str | None = None,
                valid_until: datetime | None = None) -> ClosedBarSnapshot:
        collected_at, cutoff = _utc(collected_at), _utc(cutoff)
        strict_source = source is not None
        if strict_source:
            if (type(source) is not BybitPublicSourceRefV1 or type(catalog_revision) is not str
                    or len(catalog_revision) != 64 or any(ch not in "0123456789abcdef" for ch in catalog_revision)
                    or type(instrument_revision) is not str or len(instrument_revision) != 64
                    or any(ch not in "0123456789abcdef" for ch in instrument_revision)
                    or type(valid_until) is not datetime or _utc(valid_until) <= collected_at
                    or source.endpoint != CANDLE_URL or source.received_at != collected_at
                    or source.server_observed_at != cutoff):
                raise ClosedBarRejected("closed_bar_source")
            query = dict(source.query)
            expected_query = tuple(sorted((("category", "linear"), ("interval", INTERVALS[scope.interval]),
                ("limit", query.get("limit", "")), ("symbol", scope.symbol))))
            limit = query.get("limit", "")
            if (source.query != expected_query or type(limit) is not str or not limit.isascii()
                    or not limit.isdigit() or not 1 <= int(limit) <= 500):
                raise ClosedBarRejected("closed_bar_source")
            extra = {
                "catalog_revision": catalog_revision,
                "instrument_revision": instrument_revision,
                "source": source_ref_payload_v1(source),
                "collected_at": collected_at.isoformat(),
                "cutoff": cutoff.isoformat(),
                "valid_until": _utc(valid_until).isoformat(),
            }
        else:
            if any(value is not None for value in (catalog_revision, instrument_revision, valid_until)) or collected_at != cutoff:
                raise ClosedBarRejected("closed_bar_clock")
            extra = {}
        rows = _rows(scope, values, cutoff)
        interval = INTERVAL_SECONDS[scope.interval] * 1000
        end = int(rows[-1]["timestamp_ms"]) + interval
        payload = _json({"v": 2 if strict_source else 1, "scope": json.loads(scope.key),
            "watermark_ms": end, "bars": rows, **extra})
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
                    if not strict_source and snapshot.source is not None:
                        raise ClosedBarRejected("closed_bar_source_required")
                    if strict_source and snapshot.catalog_revision is None:
                        old_rows = _rows(scope, snapshot.candles, snapshot.cutoff)
                        if old_rows != rows:
                            raise ClosedBarRejected("closed_bar_catalog_conflict")
                        db.execute("UPDATE closed_bar_snapshots SET body=?,digest=?,collected_at=?,cutoff=? "
                            "WHERE scope=? AND watermark_ms=?",
                            (payload, digest, collected_at.isoformat(), cutoff.isoformat(), scope.key, end))
                        migrated = db.execute("SELECT * FROM closed_bar_snapshots WHERE scope=? AND watermark_ms=?",
                            (scope.key, end)).fetchone()
                        assert migrated is not None
                        snapshot = replace(self._snapshot(scope, migrated), is_new_input=False)
                    elif strict_source and (snapshot.catalog_revision != catalog_revision
                            or snapshot.instrument_revision != instrument_revision):
                        raise ClosedBarRejected("closed_bar_catalog_conflict")
                    if old_snapshot["digest"] != digest or old_snapshot["body"] != payload:
                        window_collected = collected_at.isoformat() if strict_source else old_snapshot["collected_at"]
                        window_cutoff = cutoff.isoformat() if strict_source else old_snapshot["cutoff"]
                        # A different window is the same input; retain its first observation time.
                        window = db.execute("SELECT * FROM closed_bar_snapshot_windows WHERE scope=? AND watermark_ms=? AND digest=?",
                            (scope.key, end, digest)).fetchone()
                        if window is None:
                            db.execute("INSERT INTO closed_bar_snapshot_windows VALUES (?,?,?,?,?,?)",
                                (scope.key, end, payload, digest, window_collected, window_cutoff))
                            window = db.execute("SELECT * FROM closed_bar_snapshot_windows WHERE scope=? AND watermark_ms=? AND digest=?",
                                (scope.key, end, digest)).fetchone()
                        assert window is not None
                        if (window["body"] != payload or window["collected_at"] != window_collected
                                or window["cutoff"] != window_cutoff):
                            raise ClosedBarRejected("closed_bar_storage")
                        if strict_source:
                            # The immutable windows table retains every exact observation while
                            # the base row acts as the durable latest-source pointer after restart.
                            db.execute("UPDATE closed_bar_snapshots SET body=?,digest=?,collected_at=?,cutoff=? "
                                "WHERE scope=? AND watermark_ms=?",
                                (payload, digest, collected_at.isoformat(), cutoff.isoformat(), scope.key, end))
                            latest_row = db.execute("SELECT * FROM closed_bar_snapshots WHERE scope=? AND watermark_ms=?",
                                (scope.key, end)).fetchone()
                            assert latest_row is not None
                            snapshot = self._snapshot(scope, latest_row)
                        else:
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
