"""Private clean-stop generations; hashes detect corruption, not hostile writers.

All callers must hold the scoped worker lease and quiesce writers across the three
ledgers. SQLite snapshots are individually consistent, not a cross-file transaction.
This module uses only the standard library and application scope value objects.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import sqlite3
import stat
import tempfile
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .protection import ProtectionScope

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


@dataclass(frozen=True)
class NativeRestartIdentity:
    scope: ProtectionScope
    native_account_id: str
    trader_id: str
    strategy_id: str
    instrument_ids: tuple[str, ...]
    native_version: str = "1.231.0"

    def __post_init__(self) -> None:
        _require(type(self.scope) is ProtectionScope and self.scope.environment == "DEMO")
        _require(type(self.instrument_ids) is tuple and 0 < len(self.instrument_ids) <= MAX_OBJECTS)
        for value in (self.scope.account_id, self.native_account_id, self.trader_id,
                      self.strategy_id, *self.instrument_ids):
            _require(type(value) is str and 0 < len(value) <= 256 and value.strip() == value)
        _require(len(set(self.instrument_ids)) == len(self.instrument_ids))
        _require(all(value.endswith("-LINEAR.BYBIT") for value in self.instrument_ids))
        _require(self.native_account_id.startswith("BYBIT-") and self.native_version == "1.231.0")


@dataclass(frozen=True)
class NativeRestartGeneration:
    payload: dict[str, object]
    generation_id: str
    application_digest: str


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


def validate_payload_shape(payload: object) -> None:
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
                _require(type(item) is dict and type(item.get("events")) is list)
                total += len(item["events"])
                _require(total <= MAX_EVENTS)
    canonical_bytes(payload)


def identity_document(identity: NativeRestartIdentity) -> dict[str, object]:
    _require(type(identity) is NativeRestartIdentity)
    value = asdict(identity)
    value["instrument_ids"] = list(identity.instrument_ids)
    return value


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def validate_generation_document(document: dict[str, Any], *,
                                 identity: NativeRestartIdentity | None = None,
                                 expected_digest: str | None = None,
                                 sealed_only: bool = True) -> NativeRestartGeneration:
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
        parsed = NativeRestartIdentity(ProtectionScope(**scope), declared["native_account_id"],
            declared["trader_id"], declared["strategy_id"], tuple(declared["instrument_ids"]),
            declared["native_version"])
        _require(identity is None or parsed == identity)
        _require(expected_digest is None or digest == expected_digest)
        validate_payload_shape(document["payload"])
        unsigned = {key: value for key, value in document.items() if key != "content_hash"}
        _require(document["content_hash"] == _digest(unsigned))
        return NativeRestartGeneration(document["payload"], identifier, digest)
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


class NativeRestartStore:
    """The caller's scoped OS lease serializes all load/consume/seal operations."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / NATIVE_FILE

    def _read(self) -> bytes | None:
        _safe_path(self.directory, directory=True)
        _safe_path(self.path, missing=True)
        if not self.path.exists():
            return None
        _require(self.path.stat().st_size <= MAX_BYTES)
        with self.path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        _require(len(raw) <= MAX_BYTES)
        return raw

    def _publish(self, document: dict[str, object], previous: bytes | None) -> None:
        temporary: str | None = None
        raw = canonical_bytes(document)
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".native-restart-", suffix=".tmp",
                                                       dir=self.directory)
            with os.fdopen(descriptor, "wb") as stream:
                os.chmod(temporary, 0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            _require(self._read() == previous)
            os.replace(temporary, self.path)
            temporary = None
            if os.name != "nt":
                descriptor = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except Exception:
            # Invalidate a replaced file on post-replace durability failure.
            if self._read() == raw:
                self.path.unlink()
            raise
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def load(self, identity: NativeRestartIdentity) -> NativeRestartGeneration | None:
        try:
            raw = self._read()
            if raw is None:
                return None
            return validate_generation_document(decode_document(raw), identity=identity,
                                                 expected_digest=application_digest(self.directory))
        except Exception:  # noqa: BLE001 - native/path/provider details must stay redacted.
            raise NativeRestartError() from None

    def mark_active(self, identity: NativeRestartIdentity,
                    generation: NativeRestartGeneration | None) -> None:
        try:
            previous = self._read()
            if generation is None:
                _require(previous is None)
                payload: dict[str, object] = {"schema": 1, "encoding": "msgpack-base64",
                    "instruments": [], "orders": [], "positions": []}
                document = self._document(identity, payload, "active")
            else:
                _require(previous is not None)
                assert previous is not None
                document = decode_document(previous)
                current = validate_generation_document(document, identity=identity,
                    expected_digest=application_digest(self.directory))
                _require(current == generation)
                document["state"] = "active"
                document.pop("content_hash")
                document["content_hash"] = _digest(document)
            self._publish(document, previous)
        except Exception:  # noqa: BLE001 - native/path/provider details must stay redacted.
            raise NativeRestartError() from None

    def _document(self, identity: NativeRestartIdentity, payload: dict[str, object],
                  state: str) -> dict[str, object]:
        validate_payload_shape(payload)
        document: dict[str, object] = {"schema": 1, "state": state,
            "identity": identity_document(identity), "generation_id": str(uuid4()),
            "application_digest": application_digest(self.directory), "payload": payload}
        document["content_hash"] = _digest(document)
        return document

    def seal(self, identity: NativeRestartIdentity,
             payload: dict[str, object]) -> NativeRestartGeneration:
        try:
            previous = self._read()
            if previous is not None:
                prior = decode_document(previous)
                validate_generation_document(prior, identity=identity, sealed_only=False)
                _require(prior["state"] == "active")
            document = self._document(identity, payload, "sealed")
            generation = validate_generation_document(document, identity=identity)
            self._publish(document, previous)
            return generation
        except Exception:  # noqa: BLE001 - native/path/provider details must stay redacted.
            raise NativeRestartError() from None
