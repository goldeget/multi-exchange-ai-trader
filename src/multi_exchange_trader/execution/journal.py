"""Scoped durable intent, reservations, and event projection persistence.

No network callbacks are accepted. Recovery needs an externally enforced exclusive
account worker: ``exclusive_owner=True`` is a caller assertion, not a process lock.
Opening a second journal never rewrites another sender's SENDING state. Reservations
are journal accounting only; callers supply qualified, non-overlapping account
counters, limits, and terminal evidence. Acknowledgement never releases capacity.
Payloads must be sanitized by callers: obvious credential keys are rejected, but
this module cannot identify secrets hidden in arbitrary strings.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from types import TracebackType
from typing import Self


class CommandKind(StrEnum):
    ENTRY = "ENTRY"
    PROTECTION = "PROTECTION"
    EXIT = "EXIT"
    CANCEL = "CANCEL"


class CommandState(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    UNKNOWN = "UNKNOWN"
    REJECTED = "REJECTED"


class JournalConflict(ValueError):
    """The same durable identity was reused with incompatible data or state."""


class ReservationLimitExceeded(ValueError):
    """Atomic journal reservation would exceed explicit caller capacity."""


class RevisionConflict(ValueError):
    """Projection changed since the caller read it; re-read and reduce again."""


def _identifier(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("identity must be a nonempty string of at most 256 characters")


def _amount(value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("amounts must be explicit finite nonnegative Decimal values")
    _decimal_text(value)


def _decimal_text(value: Decimal) -> str:
    if (
        not value.is_finite()
        or len(value.as_tuple().digits) > 256
        or (value and (abs(value.adjusted()) > 1000 or abs(int(value.as_tuple().exponent)) > 1000))
    ):
        raise ValueError("non-finite or excessively large Decimal")
    if value == 0:
        return "0"
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _canonical(payload: object) -> str:
    remaining = 10000
    secret_keys = {
        "apikey",
        "secret",
        "apisecret",
        "password",
        "token",
        "accesstoken",
        "refreshtoken",
        "authorization",
        "privatekey",
        "credentials",
    }

    def convert(value: object, depth: int) -> object:
        nonlocal remaining
        remaining -= 1
        if depth > 32 or remaining < 0:
            raise ValueError("payload nesting or item count exceeds bound")
        if isinstance(value, Decimal):
            return _decimal_text(value)
        if value is None or isinstance(value, (bool, int, str)):
            if isinstance(value, str) and len(value) > 65536:
                raise ValueError("payload string exceeds bound")
            if isinstance(value, int) and value.bit_length() > 4096:
                raise ValueError("payload integer exceeds bound")
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("non-finite number")
            return value
        if isinstance(value, dict):
            result: dict[str, object] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("JSON keys must be strings")
                if key.lower().replace("_", "").replace("-", "") in secret_keys:
                    raise ValueError("credential fields may not enter the journal")
                result[key] = convert(item, depth + 1)
            return result
        if isinstance(value, (list, tuple)):
            return [convert(item, depth + 1) for item in value]
        raise ValueError("payload must contain JSON values or finite Decimal")

    result = json.dumps(
        convert(payload, 0),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    if len(result.encode("utf-8")) > 65536:
        raise ValueError("payload exceeds 64 KiB")
    return result


@dataclass(frozen=True)
class ExecutionScope:
    account_id: str
    environment: str

    def __post_init__(self) -> None:
        _identifier(self.account_id)
        if self.environment not in ("DEMO", "LIVE"):
            raise ValueError("explicit DEMO or LIVE environment is required")


@dataclass(frozen=True)
class Reservation:
    risk_amount: Decimal
    notional_amount: Decimal
    margin_amount: Decimal

    def __post_init__(self) -> None:
        for value in (self.risk_amount, self.notional_amount, self.margin_amount):
            _amount(value)


@dataclass(frozen=True)
class ReservationLimits:
    """Current counters must exclude reservations already held by this journal."""

    max_risk: Decimal
    max_notional: Decimal
    max_margin: Decimal
    max_commands: int
    current_risk: Decimal
    current_notional: Decimal
    current_margin: Decimal
    current_commands: int

    def __post_init__(self) -> None:
        for value in (
            self.max_risk,
            self.max_notional,
            self.max_margin,
            self.current_risk,
            self.current_notional,
            self.current_margin,
        ):
            _amount(value)
        for count in (self.max_commands, self.current_commands):
            if type(count) is not int or count < 0:
                raise ValueError("command counts must be nonnegative integers")


@dataclass(frozen=True)
class CommandRecord:
    command_key: str
    client_order_id: str
    kind: CommandKind
    payload_json: str
    payload_hash: str
    state: CommandState
    reservation: Reservation | None
    terminal_confirmed: bool


@dataclass(frozen=True)
class ProjectionRecord:
    projection_key: str
    revision: int
    payload_json: str


@dataclass(frozen=True)
class AuditRecord:
    sequence: int
    command_key: str | None
    action: str
    detail_json: str
    occurred_at: datetime


_SCHEMA = """
CREATE TABLE IF NOT EXISTS commands (
 account TEXT NOT NULL, environment TEXT NOT NULL, command_key TEXT NOT NULL,
 client_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, payload TEXT NOT NULL,
 payload_hash TEXT NOT NULL, state TEXT NOT NULL,
 risk TEXT, notional TEXT, margin TEXT, terminal INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(account, environment, command_key));
CREATE TABLE IF NOT EXISTS audit (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
 environment TEXT NOT NULL, command_key TEXT, action TEXT NOT NULL,
 detail TEXT NOT NULL, occurred_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
 BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
 BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TABLE IF NOT EXISTS projections (
 account TEXT NOT NULL, environment TEXT NOT NULL, projection_key TEXT NOT NULL,
 revision INTEGER NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(account, environment, projection_key));
CREATE TABLE IF NOT EXISTS inbox (
 account TEXT NOT NULL, environment TEXT NOT NULL, event_id TEXT NOT NULL,
 event_payload TEXT NOT NULL, projection_key TEXT NOT NULL,
 revision INTEGER NOT NULL, projection_payload TEXT NOT NULL,
 PRIMARY KEY(account, environment, event_id));
"""


class CommandJournal:
    def __init__(self, path: Path, scope: ExecutionScope) -> None:
        self.scope = scope
        self._scope = (scope.account_id, scope.environment)
        self._db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        try:
            # Simultaneous first-open mode changes can return SQLITE_BUSY without
            # invoking SQLite's busy handler. Only this idempotent local setup is
            # retried; commands/effects are never replayed by this mechanism.
            deadline = time.monotonic() + 10
            while True:
                try:
                    mode = self._db.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                    if mode != "wal":
                        raise ValueError("journal requires a file supporting SQLite WAL")
                    break
                except sqlite3.OperationalError as error:
                    if (getattr(error, "sqlite_errorcode", 0) & 255) != sqlite3.SQLITE_BUSY or (
                        time.monotonic() >= deadline
                    ):
                        raise
                    time.sleep(0.01)
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.executescript(_SCHEMA)
        except BaseException:
            self._db.close()
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._db.execute("COMMIT")
        except BaseException:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise

    def _audit(self, key: str | None, action: str, detail: object) -> None:
        self._db.execute(
            "INSERT INTO audit(account,environment,command_key,action,detail,"
            "occurred_at) VALUES(?,?,?,?,?,?)",
            (*self._scope, key, action, _canonical(detail), datetime.now(UTC).isoformat()),
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> CommandRecord:
        reservation = (
            None
            if row["risk"] is None
            else Reservation(Decimal(row["risk"]), Decimal(row["notional"]), Decimal(row["margin"]))
        )
        return CommandRecord(
            row["command_key"],
            row["client_id"],
            CommandKind(row["kind"]),
            row["payload"],
            row["payload_hash"],
            CommandState(row["state"]),
            reservation,
            bool(row["terminal"]),
        )

    def get(self, command_key: str) -> CommandRecord | None:
        row = self._db.execute(
            "SELECT * FROM commands WHERE account=? AND environment=? AND command_key=?",
            (*self._scope, command_key),
        ).fetchone()
        return None if row is None else self._record(row)

    def _required(self, command_key: str) -> CommandRecord:
        record = self.get(command_key)
        if record is None:
            raise KeyError(command_key)
        return record

    def list_commands(
        self, states: tuple[CommandState, ...] | None = None
    ) -> tuple[CommandRecord, ...]:
        rows = self._db.execute(
            "SELECT * FROM commands WHERE account=? AND environment=? ORDER BY command_key",
            self._scope,
        ).fetchall()
        return tuple(
            self._record(row)
            for row in rows
            if states is None or CommandState(row["state"]) in states
        )

    def register(
        self,
        command_key: str,
        kind: CommandKind,
        payload: object,
        *,
        reservation: Reservation | None = None,
        limits: ReservationLimits | None = None,
    ) -> CommandRecord:
        _identifier(command_key)
        if not isinstance(kind, CommandKind):
            raise TypeError("explicit CommandKind is required")
        canonical = _canonical(payload)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        with self._transaction():
            existing = self.get(command_key)
            if existing is not None:
                if (existing.kind, existing.payload_json, existing.reservation) != (
                    kind,
                    canonical,
                    reservation,
                ):
                    raise JournalConflict("command identity has incompatible immutable data")
                return existing
            if reservation is not None:
                if limits is None:
                    raise ValueError("explicit limits are required for reservations")
                self._check_capacity(reservation, limits)
            client_id = (
                "ec_"
                + hashlib.sha256(_canonical([*self._scope, command_key]).encode()).hexdigest()[:32]
            )
            amounts = (
                (None, None, None)
                if reservation is None
                else (
                    _decimal_text(reservation.risk_amount),
                    _decimal_text(reservation.notional_amount),
                    _decimal_text(reservation.margin_amount),
                )
            )
            self._db.execute(
                "INSERT INTO commands(account,environment,command_key,client_id,"
                "kind,payload,payload_hash,state,risk,notional,margin) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    *self._scope,
                    command_key,
                    client_id,
                    kind.value,
                    canonical,
                    digest,
                    CommandState.PENDING.value,
                    *amounts,
                ),
            )
            self._audit(command_key, "REGISTER", {"payload_hash": digest})
            result = self._required(command_key)
        return result

    def _check_capacity(self, reservation: Reservation, limits: ReservationLimits) -> None:
        held = tuple(
            record.reservation
            for record in self.list_commands()
            if record.reservation is not None and not record.terminal_confirmed
        )
        # Fraction preserves every Decimal digit regardless of the ambient context.
        risk = sum((Fraction(item.risk_amount) for item in held if item is not None), Fraction(0))
        notional = sum(
            (Fraction(item.notional_amount) for item in held if item is not None), Fraction(0)
        )
        margin = sum(
            (Fraction(item.margin_amount) for item in held if item is not None), Fraction(0)
        )
        if (
            Fraction(limits.current_risk) + risk + Fraction(reservation.risk_amount)
            > limits.max_risk
            or Fraction(limits.current_notional) + notional + Fraction(reservation.notional_amount)
            > limits.max_notional
            or Fraction(limits.current_margin) + margin + Fraction(reservation.margin_amount)
            > limits.max_margin
            or limits.current_commands + len(held) + 1 > limits.max_commands
        ):
            raise ReservationLimitExceeded("explicit account plus held journal capacity exceeded")

    def claim(self, command_key: str) -> CommandRecord | None:
        with self._transaction():
            if self._required(command_key).state is not CommandState.PENDING:
                return None
            self._db.execute(
                "UPDATE commands SET state=? WHERE account=? AND environment=? AND command_key=?",
                (CommandState.SENDING.value, *self._scope, command_key),
            )
            self._audit(command_key, "CLAIM", {})
            result = self._required(command_key)
        return result

    def _transition(self, key: str, target: CommandState, evidence: str) -> CommandRecord:
        if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 2048:
            raise ValueError("bounded nonempty sanitized evidence is required")
        with self._transaction():
            record = self._required(key)
            if record.state is target:
                return record
            allowed = {CommandState.SENDING, CommandState.UNKNOWN}
            if target is CommandState.REJECTED:
                allowed.add(CommandState.PENDING)
            if record.state not in allowed or record.terminal_confirmed:
                raise JournalConflict("invalid command state transition")
            self._db.execute(
                "UPDATE commands SET state=? WHERE account=? AND environment=? AND command_key=?",
                (target.value, *self._scope, key),
            )
            self._audit(key, target.value, {"evidence": evidence})
            result = self._required(key)
        return result

    def acknowledge(self, command_key: str, evidence: str) -> CommandRecord:
        return self._transition(command_key, CommandState.ACKNOWLEDGED, evidence)

    def mark_unknown(self, command_key: str, evidence: str) -> CommandRecord:
        return self._transition(command_key, CommandState.UNKNOWN, evidence)

    def reject(self, command_key: str, evidence: str) -> CommandRecord:
        return self._transition(command_key, CommandState.REJECTED, evidence)

    def recover_sending(self, *, exclusive_owner: bool) -> tuple[CommandRecord, ...]:
        """Call only after external account-worker fencing excludes every active sender."""
        if exclusive_owner is not True:
            raise ValueError("exclusive account worker ownership must be established externally")
        with self._transaction():
            sending = self.list_commands((CommandState.SENDING,))
            for record in sending:
                self._db.execute(
                    "UPDATE commands SET state=? WHERE account=? AND environment=? "
                    "AND command_key=?",
                    (CommandState.UNKNOWN.value, *self._scope, record.command_key),
                )
                self._audit(record.command_key, "RECOVER_UNKNOWN", {})
            result = tuple(self._required(record.command_key) for record in sending)
        return result

    def release_reservation(
        self, command_key: str, *, confirmed_terminal: bool, terminal_evidence: str
    ) -> CommandRecord:
        """Caller confirms final venue evidence, including no outstanding ambiguous effect."""
        if (
            confirmed_terminal is not True
            or not isinstance(terminal_evidence, str)
            or not (terminal_evidence.strip())
            or len(terminal_evidence) > 2048
        ):
            raise ValueError("explicit confirmed terminal evidence is required")
        with self._transaction():
            record = self._required(command_key)
            if record.state not in (CommandState.ACKNOWLEDGED, CommandState.REJECTED):
                raise JournalConflict("UNKNOWN or unacknowledged commands retain reservations")
            if not record.terminal_confirmed:
                self._db.execute(
                    "UPDATE commands SET terminal=1 WHERE account=? AND environment=? "
                    "AND command_key=?",
                    (*self._scope, command_key),
                )
                self._audit(command_key, "RELEASE", {"evidence": terminal_evidence})
            result = self._required(command_key)
        return result

    def audit_events(self) -> tuple[AuditRecord, ...]:
        rows = self._db.execute(
            "SELECT * FROM audit WHERE account=? AND environment=? ORDER BY sequence", self._scope
        ).fetchall()
        return tuple(
            AuditRecord(
                row["sequence"],
                row["command_key"],
                row["action"],
                row["detail"],
                datetime.fromisoformat(row["occurred_at"]),
            )
            for row in rows
        )

    def get_projection(self, projection_key: str) -> ProjectionRecord | None:
        row = self._db.execute(
            "SELECT * FROM projections WHERE account=? AND environment=? AND projection_key=?",
            (*self._scope, projection_key),
        ).fetchone()
        return (
            None
            if row is None
            else ProjectionRecord(projection_key, row["revision"], row["payload"])
        )

    def apply_event(
        self,
        event_id: str,
        event_payload: object,
        projection_key: str,
        expected_revision: int,
        projection_payload: object,
    ) -> ProjectionRecord:
        """Atomically deduplicate and CAS; replay returns the CURRENT projection.

        A replay compares immutable event payload plus projection key and ignores
        proposed projection/revision. Thus replay cannot rewind later events.
        Reduction happens in caller code before this method, never in a transaction.
        """
        _identifier(event_id)
        _identifier(projection_key)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("revision must be a nonnegative integer")
        event_json, projection_json = _canonical(event_payload), _canonical(projection_payload)
        with self._transaction():
            event = self._db.execute(
                "SELECT * FROM inbox WHERE account=? AND environment=? AND event_id=?",
                (*self._scope, event_id),
            ).fetchone()
            if event is not None:
                if (event["event_payload"], event["projection_key"]) != (
                    event_json,
                    projection_key,
                ):
                    raise JournalConflict("event identity has incompatible immutable data")
                current = self.get_projection(projection_key)
                if current is None:
                    raise JournalConflict("persisted event has no corresponding projection")
                return current
            current = self.get_projection(projection_key)
            if (0 if current is None else current.revision) != expected_revision:
                raise RevisionConflict("projection revision differs from expected revision")
            revision = expected_revision + 1
            self._db.execute(
                "INSERT INTO projections VALUES(?,?,?,?,?) ON CONFLICT(account,"
                "environment,projection_key) DO UPDATE SET revision=excluded.revision,"
                "payload=excluded.payload",
                (*self._scope, projection_key, revision, projection_json),
            )
            self._db.execute(
                "INSERT INTO inbox VALUES(?,?,?,?,?,?,?)",
                (*self._scope, event_id, event_json, projection_key, revision, projection_json),
            )
            self._audit(
                None,
                "APPLY_EVENT",
                {"event_id": event_id, "projection_key": projection_key, "revision": revision},
            )
        return ProjectionRecord(projection_key, revision, projection_json)
