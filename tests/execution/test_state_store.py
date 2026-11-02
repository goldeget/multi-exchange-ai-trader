"""Durable normalized projections survive crashes without authorizing resend."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from multi_exchange_trader.execution.journal import (
    ExecutionScope,
    JournalConflict,
    RevisionConflict,
)
from multi_exchange_trader.execution.state_store import RuntimeStateStore

SCOPE = ExecutionScope("fixture-account", "DEMO")


def test_per_position_records_exceed_aggregate_codec_limit_and_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with RuntimeStateStore(path, SCOPE) as store:
        for index in range(40):
            store.apply_position_event(f"fill-{index}", {"fill": index}, f"p-{index}", 0,
                                       {"fills": "x" * 3000})
    with RuntimeStateStore(path, SCOPE) as restored:
        positions = restored.list_positions()
        assert len(positions) == 40
        assert sum(len(item.payload_json) for item in positions) > 65536
        assert json.loads(restored.get_position("p-7").payload_json)["fills"] == "x" * 3000


def test_duplicate_event_returns_current_projection_and_conflicting_identity_fails(tmp_path: Path) -> None:
    with RuntimeStateStore(tmp_path / "state.sqlite3", SCOPE) as store:
        first = store.apply_position_event("fill", {"quantity": "1"}, "p", 0, {"quantity": "1"})
        current = store.save_position("p", {"quantity": "2"}, first.revision)
        replay = store.apply_position_event("fill", {"quantity": "1"}, "p", 0, {"quantity": "1"})
        assert replay == current
        with pytest.raises(JournalConflict):
            store.apply_position_event("fill", {"quantity": "9"}, "p", 0, {})
        with pytest.raises(JournalConflict):
            store.apply_position_event("fill", {"quantity": "1"}, "another", 0, {})


def test_two_connections_use_compare_and_swap_without_lost_update(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with RuntimeStateStore(path, SCOPE) as first, RuntimeStateStore(path, SCOPE) as second:
        first.save_position("p", {"quantity": "1"}, 0)
        with pytest.raises(RevisionConflict):
            second.save_position("p", {"quantity": "9"}, 0)
        assert json.loads(second.get_position("p").payload_json) == {"quantity": "1"}


def test_failed_inbox_write_rolls_back_projection_atomically(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with RuntimeStateStore(path, SCOPE) as store:
        with sqlite3.connect(path) as db:
            db.execute("CREATE TRIGGER fail_inbox BEFORE INSERT ON runtime_inbox "
                       "BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
        with pytest.raises(sqlite3.IntegrityError):
            store.apply_position_event("fill", {}, "p", 0, {"quantity": "1"})
        assert store.get_position("p") is None
        with sqlite3.connect(path) as db:
            db.execute("DROP TRIGGER fail_inbox")
        assert store.apply_position_event("fill", {}, "p", 0, {}).revision == 1


def test_open_does_not_recover_other_worker_but_explicit_fenced_recovery_marks_uncertain(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.sqlite3"
    with RuntimeStateStore(path, SCOPE) as store:
        position = store.save_position("p", {"quantity": "1"}, 0)
        order = store.save_order("o", {"client_id": "owned-fixture"}, 0)
        store.mark_reconciled("position", "p", position.revision, evidence="independent GET")
        store.mark_reconciled("order", "o", order.revision, evidence="independent GET")
        with RuntimeStateStore(path, SCOPE) as another:
            assert not another.get_position("p").reconciliation_required
            with pytest.raises(ValueError):
                another.recover(exclusive_owner=False)
            another.recover(exclusive_owner=True)
        assert store.get_position("p").reconciliation_required
        assert store.get_order("o").reconciliation_required
        # Recovery revokes old readers' ability to claim reconciliation.
        with pytest.raises(RevisionConflict):
            store.mark_reconciled("position", "p", position.revision, evidence="stale GET")


def test_account_and_environment_rows_are_isolated(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with RuntimeStateStore(path, SCOPE) as first:
        first.save_position("p", {"quantity": "1"}, 0)
    with RuntimeStateStore(path, ExecutionScope("fixture-account", "LIVE")) as other:
        assert other.list_positions() == ()
        other.apply_position_event("fill", {}, "p", 0, {"quantity": "9"})
    with RuntimeStateStore(path, SCOPE) as first:
        assert json.loads(first.get_position("p").payload_json) == {"quantity": "1"}


def test_process_exit_keeps_committed_event_and_discards_uncommitted_projection(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    program = """import os,sqlite3,sys
from pathlib import Path
from multi_exchange_trader.execution.journal import ExecutionScope
from multi_exchange_trader.execution.state_store import RuntimeStateStore
store=RuntimeStateStore(Path(sys.argv[1]),ExecutionScope('fixture-account','DEMO'))
store.apply_position_event('fill',{},'p',0,{'quantity':'1'})
db=sqlite3.connect(sys.argv[1],isolation_level=None)
db.execute('BEGIN IMMEDIATE')
db.execute("UPDATE runtime_positions SET payload='{}',revision=9")
os._exit(0)
"""
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    subprocess.run([sys.executable, "-c", program, str(path)], env=environment, check=True)
    with RuntimeStateStore(path, SCOPE) as store:
        record = store.get_position("p")
        assert record.revision == 1
        assert json.loads(record.payload_json) == {"quantity": "1"}
        assert store.apply_position_event("fill", {}, "p", 0, {}).revision == 1


@pytest.mark.parametrize("damage", ["version", "missing_table", "invalid_payload", "invalid_flag"])
def test_invalid_restored_state_refused_without_repair(tmp_path: Path, damage: str) -> None:
    path = tmp_path / "state.sqlite3"
    with RuntimeStateStore(path, SCOPE) as store:
        store.save_position("p", {}, 0)
    with sqlite3.connect(path) as db:
        if damage == "version":
            db.execute("PRAGMA user_version=999")
        elif damage == "missing_table":
            db.execute("DROP TABLE runtime_orders")
        elif damage == "invalid_payload":
            db.execute("UPDATE runtime_positions SET payload='not JSON'")
        else:
            db.execute("PRAGMA ignore_check_constraints=ON")
            db.execute("UPDATE runtime_positions SET reconciliation_required=2")
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        RuntimeStateStore(path, SCOPE)


@pytest.mark.parametrize("payload", [{"api_key": "forbidden"}, {"large": "x" * 65536}])
def test_bounded_sanitized_row_payloads_fail_before_writing(tmp_path: Path, payload: object) -> None:
    with RuntimeStateStore(tmp_path / "state.sqlite3", SCOPE) as store:
        with pytest.raises(ValueError):
            store.save_position("p", payload, 0)
        assert store.list_positions() == ()


def test_position_event_history_is_scoped_ordered_and_deduplicated(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with RuntimeStateStore(path, SCOPE) as store:
        store.apply_position_event("z-first", {"quantity": "1"}, "p", 0, {"quantity": "1"})
        store.apply_position_event("z-first", {"quantity": "1"}, "p", 0, {})
        store.save_position("p", {"quantity": "1", "observed": True}, 1)
        store.apply_position_event("a-second", {"quantity": "2"}, "p", 2, {"quantity": "3"})
        store.apply_position_event("unrelated", {}, "other-position", 0, {})
    with RuntimeStateStore(path, ExecutionScope("other-account", "DEMO")) as other:
        other.apply_position_event("z-first", {"quantity": "99"}, "p", 0, {})
    with RuntimeStateStore(path, SCOPE) as restored:
        events = restored.list_position_events("p")
        assert [(event.event_id, event.position_id, event.revision, json.loads(event.payload_json))
                for event in events] == [("z-first", "p", 1, {"quantity": "1"}),
                                        ("a-second", "p", 3, {"quantity": "2"})]
        assert restored.list_position_events("missing") == ()


def test_position_event_history_rejects_corruption_after_open(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with RuntimeStateStore(path, SCOPE) as store:
        store.apply_position_event("event", {}, "p", 0, {})
        with sqlite3.connect(path) as db:
            db.execute("UPDATE runtime_inbox SET event_payload='not-json'")
        with pytest.raises(ValueError):
            store.list_position_events("p")
        with pytest.raises(ValueError):
            store.list_position_events("")


@pytest.mark.parametrize("damage", ["missing_keys", "wrong_scope_key", "partial_key",
                                  "wrong_collation", "missing_fk", "wrong_fk", "cascading_fk",
                                  "duplicate_positions"])
def test_same_columns_and_version_require_scoped_keys_and_foreign_key(
    tmp_path: Path, damage: str,
) -> None:
    from multi_exchange_trader.execution.state_store import _SCHEMA

    path = tmp_path / "state.sqlite3"
    schema = _SCHEMA
    position_key = "PRIMARY KEY(account,environment,position_id)"
    foreign_key = ("FOREIGN KEY(account,environment,position_id)\n "
                   "REFERENCES runtime_positions(account,environment,position_id)")
    if damage == "missing_keys":
        for identity in ("order_id", "position_id", "event_id"):
            schema = schema.replace(f"PRIMARY KEY(account,environment,{identity})", "CHECK(1)")
        schema = schema.replace(foreign_key, "CHECK(1)")
    elif damage == "duplicate_positions":
        schema = schema.replace(position_key, "CHECK(1)")
    elif damage == "wrong_scope_key":
        schema = schema.replace(position_key, "PRIMARY KEY(position_id)")
    elif damage == "partial_key":
        schema = schema.replace(position_key, "CHECK(1)")
        schema += ("CREATE UNIQUE INDEX partial_position ON runtime_positions "
                   "(account,environment,position_id) WHERE revision > 1;")
    elif damage == "wrong_collation":
        schema = schema.replace(position_key,
                                "PRIMARY KEY(account,environment,position_id COLLATE NOCASE)")
    elif damage == "missing_fk":
        schema = schema.replace(foreign_key, "CHECK(1)")
    elif damage == "wrong_fk":
        schema = schema.replace(foreign_key,
                                "FOREIGN KEY(account,environment,position_id) REFERENCES "
                                "runtime_orders(account,environment,order_id)")
    else:
        schema = schema.replace(foreign_key, foreign_key + " ON DELETE CASCADE")
    with sqlite3.connect(path) as db:
        db.executescript(schema)
        if damage == "duplicate_positions":
            db.executemany("INSERT INTO runtime_positions VALUES(?,?,?,?,?,?)",
                           [("fixture-account", "DEMO", "p", 1, "{}", 1)] * 2)
        before = db.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    with pytest.raises(ValueError, match="runtime state.*(key|constraint)"):
        RuntimeStateStore(path, SCOPE)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == before


def test_equivalent_unique_indexes_support_scoped_cas_and_inbox(tmp_path: Path) -> None:
    from multi_exchange_trader.execution.state_store import _SCHEMA

    path = tmp_path / "state.sqlite3"
    schema = _SCHEMA
    for table, identity in (("runtime_orders", "order_id"), ("runtime_positions", "position_id"),
                            ("runtime_inbox", "event_id")):
        schema = schema.replace(f"PRIMARY KEY(account,environment,{identity})", "CHECK(1)")
        schema += f"CREATE UNIQUE INDEX {table}_identity ON {table}({identity},environment,account);"
    schema = schema.replace("FOREIGN KEY(account,environment,position_id)",
                            "FOREIGN KEY(position_id,account,environment)").replace(
                                "REFERENCES runtime_positions(account,environment,position_id)",
                                "REFERENCES runtime_positions(position_id,account,environment)")
    with sqlite3.connect(path) as db:
        db.executescript(schema)
    with RuntimeStateStore(path, SCOPE) as store:
        assert store.apply_position_event("fill", {}, "p", 0, {}).revision == 1
        assert store.save_position("p", {}, 1).revision == 2
        assert store.save_order("o", {}, 0).revision == 1
