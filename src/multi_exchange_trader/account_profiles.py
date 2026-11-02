"""One selected DEMO account's private, immutable expectation configuration.

These records are trusted operator configuration, never binding or proof of
current authentication. This module performs no credential or network access.
"""

import json
import os
import re
import unicodedata
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import cast
from uuid import UUID, uuid4

from ._credential_files import (
    CredentialStoreError,
    create_private_parent,
    current_identity,
    private_lock,
    publish_private,
    read_checked,
)

_MAX_DISK = 65536
_MAX_REVISION = 2**63 - 1
_ADOPTED_POLICY = "uid-anchored-adoption-v1"
_SAFE_CODES = frozenset({
    "UNSAFE_PATH", "INVALID_STORE", "INVALID_VALUES", "REVISION_CONFLICT", "LOCK_TIMEOUT",
    "PERSISTENCE_FAILED", "INVALID_ARGUMENTS", "PROFILE_PENDING", "PROFILE_COMPLETE",
    "UNSUPPORTED_POLICY", "PROTECTION_UNAVAILABLE",
})


class AccountProfileError(RuntimeError):
    """Fixed safe codes, with no input, identities, paths or OS diagnostics."""

    def __init__(self, code: str) -> None:
        super().__init__(code if code in _SAFE_CODES else "INVALID_STORE")

    @property
    def code(self) -> str:
        return str(self)


@dataclass(frozen=True, slots=True)
class AccountExpectations:
    """Private tuple and explicit provenance; never current authentication."""

    profile_id: str
    revision: int
    basis: str
    source: str
    policy: str
    _uid: str = field(repr=False)
    _parent_uid: str = field(repr=False)
    _is_master: bool = field(repr=False)


@dataclass(frozen=True, slots=True)
class AccountProfileResult:
    revision: int
    profile_id: str
    state: str
    basis: str
    schema: int = field(default=1, init=False)


@dataclass(frozen=True, slots=True)
class AccountProfileSnapshot:
    revision: int
    profile_id: str
    _profile: Mapping[str, object] = field(repr=False)
    schema: int = field(default=1, init=False)

    def __post_init__(self) -> None:
        # Every nested object has fixed fields and at most one mapping level.
        frozen = {name: MappingProxyType(dict(value)) if isinstance(value, Mapping) else value
                  for name, value in self._profile.items()}
        object.__setattr__(self, "_profile", MappingProxyType(frozen))

    @property
    def _uid(self) -> str:
        return cast(str, self._profile["uid"])

    @property
    def state(self) -> str:
        return "PENDING" if self._profile["relationship"] is None else "COMPLETE"

    @property
    def basis(self) -> str:
        relation = self._profile["relationship"]
        return ("OPERATOR_SELECTED" if relation is None else
                cast(str, cast(Mapping[str, object], relation)["basis"]))

    def safe_result(self) -> AccountProfileResult:
        return AccountProfileResult(self.revision, self.profile_id, self.state, self.basis)

    def resolve_expectations(self, *, supported_policies: frozenset[str]) -> AccountExpectations:
        relation = self._profile["relationship"]
        if relation is None:
            raise AccountProfileError("PROFILE_PENDING")
        relationship = cast(Mapping[str, object], relation)
        policy = cast(str, relationship["policy"])
        if policy not in supported_policies:
            raise AccountProfileError("UNSUPPORTED_POLICY")
        return AccountExpectations(
            self.profile_id, self.revision, cast(str, relationship["basis"]),
            cast(str, relationship["source"]), policy, self._uid,
            cast(str, relationship["parent_uid"]), cast(bool, relationship["is_master"]),
        )


def _object(value: object, fields: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError
    return cast(dict[str, object], value)


def _uid(value: object, *, allow_zero: bool = False) -> str:
    if type(value) is not str or (re.fullmatch(r"[1-9][0-9]{0,19}", value) is None and
                                  not (allow_zero and value == "0")):
        raise ValueError
    return value


def _uuid(value: object) -> str:
    if type(value) is not str:
        raise ValueError
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError
    return value


def _utc(value: object) -> str:
    if type(value) is not str or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{6})?Z", value,
    ) is None:
        raise ValueError
    if datetime.fromisoformat(value).isoformat().replace("+00:00", "Z") != value:
        raise ValueError
    return value


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise ValueError


def _no_controls(value: object) -> None:
    if isinstance(value, str):
        if any(unicodedata.category(char).startswith("C") for char in value):
            raise ValueError
    elif isinstance(value, dict):
        for key, item in value.items():
            _no_controls(key)
            _no_controls(item)
    elif isinstance(value, list):
        for item in value:
            _no_controls(item)


def _decode(raw: bytes) -> AccountProfileSnapshot:
    if len(raw) > _MAX_DISK:
        raise ValueError
    data: object = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                              parse_constant=_constant)
    _no_controls(data)
    root = _object(data, {"schema", "revision", "selected_profile_id", "profiles"})
    if type(root["schema"]) is not int or root["schema"] != 1 or (
        type(root["revision"]) is not int or not 1 <= root["revision"] <= _MAX_REVISION
    ):
        raise ValueError
    selected = _uuid(root["selected_profile_id"])
    values = root["profiles"]
    if not isinstance(values, list) or len(values) != 1:
        raise ValueError
    profile = _object(values[0], {"profile_id", "exchange", "environment", "uid",
                                  "uid_evidence", "relationship", "last_observation"})
    if _uuid(profile["profile_id"]) != selected or profile["exchange"] != "BYBIT" or (
        profile["environment"] != "DEMO"
    ):
        raise ValueError
    uid = _uid(profile["uid"])
    evidence = _object(profile["uid_evidence"], {"basis", "evidence_id", "confirmed_at"})
    if evidence["basis"] != "OPERATOR_SELECTED":
        raise ValueError
    _uuid(evidence["evidence_id"])
    _utc(evidence["confirmed_at"])
    if profile["relationship"] is not None:
        relation = _object(profile["relationship"], {
            "parent_uid", "is_master", "basis", "source", "evidence_id", "confirmed_at",
            "provider_observed_at", "policy",
        })
        if _uid(relation["parent_uid"], allow_zero=True) == uid or (
            type(relation["is_master"]) is not bool
        ):
            raise ValueError
        _uuid(relation["evidence_id"])
        _utc(relation["confirmed_at"])
        if relation["basis"] == "INDEPENDENT_CONFIRMED":
            if relation["policy"] != "independent-v1" or relation["source"] not in (
                "BYBIT_UI", "BYBIT_SUPPORT", "PRIOR_VERIFIED_RECORD",
            ) or relation["provider_observed_at"] is not None:
                raise ValueError
        elif relation["basis"] == "PROVIDER_OBSERVED_OPERATOR_ADOPTED":
            if relation["policy"] != _ADOPTED_POLICY or relation["source"] != "BYBIT_DEMO_QUERY_API":
                raise ValueError
            _utc(relation["provider_observed_at"])
        else:
            raise ValueError
    if profile["last_observation"] is not None:
        observed = _object(profile["last_observation"], {
            "source", "parent_uid", "is_master", "observed_at", "server_observed_at",
        })
        if profile["relationship"] is None or observed["source"] != "BYBIT_DEMO_QUERY_API" or (
            _uid(observed["parent_uid"], allow_zero=True) == uid or
            type(observed["is_master"]) is not bool
        ):
            raise ValueError
        _utc(observed["observed_at"])
        _utc(observed["server_observed_at"])
    return AccountProfileSnapshot(root["revision"], selected, profile)


def _encode(snapshot: AccountProfileSnapshot) -> bytes:
    profile = {name: dict(value) if isinstance(value, Mapping) else value
               for name, value in snapshot._profile.items()}
    return json.dumps({"schema": 1, "revision": snapshot.revision,
                       "selected_profile_id": snapshot.profile_id, "profiles": [profile]},
                      sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class AccountProfileStore:
    """One selected private file; construction and absent reads create nothing."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def __repr__(self) -> str:
        return "AccountProfileStore(<private>)"

    def load(self) -> AccountProfileSnapshot | None:
        try:
            raw = read_checked(self.path, _MAX_DISK)
            return None if raw is None else _decode(raw)
        except CredentialStoreError as error:
            raise AccountProfileError(error.code) from None
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            raise AccountProfileError("INVALID_STORE") from None

    def register_uid(self, uid: str, *, expected_revision: int | None) -> AccountProfileResult:
        try:
            try:
                _uid(uid)
            except ValueError:
                raise AccountProfileError("INVALID_VALUES") from None
            # Registration can only create absence; updates/replacements are unsupported.
            if expected_revision is not None:
                raise AccountProfileError("REVISION_CONFLICT")
            create_private_parent(self.path)
            with private_lock(self.path, lock_name=".account-profiles.lock") as verify_lock:
                original = current_identity(self.path)
                if self.load() is not None:
                    raise AccountProfileError("REVISION_CONFLICT")
                profile_id = str(uuid4())
                snapshot = AccountProfileSnapshot(1, profile_id, {
                    "profile_id": profile_id, "exchange": "BYBIT", "environment": "DEMO",
                    "uid": uid, "uid_evidence": {"basis": "OPERATOR_SELECTED",
                        "evidence_id": str(uuid4()),
                        "confirmed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z")},
                    "relationship": None, "last_observation": None,
                })
                self._publish_and_confirm(snapshot, original, verify_lock)
                return snapshot.safe_result()
        except AccountProfileError:
            raise
        except CredentialStoreError as error:
            raise AccountProfileError(error.code) from None
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            raise AccountProfileError("PERSISTENCE_FAILED") from None

    def _publish_and_confirm(
        self, snapshot: AccountProfileSnapshot, original: tuple[int, int, int, int] | None,
        verify_lock: Callable[[], None], *, guard: Callable[[], None] = lambda: None,
        publication_section: Callable[[], AbstractContextManager[None]] = nullcontext,
    ) -> None:
        content = _encode(snapshot)
        _decode(content)
        publish_private(self.path, content, original, verify_lock, prefix=".account-profiles-",
                        identify=current_identity, before_replace=guard,
                        publication_section=publication_section)
        try:
            confirmed = self.load()
        except AccountProfileError:
            raise AccountProfileError("PERSISTENCE_FAILED") from None
        if confirmed != snapshot:
            raise AccountProfileError("PERSISTENCE_FAILED")

    def _adopt_initial(
        self, *, profile_id: str, expected_uid: str, expected_revision: int,
        relationship: Mapping[str, object], last_observation: Mapping[str, object],
        guard: Callable[[], None],
        publication_section: Callable[[], AbstractContextManager[None]] = nullcontext,
    ) -> AccountProfileResult:
        """Private issuer boundary; guard checks the same ongoing consumed operation.

        Task2 owns candidate validity/consumption. Guards must be repeatable,
        side-effect-free checks and run before lock, after lock and before replace.
        The store permits only the exact pending target's first adopted relation.
        It never accepts an existing complete relationship as an update.
        The issuer may separately hold a final publication section to order close
        against checked replacement; the repeated guard itself never publishes.
        """
        try:
            if type(expected_revision) is not int or not 1 <= expected_revision < _MAX_REVISION:
                raise AccountProfileError("REVISION_CONFLICT")
            self._check_guard(guard)
            create_private_parent(self.path)
            with private_lock(self.path, lock_name=".account-profiles.lock") as verify_lock:
                self._check_guard(guard)
                original = current_identity(self.path)
                previous = self.load()
                if previous is None or previous.revision != expected_revision or (
                    previous.profile_id != profile_id or previous._uid != expected_uid
                ):
                    raise AccountProfileError("REVISION_CONFLICT")
                if previous.state != "PENDING":
                    raise AccountProfileError("PROFILE_COMPLETE")
                profile = dict(previous._profile)
                profile.update(relationship=dict(relationship),
                               last_observation=dict(last_observation))
                snapshot = AccountProfileSnapshot(previous.revision + 1, profile_id, profile)
                try:
                    _decode(_encode(snapshot))
                    if relationship.get("basis") != "PROVIDER_OBSERVED_OPERATOR_ADOPTED" or any(
                        relationship.get(name) != last_observation.get(other) for name, other in (
                            ("parent_uid", "parent_uid"), ("is_master", "is_master"),
                            ("provider_observed_at", "observed_at"),
                        )
                    ):
                        raise ValueError
                except (ValueError, TypeError, OverflowError, RecursionError):
                    raise AccountProfileError("INVALID_VALUES") from None
                self._publish_and_confirm(snapshot, original, verify_lock,
                    guard=lambda: self._check_guard(guard), publication_section=publication_section)
                return snapshot.safe_result()
        except AccountProfileError:
            raise
        except CredentialStoreError as error:
            raise AccountProfileError(error.code) from None
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            raise AccountProfileError("PERSISTENCE_FAILED") from None

    @staticmethod
    def _check_guard(guard: Callable[[], None]) -> None:
        try:
            guard()
        except AccountProfileError:
            raise
        except Exception:  # noqa: BLE001 - issuer diagnostics must never cross this boundary.
            raise AccountProfileError("PERSISTENCE_FAILED") from None


def default_account_profile_store(environ: Mapping[str, str]) -> AccountProfileStore | None:
    """Pure selection: no stat/open/resolve, with explicit off to disable."""
    explicit = environ.get("TRADER_ACCOUNT_PROFILE_FILE")
    if explicit is not None:
        if explicit.strip().lower() == "off":
            return None
        path = Path(explicit)
    elif os.name == "nt":
        windows_base = environ.get("LOCALAPPDATA", "")
        if not windows_base:
            raise AccountProfileError("UNSAFE_PATH")
        path = Path(windows_base) / "MultiExchangeTrader" / "private" / "account-profiles.json"
    elif os.name == "posix":
        base = environ.get("XDG_CONFIG_HOME")
        if base is None:
            home = environ.get("HOME", "")
            if not home:
                raise AccountProfileError("UNSAFE_PATH")
            base = str(Path(home) / ".config")
        path = Path(base) / "multi-exchange-trader" / "private" / "account-profiles.json"
    else:
        raise AccountProfileError("PROTECTION_UNAVAILABLE")
    if not path.is_absolute() or ".." in path.parts:
        raise AccountProfileError("UNSAFE_PATH")
    return AccountProfileStore(path)
