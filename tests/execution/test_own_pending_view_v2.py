"""Local true-J pair/lifetime tests only; synthetic inputs grant no execution authority."""

import copy
import hashlib
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from test_reservations_v2 import SCOPE, bound, claim, fixture_values, register, reserve, snapshot
from test_risk_v2_codec import NOW

from multi_exchange_trader.execution import journal as j
from multi_exchange_trader.execution import reservations_v2 as r
from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.risk_v2_codec import (
    DurableEntryIntentV2,
    encode_entry_intent_v2,
)

CONFLICT = "RISK_V2_LOCAL_ADMISSION_CONFLICT"


def identity(intent: DurableEntryIntentV2) -> str:
    return r.entry_command_identity_sha256_v2(
        intent.envelope.scope.product_scope.account, intent.command_key, intent, reserve(intent)
    )


def capture(journal: j.CommandJournal, intent: DurableEntryIntentV2, **changes: Any) -> Any:
    method = getattr(journal, "_own_pending_view_v2", None)
    assert callable(method), "journal must issue a true own-PENDING view"
    args = {
        "command_key": intent.command_key,
        "owner_epoch": "OWNER_1",
        "expected_command_identity_sha256": identity(intent),
        "now": NOW,
    }
    args.update(changes)
    scope = args.pop("scope", intent.envelope.scope)
    return method(scope, **args)


def require(journal: j.CommandJournal, view: object, intent: DurableEntryIntentV2,
            **changes: Any) -> Any:
    method = getattr(journal, "_require_own_pending_view_v2", None)
    assert callable(method), "journal must revalidate its original own-PENDING view"
    args = {
        "scope": intent.envelope.scope,
        "command_key": intent.command_key,
        "expected_command_identity_sha256": identity(intent),
        "now": NOW,
    }
    args.update(changes)
    return method(view, **args)


def rejected(code: str | None = None) -> Any:
    return pytest.raises(c.RiskV2Rejected, match=None if code is None else "^" + code + "$")


def register_other(journal: j.CommandJournal) -> j.CommandRecord:
    own_marks = fixture_values()[3]
    other = fixture_values("OTHER", symbol="YAAUSDT")
    return register(journal, (*other[:3], own_marks + other[3]))


@pytest.fixture
def pending(tmp_path: Path) -> Iterator[tuple[j.CommandJournal, DurableEntryIntentV2]]:
    with j.CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        bound(journal)
        values = fixture_values()
        register(journal, values)
        yield journal, values[0]


def test_pair_subtracts_only_own_hold_with_full_pins_and_original_time(pending: Any) -> None:
    journal, intent = pending
    original = journal.get(intent.command_key)
    view = capture(journal, intent)
    data = require(journal, view, intent, now=NOW + timedelta(microseconds=1))
    assert data.record == original
    assert data.intent == intent
    assert data.reservation == reserve(intent)
    assert data.scope == intent.envelope.scope
    assert data.command_identity_sha256 == identity(intent)
    assert data.full == snapshot(journal, intent)
    assert data.full.position_count == 1
    assert data.full.risk_amount == Decimal(12)
    assert data.full.margin_amount == Decimal(52)
    assert data.full.components[0].base_quantity == Decimal(1)
    assert data.full.components[0].component_id == original.client_order_id
    assert data.evaluation.position_count == 0
    assert data.evaluation.risk_amount == Decimal(0)
    assert data.evaluation.margin_amount == Decimal(0)
    assert data.evaluation.components == ()
    assert data.full.excluded_command_key is None
    assert data.evaluation.excluded_command_key == "COMMAND"
    assert data.full.snapshot_id != data.evaluation.snapshot_id
    assert data.full.held_quantity_sha256 == data.evaluation.held_quantity_sha256
    assert data.full.journal_revision == data.evaluation.journal_revision == 2
    assert data.full.owner_generation == data.evaluation.owner_generation == "OWNER_1"
    assert data.full.observed_at == data.evaluation.observed_at == NOW
    assert data.intent.source_record_sha256s == intent.source_record_sha256s
    assert data.record.payload_json == encode_entry_intent_v2(intent)
    assert require(journal, view, intent) is data


def test_other_held_instrument_and_source_preimages_remain(pending: Any) -> None:
    journal, intent = pending
    other = register_other(journal)
    data = require(journal, capture(journal, intent), intent)
    assert data.full.position_count == 2
    assert data.full.risk_amount == Decimal(24)
    assert data.full.margin_amount == Decimal(104)
    assert data.evaluation.position_count == 1
    assert data.evaluation.risk_amount == Decimal(12)
    assert data.evaluation.margin_amount == Decimal(52)
    assert len(data.evaluation.components) == 1
    assert data.evaluation.components[0].component_id == other.client_order_id
    assert data.evaluation.components[0].instrument.symbol == "YAAUSDT"
    assert data.evaluation.components[0].base_quantity == Decimal(1)
    assert journal.get("OTHER") == other
    assert data.full.held_quantity_sha256 == data.evaluation.held_quantity_sha256


def test_capture_and_require_use_two_single_transactions_without_writes(pending: Any) -> None:
    journal, intent = pending
    before = tuple(journal._db.iterdump())
    changes = journal._db.total_changes
    statements: list[str] = []
    journal._db.set_trace_callback(statements.append)
    try:
        view = capture(journal, intent)
        require(journal, view, intent)
    finally:
        journal._db.set_trace_callback(None)
    assert journal._db.total_changes == changes
    assert tuple(journal._db.iterdump()) == before
    assert sum(s == "BEGIN IMMEDIATE" for s in statements) == 2
    assert sum(s == "COMMIT" for s in statements) == 2
    assert not any(s.split()[0] in {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"}
                   for s in statements)


@pytest.mark.parametrize("kind", ["object", "new", "copy", "deepcopy", "public_excluded"])
def test_only_original_handle_is_accepted(pending: Any, kind: str) -> None:
    journal, intent = pending
    view = capture(journal, intent)
    candidates = {
        "object": object(),
        "new": object.__new__(type(view)),
        "copy": copy.copy(view),
        "deepcopy": copy.deepcopy(view),
        "public_excluded": snapshot(journal, intent, exclude="COMMAND"),
    }
    with rejected(CONFLICT):
        require(journal, candidates[kind], intent)


def test_equal_other_journal_and_equal_recapture_do_not_copy_authority(pending: Any,
                                                                     tmp_path: Path) -> None:
    journal, intent = pending
    old = capture(journal, intent)
    with j.CommandJournal(tmp_path / "other.db", SCOPE) as other:
        bound(other)
        register(other, fixture_values())
        capture(other, intent)
        with rejected(CONFLICT):
            require(other, old, intent)
    new = capture(journal, intent)
    assert new is not old
    with rejected(CONFLICT):
        require(journal, old, intent)
    # A stale request may retire the slot conservatively; recapture is explicit.
    assert require(journal, capture(journal, intent), intent).full.position_count == 1


@pytest.mark.parametrize("changes,code", [
    ({"command_key": "OTHER"}, CONFLICT),
    ({"owner_epoch": "OWNER_2"}, CONFLICT),
    ({"expected_command_identity_sha256": "a" * 64}, CONFLICT),
    ({"command_key": ""}, "RISK_V2_INPUT_INVALID"),
    ({"owner_epoch": 1}, "RISK_V2_INPUT_INVALID"),
    ({"expected_command_identity_sha256": "bad"}, "RISK_V2_INPUT_INVALID"),
    ({"now": NOW - timedelta(microseconds=1)}, CONFLICT),
    ({"now": NOW + timedelta(seconds=1)}, CONFLICT),
    ({"scope": object()}, "RISK_V2_INPUT_INVALID"),
])
def test_failed_capture_always_retires_previous_view(pending: Any, changes: Any, code: str) -> None:
    journal, intent = pending
    old = capture(journal, intent)
    with rejected(code):
        capture(journal, intent, **changes)
    with rejected(CONFLICT):
        require(journal, old, intent)


@pytest.mark.parametrize("what", ["account", "instrument", "strategy"])
def test_capture_and_require_reject_wrong_scope(pending: Any, what: str) -> None:
    journal, intent = pending
    scope = intent.envelope.scope
    if what == "account":
        scope = replace(scope, product_scope=replace(scope.product_scope,
            account=replace(scope.product_scope.account, account_id="OTHER")))
    elif what == "instrument":
        scope = replace(scope, instrument=replace(scope.instrument, symbol="YAAUSDT"))
    else:
        scope = replace(scope, strategy=replace(scope.strategy, strategy_id="OTHER"))
    with rejected("RISK_V2_SCOPE_MISMATCH"):
        capture(journal, intent, scope=scope)
    view = capture(journal, intent)
    with rejected("RISK_V2_SCOPE_MISMATCH"):
        require(journal, view, intent, scope=scope)


@pytest.mark.parametrize("change", [
    {"command_key": "OTHER"}, {"expected_command_identity_sha256": "b" * 64},
    {"now": NOW - timedelta(microseconds=1)}, {"now": NOW + timedelta(seconds=1)},
])
def test_require_rechecks_request_identity_and_exclusive_expiry(pending: Any, change: Any) -> None:
    journal, intent = pending
    with rejected(CONFLICT):
        require(journal, capture(journal, intent), intent, **change)


@pytest.mark.parametrize("state", ["SENDING", "ACKNOWLEDGED", "UNKNOWN", "REJECTED", "TERMINAL"])
def test_capture_refuses_nonpending_or_terminal_original(pending: Any, state: str) -> None:
    journal, intent = pending
    if state == "TERMINAL":
        journal._db.execute("UPDATE commands SET terminal=1 WHERE command_key='COMMAND'")
    else:
        journal._db.execute("UPDATE commands SET state=? WHERE command_key='COMMAND'", (state,))
    with rejected(CONFLICT):
        capture(journal, intent)


@pytest.mark.parametrize("sql,args", [
    ("UPDATE commands SET state='SENDING' WHERE command_key='COMMAND'", ()),
    ("UPDATE commands SET terminal=1 WHERE command_key='COMMAND'", ()),
    ("UPDATE commands SET client_id='WRONG' WHERE command_key='COMMAND'", ()),
    ("UPDATE commands SET payload_hash=? WHERE command_key='COMMAND'", ("b" * 64,)),
    ("UPDATE commands SET payload='{}' WHERE command_key='COMMAND'", ()),
    ("UPDATE commands SET payload=payload||' ' WHERE command_key='COMMAND'", ()),
    ("UPDATE entry_reservations_v2 SET margin_amount='52.0' WHERE command_key='COMMAND'", ()),
    ("UPDATE entry_reservations_v2 SET base_quantity='1' WHERE command_key='COMMAND'", ()),
    ("UPDATE entry_reservations_v2 SET risk_amount='NaN' WHERE command_key='COMMAND'", ()),
    ("DELETE FROM entry_reservations_v2 WHERE command_key='COMMAND'", ()),
    ("UPDATE reservation_meta_v2 SET owner_epoch='OWNER_2'", ()),
    ("UPDATE reservation_meta_v2 SET capacity_revision=capacity_revision+1", ()),
    ("UPDATE reservation_meta_v2 SET schema_version=3", ()),
    ("DELETE FROM reservation_meta_v2", ()),
])
def test_require_rejects_changed_or_malformed_current_rows(pending: Any, sql: str, args: Any) -> None:
    journal, intent = pending
    view = capture(journal, intent)
    journal._db.execute(sql, args)
    with rejected():
        require(journal, view, intent)


def test_all_rows_phase_and_terminal_are_witnessed_even_when_full_hash_is_unchanged(
    pending: Any,
) -> None:
    journal, intent = pending
    other = register_other(journal)
    view = capture(journal, intent)
    full = snapshot(journal, intent)
    journal._db.execute("UPDATE commands SET state='UNKNOWN' WHERE command_key='OTHER'")
    assert snapshot(journal, intent).held_quantity_sha256 == full.held_quantity_sha256
    assert snapshot(journal, intent).journal_revision == full.journal_revision
    with rejected(CONFLICT):
        require(journal, view, intent)
    assert journal.get("OTHER").client_order_id == other.client_order_id


def test_valid_unheld_v1_row_change_is_also_witnessed(pending: Any) -> None:
    journal, intent = pending
    journal.register("CANCEL", j.CommandKind.CANCEL, {"source": "FIXTURE"})
    view = capture(journal, intent)
    journal._db.execute("UPDATE commands SET state='UNKNOWN' WHERE command_key='CANCEL'")
    with rejected(CONFLICT):
        require(journal, view, intent)


def test_legacy_entry_cannot_be_original(tmp_path: Path) -> None:
    with j.CommandJournal(tmp_path / "legacy.db", SCOPE) as journal:
        bound(journal)
        journal.register("COMMAND", j.CommandKind.ENTRY, {"source": "FIXTURE"})
        with rejected("RISK_V2_LEGACY_RESERVATION_RECONCILIATION_REQUIRED"):
            capture(journal, fixture_values()[0])


def test_real_bind_and_register_invalidate_retained_view(pending: Any) -> None:
    journal, intent = pending
    view = capture(journal, intent)
    register_other(journal)
    with rejected(CONFLICT):
        require(journal, view, intent)
    view = capture(journal, intent)
    journal.bind_owner_epoch_v2("OWNER_2", expected_previous_owner_epoch="OWNER_1",
                                expected_journal_revision=3)
    with rejected(CONFLICT):
        require(journal, view, intent)


def test_real_other_hold_release_invalidates_view_and_preserves_own_reservation(pending: Any) -> None:
    journal, intent = pending
    register_other(journal)
    journal.reject("OTHER", "FIXTURE_TERMINAL_REJECTION")
    view = capture(journal, intent)
    journal.release_reservation("OTHER", confirmed_terminal=True,
                                terminal_evidence="FIXTURE_CONFIRMED_TERMINAL")
    with rejected(CONFLICT):
        require(journal, view, intent)
    data = require(journal, capture(journal, intent), intent)
    assert data.full.position_count == 1 and data.evaluation.position_count == 0
    assert data.full.risk_amount == Decimal(12) and data.full.margin_amount == Decimal(52)
    assert data.full.journal_revision == 4


def test_failed_legacy_claim_also_consumes_the_pending_view(pending: Any) -> None:
    journal, intent = pending
    view = capture(journal, intent)
    with rejected(CONFLICT):
        journal.claim("COMMAND")
    assert journal.get("COMMAND").state is j.CommandState.PENDING
    with rejected(CONFLICT):
        require(journal, view, intent)


def test_claim_consumes_view_without_releasing_or_changing_full_pins(pending: Any) -> None:
    journal, intent = pending
    view = capture(journal, intent)
    full = snapshot(journal, intent)
    result = claim(journal, intent)
    assert result is not None and result.state is j.CommandState.SENDING
    after = snapshot(journal, intent)
    assert after.held_quantity_sha256 == full.held_quantity_sha256
    assert after.journal_revision == full.journal_revision
    assert after.risk_amount == Decimal(12) and after.margin_amount == Decimal(52)
    with rejected(CONFLICT):
        require(journal, view, intent)
    with rejected(CONFLICT):
        capture(journal, intent)


def test_failed_claim_and_close_never_revive_a_view(pending: Any) -> None:
    journal, intent = pending
    old = capture(journal, intent)
    with rejected(CONFLICT):
        claim(journal, intent, expected_command_identity_sha256="b" * 64)
    assert journal.get("COMMAND").state is j.CommandState.PENDING
    with rejected(CONFLICT):
        require(journal, old, intent)
    fresh = capture(journal, intent)
    journal.close()
    with rejected(CONFLICT):
        require(journal, fresh, intent)
    with rejected(CONFLICT):
        capture(journal, intent)


@pytest.mark.parametrize("target,field,value", [
    ("data", "command_identity_sha256", "b" * 64),
    ("record", "terminal_confirmed", 0),
    ("record", "state", "PENDING"),
    ("record", "reservation_v2", None),
    ("reservation", "margin_amount", Decimal("52.0")),
    ("reservation", "schema_version", True),
    ("intent", "position_id", "OTHER"),
    ("intent", "source_record_sha256s", ("b" * 64,)),
    ("full", "observed_at", NOW + timedelta(microseconds=1)),
    ("evaluation", "risk_amount", Decimal("-0.00")),
    ("evaluation", "position_count", False),
])
def test_retained_alias_mutation_cannot_rebaseline_original_witness(pending: Any,
    target: str, field: str, value: object,
) -> None:
    journal, intent = pending
    view = capture(journal, intent)
    data = require(journal, view, intent)
    obj = data if target == "data" else getattr(data, target)
    object.__setattr__(obj, field, value)
    with rejected():
        require(journal, view, intent)


def test_terminal_v1_decimal_representation_is_not_erased_from_witness(tmp_path: Path) -> None:
    with j.CommandJournal(tmp_path / "legacy.db", SCOPE) as journal:
        limits = j.ReservationLimits(*(Decimal(1000),) * 3, 10, *(Decimal(0),) * 3, 0)
        journal.register("LEGACY", j.CommandKind.ENTRY, {"source": "FIXTURE"},
                         reservation=j.Reservation(Decimal("1.00"), Decimal(2), Decimal(3)),
                         limits=limits)
        journal._db.execute("UPDATE commands SET terminal=1 WHERE command_key='LEGACY'")
        bound(journal)
        values = fixture_values()
        register(journal, values)
        intent = values[0]
        view = capture(journal, intent)
        journal._db.execute("UPDATE commands SET risk='1.0' WHERE command_key='LEGACY'")
        with rejected(CONFLICT):
            require(journal, view, intent)


def test_capture_rejects_tampered_but_rehashed_canonical_frame(pending: Any) -> None:
    journal, intent = pending
    raw = encode_entry_intent_v2(intent).replace('"position_id":"POSITION"',
                                                '"position_id":"OTHER"')
    journal._db.execute("UPDATE commands SET payload=?,payload_hash=? WHERE command_key='COMMAND'",
                       (raw, hashlib.sha256(raw.encode()).hexdigest()))
    with rejected():
        capture(journal, intent)


def test_external_connection_phase_change_is_detected(pending: Any, tmp_path: Path) -> None:
    journal, intent = pending
    view = capture(journal, intent)
    with sqlite3.connect(tmp_path / "journal.db") as db:
        db.execute("UPDATE commands SET state='SENDING' WHERE command_key='COMMAND'")
    with rejected(CONFLICT):
        require(journal, view, intent)


def test_issuer_copy_before_capture_cannot_issue(pending: Any) -> None:
    journal, intent = pending
    alias = copy.copy(journal)
    assert alias is not journal
    statements: list[str] = []
    journal._db.set_trace_callback(statements.append)
    try:
        with rejected(CONFLICT):
            capture(alias, intent)
        assert statements == []
    finally:
        journal._db.set_trace_callback(None)
    data = require(journal, capture(journal, intent), intent)
    assert data.full.position_count == 1 and data.evaluation.position_count == 0
    assert data.full.risk_amount == Decimal(12) and data.full.margin_amount == Decimal(52)


def test_issuer_copy_after_capture_cannot_consume(pending: Any) -> None:
    journal, intent = pending
    view = capture(journal, intent)
    data = require(journal, view, intent)
    alias = copy.copy(journal)
    statements: list[str] = []
    journal._db.set_trace_callback(statements.append)
    try:
        with rejected(CONFLICT):
            require(alias, view, intent)
        assert statements == []
    finally:
        journal._db.set_trace_callback(None)
    assert require(journal, view, intent) is data


def test_issuer_copy_cannot_revive_equal_recapture(pending: Any) -> None:
    journal, intent = pending
    old = capture(journal, intent)
    alias = copy.copy(journal)
    fresh = capture(journal, intent)
    data = require(journal, fresh, intent)
    assert fresh is not old
    with rejected(CONFLICT):
        require(alias, old, intent)
    assert require(journal, fresh, intent) is data
    with rejected(CONFLICT):
        require(journal, old, intent)


def test_issuer_copy_cannot_revive_failed_claim(pending: Any) -> None:
    journal, intent = pending
    old = capture(journal, intent)
    alias = copy.copy(journal)
    with rejected(CONFLICT):
        claim(journal, intent, expected_command_identity_sha256="b" * 64)
    assert journal.get("COMMAND").state is j.CommandState.PENDING
    with rejected(CONFLICT):
        require(alias, old, intent)
    with rejected(CONFLICT):
        require(journal, old, intent)
    assert require(journal, capture(journal, intent), intent).full.position_count == 1


@pytest.mark.parametrize("operation", ["capture", "require"])
def test_issuer_copy_of_copy_is_not_an_original(pending: Any, operation: str) -> None:
    journal, intent = pending
    view = capture(journal, intent)
    data = require(journal, view, intent)
    alias = copy.copy(copy.copy(journal))
    with rejected(CONFLICT):
        if operation == "capture":
            capture(alias, intent)
        else:
            require(alias, view, intent)
    assert require(journal, view, intent) is data


@pytest.mark.parametrize("operation", ["capture", "require"])
def test_issuer_constructor_bypass_has_no_authority(pending: Any, operation: str) -> None:
    journal, intent = pending
    view = capture(journal, intent)
    data = require(journal, view, intent)
    uninitialized = object.__new__(j.CommandJournal)
    with rejected(CONFLICT):
        if operation == "capture":
            capture(uninitialized, intent)
        else:
            require(uninitialized, view, intent)
    assert require(journal, view, intent) is data
