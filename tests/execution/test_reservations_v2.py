"""Fixture-only atomic V2 reservations, with independent literal hash domains."""

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from datetime import timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from threading import Barrier
from typing import Any, cast

import pytest
from test_risk_v2_codec import GOLDEN_WIRES, NOW, actual_intent

from multi_exchange_trader.execution import linear_risk_evidence_v2 as e
from multi_exchange_trader.execution import reservations_v2 as r
from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.journal import (
    CommandJournal,
    CommandKind,
    CommandRecord,
    CommandState,
    ExecutionScope,
    JournalConflict,
    Reservation,
    ReservationLimits,
)
from multi_exchange_trader.execution.risk_v2_codec import (
    DurableEntryIntentV2,
    decode_entry_intent_v2,
    encode_entry_intent_v2,
)

D = Decimal
SCOPE = ExecutionScope("한글", "DEMO")
CONFLICT = "RISK_V2_LOCAL_ADMISSION_CONFLICT"
LEGACY = "RISK_V2_LEGACY_RESERVATION_RECONCILIATION_REQUIRED"
ENVELOPE = "22e29f771c4819879dc9848542e87f25bbeb53939ccd63ed71476e56d947a47d"
WIRE_HASH = "e45195df8a31954b052586bebf7a4762a934bb98e930031080066ee109e62f93"
CLIENT = "ec_96fac1eee83998d5ca0d0bfa4b4b673a"
IDENTITY_WIRE = b'{"fields":{"account_id":"\\ud55c\\uae00","command_key":"COMMAND","environment":"DEMO","intent_sha256":"e45195df8a31954b052586bebf7a4762a934bb98e930031080066ee109e62f93","kind":"ENTRY","reservation":{"base_quantity":"1.00","envelope_sha256":"22e29f771c4819879dc9848542e87f25bbeb53939ccd63ed71476e56d947a47d","instrument":{"product":"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"margin_amount":"52.00000000","risk_amount":"12.0000","schema_version":2}},"record_type":"LocalEntryCommandIdentityV2","schema":"RISK_V2_CANONICAL_V1"}'
HELD_WIRE = b'{"fields":{"account_id":"\\ud55c\\uae00","entries":[{"base_quantity":"1.00","client_order_id":"ec_96fac1eee83998d5ca0d0bfa4b4b673a","command_key":"COMMAND","envelope_sha256":"22e29f771c4819879dc9848542e87f25bbeb53939ccd63ed71476e56d947a47d","instrument":{"product":"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"margin_amount":"52.00000000","payload_hash":"e45195df8a31954b052586bebf7a4762a934bb98e930031080066ee109e62f93","position_count":1,"risk_amount":"12.0000","version":2}],"environment":"DEMO"},"record_type":"LocalHeldQuantitySetV2","schema":"RISK_V2_CANONICAL_V1"}'
SNAPSHOT_WIRE = b'{"fields":{"account_id":"\\ud55c\\uae00","environment":"DEMO","excluded_command_key":null,"held_quantity_sha256":"6b78bc35f06849ffd8f8b9e3f306d60022841a8ac1e41e18228c4acda9d887b3","journal_revision":2,"owner_epoch":"OWNER_1"},"record_type":"LocalReservationSnapshotIdentityV2","schema":"RISK_V2_CANONICAL_V1"}'


def rejected(code: str = CONFLICT) -> Any:
    return pytest.raises(c.RiskV2Rejected, match="^" + code + "$")


def reserve(intent: DurableEntryIntentV2) -> r.ReservationV2:
    required = intent.assessment.required_reservation_quote
    assert required is not None
    return r.ReservationV2(
        2,
        intent.envelope.scope.instrument,
        c.envelope_sha256_v2(intent.envelope),
        intent.envelope.base_quantity,
        intent.assessment.preparation.sizing.estimated_loss_quote,
        required,
    )


def test_literal_domains_before_helper_behavior() -> None:
    for raw, expected in (
        (IDENTITY_WIRE, "e8d5ace2484ea32a3c61918f891a19c682377cf594fcaf51587aeb767d604b1f"),
        (HELD_WIRE, "6b78bc35f06849ffd8f8b9e3f306d60022841a8ac1e41e18228c4acda9d887b3"),
        (SNAPSHOT_WIRE, "a572627bde1f68bab9ba8dd771ddde9b2bec3c86e481ca0c4d8718c0fc0b69f5"),
    ):
        tree = json.loads(raw)
        assert (
            json.dumps(tree, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
                "ascii"
            )
            == raw
        )
        assert hashlib.sha256(raw).hexdigest() == expected

        def tuples(value: Any) -> Any:
            if isinstance(value, list):
                return tuple(tuples(item) for item in value)
            if isinstance(value, dict):
                return {key: tuples(item) for key, item in value.items()}
            return value

        assert c._canonical_sha256_v2(tree["record_type"], tuples(tree["fields"])) == expected
    intent = decode_entry_intent_v2(GOLDEN_WIRES["POSITIVE_ROOT"])
    assert hashlib.sha256(encode_entry_intent_v2(intent).encode()).hexdigest() == WIRE_HASH
    assert (
        r.entry_command_identity_sha256_v2(
            intent.envelope.scope.product_scope.account, "COMMAND", intent, reserve(intent)
        )
        == "e8d5ace2484ea32a3c61918f891a19c682377cf594fcaf51587aeb767d604b1f"
    )
    record = CommandRecord(
        "COMMAND",
        CLIENT,
        CommandKind.ENTRY,
        GOLDEN_WIRES["POSITIVE_ROOT"],
        WIRE_HASH,
        CommandState.PENDING,
        None,
        False,
        reserve(intent),
    )
    assert r._held_hash(intent.envelope.scope.product_scope.account, (record,)) == (
        "6b78bc35f06849ffd8f8b9e3f306d60022841a8ac1e41e18228c4acda9d887b3"
    )
    snapshot = r._snapshot(
        intent.envelope.scope.product_scope.account,
        (record,),
        owner_epoch="OWNER_1",
        revision=2,
        now=NOW,
        exclude=None,
    )
    assert (
        snapshot.snapshot_id == "a572627bde1f68bab9ba8dd771ddde9b2bec3c86e481ca0c4d8718c0fc0b69f5"
    )


def fixture_values(
    key: str = "COMMAND",
    *,
    margin_only: bool = False,
    policy_changes: dict[str, Any] | None = None,
    symbol: str = "XAAUSDT",
) -> tuple[
    DurableEntryIntentV2,
    c.LinearRiskPolicyV2,
    c.LinearAccountRiskSnapshotV2,
    tuple[c.LinearMarkQuoteV2, ...],
]:
    """Coherent accepted graph, with explicit synthetic policy/account amounts."""
    original = actual_intent("POSITIVE_ROOT")
    instrument = replace(original.envelope.scope.instrument, symbol=symbol)
    scope = replace(original.envelope.scope, instrument=instrument)
    policy = c.LinearRiskPolicyV2(
        scope,
        "POLICY_V1",
        "LOSS_FIXED",
        c.LinearCapitalCapsV2(
            D(100000), D(100000), "LINEAR_MARK_GROSS_AT_ADMISSION_V1", "NOT_REQUIRED", None
        ),
        D(1),
        D(10),
        D(12),
        D(100000),
        D(100000),
        1000,
        1000,
        D(100000),
        D(100000),
        D(100000),
        "ISOLATED",
        "ONE_WAY",
        D("0.01"),
        D(10),
        D(10),
    )
    if policy_changes:
        policy = replace(policy, **policy_changes)
    ph = c.risk_policy_sha256_v2(policy)
    pin = replace(
        original.envelope.instrument_pin,
        key=instrument,
        native_instrument_id=symbol + "-LINEAR.BYBIT",
    )
    envelope = replace(original.envelope, policy_sha256=ph, scope=scope, instrument_pin=pin)
    prep = original.assessment.preparation
    sizing = replace(prep.sizing, scope=scope, envelope=envelope, policy_sha256=ph)
    minimum = replace(
        prep.minimum_amount, scope=scope, envelope_sha256=c.envelope_sha256_v2(envelope)
    )
    tier = replace(prep.tier_bound, instrument=instrument)
    valuation = replace(prep.valuation, scope=scope)
    binding = replace(
        prep.binding,
        policy_sha256=ph,
        scope=scope,
        instrument_pin=pin,
        envelope_sha256=c.envelope_sha256_v2(envelope),
        sizing_sha256=c.sizing_sha256_v2(sizing),
        tier_bound_sha256=e.tier_bound_sha256_v2(tier),
        valuation_sha256=c.valuation_sha256_v2(valuation),
        minimum_assessment_sha256=e.minimum_assessment_sha256_v2(minimum),
    )
    prep = replace(
        prep,
        sizing=sizing,
        minimum_amount=minimum,
        binding=binding,
        tier_bound=tier,
        valuation=valuation,
    )
    bh = e.binding_sha256_v2(binding)
    assert original.assessment.margin is not None
    margin = replace(
        original.assessment.margin,
        binding_sha256=bh,
        reference=replace(original.assessment.margin.reference, input_sha256=bh),
    )
    assert original.assessment.liquidation is not None
    mh = e.margin_assessment_sha256_v2(margin)
    liquidation = replace(
        original.assessment.liquidation,
        binding_sha256=bh,
        margin_assessment_sha256=mh,
        reference=replace(original.assessment.liquidation.reference, input_sha256=mh),
    )
    assert original.assessment.leverage_readback is not None
    readback = replace(original.assessment.leverage_readback, binding_sha256=bh)
    assessment = replace(
        original.assessment,
        preparation=prep,
        margin=margin,
        liquidation=None if margin_only else liquidation,
        leverage_readback=None if margin_only else readback,
        stage="PLAN" if margin_only else "SUBMIT",
        blockers=("RISK_V2_LIQUIDATION_UNVERIFIED", "RISK_V2_LEVERAGE_READBACK_UNVERIFIED")
        if margin_only
        else (),
        liquidation_buffer_result=None if margin_only else "GAP_SATISFIED",
    )
    intent = replace(original, command_key=key, envelope=envelope, assessment=assessment)
    ref = c.RiskObservationRefV2("ACCOUNT", "SOURCE", "FIXTURE", "SOURCE_V1", NOW, NOW, "a" * 64)
    account = c.LinearAccountRiskSnapshotV2(
        scope.product_scope.account,
        ref,
        "COVERAGE_V1",
        (),
        "UTC_REALIZED_NET_FEES_FUNDING",
        NOW.date(),
        D(100000),
        D(100000),
        D(0),
        D(0),
        0,
        D(0),
        D(0),
        0,
        "ISOLATED",
        "ONE_WAY",
        (),
    )
    marks = (c.LinearMarkQuoteV2(scope.instrument, replace(ref, observation_id="MARK"), D(100)),)
    return intent, policy, account, marks


def bound(journal: CommandJournal) -> None:
    assert (
        journal.bind_owner_epoch_v2(
            "OWNER_1", expected_previous_owner_epoch=None, expected_journal_revision=0
        )
        == 1
    )


def snapshot(
    journal: CommandJournal, intent: DurableEntryIntentV2, exclude: str | None = None
) -> c.LinearReservationSnapshotV2:
    return journal.reservation_snapshot_v2(
        intent.envelope.scope, now=NOW, owner_generation="OWNER_1", exclude=exclude
    )


def admission(journal: CommandJournal, values: Any, **overrides: Any) -> r.ReservationAdmissionV2:
    intent, policy, account, marks = values
    snap = snapshot(journal, intent)
    result = r.ReservationAdmissionV2(
        account.account,
        "OWNER_1",
        snap.journal_revision,
        snap.held_quantity_sha256,
        r.entry_command_identity_sha256_v2(
            account.account, intent.command_key, intent, reserve(intent)
        ),
        policy,
        account,
        marks,
        NOW,
    )
    return replace(result, **overrides)


def register(journal: CommandJournal, values: Any) -> CommandRecord:
    intent = values[0]
    return journal.register_entry_v2(
        intent.command_key,
        intent,
        reservation=reserve(intent),
        admission=admission(journal, values),
    )


def claim(
    journal: CommandJournal, intent: DurableEntryIntentV2, **overrides: Any
) -> CommandRecord | None:
    snap = snapshot(journal, intent)
    pins: dict[str, Any] = {
        "owner_epoch": "OWNER_1",
        "expected_journal_revision": snap.journal_revision,
        "expected_held_quantity_sha256": snap.held_quantity_sha256,
        "expected_command_identity_sha256": r.entry_command_identity_sha256_v2(
            intent.envelope.scope.product_scope.account, intent.command_key, intent, reserve(intent)
        ),
    }
    pins.update(overrides)
    return journal.claim_entry_v2(intent.command_key, **pins)


def test_atomic_register_replay_claim_release_retains_identity(tmp_path: Path) -> None:
    values = fixture_values(margin_only=True)
    intent = values[0]
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        bound(journal)
        before = admission(journal, values)
        first = register(journal, values)
        assert first.payload_json == encode_entry_intent_v2(intent)
        assert first.reservation is None and first.reservation_v2 == reserve(intent)
        assert decode_entry_intent_v2(first.payload_json).assessment.blockers == (
            "RISK_V2_LIQUIDATION_UNVERIFIED",
            "RISK_V2_LEVERAGE_READBACK_UNVERIFIED",
        )
        with rejected():
            journal.register_entry_v2(
                "COMMAND", intent, reservation=reserve(intent), admission=before
            )
        snap = snapshot(journal, intent)
        assert snap.journal_revision == 2 and snap.position_count == 1
        audit_count = len(journal.audit_events())
        assert register(journal, values) == first
        assert len(journal.audit_events()) == audit_count
        claimed = claim(journal, intent)
        assert claimed is not None and claimed.state is CommandState.SENDING
        assert snapshot(journal, intent) == snap
        assert claim(journal, intent) is None
        with rejected():
            claim(journal, intent, expected_journal_revision=1)
        with rejected():
            journal.claim("COMMAND")
        journal.mark_unknown("COMMAND", "fixture ambiguous")
        with pytest.raises(JournalConflict):
            journal.release_reservation(
                "COMMAND", confirmed_terminal=True, terminal_evidence="fixture"
            )
        assert snapshot(journal, intent) == snap
        journal.acknowledge("COMMAND", "fixture ack")
        assert snapshot(journal, intent) == snap
        released = journal.release_reservation(
            "COMMAND", confirmed_terminal=True, terminal_evidence="fixture final"
        )
        assert released.reservation_v2 == first.reservation_v2
        terminal = snapshot(journal, intent)
        assert terminal.journal_revision == 3 and terminal.position_count == 0
        journal.release_reservation(
            "COMMAND", confirmed_terminal=True, terminal_evidence="repeat final"
        )
        assert snapshot(journal, intent) == terminal
        assert register(journal, values) == released
        assert claim(journal, intent) is None
        assert journal._db.execute("SELECT count(*) FROM entry_reservations_v2").fetchone()[0] == 1
        journal.register("LEGACY_AFTER", CommandKind.ENTRY, {"legacy": True})


def test_none_margin_plan_never_zero_filled(tmp_path: Path) -> None:
    intent = actual_intent()
    with rejected("RISK_V2_MARGIN_UNVERIFIED"):
        r.entry_command_identity_sha256_v2(
            intent.envelope.scope.product_scope.account,
            intent.command_key,
            intent,
            r.ReservationV2(
                2,
                intent.envelope.scope.instrument,
                c.envelope_sha256_v2(intent.envelope),
                D(1),
                D(12),
                D(0),
            ),
        )


def test_exact_dto_shapes_and_closed_scalars() -> None:
    assert [field.name for field in fields(r.ReservationV2)] == [
        "schema_version",
        "instrument",
        "envelope_sha256",
        "base_quantity",
        "risk_amount",
        "margin_amount",
    ]
    assert [field.name for field in fields(r.ReservationAdmissionV2)] == [
        "account_scope",
        "owner_epoch",
        "expected_journal_revision",
        "expected_held_quantity_sha256",
        "expected_command_identity_sha256",
        "policy",
        "account",
        "marks",
        "now",
    ]
    original = reserve(fixture_values()[0])
    for name, bad in (
        ("schema_version", True),
        ("base_quantity", D(0)),
        ("risk_amount", D("NaN")),
        ("margin_amount", D(-1)),
        ("envelope_sha256", "A" * 64),
    ):
        with rejected("RISK_V2_INPUT_INVALID"):
            replace(original, **cast(dict[str, Any], {name: bad}))


@pytest.mark.parametrize("epoch", ["x" * 96, "a_.:/-09"])
def test_epoch_boundary_same_epoch_and_revision_max(tmp_path: Path, epoch: str) -> None:
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        assert (
            journal.bind_owner_epoch_v2(
                epoch, expected_previous_owner_epoch=None, expected_journal_revision=0
            )
            == 1
        )
        assert (
            journal.bind_owner_epoch_v2(
                epoch, expected_previous_owner_epoch=epoch, expected_journal_revision=1
            )
            == 1
        )
        assert (
            journal.bind_owner_epoch_v2(
                "NEXT", expected_previous_owner_epoch=epoch, expected_journal_revision=1
            )
            == 2
        )
        journal._db.execute("UPDATE reservation_meta_v2 SET capacity_revision=?", (2**63 - 1,))
        assert (
            journal.bind_owner_epoch_v2(
                "NEXT", expected_previous_owner_epoch="NEXT", expected_journal_revision=2**63 - 1
            )
            == 2**63 - 1
        )
        count = len(journal.audit_events())
        with rejected("RISK_V2_RESOURCE_LIMIT"):
            journal.bind_owner_epoch_v2(
                "THIRD", expected_previous_owner_epoch="NEXT", expected_journal_revision=2**63 - 1
            )
        assert len(journal.audit_events()) == count


@pytest.mark.parametrize("bad", ["x" * 97, "한글", " x", "x\n", "_x", True, None])
def test_epoch_rejected_without_lazy_mutation(tmp_path: Path, bad: Any) -> None:
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        with rejected("RISK_V2_INPUT_INVALID"):
            journal.bind_owner_epoch_v2(
                bad, expected_previous_owner_epoch=None, expected_journal_revision=0
            )
        assert not journal._db.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%_v2'"
        ).fetchall()
        for previous in (bad,):
            if previous is not None:
                with rejected("RISK_V2_INPUT_INVALID"):
                    journal.bind_owner_epoch_v2(
                        "OWNER_1",
                        expected_previous_owner_epoch=previous,
                        expected_journal_revision=0,
                    )


@pytest.mark.parametrize("bad", [True, -1, 2**63, D(1), "1"])
def test_revision_closed_domain(tmp_path: Path, bad: Any) -> None:
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        with rejected("RISK_V2_INPUT_INVALID"):
            journal.bind_owner_epoch_v2(
                "OWNER_1", expected_previous_owner_epoch=None, expected_journal_revision=bad
            )
        bound(journal)
        values = fixture_values()
        with rejected("RISK_V2_INPUT_INVALID"):
            admission(journal, values, expected_journal_revision=bad)
        with rejected("RISK_V2_INPUT_INVALID"):
            claim(journal, values[0], expected_journal_revision=bad)


def test_exclusion_preserves_all_held_identity_and_checks_every_key(tmp_path: Path) -> None:
    values = fixture_values()
    intent = values[0]
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        bound(journal)
        register(journal, values)
        full = snapshot(journal, intent)
        excluded = snapshot(journal, intent, "COMMAND")
        assert excluded.components == () and excluded.position_count == 0
        assert excluded.margin_amount == excluded.risk_amount == 0
        assert excluded.held_quantity_sha256 == full.held_quantity_sha256
        assert excluded.journal_revision == full.journal_revision
        assert excluded.snapshot_id != full.snapshot_id
        journal.register("CONTROL", CommandKind.CANCEL, {})
        for key in ("MISSING", "CONTROL"):
            with rejected():
                snapshot(journal, intent, key)
        journal._db.execute("UPDATE entry_reservations_v2 SET margin_amount='53.00000000'")
        with rejected():
            snapshot(journal, intent, "COMMAND")


@pytest.mark.parametrize("kind", list(CommandKind))
@pytest.mark.parametrize("scalar", [False, True])
def test_mixed_legacy_new_register_guards_every_kind(
    tmp_path: Path, kind: CommandKind, scalar: bool
) -> None:
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        bound(journal)
        register(journal, fixture_values())
        kwargs: dict[str, Any] = (
            {}
            if not scalar
            else {
                "reservation": Reservation(D(1), D(1), D(1)),
                "limits": ReservationLimits(D(100), D(100), D(100), 100, D(0), D(0), D(0), 0),
            }
        )
        if scalar or kind is CommandKind.ENTRY:
            with rejected(LEGACY):
                journal.register("LEGACY", kind, {}, **kwargs)
            assert journal.get("LEGACY") is None
        else:
            journal.register("LEGACY", kind, {}, **kwargs)
            claimed = journal.claim("LEGACY")
            assert claimed is not None and claimed.state is CommandState.SENDING


@pytest.mark.parametrize(
    "kind,scalar", [(CommandKind.ENTRY, False), *[(kind, True) for kind in CommandKind]]
)
def test_active_legacy_precedes_v2_replay_and_claim_state(
    tmp_path: Path, kind: CommandKind, scalar: bool
) -> None:
    values = fixture_values()
    intent = values[0]
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        bound(journal)
        register(journal, values)
        pins = admission(journal, values)
        assert claim(journal, intent) is not None
        # Fixture SQL represents an old writer that the upgraded API cannot fence.
        journal._db.execute(
            "INSERT INTO commands VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                *journal._scope,
                "OLD",
                "old_client",
                kind.value,
                "{}",
                "a" * 64,
                "PENDING",
                "1" if scalar else None,
                "1" if scalar else None,
                "1" if scalar else None,
                0,
            ),
        )
        with rejected(LEGACY):
            snapshot(journal, intent)
        with rejected(LEGACY):
            journal.register_entry_v2(
                "COMMAND", intent, reservation=reserve(intent), admission=pins
            )
        with rejected(LEGACY):
            journal.claim_entry_v2(
                "COMMAND",
                owner_epoch="OWNER_1",
                expected_journal_revision=2,
                expected_held_quantity_sha256=pins.expected_held_quantity_sha256,
                expected_command_identity_sha256=pins.expected_command_identity_sha256,
            )
        with rejected(LEGACY):
            journal.claim("OLD")
        journal._db.execute("UPDATE commands SET terminal=1 WHERE command_key='OLD'")
        assert snapshot(journal, intent).position_count == 1


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("UPDATE commands SET terminal=2", "RISK_V2_INPUT_INVALID"),
        ("UPDATE commands SET terminal='bad'", "RISK_V2_INPUT_INVALID"),
        ("UPDATE commands SET kind='CANCEL'", "RISK_V2_INPUT_INVALID"),
        ("UPDATE commands SET risk='1'", "RISK_V2_INPUT_INVALID"),
        ("UPDATE commands SET client_id='wrong'", CONFLICT),
        ("UPDATE commands SET payload_hash='" + "b" * 64 + "'", CONFLICT),
        ("UPDATE entry_reservations_v2 SET version=3", "RISK_V2_INPUT_INVALID"),
        ("UPDATE entry_reservations_v2 SET base_quantity='2'", CONFLICT),
        ("UPDATE entry_reservations_v2 SET base_quantity='1.0'", CONFLICT),
        ("UPDATE entry_reservations_v2 SET base_quantity='NaN'", "RISK_V2_INPUT_INVALID"),
        ("DELETE FROM entry_reservations_v2", "RISK_V2_INPUT_INVALID"),
        ("DELETE FROM reservation_meta_v2", "RISK_V2_INPUT_INVALID"),
        ("DELETE FROM commands", "RISK_V2_INPUT_INVALID"),
    ],
)
def test_retained_terminal_corruption_and_orphans_before_filter(
    tmp_path: Path, mutation: str, code: str
) -> None:
    values = fixture_values()
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        bound(journal)
        register(journal, values)
        journal.reject("COMMAND", "fixture rejection")
        journal.release_reservation(
            "COMMAND", confirmed_terminal=True, terminal_evidence="fixture final"
        )
        journal._db.execute(mutation)
        with rejected(code):
            journal.list_commands((CommandState.SENDING,))
        with rejected(code):
            snapshot(journal, values[0])


@pytest.mark.parametrize("version", [2, 3, None])
def test_reserved_frame_missing_row_never_downgrades(tmp_path: Path, version: Any) -> None:
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        frame = {"schema": "RISK_V2_DURABLE_INTENT", "wire_version": version}
        with rejected("RISK_V2_INPUT_INVALID"):
            journal.register("DOWNGRADE", CommandKind.ENTRY, frame)
        assert journal.get("DOWNGRADE") is None
        assert (
            journal.register("LEGIT", CommandKind.ENTRY, {"schema_version": 2}).reservation_v2
            is None
        )


def test_partial_unknown_missing_meta_and_other_scope_lazy_bind(tmp_path: Path) -> None:
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        journal._db.execute("PRAGMA user_version=27")
        bound(journal)
        journal._db.execute("DELETE FROM reservation_meta_v2")
        bound(journal)  # Empty scope remains a valid first-bind state.
        assert journal._db.execute("PRAGMA user_version").fetchone()[0] == 27
    with CommandJournal(tmp_path / "journal.db", ExecutionScope("OTHER", "DEMO")) as journal:
        bound(journal)
    with CommandJournal(tmp_path / "partial.db", SCOPE) as journal:
        journal._db.execute("CREATE TABLE reservation_meta_v2(x TEXT)")
        with rejected("RISK_V2_INPUT_INVALID"):
            bound(journal)
        assert not journal._db.execute(
            "SELECT name FROM sqlite_master WHERE name='entry_reservations_v2'"
        ).fetchall()
    with CommandJournal(tmp_path / "unknown.db", SCOPE) as journal:
        bound(journal)
        journal._db.execute("ALTER TABLE entry_reservations_v2 ADD COLUMN unexpected TEXT")
        with rejected("RISK_V2_INPUT_INVALID"):
            snapshot(journal, fixture_values()[0])


@pytest.mark.parametrize("failure", ["audit", "commit"])
def test_real_sqlite_failure_rolls_back_all_four_writes(tmp_path: Path, failure: str) -> None:
    path = tmp_path / "journal.db"
    values = fixture_values()
    with CommandJournal(path, SCOPE) as journal:
        bound(journal)
        before = snapshot(journal, values[0])
        if failure == "audit":
            journal._db.execute(
                "CREATE TRIGGER fail_v2 BEFORE INSERT ON audit "
                "WHEN NEW.action='REGISTER_ENTRY_V2' BEGIN SELECT RAISE(ABORT,'fixture audit'); END"
            )
        else:

            def deny_commit(
                action: int,
                first: str | None,
                second: str | None,
                database: str | None,
                trigger: str | None,
            ) -> int:
                return (
                    sqlite3.SQLITE_DENY
                    if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT"
                    else sqlite3.SQLITE_OK
                )

            journal._db.set_authorizer(deny_commit)
        with pytest.raises(sqlite3.DatabaseError):
            register(journal, values)
        journal._db.set_authorizer(None)
        assert not journal._db.in_transaction
        assert journal.get("COMMAND") is None
        assert snapshot(journal, values[0]) == before
        assert len(journal.audit_events()) == 1
        assert journal._db.execute("SELECT count(*) FROM entry_reservations_v2").fetchone()[0] == 0
    with CommandJournal(path, SCOPE) as reopened:
        assert reopened.get("COMMAND") is None
        assert snapshot(reopened, values[0]) == before


def test_claim_and_release_audit_rollback(tmp_path: Path) -> None:
    values = fixture_values()
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        bound(journal)
        register(journal, values)
        before = snapshot(journal, values[0])
        journal._db.execute(
            "CREATE TRIGGER fail_claim BEFORE INSERT ON audit "
            "WHEN NEW.action='CLAIM_ENTRY_V2' BEGIN SELECT RAISE(ABORT,'fixture claim'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            claim(journal, values[0])
        pending = journal.get("COMMAND")
        assert pending is not None and pending.state is CommandState.PENDING
        assert snapshot(journal, values[0]) == before
        journal.reject("COMMAND", "fixture rejection")
        journal._db.execute(
            "CREATE TRIGGER fail_release BEFORE INSERT ON audit "
            "WHEN NEW.action='RELEASE' BEGIN SELECT RAISE(ABORT,'fixture release'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            journal.release_reservation(
                "COMMAND", confirmed_terminal=True, terminal_evidence="fixture final"
            )
        unreleased = journal.get("COMMAND")
        assert unreleased is not None and not unreleased.terminal_confirmed
        assert snapshot(journal, values[0]) == before


@pytest.mark.parametrize(
    "policy_changes,account_changes,code",
    [
        ({"per_trade_loss": D(11)}, {}, "RISK_V2_TRADE_LOSS_LIMIT"),
        ({"max_total_risk": D(11)}, {}, "RISK_V2_TOTAL_RISK_LIMIT"),
        ({"max_daily_loss": D(11)}, {}, "RISK_V2_DAILY_LOSS_LIMIT"),
        ({"max_drawdown": D(11)}, {}, "RISK_V2_DRAWDOWN_LIMIT"),
        (
            {"max_consecutive_losses": 1},
            {"consecutive_losses": 1},
            "RISK_V2_CONSECUTIVE_LOSS_LIMIT",
        ),
        ({"max_positions": 1}, {"position_count": 1}, "RISK_V2_POSITION_LIMIT"),
        ({}, {"external_same_symbol_activity_ids": ("EXTERNAL",)}, "RISK_V2_EXTERNAL_CONFLICT"),
        ({"margin_allocation": D(51)}, {}, "RISK_V2_MARGIN_LIMIT"),
        ({}, {"available_margin": D(51)}, "RISK_V2_MARGIN_LIMIT"),
        ({"max_total_margin": D(52)}, {"total_margin": D(1)}, "RISK_V2_MARGIN_LIMIT"),
        ({}, {"loss_day": (NOW - timedelta(days=1)).date()}, "RISK_V2_LOSS_LEDGER_UNVERIFIED"),
        ({}, {"margin_mode": "CROSS"}, "RISK_V2_UNSUPPORTED_MODE"),
    ],
)
def test_full_current_economic_guards_atomic(
    tmp_path: Path, policy_changes: Any, account_changes: Any, code: str
) -> None:
    intent, policy, account, marks = fixture_values(policy_changes=policy_changes)
    values = (intent, policy, replace(account, **account_changes), marks)
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        bound(journal)
        before = snapshot(journal, intent)
        with rejected(code):
            register(journal, values)
        assert journal.get(intent.command_key) is None
        assert snapshot(journal, intent) == before
        assert len(journal.audit_events()) == 1


def test_current_mark_drift_exact_equality_and_hostile_context(tmp_path: Path) -> None:
    changes = {
        "max_total_risk": D(24),
        "max_daily_loss": D(24),
        "max_drawdown": D(24),
        "max_positions": 2,
        "max_total_margin": D(104),
        "margin_allocation": D(52),
        "caps": c.LinearCapitalCapsV2(
            D(100), D(200), "LINEAR_MARK_GROSS_AT_ADMISSION_V1", "NOT_REQUIRED", None
        ),
    }
    first = fixture_values(policy_changes=changes)
    second = fixture_values("SECOND", policy_changes=changes)
    first = (*first[:2], replace(first[2], available_margin=D(104)), first[3])
    second = (*second[:2], replace(second[2], available_margin=D(104)), second[3])
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        bound(journal)
        with localcontext() as context:
            context.prec = 1
            register(journal, first)
            drift = (*second[:3], (replace(second[3][0], mark_price=D(101)),))
            with rejected("RISK_V2_ORDER_ADMISSION_LIMIT"):
                register(journal, drift)
            register(journal, second)
            snap = snapshot(journal, first[0])
            assert snap.risk_amount == D(24) and snap.margin_amount == D(104)
            assert snap.position_count == 2
            assert snap.journal_revision == 3
        assert len(journal.audit_events()) == 3


def test_all_symbols_and_component_order_and_mark_coverage(tmp_path: Path) -> None:
    y = fixture_values("A_Y", symbol="YBBUSDT")
    x = fixture_values("Z_X")
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        bound(journal)
        register(journal, y)
        snap = snapshot(journal, x[0])
        assert [item.instrument.symbol for item in snap.components] == ["YBBUSDT"]
        with rejected("RISK_V2_MARK_COVERAGE_REQUIRED"):
            register(journal, x)
        combined = (x[3][0], y[3][0])
        register(journal, (*x[:3], combined))
        snap = snapshot(journal, x[0])
        assert [item.instrument.symbol for item in snap.components] == ["XAAUSDT", "YBBUSDT"]
        assert snap.risk_amount == D(24) and snap.margin_amount == D(104)
        assert snap.position_count == 2
        # Account overlap is checked against actual deterministic component IDs.
        component = replace(snap.components[0], origin="ACCOUNT_POSITION")
        z = fixture_values("THIRD")
        with rejected("RISK_V2_EXPOSURE_OVERLAP"):
            register(journal, (z[0], z[1], replace(z[2], components=(component,)), combined))


def test_refreshed_terminal_replay_skips_new_economic_gates(tmp_path: Path) -> None:
    values = fixture_values()
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        bound(journal)
        register(journal, values)
        journal.reject("COMMAND", "fixture rejection")
        terminal = journal.release_reservation(
            "COMMAND", confirmed_terminal=True, terminal_evidence="fixture final"
        )
        excluded = snapshot(journal, values[0], "COMMAND")
        assert excluded.position_count == 0
        count = len(journal.audit_events())
        stale_economics = (*values[:2], replace(values[2], available_margin=D(0)), values[3])
        assert register(journal, stale_economics) == terminal
        assert len(journal.audit_events()) == count
        with rejected():
            claim(journal, values[0], expected_command_identity_sha256="b" * 64)
        snap = snapshot(journal, values[0])
        with rejected():
            journal.claim_entry_v2(
                "ABSENT",
                owner_epoch="OWNER_1",
                expected_journal_revision=snap.journal_revision,
                expected_held_quantity_sha256=snap.held_quantity_sha256,
                expected_command_identity_sha256="b" * 64,
            )


def test_separate_connection_competitors_one_capacity_winner(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    values = fixture_values()
    with CommandJournal(path, SCOPE) as journal:
        bound(journal)
        captured = admission(journal, values)
    barrier = Barrier(2)

    def compete(key: str) -> str:
        intent = replace(values[0], command_key=key)
        pins = replace(
            captured,
            expected_command_identity_sha256=r.entry_command_identity_sha256_v2(
                values[2].account, key, intent, reserve(intent)
            ),
        )
        with CommandJournal(path, SCOPE) as journal:
            barrier.wait(timeout=10)
            try:
                journal.register_entry_v2(key, intent, reservation=reserve(intent), admission=pins)
            except c.RiskV2Rejected as error:
                return error.code
        return "WINNER"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, ("FIRST", "SECOND")))
    assert sorted(results) == sorted(["WINNER", CONFLICT])
    with CommandJournal(path, SCOPE) as journal:
        assert len(journal.list_commands()) == 1
        assert snapshot(journal, values[0]).journal_revision == 2
        assert len(journal.audit_events()) == 2


def test_prospective_postinsert_aggregate_64k_rolls_back(tmp_path: Path) -> None:
    """A fitting current set plus individually fitting candidate exceeds 64 KiB."""
    values = fixture_values("CANDIDATE" + "X" * 87)
    intent = values[0]
    reservation = reserve(intent)
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        bound(journal)
        # Independent stdlib projection from literal field names, never _held_hash.
        template = {
            "command_key": intent.command_key,
            "client_order_id": CLIENT,
            "payload_hash": "a" * 64,
            "version": 2,
            "instrument": {"venue": "BYBIT", "product": "LINEAR", "symbol": "XAAUSDT"},
            "base_quantity": "1.00",
            "envelope_sha256": reservation.envelope_sha256,
            "risk_amount": "12.0000",
            "margin_amount": "52.00000000",
            "position_count": 1,
        }

        def length(count: int) -> int:
            return len(
                json.dumps(
                    {
                        "schema": "RISK_V2_CANONICAL_V1",
                        "record_type": "LocalHeldQuantitySetV2",
                        "fields": {
                            "account_id": "한글",
                            "environment": "DEMO",
                            "entries": [template] * count,
                        },
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("ascii")
            )

        count = 0
        while length(count + 1) <= 65536:
            count += 1
        assert length(count) <= 65536 < length(count + 1)
        # These are synthetic, codec-valid existing rows. Direct fixture SQL avoids
        # turning test preparation into a quadratic sequence of journal admissions.
        with journal._transaction():
            for index in range(count):
                key = ("SEED" + str(index)).ljust(96, "X")
                seed = replace(intent, command_key=key)
                wire = encode_entry_intent_v2(seed)
                payload_hash = hashlib.sha256(wire.encode()).hexdigest()
                client = (
                    "ec_"
                    + hashlib.sha256(
                        json.dumps(
                            ["한글", "DEMO", key], separators=(",", ":"), ensure_ascii=True
                        ).encode()
                    ).hexdigest()[:32]
                )
                journal._db.execute(
                    "INSERT INTO commands VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        *journal._scope,
                        key,
                        client,
                        "ENTRY",
                        wire,
                        payload_hash,
                        "PENDING",
                        None,
                        None,
                        None,
                        0,
                    ),
                )
                journal._db.execute(
                    "INSERT INTO entry_reservations_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        *journal._scope,
                        key,
                        2,
                        "BYBIT",
                        "LINEAR",
                        "XAAUSDT",
                        reservation.envelope_sha256,
                        "1.00",
                        "12.0000",
                        "52.00000000",
                    ),
                )
            journal._db.execute("UPDATE reservation_meta_v2 SET capacity_revision=?", (count + 1,))
        before = snapshot(journal, intent)
        assert before.position_count == count and count < 1024
        count_audit = len(journal.audit_events())
        with rejected("RISK_V2_RESOURCE_LIMIT"):
            register(journal, values)
        assert journal.get(intent.command_key) is None
        assert snapshot(journal, intent) == before
        assert len(journal.audit_events()) == count_audit
        assert (
            journal._db.execute("SELECT count(*) FROM entry_reservations_v2").fetchone()[0] == count
        )


def test_mark100_and_component1024_bounds(tmp_path: Path) -> None:
    values = fixture_values()
    mark = values[3][0]
    marks = tuple(
        replace(mark, instrument=replace(mark.instrument, symbol=f"K{i:03}USDT"))
        for i in range(101)
    )
    assert len(c._marks_payload_v2(marks[:100])) == 100
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        bound(journal)
        with rejected("RISK_V2_RESOURCE_LIMIT"):
            admission(journal, values, marks=marks)
    record = CommandRecord(
        "COMMAND",
        CLIENT,
        CommandKind.ENTRY,
        encode_entry_intent_v2(values[0]),
        "a" * 64,
        CommandState.PENDING,
        None,
        False,
        reserve(values[0]),
    )
    with rejected("RISK_V2_RESOURCE_LIMIT"):
        r._held_hash(
            values[2].account, tuple(replace(record, command_key=f"K{i}") for i in range(1025))
        )


def test_all_local_pin_mismatches_and_scope_and_freshness(tmp_path: Path) -> None:
    values = fixture_values()
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        bound(journal)
        good = admission(journal, values)
        for changes in (
            {"owner_epoch": "OTHER"},
            {"expected_journal_revision": 0},
            {"expected_held_quantity_sha256": "b" * 64},
            {"expected_command_identity_sha256": "b" * 64},
        ):
            with rejected():
                journal.register_entry_v2(
                    "COMMAND",
                    values[0],
                    reservation=reserve(values[0]),
                    admission=replace(good, **changes),
                )
        with rejected("RISK_V2_SCOPE_MISMATCH"):
            journal.reservation_snapshot_v2(
                replace(
                    values[0].envelope.scope,
                    product_scope=replace(
                        values[0].envelope.scope.product_scope,
                        account=replace(values[2].account, account_id="OTHER"),
                    ),
                ),
                now=NOW,
                owner_generation="OWNER_1",
                exclude=None,
            )
        with rejected("RISK_V2_INPUT_INVALID"):
            journal.reservation_snapshot_v2(
                values[0].envelope.scope, now=NOW, owner_generation="한글", exclude=None
            )
        with rejected("RISK_V2_FUTURE_EVIDENCE"):
            journal.register_entry_v2(
                "COMMAND",
                values[0],
                reservation=reserve(values[0]),
                admission=replace(good, now=NOW - timedelta(microseconds=1)),
            )
        with rejected("RISK_V2_STALE_EVIDENCE"):
            journal.register_entry_v2(
                "COMMAND",
                values[0],
                reservation=reserve(values[0]),
                admission=replace(good, now=NOW + timedelta(seconds=11)),
            )
        assert journal.get("COMMAND") is None
        assert snapshot(journal, values[0]).journal_revision == 1


def test_crosssymbol_current_mark_revalues_existing_hold(tmp_path: Path) -> None:
    caps = c.LinearCapitalCapsV2(
        D(1000), D(300), "LINEAR_MARK_GROSS_AT_ADMISSION_V1", "NOT_REQUIRED", None
    )
    y = fixture_values("Y", symbol="YBBUSDT", policy_changes={"caps": caps})
    x = fixture_values("X", policy_changes={"caps": caps})
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        bound(journal)
        register(journal, y)
        before = snapshot(journal, x[0])
        marks = (x[3][0], replace(y[3][0], mark_price=D(201)))
        with rejected("RISK_V2_ACCOUNT_ADMISSION_LIMIT"):
            register(journal, (*x[:3], marks))
        assert snapshot(journal, x[0]) == before
        # Exact equality at the aggregate limit is admitted.
        register(journal, (*x[:3], (x[3][0], replace(y[3][0], mark_price=D(200)))))


def test_revision_max_blocks_insert_and_release_atomically(tmp_path: Path) -> None:
    values = fixture_values()
    with CommandJournal(tmp_path / "insert.db", SCOPE) as journal:
        bound(journal)
        journal._db.execute("UPDATE reservation_meta_v2 SET capacity_revision=?", (2**63 - 1,))
        with rejected("RISK_V2_RESOURCE_LIMIT"):
            register(journal, values)
        assert journal.get("COMMAND") is None
        assert snapshot(journal, values[0]).journal_revision == 2**63 - 1
    with CommandJournal(tmp_path / "release.db", SCOPE) as journal:
        bound(journal)
        register(journal, values)
        journal.reject("COMMAND", "fixture rejection")
        journal._db.execute("UPDATE reservation_meta_v2 SET capacity_revision=?", (2**63 - 1,))
        count = len(journal.audit_events())
        with rejected("RISK_V2_RESOURCE_LIMIT"):
            journal.release_reservation(
                "COMMAND", confirmed_terminal=True, terminal_evidence="fixture final"
            )
        record = journal.get("COMMAND")
        assert record is not None and not record.terminal_confirmed
        assert len(journal.audit_events()) == count
        assert snapshot(journal, values[0]).position_count == 1


def test_bind_ddl_audit_failure_and_missing_meta_cannot_reset_history(tmp_path: Path) -> None:
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        journal._db.execute(
            "CREATE TRIGGER fail_bind BEFORE INSERT ON audit "
            "WHEN NEW.action='BIND_OWNER_EPOCH_V2' BEGIN SELECT RAISE(ABORT,'fixture bind'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            bound(journal)
        assert not journal._db.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%_v2'"
        ).fetchall()
        journal._db.execute("DROP TRIGGER fail_bind")
        bound(journal)
        register(journal, fixture_values())
        journal.reject("COMMAND", "fixture rejection")
        journal.release_reservation(
            "COMMAND", confirmed_terminal=True, terminal_evidence="fixture final"
        )
        journal._db.execute("DELETE FROM reservation_meta_v2")
        with rejected("RISK_V2_INPUT_INVALID"):
            bound(journal)
        assert journal._db.execute("SELECT count(*) FROM reservation_meta_v2").fetchone()[0] == 0


def test_replay_structural_scope_and_owner_change_pins(tmp_path: Path) -> None:
    values = fixture_values()
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        bound(journal)
        register(journal, values)
        pins = admission(journal, values)
        with rejected("RISK_V2_SCOPE_MISMATCH"):
            replace(
                pins,
                account=replace(values[2], account=replace(values[2].account, account_id="OTHER")),
            )
        assert (
            journal.bind_owner_epoch_v2(
                "OWNER_2", expected_previous_owner_epoch="OWNER_1", expected_journal_revision=2
            )
            == 3
        )
        with rejected():
            journal.register_entry_v2(
                "COMMAND", values[0], reservation=reserve(values[0]), admission=pins
            )
        with rejected():
            journal.claim_entry_v2(
                "COMMAND",
                owner_epoch="OWNER_1",
                expected_journal_revision=2,
                expected_held_quantity_sha256=pins.expected_held_quantity_sha256,
                expected_command_identity_sha256=pins.expected_command_identity_sha256,
            )
        fresh = journal.reservation_snapshot_v2(
            values[0].envelope.scope, now=NOW, owner_generation="OWNER_2", exclude=None
        )
        assert fresh.held_quantity_sha256 == pins.expected_held_quantity_sha256
        assert fresh.journal_revision == 3


def test_terminal_unreserved_v1_claim_is_capacity_neutral(tmp_path: Path) -> None:
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        legacy = journal.register("OLD", CommandKind.ENTRY, {"legacy": True})
        journal.reject("OLD", "fixture rejection")
        terminal = journal.release_reservation(
            "OLD", confirmed_terminal=True, terminal_evidence="fixture final"
        )
        assert terminal.reservation is None and legacy.reservation_v2 is None
        bound(journal)
        register(journal, fixture_values())
        assert journal.register("OLD", CommandKind.ENTRY, {"legacy": True}) == terminal
        assert journal.claim("OLD") is None


def test_lazy_bind_and_empty_snapshot(tmp_path: Path) -> None:
    with CommandJournal(tmp_path / "journal.db", SCOPE) as journal:
        assert not journal._db.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%_v2'"
        ).fetchall()
        assert (
            journal.bind_owner_epoch_v2(
                "OWNER_1", expected_previous_owner_epoch=None, expected_journal_revision=0
            )
            == 1
        )
        intent = actual_intent()
        snapshot = journal.reservation_snapshot_v2(
            intent.envelope.scope, now=NOW, owner_generation="OWNER_1", exclude=None
        )
        assert snapshot.components == ()
        assert snapshot.journal_revision == 1
        assert snapshot.risk_amount == snapshot.margin_amount == 0
