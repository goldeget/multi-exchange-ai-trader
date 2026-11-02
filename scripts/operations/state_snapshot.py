"""Offline ledger/runtime recovery, Python 3.12 standard library only.

No command stops services or opens source SQLite databases. Operators must stop
ALL writers and keep source/backup/parent directories private for the operation.
The manifest detects accidental corruption; it is not a signature or encryption.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import runpy
import shutil
import sqlite3
import stat
import struct
import sys
import tempfile
import time
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

MAX_FILES = 10_000
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
SQLITE_SECONDS = 10
PAPER = {
    "metadata": "value", "scenarios": "id created_at snapshot",
    "events": "scenario seq payload received_at previous hash",
    "commands": "scenario key payload seq result_hash", "fills": "scenario idx payload",
    "orders": "scenario idx payload", "settlements": "scenario seq payload",
}
SCHEMAS = {
    "paper.sqlite3": PAPER,
    "spot-paper.sqlite3": PAPER,
    "order-reviews.sqlite3": {"order_reviews": "id scenario payload confirmation_key result"},
    "ai.sqlite3": {"ai_settings": "id revision monthly_limit",
                   "reservations": "request_id period maximum actual status updated_at"},
    "recommendations.sqlite3": {"cards": "id status payload commands updated_at"},
    "research.sqlite3": {
        "metadata": "value", "runs": "id snapshot",
        "journal": "run seq operation snapshot previous hash",
        "commands": "run key payload response", "reports": "id payload",
        "report_commands": "key dataset report",
    },
}
RUNTIME_SCHEMAS = {
    "execution.sqlite3": {
        "commands": "account environment command_key client_id kind payload payload_hash state "
                    "risk notional margin terminal",
        "audit": "sequence account environment command_key action detail occurred_at",
        "projections": "account environment projection_key revision payload",
        "inbox": "account environment event_id event_payload projection_key revision projection_payload",
    },
    "runtime-state.sqlite3": {
        "runtime_orders": "account environment order_id revision payload reconciliation_required",
        "runtime_positions": "account environment position_id revision payload reconciliation_required",
        "runtime_inbox": "account environment event_id event_payload position_id revision",
    },
    "runtime-policy.sqlite3": {"runtime_policy": "id revision payload desired_state"},
    "ai-dispatch.sqlite3": {
        "ai_dispatches": "request_id binding_hash maximum status failure exact_cost created_at updated_at",
    },
}
RUNTIME_KEYS: dict[str, dict[str, tuple[tuple[str, ...], ...]]] = {
    "execution.sqlite3": {
        "commands": (("account", "environment", "command_key"), ("client_id",)),
        "audit": (("sequence",),),
        "projections": (("account", "environment", "projection_key"),),
        "inbox": (("account", "environment", "event_id"),),
    },
    "runtime-state.sqlite3": {
        "runtime_orders": (("account", "environment", "order_id"),),
        "runtime_positions": (("account", "environment", "position_id"),),
        "runtime_inbox": (("account", "environment", "event_id"),),
    },
    "runtime-policy.sqlite3": {"runtime_policy": (("id",),)},
    "ai-dispatch.sqlite3": {"ai_dispatches": (("request_id",),)},
}
HISTORY_SCHEMAS = {
    "history.sqlite3": {
        "history_scope": "id account environment",
        "history_snapshots": "observation_key snapshot_id symbol start_ms end_ms observed_at "
                             "server_observed_at transport_injected",
        "history_variants": "kind record_id variant_hash symbol event_ms updated_ms payload",
        "history_members": "observation_key kind record_id variant_hash",
        "history_conflicts": "conflict_key kind record_id left_observation_key right_observation_key reason",
    },
}
ALL_SCHEMAS = SCHEMAS | RUNTIME_SCHEMAS | HISTORY_SCHEMAS
# Anchor to this script, including when launched outside the repository or via runpy.
# The sibling is part of the portable operations tool, not an application dependency.
_HISTORY_VALIDATOR = runpy.run_path(str(Path(__file__).with_name("history_snapshot_validation.py")))
METADATA = {
    "paper.sqlite3": {"schema": 1, "assumptions": "PAPER-v1", "engine": "1.231.0"},
    "spot-paper.sqlite3": {"schema": 1, "assumptions": "SPOT-PAPER-v2",
                           "engine": "1.231.0", "product": "SPOT"},
    "research.sqlite3": {"schema": 1, "engine": "1.231.0", "automation": "SYNTHETIC_AUTO_V1",
                         "event_limit": 500, "scenario_limit": 16},
}


class SnapshotError(ValueError):
    """Safe failure; command output never includes file contents or raw errors."""


@dataclass(frozen=True)
class Entry:
    path: str
    size: int
    sha256: str


def safe_path(raw: str | Path, *, directory: bool = True) -> Path:
    path = Path(raw)
    if ".." in path.parts:
        raise SnapshotError("Parent traversal is forbidden")
    path = path.absolute()
    for part in [*reversed(path.parents), path]:
        if part.is_symlink() or part.is_junction():
            raise SnapshotError("Symlinks and junctions are forbidden")
    if directory and not path.is_dir():
        raise SnapshotError("Existing directory required")
    return path


def target_path(raw: str | Path, source: Path) -> Path:
    path = safe_path(raw, directory=False)
    safe_path(path.parent)
    if path.exists() or path == source or source in path.parents:
        raise SnapshotError("Target must be new and outside the source")
    return path


def file_hash(path: Path) -> tuple[int, str]:
    safe_path(path, directory=False)
    initial = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
        raise SnapshotError("Only regular, non-hardlinked files are accepted")
    if initial.st_size > MAX_FILE_BYTES:
        raise SnapshotError("File size limit exceeded")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    digest, size = hashlib.sha256(), 0
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino):
            raise SnapshotError("File changed during read")
        while block := stream.read(1024 * 1024):
            size += len(block)
            if size > MAX_FILE_BYTES:
                raise SnapshotError("File size limit exceeded")
            digest.update(block)
        final = os.fstat(stream.fileno())
    current = path.stat(follow_symlinks=False)
    def identity(s: os.stat_result) -> tuple[int, ...]:
        # Windows Path.stat and fstat expose different legacy ctime meanings.
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns,
                s.st_ctime_ns if os.name == "posix" else 0)
    if identity(initial) != identity(final) or identity(final) != identity(current):
        raise SnapshotError("File changed during read")
    return size, digest.hexdigest()


def inventory(root: Path, *, require_runtime: bool = False,
              require_history: bool = False) -> list[Entry]:
    safe_path(root)
    files: list[Path] = []
    allowed = {name + suffix for name in ALL_SCHEMAS for suffix in ("", "-wal", "-shm")}
    with os.scandir(root) as children:
        for child in children:
            path = Path(child.path)
            if child.name == "datasets":
                safe_path(path)
                with os.scandir(path) as datasets:
                    for dataset in datasets:
                        if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}\.json", dataset.name):
                            raise SnapshotError("Unexpected dataset path")
                        files.append(Path(dataset.path))
                        if len(files) > MAX_FILES:
                            raise SnapshotError("File count limit exceeded")
            elif child.name in allowed:
                files.append(path)
            else:
                raise SnapshotError("Unexpected state entry; secrets, rollback journals and extras refused")
            if len(files) > MAX_FILES:
                raise SnapshotError("File count limit exceeded")
    if not (root / "datasets").is_dir() or any(not (root / name).is_file() for name in SCHEMAS):
        raise SnapshotError("All six ledgers and datasets directory are required")
    present = {path.name for path in files}
    if ("execution.sqlite3" in present) != ("runtime-state.sqlite3" in present):
        raise SnapshotError("Execution journal and normalized runtime state must be preserved together")
    if require_runtime and not set(RUNTIME_SCHEMAS) <= present:
        raise SnapshotError("Complete four-store runtime generation required")
    if require_history and "history.sqlite3" not in present:
        raise SnapshotError("History generation required")
    if "history.sqlite3" in present and not {"execution.sqlite3", "runtime-state.sqlite3"} <= present:
        raise SnapshotError("History requires execution journal and normalized runtime state")
    for name in ALL_SCHEMAS:
        if name not in present and any(name + suffix in present for suffix in ("-wal", "-shm")):
            raise SnapshotError("SQLite sidecar has no corresponding database")
    result, total = [], 0
    for path in sorted(files):
        size, digest = file_hash(path)
        total += size
        if total > MAX_TOTAL_BYTES:
            raise SnapshotError("Total size limit exceeded")
        result.append(Entry(path.relative_to(root).as_posix(), size, digest))
    return result


def copy_files(source: Path, target: Path, entries: list[Entry]) -> None:
    (target / "datasets").mkdir(mode=0o700)
    for entry in entries:
        src, dst = source / entry.path, target / entry.path
        # Parents are trusted private directories; O_NOFOLLOW additionally rejects file swaps.
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(src, flags), "rb") as reader, dst.open("xb") as writer:
            os.chmod(dst, 0o600)
            copied = 0
            while block := reader.read(1024 * 1024):
                copied += len(block)
                if copied > entry.size:
                    raise SnapshotError("File changed during copy")
                writer.write(block)
            writer.flush()
            os.fsync(writer.fileno())
        if file_hash(dst) != (entry.size, entry.sha256):
            raise SnapshotError("Copy hash mismatch")


def check_wal(path: Path) -> None:
    """Reject damaged/ambiguous WAL that SQLite might silently ignore.

    Format/checksum: https://www.sqlite.org/fileformat.html#wal_file_format
    Conservative offline format: no incomplete, uncommitted or reused stale tail.
    """
    if path.stat().st_size == 0:
        return
    deadline = time.monotonic() + SQLITE_SECONDS
    with path.open("rb") as stream:
        header = stream.read(32)
        if len(header) != 32:
            raise SnapshotError("Incomplete WAL header")
        magic, version, page_size = struct.unpack(">III", header[:12])
        if (magic not in {0x377F0682, 0x377F0683} or version != 3007000
                or not 512 <= page_size <= 65536 or page_size & (page_size - 1)):
            raise SnapshotError("Unsupported WAL header")
        byte_order = "<II" if magic == 0x377F0682 else ">II"

        def checksum(data: bytes, state: tuple[int, int]) -> tuple[int, int]:
            first, second = state
            for left, right in struct.iter_unpack(byte_order, data):
                first = (first + left + second) & 0xFFFFFFFF
                second = (second + right + first) & 0xFFFFFFFF
            return first, second

        running = checksum(header[:24], (0, 0))
        if running != struct.unpack(">II", header[24:32]):
            raise SnapshotError("WAL header checksum mismatch")
        last_commit = True
        while frame := stream.read(24 + page_size):
            if time.monotonic() > deadline:
                raise SnapshotError("WAL verification time limit exceeded")
            if len(frame) != 24 + page_size or frame[8:16] != header[16:24]:
                raise SnapshotError("Incomplete or reused WAL tail requires manual review")
            page, commit = struct.unpack(">II", frame[:8])
            if page == 0:
                raise SnapshotError("Invalid WAL page")
            running = checksum(frame[24:], checksum(frame[:8], running))
            if running != struct.unpack(">II", frame[16:24]):
                raise SnapshotError("WAL frame checksum mismatch")
            last_commit = commit != 0
        if not last_commit:
            raise SnapshotError("Uncommitted WAL tail requires manual review")


def check_sqlite(root: Path, entries: list[Entry]) -> None:
    # SQLite may rebuild SHM or perform recovery. Only disposable copies are opened.
    with tempfile.TemporaryDirectory(prefix="trader-sqlite-check-") as raw:
        scratch = Path(raw)
        for entry in entries:
            if "/" not in entry.path:
                shutil.copyfile(root / entry.path, scratch / entry.path)
                if entry.path.endswith("-wal"):
                    check_wal(scratch / entry.path)
        present = {entry.path for entry in entries}
        history_scope: tuple[str, str] | None = None
        runtime_scopes: set[tuple[str, str]] = set()
        for name, schema in ALL_SCHEMAS.items():
            if name not in present:
                continue
            deadline = time.monotonic() + SQLITE_SECONDS
            with closing(sqlite3.connect((scratch / name).as_uri() + "?mode=ro", uri=True,
                                         timeout=1)) as db:
                def expired(stop_at: float = deadline) -> int:
                    return int(time.monotonic() > stop_at)
                db.set_progress_handler(expired, 1000)
                db.execute("PRAGMA trusted_schema=OFF")
                db.execute("PRAGMA query_only=ON")
                if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise SnapshotError("SQLite integrity check failed")
                tables = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )}
                if tables != set(schema):
                    raise SnapshotError("Unsupported SQLite schema")
                for table, columns in schema.items():
                    actual = [row[1] for row in db.execute(f'PRAGMA table_xinfo("{table}")')]
                    if actual != columns.split():
                        raise SnapshotError("Unsupported SQLite columns")
                version = db.execute("PRAGMA user_version").fetchone()[0]
                versioned = (name in METADATA or name in HISTORY_SCHEMAS
                             or name in RUNTIME_SCHEMAS and name != "execution.sqlite3")
                if version != (1 if versioned else 0):
                    raise SnapshotError("Unsupported SQLite version")
                if name in METADATA:
                    rows = db.execute("SELECT value FROM metadata").fetchmany(2)
                    if len(rows) != 1 or json.loads(rows[0][0]) != METADATA[name]:
                        raise SnapshotError("Unsupported ledger metadata")
                if name == "ai.sqlite3" and "ai-dispatch.sqlite3" not in present:
                    for (request_id,) in db.execute("SELECT request_id FROM reservations"):
                        if isinstance(request_id, str) and re.fullmatch(r"dispatch_[0-9a-f]{64}", request_id):
                            raise SnapshotError("AI dispatch history file is required for dispatched liabilities")
                if name in RUNTIME_SCHEMAS:
                    check_runtime(db, name)
                    if name in {"execution.sqlite3", "runtime-state.sqlite3"}:
                        for table in schema:
                            runtime_scopes.update(tuple(row) for row in db.execute(
                                f'SELECT DISTINCT account,environment FROM "{table}"'))
                    if name == "ai-dispatch.sqlite3":
                        check_dispatch_budget(db, scratch / "ai.sqlite3")
                if name in HISTORY_SCHEMAS:
                    history_scope = check_history(db)
        if history_scope is not None and runtime_scopes - {history_scope}:
            raise SnapshotError("History and paired runtime account scopes must match")


def check_history(db: sqlite3.Connection) -> tuple[str, str]:
    """Standalone strict schema, payload, provenance and conflict validation."""
    try:
        return cast(tuple[str, str], _HISTORY_VALIDATOR["check_history"](db))
    except (sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise SnapshotError("History database integrity validation failed") from None


def json_payload(raw: object) -> object:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 65536:
        raise SnapshotError("Runtime payload limit or type invalid")

    def nonfinite(_value: str) -> Any:
        raise SnapshotError("Nonfinite runtime JSON")

    result = json.loads(raw, object_pairs_hook=unique_object, parse_constant=nonfinite)
    json.dumps(result, allow_nan=False)
    return result


def identifier(value: object) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise SnapshotError("Runtime identity invalid")


def nonnegative(value: object) -> Decimal:
    if not isinstance(value, str) or len(value) > 2048:
        raise SnapshotError("Runtime amount invalid")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise SnapshotError("Runtime amount invalid") from None
    if not result.is_finite() or result < 0:
        raise SnapshotError("Runtime amount invalid")
    return result


def check_runtime_constraints(db: sqlite3.Connection, name: str) -> None:
    for table, required in RUNTIME_KEYS[name].items():
        indexes = list(db.execute(f'PRAGMA index_list("{table}")'))
        keys: list[set[str]] = []
        for index in indexes:
            if index[2] and not index[4]:
                parts = db.execute('SELECT name,coll FROM pragma_index_xinfo(?) WHERE "key"=1',
                                   (index[1],)).fetchall()
                if (len(parts) == len({part[0] for part in parts})
                        and all(part[0] is not None and part[1].upper() == "BINARY" for part in parts)):
                    keys.append({part[0] for part in parts})
        columns = list(db.execute(f'PRAGMA table_xinfo("{table}")'))
        primary = [row for row in columns if row[5]]
        rowid_key = (len(primary) == 1 and primary[0][2].upper() == "INTEGER"
                     and not any(index[3] == "pk" for index in indexes))
        if rowid_key:
            keys.append({primary[0][1]})
        if any(set(key) not in keys for key in required):
            raise SnapshotError("Unsupported runtime SQLite identity key")
        # Audit INSERT omits sequence; only an INTEGER rowid primary key generates it.
        if table == "audit" and (not rowid_key or primary[0][1] != "sequence"):
            raise SnapshotError("Unsupported runtime audit sequence key")
        references = [tuple(row) for row in db.execute(f'PRAGMA foreign_key_list("{table}")')]
        expected = ({("runtime_positions", column, column, "NO ACTION", "NO ACTION", "NONE")
                     for column in ("account", "environment", "position_id")}
                    if name == "runtime-state.sqlite3" and table == "runtime_inbox" else set())
        if (len(references) != len(expected) or {row[2:] for row in references} != expected
                or expected and len({row[0] for row in references}) != 1):
            raise SnapshotError("Unsupported runtime SQLite foreign key constraint")


def check_runtime(db: sqlite3.Connection, name: str) -> None:
    """Storage invariants only; application replay/venue reconciliation still required."""
    objects = db.execute("SELECT name,type,sql FROM sqlite_master WHERE type IN ('view','trigger')").fetchall()
    expected = {"audit_no_update": "UPDATE", "audit_no_delete": "DELETE"} if name == "execution.sqlite3" else {}
    if {row[0] for row in objects} != set(expected) or any(row[1] != "trigger" for row in objects):
        raise SnapshotError("Unsupported runtime SQLite objects")
    for trigger, _kind, sql in objects:
        expected_sql = (f"CREATE TRIGGER {trigger} BEFORE {expected[trigger]} ON audit "
                        "BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END")
        if " ".join(sql.split()) != expected_sql:
            raise SnapshotError("Runtime audit immutability is unsupported")
    check_runtime_constraints(db, name)
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise SnapshotError("Runtime references are incomplete")
    db.row_factory = sqlite3.Row
    for table in RUNTIME_SCHEMAS[name]:
        for row in db.execute(f'SELECT * FROM "{table}"'):
            columns = set(row.keys())
            if "account" in columns:
                identifier(row["account"])
                if row["environment"] not in {"DEMO", "LIVE"}:
                    raise SnapshotError("Runtime environment invalid")
            for column in columns & {"order_id", "position_id", "event_id", "command_key", "projection_key"}:
                if row[column] is not None or column != "command_key":
                    identifier(row[column])
            if "revision" in columns and (type(row["revision"]) is not int or row["revision"] < (0 if name == "runtime-policy.sqlite3" else 1)):
                raise SnapshotError("Runtime revision invalid")
            for column in columns & {"payload", "event_payload", "projection_payload", "detail"}:
                json_payload(row[column])
            if "reconciliation_required" in columns and row["reconciliation_required"] not in (0, 1):
                raise SnapshotError("Runtime reconciliation flag invalid")
            if table == "runtime_inbox":
                current = db.execute("SELECT revision FROM runtime_positions WHERE account=? AND environment=? AND position_id=?",
                                     (row["account"], row["environment"], row["position_id"])).fetchone()
                if current is None or current[0] < row["revision"]:
                    raise SnapshotError("Runtime inbox projection missing or stale")
            elif name == "execution.sqlite3" and table == "commands":
                identifier(row["client_id"])
                if row["kind"] not in {"ENTRY", "PROTECTION", "EXIT", "CANCEL", "LEVERAGE"} or row["state"] not in {"PENDING", "SENDING", "ACKNOWLEDGED", "UNKNOWN", "REJECTED"}:
                    raise SnapshotError("Runtime command state invalid")
                if hashlib.sha256(row["payload"].encode()).hexdigest() != row["payload_hash"] or row["terminal"] not in (0, 1):
                    raise SnapshotError("Runtime command integrity invalid")
                amounts = [row[key] for key in ("risk", "notional", "margin")]
                if any(item is None for item in amounts) and not all(item is None for item in amounts):
                    raise SnapshotError("Runtime reservation incomplete")
                for value in amounts:
                    if value is not None:
                        nonnegative(value)
            elif name == "runtime-policy.sqlite3":
                if row["id"] != 1 or row["desired_state"] != "STOPPED" or not isinstance(json_payload(row["payload"]), dict):
                    raise SnapshotError("Runtime policy cannot restore execution authority")
            elif name == "ai-dispatch.sqlite3":
                identifier(row["request_id"])
                if not isinstance(row["binding_hash"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["binding_hash"]):
                    raise SnapshotError("AI dispatch binding invalid")
                if row["status"] not in {"CLAIMED", "DISPATCHING", "CHARGED", "SETTLED", "UNCERTAIN", "BLOCKED"}:
                    raise SnapshotError("AI dispatch state invalid")
                if nonnegative(row["maximum"]) <= 0:
                    raise SnapshotError("AI dispatch maximum invalid")
                if row["exact_cost"] is not None:
                    nonnegative(row["exact_cost"])
    if name == "runtime-policy.sqlite3" and db.execute("SELECT count(*) FROM runtime_policy").fetchone()[0] != 1:
        raise SnapshotError("Runtime policy must contain exactly one draft")


def check_dispatch_budget(dispatch: sqlite3.Connection, budget_path: Path) -> None:
    with closing(sqlite3.connect(budget_path.as_uri() + "?mode=ro", uri=True, timeout=1)) as budget:
        budget.execute("PRAGMA trusted_schema=OFF")
        budget.execute("PRAGMA query_only=ON")
        budget.row_factory = sqlite3.Row
        known: set[str] = set()
        for row in dispatch.execute("SELECT * FROM ai_dispatches"):
            charged = row["status"] in {"CHARGED", "SETTLED"}
            if charged != (row["exact_cost"] is not None):
                raise SnapshotError("AI charge evidence does not match dispatch state")
            reservation_id = "dispatch_" + hashlib.sha256(row["request_id"].encode()).hexdigest()
            known.add(reservation_id)
            reservation = budget.execute("SELECT * FROM reservations WHERE request_id=?", (reservation_id,)).fetchone()
            if reservation is None:
                if row["status"] not in {"CLAIMED", "BLOCKED"}:
                    raise SnapshotError("AI dispatch liability is missing from budget")
                continue
            if (nonnegative(reservation["maximum"]) != nonnegative(row["maximum"])
                    and row["status"] != "BLOCKED"):
                raise SnapshotError("AI dispatch and budget maximum differ")
            if reservation["status"] not in {"reserved", "uncertain", "settled"}:
                raise SnapshotError("AI budget liability state invalid")
            # Unknown sent calls keep their full reservation. Only the durable
            # CHARGED write precedes settlement; BLOCKED may retain an unrelated
            # preexisting reservation, including an already settled one.
            if (row["status"] in {"DISPATCHING", "UNCERTAIN"}
                    and reservation["status"] != "uncertain"
                    or row["status"] == "CLAIMED" and reservation["status"] == "settled"
                    or charged and reservation["status"] == "reserved"):
                raise SnapshotError("AI dispatch and budget states do not match")
            if row["status"] == "SETTLED" and reservation["status"] != "settled":
                raise SnapshotError("AI settlement is incomplete")
            if (reservation["status"] == "settled" and row["exact_cost"] is not None
                    and nonnegative(reservation["actual"]) != nonnegative(row["exact_cost"])):
                raise SnapshotError("AI exact charge and budget settlement differ")
        for (reservation_id,) in budget.execute("SELECT request_id FROM reservations"):
            if re.fullmatch(r"dispatch_[0-9a-f]{64}", reservation_id) and reservation_id not in known:
                raise SnapshotError("AI dispatch history is missing for a budget liability")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError("Duplicate manifest fields")
        result[key] = value
    return result


def verify(source: Path, *, require_runtime: bool = False,
           require_history: bool = False) -> list[Entry]:
    safe_path(source)
    if {p.name for p in source.iterdir()} != {"manifest.json", "data"}:
        raise SnapshotError("Incomplete snapshot or unexpected snapshot entry")
    path = safe_path(source / "manifest.json", directory=False)
    size, _digest = file_hash(path)
    if size > MAX_MANIFEST_BYTES:
        raise SnapshotError("Manifest size limit exceeded")
    manifest = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    base_fields = {"format_version", "created_at", "writers_stopped", "files"}
    if (not isinstance(manifest, dict)
            or manifest.get("format_version") not in (1, 2, 3)
            or set(manifest) != (base_fields
                | ({"runtime_files"} if manifest.get("format_version") in (2, 3) else set())
                | ({"history_files"} if manifest.get("format_version") == 3 else set()))
            or type(manifest["format_version"]) is not int
            or manifest["writers_stopped"] is not True
            or not isinstance(manifest["created_at"], str)
            or not isinstance(manifest["files"], list)
            or len(manifest["files"]) > MAX_FILES):
        raise SnapshotError("Unsupported manifest")
    entries = []
    for item in manifest["files"]:
        if (not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}
                or not isinstance(item["path"], str) or type(item["size"]) is not int
                or not 0 <= item["size"] <= MAX_FILE_BYTES
                or not isinstance(item["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
            raise SnapshotError("Invalid manifest file record")
        entries.append(Entry(**item))
    # Match against independently discovered allowlisted paths before using manifest paths.
    actual = inventory(source / "data", require_runtime=require_runtime, require_history=require_history)
    if entries != actual:
        raise SnapshotError("Manifest paths, completeness or hashes do not match")
    runtime_files = sorted(entry.path for entry in actual if entry.path in RUNTIME_SCHEMAS)
    history_files = sorted(entry.path for entry in actual if entry.path in HISTORY_SCHEMAS)
    if manifest["format_version"] != 3 and history_files:
        raise SnapshotError("History data requires a version three manifest")
    if manifest["format_version"] == 3 and (
            history_files != ["history.sqlite3"] or manifest["history_files"] != history_files):
        raise SnapshotError("History generation completeness does not match manifest")
    if manifest["format_version"] == 1 and runtime_files:
        raise SnapshotError("Runtime data requires a version two manifest")
    if manifest["format_version"] in (2, 3) and (not runtime_files or manifest["runtime_files"] != runtime_files):
        raise SnapshotError("Runtime generation completeness does not match manifest")
    check_sqlite(source / "data", actual)
    if inventory(source / "data") != actual:
        raise SnapshotError("Snapshot changed during verification")
    return actual


def publish(source: Path, target: Path, entries: list[Entry], *, snapshot: bool) -> None:
    # Exclusive mkdir reserves a never-existing destination; no rename/overwrite fallback.
    target.mkdir(mode=0o700)
    data = target / "data" if snapshot else target
    if snapshot:
        data.mkdir(mode=0o700)
    copy_files(source, data, entries)
    if snapshot:
        runtime_files = sorted(entry.path for entry in entries if entry.path in RUNTIME_SCHEMAS)
        history_files = sorted(entry.path for entry in entries if entry.path in HISTORY_SCHEMAS)
        manifest: dict[str, Any] = {"format_version": 3 if history_files else 2 if runtime_files else 1,
                    "created_at": datetime.now(UTC).isoformat(),
                    "writers_stopped": True, "files": [asdict(e) for e in entries]}
        if runtime_files:
            manifest["runtime_files"] = runtime_files
        if history_files:
            manifest["history_files"] = history_files
        with (target / "manifest.json").open("x", encoding="utf-8") as stream:
            os.chmod(target / "manifest.json", 0o600)
            json.dump(manifest, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    for name in ("snapshot", "verify", "restore"):
        command = commands.add_parser(name)
        command.add_argument("--source", required=True, type=Path)
        command.add_argument("--require-runtime", action="store_true",
                             help="Require all four runtime stores, including durable dispatch history")
        command.add_argument("--require-history", action="store_true",
                             help="Require history with execution journal and runtime state")
        if name != "verify":
            command.add_argument("--target", required=True, type=Path)
        if name == "snapshot":
            command.add_argument("--writers-stopped", action="store_true",
                                 help="Attest ALL writers are stopped and will remain stopped")
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        source = safe_path(args.source)
        if args.command == "verify":
            entries = verify(source, require_runtime=args.require_runtime, require_history=args.require_history)
        else:
            target = target_path(args.target, source)
            if args.command == "snapshot":
                if not args.writers_stopped:
                    raise SnapshotError("All writers must be stopped: --writers-stopped required")
                before = inventory(source, require_runtime=args.require_runtime, require_history=args.require_history)
                with tempfile.TemporaryDirectory(prefix=".trader-stage-", dir=target.parent) as raw:
                    stage = Path(raw)
                    copy_files(source, stage, before)
                    if inventory(source) != before or inventory(stage) != before:
                        raise SnapshotError("Source changed; no consistent snapshot produced")
                    check_sqlite(stage, before)
                    if inventory(source) != before:
                        raise SnapshotError("Source changed; no consistent snapshot produced")
                    publish(stage, target, before, snapshot=True)
                entries = verify(target, require_runtime=args.require_runtime, require_history=args.require_history)
            else:
                entries = verify(source, require_runtime=args.require_runtime, require_history=args.require_history)
                # Freeze the verified generation before publishing restored data.
                with tempfile.TemporaryDirectory(prefix=".trader-stage-", dir=target.parent) as raw:
                    stage = Path(raw)
                    copy_files(source / "data", stage, entries)
                    check_sqlite(stage, entries)
                    if inventory(stage) != entries:
                        raise SnapshotError("Restore copy mismatch")
                    publish(stage, target, entries, snapshot=False)
                if inventory(target) != entries:
                    raise SnapshotError("Restore copy mismatch")
        print(json.dumps({"status": "verified", "operation": args.command,
                          "files": len(entries), "bytes": sum(e.size for e in entries)}))
        return 0
    except SnapshotError as error:
        # Only our fixed diagnostics, never a raw library error or supplied path/value.
        print(f"Recovery refused: {error}", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, RecursionError):
        # Never print input strings, database payloads, credentials or raw exception details.
        print("Recovery refused: check paths, writers stopped, schema, limits and manifest integrity.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
