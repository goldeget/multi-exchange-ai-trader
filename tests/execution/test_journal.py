from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest

from multi_exchange_trader.execution.journal import (
    CommandJournal,
    CommandKind,
    CommandState,
    ExecutionScope,
    JournalConflict,
    Reservation,
    ReservationLimitExceeded,
    ReservationLimits,
    RevisionConflict,
)


def journal(tmp_path: Path, account: str = "account-a") -> CommandJournal:
    return CommandJournal(tmp_path / "journal.sqlite", ExecutionScope(account, "DEMO"))


def test_replay_is_durable_and_conflicting_payload_fails(tmp_path: Path) -> None:
    first = journal(tmp_path).register("entry-1", CommandKind.ENTRY, {"quantity": Decimal("1.00")})
    replay = journal(tmp_path).register("entry-1", CommandKind.ENTRY, {"quantity": Decimal(1)})
    assert first == replay
    assert len(first.client_order_id) <= 36
    with pytest.raises(JournalConflict):
        journal(tmp_path).register("entry-1", CommandKind.ENTRY, {"quantity": "2"})
    assert len(journal(tmp_path).audit_events()) == 1


def test_scope_is_explicit_and_separated(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ExecutionScope("account", "MAINNET")
    one = journal(tmp_path).register("entry-1", CommandKind.ENTRY, {})
    two = journal(tmp_path, "account-b").register("entry-1", CommandKind.ENTRY, {})
    live = CommandJournal(tmp_path / "journal.sqlite", ExecutionScope("account-a", "LIVE"))
    assert live.get("entry-1") is None
    assert one.client_order_id != two.client_order_id


def test_only_one_connection_can_claim(tmp_path: Path) -> None:
    journal(tmp_path).register("entry", CommandKind.ENTRY, {})

    def claim() -> bool:
        with journal(tmp_path) as connection:
            return connection.claim("entry") is not None

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: claim(), range(2))) == [False, True]
    assert journal(tmp_path).get("entry").state is CommandState.SENDING  # type: ignore[union-attr]


def test_recovery_is_explicit_and_unknown_never_reclaims(tmp_path: Path) -> None:
    first = journal(tmp_path)
    first.register("entry", CommandKind.ENTRY, {})
    assert first.claim("entry") is not None
    reopened = journal(tmp_path)
    assert reopened.get("entry").state is CommandState.SENDING  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        reopened.recover_sending(exclusive_owner=False)
    recovered = reopened.recover_sending(exclusive_owner=True)
    assert recovered[0].state is CommandState.UNKNOWN
    assert reopened.claim("entry") is None
    assert [event.action for event in reopened.audit_events()] == [
        "REGISTER",
        "CLAIM",
        "RECOVER_UNKNOWN",
    ]
    assert reopened.acknowledge("entry", "same ID found").state is CommandState.ACKNOWLEDGED
    assert reopened.claim("entry") is None


def test_failed_claim_transaction_never_grants_claim(tmp_path: Path) -> None:
    instance = journal(tmp_path)
    instance.register("entry", CommandKind.ENTRY, {})
    with sqlite3.connect(tmp_path / "journal.sqlite") as connection:
        connection.execute(
            "CREATE TRIGGER fail_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT, 'disk failure'); END"
        )
    with pytest.raises(sqlite3.DatabaseError):
        instance.claim("entry")
    assert instance.get("entry").state is CommandState.PENDING  # type: ignore[union-attr]


def limits() -> ReservationLimits:
    return ReservationLimits(
        Decimal(10), Decimal(100), Decimal(30), 1, Decimal(0), Decimal(0), Decimal(0), 0
    )


def test_competing_reservations_are_atomic_and_unknown_retains_capacity(tmp_path: Path) -> None:
    def reserve(key: str) -> bool:
        with journal(tmp_path) as connection:
            try:
                connection.register(
                    key,
                    CommandKind.ENTRY,
                    {},
                    reservation=Reservation(Decimal(6), Decimal(60), Decimal(20)),
                    limits=limits(),
                )
                return True
            except ReservationLimitExceeded:
                return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(reserve, ("a", "b"))) == [False, True]
    connection = journal(tmp_path)
    key = connection.list_commands()[0].command_key
    connection.claim(key)
    connection.mark_unknown(key, "timeout")
    with pytest.raises(JournalConflict):
        connection.release_reservation(key, confirmed_terminal=True, terminal_evidence="not enough")
    connection.acknowledge(key, "found")
    with pytest.raises(ReservationLimitExceeded):
        connection.register(
            "third",
            CommandKind.ENTRY,
            {},
            reservation=Reservation(Decimal(6), Decimal(60), Decimal(20)),
            limits=limits(),
        )
    connection.release_reservation(
        key, confirmed_terminal=True, terminal_evidence="verified terminal"
    )
    assert reserve("fourth")


@pytest.mark.parametrize(
    "payload",
    [
        {"bad": float("nan")},
        {"bad": Decimal("Infinity")},
        {"api_key": "private"},
        {"nested": "x" * 65537},
    ],
)
def test_unsafe_payloads_are_rejected(tmp_path: Path, payload: object) -> None:
    with pytest.raises(ValueError):
        journal(tmp_path).register("unsafe", CommandKind.ENTRY, payload)
    assert journal(tmp_path).list_commands() == ()


def test_audit_cannot_be_modified_or_deleted(tmp_path: Path) -> None:
    journal(tmp_path).register("entry", CommandKind.ENTRY, {})
    with sqlite3.connect(tmp_path / "journal.sqlite") as connection:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("DELETE FROM audit")
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("UPDATE audit SET action = 'ERASE'")


def test_event_inbox_and_projection_commit_atomically_with_cas(tmp_path: Path) -> None:
    first, second = journal(tmp_path), journal(tmp_path)
    applied = first.apply_event("fill-1", {"qty": "1"}, "position", 0, {"owned": "1"})
    assert applied.revision == 1
    assert second.apply_event("fill-1", {"qty": "1"}, "position", 0, {"owned": "1"}) == applied
    with pytest.raises(JournalConflict):
        second.apply_event("fill-1", {"qty": "2"}, "position", 1, {"owned": "2"})
    with pytest.raises(RevisionConflict):
        second.apply_event("fill-2", {"qty": "1"}, "position", 0, {"owned": "2"})
    assert first.get_projection("position") == applied
    assert second.apply_event("fill-2", {"qty": "1"}, "position", 1, {"owned": "2"}).revision == 2


def test_actual_commit_failure_rolls_back_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_connect = sqlite3.connect

    class FailedCommit(sqlite3.Connection):
        fail_commit = False

        def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:
            if self.fail_commit and sql == "COMMIT":
                raise sqlite3.OperationalError("simulated failed durable commit")
            return super().execute(sql, parameters)  # type: ignore[arg-type]

    database = real_connect(tmp_path / "journal.sqlite", isolation_level=None, factory=FailedCommit)
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: database)
    instance = journal(tmp_path)
    instance.register("entry", CommandKind.ENTRY, {})
    database.fail_commit = True
    with pytest.raises(sqlite3.OperationalError):
        instance.claim("entry")
    assert instance.get("entry").state is CommandState.PENDING  # type: ignore[union-attr]
    assert [event.action for event in instance.audit_events()] == ["REGISTER"]


def test_reservations_compare_exact_decimals_without_context_rounding(tmp_path: Path) -> None:
    instance = journal(tmp_path)
    capacity = ReservationLimits(
        Decimal(1), Decimal(1), Decimal(1), 10, Decimal(1), Decimal(0), Decimal(0), 0
    )
    with pytest.raises(ReservationLimitExceeded):
        instance.register(
            "tiny-excess",
            CommandKind.ENTRY,
            {},
            reservation=Reservation(Decimal("1E-40"), Decimal(0), Decimal(0)),
            limits=capacity,
        )
    assert instance.get("tiny-excess") is None


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal(-1), True])
def test_reservation_requires_finite_nonnegative_decimal(value: Decimal) -> None:
    with pytest.raises(ValueError):
        Reservation(value, Decimal(0), Decimal(0))


def test_event_audit_failure_rolls_back_projection_and_inbox(tmp_path: Path) -> None:
    instance = journal(tmp_path)
    with sqlite3.connect(tmp_path / "journal.sqlite") as connection:
        connection.execute(
            "CREATE TRIGGER fail_event BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT, 'disk failure'); END"
        )
    with pytest.raises(sqlite3.DatabaseError):
        instance.apply_event("fill", {}, "position", 0, {"owned": "1"})
    assert instance.get_projection("position") is None
    with sqlite3.connect(tmp_path / "journal.sqlite") as connection:
        connection.execute("DROP TRIGGER fail_event")
    assert instance.apply_event("fill", {}, "position", 0, {"owned": "1"}).revision == 1


def test_late_duplicate_event_does_not_rewind_newer_projection(tmp_path: Path) -> None:
    instance = journal(tmp_path)
    instance.apply_event("fill-1", {"qty": "1"}, "position", 0, {"owned": "1"})
    instance.apply_event("fill-2", {"qty": "2"}, "position", 1, {"owned": "3"})
    replay = instance.apply_event("fill-1", {"qty": "1"}, "position", 0, {"owned": "1"})
    assert replay.revision == 2
    current = instance.get_projection("position")
    assert current is not None
    assert (current.revision, current.payload_json) == (2, '{"owned":"3"}')


def test_excessive_decimal_scale_is_rejected_before_formatting(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        journal(tmp_path).register("tiny", CommandKind.ENTRY, {"value": Decimal("1.00001E-1000")})


def test_negative_or_boolean_counts_cannot_expand_reservations() -> None:
    with pytest.raises(ValueError):
        ReservationLimits(
            Decimal(1), Decimal(1), Decimal(1), True, Decimal(0), Decimal(0), Decimal(0), 0
        )


def test_untyped_command_kind_and_non_string_json_keys_are_rejected(tmp_path: Path) -> None:
    instance = journal(tmp_path)
    with pytest.raises(TypeError):
        instance.register("entry", "ENTRY", {})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        instance.register("entry", CommandKind.ENTRY, {1: "bad"})


def test_duplicate_event_cannot_change_projection_key(tmp_path: Path) -> None:
    instance = journal(tmp_path)
    instance.apply_event("fill", {"qty": "1"}, "position", 0, {"owned": "1"})
    with pytest.raises(JournalConflict):
        instance.apply_event("fill", {"qty": "1"}, "other-position", 0, {"owned": "1"})
    assert instance.get_projection("other-position") is None


def test_duplicate_event_returns_current_projection_without_applying_again(tmp_path: Path) -> None:
    instance = journal(tmp_path)
    instance.apply_event("fill-1", {"qty": "1"}, "position", 0, {"owned": "1"})
    instance.apply_event("fill-2", {"qty": "2"}, "position", 1, {"owned": "3"})
    result = instance.apply_event("fill-1", {"qty": "1"}, "position", 2, {"owned": "4"})
    assert (result.revision, result.payload_json) == (2, '{"owned":"3"}')
    assert len(instance.audit_events()) == 2


def test_transient_first_open_wal_contention_preserves_durable_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BusyFirstWal(sqlite3.Connection):
        busy = True

        def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:
            if self.busy and sql == "PRAGMA journal_mode=WAL":
                self.busy = False
                error = sqlite3.OperationalError("database is locked")
                error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                raise error
            return super().execute(sql, parameters)  # type: ignore[arg-type]

    database = sqlite3.connect(
        tmp_path / "journal.sqlite", isolation_level=None, factory=BusyFirstWal
    )
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: database)
    with journal(tmp_path) as instance:
        registered = instance.register("entry", CommandKind.ENTRY, {})
        assert registered.state is CommandState.PENDING
        assert database.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
