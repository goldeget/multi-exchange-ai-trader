"""Durable at-most-one paid dispatch, independent from budget reservation identity.

An interrupted dispatch is never retried, including after process restart. Unknown
charges retain the full reservation. Local reconciliation never calls a provider.
"""

import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..execution.modes import (
    SelectionRejected,
    SelectionRequest,
    SelectionResult,
    select_all_in_one,
)
from .budget import BudgetBook, amount, money
from .generation import GenerationPriceQuote, OpenRouterGenerationClient


def _now() -> datetime:
    return datetime.now(UTC)


def _reservation_id(request_id: str) -> str:
    return "dispatch_" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DispatchResult:
    request_id: str
    status: str
    failure: str | None = None
    reservation_status: str | None = None
    exact_cost_usd: str | None = None
    selection: SelectionResult | None = field(default=None, repr=False)


class AIDispatcher:
    """Construction/status are local only; dispatch requires explicit spending inputs.

    Use a single canonical ai-dispatch.sqlite3 and BudgetBook for every app worker.
    Database backup and restore must include both files from one quiescent snapshot.
    Results are returned once; reading/replaying an ID never re-authorizes selection.
    """

    def __init__(
        self, path: str | Path, budget: BudgetBook, client: OpenRouterGenerationClient, *,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.path, self.budget, self.client, self.clock = Path(path), budget, client, clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported AI dispatch storage")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS ai_dispatches (
                    request_id TEXT PRIMARY KEY, binding_hash TEXT NOT NULL,
                    maximum TEXT NOT NULL, status TEXT NOT NULL, failure TEXT,
                    exact_cost TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                PRAGMA user_version=1;
            """)
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("AI dispatch storage corruption")
            db.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    def _record(
        self, request_id: str, status: str, failure: str | None, cost: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._record_locked(db, request_id, status, failure, cost)
            db.commit()

    def _record_locked(
        self, db: sqlite3.Connection, request_id: str, status: str,
        failure: str | None, cost: str | None = None,
    ) -> None:
        db.execute("""UPDATE ai_dispatches SET status=?,failure=?,
                   exact_cost=COALESCE(?,exact_cost),updated_at=? WHERE request_id=?""",
                   (status, failure, cost, self.clock().isoformat(), request_id))

    def _apply_charge_locked(self, db: sqlite3.Connection, row: sqlite3.Row) -> None:
        # The budget commit may outlive a dispatch-storage crash. settle is idempotent,
        # so retry only this local accounting operation, never generation or selection.
        if row["exact_cost"] is None:
            raise ValueError("Missing persisted AI charge")
        self.budget.settle(_reservation_id(row["request_id"]), row["exact_cost"])
        self._record_locked(db, row["request_id"], "SETTLED", row["failure"])

    def status(self, request_id: str) -> DispatchResult | None:
        """No network, replay, automatic charge release, or selection reconstruction."""
        with self._connect() as db:
            row = db.execute("SELECT * FROM ai_dispatches WHERE request_id=?",
                             (request_id,)).fetchone()
        if row is None:
            return None
        status = row["status"]
        if status not in {"CLAIMED", "DISPATCHING", "CHARGED", "SETTLED", "UNCERTAIN", "BLOCKED"}:
            raise ValueError("Invalid AI dispatch storage")
        reservation = None
        try:
            reservation = str(self.budget.reconcile(_reservation_id(request_id))["status"])
        except ValueError:
            pass
        # CLAIMED may represent a crash before/after reserving. It is never permission.
        if status in {"CLAIMED", "DISPATCHING"}:
            status = "UNCERTAIN"
        return DispatchResult(request_id, status, row["failure"], reservation, row["exact_cost"])

    def reconcile_charge(self, request_id: str) -> DispatchResult:
        """Apply already persisted exact provider charge after a settlement/storage crash."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM ai_dispatches WHERE request_id=?",
                             (request_id,)).fetchone()
            if row is None:
                raise ValueError("AI dispatch not found")
            if row["exact_cost"] is not None:
                self._apply_charge_locked(db, row)
            db.commit()
        current = self.status(request_id)
        assert current is not None
        return current

    def dispatch(
        self, request: SelectionRequest, *, maximum_cost_usd: str,
        quote: GenerationPriceQuote | None = None, enabled: bool = False,
    ) -> DispatchResult:
        """Explicit call only. Key registration and budget configuration do not imply consent.

        The caller is trusted server policy code, not a provider response or HTTP JSON.
        It must independently authorize mode/scope/risk and keep policy fresh for execution.
        """
        request_id = request.request_id if type(request) is SelectionRequest else "invalid"
        if (type(request_id) is not str or len(request_id) > 256
                or "sk-" in request_id.lower() or "bearer" in request_id.lower()):
            return DispatchResult("invalid", "BLOCKED", "INVALID_DISPATCH_INPUT")
        if enabled is not True:
            return DispatchResult(request_id, "BLOCKED", "GENERATION_DISABLED")
        if quote is None:
            return DispatchResult(request_id, "BLOCKED", "PRICE_QUOTE_REQUIRED")
        try:
            # Fully validate before any durable claim or reservation.
            self.client.prepare(request, quote=quote, now=self.clock())
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}", request_id):
                raise ValueError
            maximum = amount(maximum_cost_usd)
            if maximum <= 0 or maximum < amount(quote.maximum_cost_usd):
                return DispatchResult(request_id, "BLOCKED", "INSUFFICIENT_MAXIMUM")
        except (ValueError, TypeError):
            return DispatchResult(request_id, "BLOCKED", "INVALID_DISPATCH_INPUT")
        binding = hashlib.sha256(
            (repr(request) + repr(quote) + self.client.model_id + money(maximum)).encode("utf-8")
        ).hexdigest()
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                return DispatchResult(request_id, "BLOCKED", "DISPATCH_STORAGE_UNAVAILABLE")
            previous = db.execute("SELECT binding_hash FROM ai_dispatches WHERE request_id=?",
                                  (request_id,)).fetchone()
            if previous is not None:
                if previous["binding_hash"] != binding:
                    return DispatchResult(request_id, "BLOCKED", "IDEMPOTENCY_CONFLICT")
                return DispatchResult(request_id, "BLOCKED", "ALREADY_DISPATCHED")
            stamp = self.clock().isoformat()
            db.execute("INSERT INTO ai_dispatches VALUES (?,?,?,?,?,?,?,?)",
                       (request_id, binding, money(maximum), "CLAIMED", None, None, stamp, stamp))
            # All workers serialize known-liability repair and new reservation admission
            # under this writer lock. Provider I/O starts only after the lock is released.
            try:
                pending = db.execute(
                    "SELECT * FROM ai_dispatches WHERE status='CHARGED'",
                ).fetchall()
                for row in pending:
                    self._apply_charge_locked(db, row)
            except (ValueError, sqlite3.Error):
                self._record_locked(db, request_id, "BLOCKED", "PENDING_CHARGE_RECONCILIATION")
                db.commit()
                return DispatchResult(request_id, "BLOCKED", "PENDING_CHARGE_RECONCILIATION")
            reservation_id = _reservation_id(request_id)
            try:
                # Existing BudgetBook evidence alone is never dispatch permission.
                try:
                    self.budget.reconcile(reservation_id)
                except ValueError:
                    pass
                else:
                    self._record_locked(db, request_id, "BLOCKED", "EXISTING_RESERVATION")
                    db.commit()
                    return DispatchResult(request_id, "BLOCKED", "EXISTING_RESERVATION")
                self.budget.reserve(reservation_id, money(maximum))
            except ValueError:
                self._record_locked(db, request_id, "BLOCKED", "BUDGET_BLOCKED")
                db.commit()
                return DispatchResult(request_id, "BLOCKED", "BUDGET_BLOCKED")
            # Both the dispatch claim and full unknown liability precede provider I/O.
            self.budget.uncertain(reservation_id)
            self._record_locked(db, request_id, "DISPATCHING", None)
            db.commit()
        try:
            generated = self.client.generate(request, quote=quote, now=self.clock())
        except Exception:  # noqa: BLE001 - never expose transport/credential exceptions.
            self._record(request_id, "UNCERTAIN", "GENERATION_FAILED")
            return DispatchResult(request_id, "UNCERTAIN", "GENERATION_FAILED", "uncertain")
        if generated.exact_cost_usd is None:
            uncertain_failure = generated.failure or "COST_UNAVAILABLE"
            self._record(request_id, "UNCERTAIN", uncertain_failure)
            return DispatchResult(request_id, "UNCERTAIN", uncertain_failure, "uncertain")
        # Record known liability durably before validating model output or releasing exposure.
        self._record(request_id, "CHARGED", generated.failure, generated.exact_cost_usd)
        self.budget.settle(reservation_id, generated.exact_cost_usd)
        selection = None
        failure = generated.failure
        if failure is None:
            try:
                selection = select_all_in_one(request=request, response=generated.output,
                                              now=self.clock())
            except SelectionRejected:
                failure = "INVALID_SELECTION"
        self._record(request_id, "SETTLED", failure)
        # Even a paid valid selection cannot proceed after an observed ceiling overrun.
        if self.budget.summary()["overrun_hold"]:
            failure, selection = "BUDGET_OVERRUN_HOLD", None
            self._record(request_id, "SETTLED", failure)
        return DispatchResult(request_id, "SETTLED", failure, "settled",
                              generated.exact_cost_usd, selection)
