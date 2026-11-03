"""Local original-claim facts only; synthetic archives grant no trading authority."""

import copy
import pickle
import sqlite3
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from test_own_pending_view_v2 import capture, identity, register_other
from test_reservations_v2 import SCOPE, bound, claim, fixture_values, register, snapshot
from test_risk_v2_codec import NOW

from multi_exchange_trader.execution import journal as j
from multi_exchange_trader.execution import risk_v2_contracts as c

CONFLICT = "RISK_V2_LOCAL_ADMISSION_CONFLICT"


def graph(tmp_path: Path, *, other: bool = False, legacy: bool = False) -> dict[str, Any]:
    journal = j.CommandJournal(tmp_path / "journal.db", SCOPE)
    bound(journal)
    intent = fixture_values()[0]
    register(journal, fixture_values())
    if other:
        register_other(journal)
    if legacy:
        journal.register("LEGACY", j.CommandKind.CANCEL, {"synthetic": "control"})
    view = capture(journal, intent)
    full = snapshot(journal, intent)
    claimed = claim(journal, intent)
    assert claimed is not None and claimed.state is j.CommandState.SENDING
    assert snapshot(journal, intent) == full
    return {"journal": journal, "intent": intent, "full": full, "claimed": claimed, "view": view}


def require(g: dict[str, Any], **changes: Any) -> None:
    owner = changes.pop("journal", g["journal"])
    method = getattr(owner, "_require_postclaim_entry_v2", None)
    assert callable(method), "original J must verify its exact successful claim return"
    args = {
        "claimed": g["claimed"], "expected_command_identity_sha256": identity(g["intent"]),
        "owner_epoch": "OWNER_1", "expected_journal_revision": g["full"].journal_revision,
        "expected_held_quantity_sha256": g["full"].held_quantity_sha256, "now": NOW,
    }
    args.update(changes)
    key = args.pop("command_key", g["intent"].command_key)
    assert method(key, **args) is None


def refusal(g: dict[str, Any], **changes: Any) -> None:
    with pytest.raises(c.RiskV2Rejected):
        require(g, **changes)


def test_original_successful_return_repeats_without_writes_and_retains_all_holds(
    tmp_path: Path,
) -> None:
    g = graph(tmp_path, other=True)
    journal = g["journal"]
    try:
        before, changes = tuple(journal._db.iterdump()), journal._db.total_changes
        assert g["full"].position_count == 2
        assert g["full"].risk_amount == Decimal(24)
        assert g["full"].margin_amount == Decimal(104)
        assert journal.get("COMMAND") == g["claimed"]
        assert journal.get("COMMAND") is not g["claimed"]
        require(g)
        require(g, now=NOW + timedelta(microseconds=1))
        assert tuple(journal._db.iterdump()) == before
        assert journal._db.total_changes == changes
        assert snapshot(journal, g["intent"]) == g["full"]
        with pytest.raises(c.RiskV2Rejected):
            journal._require_own_pending_view_v2(
                g["view"], scope=g["intent"].envelope.scope, command_key="COMMAND",
                expected_command_identity_sha256=identity(g["intent"]), now=NOW,
            )
        require(g)
    finally:
        journal.close()


@pytest.mark.parametrize("kind", ["get", "replace", "copy", "pickle", "object"])
def test_equal_or_fabricated_return_is_never_original(tmp_path: Path, kind: str) -> None:
    g = graph(tmp_path)
    try:
        original = g["claimed"]
        candidate = {
            "get": lambda: g["journal"].get("COMMAND"),
            "replace": lambda: replace(original), "copy": lambda: copy.copy(original),
            "pickle": lambda: pickle.loads(pickle.dumps(original)), "object": object,
        }[kind]()
        statements: list[str] = []
        g["journal"]._db.set_trace_callback(statements.append)
        refusal(g, claimed=candidate)
        assert statements == []
        refusal(g)
    finally:
        g["journal"]._db.set_trace_callback(None)
        g["journal"].close()


@pytest.mark.parametrize("kind", ["copy", "copy-of-copy", "no-init", "other", "reopened"])
def test_wrong_issuer_refuses_before_SQL_and_original_survives(tmp_path: Path, kind: str) -> None:
    g = graph(tmp_path)
    other = None
    try:
        if kind == "copy":
            wrong = copy.copy(g["journal"])
        elif kind == "copy-of-copy":
            wrong = copy.copy(copy.copy(g["journal"]))
        elif kind == "no-init":
            wrong = object.__new__(j.CommandJournal)
        else:
            path = tmp_path / ("journal.db" if kind == "reopened" else "other.db")
            other = wrong = j.CommandJournal(path, SCOPE)
        statements: list[str] = []
        g["journal"]._db.set_trace_callback(statements.append)
        refusal(g, journal=wrong)
        assert statements == []
        g["journal"]._db.set_trace_callback(None)
        require(g)
    finally:
        g["journal"]._db.set_trace_callback(None)
        if other is not None:
            other.close()
        g["journal"].close()


@pytest.mark.parametrize("name,bad", [
    ("command_key", "OTHER"), ("owner_epoch", "OWNER_2"),
    ("expected_journal_revision", 999), ("expected_held_quantity_sha256", "f" * 64),
    ("expected_command_identity_sha256", "f" * 64), ("command_key", object()),
    ("owner_epoch", object()), ("expected_journal_revision", True),
    ("expected_held_quantity_sha256", object()), ("now", object()),
])
def test_wrong_or_malformed_original_pins_retire_before_SQL(
    tmp_path: Path, name: str, bad: Any,
) -> None:
    g = graph(tmp_path)
    try:
        statements: list[str] = []
        g["journal"]._db.set_trace_callback(statements.append)
        refusal(g, **{name: bad})
        assert statements == []
        refusal(g)
    finally:
        g["journal"]._db.set_trace_callback(None)
        g["journal"].close()


@pytest.mark.parametrize("kind", ["same-none", "failed", "malformed", "legacy", "legacy-failed"])
def test_every_next_claim_attempt_retires_without_releasing(
    tmp_path: Path, kind: str,
) -> None:
    g = graph(tmp_path, legacy=True)
    journal = g["journal"]
    try:
        if kind == "same-none":
            assert claim(journal, g["intent"]) is None
        elif kind == "failed":
            with pytest.raises(c.RiskV2Rejected):
                claim(journal, g["intent"], expected_command_identity_sha256="f" * 64)
        elif kind == "malformed":
            with pytest.raises(c.RiskV2Rejected):
                claim(journal, g["intent"], owner_epoch=True)
        elif kind == "legacy":
            assert journal.claim("LEGACY").state is j.CommandState.SENDING
        else:
            with pytest.raises(KeyError):
                journal.claim("MISSING")
        refusal(g)
        assert snapshot(journal, g["intent"]) == g["full"]
        assert journal.get("COMMAND").state is j.CommandState.SENDING
    finally:
        journal.close()


@pytest.mark.parametrize("kind", ["record", "reservation", "equal-reservation", "enum", "terminal"])
def test_retained_alias_mutation_restoration_does_not_revive(tmp_path: Path, kind: str) -> None:
    g = graph(tmp_path)
    target, name = g["claimed"], "payload_hash"
    if kind == "reservation":
        target, name = target.reservation_v2, "margin_amount"
        value: Any = Decimal("52.0")
    elif kind == "equal-reservation":
        name, value = "reservation_v2", replace(target.reservation_v2)
    elif kind == "enum":
        name, value = "state", "SENDING"
    elif kind == "terminal":
        name, value = "terminal_confirmed", 0
    else:
        value = "f" * 64
    old = getattr(target, name)
    try:
        object.__setattr__(target, name, value)
        refusal(g)
        object.__setattr__(target, name, old)
        refusal(g)
        assert snapshot(g["journal"], g["intent"]) == g["full"]
    finally:
        object.__setattr__(target, name, old)
        g["journal"].close()


@pytest.mark.parametrize("kind", ["scope-object", "scope-value", "scope-tuple"])
def test_original_scope_association_is_preserved(tmp_path: Path, kind: str) -> None:
    g = graph(tmp_path)
    journal = g["journal"]
    old_scope, old_tuple = journal.scope, journal._scope
    try:
        if kind == "scope-object":
            journal.scope = replace(old_scope)
        elif kind == "scope-tuple":
            journal._scope = (old_tuple[0], old_tuple[1])
        else:
            object.__setattr__(old_scope, "account_id", "OTHER_ACCOUNT")
        refusal(g)
        journal.scope, journal._scope = old_scope, old_tuple
        object.__setattr__(old_scope, "account_id", old_tuple[0])
        refusal(g)
    finally:
        journal.scope, journal._scope = old_scope, old_tuple
        object.__setattr__(old_scope, "account_id", old_tuple[0])
        journal.close()


@pytest.mark.parametrize("kind", ["account", "environment", "terminal"])
def test_mutated_public_alias_is_closed_before_comparison_or_truthiness(
    tmp_path: Path, kind: str,
) -> None:
    g = graph(tmp_path)
    calls: list[str] = []

    class Hostile:
        def __eq__(self, other: object) -> bool:
            calls.append("eq")
            raise AssertionError("closed aliases must not dispatch comparison")

        def __bool__(self) -> bool:
            calls.append("bool")
            raise AssertionError("closed aliases must not dispatch truthiness")

    target, field = ((g["claimed"], "terminal_confirmed") if kind == "terminal"
                     else (g["journal"].scope, "account_id" if kind == "account" else "environment"))
    old = getattr(target, field)
    try:
        object.__setattr__(target, field, Hostile())
        refusal(g)
        assert calls == []
        object.__setattr__(target, field, old)
        refusal(g)
    finally:
        object.__setattr__(target, field, old)
        g["journal"].close()


@pytest.mark.parametrize("key,state", [("COMMAND", "ACKNOWLEDGED"), ("COMMAND", "UNKNOWN"), ("OTHER", "SENDING")])
def test_actual_raw_phase_change_is_seen_even_with_stable_full_pins(
    tmp_path: Path, key: str, state: str,
) -> None:
    g = graph(tmp_path, other=True)
    journal = g["journal"]
    original = "SENDING" if key == "COMMAND" else "PENDING"
    try:
        journal._db.execute("UPDATE commands SET state=? WHERE command_key=?", (state, key))
        assert snapshot(journal, g["intent"]) == g["full"]
        refusal(g)
        journal._db.execute("UPDATE commands SET state=? WHERE command_key=?", (original, key))
        refusal(g)
        assert snapshot(journal, g["intent"]) == g["full"]
    finally:
        journal.close()


@pytest.mark.parametrize("when", ["before", "expiry"])
def test_archive_time_bound_is_supplied_time_only_and_exclusive(tmp_path: Path, when: str) -> None:
    g = graph(tmp_path)
    try:
        value = (g["intent"].assessment.assessed_at - timedelta(microseconds=1)
                 if when == "before" else g["intent"].envelope.valid_until)
        refusal(g, now=value)
        refusal(g)
        assert snapshot(g["journal"], g["intent"]) == g["full"]
    finally:
        g["journal"].close()


def test_actual_audit_trigger_failure_rolls_back_without_postclaim_proof(tmp_path: Path) -> None:
    journal = j.CommandJournal(tmp_path / "journal.db", SCOPE)
    try:
        bound(journal)
        intent = fixture_values()[0]
        register(journal, fixture_values())
        full = snapshot(journal, intent)
        journal._db.execute("CREATE TEMP TRIGGER deny_claim BEFORE INSERT ON audit "
                            "WHEN NEW.action='CLAIM_ENTRY_V2' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with pytest.raises(sqlite3.IntegrityError):
            claim(journal, intent)
        current = journal.get("COMMAND")
        assert current is not None and current.state is j.CommandState.PENDING
        assert snapshot(journal, intent) == full
        refusal({"journal": journal, "intent": intent, "full": full, "claimed": journal.get("COMMAND")})
    finally:
        journal.close()


def test_actual_denied_COMMIT_never_publishes_candidate(tmp_path: Path) -> None:
    journal = j.CommandJournal(tmp_path / "journal.db", SCOPE)
    try:
        bound(journal)
        intent = fixture_values()[0]
        register(journal, fixture_values())
        full = snapshot(journal, intent)
        pins: dict[str, Any] = {"owner_epoch": "OWNER_1", "expected_journal_revision": full.journal_revision,
                "expected_held_quantity_sha256": full.held_quantity_sha256,
                "expected_command_identity_sha256": identity(intent)}
        denied: list[str] = []

        def authorizer(action: int, a: Any, b: Any, db: Any, trigger: Any) -> int:
            if action == sqlite3.SQLITE_TRANSACTION and a == "COMMIT":
                denied.append(a)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        journal._db.set_authorizer(authorizer)
        with pytest.raises(sqlite3.DatabaseError):
            journal.claim_entry_v2("COMMAND", **pins)
        journal._db.set_authorizer(None)
        assert denied == ["COMMIT"]
        current = journal.get("COMMAND")
        assert current is not None and current.state is j.CommandState.PENDING
        assert snapshot(journal, intent) == full
        refusal({"journal": journal, "intent": intent, "full": full, "claimed": journal.get("COMMAND")})
    finally:
        journal._db.set_authorizer(None)
        journal.close()


@pytest.mark.parametrize("field", ["account_id", "environment"])
def test_COMMIT_mutated_scope_never_dispatches_equality_or_publishes(
    tmp_path: Path, field: str,
) -> None:
    journal = j.CommandJournal(tmp_path / "journal.db", SCOPE)
    original = getattr(journal.scope, field)
    calls: list[str] = []

    class Hostile:
        def __eq__(self, other: object) -> bool:
            calls.append("eq")
            raise AssertionError("publication must close aliased scope types first")

    try:
        bound(journal)
        intent = fixture_values()[0]
        register(journal, fixture_values())
        full = snapshot(journal, intent)
        entered = False

        def trace(statement: str) -> None:
            nonlocal entered
            if statement == "COMMIT" and not entered:
                entered = True
                object.__setattr__(journal.scope, field, Hostile())

        journal._db.set_trace_callback(trace)
        result = journal.claim_entry_v2(
            "COMMAND", owner_epoch="OWNER_1", expected_journal_revision=full.journal_revision,
            expected_held_quantity_sha256=full.held_quantity_sha256,
            expected_command_identity_sha256=identity(intent),
        )
        journal._db.set_trace_callback(None)
        assert entered and calls == []
        assert result is not None and result.state is j.CommandState.SENDING
        object.__setattr__(journal.scope, field, original)
        refusal({"journal": journal, "intent": intent, "full": full, "claimed": result})
        assert snapshot(journal, intent) == full
    finally:
        journal._db.set_trace_callback(None)
        object.__setattr__(journal.scope, field, original)
        journal.close()


@pytest.mark.parametrize("phase", ["claim", "require"])
def test_actual_COMMIT_trace_nested_failed_claim_cannot_resurrect(
    tmp_path: Path, phase: str,
) -> None:
    g = graph(tmp_path) if phase == "require" else None
    journal = g["journal"] if g else j.CommandJournal(tmp_path / "journal.db", SCOPE)
    try:
        if g is None:
            bound(journal)
            intent = fixture_values()[0]
            register(journal, fixture_values())
            full = snapshot(journal, intent)
        else:
            intent, full = g["intent"], g["full"]
        failures: list[str] = []
        entered = False

        def trace(statement: str) -> None:
            nonlocal entered
            if statement == "COMMIT" and not entered:
                entered = True
                try:
                    malformed: Any = True
                    journal.claim_entry_v2(
                        "COMMAND", owner_epoch=malformed, expected_journal_revision=full.journal_revision,
                        expected_held_quantity_sha256=full.held_quantity_sha256,
                        expected_command_identity_sha256=identity(intent),
                    )
                except c.RiskV2Rejected as error:
                    failures.append(str(error))

        journal._db.set_trace_callback(trace)
        if phase == "claim":
            claimed = journal.claim_entry_v2(
                "COMMAND", owner_epoch="OWNER_1", expected_journal_revision=full.journal_revision,
                expected_held_quantity_sha256=full.held_quantity_sha256,
                expected_command_identity_sha256=identity(intent),
            )
            assert claimed is not None and claimed.state is j.CommandState.SENDING
            g = {"journal": journal, "intent": intent, "full": full, "claimed": claimed}
        else:
            assert g is not None
            with pytest.raises(c.RiskV2Rejected):
                require(g)
        journal._db.set_trace_callback(None)
        assert entered and len(failures) == 1
        assert g is not None
        refusal(g)
        assert snapshot(journal, intent) == full
        current = journal.get("COMMAND")
        assert current is not None and current.state is j.CommandState.SENDING
    finally:
        journal._db.set_trace_callback(None)
        journal.close()


def test_direct_connection_close_propagates_SQLite_error_and_retires(tmp_path: Path) -> None:
    g = graph(tmp_path)
    try:
        g["journal"]._db.close()
        with pytest.raises(sqlite3.ProgrammingError):
            require(g)
        refusal(g)
    finally:
        g["journal"].close()


def test_close_and_reopen_cannot_restore_original_proof(tmp_path: Path) -> None:
    g = graph(tmp_path)
    g["journal"].close()
    refusal(g)
    with j.CommandJournal(tmp_path / "journal.db", SCOPE) as reopened:
        refusal(g, journal=reopened)
        assert snapshot(reopened, g["intent"]) == g["full"]
