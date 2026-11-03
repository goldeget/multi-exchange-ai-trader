"""Revisioned operator drafts. Saving settings never grants execution authority."""

import json
import re
import sqlite3
from contextlib import closing
from dataclasses import fields
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from .policy_sections import (
    RISK_DECIMALS,
    catalog_from_wire,
    eligible_references,
    section_missing_fields,
    validate_sections_v2,
)
from .risk import RiskScope, UserRiskPolicy
from .risk_v2_contracts import LinearCapitalCapsV2, LinearRiskPolicyV2, RiskScopeV2, RiskV2Rejected
from .strategy_catalog import ApprovalEnvironment, require_strategy_references

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


class RuntimePolicyBook:
    def __init__(self, path: Path) -> None:
        self.path = path
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
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        return db

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
        if version not in {1, 2}:
            raise ValueError("policy schema version")
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
        blockers = [blocker, "NATIVE_RUNTIME_NOT_QUALIFIED", "WORKER_NOT_CONNECTED"]
        if version == 2:
            if draft["strategy"] is not None:
                blockers.append("STRATEGY_CURRENT_PUBLICATION_REQUIRED")
            if draft["scope"] is not None and draft["scope"]["selected_product"] == "SPOT":
                blockers.append("POLICY_SPOT_RISK_UNSUPPORTED")
        return {"schema_version": version, "revision": row["revision"], "policy": draft,
                "missing_fields": missing, "desired_state": "STOPPED",
                "live_order_enabled": False, "worker_running": False,
                "blockers": blockers}

    def read(self) -> dict[str, Any]:
        with closing(self.connect()) as db, db:
            db.execute("BEGIN")
            return self._read(db)

    def save(self, revision: object, draft: object) -> dict[str, Any]:
        raise ValueError("POLICY_V1_MIGRATION_REQUIRED")

    def migrate_v1(self, expected_revision: int) -> dict[str, Any]:
        revision = validate_revision(expected_revision, increment=True)
        with closing(self.connect()) as db, db:
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
        if expected_strategy_generation is not None:
            raise ValueError("policy strategy generation")
        if policy_sections_version is not None and (type(policy_sections_version) is not int or policy_sections_version != 1):
            raise ValueError("policy sections capability")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            # Validate persisted pins before capability checks and revision CAS.
            current = self._read(db)
            policy = validate_v2(draft)
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
            return self._read(db)

    def risk_policy_v2(self, scope: RiskScopeV2) -> LinearRiskPolicyV2:
        snapshot = self.read()
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
        values: dict[str, Any] = {name: Decimal(value) if name in RISK_DECIMALS else value
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
