"""Scoped, immutable SQLite observations. Query completion never grants authority."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from itertools import combinations
from pathlib import Path
from threading import get_ident
from types import TracebackType
from typing import Any, Literal, Self, cast

from .history import (
    ExecutionRecord,
    HistoryError,
    HistorySnapshot,
    HistoryWindow,
    OrderHistoryRecord,
    TransactionRecord,
    _symbol,
    canonical_history_decimal,
    validate_history_record,
    validate_history_scope,
    validate_history_snapshot,
)
from .history_coverage import (
    HistoryConflict,
    HistoryCoverage,
    HistoryKind,
    HistoryRecord,
    HistoryWindowView,
    IngestionReceipt,
    ObservedVariant,
    QueryProvenance,
    compose_coverage,
)
from .protection import ProtectionScope

SCHEMA_VERSION = 1
_DEFINITIONS = {
    "history_scope": "id INTEGER NOT NULL PRIMARY KEY CHECK(id=1), "
        "account TEXT NOT NULL, environment TEXT NOT NULL",
    "history_snapshots": "observation_key TEXT NOT NULL PRIMARY KEY, snapshot_id TEXT NOT NULL, "
        "symbol TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, "
        "observed_at TEXT NOT NULL, server_observed_at TEXT NOT NULL, "
        "transport_injected INTEGER NOT NULL",
    "history_variants": "kind TEXT NOT NULL, record_id TEXT NOT NULL, variant_hash TEXT NOT NULL, "
        "symbol TEXT NOT NULL, event_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL, "
        "payload TEXT NOT NULL, PRIMARY KEY(kind,record_id,variant_hash)",
    "history_members": "observation_key TEXT NOT NULL, kind TEXT NOT NULL, record_id TEXT NOT NULL, "
        "variant_hash TEXT NOT NULL, PRIMARY KEY(observation_key,kind,record_id), "
        "FOREIGN KEY(observation_key) REFERENCES history_snapshots(observation_key), "
        "FOREIGN KEY(kind,record_id,variant_hash) "
        "REFERENCES history_variants(kind,record_id,variant_hash)",
    "history_conflicts": "conflict_key TEXT NOT NULL PRIMARY KEY, kind TEXT NOT NULL, "
        "record_id TEXT NOT NULL, left_observation_key TEXT NOT NULL, "
        "right_observation_key TEXT NOT NULL, reason TEXT NOT NULL, "
        "FOREIGN KEY(left_observation_key) REFERENCES history_snapshots(observation_key), "
        "FOREIGN KEY(right_observation_key) REFERENCES history_snapshots(observation_key)",
}
_KEYS = {
    "history_scope": ("id",), "history_snapshots": ("observation_key",),
    "history_variants": ("kind", "record_id", "variant_hash"),
    "history_members": ("observation_key", "kind", "record_id"),
    "history_conflicts": ("conflict_key",),
}
_RECORD_TYPES: dict[str, type[ExecutionRecord | OrderHistoryRecord | TransactionRecord]] = {
    "EXECUTION": ExecutionRecord, "ORDER": OrderHistoryRecord, "TRANSACTION": TransactionRecord,
}
VariantKey = tuple[str, str, str]
VariantRow = tuple[str, str, str, str, int, int, str]


def _fail() -> None:
    raise HistoryError("HISTORY_DATABASE_INVALID")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False)


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _trigger(table: str, operation: str) -> str:
    return (f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'history is append-only'); END")


def _create_schema(db: sqlite3.Connection) -> None:
    for table, definition in _DEFINITIONS.items():
        db.execute(f"CREATE TABLE {table} ({definition})")
        for operation in ("UPDATE", "DELETE"):
            db.execute(_trigger(table, operation))
    # Lookup indexes have no identity role; equivalent existing schemas may omit them.
    for name, table, columns in (
        ("history_members_variant", "history_members", "kind,record_id,variant_hash,observation_key"),
        ("history_snapshots_id", "history_snapshots", "snapshot_id,observation_key"),
        ("history_conflicts_left", "history_conflicts", "left_observation_key"),
        ("history_conflicts_right", "history_conflicts", "right_observation_key"),
    ):
        db.execute(f"CREATE INDEX {name} ON {table} ({columns})")
    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def _references(db: sqlite3.Connection, table: str) -> set[tuple[tuple[Any, ...], ...]]:
    groups: dict[int, list[tuple[Any, ...]]] = {}
    for row in db.execute(f'PRAGMA foreign_key_list("{table}")'):
        groups.setdefault(row[0], []).append(tuple(row[1:]))
    return {tuple(sorted(rows)) for rows in groups.values()}


def _sql_tokens(sql: str) -> tuple[tuple[str, str], ...]:
    """Lex original SQLite DDL once; quoted content never becomes comment or syntax."""
    result: list[tuple[str, str]] = []
    cursor = 0
    while cursor < len(sql):
        start = cursor
        char = sql[cursor]
        if char.isspace():
            cursor += 1
        elif sql.startswith("--", cursor):
            end = sql.find("\n", cursor + 2)
            cursor = len(sql) if end == -1 else end + 1
        elif sql.startswith("/*", cursor):
            end = sql.find("*/", cursor + 2)
            if end == -1:
                _fail()
            cursor = end + 2
        elif char in "'\"`[":
            closing = "]" if char == "[" else char
            cursor += 1
            while cursor < len(sql):
                if sql[cursor] == closing:
                    cursor += 1
                    if closing != "]" and cursor < len(sql) and sql[cursor] == closing:
                        cursor += 1  # Doubled delimiters escape quotes, not comments.
                        continue
                    break
                cursor += 1
            else:
                _fail()
            result.append(("quoted", sql[start:cursor]))
        elif char.isalpha() or char == "_":
            cursor += 1
            while cursor < len(sql) and (sql[cursor].isalnum() or sql[cursor] in "_$"):
                cursor += 1
            result.append(("word", sql[start:cursor]))
        elif char.isdigit():
            cursor += 1
            while cursor < len(sql) and sql[cursor].isdigit():
                cursor += 1
            result.append(("number", sql[start:cursor]))
        else:
            cursor += 1
            result.append(("symbol", char))
    return tuple(result)


def _validate_binary_collations(sql: str) -> None:
    """Recognize actual COLLATE clauses, including comments and quoted names."""
    tokens = _sql_tokens(sql)
    for index, (kind, value) in enumerate(tokens):
        if kind != "word" or value.upper() != "COLLATE":
            continue
        if index + 1 == len(tokens):
            _fail()
        name_kind, name = tokens[index + 1]
        if name_kind == "quoted":
            delimiter = name[0]
            name = name[1:-1]
            if delimiter != "[":
                name = name.replace(delimiter * 2, delimiter)
        elif name_kind != "word":
            _fail()
        if name.lower() != "binary":
            _fail()


def _validate_schema(db: sqlite3.Connection) -> None:
    if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        _fail()
    objects = list(db.execute("SELECT name,type,sql FROM sqlite_master "
                              "WHERE substr(lower(name),1,7) != 'sqlite_'"))
    if {r[0] for r in objects if r[1] == "table"} != set(_DEFINITIONS):
        _fail()
    expected_triggers = {f"{table}_no_{op.lower()}": _trigger(table, op)
                         for table in _DEFINITIONS for op in ("UPDATE", "DELETE")}
    if {r[0] for r in objects if r[1] in {"trigger", "view"}} != set(expected_triggers):
        _fail()
    for name, kind, sql in objects:
        if kind == "trigger" and _sql_tokens(sql) != _sql_tokens(expected_triggers[name]):
            _fail()
        if kind == "view":
            _fail()
        if kind == "table":
            _validate_binary_collations(sql)
    scope_tokens = tuple((kind, value.upper() if kind == "word" else value)
        for kind, value in _sql_tokens(next(r[2] for r in objects if r[0] == "history_scope")))
    required_check = (("word", "CHECK"), ("symbol", "("), ("word", "ID"),
                      ("symbol", "="), ("number", "1"), ("symbol", ")"))
    if not any(scope_tokens[index:index + len(required_check)] == required_check
               for index in range(len(scope_tokens))):
        _fail()
    with closing(sqlite3.connect(":memory:")) as reference:
        _create_schema(reference)
        for table, required_key in _KEYS.items():
            columns = list(db.execute(f'PRAGMA table_xinfo("{table}")'))
            expected = list(reference.execute(f'PRAGMA table_xinfo("{table}")'))
            # Identity may be an equivalent full UNIQUE BINARY key, not necessarily PK.
            if [(r[1], r[2].upper(), r[3], r[4], r[6]) for r in columns] != [
                    (r[1], r[2].upper(), r[3], r[4], r[6]) for r in expected]:
                _fail()
            indexes = list(db.execute(f'PRAGMA index_list("{table}")'))
            keys: list[set[str]] = []
            for index in indexes:
                if index[2] and not index[4]:
                    parts = list(db.execute('SELECT name,coll FROM pragma_index_xinfo(?) '
                                            'WHERE "key"=1', (index[1],)))
                    if (len(parts) == len({p[0] for p in parts})
                            and all(p[0] is not None and p[1] == "BINARY" for p in parts)):
                        keys.append({p[0] for p in parts})
            primary = [r for r in columns if r[5]]
            if (len(primary) == 1 and primary[0][2].upper() == "INTEGER"
                    and not any(index[3] == "pk" for index in indexes)):
                keys.append({primary[0][1]})
            if set(required_key) not in keys or _references(db, table) != _references(reference, table):
                _fail()
            if len(list(db.execute(f'PRAGMA foreign_key_list("{table}")'))) != len(
                    list(reference.execute(f'PRAGMA foreign_key_list("{table}")'))):
                _fail()
    if ([tuple(row) for row in db.execute("PRAGMA quick_check")] != [("ok",)]
            or db.execute("PRAGMA foreign_key_check").fetchone() is not None):
        _fail()


def _payload(record: HistoryRecord) -> str:
    data = {item.name: (canonical_history_decimal(getattr(record, item.name))
                       if item.type == "Decimal" else getattr(record, item.name))
            for item in fields(record)}
    result = _json(data)
    if len(result.encode("utf-8")) > 65536:
        _fail()
    return result


def _variant(record: HistoryRecord) -> VariantRow:
    if type(record) is ExecutionRecord:
        kind, identity, event, updated = ("EXECUTION", record.execution_id,
                                          record.time_ms, record.time_ms)
    elif type(record) is TransactionRecord:
        kind, identity, event, updated = ("TRANSACTION", record.transaction_id,
                                          record.time_ms, record.time_ms)
    else:
        order = cast(OrderHistoryRecord, record)
        kind, identity, event, updated = ("ORDER", order.venue_order_id,
                                          order.created_ms, order.updated_ms)
    payload = _payload(record)
    return (kind, identity, hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            record.symbol, event, updated, payload)


def _decode_record(kind: str, payload: str) -> HistoryRecord:
    if kind not in _RECORD_TYPES or type(payload) is not str or len(payload.encode("utf-8")) > 65536:
        raise HistoryError("HISTORY_DATABASE_INVALID")
    # Canonical equality below also rejects duplicate keys and noncanonical decimal spellings.
    data = json.loads(payload)
    cls = _RECORD_TYPES[kind]
    if type(data) is not dict or set(data) != {item.name for item in fields(cls)}:
        raise HistoryError("HISTORY_DATABASE_INVALID")
    for item in fields(cls):
        if item.type == "Decimal":
            value = data[item.name]
            if type(value) is not str or len(value) > 64:
                _fail()
            try:
                data[item.name] = Decimal(value)
            except InvalidOperation:
                raise HistoryError("HISTORY_DATABASE_INVALID") from None
            if not data[item.name].is_finite():
                _fail()  # InvalidOperation may be untrapped in the caller's Decimal context.
    record = cls(**data)
    if _payload(record) != payload:
        _fail()
    event = record.created_ms if isinstance(record, OrderHistoryRecord) else record.time_ms
    validate_history_record(record, symbol=record.symbol, window=HistoryWindow(event, event))
    return record


def _metadata(snapshot: HistorySnapshot) -> tuple[str, str, int, int, str, str, int]:
    return (snapshot.snapshot_id, snapshot.symbol, snapshot.window.start_ms, snapshot.window.end_ms,
            snapshot.observed_at.astimezone(UTC).isoformat(),
            snapshot.server_observed_at.astimezone(UTC).isoformat(), int(snapshot.transport_injected))


def _observation_key(scope: ProtectionScope, metadata: tuple[Any, ...],
                     members: list[VariantKey]) -> str:
    return _hash([scope.account_id, scope.environment, list(metadata), sorted(members)])


def _conflict(kind: Literal["EXECUTION", "TRANSACTION", "ORDER", "SNAPSHOT"], identity: str,
              left: str, right: str, reason: str) -> HistoryConflict:
    left, right = sorted((left, right))
    return HistoryConflict(_hash([kind, identity, reason, left, right]), kind, identity,
                           left, right, reason)


def _reasons(left: HistoryRecord, right: HistoryRecord) -> tuple[str, ...]:
    if left == right:
        return ()
    if not isinstance(left, OrderHistoryRecord) or not isinstance(right, OrderHistoryRecord):
        return ("EVENT_CONTENT_CHANGED",)
    reasons = []
    if left.updated_ms == right.updated_ms:
        reasons.append("ORDER_UPDATE_COLLISION")
    if left.symbol != right.symbol or left.side != right.side:
        reasons.append("ORDER_IDENTITY_CHANGED")
    older, newer = sorted((left, right), key=lambda r: r.updated_ms)
    if older.updated_ms < newer.updated_ms and older.cumulative_filled > newer.cumulative_filled:
        reasons.append("ORDER_FILL_REGRESSION")
    return tuple(reasons)


def _required_conflicts(snapshots: dict[str, HistorySnapshot]) -> dict[str, HistoryConflict]:
    result: dict[str, HistoryConflict] = {}
    by_snapshot_id: dict[str, list[str]] = {}
    by_identity: dict[tuple[HistoryKind, str],
                      dict[str, tuple[HistoryRecord, set[str]]]] = {}
    for key, snapshot in snapshots.items():
        by_snapshot_id.setdefault(snapshot.snapshot_id, []).append(key)
        records: tuple[HistoryRecord, ...] = (*snapshot.executions, *snapshot.orders,
                                              *snapshot.transactions)
        for record in records:
            kind, identity, variant_hash, *_ = _variant(record)
            variants = by_identity.setdefault((cast(HistoryKind, kind), identity), {})
            if variant_hash in variants:
                if variants[variant_hash][0] != record:
                    _fail()
                variants[variant_hash][1].add(key)
            else:
                variants[variant_hash] = (record, {key})
    for snapshot_id, keys in by_snapshot_id.items():
        for left_key, right_key in combinations(keys, 2):
            conflict = _conflict("SNAPSHOT", snapshot_id, left_key, right_key, "SNAPSHOT_ID_REUSED")
            _add_conflict(result, conflict)
    for (kind, identity), variants in by_identity.items():
        for (left, left_keys), (right, right_keys) in combinations(variants.values(), 2):
            reasons = _reasons(left, right)
            if reasons:
                # Only real contradictions incur the unavoidable pair-evidence output cost.
                for left_key in left_keys:
                    for right_key in right_keys:
                        for reason in reasons:
                            _add_conflict(result, _conflict(kind, identity, left_key, right_key, reason))
    return result


def _add_conflict(result: dict[str, HistoryConflict], conflict: HistoryConflict) -> None:
    previous = result.get(conflict.conflict_key)
    if previous is not None and previous != conflict:
        _fail()
    result[conflict.conflict_key] = conflict


def _new_conflicts(db: sqlite3.Connection, observation_key: str, snapshot_id: str,
                   records: tuple[HistoryRecord, ...], variants: list[VariantRow],
                   ) -> dict[str, HistoryConflict]:
    """Compare only this new observation's IDs against distinct saved variants."""
    result: dict[str, HistoryConflict] = {}
    for (other_key,) in db.execute("SELECT observation_key FROM history_snapshots "
                                   "WHERE snapshot_id=? AND observation_key!=?",
                                   (snapshot_id, observation_key)):
        _add_conflict(result, _conflict("SNAPSHOT", snapshot_id, observation_key, other_key,
                                        "SNAPSHOT_ID_REUSED"))
    for record, variant in zip(records, variants, strict=True):
        kind, identity, variant_hash = variant[:3]
        for raw in db.execute("SELECT * FROM history_variants WHERE kind=? AND record_id=? "
                              "AND variant_hash!=?", (kind, identity, variant_hash)):
            old = _decode_record(kind, raw[6])
            if tuple(raw) != _variant(old):
                _fail()
            reasons = _reasons(record, old)
            if reasons:
                for (other_key,) in db.execute("SELECT observation_key FROM history_members "
                        "WHERE kind=? AND record_id=? AND variant_hash=?", tuple(raw[:3])):
                    for reason in reasons:
                        _add_conflict(result, _conflict(cast(HistoryKind, kind), identity,
                            observation_key, other_key, reason))
    return result


def _read_evidence(db: sqlite3.Connection, scope: ProtectionScope) -> dict[str, HistorySnapshot]:
    variants: dict[VariantKey, HistoryRecord] = {}
    for raw in db.execute("SELECT * FROM history_variants"):
        row = tuple(raw)
        record = _decode_record(row[0], row[6])
        if row != _variant(record) or row[:3] in variants:
            _fail()
        variants[row[:3]] = record
    members: dict[str, list[VariantKey]] = {}
    used: set[VariantKey] = set()
    for observation, kind, identity, variant_hash in db.execute("SELECT * FROM history_members"):
        key = (kind, identity, variant_hash)
        if key not in variants:
            _fail()
        members.setdefault(observation, []).append(key)
        used.add(key)
    snapshots: dict[str, HistorySnapshot] = {}
    for raw in db.execute("SELECT * FROM history_snapshots"):
        key, snapshot_id, symbol, start, end, observed, server, injected = tuple(raw)
        if type(injected) is not int or injected not in (0, 1):
            _fail()
        records = [variants[item] for item in members.get(key, [])]
        snapshot = HistorySnapshot(scope, symbol, snapshot_id, HistoryWindow(start, end),
            datetime.fromisoformat(observed), datetime.fromisoformat(server),
            tuple(r for r in records if type(r) is ExecutionRecord),
            tuple(r for r in records if type(r) is OrderHistoryRecord),
            tuple(r for r in records if type(r) is TransactionRecord), bool(injected))
        validate_history_snapshot(snapshot)
        if (tuple(raw)[1:] != _metadata(snapshot)
                or key != _observation_key(scope, _metadata(snapshot), members.get(key, []))
                or key in snapshots):
            _fail()
        snapshots[key] = snapshot
    if used != set(variants) or not set(members) <= set(snapshots):
        _fail()
    return snapshots


def validate_history_database(db: sqlite3.Connection, *,
                               scope: ProtectionScope | None = None) -> ProtectionScope:
    """Read-only validation of schema, typed contents, provenance and all conflict evidence."""
    try:
        _validate_schema(db)
        rows = list(db.execute("SELECT id,account,environment FROM history_scope"))
        if len(rows) != 1 or type(rows[0][0]) is not int or rows[0][0] != 1:
            _fail()
        persisted = ProtectionScope(rows[0][1], rows[0][2])
        validate_history_scope(persisted)
        if scope is not None:
            validate_history_scope(scope)
            if scope != persisted:
                raise HistoryError("HISTORY_SCOPE_MISMATCH")
        snapshots = _read_evidence(db, persisted)
        expected = _required_conflicts(snapshots)
        actual = {row[0]: tuple(row) for row in db.execute("SELECT * FROM history_conflicts")}
        if actual != {key: tuple(getattr(value, f.name) for f in fields(value))
                      for key, value in expected.items()}:
            _fail()
        return persisted
    except HistoryError:
        raise
    except (sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise HistoryError("HISTORY_DATABASE_INVALID") from None


class HistoryStore:
    """One scope and owner thread per file; only atomic INSERTs are exposed."""

    def __init__(self, path: Path, scope: ProtectionScope) -> None:
        validate_history_scope(scope)
        self.scope = scope
        self.path = Path(path)
        self._owner_thread = get_ident()
        self._closed = False
        existing = self.path.exists()
        db = sqlite3.connect(self.path.resolve().as_uri() + ("?mode=rw" if existing else "?mode=rwc"),
                             uri=True, isolation_level=None)
        self._db = db
        try:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA foreign_keys=ON")
            if existing:
                validate_history_database(db, scope=scope)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            if not existing:
                db.execute("BEGIN IMMEDIATE")
                _create_schema(db)
                db.execute("INSERT INTO history_scope VALUES (1,?,?)",
                           (scope.account_id, scope.environment))
                db.execute("COMMIT")
        except Exception:
            db.close()
            raise

    def ingest(self, snapshot: HistorySnapshot) -> IngestionReceipt:
        self._check_open()
        validate_history_snapshot(snapshot)
        if snapshot.scope != self.scope:
            raise HistoryError("HISTORY_SCOPE_MISMATCH")
        records: tuple[HistoryRecord, ...] = (*snapshot.executions, *snapshot.orders,
                                              *snapshot.transactions)
        variants = [_variant(r) for r in records]
        members = [(r[0], r[1], r[2]) for r in variants]
        metadata = _metadata(snapshot)
        key = _observation_key(self.scope, metadata, members)
        db = self._db
        try:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM history_snapshots WHERE observation_key=?",
                                  (key,)).fetchone()
            new_variants = 0
            if existing is not None:
                stored_members = list(db.execute("SELECT kind,record_id,variant_hash FROM "
                                                  "history_members WHERE observation_key=?", (key,)))
                if tuple(existing)[1:] != metadata or sorted(stored_members) != sorted(members):
                    _fail()
            for row in variants:
                previous = db.execute("SELECT * FROM history_variants WHERE kind=? AND "
                                      "record_id=? AND variant_hash=?", row[:3]).fetchone()
                if previous is not None:
                    if tuple(previous) != row:
                        _fail()
                elif existing is not None:
                    _fail()
                else:
                    db.execute("INSERT INTO history_variants VALUES (?,?,?,?,?,?,?)", row)
                    new_variants += 1
            if existing is None:
                db.execute("INSERT INTO history_snapshots VALUES (?,?,?,?,?,?,?,?)", (key, *metadata))
                db.executemany("INSERT INTO history_members VALUES (?,?,?,?)",
                               [(key, *member) for member in members])
            required = (_new_conflicts(db, key, snapshot.snapshot_id, records, variants)
                        if existing is None else {})
            new_conflicts = 0
            for conflict in required.values():
                values = tuple(getattr(conflict, f.name) for f in fields(conflict))
                previous = db.execute("SELECT * FROM history_conflicts WHERE conflict_key=?",
                                      (conflict.conflict_key,)).fetchone()
                if previous is None:
                    db.execute("INSERT INTO history_conflicts VALUES (?,?,?,?,?,?)", values)
                    new_conflicts += 1
                elif tuple(previous) != values:
                    _fail()
            conflict_keys = tuple(row[0] for row in db.execute(
                "SELECT conflict_key FROM history_conflicts WHERE left_observation_key=? "
                "OR right_observation_key=? ORDER BY conflict_key", (key, key)))
            receipt = IngestionReceipt(key, snapshot.snapshot_id, existing is not None,
                                       new_variants, new_conflicts, conflict_keys)
            db.execute("COMMIT")
            return receipt
        except Exception as error:
            if db.in_transaction:
                db.execute("ROLLBACK")
            if isinstance(error, HistoryError):
                raise
            raise HistoryError("HISTORY_INGEST_FAILED") from None

    def coverage(self, symbol: str, *, start_ms: int, end_ms: int) -> HistoryCoverage:
        """Report bounded query completion, gaps and durable contradictions."""
        return self._query_window(symbol, start_ms, end_ms, include_variants=False).coverage

    def load_window(self, symbol: str, *, start_ms: int, end_ms: int) -> HistoryWindowView:
        """Return every distinct relevant variant, never a newly fresh snapshot."""
        return self._query_window(symbol, start_ms, end_ms, include_variants=True)

    def _query_window(self, symbol: str, start_ms: int, end_ms: int,
                      *, include_variants: bool) -> HistoryWindowView:
        self._check_open()
        symbol = _symbol(symbol)
        window = HistoryWindow(start_ms, end_ms)
        db = self._db
        try:
            # One read transaction keeps coverage, conflicts and variants at the same
            # SQLite snapshot if another owner commits new evidence concurrently.
            db.execute("BEGIN")
            provenance = tuple(QueryProvenance(row[0], row[1], row[2],
                HistoryWindow(row[3], row[4]), datetime.fromisoformat(row[5]),
                datetime.fromisoformat(row[6]), bool(row[7])) for row in db.execute(
                    "SELECT * FROM history_snapshots WHERE start_ms<=? AND end_ms>=? "
                    "ORDER BY observation_key", (end_ms, start_ms)))
            # Account-wide transaction coverage makes every overlapping observation
            # contribute. Identity joins also taint earlier matching order updates
            # that were not themselves an endpoint of the contradiction pair.
            conflicts = tuple(HistoryConflict(*row) for row in db.execute(
                "WITH contributing AS (SELECT observation_key FROM history_snapshots "
                "WHERE start_ms<=? AND end_ms>=?) "
                "SELECT c.* FROM history_conflicts c WHERE "
                "EXISTS (SELECT 1 FROM contributing p WHERE "
                "p.observation_key=c.left_observation_key OR "
                "p.observation_key=c.right_observation_key) OR "
                "EXISTS (SELECT 1 FROM history_members m JOIN contributing p "
                "ON p.observation_key=m.observation_key WHERE "
                "m.kind=c.kind AND m.record_id=c.record_id) ORDER BY c.conflict_key",
                (end_ms, start_ms)))
            variants: list[ObservedVariant] = []
            if include_variants:
                rows = db.execute(
                    "SELECT v.kind,v.record_id,v.variant_hash,v.payload,m.observation_key "
                    "FROM history_variants v JOIN history_members m "
                    "ON m.kind=v.kind AND m.record_id=v.record_id "
                    "AND m.variant_hash=v.variant_hash WHERE v.event_ms>=? AND v.event_ms<=? "
                    "AND (v.kind='TRANSACTION' OR v.symbol=?) "
                    "ORDER BY v.kind,v.record_id,v.variant_hash,m.observation_key",
                    (start_ms, end_ms, symbol))
                keys: list[str] = []
                previous: tuple[str, str, str, str] | None = None
                for kind, record_id, variant_hash, payload, observation_key in rows:
                    current = (kind, record_id, variant_hash, payload)
                    if previous is not None and current != previous:
                        variants.append(ObservedVariant(cast(HistoryKind, previous[0]),
                            previous[1], previous[2], _decode_record(previous[0], previous[3]),
                            tuple(keys)))
                        keys = []
                    keys.append(observation_key)
                    previous = current
                if previous is not None:
                    variants.append(ObservedVariant(cast(HistoryKind, previous[0]),
                        previous[1], previous[2], _decode_record(previous[0], previous[3]),
                        tuple(keys)))
            result = HistoryWindowView(compose_coverage(self.scope, symbol, window,
                provenance, conflicts), tuple(variants))
            db.execute("COMMIT")
            return result
        except Exception as error:
            if db.in_transaction:
                db.execute("ROLLBACK")
            if isinstance(error, HistoryError):
                raise
            raise HistoryError("HISTORY_QUERY_FAILED") from None

    def close(self) -> None:
        if get_ident() != self._owner_thread:
            raise HistoryError("HISTORY_OWNER_THREAD_REQUIRED")
        self._db.close()
        self._closed = True

    def _check_open(self) -> None:
        if get_ident() != self._owner_thread:
            raise HistoryError("HISTORY_OWNER_THREAD_REQUIRED")
        if self._closed:
            raise HistoryError("HISTORY_STORE_CLOSED")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()
