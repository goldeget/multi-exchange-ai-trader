"""Frozen BASE V1 source. LF hash is platform independent; test use only."""

import hashlib
import sys
import types

SOURCE_SHA256 = 'b890e54b4e02e7f400a1dc3f578111b64b2a0be5d44fa11df6ae34dbc06c15ea'
SOURCE = r'''"""Revisioned operator drafts. Saving settings never grants execution authority."""

import json
import re
import sqlite3
from contextlib import closing
from dataclasses import fields
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from .risk import RiskScope, UserRiskPolicy

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
    except (UnicodeError, RecursionError) as exc:
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


class RuntimePolicyBook:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in {0, 1}:
                raise ValueError("policy schema version")
            db.execute("CREATE TABLE IF NOT EXISTS runtime_policy (id INTEGER PRIMARY KEY "
                       "CHECK(id=1),revision INTEGER NOT NULL,payload TEXT NOT NULL,"
                       "desired_state TEXT NOT NULL CHECK(desired_state='STOPPED'))")
            db.execute("INSERT OR IGNORE INTO runtime_policy VALUES(1,0,?,'STOPPED')",
                       (json.dumps(dict.fromkeys(KEYS)),))
            db.execute("PRAGMA user_version=1")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("policy corruption")
            db.commit()
        self.read()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _read(db: sqlite3.Connection) -> dict[str, Any]:
        row = db.execute("SELECT * FROM runtime_policy WHERE id=1").fetchone()
        if row is None or type(row["revision"]) is not int or row["revision"] < 0:
            raise ValueError("policy storage")
        draft = validate_draft(parse_json(row["payload"]))
        if row["desired_state"] != "STOPPED":
            raise ValueError("policy authority")
        return {"schema_version": 1, "revision": row["revision"], "policy": draft,
                "missing_fields": missing_fields(draft), "desired_state": "STOPPED",
                "live_order_enabled": False, "worker_running": False,
                "blockers": ["NATIVE_RUNTIME_NOT_QUALIFIED", "WORKER_NOT_CONNECTED"]}

    def read(self) -> dict[str, Any]:
        db = self.connect()
        try:
            return self._read(db)
        finally:
            db.close()

    def save(self, revision: object, draft: object) -> dict[str, Any]:
        if type(revision) is not int or revision < 0:
            raise ValueError("policy revision")
        payload = json.dumps(validate_draft(draft), sort_keys=True, separators=(",", ":"))
        db = self.connect()
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                self._read(db)
                if db.execute("UPDATE runtime_policy SET revision=revision+1,payload=? "
                              "WHERE id=1 AND revision=?", (payload, revision)).rowcount != 1:
                    raise ValueError("policy revision conflict")
                return self._read(db)
        finally:
            db.close()

    def risk_policy(self, scope: RiskScope) -> UserRiskPolicy:
        snapshot = self.read()
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
'''

def load_v1():
    assert hashlib.sha256(SOURCE.encode()).hexdigest() == SOURCE_SHA256
    name = "multi_exchange_trader.execution._frozen_runtime_policy_v1"
    module = types.ModuleType(name)
    sys.modules[name] = module
    # The source is a static, hashed original; execution is restricted to this test fixture.
    exec(compile(SOURCE, "<frozen-runtime-policy-v1>", "exec"), module.__dict__)  # noqa: S102
    return module.RuntimePolicyBook
