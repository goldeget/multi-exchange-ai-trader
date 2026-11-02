"""Canonical stdlib wire/digest implementation for sealed local generations.

The packaged source is canonical. Mechanically copy this file byte-for-byte to
scripts/operations/native_restart_format.py; the parity gate never updates it.
No SDK hydration or authenticated venue authority is established by this format.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import UUID

MAX_BYTES = 16 * 1024 * 1024
MAX_OBJECTS = 2_000
MAX_EVENTS = 100_000
DATABASE_FILES = ("execution.sqlite3", "runtime-state.sqlite3", "history.sqlite3")
NATIVE_FILE = "native-restart.json"


class NativeRestartError(ValueError):
    def __init__(self) -> None:
        super().__init__("NATIVE_RESTART_BLOCKED")


def _require(condition: bool) -> None:
    if not condition:
        raise NativeRestartError()


def _json_check(value: object, depth: int = 0) -> None:
    _require(depth <= 32)
    if type(value) is dict:
        _require(len(value) <= MAX_EVENTS)
        for key, item in value.items():
            _require(type(key) is str)
            _json_check(item, depth + 1)
    elif type(value) is list:
        _require(len(value) <= MAX_EVENTS)
        for item in value:
            _json_check(item, depth + 1)
    elif type(value) is float:
        _require(math.isfinite(value))
    elif type(value) is str:
        _require(len(value) <= MAX_BYTES)
    else:
        _require(value is None or type(value) in (str, int, bool))


def canonical_bytes(value: object) -> bytes:
    """Bounded canonical JSON used by both private files and offline backup validation."""
    try:
        _json_check(value)
        result = json.dumps(value, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=True, allow_nan=False).encode("ascii")
        _require(len(result) <= MAX_BYTES)
        return result
    except Exception:  # noqa: BLE001 - native/path/provider details must stay redacted.
        raise NativeRestartError() from None


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        _require(key not in result)
        result[key] = value
    return result


def decode_document(raw: bytes) -> dict[str, Any]:
    try:
        _require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(NativeRestartError()))
        _require(type(value) is dict)
        canonical_bytes(value)
        return dict(value)
    except Exception:  # noqa: BLE001 - native/path/provider details must stay redacted.
        raise NativeRestartError() from None


def _encoded_blob(value: object) -> None:
    _require(type(value) is str and 0 < len(value) <= MAX_BYTES)
    assert isinstance(value, str)
    try:
        raw = base64.b64decode(value, validate=True)
        _require(bool(raw) and base64.b64encode(raw).decode("ascii") == value)
    except ValueError:
        raise NativeRestartError() from None


def validate_payload_shape(payload: object) -> None:
    """Validate the inert wire schema; native event/state semantics belong to the codec."""
    _require(type(payload) is dict)
    assert isinstance(payload, dict)
    _require(set(payload) == {"schema", "encoding", "instruments", "orders", "positions"})
    _require(type(payload["schema"]) is int and payload["schema"] == 1)
    _require(payload["encoding"] == "msgpack-base64")
    total = 0
    for key in ("instruments", "orders", "positions"):
        values = payload[key]
        _require(type(values) is list and len(values) <= MAX_OBJECTS)
        if key != "instruments":
            for item in values:
                fields = {"events", "state", "position_id", "client_id"} if key == "orders" else {
                    "events", "state"}
                _require(type(item) is dict and set(item) == fields)
                _require(type(item["events"]) is list and bool(item["events"]))
                _require(type(item["state"]) is dict and bool(item["state"]))
                if key == "orders":
                    for field in ("position_id", "client_id"):
                        value = item[field]
                        _require(value is None or (type(value) is str and 0 < len(value) <= 256))
                total += len(item["events"])
                _require(total <= MAX_EVENTS)
    # Enforce total encoded size before allocating any decoded blob buffers.
    canonical_bytes(payload)
    for key in ("instruments", "orders", "positions"):
        for item in payload[key]:
            if key == "instruments":
                _encoded_blob(item)
            else:
                for event in item["events"]:
                    _encoded_blob(event)


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def validate_generation_document(document: dict[str, Any], *,
                                 identity: dict[str, object] | None = None,
                                 expected_digest: str | None = None,
                                 sealed_only: bool = True) -> None:
    """Pure validation for offline backup readers; never imports the native SDK."""
    try:
        _require(set(document) == {"schema", "state", "identity", "generation_id",
                                   "application_digest", "payload", "content_hash"})
        _require(type(document["schema"]) is int and document["schema"] == 1)
        _require(document["state"] in ({"sealed"} if sealed_only else {"sealed", "active"}))
        identifier = document["generation_id"]
        _require(type(identifier) is str and str(UUID(identifier)) == identifier)
        digest = document["application_digest"]
        _require(type(digest) is str and len(digest) == 64
                 and all(c in "0123456789abcdef" for c in digest))
        declared = document["identity"]
        _require(type(declared) is dict and set(declared) == {"scope", "native_account_id",
            "trader_id", "strategy_id", "instrument_ids", "native_version"})
        scope = declared["scope"]
        _require(type(scope) is dict and set(scope) == {"account_id", "environment"})
        _require(type(declared["instrument_ids"]) is list)
        _require(scope["environment"] == "DEMO")
        instruments = declared["instrument_ids"]
        _require(0 < len(instruments) <= MAX_OBJECTS)
        for value in (scope["account_id"], declared["native_account_id"], declared["trader_id"],
                      declared["strategy_id"], *instruments):
            _require(type(value) is str and 0 < len(value) <= 256 and value.strip() == value)
        _require(len(set(instruments)) == len(instruments))
        _require(all(value.endswith("-LINEAR.BYBIT") for value in instruments))
        _require(declared["native_account_id"].startswith("BYBIT-")
                 and declared["native_version"] == "1.231.0")
        _require(identity is None or declared == identity)
        _require(expected_digest is None or digest == expected_digest)
        validate_payload_shape(document["payload"])
        unsigned = {key: value for key, value in document.items() if key != "content_hash"}
        _require(document["content_hash"] == _digest(unsigned))

    except Exception:  # noqa: BLE001 - native/path/provider details must stay redacted.
        raise NativeRestartError() from None


def _safe_path(path: Path, *, directory: bool = False, missing: bool = False) -> None:
    """Reject reparse points/junctions, symlinks and hardlinked regular files."""
    _require(".." not in path.parts)
    for ancestor in (path, *path.parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            _require(ancestor == path and missing)
            continue
        _require(not stat.S_ISLNK(info.st_mode)
                 and not (getattr(info, "st_file_attributes", 0) & 0x400))
        if ancestor == path and not directory:
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
        else:
            _require(stat.S_ISDIR(info.st_mode))


def _cell(value: object) -> list[object]:
    if value is None:
        return ["null"]
    if type(value) is bytes:
        return ["blob", base64.b64encode(value).decode("ascii")]
    if type(value) is float:
        _require(math.isfinite(value))
        return ["real", value.hex()]
    if type(value) is int:
        return ["integer", str(value)]
    _require(type(value) is str)
    return ["text", value]


def application_digest(directory: Path) -> str:
    """WAL-aware logical schema/all-row digest, independent of SQLite page layout."""
    try:
        _safe_path(directory, directory=True)
        ledgers: dict[str, object] = {}
        remaining = MAX_BYTES
        for filename in DATABASE_FILES:
            path = directory / filename
            _safe_path(path, missing=True)
            for suffix in ("-wal", "-shm", "-journal"):
                _safe_path(directory / (filename + suffix), missing=True)
            if not path.exists():
                _require(not any((directory / (filename + suffix)).exists()
                                 for suffix in ("-wal", "-shm", "-journal")))
                ledgers[filename] = None
                continue
            with closing(sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True)) as connection:
                connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_BYTES)
                connection.execute("PRAGMA query_only=ON")
                connection.execute("PRAGMA trusted_schema=OFF")
                connection.execute("BEGIN")
                schema = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                    "ORDER BY type,name,tbl_name,sql LIMIT 2001").fetchall()
                _require(len(schema) <= MAX_OBJECTS)
                tables: dict[str, object] = {}
                content: dict[str, object] = {"schema": [list(row) for row in schema],
                    "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
                    "application_id": connection.execute("PRAGMA application_id").fetchone()[0],
                    "tables": tables}
                for kind, name, _, sql in schema:
                    if kind != "table":
                        continue
                    _require(type(sql) is str and "VIRTUAL TABLE" not in sql.upper())
                    quoted = '"' + name.replace('"', '""') + '"'
                    rows: list[str] = []
                    for row in connection.execute("SELECT * FROM " + quoted):
                        encoded = canonical_bytes([_cell(cell) for cell in row])
                        remaining -= len(encoded)
                        _require(remaining >= 0)
                        rows.append(encoded.decode("ascii"))
                    tables[name] = sorted(rows)
                ledgers[filename] = content
        return _digest(ledgers)
    except Exception:  # noqa: BLE001 - native/path/provider details must stay redacted.
        raise NativeRestartError() from None
