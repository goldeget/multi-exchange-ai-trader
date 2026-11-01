"""Transactional USD budget; observed overruns persist and hold new reservations."""

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

_UNITS_PER_USD = 100_000_000


def amount(value: object) -> Decimal:
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,8})?", value):
        raise ValueError("budget decimal string required")
    result = Decimal(value)
    if not result.is_finite() or result < 0 or result > 100:
        raise ValueError("budget amount outside limit")
    return result


def actual_amount(value: object) -> Decimal:
    """Accept exact bounded plain-decimal evidence, independently of spending limits."""
    if (not isinstance(value, str) or len(value) > 129
            or not re.fullmatch(r"(?:0|[1-9][0-9]{0,63})(?:\.[0-9]{1,64})?", value)):
        raise ValueError("actual charge decimal string required")
    return Decimal(value)


def _units(value: Decimal) -> int:
    """Round nonnegative USD upward to 1e-8 units without Decimal arithmetic."""
    parts = value.as_tuple()
    if parts.sign or not isinstance(parts.exponent, int):
        raise ValueError("invalid money value")
    coefficient = int("".join(str(digit) for digit in parts.digits))
    shift = parts.exponent + 8
    if shift >= 0:
        multiplier: int = 10 ** shift
        return coefficient * multiplier
    divisor: int = 10 ** -shift
    return (coefficient + divisor - 1) // divisor


def _unit_money(units: int) -> str:
    whole, fraction = divmod(units, _UNITS_PER_USD)
    return f"{whole}.{fraction:08d}"


def money(value: Decimal) -> str:
    return _unit_money(_units(value))


class BudgetBook:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS ai_settings (
                    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
                    monthly_limit TEXT NOT NULL);
                INSERT OR IGNORE INTO ai_settings VALUES (1,0,'0.00000000');
                CREATE TABLE IF NOT EXISTS reservations (
                    request_id TEXT PRIMARY KEY, period TEXT NOT NULL, maximum TEXT NOT NULL,
                    actual TEXT, status TEXT NOT NULL, updated_at TEXT NOT NULL);
            """)
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("AI budget storage corruption")
            self._summary(db)
            db.commit()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    def _summary(self, db: sqlite3.Connection) -> dict[str, Any]:
        current_period = datetime.now(UTC).strftime("%Y-%m")
        limit = amount(db.execute("SELECT monthly_limit FROM ai_settings").fetchone()[0])
        used, reserved, uncertain, overrun_liability, overrun_count = 0, 0, 0, 0, 0
        for row in db.execute("SELECT * FROM reservations"):
            if row["status"] not in {"settled", "reserved", "uncertain"}:
                raise ValueError("AI budget storage state")
            maximum = amount(row["maximum"])
            if row["status"] == "settled":
                actual = actual_amount(row["actual"])
                if row["period"] == current_period:
                    used += _units(actual)
                # Settled evidence is immutable. Scanning every period retains this hold
                # across restart/month changes without changing the existing DB schema.
                if actual > maximum:
                    overrun_count += 1
                    overrun_liability += _units(actual)
            else:
                reserved += _units(maximum)
                if row["status"] == "uncertain":
                    uncertain += _units(maximum)
        return {"currency": "USD", "period": current_period, "monthly_limit": money(limit),
                "used": _unit_money(used), "reserved": _unit_money(reserved),
                "uncertain": _unit_money(uncertain),
                "remaining": _unit_money(max(0, _units(limit) - used - reserved)),
                "overrun_hold": overrun_count > 0, "overrun_count": overrun_count,
                "overrun_liability": _unit_money(overrun_liability),
                "provider_balance": None, "scope": "INTERNAL_PROGRAM_LIMIT"}

    def summary(self) -> dict[str, Any]:
        with self.connect() as db:
            db.execute("BEGIN")
            return self._summary(db)

    def settings(self) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM ai_settings").fetchone()
            return {"revision": row["revision"], "monthly_limit": row["monthly_limit"],
                    "mode": "OFFLINE_TEST", "allowed_models": [], "llm_enabled": False,
                    "provider_status": "NOT_CONFIGURED", "provider_verified_at": None}

    def configure(self, revision: int, monthly_limit: object) -> dict[str, Any]:
        limit = amount(monthly_limit)
        if type(revision) is not int or revision < 0:
            raise ValueError("settings revision required")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM ai_settings").fetchone()
            if row["revision"] != revision:
                raise ValueError("settings revision conflict")
            summary = self._summary(db)
            if _units(limit) < _units(Decimal(summary["used"])) + _units(Decimal(summary["reserved"])):
                raise ValueError("budget below existing exposure")
            db.execute("UPDATE ai_settings SET revision=?, monthly_limit=? WHERE id=1",
                       (revision + 1, money(limit)))
            db.commit()
        return self.settings()

    def reserve(self, request_id: str, max_cost: object) -> dict[str, Any]:
        """Return reservation evidence; an existing ID is never dispatch permission."""
        maximum = amount(max_cost)
        if (not isinstance(request_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id) or maximum <= 0):
            raise ValueError("budget request required")
        stamp = datetime.now(UTC)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM reservations WHERE request_id=?", (request_id,)).fetchone()
            if previous is not None:
                if Decimal(previous["maximum"]) != maximum:
                    raise ValueError("budget idempotency conflict")
                return dict(previous)
            summary = self._summary(db)
            if summary["overrun_hold"]:
                raise ValueError("AI budget overrun reconciliation required")
            if maximum > Decimal(summary["remaining"]):
                raise ValueError("budget exhausted")
            db.execute("INSERT INTO reservations VALUES (?,?,?,?,?,?)",
                       (request_id, stamp.strftime("%Y-%m"), money(maximum), None,
                        "reserved", stamp.isoformat()))
            db.commit()
        return self.reconcile(request_id)

    def uncertain(self, request_id: str) -> dict[str, Any]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM reservations WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise ValueError("budget request not found")
            if row["status"] != "settled":
                db.execute("UPDATE reservations SET status='uncertain',updated_at=? WHERE request_id=?",
                           (datetime.now(UTC).isoformat(), request_id))
            db.commit()
        return self.reconcile(request_id)

    def settle(self, request_id: str, actual_cost: object) -> dict[str, Any]:
        actual = actual_amount(actual_cost)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM reservations WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise ValueError("budget request not found")
            if row["status"] == "settled" and Decimal(row["actual"]) != actual:
                raise ValueError("budget settlement conflict")
            if row["status"] == "settled":
                return dict(row)
            db.execute("UPDATE reservations SET actual=?,status='settled',updated_at=? WHERE request_id=?",
                       (format(actual, "f"), datetime.now(UTC).isoformat(), request_id))
            db.commit()
        return self.reconcile(request_id)

    def reconcile(self, request_id: str) -> dict[str, Any]:
        """Return persisted evidence; never infer that an unknown provider charge was zero."""
        with self.connect() as db:
            row = db.execute("SELECT * FROM reservations WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise ValueError("budget request not found")
            return dict(row)
