"""Normalized, scoped runtime projections; command truth stays in CommandJournal.

Every row and event has its own bounded payload, so a growing account never needs
one aggregate 64 KiB JSON projection. Opening the store has no recovery effects.
Only the externally fenced account worker may revoke reconciliation on restart;
this store does not establish the lease or qualify venue evidence itself.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Literal, Self, cast

from .journal import ExecutionScope, JournalConflict, RevisionConflict, _canonical, _identifier

STATE_SCHEMA = {
    "runtime_orders": "account environment order_id revision payload reconciliation_required",
    "runtime_positions": "account environment position_id revision payload reconciliation_required",
    "runtime_inbox": "account environment event_id event_payload position_id revision",
}
_SCHEMA = """
CREATE TABLE runtime_orders (
 account TEXT NOT NULL, environment TEXT NOT NULL, order_id TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision > 0), payload TEXT NOT NULL,
 reconciliation_required INTEGER NOT NULL CHECK(reconciliation_required IN (0,1)),
 PRIMARY KEY(account,environment,order_id));
CREATE TABLE runtime_positions (
 account TEXT NOT NULL, environment TEXT NOT NULL, position_id TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision > 0), payload TEXT NOT NULL,
 reconciliation_required INTEGER NOT NULL CHECK(reconciliation_required IN (0,1)),
 PRIMARY KEY(account,environment,position_id));
CREATE TABLE runtime_inbox (
 account TEXT NOT NULL, environment TEXT NOT NULL, event_id TEXT NOT NULL,
 event_payload TEXT NOT NULL, position_id TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision > 0),
 PRIMARY KEY(account,environment,event_id),
 FOREIGN KEY(account,environment,position_id)
 REFERENCES runtime_positions(account,environment,position_id));
PRAGMA user_version=1;
"""


@dataclass(frozen=True)
class PositionRecord:
    position_id: str
    revision: int
    payload_json: str
    reconciliation_required: bool


@dataclass(frozen=True)
class OrderRecord:
    order_id: str
    revision: int
    payload_json: str
    reconciliation_required: bool


@dataclass(frozen=True)
class InboxRecord:
    event_id: str
    position_id: str
    revision: int
    payload_json: str


def _payload(raw: str) -> None:
    if type(raw) is not str or len(raw.encode("utf-8")) > 65536:
        raise ValueError("invalid bounded runtime payload")
    if _canonical(json.loads(raw)) != raw:
        raise ValueError("invalid canonical runtime payload")


class RuntimeStateStore:
    def __init__(self, path: Path, scope: ExecutionScope) -> None:
        if type(scope) is not ExecutionScope:
            raise TypeError("explicit execution scope required")
        self.scope = scope
        self._scope = (scope.account_id, scope.environment)
        self._db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute("PRAGMA trusted_schema=OFF")
            self._db.execute("PRAGMA foreign_keys=ON")
            if self._db.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                raise ValueError("runtime state requires durable file-backed WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            # Serialize first-open without repairing partial or unsupported restores.
            with self._transaction():
                tables = self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                if not tables.fetchall() and self._db.execute("PRAGMA user_version").fetchone()[0] == 0:
                    for statement in _SCHEMA.split(";"):
                        if statement.strip():
                            self._db.execute(statement)
                self._validate()
        except BaseException:
            self._db.close()
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._db.execute("COMMIT")
        except BaseException:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise

    def _validate(self) -> None:
        if self._db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("unsupported runtime state version")
        objects = self._db.execute(
            "SELECT name,type FROM sqlite_master WHERE type IN ('table','trigger','view')"
        ).fetchall()
        if {(row[0], row[1]) for row in objects} != {(table, "table") for table in STATE_SCHEMA}:
            raise ValueError("unsupported runtime state schema")
        for table, columns in STATE_SCHEMA.items():
            actual = [row[1] for row in self._db.execute(f'PRAGMA table_xinfo("{table}")')]
            if actual != columns.split():
                raise ValueError("unsupported runtime state columns")
            identity = {"runtime_orders": "order_id", "runtime_positions": "position_id",
                        "runtime_inbox": "event_id"}[table]
            required = {"account", "environment", identity}
            # Accept equivalent full UNIQUE indexes as well as PRIMARY KEY DDL.
            # Partial/expression/NOCASE indexes cannot establish scoped identity.
            keys = []
            for index in self._db.execute(f'PRAGMA index_list("{table}")'):
                if index[2] and not index[4]:
                    parts = self._db.execute(
                        'SELECT name,coll FROM pragma_index_xinfo(?) WHERE "key"=1',
                        (index[1],),
                    ).fetchall()
                    if (len(parts) == len(required)
                            and all(part[1].upper() == "BINARY" for part in parts)):
                        keys.append({part[0] for part in parts})
            if required not in keys:
                raise ValueError("unsupported runtime state identity key")
            references = [tuple(row) for row in self._db.execute(f'PRAGMA foreign_key_list("{table}")')]
            expected = ({("runtime_positions", column, column, "NO ACTION", "NO ACTION", "NONE")
                         for column in ("account", "environment", "position_id")}
                        if table == "runtime_inbox" else set())
            if (len(references) != len(expected) or {row[2:] for row in references} != expected
                    or expected and len({row[0] for row in references}) != 1):
                raise ValueError("unsupported runtime state foreign key constraint")
        if [tuple(row) for row in self._db.execute("PRAGMA integrity_check")] != [("ok",)]:
            raise ValueError("runtime state integrity check failed")
        if self._db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("runtime state references are incomplete")
        for table in STATE_SCHEMA:
            for row in self._db.execute(f'SELECT * FROM "{table}"'):
                ExecutionScope(row["account"], row["environment"])
                _identifier(row["position_id"] if table != "runtime_orders" else row["order_id"])
                if type(row["revision"]) is not int or row["revision"] <= 0:
                    raise ValueError("invalid runtime state revision")
                if table == "runtime_inbox":
                    _identifier(row["event_id"])
                    _payload(row["event_payload"])
                    current = self._db.execute(
                        "SELECT revision FROM runtime_positions WHERE account=? AND environment=? "
                        "AND position_id=?", (row["account"], row["environment"], row["position_id"]),
                    ).fetchone()
                    if current is None or row["revision"] > current[0]:
                        raise ValueError("runtime event revision is inconsistent")
                else:
                    self._validate_record(row)

    @staticmethod
    def _validate_record(row: sqlite3.Row) -> None:
        _payload(row["payload"])
        if type(row["revision"]) is not int or row["revision"] <= 0:
            raise ValueError("invalid runtime state revision")
        if type(row["reconciliation_required"]) is not int or row["reconciliation_required"] not in (0, 1):
            raise ValueError("invalid runtime reconciliation flag")

    def _get(self, kind: Literal["position", "order"], identity: str) -> sqlite3.Row | None:
        _identifier(identity)
        row = self._db.execute(
            f"SELECT * FROM runtime_{kind}s WHERE account=? AND environment=? AND {kind}_id=?",
            (*self._scope, identity),
        ).fetchone()
        if row is not None:
            self._validate_record(row)
        return cast(sqlite3.Row | None, row)

    def get_position(self, position_id: str) -> PositionRecord | None:
        row = self._get("position", position_id)
        return None if row is None else PositionRecord(position_id, row["revision"], row["payload"],
                                                      bool(row["reconciliation_required"]))

    def get_order(self, order_id: str) -> OrderRecord | None:
        row = self._get("order", order_id)
        return None if row is None else OrderRecord(order_id, row["revision"], row["payload"],
                                                   bool(row["reconciliation_required"]))

    def list_positions(self) -> tuple[PositionRecord, ...]:
        rows = self._db.execute("SELECT position_id FROM runtime_positions WHERE account=? "
                                "AND environment=? ORDER BY position_id", self._scope).fetchall()
        result = tuple(self.get_position(row[0]) for row in rows)
        return tuple(record for record in result if record is not None)

    def list_orders(self) -> tuple[OrderRecord, ...]:
        rows = self._db.execute("SELECT order_id FROM runtime_orders WHERE account=? "
                                "AND environment=? ORDER BY order_id", self._scope).fetchall()
        result = tuple(self.get_order(row[0]) for row in rows)
        return tuple(record for record in result if record is not None)

    def list_position_events(self, position_id: str) -> tuple[InboxRecord, ...]:
        """Immutable evidence in reducer order, scoped to this account/environment."""
        _identifier(position_id)
        rows = self._db.execute(
            "SELECT event_id,position_id,revision,event_payload FROM runtime_inbox "
            "WHERE account=? AND environment=? AND position_id=? ORDER BY revision,event_id",
            (*self._scope, position_id),
        ).fetchall()
        return tuple(self._inbox_record(row) for row in rows)

    @staticmethod
    def _inbox_record(row: sqlite3.Row) -> InboxRecord:
        _identifier(row["event_id"])
        _identifier(row["position_id"])
        _payload(row["event_payload"])
        if type(row["revision"]) is not int or row["revision"] < 1:
            raise ValueError("invalid runtime event revision")
        return InboxRecord(row["event_id"], row["position_id"], row["revision"], row["event_payload"])

    def get_inbox_event(self, event_id: str) -> InboxRecord | None:
        """Read a scoped collision guard across positions without any state transition."""
        _identifier(event_id)
        row = self._db.execute(
            "SELECT event_id,position_id,revision,event_payload FROM runtime_inbox "
            "WHERE account=? AND environment=? AND event_id=?", (*self._scope, event_id),
        ).fetchone()
        return None if row is None else self._inbox_record(row)

    def _save(self, kind: Literal["position", "order"], identity: str, payload: str,
              expected_revision: int) -> None:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("nonnegative expected revision required")
        current = self._get(kind, identity)
        if (0 if current is None else current["revision"]) != expected_revision:
            raise RevisionConflict("runtime projection revision differs from expected")
        self._db.execute(
            f"INSERT INTO runtime_{kind}s VALUES(?,?,?,?,?,1) ON CONFLICT(account,environment,"
            f"{kind}_id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,"
            "reconciliation_required=1",
            (*self._scope, identity, expected_revision + 1, payload),
        )

    def save_position(self, position_id: str, payload: object, expected_revision: int) -> PositionRecord:
        canonical = _canonical(payload)
        with self._transaction():
            self._save("position", position_id, canonical, expected_revision)
            result = self.get_position(position_id)
            assert result is not None
            return result

    def save_order(self, order_id: str, payload: object, expected_revision: int) -> OrderRecord:
        canonical = _canonical(payload)
        with self._transaction():
            self._save("order", order_id, canonical, expected_revision)
            result = self.get_order(order_id)
            assert result is not None
            return result

    def apply_position_event(self, event_id: str, event_payload: object, position_id: str,
                             expected_revision: int, projection_payload: object) -> PositionRecord:
        _identifier(event_id)
        _identifier(position_id)
        event_json, projection_json = _canonical(event_payload), _canonical(projection_payload)
        with self._transaction():
            previous = self._db.execute(
                "SELECT * FROM runtime_inbox WHERE account=? AND environment=? AND event_id=?",
                (*self._scope, event_id),
            ).fetchone()
            if previous is not None:
                if (previous["event_payload"], previous["position_id"]) != (event_json, position_id):
                    raise JournalConflict("runtime event identity has incompatible immutable data")
            else:
                self._save("position", position_id, projection_json, expected_revision)
                self._db.execute("INSERT INTO runtime_inbox VALUES(?,?,?,?,?,?)",
                                 (*self._scope, event_id, event_json, position_id, expected_revision + 1))
            current = self.get_position(position_id)
            if current is None:
                raise JournalConflict("runtime event has no corresponding projection")
            return current

    def recover(self, *, exclusive_owner: bool) -> None:
        """Revoke stale reconciliation only after external account-worker fencing."""
        if exclusive_owner is not True:
            raise ValueError("exclusive account worker ownership required")
        with self._transaction():
            for table in ("runtime_positions", "runtime_orders"):
                self._db.execute(f"UPDATE {table} SET reconciliation_required=1,revision=revision+1 "
                                 "WHERE account=? AND environment=?", self._scope)

    def mark_reconciled(self, kind: Literal["position", "order"], identity: str,
                        expected_revision: int, *, evidence: str) -> None:
        """Caller must independently verify fresh scoped venue readback under its lease."""
        if kind not in ("position", "order") or not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("explicit kind and reconciliation evidence required")
        if len(evidence) > 2048 or type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("bounded evidence and positive revision required")
        with self._transaction():
            current = self._get(kind, identity)
            if current is None or current["revision"] != expected_revision:
                raise RevisionConflict("runtime reconciliation revision differs from expected")
            self._db.execute(f"UPDATE runtime_{kind}s SET reconciliation_required=0,revision=revision+1 "
                             f"WHERE account=? AND environment=? AND {kind}_id=?",
                             (*self._scope, identity))
