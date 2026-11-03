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
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from multi_exchange_trader.execution.reservations_v2 import (
        ReservationAdmissionV2,
        ReservationV2,
    )
    from multi_exchange_trader.execution.risk_v2_codec import DurableEntryIntentV2
    from multi_exchange_trader.execution.risk_v2_contracts import (
        LinearReservationSnapshotV2,
        RiskScopeV2,
    )


class CommandKind(StrEnum):
    ENTRY = "ENTRY"
    PROTECTION = "PROTECTION"
    EXIT = "EXIT"
    CANCEL = "CANCEL"
    LEVERAGE = "LEVERAGE"


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
    reservation_v2: ReservationV2 | None = None


class _OwnPendingViewV2:
    """Identity-only local handle; construction alone proves nothing."""

    __slots__ = ()


@dataclass(frozen=True)
class _OwnPendingDataV2:
    scope: RiskScopeV2
    record: CommandRecord
    intent: DurableEntryIntentV2
    reservation: ReservationV2
    command_identity_sha256: str
    full: LinearReservationSnapshotV2
    evaluation: LinearReservationSnapshotV2


def _own_pending_record_witness_v2(record: CommandRecord) -> tuple[object, ...]:
    """Detached exact values, including legacy decimals and phase absent from held hash."""
    from multi_exchange_trader.execution import reservations_v2 as v2
    from multi_exchange_trader.execution import risk_v2_contracts as c

    values = c._fields_v2(record, CommandRecord)
    if (
        any(type(values[name]) is not str for name in (
            "command_key", "client_order_id", "payload_json", "payload_hash"
        ))
        or type(values["kind"]) is not CommandKind
        or type(values["state"]) is not CommandState
        or type(values["terminal_confirmed"]) is not bool
    ):
        c._reject_v2()
    legacy = None
    if record.reservation is not None:
        amounts = c._fields_v2(record.reservation, Reservation)
        legacy_parts = []
        for name in ("risk_amount", "notional_amount", "margin_amount"):
            amount = amounts[name]
            if type(amount) is not Decimal:
                c._reject_v2()
            _amount(amount)
            legacy_parts.append(str(amount))
        legacy = tuple(legacy_parts)
    return (
        record.command_key,
        record.client_order_id,
        record.kind.value,
        record.payload_json,
        record.payload_hash,
        record.state.value,
        legacy,
        record.terminal_confirmed,
        None if record.reservation_v2 is None
        else c._json_text_v2(v2._reservation_payload(record.reservation_v2)),
    )


def _own_pending_data_witness_v2(data: _OwnPendingDataV2) -> tuple[object, ...]:
    """Use existing closed payload recipes, never mutable DTO equality as a baseline."""
    from multi_exchange_trader.execution import reservations_v2 as v2
    from multi_exchange_trader.execution import risk_v2_contracts as c
    from multi_exchange_trader.execution.risk_v2_codec import encode_entry_intent_v2

    c._fields_v2(data, _OwnPendingDataV2)
    return (
        c._json_text_v2(c._scope_payload_v2(data.scope)),
        _own_pending_record_witness_v2(data.record),
        encode_entry_intent_v2(data.intent),
        c._json_text_v2(v2._reservation_payload(data.reservation)),
        c._digest_v2(data.command_identity_sha256),
        c._json_text_v2(c._reservation_snapshot_payload_v2(data.full)),
        c._json_text_v2(c._reservation_snapshot_payload_v2(data.evaluation)),
    )


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

_V2_TABLES = {
    "reservation_meta_v2": (
        "account TEXT NOT NULL, environment TEXT NOT NULL, schema_version INTEGER NOT NULL, "
        "capacity_revision INTEGER NOT NULL, owner_epoch TEXT NOT NULL, "
        "PRIMARY KEY(account, environment)"
    ),
    "entry_reservations_v2": (
        "account TEXT NOT NULL, environment TEXT NOT NULL, command_key TEXT NOT NULL, "
        "version INTEGER NOT NULL, instrument_venue TEXT NOT NULL, "
        "instrument_product TEXT NOT NULL, instrument_symbol TEXT NOT NULL, "
        "envelope_sha256 TEXT NOT NULL, base_quantity TEXT NOT NULL, "
        "risk_amount TEXT NOT NULL, margin_amount TEXT NOT NULL, "
        "PRIMARY KEY(account, environment, command_key)"
    ),
}


class CommandJournal:
    def __init__(self, path: Path, scope: ExecutionScope) -> None:
        self.scope = scope
        self._scope = (scope.account_id, scope.environment)
        self._own_pending_issuer_v2: CommandJournal = self
        self._own_pending_closed_v2 = False
        self._own_pending_slot_v2: tuple[
            _OwnPendingViewV2, _OwnPendingDataV2, tuple[object, ...], tuple[object, ...]
        ] | None = None
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
        self._own_pending_slot_v2 = None
        self._own_pending_closed_v2 = True
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

    def _record(self, row: sqlite3.Row) -> CommandRecord:
        from multi_exchange_trader.execution import reservations_v2 as v2
        from multi_exchange_trader.execution import risk_v2_contracts as c
        from multi_exchange_trader.execution.protection import ProtectionScope
        from multi_exchange_trader.execution.risk_v2_codec import (
            decode_entry_intent_v2,
            encode_entry_intent_v2,
        )
        from multi_exchange_trader.trading_product import InstrumentKey

        stored = None
        if self._v2_schema():
            stored = self._db.execute(
                "SELECT * FROM entry_reservations_v2 WHERE account=? AND environment=? "
                "AND command_key=?",
                (*self._scope, row["command_key"]),
            ).fetchone()
        if stored is not None:
            self._v2_meta(required=True)
            text_fields = (
                "account",
                "environment",
                "command_key",
                "instrument_venue",
                "instrument_product",
                "instrument_symbol",
                "envelope_sha256",
                "base_quantity",
                "risk_amount",
                "margin_amount",
            )
            if (
                any(type(stored[name]) is not str for name in text_fields)
                or type(stored["version"]) is not int
                or stored["version"] != 2
                or type(row["terminal"]) is not int
                or row["terminal"] not in (0, 1)
                or row["kind"] != "ENTRY"
                or any(row[name] is not None for name in ("risk", "notional", "margin"))
                or any(
                    type(row[name]) is not str
                    for name in ("command_key", "client_id", "payload", "payload_hash", "state")
                )
            ):
                c._reject_v2()
            try:
                reservation_v2 = v2.ReservationV2(
                    2,
                    InstrumentKey(
                        stored["instrument_venue"],
                        stored["instrument_product"],
                        stored["instrument_symbol"],
                    ),
                    stored["envelope_sha256"],
                    Decimal(stored["base_quantity"]),
                    Decimal(stored["risk_amount"]),
                    Decimal(stored["margin_amount"]),
                )
                state = CommandState(row["state"])
            except (ValueError, ArithmeticError):
                c._reject_v2()
            try:
                intent = decode_entry_intent_v2(row["payload"])
            except c.RiskV2Rejected:
                c._reject_v2()
            payload = v2._reservation_payload(reservation_v2)
            if any(
                payload[name] != stored[name]
                for name in ("base_quantity", "risk_amount", "margin_amount")
            ):
                c._reject_v2()
            c._digest_v2(row["payload_hash"])
            if (
                encode_entry_intent_v2(intent) != row["payload"]
                or hashlib.sha256(row["payload"].encode("utf-8")).hexdigest() != row["payload_hash"]
                or row["client_id"] != self._client_order_id(row["command_key"])
            ):
                c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
            v2.entry_command_identity_sha256_v2(
                ProtectionScope(*self._scope), row["command_key"], intent, reservation_v2
            )
            return CommandRecord(
                row["command_key"],
                row["client_id"],
                CommandKind.ENTRY,
                row["payload"],
                row["payload_hash"],
                state,
                None,
                bool(row["terminal"]),
                reservation_v2,
            )
        if row["kind"] == "ENTRY":
            # The discriminator is reserved independently of wire_version. A V2
            # row may never silently downgrade to an unreserved legacy command.
            try:
                frame = json.loads(row["payload"])
            except (TypeError, ValueError):
                frame = None
            if type(frame) is dict and frame.get("schema") == "RISK_V2_DURABLE_INTENT":
                c._reject_v2()
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
        self._v2_orphans()
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
        self._v2_orphans()
        rows = self._db.execute(
            "SELECT * FROM commands WHERE account=? AND environment=? ORDER BY command_key",
            self._scope,
        ).fetchall()
        records = tuple(self._record(row) for row in rows)
        return tuple(record for record in records if states is None or record.state in states)

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
                if existing.reservation_v2 is not None:
                    from multi_exchange_trader.execution import risk_v2_contracts as c

                    c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
                if (existing.kind, existing.payload_json, existing.reservation) != (
                    kind,
                    canonical,
                    reservation,
                ):
                    raise JournalConflict("command identity has incompatible immutable data")
                return existing
            if (reservation is not None or kind is CommandKind.ENTRY) and self._v2_has_holds():
                from multi_exchange_trader.execution import risk_v2_contracts as c

                c._reject_v2("RISK_V2_LEGACY_RESERVATION_RECONCILIATION_REQUIRED")
            if reservation is not None:
                if limits is None:
                    raise ValueError("explicit limits are required for reservations")
                self._check_capacity(reservation, limits)
            client_id = self._client_order_id(command_key)
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
        self._own_pending_slot_v2 = None
        with self._transaction():
            record = self._required(command_key)
            if record.reservation_v2 is not None:
                from multi_exchange_trader.execution import risk_v2_contracts as c

                c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
            if (
                record.reservation is not None
                or (record.kind is CommandKind.ENTRY and not record.terminal_confirmed)
            ) and self._v2_has_holds():
                from multi_exchange_trader.execution import risk_v2_contracts as c

                c._reject_v2("RISK_V2_LEGACY_RESERVATION_RECONCILIATION_REQUIRED")
            if record.state is not CommandState.PENDING:
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
                if record.reservation_v2 is not None:
                    self._v2_increment()
                self._db.execute(
                    "UPDATE commands SET terminal=1 WHERE account=? AND environment=? "
                    "AND command_key=?",
                    (*self._scope, command_key),
                )
                self._audit(command_key, "RELEASE", {"evidence": terminal_evidence})
            result = self._required(command_key)
        return result

    def _client_order_id(self, command_key: str) -> str:
        return (
            "ec_"
            + hashlib.sha256(_canonical([*self._scope, command_key]).encode()).hexdigest()[:32]
        )

    def _v2_schema(self, *, create: bool = False) -> bool:
        """Recognize only our exact two-table schema; never repair an unknown DB."""
        found = self._db.execute(
            "SELECT name,type,sql FROM sqlite_master WHERE name IN (?,?)",
            tuple(_V2_TABLES),
        ).fetchall()
        if not found:
            if not create:
                return False
            for name, columns in _V2_TABLES.items():
                self._db.execute(f"CREATE TABLE {name} ({columns})")
            return True
        from multi_exchange_trader.execution import risk_v2_contracts as c

        if len(found) != 2:
            c._reject_v2()
        for row in found:
            expected = f"CREATE TABLE {row['name']} ({_V2_TABLES[row['name']]})"
            if row["type"] != "table" or "".join(row["sql"].split()) != "".join(expected.split()):
                c._reject_v2()
        return True

    def _v2_orphans(self) -> None:
        if not self._v2_schema():
            return
        from multi_exchange_trader.execution import risk_v2_contracts as c

        if (
            self._db.execute(
                "SELECT 1 FROM entry_reservations_v2 r LEFT JOIN commands c "
                "ON c.account=r.account AND c.environment=r.environment "
                "AND c.command_key=r.command_key WHERE r.account=? AND r.environment=? "
                "AND c.command_key IS NULL LIMIT 1",
                self._scope,
            ).fetchone()
            is not None
        ):
            c._reject_v2()
        self._v2_meta(required=False)

    def _v2_meta(self, *, required: bool) -> tuple[str, int] | None:
        from multi_exchange_trader.execution import reservations_v2 as v2
        from multi_exchange_trader.execution import risk_v2_contracts as c

        if not self._v2_schema():
            if required:
                c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
            return None
        row = self._db.execute(
            "SELECT * FROM reservation_meta_v2 WHERE account=? AND environment=?", self._scope
        ).fetchone()
        if row is None:
            if (
                self._db.execute(
                    "SELECT 1 FROM entry_reservations_v2 WHERE account=? AND environment=? LIMIT 1",
                    self._scope,
                ).fetchone()
                is not None
            ):
                c._reject_v2()
            if required:
                c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
            return None
        if type(row["schema_version"]) is not int or row["schema_version"] != 2:
            c._reject_v2()
        return c._identifier_v2(row["owner_epoch"]), v2._revision(row["capacity_revision"])

    def _v2_increment(self) -> int:
        from multi_exchange_trader.execution import reservations_v2 as v2

        meta = self._v2_meta(required=True)
        assert meta is not None
        revision = v2._revision(meta[1], increment=True)
        self._db.execute(
            "UPDATE reservation_meta_v2 SET capacity_revision=? WHERE account=? AND environment=?",
            (revision, *self._scope),
        )
        return revision

    def _v2_has_holds(self) -> bool:
        return any(
            record.reservation_v2 is not None and not record.terminal_confirmed
            for record in self.list_commands()
        )

    def _v2_checked_records(self) -> tuple[CommandRecord, ...]:
        from multi_exchange_trader.execution import risk_v2_contracts as c

        records = self.list_commands()
        if any(
            not record.terminal_confirmed
            and record.reservation_v2 is None
            and (record.reservation is not None or record.kind is CommandKind.ENTRY)
            for record in records
        ):
            c._reject_v2("RISK_V2_LEGACY_RESERVATION_RECONCILIATION_REQUIRED")
        return records

    def bind_owner_epoch_v2(
        self,
        owner_epoch: str,
        *,
        expected_previous_owner_epoch: str | None,
        expected_journal_revision: int,
    ) -> int:
        """Local CAS only; caller must separately establish real exclusive ownership."""
        from multi_exchange_trader.execution import reservations_v2 as v2
        from multi_exchange_trader.execution import risk_v2_contracts as c
        from multi_exchange_trader.execution.protection import ProtectionScope

        c._account_payload_v2(ProtectionScope(*self._scope))
        c._identifier_v2(owner_epoch)
        if expected_previous_owner_epoch is not None:
            c._identifier_v2(expected_previous_owner_epoch)
        v2._revision(expected_journal_revision)
        with self._transaction():
            self._v2_schema(create=True)
            self._v2_orphans()
            # Validate retained identities even if no capacity will change.
            self.list_commands()
            meta = self._v2_meta(required=False)
            if (None if meta is None else meta[0], 0 if meta is None else meta[1]) != (
                expected_previous_owner_epoch,
                expected_journal_revision,
            ):
                c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
            if meta is not None and meta[0] == owner_epoch:
                return meta[1]
            revision = v2._revision(0 if meta is None else meta[1], increment=True)
            if meta is None:
                self._db.execute(
                    "INSERT INTO reservation_meta_v2 VALUES(?,?,?,?,?)",
                    (*self._scope, 2, revision, owner_epoch),
                )
            else:
                self._db.execute(
                    "UPDATE reservation_meta_v2 SET owner_epoch=?,capacity_revision=? "
                    "WHERE account=? AND environment=?",
                    (owner_epoch, revision, *self._scope),
                )
            self._audit(None, "BIND_OWNER_EPOCH_V2", {"capacity_revision": revision})
        return revision

    def reservation_snapshot_v2(
        self,
        scope: RiskScopeV2,
        *,
        now: datetime,
        owner_generation: str,
        exclude: str | None,
    ) -> LinearReservationSnapshotV2:
        from multi_exchange_trader.execution import reservations_v2 as v2
        from multi_exchange_trader.execution import risk_v2_contracts as c
        from multi_exchange_trader.execution.protection import ProtectionScope

        c._scope_payload_v2(scope)
        c._identifier_v2(owner_generation)
        c._utc_v2(now)
        account = ProtectionScope(*self._scope)
        if c._account_payload_v2(account) != c._account_payload_v2(scope.product_scope.account):
            c._reject_v2("RISK_V2_SCOPE_MISMATCH")
        with self._transaction():
            records = self._v2_checked_records()
            meta = self._v2_meta(required=True)
            assert meta is not None
            if meta[0] != owner_generation:
                c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
            result = v2._snapshot(
                account, records, owner_epoch=meta[0], revision=meta[1], now=now, exclude=exclude
            )
        return result

    def _own_pending_rows_witness_v2(
        self, records: tuple[CommandRecord, ...], meta: tuple[str, int]
    ) -> tuple[object, ...]:
        # Raw SQLite primitives also retain storage representation and terminal
        # integers that legacy decoding may otherwise normalize. No new hash or
        # journal schema is introduced by this in-memory comparison witness.
        rows = []
        for table, order in (
            ("commands", "command_key"),
            ("entry_reservations_v2", "command_key"),
            ("reservation_meta_v2", "account"),
        ):
            stored = self._db.execute(
                f"SELECT * FROM {table} WHERE account=? AND environment=? ORDER BY {order}",
                self._scope,
            ).fetchall()
            rows.append(tuple(tuple((type(value), value) for value in row) for row in stored))
        return (
            self._scope,
            meta,
            tuple(_own_pending_record_witness_v2(record) for record in records),
            tuple(rows),
        )

    def _own_pending_data_v2(
        self,
        scope: RiskScopeV2,
        *,
        command_key: str,
        owner_epoch: str,
        expected_command_identity_sha256: str,
        now: datetime,
        records: tuple[CommandRecord, ...],
        meta: tuple[str, int],
    ) -> _OwnPendingDataV2:
        from multi_exchange_trader.execution import reservations_v2 as v2
        from multi_exchange_trader.execution import risk_v2_contracts as c
        from multi_exchange_trader.execution.protection import ProtectionScope
        from multi_exchange_trader.execution.risk_v2_codec import decode_entry_intent_v2

        record = next((item for item in records if item.command_key == command_key), None)
        if (
            meta[0] != owner_epoch
            or record is None
            or record.kind is not CommandKind.ENTRY
            or record.state is not CommandState.PENDING
            or record.terminal_confirmed
            or record.reservation is not None
            or record.reservation_v2 is None
        ):
            c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
        intent = decode_entry_intent_v2(record.payload_json)
        account = ProtectionScope(*self._scope)
        if c._scope_payload_v2(scope) != c._scope_payload_v2(intent.envelope.scope):
            c._reject_v2("RISK_V2_SCOPE_MISMATCH")
        identity = v2.entry_command_identity_sha256_v2(
            account, command_key, intent, record.reservation_v2
        )
        if (
            identity != expected_command_identity_sha256
            or record.client_order_id != self._client_order_id(command_key)
            or not intent.assessment.assessed_at <= now < intent.envelope.valid_until
        ):
            c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
        full = v2._snapshot(
            account, records, owner_epoch=meta[0], revision=meta[1], now=now, exclude=None
        )
        evaluation = v2._snapshot(
            account, records, owner_epoch=meta[0], revision=meta[1], now=now, exclude=command_key
        )
        return _OwnPendingDataV2(
            scope, record, intent, record.reservation_v2, identity, full, evaluation
        )

    def _own_pending_view_v2(
        self,
        scope: RiskScopeV2,
        *,
        command_key: str,
        owner_epoch: str,
        expected_command_identity_sha256: str,
        now: datetime,
    ) -> _OwnPendingViewV2:
        """Capture one true local PENDING pair; no lease, source or financial authority."""
        from multi_exchange_trader.execution import risk_v2_contracts as c

        issuer: object = getattr(self, "_own_pending_issuer_v2", None)
        if issuer is not self:
            c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
        # Even a failed/equal-value recapture cannot leave an older handle current.
        self._own_pending_slot_v2 = None
        if type(self) is not CommandJournal or self._own_pending_closed_v2:
            c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
        c._scope_payload_v2(scope)
        c._identifier_v2(command_key)
        c._identifier_v2(owner_epoch)
        c._digest_v2(expected_command_identity_sha256)
        now = c._utc_v2(now)
        with self._transaction():
            records = self._v2_checked_records()
            meta = self._v2_meta(required=True)
            assert meta is not None
            data = self._own_pending_data_v2(
                scope, command_key=command_key, owner_epoch=owner_epoch,
                expected_command_identity_sha256=expected_command_identity_sha256,
                now=now, records=records, meta=meta,
            )
            rows_witness = self._own_pending_rows_witness_v2(records, meta)
            data_witness = _own_pending_data_witness_v2(data)
        view = _OwnPendingViewV2()
        self._own_pending_slot_v2 = (view, data, rows_witness, data_witness)
        return view

    def _require_own_pending_view_v2(
        self,
        view: _OwnPendingViewV2,
        *,
        scope: RiskScopeV2,
        command_key: str,
        expected_command_identity_sha256: str,
        now: datetime,
    ) -> _OwnPendingDataV2:
        """Replay the original pair and every checked row; never rebaseline a DTO alias."""
        from multi_exchange_trader.execution import risk_v2_contracts as c

        try:
            issuer: object = getattr(self, "_own_pending_issuer_v2", None)
            if issuer is not self:
                c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
            slot = self._own_pending_slot_v2
            if (
                type(self) is not CommandJournal
                or self._own_pending_closed_v2
                or type(view) is not _OwnPendingViewV2
                or slot is None
                or view is not slot[0]
            ):
                c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
            _, data, rows_witness, data_witness = slot
            c._scope_payload_v2(scope)
            c._identifier_v2(command_key)
            c._digest_v2(expected_command_identity_sha256)
            now = c._utc_v2(now)
            if _own_pending_data_witness_v2(data) != data_witness:
                c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
            if c._scope_payload_v2(scope) != c._scope_payload_v2(data.scope):
                c._reject_v2("RISK_V2_SCOPE_MISMATCH")
            if (
                command_key != data.record.command_key
                or expected_command_identity_sha256 != data.command_identity_sha256
                or not data.full.observed_at <= now < data.intent.envelope.valid_until
            ):
                c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
            with self._transaction():
                records = self._v2_checked_records()
                meta = self._v2_meta(required=True)
                assert meta is not None
                if self._own_pending_rows_witness_v2(records, meta) != rows_witness:
                    c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
                replay = self._own_pending_data_v2(
                    scope, command_key=command_key, owner_epoch=data.full.owner_generation,
                    expected_command_identity_sha256=expected_command_identity_sha256,
                    now=data.full.observed_at, records=records, meta=meta,
                )
                if _own_pending_data_witness_v2(replay) != data_witness:
                    c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
            return data
        except BaseException:
            # A failed require is conservatively terminal for this one local slot.
            self._own_pending_slot_v2 = None
            raise

    def _v2_pins(
        self,
        records: tuple[CommandRecord, ...],
        owner_epoch: str,
        expected_journal_revision: int,
        expected_held_quantity_sha256: str,
    ) -> tuple[str, int]:
        from multi_exchange_trader.execution import reservations_v2 as v2
        from multi_exchange_trader.execution import risk_v2_contracts as c
        from multi_exchange_trader.execution.protection import ProtectionScope

        c._identifier_v2(owner_epoch)
        v2._revision(expected_journal_revision)
        c._digest_v2(expected_held_quantity_sha256)
        meta = self._v2_meta(required=True)
        assert meta is not None
        digest = v2._held_hash(ProtectionScope(*self._scope), records)
        if (*meta, digest) != (
            owner_epoch,
            expected_journal_revision,
            expected_held_quantity_sha256,
        ):
            c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
        return meta

    def register_entry_v2(
        self,
        command_key: str,
        intent: DurableEntryIntentV2,
        *,
        reservation: ReservationV2,
        admission: ReservationAdmissionV2,
    ) -> CommandRecord:
        from multi_exchange_trader.execution import reservations_v2 as v2
        from multi_exchange_trader.execution import risk_v2_contracts as c
        from multi_exchange_trader.execution.protection import ProtectionScope
        from multi_exchange_trader.execution.risk_v2_codec import encode_entry_intent_v2
        from multi_exchange_trader.execution.risk_v2_math import decimal_text_v2

        v2._validate_admission(admission)
        account = ProtectionScope(*self._scope)
        if c._account_payload_v2(account) != c._account_payload_v2(admission.account_scope):
            c._reject_v2("RISK_V2_SCOPE_MISMATCH")
        identity = v2.entry_command_identity_sha256_v2(account, command_key, intent, reservation)
        if c._scope_payload_v2(intent.envelope.scope) != c._scope_payload_v2(
            admission.policy.scope
        ):
            c._reject_v2("RISK_V2_SCOPE_MISMATCH")
        wire = encode_entry_intent_v2(intent)
        digest = hashlib.sha256(wire.encode("utf-8")).hexdigest()
        with self._transaction():
            records = self._v2_checked_records()
            meta = self._v2_pins(
                records,
                admission.owner_epoch,
                admission.expected_journal_revision,
                admission.expected_held_quantity_sha256,
            )
            if identity != admission.expected_command_identity_sha256:
                c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
            existing = next((item for item in records if item.command_key == command_key), None)
            if existing is not None:
                if (
                    existing.reservation_v2 is None
                    or existing.payload_json != wire
                    or v2._reservation_payload(existing.reservation_v2)
                    != v2._reservation_payload(reservation)
                ):
                    c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
                return existing
            snapshot = v2._snapshot(
                account,
                records,
                owner_epoch=meta[0],
                revision=meta[1],
                now=admission.now,
                exclude=None,
            )
            v2._check_capacity(intent, reservation, admission, snapshot)
            revision = self._v2_increment()
            self._db.execute(
                "INSERT INTO commands(account,environment,command_key,client_id,kind,payload,"
                "payload_hash,state,risk,notional,margin) VALUES(?,?,?,?,?,?,?,?,NULL,NULL,NULL)",
                (
                    *self._scope,
                    command_key,
                    self._client_order_id(command_key),
                    "ENTRY",
                    wire,
                    digest,
                    CommandState.PENDING.value,
                ),
            )
            self._db.execute(
                "INSERT INTO entry_reservations_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    *self._scope,
                    command_key,
                    2,
                    reservation.instrument.venue,
                    reservation.instrument.product,
                    reservation.instrument.symbol,
                    reservation.envelope_sha256,
                    decimal_text_v2(reservation.base_quantity),
                    decimal_text_v2(reservation.risk_amount),
                    decimal_text_v2(reservation.margin_amount),
                ),
            )
            # Validate the prospective aggregate, not just a fitting pre-state.
            prospective = self._v2_checked_records()
            v2._snapshot(
                account,
                prospective,
                owner_epoch=meta[0],
                revision=revision,
                now=admission.now,
                exclude=None,
            )
            self._audit(
                command_key,
                "REGISTER_ENTRY_V2",
                {"payload_hash": digest, "capacity_revision": revision},
            )
            result = self._required(command_key)
        return result

    def claim_entry_v2(
        self,
        command_key: str,
        *,
        owner_epoch: str,
        expected_journal_revision: int,
        expected_held_quantity_sha256: str,
        expected_command_identity_sha256: str,
    ) -> CommandRecord | None:
        from multi_exchange_trader.execution import reservations_v2 as v2
        from multi_exchange_trader.execution import risk_v2_contracts as c
        from multi_exchange_trader.execution.protection import ProtectionScope
        from multi_exchange_trader.execution.risk_v2_codec import decode_entry_intent_v2

        self._own_pending_slot_v2 = None
        c._identifier_v2(command_key)
        c._identifier_v2(owner_epoch)
        v2._revision(expected_journal_revision)
        c._digest_v2(expected_held_quantity_sha256)
        c._digest_v2(expected_command_identity_sha256)
        with self._transaction():
            records = self._v2_checked_records()
            self._v2_pins(
                records, owner_epoch, expected_journal_revision, expected_held_quantity_sha256
            )
            record = next((item for item in records if item.command_key == command_key), None)
            if record is None or record.reservation_v2 is None:
                c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
            identity = v2.entry_command_identity_sha256_v2(
                ProtectionScope(*self._scope),
                command_key,
                decode_entry_intent_v2(record.payload_json),
                record.reservation_v2,
            )
            if identity != expected_command_identity_sha256:
                c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
            if record.state is not CommandState.PENDING or record.terminal_confirmed:
                return None
            changed = self._db.execute(
                "UPDATE commands SET state=? WHERE account=? AND environment=? AND command_key=? "
                "AND state=? AND terminal=0",
                (CommandState.SENDING.value, *self._scope, command_key, CommandState.PENDING.value),
            )
            if changed.rowcount != 1:
                c._reject_v2("RISK_V2_LOCAL_ADMISSION_CONFLICT")
            self._audit(command_key, "CLAIM_ENTRY_V2", {})
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
