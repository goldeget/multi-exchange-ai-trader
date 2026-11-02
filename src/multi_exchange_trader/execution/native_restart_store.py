"""Private clean-stop generations; hashes detect corruption, not hostile writers.

All callers must hold the scoped worker lease and quiesce writers across the three
ledgers. SQLite snapshots are individually consistent, not a cross-file transaction.
This module uses only the standard library and application scope value objects.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .native_restart_format import (
    DATABASE_FILES,
    MAX_BYTES,
    MAX_EVENTS,
    MAX_OBJECTS,
    NATIVE_FILE,
    NativeRestartError,
    _digest,
    _require,
    _safe_path,
    application_digest,
    canonical_bytes,
    decode_document,
    validate_payload_shape,
)
from .native_restart_format import (
    validate_generation_document as _validate_generation_document,
)
from .protection import ProtectionScope

__all__ = [
    "DATABASE_FILES",
    "MAX_BYTES",
    "MAX_EVENTS",
    "MAX_OBJECTS",
    "NATIVE_FILE",
    "NativeRestartError",
    "NativeRestartGeneration",
    "NativeRestartIdentity",
    "NativeRestartStore",
    "_require",
    "application_digest",
    "canonical_bytes",
    "decode_document",
    "identity_document",
    "validate_generation_document",
    "validate_payload_shape",
]


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


def identity_document(identity: NativeRestartIdentity) -> dict[str, object]:
    _require(type(identity) is NativeRestartIdentity)
    value = asdict(identity)
    value["instrument_ids"] = list(identity.instrument_ids)
    return value


def validate_generation_document(document: dict[str, Any], *,
                                 identity: NativeRestartIdentity | None = None,
                                 expected_digest: str | None = None,
                                 sealed_only: bool = True) -> NativeRestartGeneration:
    """Application type adapter for the shared inert wire validator."""
    _validate_generation_document(document,
        identity=None if identity is None else identity_document(identity),
        expected_digest=expected_digest, sealed_only=sealed_only)
    return NativeRestartGeneration(document["payload"], document["generation_id"],
                                   document["application_digest"])


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
