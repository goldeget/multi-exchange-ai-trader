import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

import pytest

from multi_exchange_trader.execution import history_store
from multi_exchange_trader.execution.history import (
    ExecutionRecord,
    HistoryError,
    HistorySnapshot,
    HistoryWindow,
    OrderHistoryRecord,
    TransactionRecord,
)
from multi_exchange_trader.execution.history_store import HistoryStore, validate_history_database
from multi_exchange_trader.execution.protection import ProtectionScope

SCOPE = ProtectionScope("12345", "DEMO")
NOW = datetime(2026, 11, 2, tzinfo=UTC)
WINDOW = HistoryWindow(1000, 10000)
D = Decimal


def snapshot(**changes: Any) -> HistorySnapshot:
    return replace(HistorySnapshot(SCOPE, "BTCUSDT", "snapshot1", WINDOW, NOW, NOW,
                                  (), (), (), True), **changes)


def execution(**changes: Any) -> ExecutionRecord:
    return replace(ExecutionRecord("e1", "o1", "c1", "BTCUSDT", "Buy", "Trade", 2000,
        D("0.4"), D("100"), D("40"), D("0.01"), "USDT", D("0.001"), D(0)), **changes)


def order(**changes: Any) -> OrderHistoryRecord:
    return replace(OrderHistoryRecord("o1", "c1", "BTCUSDT", "Buy", 0, "PartiallyFilled",
        D(1), D("0.4"), D("0.6"), False, 1000, 2000), **changes)


def transaction(**changes: Any) -> TransactionRecord:
    return replace(TransactionRecord("t1", "", "", "", "", "USDT", "TRANSFER_IN", 2000,
        D("12345678901234567890.123456789012345678"), D(0), D(0),
        D("12345678901234567890.123456789012345678"), D("999")), **changes)


def counts(path: Path) -> tuple[int, ...]:
    with sqlite3.connect(path) as db:
        return tuple(db.execute(f"SELECT count(*) FROM history_{table}").fetchone()[0]
                     for table in ("scope", "snapshots", "variants", "members", "conflicts"))


def test_exact_replay_and_decimal_equivalence_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    with HistoryStore(path, SCOPE) as store, localcontext() as ctx:
        ctx.prec = 2
        first = store.ingest(snapshot(executions=(execution(),)))
        again = store.ingest(snapshot(executions=(execution(quantity=D("0.4000"),
                                                           closed_quantity=D("-0.0")),)))
        assert first.new_variant_count == 1 and not first.duplicate
        assert again.duplicate and again.observation_key == first.observation_key
        assert again.new_variant_count == again.new_conflict_count == 0
    assert counts(path) == (1, 1, 1, 1, 0)


def test_account_transactions_share_one_variant_across_symbols(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    with HistoryStore(path, SCOPE) as store:
        store.ingest(snapshot(transactions=(transaction(),)))
        store.ingest(snapshot(symbol="ETHUSDT", snapshot_id="snapshot2",
                              transactions=(transaction(),)))
    assert counts(path) == (1, 2, 1, 2, 0)
    with sqlite3.connect(path) as db:
        payload = db.execute("SELECT payload FROM history_variants").fetchone()[0]
        assert '12345678901234567890.123456789012345678' in payload
        assert validate_history_database(db) == SCOPE


def test_changed_event_payload_persists_both_variants_and_conflict_after_reopen(
    tmp_path: Path,
) -> None:
    path = tmp_path / "history.sqlite3"
    changed = snapshot(snapshot_id="snapshot2", executions=(execution(fee=D("-0.01")),))
    with HistoryStore(path, SCOPE) as store:
        store.ingest(snapshot(executions=(execution(),)))
        receipt = store.ingest(changed)
        assert receipt.new_conflict_count == 1
    with HistoryStore(path, SCOPE) as store:
        replay = store.ingest(changed)
        assert replay.duplicate and replay.new_conflict_count == 0
        assert replay.conflict_keys == receipt.conflict_keys
    assert counts(path) == (1, 2, 2, 2, 1)


def test_order_update_collision_regression_and_out_of_order_arrival(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    with HistoryStore(path, SCOPE) as store:
        assert store.ingest(snapshot(orders=(order(updated_ms=3000,
            cumulative_filled=D("0.8"), leaves_quantity=D("0.2")),))).new_conflict_count == 0
        assert store.ingest(snapshot(snapshot_id="older", orders=(order(),),
            executions=(execution(),))).new_conflict_count == 0
        assert store.ingest(snapshot(snapshot_id="later", orders=(order(updated_ms=4000),)
            )).new_conflict_count == 1
        assert store.ingest(snapshot(snapshot_id="collision", orders=(order(updated_ms=4000,
            cumulative_filled=D("0.5"), leaves_quantity=D("0.5")),))).new_conflict_count == 2
    with sqlite3.connect(path) as db:
        assert {r[0] for r in db.execute("SELECT reason FROM history_conflicts")} == {
            "ORDER_FILL_REGRESSION", "ORDER_UPDATE_COLLISION"}
        validate_history_database(db)


def test_snapshot_id_reuse_is_durable_conflict(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    with HistoryStore(path, SCOPE) as store:
        store.ingest(snapshot())
        assert store.ingest(snapshot(executions=(execution(),))).new_conflict_count == 1
    with HistoryStore(path, SCOPE):
        assert counts(path) == (1, 2, 1, 1, 1)


@pytest.mark.parametrize("changes", [
    {"scope": ProtectionScope("other", "DEMO")}, {"transport_injected": 1},
    {"executions": [execution()]}, {"executions": (execution(quantity=1),)},
    {"executions": (execution(side="BUY"),)}, {"executions": (execution(), execution())},
    {"executions": (execution(time_ms=True),)}, {"observed_at": NOW.replace(tzinfo=None)},
    {"transactions": (transaction(change=D(1)),)},
    {"orders": (order(cumulative_filled=D(2)),)},
])
def test_scope_and_forged_typed_snapshot_rejected_without_rows(
    tmp_path: Path, changes: dict[str, Any],
) -> None:
    path = tmp_path / "history.sqlite3"
    with HistoryStore(path, SCOPE) as store, pytest.raises(HistoryError):
        store.ingest(snapshot(**changes))
    assert counts(path) == (1, 0, 0, 0, 0)


@pytest.mark.parametrize("table", ["history_snapshots", "history_members", "history_conflicts"])
def test_atomic_failure_before_commit_leaves_no_receipt_or_partial_rows(
    tmp_path: Path, table: str,
) -> None:
    path = tmp_path / "history.sqlite3"
    with HistoryStore(path, SCOPE) as store:
        store.ingest(snapshot(executions=(execution(),)))
        # The authorizer fails the actual SQLite statement after preceding writes.
        store._db.set_authorizer(lambda action, name, _c, _d, _s:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and name == table
            else sqlite3.SQLITE_OK)
        with pytest.raises(HistoryError):
            store.ingest(snapshot(snapshot_id="changed", executions=(execution(fee=D(2)),)))
        store._db.set_authorizer(None)
    with HistoryStore(path, SCOPE):
        assert counts(path) == (1, 1, 1, 1, 0)


@pytest.mark.parametrize("table", ["scope", "snapshots", "variants", "members", "conflicts"])
def test_append_only_triggers_reject_update_and_delete(tmp_path: Path, table: str) -> None:
    path = tmp_path / "history.sqlite3"
    with HistoryStore(path, SCOPE) as store:
        store.ingest(snapshot(executions=(execution(),)))
        store.ingest(snapshot(snapshot_id="changed", executions=(execution(fee=D(1)),)))
    with sqlite3.connect(path) as db:
        column = db.execute(f"PRAGMA table_info(history_{table})").fetchone()[1]
        for sql in (f"DELETE FROM history_{table}",
                    f"UPDATE history_{table} SET {column}={column}"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(sql)


@pytest.mark.parametrize("tamper", [
    "DROP TRIGGER history_members_no_delete",
    "CREATE VIEW hidden AS SELECT * FROM history_scope",
    "PRAGMA user_version=2",
    "DROP TRIGGER history_variants_no_update; UPDATE history_variants SET event_ms=9999",
    "DROP TRIGGER history_conflicts_no_delete; DELETE FROM history_conflicts",
])
def test_corrupt_schema_or_evidence_rejected(tmp_path: Path, tamper: str) -> None:
    path = tmp_path / "history.sqlite3"
    with HistoryStore(path, SCOPE) as store:
        store.ingest(snapshot(executions=(execution(),)))
        store.ingest(snapshot(snapshot_id="changed", executions=(execution(fee=D(1)),)))
    with sqlite3.connect(path) as db:
        original = list(db.execute("SELECT sql FROM sqlite_master WHERE type='trigger'"))
        db.executescript(tamper)
        # Restore removed guards to exercise content validation independently.
        if ";" in tamper:
            for (sql,) in original:
                db.execute(sql.replace("CREATE TRIGGER ", "CREATE TRIGGER IF NOT EXISTS "))
        db.commit()
        with pytest.raises(HistoryError):
            validate_history_database(db)
    with pytest.raises(HistoryError):
        HistoryStore(path, SCOPE)


@pytest.mark.parametrize("replacement,index_sql,valid", [
    ("", "", False),
    ("", "CREATE UNIQUE INDEX candidate ON history_variants(kind,record_id,variant_hash)", True),
    ("", ("CREATE UNIQUE INDEX candidate ON history_variants(kind,record_id,variant_hash) "
     "WHERE kind='ORDER'"), False),
    ("", ("CREATE UNIQUE INDEX candidate ON history_variants(kind COLLATE NOCASE,record_id,"
     "variant_hash)"), False),
    ("", "CREATE UNIQUE INDEX candidate ON history_variants(kind,record_id,record_id)", False),
    ("", "CREATE UNIQUE INDEX candidate ON history_variants(kind,record_id,lower(variant_hash))",
     False),
])
def test_strict_schema_requires_full_binary_identity(
    tmp_path: Path, replacement: str, index_sql: str, valid: bool,
) -> None:
    original = tmp_path / "original.sqlite3"
    target = tmp_path / "candidate.sqlite3"
    with HistoryStore(original, SCOPE):
        pass
    with sqlite3.connect(original) as source, sqlite3.connect(target) as db:
        for (sql,) in source.execute("SELECT sql FROM sqlite_master WHERE type='table'"):
            db.execute(sql.replace(", PRIMARY KEY(kind,record_id,variant_hash)", replacement))
        if index_sql:
            db.execute(index_sql)
        for (sql,) in source.execute("SELECT sql FROM sqlite_master WHERE type='trigger'"):
            db.execute(sql)
        db.execute("INSERT INTO history_scope VALUES (1,'12345','DEMO')")
        db.execute("PRAGMA user_version=1")
        if valid:
            assert validate_history_database(db) == SCOPE
        else:
            with pytest.raises(HistoryError):
                validate_history_database(db)


@pytest.mark.parametrize("before,after", [
    ("CHECK(id=1)", ""),
    ("CHECK(id=1)", "/* CHECK(id=1) */"),
    ("CHECK(id=1)", 'CONSTRAINT "CHECK(id=1)" CHECK(id>=0)'),
    ("CHECK(id=1)", 'CONSTRAINT [CHECK(id=1)] CHECK(id>=0)'),
    ("CHECK(id=1)", 'CONSTRAINT `CHECK(id=1)` CHECK(id>=0)'),
    ("CHECK(id=1)", ('CONSTRAINT "/*" CHECK(id>=0) /* harmless */ '
     'CONSTRAINT "CHECK(id=1)" CHECK(id>=0)')),
    ("CHECK(id=1)", ('CONSTRAINT [/*] CHECK(id>=0) /* harmless */ '
     'CONSTRAINT [CHECK(id=1)] CHECK(id>=0)')),
    ("CHECK(id=1)", ('CONSTRAINT `/*` CHECK(id>=0) /* harmless */ '
     'CONSTRAINT `CHECK(id=1)` CHECK(id>=0)')),
    ("CHECK(id=1)", ('CONSTRAINT "--" CHECK(id>=0) -- harmless\n'
     'CONSTRAINT "CHECK(id=1)" CHECK(id>=0)')),
    ("CHECK(id=1)", ('CONSTRAINT [--] CHECK(id>=0) -- harmless\n'
     'CONSTRAINT [CHECK(id=1)] CHECK(id>=0)')),
    ("CHECK(id=1)", ('CONSTRAINT `--` CHECK(id>=0) -- harmless\n'
     'CONSTRAINT `CHECK(id=1)` CHECK(id>=0)')),
    ("REFERENCES history_snapshots(observation_key)",
     "REFERENCES history_snapshots(observation_key) ON DELETE CASCADE"),
    ("BEFORE UPDATE ON history_members", "AFTER UPDATE ON history_members"),
    ("SELECT RAISE(ABORT, 'history is append-only')", "SELECT 1"),
    ("history is append-only", "history  is append-only"),
])
def test_missing_check_or_changed_foreign_keys_or_trigger_body_rejected(
    tmp_path: Path, before: str, after: str,
) -> None:
    original = tmp_path / "original.sqlite3"
    target = tmp_path / "candidate.sqlite3"
    with HistoryStore(original, SCOPE):
        pass
    with sqlite3.connect(original) as source, sqlite3.connect(target) as db:
        for (sql,) in source.execute("SELECT sql FROM sqlite_master WHERE type IN ('table','trigger')"
                                     " ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END"):
            db.execute(sql.replace(before, after))
        db.execute("INSERT INTO history_scope VALUES (1,'12345','DEMO')")
        db.execute("PRAGMA user_version=1")
        with pytest.raises(HistoryError):
            validate_history_database(db)


def test_validator_accepts_readonly_connection_and_preserves_row_factory(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    with HistoryStore(path, SCOPE) as store:
        store.ingest(snapshot(executions=(execution(),)))
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        assert validate_history_database(db) == SCOPE
        assert db.row_factory is sqlite3.Row


def test_no_repair_of_partial_or_empty_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    path.touch()
    with pytest.raises(HistoryError):
        HistoryStore(path, SCOPE)
    assert path.read_bytes() == b""


def test_snapshot_minimum_server_time_does_not_reject_later_order_page(tmp_path: Path) -> None:
    with HistoryStore(tmp_path / "history.sqlite3", SCOPE) as store:
        receipt = store.ingest(snapshot(server_observed_at=datetime.fromtimestamp(1, UTC),
                                        orders=(order(updated_ms=3000),)))
        assert not receipt.duplicate


def test_store_rejects_another_account_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    with HistoryStore(path, SCOPE):
        pass
    with pytest.raises(HistoryError):
        HistoryStore(path, ProtectionScope("other", "DEMO"))


def test_owner_thread_and_closed_store_fail_with_fixed_diagnostics(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    store = HistoryStore(path, SCOPE)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor, pytest.raises(HistoryError):
            executor.submit(store.ingest, snapshot()).result()
    finally:
        store.close()
    with pytest.raises(HistoryError):
        store.ingest(snapshot())
    assert counts(path) == (1, 0, 0, 0, 0)


def test_account_transaction_conflict_crosses_query_symbols(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    with HistoryStore(path, SCOPE) as store:
        store.ingest(snapshot(transactions=(transaction(),)))
        receipt = store.ingest(snapshot(snapshot_id="eth", symbol="ETHUSDT",
            transactions=(transaction(cash_balance=D(998)),)))
        assert receipt.new_variant_count == receipt.new_conflict_count == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT kind,reason FROM history_conflicts").fetchall() == [
            ("TRANSACTION", "EVENT_CONTENT_CHANGED")]


def test_order_amendments_and_normal_progress_are_not_identity_conflicts(tmp_path: Path) -> None:
    with HistoryStore(tmp_path / "history.sqlite3", SCOPE) as store:
        store.ingest(snapshot(orders=(order(),), executions=(execution(),)))
        receipt = store.ingest(snapshot(snapshot_id="later", orders=(order(quantity=D(2),
            cumulative_filled=D("0.8"), leaves_quantity=D("1.2"), updated_ms=4000),)))
        assert receipt.new_variant_count == 1 and receipt.new_conflict_count == 0


def test_order_identity_conflict_and_repeated_pair_evidence(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    with HistoryStore(path, SCOPE) as store:
        store.ingest(snapshot(orders=(order(),)))
        receipt = store.ingest(snapshot(snapshot_id="eth", symbol="ETHUSDT",
            orders=(order(symbol="ETHUSDT", updated_ms=3000),)))
        assert receipt.new_conflict_count == 1
        receipt = store.ingest(snapshot(snapshot_id="same-btc", orders=(order(),)))
        assert receipt.new_variant_count == 0 and receipt.new_conflict_count == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT reason FROM history_conflicts").fetchall() == [
            ("ORDER_IDENTITY_CHANGED",), ("ORDER_IDENTITY_CHANGED",)]
        validate_history_database(db)


@pytest.mark.parametrize("sql", [
    "CREATE TABLE sqliteXhidden(payload TEXT)",
    "CREATE VIEW sqliteXhidden AS SELECT * FROM history_scope",
    "CREATE TRIGGER sqliteXhidden AFTER INSERT ON history_scope BEGIN SELECT 1; END",
])
def test_user_objects_resembling_sqlite_prefix_are_not_hidden(tmp_path: Path, sql: str) -> None:
    path = tmp_path / "history.sqlite3"
    with HistoryStore(path, SCOPE):
        pass
    with sqlite3.connect(path) as db:
        db.execute(sql)
        with pytest.raises(HistoryError, match="^HISTORY_DATABASE_INVALID$"):
            validate_history_database(db)


@pytest.mark.parametrize("trap_invalid", [True, False])
def test_malformed_persisted_decimal_has_fixed_error_without_repair(
    tmp_path: Path, trap_invalid: bool,
) -> None:
    path = tmp_path / "history.sqlite3"
    with HistoryStore(path, SCOPE) as store:
        store.ingest(snapshot(executions=(execution(),)))
    with sqlite3.connect(path) as db:
        trigger = db.execute("SELECT sql FROM sqlite_master "
                             "WHERE name='history_variants_no_update'").fetchone()[0]
        db.execute("DROP TRIGGER history_variants_no_update")
        db.execute("UPDATE history_variants SET payload=replace(payload,?,?)",
                   ('"quantity":"0.4"', '"quantity":"invalid-decimal"'))
        db.execute(trigger)
    db.close()
    original = path.read_bytes()
    with localcontext() as context:
        context.traps[InvalidOperation] = trap_invalid
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db, pytest.raises(
                HistoryError, match="^HISTORY_DATABASE_INVALID$"):
            validate_history_database(db)
        with pytest.raises(HistoryError, match="^HISTORY_DATABASE_INVALID$"):
            HistoryStore(path, SCOPE)
    assert path.read_bytes() == original


def test_full_integrity_compares_distinct_variants_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    comparisons = 0
    original_reasons = history_store._reasons

    def count_reasons(left: Any, right: Any) -> tuple[str, ...]:
        nonlocal comparisons
        comparisons += 1
        return original_reasons(left, right)

    monkeypatch.setattr(history_store, "_reasons", count_reasons)
    observations = {f"key-{n}": snapshot(snapshot_id=f"snapshot-{n}", executions=(execution(),))
                    for n in range(100)}
    assert history_store._required_conflicts(observations) == {}
    assert comparisons == 0  # 100 identical observations have one distinct variant.
    observations.update({f"changed-{n}": snapshot(snapshot_id=f"changed-{n}",
                         executions=(execution(fee=D(2)),)) for n in range(4)})
    conflicts = history_store._required_conflicts(observations)
    assert comparisons == 1
    assert len(conflicts) == 400  # Every 100 x 4 contradictory observation pair is retained.
    assert {c.reason for c in conflicts.values()} == {"EVENT_CONTENT_CHANGED"}


def test_ingest_reads_only_related_evidence_and_replay_skips_content_comparisons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "history.sqlite3"
    with HistoryStore(path, SCOPE) as store:
        store.ingest(snapshot(snapshot_id="unrelated", executions=(execution(execution_id="other"),)))

        def reject_whole_history(*_args: Any) -> Any:
            pytest.fail("ingest reconstructed or compared unrelated historical observations")

        monkeypatch.setattr(history_store, "_read_evidence", reject_whole_history)
        monkeypatch.setattr(history_store, "_required_conflicts", reject_whole_history)
        comparisons: list[tuple[str, str]] = []
        original_reasons = history_store._reasons

        def count_reasons(left: Any, right: Any) -> tuple[str, ...]:
            comparisons.append((left.execution_id, right.execution_id))
            return original_reasons(left, right)

        monkeypatch.setattr(history_store, "_reasons", count_reasons)
        for n in range(12):
            store.ingest(snapshot(snapshot_id=f"same-{n}", executions=(execution(),)))
        assert comparisons == []
        changed = snapshot(snapshot_id="changed", executions=(execution(fee=D(2)),))
        receipt = store.ingest(changed)
        assert comparisons == [("e1", "e1")]
        assert receipt.new_conflict_count == 12
        comparisons.clear()
        replay = store.ingest(changed)
        assert replay.duplicate and replay.conflict_keys == receipt.conflict_keys
        assert comparisons == []
        # A later matching observation gains new pair evidence, never clears old conflicts.
        later = store.ingest(snapshot(snapshot_id="same-later", executions=(execution(),)))
        assert later.new_variant_count == 0 and later.new_conflict_count == 1
    monkeypatch.undo()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM history_conflicts").fetchone()[0] == 13
        assert validate_history_database(db) == SCOPE


@pytest.mark.parametrize("constraint", [
    'CONSTRAINT "/*" CHECK(id=1) /* harmless */',
    'CONSTRAINT [/*] CHECK(id=1) /* harmless */',
    'CONSTRAINT `/*` CHECK(id=1) /* harmless */',
    'CONSTRAINT "--" CHECK(id=1) -- harmless\n',
])
def test_actual_scope_check_survives_comment_markers_in_identifiers(
    tmp_path: Path, constraint: str,
) -> None:
    original = tmp_path / "original.sqlite3"
    target = tmp_path / "candidate.sqlite3"
    with HistoryStore(original, SCOPE):
        pass
    with sqlite3.connect(original) as source, sqlite3.connect(target) as db:
        for (sql,) in source.execute("SELECT sql FROM sqlite_master WHERE type IN ('table','trigger')"
                                     " ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END"):
            db.execute(sql.replace("CHECK(id=1)", constraint))
        db.execute("INSERT INTO history_scope VALUES (1,'12345','DEMO')")
        db.execute("PRAGMA user_version=1")
        assert validate_history_database(db) == SCOPE
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO history_scope VALUES (2,'other','LIVE')")


@pytest.mark.parametrize("clause,valid", [
    ("COLLATE/**/NOCASE", False),
    ("COLLATE/* token */RTRIM", False),
    ("COLLATE-- token\nNOCASE", False),
    ('COLLATE/**/"NOCASE"', False),
    ("COLLATE/**/[RTRIM]", False),
    ("COLLATE/**/`NOCASE`", False),
    ("COLLATE/**/'NOCASE'", False),
    ("COLLATE/**/BINARY", True),
    ('COLLATE/**/"binary"', True),
    ("COLLATE/**/[BINARY]", True),
    ("COLLATE/**/`BINARY`", True),
    ("COLLATE/**/'BINARY'", True),
    ('/* COLLATE NOCASE */ COLLATE/**/"BINARY"', True),
    ('CONSTRAINT "COLLATE NOCASE" CHECK(length(snapshot_id)>0) COLLATE BINARY', True),
])
def test_nonkey_history_collation_preserves_binary_identity_or_refuses_open(
    tmp_path: Path, clause: str, valid: bool,
) -> None:
    original = tmp_path / "original.sqlite3"
    target = tmp_path / "candidate.sqlite3"
    with HistoryStore(original, SCOPE):
        pass
    with closing(sqlite3.connect(original)) as source, closing(sqlite3.connect(target)) as db:
        for (sql,) in source.execute("SELECT sql FROM sqlite_master WHERE type='table'"):
            db.execute(sql.replace("snapshot_id TEXT NOT NULL", "snapshot_id TEXT NOT NULL " + clause))
        for (sql,) in source.execute("SELECT sql FROM sqlite_master WHERE type='trigger'"):
            db.execute(sql)
        db.execute("INSERT INTO history_scope VALUES (1,'12345','DEMO')")
        db.execute("PRAGMA user_version=1")
        db.commit()
    before = target.read_bytes()
    with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as db:
        if valid:
            assert validate_history_database(db) == SCOPE
        else:
            with pytest.raises(HistoryError, match="^HISTORY_DATABASE_INVALID$"):
                validate_history_database(db)
    assert target.read_bytes() == before
    if not valid:
        with pytest.raises(HistoryError, match="^HISTORY_DATABASE_INVALID$"):
            HistoryStore(target, SCOPE)
        assert target.read_bytes() == before
    else:
        with HistoryStore(target, SCOPE) as store:
            assert store.ingest(snapshot(snapshot_id="CaseID")).new_conflict_count == 0
            assert store.ingest(snapshot(snapshot_id="caseid")).new_conflict_count == 0
        with HistoryStore(target, SCOPE) as store:
            assert store.ingest(snapshot(snapshot_id="CaseID")).duplicate
