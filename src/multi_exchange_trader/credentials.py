"""One-time private program credential registration, without activation or network I/O."""

import base64
import binascii
import json
import os
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from ._credential_files import (
    CredentialStoreError,
    Protector,
    WindowsDPAPI,
    create_private_parent,
    current_identity,
    private_lock,
    publish_private,
    read_checked,
    sync_directory,
)
from .ai.provider_config import load_ai_provider_config

__all__ = ["CredentialSnapshot", "CredentialStore", "CredentialStoreError", "RegistrationResult",
           "default_credential_store", "import_env"]

_ALLOWLIST = frozenset({
    "BYBIT_API_KEY", "BYBIT_API_SECRET", "BYBIT_DEMO_API_KEY", "BYBIT_DEMO_API_SECRET",
    "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "AI_PROVIDER",
})
_AI_KEYS = _ALLOWLIST - {
    "BYBIT_API_KEY", "BYBIT_API_SECRET", "BYBIT_DEMO_API_KEY", "BYBIT_DEMO_API_SECRET",
}
_PAIR_NAMES = {"mainnet": ("BYBIT_API_KEY", "BYBIT_API_SECRET"),
               "demo": ("BYBIT_DEMO_API_KEY", "BYBIT_DEMO_API_SECRET")}
_MAX_PLAINTEXT = 16384
_MAX_DISK = 65536
_MAX_REVISION = 2**63 - 1


def _namespaces(values: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(name for name, key in (
        ("BYBIT_LIVE", "BYBIT_API_KEY"), ("BYBIT_DEMO", "BYBIT_DEMO_API_KEY"),
        ("ANTHROPIC", "ANTHROPIC_API_KEY"), ("OPENROUTER", "OPENROUTER_API_KEY"),
    ) if key in values)


@dataclass(frozen=True, slots=True)
class CredentialSnapshot:
    """Immutable private snapshot. Generic dataclass encoding is intentionally unsupported."""

    revision: int
    _values: Mapping[str, str] = field(repr=False)
    schema: int = field(default=1, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_values", MappingProxyType(dict(self._values)))

    @property
    def namespaces(self) -> tuple[str, ...]:
        return _namespaces(self._values)

    def bybit_pair(self, environment: Literal["mainnet", "demo"]) -> tuple[str, str] | None:
        if environment not in _PAIR_NAMES:
            raise CredentialStoreError("INVALID_VALUES")
        key, secret = _PAIR_NAMES[environment]
        if key not in self._values:
            return None
        return self._values[key], self._values[secret]

    def ai_values(self) -> Mapping[str, str]:
        return MappingProxyType({name: value for name, value in self._values.items()
                                 if name in _AI_KEYS})


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    revision: int
    namespaces: tuple[str, ...]
    schema: int = field(default=1, init=False)


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError
        result[name] = value
    return result


def _json_object(raw: bytes) -> dict[str, object]:
    value: object = json.loads(raw, object_pairs_hook=_pairs)
    if not isinstance(value, dict):
        raise TypeError
    return cast(dict[str, object], value)


def _validate_values(values: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in values.items():
        if name not in _ALLOWLIST or not isinstance(value, str) or not value or (
            value != value.strip() or any(unicodedata.category(char).startswith("C")
                                         for char in value)
        ):
            raise CredentialStoreError("INVALID_VALUES")
        result[name] = value
    if len(_json_bytes(result)) > _MAX_PLAINTEXT:
        raise CredentialStoreError("INVALID_VALUES")
    for key, secret in _PAIR_NAMES.values():
        if (key in result) != (secret in result):
            raise CredentialStoreError("INVALID_VALUES")
    if "BYBIT_API_KEY" in result and "BYBIT_DEMO_API_KEY" in result and (
        result["BYBIT_API_KEY"] == result["BYBIT_DEMO_API_KEY"] or
        result["BYBIT_API_SECRET"] == result["BYBIT_DEMO_API_SECRET"]
    ):
        raise CredentialStoreError("INVALID_VALUES")
    # Both namespaces are checked even if the selected provider is the other one.
    explicit = dict(result, AI_ENV_FILE="off", ANTHROPIC_ENV_FILE="off")
    if (result.get("AI_PROVIDER", "ANTHROPIC") not in {"ANTHROPIC", "OPENROUTER"} or any(
        load_ai_provider_config(dict(explicit, AI_PROVIDER=provider)).status ==
        "INVALID_CONFIGURATION" for provider in ("ANTHROPIC", "OPENROUTER")
    )):
        raise CredentialStoreError("INVALID_VALUES")
    return result


class CredentialStore:
    """An explicit private file; construction and reads never create storage.

    ``protector`` is an explicit test seam. It writes a test-only envelope which
    the normal platform constructor refuses, regardless of environment settings.
    """

    def __init__(self, path: Path, *, protector: Protector | None = None) -> None:
        self.path = Path(path)
        self._protector = protector

    def __repr__(self) -> str:
        return "CredentialStore(<private>)"

    def _protection(self) -> str:
        if self._protector is not None:
            return "test-injected"
        if os.name == "nt":
            return "windows-dpapi-user"
        if os.name == "posix":
            return "posix-private"
        raise CredentialStoreError("PROTECTION_UNAVAILABLE")

    def _encode(self, snapshot: CredentialSnapshot) -> bytes:
        payload = _json_bytes({"schema": 1, "revision": snapshot.revision,
                               "values": dict(snapshot._values)})
        if len(payload) > _MAX_PLAINTEXT:
            raise CredentialStoreError("INVALID_VALUES")
        protection = self._protection()
        if self._protector is not None:
            payload = self._protector.protect(payload)
        elif os.name == "nt":
            payload = WindowsDPAPI().protect(payload)
        envelope = _json_bytes({"schema": 1, "protection": protection,
                                "data": base64.b64encode(payload).decode("ascii")})
        if len(envelope) > _MAX_DISK:
            raise CredentialStoreError("INVALID_VALUES")
        return envelope

    def _decode(self, raw: bytes) -> CredentialSnapshot:
        envelope = _json_object(raw)
        if (set(envelope) != {"schema", "protection", "data"} or
            type(envelope["schema"]) is not int or envelope["schema"] != 1 or
            envelope["protection"] != self._protection() or
            not isinstance(envelope["data"], str)):
            raise ValueError
        payload = base64.b64decode(envelope["data"], validate=True)
        if self._protector is not None:
            payload = self._protector.unprotect(payload)
        elif os.name == "nt":
            payload = WindowsDPAPI().unprotect(payload)
        if len(payload) > _MAX_PLAINTEXT:
            raise ValueError
        value = _json_object(payload)
        if (set(value) != {"schema", "revision", "values"} or
            type(value["schema"]) is not int or value["schema"] != 1 or
            type(value["revision"]) is not int or
            not 1 <= value["revision"] <= _MAX_REVISION or
            not isinstance(value["values"], dict)):
            raise ValueError
        values = _validate_values(cast(dict[str, str], value["values"]))
        return CredentialSnapshot(value["revision"], values)

    def load(self) -> CredentialSnapshot | None:
        try:
            raw = read_checked(self.path, _MAX_DISK)
            return None if raw is None else self._decode(raw)
        except CredentialStoreError as exc:
            if str(exc) in {"INVALID_VALUES", "PROTECTION_UNAVAILABLE"}:
                raise CredentialStoreError("INVALID_STORE") from None
            raise
        except (OSError, ValueError, TypeError, OverflowError, RecursionError, binascii.Error):
            raise CredentialStoreError("INVALID_STORE") from None

    def register(self, values: Mapping[str, str], *,
                 expected_revision: int | None) -> RegistrationResult:
        try:
            if expected_revision is not None and (type(expected_revision) is not int or
                                                  not 1 <= expected_revision < _MAX_REVISION):
                raise CredentialStoreError("REVISION_CONFLICT")
            # Validate key types/allowlist now; complete-pair validation follows merge.
            updates = dict(values)
            if any(name not in _ALLOWLIST for name in updates):
                raise CredentialStoreError("INVALID_VALUES")
            create_private_parent(self.path)
            with private_lock(self.path) as verify_lock:
                original = current_identity(self.path)
                previous = self.load()
                if (None if previous is None else previous.revision) != expected_revision:
                    raise CredentialStoreError("REVISION_CONFLICT")
                merged = {} if previous is None else dict(previous._values)
                merged.update(updates)
                validated = _validate_values(merged)
                revision = 1 if previous is None else previous.revision + 1
                snapshot = CredentialSnapshot(revision, validated)
                self._publish(self._encode(snapshot), original, verify_lock)
                confirmed = self.load()
                if confirmed != snapshot:
                    raise CredentialStoreError("PERSISTENCE_FAILED")
                return RegistrationResult(revision, snapshot.namespaces)
        except CredentialStoreError:
            raise
        except (OSError, ValueError, TypeError, OverflowError):
            raise CredentialStoreError("PERSISTENCE_FAILED") from None

    def _publish(self, content: bytes, original: tuple[int, int, int, int] | None,
                 verify_lock: Callable[[], None]) -> None:
        publish_private(self.path, content, original, verify_lock, prefix=".credentials-",
                        identify=current_identity, sync=sync_directory)


def default_credential_store(environ: Mapping[str, str]) -> CredentialStore | None:
    """Select an absolute store without probing or creating it; explicit off disables it."""
    explicit = environ.get("TRADER_CREDENTIAL_FILE")
    if explicit is not None:
        if explicit.strip().lower() == "off":
            return None
        path = Path(explicit)
    elif os.name == "nt":
        windows_base = environ.get("LOCALAPPDATA", "")
        if not windows_base:
            raise CredentialStoreError("UNSAFE_PATH")
        path = Path(windows_base) / "MultiExchangeTrader" / "private" / "credentials.bin"
    elif os.name == "posix":
        base = environ.get("XDG_CONFIG_HOME")
        if base is None:
            home = environ.get("HOME", "")
            if not home:
                raise CredentialStoreError("UNSAFE_PATH")
            base = str(Path(home) / ".config")
        path = Path(base) / "multi-exchange-trader" / "private" / "credentials.json"
    else:
        raise CredentialStoreError("PROTECTION_UNAVAILABLE")
    if not path.is_absolute() or ".." in path.parts:
        raise CredentialStoreError("UNSAFE_PATH")
    return CredentialStore(path)


def import_env(path: Path, store: CredentialStore, *,
               expected_revision: int | None) -> RegistrationResult:
    """Read only the explicitly selected source once; never expand or execute its values."""
    try:
        raw = read_checked(Path(path), _MAX_PLAINTEXT, private=False)
        if raw is None:
            raise CredentialStoreError("INVALID_IMPORT")
        content = raw.decode("utf-8-sig")
        lines = re.split(r"\r\n|\r|\n", content)
        if lines and lines[-1] == "":
            lines.pop()
        if len(lines) > 256 or any(unicodedata.category(char).startswith("C") and
                                   char not in "\r\n" for char in content):
            raise CredentialStoreError("INVALID_IMPORT")
        values: dict[str, str] = {}
        seen: set[str] = set()
        for line in lines:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if text.startswith("export "):
                text = text[7:].lstrip()
            if "=" not in text:
                raise CredentialStoreError("INVALID_IMPORT")
            name, value = text.split("=", 1)
            name = name.strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name in seen:
                raise CredentialStoreError("INVALID_IMPORT")
            seen.add(name)
            if name not in _ALLOWLIST:
                continue
            value = value.strip()
            if value.startswith(("'", '"')):
                if len(value) < 2 or value[-1] != value[0]:
                    raise CredentialStoreError("INVALID_IMPORT")
                value = value[1:-1]
            values[name] = value
        return store.register(values, expected_revision=expected_revision)
    except CredentialStoreError:
        raise
    except (OSError, ValueError, TypeError, RecursionError):
        raise CredentialStoreError("INVALID_IMPORT") from None
