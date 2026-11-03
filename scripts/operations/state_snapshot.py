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
from itertools import pairwise
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
# J creates this optional pair lazily. Its exact storage shape is separate from
# typed Risk V2 replay, which must still precede any runtime activation/recovery.
RISK_V2_TABLES = {
    "reservation_meta_v2": (
        "account TEXT NOT NULL, environment TEXT NOT NULL, schema_version INTEGER NOT NULL, "
        "capacity_revision INTEGER NOT NULL, owner_epoch TEXT NOT NULL, "
        "PRIMARY KEY(account, environment)"
    ),
    "entry_reservations_v2": (
        "account TEXT NOT NULL, environment TEXT NOT NULL, command_key TEXT NOT NULL, "
        "version INTEGER NOT NULL, instrument_venue TEXT NOT NULL, "
        "instrument_product TEXT NOT NULL, instrument_symbol TEXT NOT NULL, "
        "envelope_sha256 TEXT NOT NULL, base_quantity TEXT NOT NULL, "
        "risk_amount TEXT NOT NULL, margin_amount TEXT NOT NULL, "
        "PRIMARY KEY(account, environment, command_key)"
    ),
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
_NATIVE_VALIDATOR = runpy.run_path(str(Path(__file__).with_name("native_restart_format.py")))
NATIVE_FILE = "native-restart.json"
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
              require_history: bool = False, require_native: bool = False) -> list[Entry]:
    safe_path(root)
    files: list[Path] = []
    allowed = {name + suffix for name in ALL_SCHEMAS for suffix in ("", "-wal", "-shm")}
    allowed.add(NATIVE_FILE)
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
    if require_native and NATIVE_FILE not in present:
        raise SnapshotError("Native generation required")
    if NATIVE_FILE in present and not (set(RUNTIME_SCHEMAS) | set(HISTORY_SCHEMAS)) <= present:
        raise SnapshotError("Native requires complete runtime and history generation")
    if "history.sqlite3" in present and not {"execution.sqlite3", "runtime-state.sqlite3"} <= present:
        raise SnapshotError("History requires execution journal and normalized runtime state")
    for name in ALL_SCHEMAS:
        if name not in present and any(name + suffix in present for suffix in ("-wal", "-shm")):
            raise SnapshotError("SQLite sidecar has no corresponding database")
    result, total = [], 0
    for path in sorted(files):
        size, digest = file_hash(path)
        if path.name == NATIVE_FILE and size > _NATIVE_VALIDATOR["MAX_BYTES"]:
            raise SnapshotError("Native generation size limit exceeded")
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
                # Policy V2 is a current format of the same file/generation, not
                # a caller-selected migration or a new portable dependency.
                if name == "runtime-policy.sqlite3" and db.execute("PRAGMA user_version").fetchone()[0] == 2:
                    check_policy_v2(db)
                    continue
                optional = check_risk_v2_schema(db) if name == "execution.sqlite3" else set()
                if tables != set(schema) | optional:
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
                        for table in set(schema) | optional:
                            runtime_scopes.update(tuple(row) for row in db.execute(
                                f'SELECT DISTINCT account,environment FROM "{table}"'))
                    if name == "ai-dispatch.sqlite3":
                        check_dispatch_budget(db, scratch / "ai.sqlite3")
                if name in HISTORY_SCHEMAS:
                    history_scope = check_history(db)
        if history_scope is not None and runtime_scopes - {history_scope}:
            raise SnapshotError("History and paired runtime account scopes must match")
        if NATIVE_FILE in present:
            try:
                # Only disposable copies are opened by the WAL-aware logical digester.
                document = _NATIVE_VALIDATOR["decode_document"]((scratch / NATIVE_FILE).read_bytes())
                _NATIVE_VALIDATOR["validate_generation_document"](document,
                    expected_digest=_NATIVE_VALIDATOR["application_digest"](scratch))
                scope = document["identity"]["scope"]
                if history_scope != (scope["account_id"], scope["environment"]):
                    raise ValueError("scope")
            except (ValueError, TypeError, KeyError, RecursionError, OSError, sqlite3.Error):
                raise SnapshotError("Native sealed generation integrity or scope invalid") from None


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


# Explicit offline counterpart of the supported policy codec. Keep
# this bounded contract local so the three-file -I -S recovery bundle stays usable.
POLICY_DECIMALS = (
    "capital_usdt", "leverage_min", "leverage_max", "per_trade_loss", "max_daily_loss",
    "max_drawdown", "max_total_notional", "max_total_risk", "max_total_margin",
    "margin_allocation", "liquidation_buffer_fraction", "max_quote_age_seconds",
    "max_evidence_age_seconds",
)
POLICY_COUNTS = ("max_consecutive_losses", "max_positions")
POLICY_CHOICES = {"mode": {"FULL_AUTO", "ALL_IN_ONE"}, "environment": {"DEMO", "LIVE"},
                  "sizing_rule": {"LOSS_FIXED"}, "margin_mode": {"ISOLATED", "CROSS"},
                  "position_mode": {"ONE_WAY", "HEDGE"}}
POLICY_LISTS = ("allowed_symbols", "allowed_strategies", "selected_symbols", "selected_strategies")
POLICY_KEYS = {*POLICY_DECIMALS, *POLICY_COUNTS, *POLICY_CHOICES, *POLICY_LISTS}
POLICY_ZERO_ALLOWED = {"liquidation_buffer_fraction", "max_quote_age_seconds", "max_evidence_age_seconds"}
POLICY_TABLE_LAYOUT = "(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL," \
                      "payload TEXT NOT NULL,desired_state TEXT NOT NULL CHECK(desired_state='STOPPED'))"


def check_policy_legacy(value: object) -> None:
    if type(value) is not dict or set(value) != POLICY_KEYS:
        raise SnapshotError("Unsupported policy legacy fields")
    for key, item in value.items():
        if item is None:
            continue
        if key in POLICY_DECIMALS:
            if (type(item) is not str or re.fullmatch(r"(?:0|[1-9][0-9]{0,15})(?:\.[0-9]{1,8})?", item) is None
                    or Decimal(item) < 0 or key not in POLICY_ZERO_ALLOWED and Decimal(item) == 0):
                raise SnapshotError("Unsupported policy legacy decimal")
        elif key in POLICY_COUNTS:
            if type(item) is not int or not 1 <= item <= 1_000_000:
                raise SnapshotError("Unsupported policy legacy count")
        elif key in POLICY_CHOICES:
            if type(item) is not str or item not in POLICY_CHOICES[key]:
                raise SnapshotError("Unsupported policy legacy choice")
        elif (type(item) is not list or len(item) > 100
              or len({entry for entry in item if type(entry) is str}) != len(item)
              or any(type(entry) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}", entry) is None
                     for entry in item)):
            raise SnapshotError("Unsupported policy legacy list")
    if (value["leverage_min"] is not None and value["leverage_max"] is not None
            and Decimal(value["leverage_min"]) > Decimal(value["leverage_max"])):
        raise SnapshotError("Unsupported policy legacy leverage")
    for selected, allowed in (("selected_symbols", "allowed_symbols"), ("selected_strategies", "allowed_strategies")):
        if value[selected] and (value[allowed] is None or not set(value[selected]) <= set(value[allowed])):
            raise SnapshotError("Unsupported policy legacy selection")
    for limit in ("margin_allocation", "max_total_margin"):
        if (value[limit] is not None and value["capital_usdt"] is not None
                and Decimal(value[limit]) > Decimal(value["capital_usdt"])):
            raise SnapshotError("Unsupported policy legacy margin")
    if value["liquidation_buffer_fraction"] is not None and Decimal(value["liquidation_buffer_fraction"]) >= 1:
        raise SnapshotError("Unsupported policy legacy buffer")


# BEGIN PORTABLE POLICY SECTIONS -- identical stdlib-only counterpart in state_snapshot.
SECTION_LIMIT = 64 * 1024
SCOPE_FIELDS = {"section_version", "account", "mode", "allowed_products", "selected_product",
                "permission_profile_revision", "allowed_instruments", "selected_instruments"}
STRATEGY_FIELDS = {"section_version", "catalog", "allowed", "selected"}
RISK_DECIMALS = {"leverage_min", "leverage_max", "per_trade_loss", "max_daily_loss", "max_drawdown",
                 "max_total_risk", "max_total_margin", "margin_allocation", "liquidation_buffer_fraction",
                 "max_quote_age_seconds", "max_evidence_age_seconds"}
RISK_ZERO = {"liquidation_buffer_fraction", "max_quote_age_seconds", "max_evidence_age_seconds"}
RISK_COUNTS = {"max_consecutive_losses", "max_positions"}
RISK_CHOICES = {"sizing_rule": {"LOSS_FIXED"}, "margin_mode": {"ISOLATED", "CROSS"},
                "position_mode": {"ONE_WAY"}}
CAP_FIELDS = {"max_order_admission_notional", "max_account_admission_notional", "valuation_rule",
              "turnover_requirement", "max_execution_quote_amount"}
DESCRIPTOR_FIELDS = {"reference", "implementation_sha256", "config_sha256", "config_schema_version",
                     "engine_contract_version", "permitted_instruments", "approval_reference",
                     "approval_source_reference", "approval_source_sha256", "approval_environments", "approval_status"}


def _invalid() -> None:
    raise ValueError("POLICY_SECTIONS_INVALID")


def _object(value: Any, keys: set[str]) -> None:
    if type(value) is not dict or set(value) != keys:
        _invalid()


def _choice(value: Any, choices: set[str]) -> None:
    if type(value) is not str or value not in choices:
        _invalid()


def _identifier(value: Any) -> None:
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}", value) is None:
        _invalid()


def _array(value: Any, maximum: int = 100) -> None:
    if type(value) is not list or len(value) > maximum:
        _invalid()
    tokens = [json.dumps(item, sort_keys=True, ensure_ascii=True, allow_nan=False) for item in value]
    if len(set(tokens)) != len(tokens):
        _invalid()


def _key_wire(value: Any) -> tuple[str, str, str]:
    _object(value, {"venue", "product", "symbol"})
    _choice(value["venue"], {"BYBIT"})
    _choice(value["product"], {"SPOT", "LINEAR"})
    if type(value["symbol"]) is not str or re.fullmatch(r"[A-Z0-9]{2,48}", value["symbol"]) is None:
        _invalid()
    return value["venue"], value["product"], value["symbol"]


def _reference_wire(value: Any) -> tuple[str, str]:
    _object(value, {"strategy_id", "revision"})
    _identifier(value["strategy_id"])
    _identifier(value["revision"])
    if value["revision"].casefold() == "latest":
        _invalid()
    return value["strategy_id"], value["revision"]


def _decimal_wire(value: Any, *, zero: bool = False) -> None:
    if (type(value) is not str or re.fullmatch(r"(?:0|[1-9][0-9]{0,15})(?:\.[0-9]{1,8})?", value) is None
            or Decimal(value) < 0 or not zero and Decimal(value) == 0):
        _invalid()


def _catalog_wire(value: Any) -> dict[tuple[str, str], Any]:
    _object(value, {"schema_version", "revision", "entries"})
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        _invalid()
    if type(value["revision"]) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value["revision"]) is None:
        _invalid()
    _array(value["entries"])
    lookup: dict[tuple[str, str], Any] = {}
    canonical = []
    for entry in value["entries"]:
        _object(entry, DESCRIPTOR_FIELDS)
        reference = _reference_wire(entry["reference"])
        if reference in lookup:
            _invalid()
        lookup[reference] = entry
        for name in ("implementation_sha256", "config_sha256", "approval_source_sha256"):
            if type(entry[name]) is not str or re.fullmatch(r"[0-9a-f]{64}", entry[name]) is None:
                _invalid()
        if type(entry["config_schema_version"]) is not int or not 1 <= entry["config_schema_version"] <= 2147483647:
            _invalid()
        for name in ("engine_contract_version", "approval_reference", "approval_source_reference"):
            _identifier(entry[name])
        _array(entry["permitted_instruments"])
        for key in entry["permitted_instruments"]:
            _key_wire(key)
        _array(entry["approval_environments"], 2)
        for environment in entry["approval_environments"]:
            _choice(environment, {"DEMO", "LIVE"})
        _choice(entry["approval_status"], {"APPROVED", "DEPRECATED", "REVOKED"})
        if not entry["permitted_instruments"] or not entry["approval_environments"]:
            _invalid()
        canonical.append({**entry, "permitted_instruments": sorted(entry["permitted_instruments"], key=_key_wire),
                          "approval_environments": sorted(entry["approval_environments"])})
    payload = {"schema_version": 1, "entries": sorted(canonical, key=lambda entry: _reference_wire(entry["reference"]))}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True,
                                      separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    if value["revision"] != "sha256:" + digest:
        _invalid()
    return lookup


def _sections_wire(scope: Any, strategy: Any, risk_v2: Any) -> dict[str, Any]:
    """Local stdlib wire checks, including whole-catalog/global consistency."""
    for value, fields in ((scope, SCOPE_FIELDS), (strategy, STRATEGY_FIELDS),
                          (risk_v2, {"section_version", "product", "caps", *RISK_DECIMALS, *RISK_COUNTS, *RISK_CHOICES})):
        if value is not None:
            _object(value, fields)
            if type(value["section_version"]) is not int or value["section_version"] != 1:
                _invalid()
    if scope is not None:
        account = scope["account"]
        if account is not None:
            _object(account, {"account_id", "environment"})
            identifier = account["account_id"]
            if (type(identifier) is not str or not 1 <= len(identifier) <= 256 or identifier != identifier.strip()
                    or any(ord(char) < 32 or ord(char) == 127 for char in identifier)):
                _invalid()
            _choice(account["environment"], {"DEMO", "LIVE"})
        for name, choices in (("mode", {"FULL_AUTO", "ALL_IN_ONE"}), ("selected_product", {"SPOT", "LINEAR"}),
                              ("permission_profile_revision", {"BYBIT_SPOT_LINEAR_NO_FUNDS_V1"})):
            if scope[name] is not None:
                _choice(scope[name], choices)
        if scope["allowed_products"] is not None:
            _array(scope["allowed_products"], 2)
            for product in scope["allowed_products"]:
                _choice(product, {"SPOT", "LINEAR"})
        if scope["selected_product"] is not None and scope["selected_product"] not in (scope["allowed_products"] or []):
            _invalid()
        for name in ("allowed_instruments", "selected_instruments"):
            if scope[name] is not None:
                _array(scope[name])
                for key in scope[name]:
                    _key_wire(key)
                    if scope["selected_product"] is not None and key["product"] != scope["selected_product"]:
                        _invalid()
        if scope["selected_instruments"] and any(key not in (scope["allowed_instruments"] or []) for key in scope["selected_instruments"]):
            _invalid()
        # Mixed-product universes are invalid even before a product is selected.
        products = {key["product"] for name in ("allowed_instruments", "selected_instruments") for key in (scope[name] or [])}
        if len(products) > 1:
            _invalid()
        if scope["selected_product"] == "SPOT" and risk_v2 is not None:
            _invalid()
    if strategy is not None:
        lookup = _catalog_wire(strategy["catalog"]) if strategy["catalog"] is not None else {}
        for name in ("allowed", "selected"):
            if strategy[name] is not None:
                _array(strategy[name])
                for reference in strategy[name]:
                    _reference_wire(reference)
        for reference in strategy["allowed"] or []:
            token = _reference_wire(reference)
            if token not in lookup or lookup[token]["approval_status"] != "APPROVED":
                _invalid()
            if scope is not None and scope["account"] is not None and scope["account"]["environment"] not in lookup[token]["approval_environments"]:
                _invalid()
        if strategy["selected"] and any(reference not in (strategy["allowed"] or []) for reference in strategy["selected"]):
            _invalid()
    if risk_v2 is not None:
        _choice(risk_v2["product"], {"LINEAR"})
        for name in RISK_DECIMALS:
            if risk_v2[name] is not None:
                _decimal_wire(risk_v2[name], zero=name in RISK_ZERO)
        for name in RISK_COUNTS:
            if risk_v2[name] is not None and (type(risk_v2[name]) is not int or not 1 <= risk_v2[name] <= 1_000_000):
                _invalid()
        for name, choices in RISK_CHOICES.items():
            if risk_v2[name] is not None:
                _choice(risk_v2[name], choices)
        if risk_v2["leverage_min"] is not None and risk_v2["leverage_max"] is not None and Decimal(risk_v2["leverage_min"]) > Decimal(risk_v2["leverage_max"]):
            _invalid()
        if risk_v2["liquidation_buffer_fraction"] is not None and Decimal(risk_v2["liquidation_buffer_fraction"]) >= 1:
            _invalid()
        caps = risk_v2["caps"]
        if caps is not None:
            _object(caps, CAP_FIELDS)
            for name in ("max_order_admission_notional", "max_account_admission_notional", "max_execution_quote_amount"):
                if caps[name] is not None:
                    _decimal_wire(caps[name])
            for name, choices in (("valuation_rule", {"LINEAR_MARK_GROSS_AT_ADMISSION_V1"}),
                                  ("turnover_requirement", {"NOT_REQUIRED", "HARD_EXECUTION_QUOTE_CAP"})):
                if caps[name] is not None:
                    _choice(caps[name], choices)
            if caps["turnover_requirement"] == "NOT_REQUIRED" and caps["max_execution_quote_amount"] is not None:
                _invalid()
    result = {"scope": scope, "strategy": strategy, "risk_v2": risk_v2}
    encoded = json.dumps(result, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > SECTION_LIMIT:
        _invalid()
    return cast(dict[str, Any], json.loads(encoded))
# END PORTABLE POLICY SECTIONS

def check_policy_sections(value: object) -> None:
    """Finite offline boundary; never import the application or a fourth bundle file."""
    try:
        _object(value, {"legacy_v1", "scope", "strategy", "risk_v2"})
        assert isinstance(value, dict)
        check_policy_legacy(value["legacy_v1"])
        _sections_wire(value["scope"], value["strategy"], value["risk_v2"])
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > SECTION_LIMIT:
            _invalid()
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise SnapshotError("Unsupported policy V2 sections") from None


def policy_row(db: sqlite3.Connection, table: str) -> tuple[int, int, str, str]:
    expected = [(0, "id", "INTEGER", 0, None, 1, 0), (1, "revision", "INTEGER", 1, None, 0, 0),
                (2, "payload", "TEXT", 1, None, 0, 0), (3, "desired_state", "TEXT", 1, None, 0, 0)]
    sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if (sql is None or " ".join(sql[0].split()) != f"CREATE TABLE {table} {POLICY_TABLE_LAYOUT}"
            or db.execute(f'PRAGMA table_xinfo("{table}")').fetchall() != expected
            or db.execute(f'PRAGMA foreign_key_list("{table}")').fetchone() is not None
            or db.execute(f'PRAGMA index_list("{table}")').fetchone() is not None):
        raise SnapshotError("Unsupported policy table layout")
    rows = db.execute(f'SELECT * FROM "{table}"').fetchmany(2)
    if (len(rows) != 1 or type(rows[0][0]) is not int or rows[0][0] != 1
            or type(rows[0][1]) is not int or not 0 <= rows[0][1] <= 2**53 - 1
            or type(rows[0][2]) is not str or rows[0][3] != "STOPPED"):
        raise SnapshotError("Unsupported policy stopped singleton")
    return cast(tuple[int, int, str, str], tuple(rows[0]))


def check_policy_v2(db: sqlite3.Connection) -> None:
    if db.execute("PRAGMA user_version").fetchone()[0] != 2:
        raise SnapshotError("Unsupported policy version")
    objects = db.execute("SELECT type,name,sql FROM sqlite_master").fetchall()
    tables = {name for kind, name, _sql in objects if kind == "table"}
    historical = {"runtime_policy", "runtime_policy_v1_archive"}
    if tables not in ({"runtime_policy_v2"}, {"runtime_policy_v2"} | historical):
        raise SnapshotError("Unsupported policy table set")
    fences = {f"{table}_immutable_{action.lower()}":
              f"CREATE TRIGGER {table}_immutable_{action.lower()} BEFORE {action} ON {table} "
              "BEGIN SELECT RAISE(ABORT,'policy immutable'); END"
              for table in historical for action in ("INSERT", "UPDATE", "DELETE")} if tables & historical else {}
    if (any(kind not in {"table", "trigger"} for kind, _name, _sql in objects)
            or {name: sql for kind, name, sql in objects if kind == "trigger"} != fences):
        raise SnapshotError("Unsupported policy immutable objects")
    row = policy_row(db, "runtime_policy_v2")
    if len(row[2].encode("utf-8")) > SECTION_LIMIT:
        raise SnapshotError("Unsupported policy V2 size")
    value = json_payload(row[2])
    check_policy_sections(value)
    if tables & historical:
        original = policy_row(db, "runtime_policy")
        archive = policy_row(db, "runtime_policy_v1_archive")
        if original != archive or row[1] <= original[1]:
            raise SnapshotError("Unsupported policy archive integrity")
        check_policy_legacy(json_payload(original[2]))


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


def _v2_require(condition: object) -> None:
    if not condition:
        raise SnapshotError("Unsupported Risk V2 storage commitment")


def _v2_object(value: Any, keys: set[str] | None = None) -> dict[str, Any]:
    _v2_require(type(value) is dict and (keys is None or set(value) == keys))
    return cast(dict[str, Any], value)


def _v2_id(value: Any) -> None:
    _v2_require(type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}", value))


def _v2_hash(value: Any) -> None:
    _v2_require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value))


def _v2_count(value: Any, *, version: bool = False) -> None:
    _v2_require(type(value) is int and (value == 2 if version else 0 <= value <= 2**63 - 1))


def _v2_account(account: Any, environment: Any) -> None:
    _v2_require(type(account) is str and 1 <= len(account) <= 256 and account == account.strip()
                and not any(ord(char) < 32 or ord(char) == 127 for char in account))
    _v2_require(type(environment) is str and environment in {"DEMO", "LIVE"})


def _v2_canonical(value: object) -> bytes:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                         allow_nan=False).encode("ascii")
    _v2_require(len(encoded) <= 65536)
    return encoded


def _v2_decimal(value: Any, *, source: bool = False, positive: bool = False) -> None:
    _v2_require(type(value) is str and len(value) <= 65536
                and re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:E[+-][0-9]+)?", value))
    mantissa, marker, token = value.partition("E")
    _v2_require(not marker or len(token) <= 7)
    exponent = int(token) if marker else 0
    coefficient = mantissa.lstrip("-").replace(".", "")
    digits = len(coefficient.lstrip("0")) or 1
    if "." in mantissa:
        exponent -= len(mantissa) - mantissa.index(".") - 1
    _v2_require(digits <= 65536 and abs(exponent) <= 65536)
    if source:
        _v2_require(digits <= 256 and abs(exponent) <= 1000 and abs(exponent + digits - 1) <= 1000)
    number = Decimal(value)
    _v2_require(number.is_finite() and (number > 0 if positive else number >= 0))
    parts = number.as_tuple()
    exponent = cast(int, parts.exponent)
    text = "".join(chr(48 + digit) for digit in parts.digits)
    adjusted, point = exponent + len(parts.digits) - 1, len(parts.digits) + exponent
    if exponent <= 0 and adjusted >= -6:
        if point <= 0:
            text = "0." + "0" * (-point) + text
        elif point < len(parts.digits):
            text = text[:point] + "." + text[point:]
    else:
        text = text[0] + ("." + text[1:] if len(text) > 1 else "")
        text += "E" + ("+" if adjusted >= 0 else "-") + str(abs(adjusted))
    _v2_require(("-" if parts.sign else "") + text == value)


def _v2_time(value: Any) -> None:
    _v2_require(type(value) is str and re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}\+00:00", value))
    _v2_require(datetime.fromisoformat(value).isoformat(timespec="microseconds") == value)


def _v2_strategy(value: Any) -> None:
    strategy = _v2_object(value, {"strategy_id", "revision"})
    for item in strategy.values():
        _v2_id(item)


def _v2_key(value: Any) -> None:
    key = _v2_object(value, {"venue", "product", "symbol"})
    _v2_require(type(key["venue"]) is str and key["venue"] == "BYBIT"
                and type(key["product"]) is str and key["product"] == "LINEAR"
                and type(key["symbol"]) is str and re.fullmatch(r"[A-Z0-9]{2,48}", key["symbol"]))


def _v2_scope(value: Any) -> None:
    scope = _v2_object(value, {"product_scope", "instrument", "strategy"})
    product = _v2_object(scope["product_scope"], {"account", "product"})
    account = _v2_object(product["account"], {"account_id", "environment"})
    _v2_account(account["account_id"], account["environment"])
    _v2_require(type(product["product"]) is str and product["product"] == "LINEAR")
    _v2_key(scope["instrument"])
    _v2_strategy(scope["strategy"])


def _v2_pin(value: Any) -> None:
    pin = _v2_object(value, {"key", "native_instrument_id", "catalog_revision", "specification_revision",
        "parser_revision", "source_rule_revision", "source_id", "source_environment", "response_time",
        "received_at", "raw_row_sha256"})
    _v2_key(pin["key"])
    _v2_require(type(pin["native_instrument_id"]) is str
                and pin["native_instrument_id"] == pin["key"]["symbol"] + "-LINEAR.BYBIT")
    for name in ("catalog_revision", "parser_revision", "source_rule_revision", "source_id"):
        _v2_id(pin[name])
    for name in ("specification_revision", "raw_row_sha256"):
        _v2_hash(pin[name])
    _v2_require(type(pin["source_environment"]) is str
                and pin["source_environment"] in {"PUBLIC_MAINNET", "PUBLIC_TESTNET", "FIXTURE"})
    for name in ("response_time", "received_at"):
        _v2_time(pin[name])


def _v2_frame(raw: Any) -> dict[str, Any]:
    _v2_require(type(raw) is str and 0 < len(raw) <= 65536)
    encoded = raw.encode("utf-8", errors="strict")
    _v2_require(len(encoded) <= 65536)
    # Bound containers before json.loads allocates them; quotes/escapes are data.
    depth, quoted, escaped = 0, False, False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            _v2_require(depth <= 32)
        elif char in "]}":
            depth -= 1

    def integer(token: str) -> int:
        _v2_require(len(token) - int(token.startswith("-")) <= 19)
        return int(token)

    def no_number(_token: str) -> Any:
        raise SnapshotError("Unsupported Risk V2 storage number")

    frame = _v2_object(json.loads(raw, object_pairs_hook=unique_object, parse_int=integer,
                                  parse_float=no_number, parse_constant=no_number),
                       {"schema", "wire_version", "record_type", "fields", "content_sha256"})
    def tree(value: Any, level: int = 1) -> None:
        _v2_require(level <= 32)
        if type(value) is dict:
            for name, child in value.items():
                _v2_require(type(name) is str)
                tree(child, level + 1)
        elif type(value) is list:
            for child in value:
                tree(child, level + 1)
        else:
            _v2_require(value is None or type(value) in (str, bool, int))

    tree(frame)
    _v2_require(frame["schema"] == "RISK_V2_DURABLE_INTENT"
                and frame["record_type"] == "DurableEntryIntentV2")
    _v2_count(frame["wire_version"], version=True)
    _v2_hash(frame["content_sha256"])
    _v2_require(_v2_canonical(frame) == encoded)
    unsigned = {name: value for name, value in frame.items() if name != "content_sha256"}
    _v2_require(hashlib.sha256(_v2_canonical(unsigned)).hexdigest() == frame["content_sha256"])
    return frame


def check_risk_v2_schema(db: sqlite3.Connection) -> set[str]:
    found = db.execute("SELECT name,type,sql FROM sqlite_master WHERE name IN (?,?)",
                       tuple(RISK_V2_TABLES)).fetchall()
    if not found:
        return set()
    _v2_require(len(found) == 2)
    for table, kind, sql in found:
        expected = f"CREATE TABLE {table} ({RISK_V2_TABLES[table]})"
        _v2_require(kind == "table" and type(sql) is str
                    and "".join(sql.split()) == "".join(expected.split()))
        columns = RISK_V2_TABLES[table].split(", PRIMARY KEY")[0].split(", ")
        key = ["account", "environment"] + (["command_key"] if table == "entry_reservations_v2" else [])
        expected_columns = [(i, field.split()[0], field.split()[1], 1, None,
                             key.index(field.split()[0]) + 1 if field.split()[0] in key else 0, 0)
                            for i, field in enumerate(columns)]
        _v2_require([tuple(row) for row in db.execute(f'PRAGMA table_xinfo("{table}")')] == expected_columns)
        _v2_require(not db.execute(f'PRAGMA foreign_key_list("{table}")').fetchall())
        indexes = db.execute(f'PRAGMA index_list("{table}")').fetchall()
        _v2_require(len(indexes) == 1 and tuple(indexes[0][2:]) == (1, "pk", 0))
        index = indexes[0][1]
        _v2_require(index == f"sqlite_autoindex_{table}_1")
        parts = db.execute('SELECT cid,name,desc,coll FROM pragma_index_xinfo(?) WHERE "key"=1',
                           (index,)).fetchall()
        _v2_require([tuple(row) for row in parts] == [(i, name, 0, "BINARY") for i, name in enumerate(key)])
    return set(RISK_V2_TABLES)


def _v2_command(command: sqlite3.Row, reservation: sqlite3.Row) -> None:
    _v2_require(command["kind"] == "ENTRY" and type(command["terminal"]) is int
                and command["terminal"] in (0, 1)
                and all(command[name] is None for name in ("risk", "notional", "margin"))
                and all(type(command[name]) is str for name in
                        ("account", "environment", "command_key", "client_id", "payload", "payload_hash", "state"))
                and command["state"] in {"PENDING", "SENDING", "ACKNOWLEDGED", "UNKNOWN", "REJECTED"})
    frame = _v2_frame(command["payload"])
    _v2_hash(command["payload_hash"])
    _v2_require(hashlib.sha256(command["payload"].encode("utf-8")).hexdigest() == command["payload_hash"])
    fields = _v2_object(frame["fields"], {"schema_version", "command_key", "position_id", "plan_id",
        "strategy", "envelope", "assessment", "protection_policy", "source_record_sha256s"})
    _v2_count(fields["schema_version"], version=True)
    for name in ("command_key", "position_id", "plan_id"):
        _v2_id(fields[name])
    _v2_require(fields["command_key"] == command["command_key"])
    _v2_strategy(fields["strategy"])
    hashes = fields["source_record_sha256s"]
    _v2_require(type(hashes) is list and 1 <= len(hashes) <= 1024)
    for value in hashes:
        _v2_hash(value)
    _v2_require(all(left < right for left, right in pairwise(hashes)))
    protection = _v2_object(fields["protection_policy"],
                           {"revision", "generation", "stop_price", "take_profit_price", "trigger_source"})
    for name in ("revision", "trigger_source"):
        _v2_id(protection[name])
    _v2_count(protection["generation"])
    for name in ("stop_price", "take_profit_price"):
        _v2_decimal(protection[name], source=True, positive=True)
    envelope = _v2_object(fields["envelope"], {"schema_version", "scope", "instrument_pin",
        "policy_revision", "policy_sha256", "side", "base_quantity", "limit_price", "order_type",
        "time_in_force", "role", "reduce_only", "origin_quote_id", "valid_until"})
    _v2_count(envelope["schema_version"], version=True)
    _v2_scope(envelope["scope"])
    scope = envelope["scope"]
    account = scope["product_scope"]["account"]
    _v2_require(account == {"account_id": command["account"], "environment": command["environment"]}
                and scope["strategy"] == fields["strategy"])
    _v2_pin(envelope["instrument_pin"])
    _v2_require(envelope["instrument_pin"]["key"] == scope["instrument"])
    for name in ("policy_revision", "origin_quote_id"):
        _v2_id(envelope[name])
    _v2_hash(envelope["policy_sha256"])
    _v2_require(envelope["side"] in ("BUY", "SELL") and envelope["order_type"] == "LIMIT"
                and envelope["time_in_force"] == "IOC" and envelope["role"] == "ENTRY"
                and envelope["reduce_only"] is False)
    _v2_decimal(envelope["base_quantity"], positive=True)
    _v2_decimal(envelope["limit_price"], source=True, positive=True)
    _v2_time(envelope["valid_until"])
    # codec._validated_fields writes this exact contract-owned raw envelope tree.
    commitment = hashlib.sha256(_v2_canonical({"schema": "RISK_V2_CANONICAL_V1",
        "record_type": "FixedLimitEnvelopeV2", "fields": envelope})).hexdigest()
    assessment = _v2_object(fields["assessment"])
    preparation = _v2_object(assessment["preparation"])
    sizing, binding, minimum, valuation, tier = (
        _v2_object(preparation[name]) for name in
        ("sizing", "binding", "minimum_amount", "valuation", "tier_bound"))
    for part in (sizing, binding, minimum, valuation):
        _v2_scope(part["scope"])
        _v2_require(part["scope"] == scope)
    _v2_key(tier["instrument"])
    _v2_pin(binding["instrument_pin"])
    _v2_require(tier["instrument"] == scope["instrument"]
                and binding["instrument_pin"] == envelope["instrument_pin"]
                and _v2_canonical(sizing["envelope"]) == _v2_canonical(envelope)
                and binding["envelope_sha256"] == minimum["envelope_sha256"] == commitment
                and sizing["policy_sha256"] == binding["policy_sha256"] == envelope["policy_sha256"]
                and protection["revision"] == envelope["policy_revision"]
                and protection["stop_price"] == sizing["stop_price"])
    _v2_decimal(sizing["estimated_loss_quote"])
    _v2_decimal(assessment["required_reservation_quote"])
    instrument = {"venue": reservation["instrument_venue"], "product": reservation["instrument_product"],
                  "symbol": reservation["instrument_symbol"]}
    _v2_key(instrument)
    _v2_hash(reservation["envelope_sha256"])
    for name in ("base_quantity", "risk_amount", "margin_amount"):
        _v2_decimal(reservation[name], positive=name == "base_quantity")
    _v2_require(instrument == scope["instrument"] and reservation["envelope_sha256"] == commitment
                and reservation["base_quantity"] == envelope["base_quantity"]
                and reservation["risk_amount"] == sizing["estimated_loss_quote"]
                and reservation["margin_amount"] == assessment["required_reservation_quote"])
    client = "ec_" + hashlib.sha256(_v2_canonical(
        [command["account"], command["environment"], command["command_key"]])).hexdigest()[:32]
    _v2_require(command["client_id"] == client)
    dto = {"schema_version": 2, "instrument": instrument, "envelope_sha256": commitment,
           **{name: reservation[name] for name in ("base_quantity", "risk_amount", "margin_amount")}}
    _v2_canonical(dto)
    _v2_canonical({"schema": "RISK_V2_CANONICAL_V1", "record_type": "LocalEntryCommandIdentityV2",
        "fields": {**account, "command_key": command["command_key"], "kind": "ENTRY",
                   "intent_sha256": command["payload_hash"], "reservation": dto}})


def check_risk_v2(db: sqlite3.Connection) -> None:
    """Finite all-row storage checks, deliberately not the 24-context economic codec."""
    db.row_factory = sqlite3.Row
    present = bool(check_risk_v2_schema(db))
    if present:
        for meta in db.execute("SELECT * FROM reservation_meta_v2"):
            _v2_account(meta["account"], meta["environment"])
            _v2_count(meta["schema_version"], version=True)
            _v2_count(meta["capacity_revision"])
            _v2_id(meta["owner_epoch"])
        for row in db.execute("SELECT * FROM entry_reservations_v2"):
            _v2_require(all(type(row[name]) is str for name in tuple(row.keys()) if name != "version"))
            _v2_account(row["account"], row["environment"])
            _v2_id(row["command_key"])
            _v2_count(row["version"], version=True)
            key = tuple(row[name] for name in ("account", "environment", "command_key"))
            command = db.execute("SELECT * FROM commands WHERE account=? AND environment=? AND command_key=?", key).fetchone()
            meta = db.execute("SELECT 1 FROM reservation_meta_v2 WHERE account=? AND environment=?", key[:2]).fetchone()
            _v2_require(command is not None and meta is not None)
            _v2_command(command, row)
    for row in db.execute("SELECT account,environment,command_key,payload FROM commands WHERE kind='ENTRY'"):
        # The reserved discriminator cannot fall back to V1, regardless of version.
        value = json_payload(row["payload"])
        if type(value) is dict and value.get("schema") == "RISK_V2_DURABLE_INTENT":
            _v2_require(present and db.execute(
                "SELECT 1 FROM entry_reservations_v2 WHERE account=? AND environment=? AND command_key=?",
                (row["account"], row["environment"], row["command_key"])).fetchone() is not None)


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
    if name == "execution.sqlite3":
        check_risk_v2(db)
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
           require_history: bool = False, require_native: bool = False) -> list[Entry]:
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
            or manifest.get("format_version") not in (1, 2, 3, 4)
            or set(manifest) != (base_fields
                | ({"runtime_files"} if manifest.get("format_version") in (2, 3, 4) else set())
                | ({"history_files"} if manifest.get("format_version") in (3, 4) else set())
                | ({"native_files"} if manifest.get("format_version") == 4 else set()))
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
    actual = inventory(source / "data", require_runtime=require_runtime,
        require_history=require_history, require_native=require_native)
    if entries != actual:
        raise SnapshotError("Manifest paths, completeness or hashes do not match")
    runtime_files = sorted(entry.path for entry in actual if entry.path in RUNTIME_SCHEMAS)
    history_files = sorted(entry.path for entry in actual if entry.path in HISTORY_SCHEMAS)
    native_files = sorted(entry.path for entry in actual if entry.path == NATIVE_FILE)
    if manifest["format_version"] != 4 and native_files:
        raise SnapshotError("Native data requires a version four manifest")
    if manifest["format_version"] == 4 and (
            native_files != [NATIVE_FILE] or manifest["native_files"] != native_files
            or runtime_files != sorted(RUNTIME_SCHEMAS)):
        raise SnapshotError("Native generation completeness does not match manifest")
    if manifest["format_version"] not in (3, 4) and history_files:
        raise SnapshotError("History data requires a version three manifest")
    if manifest["format_version"] in (3, 4) and (
            history_files != ["history.sqlite3"] or manifest["history_files"] != history_files):
        raise SnapshotError("History generation completeness does not match manifest")
    if manifest["format_version"] == 1 and runtime_files:
        raise SnapshotError("Runtime data requires a version two manifest")
    if manifest["format_version"] in (2, 3, 4) and (not runtime_files or manifest["runtime_files"] != runtime_files):
        raise SnapshotError("Runtime generation completeness does not match manifest")
    check_sqlite(source / "data", actual)
    if inventory(source / "data", require_native=require_native) != actual:
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
        native_files = sorted(entry.path for entry in entries if entry.path == NATIVE_FILE)
        manifest: dict[str, Any] = {"format_version": 4 if native_files else 3 if history_files else 2 if runtime_files else 1,
                    "created_at": datetime.now(UTC).isoformat(),
                    "writers_stopped": True, "files": [asdict(e) for e in entries]}
        if runtime_files:
            manifest["runtime_files"] = runtime_files
        if history_files:
            manifest["history_files"] = history_files
        if native_files:
            manifest["native_files"] = native_files
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
        command.add_argument("--require-native", action="store_true",
                             help="Require a sealed native file and complete runtime/history generation")
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
            entries = verify(source, require_runtime=args.require_runtime,
                require_history=args.require_history, require_native=args.require_native)
        else:
            target = target_path(args.target, source)
            if args.command == "snapshot":
                if not args.writers_stopped:
                    raise SnapshotError("All writers must be stopped: --writers-stopped required")
                before = inventory(source, require_runtime=args.require_runtime,
                    require_history=args.require_history, require_native=args.require_native)
                with tempfile.TemporaryDirectory(prefix=".trader-stage-", dir=target.parent) as raw:
                    stage = Path(raw)
                    copy_files(source, stage, before)
                    if (inventory(source, require_native=args.require_native) != before
                            or inventory(stage, require_native=args.require_native) != before):
                        raise SnapshotError("Source changed; no consistent snapshot produced")
                    check_sqlite(stage, before)
                    if inventory(source, require_native=args.require_native) != before:
                        raise SnapshotError("Source changed; no consistent snapshot produced")
                    publish(stage, target, before, snapshot=True)
                entries = verify(target, require_runtime=args.require_runtime,
                    require_history=args.require_history, require_native=args.require_native)
            else:
                entries = verify(source, require_runtime=args.require_runtime,
                    require_history=args.require_history, require_native=args.require_native)
                # Freeze the verified generation before publishing restored data.
                with tempfile.TemporaryDirectory(prefix=".trader-stage-", dir=target.parent) as raw:
                    stage = Path(raw)
                    copy_files(source / "data", stage, entries)
                    check_sqlite(stage, entries)
                    if inventory(stage, require_native=args.require_native) != entries:
                        raise SnapshotError("Restore copy mismatch")
                    publish(stage, target, entries, snapshot=False)
                if inventory(target, require_native=args.require_native) != entries:
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
