"""Revisioned operator drafts. Saving settings never grants execution authority."""

import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field, fields
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Any, cast

from .policy_sections import (
    RISK_DECIMALS,
    catalog_from_wire,
    eligible_references,
    section_missing_fields,
    validate_sections_v2,
)
from .risk import RiskScope, UserRiskPolicy
from .risk_v2_contracts import (
    LinearCapitalCapsV2,
    LinearRiskPolicyV2,
    RiskScopeV2,
    RiskV2Rejected,
    risk_policy_sha256_v2,
)
from .strategy_catalog import ApprovalEnvironment, require_strategy_references
from .strategy_publication_v1 import _publication_policy_v1, _publication_read_v1

DECIMALS = (
    "capital_usdt", "leverage_min", "leverage_max", "per_trade_loss", "max_daily_loss",
    "max_drawdown", "max_total_notional", "max_total_risk", "max_total_margin",
    "margin_allocation", "liquidation_buffer_fraction", "max_quote_age_seconds",
    "max_evidence_age_seconds",
)
COUNTS = ("max_consecutive_losses", "max_positions")
CHOICES = {"mode": {"FULL_AUTO", "ALL_IN_ONE"}, "environment": {"DEMO", "LIVE"},
           "sizing_rule": {"LOSS_FIXED"}, "margin_mode": {"ISOLATED", "CROSS"},
           "position_mode": {"ONE_WAY", "HEDGE"}}
LISTS = ("allowed_symbols", "allowed_strategies", "selected_symbols", "selected_strategies")
KEYS = (*DECIMALS, *COUNTS, *CHOICES, *LISTS)
ZERO_ALLOWED = {"liquidation_buffer_fraction", "max_quote_age_seconds",
                "max_evidence_age_seconds"}


def parse_json(raw: bytes | str) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate policy field")
            result[key] = value
        return result

    def constant(_: str) -> object:
        raise ValueError("nonfinite policy field")

    try:
        return cast(object, json.loads(raw, object_pairs_hook=pairs, parse_constant=constant))
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("invalid policy JSON") from exc


def validate_draft(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(KEYS):
        raise ValueError("policy fields")
    for key in KEYS:
        item = value[key]
        if item is None:
            continue
        if key in DECIMALS:
            if (type(item) is not str or not re.fullmatch(r"(?:0|[1-9][0-9]{0,15})(?:\.[0-9]{1,8})?", item)
                    or Decimal(item) < 0 or key not in ZERO_ALLOWED and Decimal(item) == 0):
                raise ValueError("policy decimal")
        elif key in COUNTS:
            if type(item) is not int or not 1 <= item <= 1_000_000:
                raise ValueError("policy count")
        elif key in CHOICES:
            if type(item) is not str or item not in CHOICES[key]:
                raise ValueError("policy choice")
        elif (type(item) is not list or len(item) > 100 or len({
                x for x in item if type(x) is str}) != len(item)
              or any(type(x) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}", x)
                     is None for x in item)):
            raise ValueError("policy allowlist")
    if (value["leverage_min"] is not None and value["leverage_max"] is not None
            and Decimal(value["leverage_min"]) > Decimal(value["leverage_max"])):
        raise ValueError("policy leverage range")
    for selected, allowed in (("selected_symbols", "allowed_symbols"),
                               ("selected_strategies", "allowed_strategies")):
        if value[selected] and (value[allowed] is None or not set(value[selected]) <= set(value[allowed])):
            raise ValueError("selection outside allowlist")
    for limit in ("margin_allocation", "max_total_margin"):
        if (value[limit] is not None and value["capital_usdt"] is not None
                and Decimal(value[limit]) > Decimal(value["capital_usdt"])):
            raise ValueError("margin exceeds allocated capital")
    if (value["liquidation_buffer_fraction"] is not None
            and Decimal(value["liquidation_buffer_fraction"]) >= 1):
        raise ValueError("liquidation buffer fraction")
    return cast(dict[str, Any], json.loads(json.dumps(value)))


def missing_fields(draft: dict[str, Any]) -> list[str]:
    required = set(KEYS) - {"selected_symbols", "selected_strategies"}
    if draft["mode"] == "FULL_AUTO":
        required.update(("selected_symbols", "selected_strategies"))
    return sorted(key for key in required if draft[key] is None or draft[key] == [])


MAX_REVISION = 2**53 - 1
V2_SECTIONS = {"legacy_v1", "scope", "strategy", "risk_v2"}
TABLE_LAYOUT = "(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL," \
               "payload TEXT NOT NULL,desired_state TEXT NOT NULL CHECK(desired_state='STOPPED'))"


def validate_revision(value: object, *, increment: bool = False) -> int:
    if type(value) is not int or not 0 <= value <= MAX_REVISION - int(increment):
        raise ValueError("policy revision")
    return value


def validate_v2(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != V2_SECTIONS:
        raise ValueError("policy v2 fields")
    result = {"legacy_v1": validate_draft(value["legacy_v1"]),
              **validate_sections_v2(value["scope"], value["strategy"], value["risk_v2"])}
    _encoded_policy(result)
    return result


def _encoded_policy(policy: dict[str, Any]) -> str:
    payload = json.dumps(policy, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    if len(payload.encode("utf-8")) > 64 * 1024:
        raise ValueError("policy body limit")
    return payload


def _parse_v2(raw: str) -> object:
    if len(raw.encode("utf-8")) > 64 * 1024:
        raise ValueError("policy body limit")
    return parse_json(raw)


def _fences() -> dict[str, str]:
    return {f"{table}_immutable_{action.lower()}":
            f"CREATE TRIGGER {table}_immutable_{action.lower()} BEFORE {action} ON {table} "
            "BEGIN SELECT RAISE(ABORT,'policy immutable'); END"
            for table in ("runtime_policy", "runtime_policy_v1_archive")
            for action in ("INSERT", "UPDATE", "DELETE")}


_SHARED_POLICY_TARGET_V2 = Path("/etc/trader/policy-target.json")


class _PolicyIdentityErrorV2(ValueError):
    """A configured original target no longer has its bound identity."""


@dataclass(frozen=True)
class _PolicyTargetBindingV2:
    book: "RuntimePolicyBook"
    original_path: Path
    config_path: Path
    config_bytes: bytes
    config_sha256: str
    config_identity: tuple[int, int]
    db_identity: tuple[int, int]


@dataclass(frozen=True)
class _PolicyAdmissionDataV2:
    book: "RuntimePolicyBook"
    binding: _PolicyTargetBindingV2
    revision: int
    policy: LinearRiskPolicyV2
    policy_sha256: str


@dataclass
class _PolicyResourcesV2:
    db: sqlite3.Connection | None = None
    descriptors: list[int] = field(default_factory=list)


def _file_identity_v2(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _protected_policy_file_v2(info: os.stat_result, *, config: bool) -> None:
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_mode & (0o022 if config else 0o077)
            or config and info.st_uid != 0):
        raise _PolicyIdentityErrorV2("policy target file")


def _protected_policy_path_v2(path: Path, *, config: bool) -> os.stat_result:
    if (not path.is_absolute() or ".." in path.parts or path != path.absolute()
            or path.is_junction()):
        raise _PolicyIdentityErrorV2("policy target path")
    for parent in path.parents:
        info = os.lstat(parent)
        if (not stat.S_ISDIR(info.st_mode) or parent.is_junction()
                or info.st_mode & 0o022 or config and info.st_uid != 0):
            raise _PolicyIdentityErrorV2("policy target parent")
    info = os.lstat(path)
    _protected_policy_file_v2(info, config=config)
    if not config and os.lstat(path.parent).st_mode & 0o077:
        raise _PolicyIdentityErrorV2("policy target private parent")
    return info


def _pin_policy_file_v2(path: Path, *, config: bool, resources: _PolicyResourcesV2) -> int:
    named = _protected_policy_path_v2(path, config=config)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_CLOEXEC", 0))
    resources.descriptors.append(descriptor)
    opened = os.fstat(descriptor)
    _protected_policy_file_v2(opened, config=config)
    if _file_identity_v2(named) != _file_identity_v2(opened):
        raise _PolicyIdentityErrorV2("policy pin identity")
    return descriptor


def _config_bytes_v2(descriptor: int) -> bytes:
    info = os.fstat(descriptor)
    if not 0 < info.st_size <= 4096:
        raise _PolicyIdentityErrorV2("policy target config size")
    os.lseek(descriptor, 0, os.SEEK_SET)
    raw = os.read(descriptor, 4097)
    if len(raw) != info.st_size:
        raise _PolicyIdentityErrorV2("policy target config read")
    return raw


class RuntimePolicyBook:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._initialize_private_v2()
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                if db.execute("SELECT 1 FROM sqlite_master").fetchone() is not None:
                    raise ValueError("policy storage")
                db.execute("CREATE TABLE runtime_policy_v2 " + TABLE_LAYOUT)
                draft = {"legacy_v1": dict.fromkeys(KEYS), "scope": None,
                         "strategy": None, "risk_v2": None}
                db.execute("INSERT INTO runtime_policy_v2 VALUES(1,0,?,'STOPPED')",
                           (json.dumps(draft),))
                db.execute("PRAGMA user_version=2")
            self._read(db)
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("policy corruption")

    def connect(self) -> sqlite3.Connection:
        if self._target_binding_v2 is not None:
            raise ValueError("configured policy requires operation lifetime")
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _initialize_private_v2(self) -> None:
        self._target_binding_v2: _PolicyTargetBindingV2 | None = None
        self._admission_gate_v2 = Lock()
        self._admission_poisoned_v2 = False
        self._quarantined_resources_v2: _PolicyResourcesV2 | None = None
        self._cleanup_failures_v2: tuple[BaseException, ...] = ()

    @classmethod
    def _from_shared_target_v2(cls, explicit_legacy_path: Path | None) -> "RuntimePolicyBook | None":
        if sys.platform != "linux":
            return None
        config = _SHARED_POLICY_TARGET_V2
        try:
            os.lstat(config)
        except FileNotFoundError:
            return None
        book = cls.__new__(cls)
        book._initialize_private_v2()
        resources = _PolicyResourcesV2()
        primary: BaseException | None = None
        try:
            config_fd = _pin_policy_file_v2(config, config=True, resources=resources)
            raw = _config_bytes_v2(config_fd)
            value = parse_json(raw)
            if (type(value) is not dict or set(value) != {"schema_version", "policy_db"}
                    or type(value["schema_version"]) is not int or value["schema_version"] != 1
                    or type(value["policy_db"]) is not str):
                raise _PolicyIdentityErrorV2("policy target config fields")
            path = Path(value["policy_db"])
            if str(path) != value["policy_db"]:
                raise _PolicyIdentityErrorV2("policy target canonical path")
            _protected_policy_path_v2(path, config=False)
            if explicit_legacy_path is not None:
                if explicit_legacy_path != path:
                    raise _PolicyIdentityErrorV2("policy target explicit path conflict")
                _protected_policy_path_v2(explicit_legacy_path, config=False)
            db_fd = _pin_policy_file_v2(path, config=False, resources=resources)
            book.path = path
            book._target_binding_v2 = _PolicyTargetBindingV2(book, path, config, raw,
                hashlib.sha256(raw).hexdigest(), _file_identity_v2(os.fstat(config_fd)),
                _file_identity_v2(os.fstat(db_fd)))
            book._verify_bound_pins_v2(resources)
            book._open_bound_connection_v2(resources, timeout=10)
            assert resources.db is not None
            resources.db.execute("BEGIN")
            book._read(resources.db)
            if resources.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("policy corruption")
            book._verify_bound_pins_v2(resources)
        except BaseException as error:
            primary = error
            raise
        finally:
            book._cleanup_bound_v2(resources, primary)
        return book

    def _require_bound_v2(self) -> _PolicyTargetBindingV2:
        binding = self._target_binding_v2
        if (binding is None or binding.book is not self or self.path is not binding.original_path
                or _SHARED_POLICY_TARGET_V2 is not binding.config_path
                or self._quarantined_resources_v2 is not None):
            self._admission_poisoned_v2 = True
            raise _PolicyIdentityErrorV2("original configured policy book required")
        return binding

    def _verify_bound_pins_v2(self, resources: _PolicyResourcesV2) -> None:
        binding = self._require_bound_v2()
        if len(resources.descriptors) != 2:
            raise _PolicyIdentityErrorV2("policy pin lifetime")
        for path, descriptor, identity, config in (
            (binding.config_path, resources.descriptors[0], binding.config_identity, True),
            (binding.original_path, resources.descriptors[1], binding.db_identity, False),
        ):
            named = _protected_policy_path_v2(path, config=config)
            opened = os.fstat(descriptor)
            _protected_policy_file_v2(opened, config=config)
            if (_file_identity_v2(named) != identity or _file_identity_v2(opened) != identity):
                raise _PolicyIdentityErrorV2("policy target changed")
        if _config_bytes_v2(resources.descriptors[0]) != binding.config_bytes:
            raise _PolicyIdentityErrorV2("policy target config changed")
        if resources.db is not None:
            databases = resources.db.execute("PRAGMA database_list").fetchall()
            names = [Path(row[2]) for row in databases if row[1] == "main"]
            if names != [binding.original_path]:
                raise _PolicyIdentityErrorV2("policy SQLite target changed")

    def _open_bound_connection_v2(self, resources: _PolicyResourcesV2, *, timeout: float) -> None:
        binding = self._require_bound_v2()
        db = sqlite3.connect(binding.original_path.as_uri() + "?mode=rw", uri=True, timeout=timeout)
        resources.db = db
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        if timeout == 0:
            db.execute("PRAGMA busy_timeout=0")
        self._verify_bound_pins_v2(resources)

    def _cleanup_bound_v2(self, resources: _PolicyResourcesV2,
                          primary: BaseException | None) -> None:
        failures: list[BaseException] = []
        db = resources.db
        if db is not None:
            try:
                if db.in_transaction:
                    db.rollback()
            except BaseException as error:  # noqa: BLE001 - finish cleanup even on cancellation.
                failures.append(error)
            try:
                db.close()
                resources.db = None
            except BaseException as error:  # noqa: BLE001 - finish cleanup even on cancellation.
                failures.append(error)
        remaining: list[int] = []
        for descriptor in resources.descriptors:
            try:
                os.close(descriptor)
            except BaseException as error:  # noqa: BLE001 - finish cleanup even on cancellation.
                failures.append(error)
                remaining.append(descriptor)
        resources.descriptors = remaining
        if isinstance(primary, (_PolicyIdentityErrorV2, OSError)) or failures:
            self._admission_poisoned_v2 = True
        if resources.db is not None or remaining:
            self._quarantined_resources_v2 = resources
            # A loader may fail before returning its original book. Transfer a
            # strong diagnostic reference through the ORIGINAL error, not GC.
            owner_error = primary if primary is not None else failures[0]
            BaseException.__setattr__(owner_error, "_policy_cleanup_owner_v2", self)
        if failures:
            self._cleanup_failures_v2 = tuple(failures)
            if primary is not None:
                primary.add_note("configured policy cleanup uncertain; owner retired")
            else:
                raise failures[0]

    @contextmanager
    def _bound_connection_scope_v2(self, *, timeout: float, commit: bool) -> Iterator[sqlite3.Connection]:
        if not self._admission_gate_v2.acquire(blocking=False):
            raise ValueError("policy operation already reserved")
        try:
            if self._admission_poisoned_v2:
                raise ValueError("policy admission owner retired")
            binding = self._require_bound_v2()
            resources = _PolicyResourcesV2()
            primary: BaseException | None = None
            try:
                _pin_policy_file_v2(binding.config_path, config=True, resources=resources)
                _pin_policy_file_v2(binding.original_path, config=False, resources=resources)
                self._verify_bound_pins_v2(resources)
                self._open_bound_connection_v2(resources, timeout=timeout)
                assert resources.db is not None
                yield resources.db
                self._verify_bound_pins_v2(resources)
                if commit:
                    resources.db.commit()
                    self._verify_bound_pins_v2(resources)
            except BaseException as error:
                primary = error
                raise
            finally:
                self._cleanup_bound_v2(resources, primary)
        finally:
            self._admission_gate_v2.release()

    @contextmanager
    def _operation_v2(self) -> Iterator[sqlite3.Connection]:
        if self._target_binding_v2 is None:
            with closing(self.connect()) as db, db:
                yield db
        else:
            with self._bound_connection_scope_v2(timeout=10, commit=True) as db:
                yield db

    @contextmanager
    def _private_admission_snapshot_v2(self, scope: RiskScopeV2, expected_revision: int,
                                       expected_policy_sha256: str) -> Iterator[_PolicyAdmissionDataV2]:
        revision = validate_revision(expected_revision)
        if (type(expected_policy_sha256) is not str
                or re.fullmatch("[0-9a-f]{64}", expected_policy_sha256) is None):
            raise ValueError("policy admission hash")
        with self._bound_connection_scope_v2(timeout=0, commit=False) as db:
            binding = self._require_bound_v2()
            db.execute("BEGIN IMMEDIATE")
            snapshot = self._read(db)
            policy = self._risk_policy_v2_from_snapshot(snapshot, scope)
            digest = risk_policy_sha256_v2(policy)
            if snapshot["revision"] != revision or digest != expected_policy_sha256:
                raise ValueError("policy admission revision conflict")
            yield _PolicyAdmissionDataV2(self, binding, revision, policy, digest)

    @staticmethod
    def _row(db: sqlite3.Connection, table: str) -> sqlite3.Row:
        expected = [("id", "INTEGER", 0, 1), ("revision", "INTEGER", 1, 0),
                    ("payload", "TEXT", 1, 0), ("desired_state", "TEXT", 1, 0)]
        actual = [(row[1], row[2], row[3], row[5])
                  for row in db.execute(f"PRAGMA table_info({table})")]
        if actual != expected:
            raise ValueError("policy storage layout")
        rows = db.execute(f"SELECT * FROM {table}").fetchall()
        if (len(rows) != 1 or rows[0]["id"] != 1 or type(rows[0]["payload"]) is not str
                or rows[0]["desired_state"] != "STOPPED"):
            raise ValueError("policy storage")
        validate_revision(rows[0]["revision"])
        return cast(sqlite3.Row, rows[0])

    @classmethod
    def _read(cls, db: sqlite3.Connection) -> dict[str, Any]:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if version not in {1, 2, 3}:
            raise ValueError("policy schema version")
        # Version 3 is the persisted publication extension, not a reinterpretation
        # of the existing schema-2 user policy payload or its STOPPED state.
        version = 2 if version == 3 else version
        historical = {"runtime_policy", "runtime_policy_v1_archive"}
        if version == 1:
            if tables & (historical | {"runtime_policy_v2"}) != {"runtime_policy"}:
                raise ValueError("policy storage layout")
            row = cls._row(db, "runtime_policy")
            draft = validate_draft(parse_json(row["payload"]))
            missing = missing_fields(draft)
            blocker = "POLICY_V1_MIGRATION_REQUIRED"
        else:
            if "runtime_policy_v2" not in tables or tables & historical not in (set(), historical):
                raise ValueError("policy storage layout")
            if tables & historical:
                original = cls._row(db, "runtime_policy")
                archive = cls._row(db, "runtime_policy_v1_archive")
                if tuple(original) != tuple(archive):
                    raise ValueError("policy archive mismatch")
                validate_draft(parse_json(original["payload"]))
                triggers = {row[0]: row[1] for row in db.execute(
                    "SELECT name,sql FROM sqlite_master WHERE type='trigger'")}
                if any(triggers.get(name) != sql for name, sql in _fences().items()):
                    raise ValueError("policy archive fence")
            row = cls._row(db, "runtime_policy_v2")
            if tables & historical and row["revision"] <= original["revision"]:
                raise ValueError("policy archive revision")
            draft = validate_v2(_parse_v2(row["payload"]))
            missing = section_missing_fields(draft)
            blocker = "POLICY_V2_ADMISSION_REQUIRED"
        publication = _publication_read_v1(db, draft, row["revision"])
        blockers = [blocker, "NATIVE_RUNTIME_NOT_QUALIFIED", "WORKER_NOT_CONNECTED"]
        if version == 2:
            if draft["strategy"] is not None and publication is None:
                blockers.append("STRATEGY_CURRENT_PUBLICATION_REQUIRED")
            if draft["scope"] is not None and draft["scope"]["selected_product"] == "SPOT":
                blockers.append("POLICY_SPOT_RISK_UNSUPPORTED")
        return {"schema_version": version, "revision": row["revision"], "policy": draft,
                "missing_fields": missing, "desired_state": "STOPPED",
                "live_order_enabled": False, "worker_running": False,
                "blockers": blockers}

    def read(self) -> dict[str, Any]:
        with self._operation_v2() as db:
            db.execute("BEGIN")
            return self._read(db)

    def publication_snapshot_v1(self) -> dict[str, Any] | None:
        """One current read transaction; returned metadata grants no execution authority."""
        with self._operation_v2() as db:
            db.execute("BEGIN")
            snapshot = self._read(db)
            return _publication_read_v1(db, snapshot["policy"], snapshot["revision"])

    def save(self, revision: object, draft: object) -> dict[str, Any]:
        raise ValueError("POLICY_V1_MIGRATION_REQUIRED")

    def migrate_v1(self, expected_revision: int) -> dict[str, Any]:
        revision = validate_revision(expected_revision, increment=True)
        with self._operation_v2() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._read(db)
            if current["schema_version"] != 1 or current["revision"] != revision:
                raise ValueError("policy revision conflict")
            # Preserve the original TEXT; decoding is validation, never archival serialization.
            original = self._row(db, "runtime_policy")
            db.execute("CREATE TABLE runtime_policy_v1_archive " + TABLE_LAYOUT)
            db.execute("INSERT INTO runtime_policy_v1_archive VALUES(?,?,?,?)", tuple(original))
            db.execute("CREATE TABLE runtime_policy_v2 " + TABLE_LAYOUT)
            draft = {"legacy_v1": current["policy"], "scope": None,
                     "strategy": None, "risk_v2": None}
            db.execute("INSERT INTO runtime_policy_v2 VALUES(1,?,?,'STOPPED')",
                       (revision + 1, json.dumps(draft)))
            for sql in _fences().values():
                db.execute(sql)
            db.execute("PRAGMA user_version=2")
            return self._read(db)

    def save_v2(self, revision: object, draft: object, *,
                expected_strategy_generation: object, policy_sections_version: object = None) -> dict[str, Any]:
        revision = validate_revision(revision, increment=True)
        if policy_sections_version is not None and (type(policy_sections_version) is not int or policy_sections_version != 2):
            raise ValueError("policy sections capability")
        with self._operation_v2() as db:
            db.execute("BEGIN IMMEDIATE")
            # Validate persisted pins before capability checks and revision CAS.
            current = self._read(db)
            policy = validate_v2(draft)
            publication = _publication_read_v1(db, current["policy"], current["revision"])
            if publication is None:
                if expected_strategy_generation is not None:
                    raise ValueError("policy strategy generation")
            else:
                if (type(expected_strategy_generation) is not int
                        or expected_strategy_generation != publication["generation"]):
                    raise ValueError("policy strategy generation")
                _publication_policy_v1(policy, publication["catalog"])
            if current["schema_version"] != 2 or current["revision"] != revision:
                raise ValueError("policy revision conflict")
            if policy_sections_version is None and any(
                    source[key] is not None for source in (current["policy"], policy)
                    for key in ("scope", "strategy", "risk_v2")):
                raise ValueError("policy sections capability")
            payload = _encoded_policy(policy)
            if db.execute("UPDATE runtime_policy_v2 SET revision=revision+1,payload=? "
                          "WHERE id=1 AND revision=?", (payload, revision)).rowcount != 1:
                raise ValueError("policy revision conflict")
            if publication is not None:
                db.execute("UPDATE strategy_publication_state_v1 SET policy_revision=? WHERE id=1",
                           (revision + 1,))
            return self._read(db)

    def risk_policy_v2(self, scope: RiskScopeV2) -> LinearRiskPolicyV2:
        return self._risk_policy_v2_from_snapshot(self.read(), scope)

    @staticmethod
    def _risk_policy_v2_from_snapshot(snapshot: dict[str, Any], scope: RiskScopeV2) -> LinearRiskPolicyV2:
        if snapshot["schema_version"] != 2:
            raise ValueError("POLICY_V1_MIGRATION_REQUIRED")
        policy = validate_v2(snapshot["policy"])
        stored_scope, strategy, risk = (policy[name] for name in ("scope", "strategy", "risk_v2"))
        # Reconstruct the exact public scope to reject forged/partially initialized DTOs.
        if type(scope) is not RiskScopeV2:
            raise RiskV2Rejected("RISK_V2_SCOPE_MISMATCH")
        try:
            RiskScopeV2(scope.product_scope, scope.instrument, scope.strategy)
        except (AttributeError, TypeError, ValueError):
            raise RiskV2Rejected("RISK_V2_SCOPE_MISMATCH") from None
        if (scope.product_scope.product == "SPOT" or stored_scope is not None
                and stored_scope["selected_product"] == "SPOT"):
            raise RiskV2Rejected("RISK_V2_UNSUPPORTED_PRODUCT")
        caps = risk["caps"] if risk is not None else None
        if (caps is None or any(caps[name] is None for name in (
                "max_order_admission_notional", "max_account_admission_notional", "valuation_rule", "turnover_requirement"))
                or caps["turnover_requirement"] == "HARD_EXECUTION_QUOTE_CAP" and caps["max_execution_quote_amount"] is None):
            raise RiskV2Rejected("RISK_V2_CAP_POLICY_MIGRATION_REQUIRED")
        if section_missing_fields(policy):
            raise ValueError("POLICY_V2_ADMISSION_REQUIRED")
        universe = stored_scope["selected_instruments" if stored_scope["mode"] == "FULL_AUTO" else "allowed_instruments"]
        if (scope.product_scope.account.account_id != stored_scope["account"]["account_id"]
                or scope.product_scope.account.environment != stored_scope["account"]["environment"]
                or scope.product_scope.product != stored_scope["selected_product"]
                or {"venue": scope.instrument.venue, "product": scope.instrument.product,
                    "symbol": scope.instrument.symbol} not in universe):
            raise RiskV2Rejected("RISK_V2_SCOPE_MISMATCH")
        allowed, selected = eligible_references(stored_scope, strategy, scope.instrument)
        if not allowed or scope.strategy not in (selected if stored_scope["mode"] == "FULL_AUTO" else allowed):
            raise RiskV2Rejected("RISK_V2_SCOPE_MISMATCH")
        catalog = catalog_from_wire(strategy["catalog"])
        selection = require_strategy_references(catalog, expected_catalog_revision=catalog.revision,
                    allowed=allowed, selected=selected, instrument=scope.instrument,
                    environment=cast(ApprovalEnvironment, scope.product_scope.account.environment))
        if (tuple(entry.reference for entry in selection.allowed) != allowed
                or tuple(entry.reference for entry in selection.selected) != selected):
            raise ValueError("POLICY_V2_ADMISSION_REQUIRED")
        cap_values: dict[str, Any] = {name: (Decimal(value) if name in {"max_order_admission_notional",
                      "max_account_admission_notional", "max_execution_quote_amount"} and value is not None else value)
                      for name, value in caps.items()}
        values: dict[str, Any] = {name: Decimal(value) if value is not None and name in RISK_DECIMALS else value
                  for name, value in risk.items() if name not in {"section_version", "product", "caps"}}
        return LinearRiskPolicyV2(scope=scope, revision=str(snapshot["revision"]),
                                  caps=LinearCapitalCapsV2(**cap_values), **values)

    def risk_policy(self, scope: RiskScope) -> UserRiskPolicy:
        snapshot = self.read()
        if snapshot["schema_version"] == 2:
            raise ValueError("POLICY_V2_ADMISSION_REQUIRED")
        draft = snapshot["policy"]
        if snapshot["missing_fields"] or scope.environment != draft["environment"]:
            raise ValueError("policy incomplete or environment mismatch")
        if scope.symbol not in draft["allowed_symbols"] or scope.strategy_id not in draft["allowed_strategies"]:
            raise ValueError("scope outside policy")
        if draft["mode"] == "FULL_AUTO" and (scope.symbol not in draft["selected_symbols"]
                or scope.strategy_id not in draft["selected_strategies"]):
            raise ValueError("scope outside user selection")
        values: dict[str, Any] = {field.name: (Decimal(draft[field.name]) if field.name in DECIMALS
                              else draft[field.name]) for field in fields(UserRiskPolicy)
                  if field.name not in {"scope", "revision"}}
        return UserRiskPolicy(scope=scope, revision=str(snapshot["revision"]), **values)
