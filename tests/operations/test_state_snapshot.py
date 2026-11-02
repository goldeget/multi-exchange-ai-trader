"""Exercise the portable recovery command with real files, SQLite and WAL."""

import hashlib
import json
import os
import re
import runpy
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts/operations/state_snapshot.py"
TABLES = {
    "paper.sqlite3": {
        "metadata": "value TEXT", "scenarios": "id TEXT,created_at TEXT,snapshot TEXT",
        "events": "scenario TEXT,seq INTEGER,payload TEXT,received_at TEXT,previous TEXT,hash TEXT",
        "commands": "scenario TEXT,key TEXT,payload TEXT,seq INTEGER,result_hash TEXT",
        "fills": "scenario TEXT,idx INTEGER,payload TEXT",
        "orders": "scenario TEXT,idx INTEGER,payload TEXT",
        "settlements": "scenario TEXT,seq INTEGER,payload TEXT",
    },
    "research.sqlite3": {
        "metadata": "value TEXT", "runs": "id TEXT,snapshot TEXT",
        "journal": "run TEXT,seq INTEGER,operation TEXT,snapshot TEXT,previous TEXT,hash TEXT",
        "commands": "run TEXT,key TEXT,payload TEXT,response TEXT",
        "reports": "id TEXT,payload TEXT", "report_commands": "key TEXT,dataset TEXT,report TEXT",
    },
    "ai.sqlite3": {
        "ai_settings": "id INTEGER,revision INTEGER,monthly_limit TEXT",
        "reservations": "request_id TEXT,period TEXT,maximum TEXT,actual TEXT,status TEXT,updated_at TEXT",
    },
    "order-reviews.sqlite3": {
        "order_reviews": "id TEXT,scenario TEXT,payload TEXT,confirmation_key TEXT,result TEXT",
    },
    "recommendations.sqlite3": {"cards": "id TEXT,status TEXT,payload TEXT,commands TEXT,updated_at TEXT"},
}
TABLES["spot-paper.sqlite3"] = TABLES["paper.sqlite3"]


def command(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)], text=True,
                          capture_output=True, timeout=35, check=False)


@pytest.fixture
def state(tmp_path: Path) -> Path:
    root = tmp_path / "state"
    root.mkdir()
    for name, tables in TABLES.items():
        with closing(sqlite3.connect(root / name)) as db:
            for table, columns in tables.items():
                db.execute(f'CREATE TABLE "{table}" ({columns})')
            if "metadata" in tables:
                db.execute("PRAGMA user_version=1")
                if name == "research.sqlite3":
                    meta = {"schema": 1, "engine": "1.231.0", "automation": "SYNTHETIC_AUTO_V1",
                            "event_limit": 500, "scenario_limit": 16}
                else:
                    meta = {"schema": 1, "engine": "1.231.0", "assumptions": "PAPER-v1"}
                    if name == "spot-paper.sqlite3":
                        meta.update(assumptions="SPOT-PAPER-v2", product="SPOT")
                db.execute("INSERT INTO metadata VALUES (?)", (json.dumps(meta),))
            db.commit()
    (root / "datasets").mkdir()
    (root / "datasets/example.json").write_text('{"fixture":"immutable"}', encoding="utf-8")
    return root


def snapshot(state: Path, target: Path) -> subprocess.CompletedProcess[str]:
    return command("snapshot", "--source", state, "--target", target, "--writers-stopped")


def test_default_command_is_read_only_help(tmp_path: Path) -> None:
    result = command()
    assert result.returncode == 0, result.stderr
    assert "snapshot" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_snapshot_requires_explicit_stopped_writer_attestation(state: Path, tmp_path: Path) -> None:
    target = tmp_path / "backup"
    result = command("snapshot", "--source", state, "--target", target)
    assert result.returncode != 0
    assert "writers" in result.stderr
    assert not target.exists()


def test_complete_generation_round_trip_preserves_all_bytes_and_source(state: Path, tmp_path: Path) -> None:
    before = {p.relative_to(state).as_posix(): p.read_bytes() for p in state.rglob("*") if p.is_file()}
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    result = snapshot(state, backup)
    assert result.returncode == 0, result.stderr
    assert command("verify", "--source", backup).returncode == 0
    result = command("restore", "--source", backup, "--target", restored)
    assert result.returncode == 0, result.stderr
    assert {p.relative_to(restored).as_posix(): p.read_bytes() for p in restored.rglob("*") if p.is_file()} == before
    assert {p.relative_to(state).as_posix(): p.read_bytes() for p in state.rglob("*") if p.is_file()} == before
    manifest = json.loads((backup / "manifest.json").read_text())
    assert len([f for f in manifest["files"] if f["path"].endswith(".sqlite3")]) == 6


def test_committed_wal_rows_survive_without_modifying_source(state: Path, tmp_path: Path) -> None:
    script = """import os,sqlite3,sys
db=sqlite3.connect(sys.argv[1])
db.execute('PRAGMA journal_mode=WAL')
db.execute('INSERT INTO ai_settings VALUES (1,7,\"0.00000000\")')
db.commit()
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", script, str(state / "ai.sqlite3")], check=True)
    wal = (state / "ai.sqlite3-wal").read_bytes()
    assert wal
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    assert snapshot(state, backup).returncode == 0
    assert command("restore", "--source", backup, "--target", restored).returncode == 0
    assert (state / "ai.sqlite3-wal").read_bytes() == wal
    assert (restored / "ai.sqlite3-wal").read_bytes() == wal
    with sqlite3.connect(restored / "ai.sqlite3") as db:
        assert db.execute("SELECT revision FROM ai_settings").fetchone() == (7,)


@pytest.mark.parametrize("damage", ["magic", "header_checksum", "frame_data", "truncated"])
def test_corrupt_wal_is_refused_even_if_sqlite_could_ignore_it(
    state: Path, tmp_path: Path, damage: str,
) -> None:
    program = """import os,sqlite3,sys
db=sqlite3.connect(sys.argv[1])
db.execute('PRAGMA journal_mode=WAL')
db.execute('INSERT INTO ai_settings VALUES (1,7,\"0.00000000\")')
db.commit()
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", program, str(state / "ai.sqlite3")], check=True)
    path = state / "ai.sqlite3-wal"
    raw = path.read_bytes()
    broken = bytearray(raw)
    if damage == "truncated":
        broken = broken[:-1]
    else:
        index = {"magic": 0, "header_checksum": 24, "frame_data": 75}[damage]
        broken[index] ^= 0xFF
    path.write_bytes(broken)
    target = tmp_path / "backup"
    result = snapshot(state, target)
    assert result.returncode != 0
    assert not target.exists()


@pytest.mark.parametrize("limit", ["MAX_FILE_BYTES", "MAX_TOTAL_BYTES", "MAX_FILES"])
def test_limit_exceeded_refuses_before_publishing(
    state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str,
) -> None:
    module = runpy.run_path(str(TOOL))
    invoke = module["main"]
    monkeypatch.setitem(invoke.__globals__, limit, 1)
    target = tmp_path / "backup"
    assert invoke(["snapshot", "--source", str(state), "--target", str(target),
                   "--writers-stopped"]) == 1
    assert not target.exists()


@pytest.mark.parametrize("change", ["missing", "corrupt", "wrong_schema", "hidden_schema",
                                  "extra", "journal"])
def test_invalid_source_never_publishes_snapshot(state: Path, tmp_path: Path, change: str) -> None:
    if change == "missing":
        (state / "ai.sqlite3").unlink()
    elif change == "corrupt":
        (state / "ai.sqlite3").write_bytes(b"not SQLite")
    elif change in {"wrong_schema", "hidden_schema"}:
        with closing(sqlite3.connect(state / "ai.sqlite3")) as db:
            if change == "hidden_schema":
                db.execute("ALTER TABLE ai_settings ADD COLUMN surprise TEXT "
                           "GENERATED ALWAYS AS ('hidden') VIRTUAL")
            else:
                db.execute("ALTER TABLE ai_settings ADD COLUMN surprise TEXT")
            db.commit()
    elif change == "extra":
        (state / "credentials.env").write_text("do-not-copy", encoding="utf-8")
    else:
        (state / "paper.sqlite3-journal").write_bytes(b"incomplete transaction")
    target = tmp_path / "backup"
    assert snapshot(state, target).returncode != 0
    assert not target.exists()


@pytest.mark.parametrize("attack", ["corrupt", "traversal", "missing", "unlisted", "version",
                                  "duplicate", "absolute", "backslash"])
def test_invalid_backup_refused_before_restore_target_created(state: Path, tmp_path: Path, attack: str) -> None:
    backup, target = tmp_path / "backup", tmp_path / "restored"
    assert snapshot(state, backup).returncode == 0
    path = backup / "manifest.json"
    manifest = json.loads(path.read_text())
    if attack == "corrupt":
        (backup / "data/datasets/example.json").write_bytes(b"changed")
    elif attack == "traversal":
        manifest["files"][0]["path"] = "../outside"
    elif attack == "absolute":
        manifest["files"][0]["path"] = str(tmp_path / "outside")
    elif attack == "backslash":
        manifest["files"][0]["path"] = "..\\outside"
    elif attack == "duplicate":
        manifest["files"].append(manifest["files"][0])
    elif attack == "missing":
        manifest["files"].pop()
    elif attack == "unlisted":
        (backup / "data/datasets/unlisted.json").write_bytes(b"extra")
    else:
        manifest["format_version"] = 999
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert command("verify", "--source", backup).returncode != 0
    assert command("restore", "--source", backup, "--target", target).returncode != 0
    assert not target.exists()


def test_real_application_stores_and_seeded_datasets_can_be_recovered(tmp_path: Path) -> None:
    from multi_exchange_trader.ai.budget import BudgetBook
    from multi_exchange_trader.paper.order_preview import OrderReviews
    from multi_exchange_trader.paper.spot_store import SpotPaperStore
    from multi_exchange_trader.paper.store import PaperStore
    from multi_exchange_trader.recommendations.book import RecommendationBook
    from multi_exchange_trader.research.storage import ResearchStore

    state = tmp_path / "actual-app-state"
    paper = PaperStore(state / "paper.sqlite3")
    spot = SpotPaperStore(state / "spot-paper.sqlite3")
    OrderReviews(state / "order-reviews.sqlite3", paper, spot=spot)
    BudgetBook(state / "ai.sqlite3")
    RecommendationBook(state / "recommendations.sqlite3")
    ResearchStore(state / "research.sqlite3", state / "datasets")
    result = snapshot(state, tmp_path / "backup")
    assert result.returncode == 0, result.stderr
    assert command("restore", "--source", tmp_path / "backup",
                   "--target", tmp_path / "restored").returncode == 0


def test_hardlinks_refused_before_target_created(state: Path, tmp_path: Path) -> None:
    linked = tmp_path / "linked-db"
    try:
        os.link(state / "ai.sqlite3", linked)
    except OSError:
        pytest.skip("hardlink creation unavailable; required in Linux CI")
    assert snapshot(state, tmp_path / "backup").returncode != 0
    assert not (tmp_path / "backup").exists()


def test_corrupt_sqlite_rejected_even_with_updated_hash(state: Path, tmp_path: Path) -> None:
    backup = tmp_path / "backup"
    assert snapshot(state, backup).returncode == 0
    data = b"a malicious but correctly hashed non database"
    (backup / "data/ai.sqlite3").write_bytes(data)
    path = backup / "manifest.json"
    manifest = json.loads(path.read_text())
    for item in manifest["files"]:
        if item["path"] == "ai.sqlite3":
            item.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert command("verify", "--source", backup).returncode != 0


def test_existing_targets_and_nested_source_refused(state: Path, tmp_path: Path) -> None:
    backup = tmp_path / "backup"
    assert snapshot(state, backup).returncode == 0
    original = (backup / "manifest.json").read_bytes()
    assert snapshot(state, backup).returncode != 0
    assert (backup / "manifest.json").read_bytes() == original
    assert snapshot(state, state / "nested").returncode != 0
    assert not (state / "nested").exists()
    assert command("restore", "--source", backup, "--target", state).returncode != 0
    assert command("restore", "--source", backup, "--target", backup / "nested").returncode != 0


def test_symlink_inputs_and_target_parent_refused(state: Path, tmp_path: Path) -> None:
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(state, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable; required in Linux CI")
    assert snapshot(alias, tmp_path / "backup").returncode != 0
    assert snapshot(state, alias / "nested").returncode != 0
    (state / "datasets/link.json").symlink_to(state / "datasets/example.json")
    assert snapshot(state, tmp_path / "backup").returncode != 0


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions require Linux CI")
def test_snapshot_and_restore_are_private(state: Path, tmp_path: Path) -> None:
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    assert snapshot(state, backup).returncode == 0
    assert command("restore", "--source", backup, "--target", restored).returncode == 0
    for root in (backup, restored):
        for path in [root, *root.rglob("*")]:
            assert path.stat().st_mode & 0o077 == 0


@pytest.fixture
def runtime_state(state: Path) -> Path:
    from multi_exchange_trader.execution.journal import CommandJournal, ExecutionScope
    from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook
    from multi_exchange_trader.execution.state_store import RuntimeStateStore

    scope = ExecutionScope("backup-fixture", "DEMO")
    with CommandJournal(state / "execution.sqlite3", scope):
        pass
    with RuntimeStateStore(state / "runtime-state.sqlite3", scope) as store:
        store.apply_position_event("fill", {"quantity": "1"}, "owned", 0, {"quantity": "1"})
    RuntimePolicyBook(state / "runtime-policy.sqlite3")
    with closing(sqlite3.connect(state / "ai-dispatch.sqlite3")) as db:
        db.execute("PRAGMA user_version=1")
        db.execute("CREATE TABLE ai_dispatches(request_id TEXT PRIMARY KEY,binding_hash TEXT,"
                   "maximum TEXT,status TEXT,failure TEXT,exact_cost TEXT,created_at TEXT,"
                   "updated_at TEXT)")
        db.commit()
    return state


def test_runtime_generation_round_trip_requires_and_preserves_four_runtime_stores(
    runtime_state: Path, tmp_path: Path,
) -> None:
    from multi_exchange_trader.execution.journal import ExecutionScope
    from multi_exchange_trader.execution.state_store import RuntimeStateStore

    backup, restored = tmp_path / "backup", tmp_path / "restored"
    result = command("snapshot", "--source", runtime_state, "--target", backup,
                     "--writers-stopped", "--require-runtime")
    assert result.returncode == 0, result.stderr
    manifest = json.loads((backup / "manifest.json").read_text())
    assert manifest["format_version"] == 2
    assert manifest["runtime_files"] == ["ai-dispatch.sqlite3", "execution.sqlite3",
                                         "runtime-policy.sqlite3", "runtime-state.sqlite3"]
    result = command("restore", "--source", backup, "--target", restored, "--require-runtime")
    assert result.returncode == 0, result.stderr
    with RuntimeStateStore(restored / "runtime-state.sqlite3", ExecutionScope("backup-fixture", "DEMO")) as store:
        assert json.loads(store.get_position("owned").payload_json) == {"quantity": "1"}
        assert store.get_position("owned").reconciliation_required


def test_disabled_policy_only_installation_remains_backuppable(runtime_state: Path, tmp_path: Path) -> None:
    for name in ("execution.sqlite3", "runtime-state.sqlite3", "ai-dispatch.sqlite3"):
        (runtime_state / name).unlink()
    backup = tmp_path / "backup"
    result = snapshot(runtime_state, backup)
    assert result.returncode == 0, result.stderr
    assert command("verify", "--source", backup).returncode == 0
    assert command("verify", "--source", backup, "--require-runtime").returncode != 0


def test_leverage_command_snapshot_restore_preserves_intent_state_and_audit(
    runtime_state: Path, tmp_path: Path,
) -> None:
    from multi_exchange_trader.execution.journal import (
        CommandJournal,
        CommandKind,
        CommandState,
        ExecutionScope,
    )

    scope = ExecutionScope("backup-fixture", "DEMO")
    payload = {"instrument_id": "BTCUSDT-LINEAR.BYBIT", "leverage": "1.8"}
    with CommandJournal(runtime_state / "execution.sqlite3", scope) as journal:
        journal.register("leverage", CommandKind.LEVERAGE, payload)
        expected = journal.claim("leverage")
        audit = journal.audit_events()
    assert expected is not None and expected.state == CommandState.SENDING

    backup, restored = tmp_path / "backup", tmp_path / "restored"
    result = command("snapshot", "--source", runtime_state, "--target", backup,
                     "--writers-stopped", "--require-runtime")
    assert result.returncode == 0, result.stderr
    result = command("verify", "--source", backup, "--require-runtime")
    assert result.returncode == 0, result.stderr
    result = command("restore", "--source", backup, "--target", restored, "--require-runtime")
    assert result.returncode == 0, result.stderr
    with CommandJournal(restored / "execution.sqlite3", scope) as journal:
        actual = journal.get("leverage")
        assert actual == expected
        assert json.loads(actual.payload_json) == payload
        assert journal.audit_events() == audit
        assert journal.claim("leverage") is None


@pytest.mark.parametrize("kind", ["leverage", "LEVERAGE_UNKNOWN", ""])
def test_unknown_command_kind_refused_before_snapshot_and_restore_publication(
    runtime_state: Path, tmp_path: Path, kind: str,
) -> None:
    from multi_exchange_trader.execution.journal import CommandJournal, CommandKind, ExecutionScope

    with CommandJournal(runtime_state / "execution.sqlite3", ExecutionScope("backup-fixture", "DEMO")) as journal:
        journal.register("leverage", CommandKind.LEVERAGE, {"leverage": "1.8"})
    backup = tmp_path / "backup"
    result = snapshot(runtime_state, backup)
    assert result.returncode == 0, result.stderr

    for path in (runtime_state / "execution.sqlite3", backup / "data/execution.sqlite3"):
        with closing(sqlite3.connect(path)) as db:
            db.execute("UPDATE commands SET kind=? WHERE command_key='leverage'", (kind,))
            db.commit()
    invalid = tmp_path / "invalid-backup"
    result = snapshot(runtime_state, invalid)
    assert result.returncode != 0
    assert "Runtime command state invalid" in result.stderr
    assert not invalid.exists()

    # Rehash the synthetic backup: restore must validate the kind beyond file integrity.
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    content = (backup / "data/execution.sqlite3").read_bytes()
    for entry in manifest["files"]:
        if entry["path"] == "execution.sqlite3":
            entry.update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    restored = tmp_path / "restored"
    result = command("restore", "--source", backup, "--target", restored, "--require-runtime")
    assert result.returncode != 0
    assert "Runtime command state invalid" in result.stderr
    assert not restored.exists()


@pytest.mark.parametrize("missing", ["execution.sqlite3", "runtime-state.sqlite3"])
def test_runtime_command_and_projection_pair_cannot_be_backed_up_separately(
    runtime_state: Path, tmp_path: Path, missing: str,
) -> None:
    (runtime_state / missing).unlink()
    assert snapshot(runtime_state, tmp_path / "backup").returncode != 0
    assert not (tmp_path / "backup").exists()


def test_legacy_generation_cannot_satisfy_explicit_runtime_requirement(state: Path, tmp_path: Path) -> None:
    assert snapshot(state, tmp_path / "legacy").returncode == 0
    result = command("restore", "--source", tmp_path / "legacy", "--target", tmp_path / "restore",
                     "--require-runtime")
    assert result.returncode != 0
    assert not (tmp_path / "restore").exists()


@pytest.mark.parametrize("damage", ["version", "payload", "missing_schema", "view", "trigger"])
def test_invalid_runtime_database_refused_even_when_other_ledgers_are_valid(
    runtime_state: Path, tmp_path: Path, damage: str,
) -> None:
    with closing(sqlite3.connect(runtime_state / "runtime-state.sqlite3")) as db:
        if damage == "version":
            db.execute("PRAGMA user_version=2")
        elif damage == "payload":
            db.execute("UPDATE runtime_positions SET payload='not-json'")
        elif damage == "missing_schema":
            db.execute("DROP TABLE runtime_inbox")
        elif damage == "view":
            db.execute("CREATE VIEW unrelated AS SELECT * FROM runtime_orders")
        else:
            db.execute("CREATE TRIGGER unrelated AFTER INSERT ON runtime_orders BEGIN SELECT 1; END")
        db.commit()
    result = snapshot(runtime_state, tmp_path / "backup")
    assert result.returncode != 0
    assert not (tmp_path / "backup").exists()


@pytest.mark.parametrize("name,table,damage", [
    ("runtime-state.sqlite3", "runtime_orders", "missing_key"),
    ("runtime-state.sqlite3", "runtime_orders", "wrong_key"),
    ("runtime-state.sqlite3", "runtime_orders", "partial_key"),
    ("runtime-state.sqlite3", "runtime_orders", "expression_key"),
    ("runtime-state.sqlite3", "runtime_orders", "nocase_key"),
    ("runtime-state.sqlite3", "runtime_orders", "duplicate_key_part"),
    ("runtime-state.sqlite3", "runtime_positions", "duplicates"),
    ("runtime-state.sqlite3", "runtime_inbox", "missing_key"),
    ("runtime-state.sqlite3", "runtime_inbox", "missing_fk"),
    ("runtime-state.sqlite3", "runtime_inbox", "wrong_fk"),
    ("execution.sqlite3", "commands", "missing_key"),
    ("execution.sqlite3", "commands", "missing_client_key"),
    ("execution.sqlite3", "audit", "missing_key"),
    ("execution.sqlite3", "projections", "missing_key"),
    ("execution.sqlite3", "inbox", "missing_key"),
    ("runtime-policy.sqlite3", "runtime_policy", "missing_key"),
    ("ai-dispatch.sqlite3", "ai_dispatches", "missing_key"),
])
def test_malformed_runtime_constraints_refused_before_snapshot_and_restore_publication(
    runtime_state: Path, tmp_path: Path, name: str, table: str, damage: str,
) -> None:
    backup = tmp_path / "backup"
    assert snapshot(runtime_state, backup).returncode == 0

    def damage_database(path: Path) -> None:
        with closing(sqlite3.connect(path)) as db:
            original = db.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
            if damage == "missing_fk":
                changed = original[:original.index(",\n FOREIGN KEY")] + ")"
            elif damage == "wrong_fk":
                changed = original.replace("REFERENCES runtime_positions(account,environment,position_id)",
                                           "REFERENCES runtime_orders(account,environment,order_id)")
            elif damage == "missing_client_key":
                changed = original.replace("client_id TEXT NOT NULL UNIQUE", "client_id TEXT NOT NULL")
            else:
                changed = re.sub(r",\s*PRIMARY KEY\([^)]*\)", "", original)
                changed = changed.replace(" PRIMARY KEY AUTOINCREMENT", "").replace(" PRIMARY KEY", "")
            assert changed != original
            # Rebuild only this synthetic table, retaining exact columns, data and version.
            rows = db.execute(f'SELECT * FROM "{table}"').fetchall()
            db.execute(f'DROP TABLE "{table}"')
            db.execute(changed)
            if damage in {"wrong_key", "partial_key", "expression_key", "nocase_key",
                          "duplicate_key_part"}:
                parts = {"wrong_key": "order_id", "partial_key": "account,environment,order_id",
                         "expression_key": "account,environment,lower(order_id)",
                         "nocase_key": "account,environment,order_id COLLATE NOCASE",
                         "duplicate_key_part": "account,environment,order_id,order_id"}[damage]
                suffix = " WHERE revision > 1" if damage == "partial_key" else ""
                db.execute(f"CREATE UNIQUE INDEX bad_identity ON runtime_orders({parts}){suffix}")
            if rows:
                marks = ",".join("?" for _ in rows[0])
                db.executemany(f'INSERT INTO "{table}" VALUES({marks})',
                               rows * (2 if damage == "duplicates" else 1))
            if table == "audit":
                for operation in ("UPDATE", "DELETE"):
                    db.execute(f"CREATE TRIGGER audit_no_{operation.lower()} BEFORE {operation} "
                               "ON audit BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END")
            db.commit()

    damage_database(runtime_state / name)
    assert snapshot(runtime_state, tmp_path / "invalid-backup").returncode != 0
    assert not (tmp_path / "invalid-backup").exists()

    damage_database(backup / "data" / name)
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest["files"]:
        if entry["path"] == name:
            content = (backup / "data" / name).read_bytes()
            entry.update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    restored = tmp_path / "restored"
    assert command("restore", "--source", backup, "--target", restored).returncode != 0
    assert not restored.exists()


def test_equivalent_runtime_unique_indexes_and_reordered_foreign_key_are_supported() -> None:
    from multi_exchange_trader.execution.state_store import _SCHEMA

    schema = _SCHEMA
    for table, identity in (("runtime_orders", "order_id"), ("runtime_positions", "position_id"),
                            ("runtime_inbox", "event_id")):
        schema = schema.replace(f"PRIMARY KEY(account,environment,{identity})", "CHECK(1)")
        schema += f"CREATE UNIQUE INDEX {table}_identity ON {table}({identity},environment,account);"
    schema = schema.replace("FOREIGN KEY(account,environment,position_id)",
                            "FOREIGN KEY(position_id,account,environment)").replace(
                                "REFERENCES runtime_positions(account,environment,position_id)",
                                "REFERENCES runtime_positions(position_id,account,environment)")
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript(schema)
        runpy.run_path(str(TOOL))["check_runtime"](db, "runtime-state.sqlite3")


def test_manifest_runtime_file_list_prevents_silent_restore_downgrade(runtime_state: Path, tmp_path: Path) -> None:
    backup = tmp_path / "backup"
    assert snapshot(runtime_state, backup).returncode == 0
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    (backup / "data/ai-dispatch.sqlite3").unlink()
    manifest["files"] = [item for item in manifest["files"] if item["path"] != "ai-dispatch.sqlite3"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert command("restore", "--source", backup, "--target", tmp_path / "restored").returncode != 0
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize("dispatch_status,budget_status,cost", [
    ("CLAIMED", None, None), ("CLAIMED", "reserved", None), ("CLAIMED", "uncertain", None),
    ("DISPATCHING", "uncertain", None), ("UNCERTAIN", "uncertain", None),
    ("CHARGED", "uncertain", "0.01"), ("CHARGED", "settled", "0.01"),
    ("SETTLED", "settled", "0.01"), ("CHARGED", "settled", "0"), ("SETTLED", "settled", "0"),
])
def test_all_dispatch_crash_windows_are_backed_up_without_releasing_liability(
    runtime_state: Path, tmp_path: Path, dispatch_status: str,
    budget_status: str | None, cost: str | None,
) -> None:
    request_id = "fixture-request"
    reservation_id = "dispatch_" + hashlib.sha256(request_id.encode()).hexdigest()
    with closing(sqlite3.connect(runtime_state / "ai-dispatch.sqlite3")) as db:
        db.execute("INSERT INTO ai_dispatches VALUES(?,?,?,?,?,?,?,?)",
                   (request_id, "a" * 64, "0.10", dispatch_status, None, cost,
                    "2026-11-02T00:00:00+00:00", "2026-11-02T00:00:00+00:00"))
        db.commit()
    if budget_status is not None:
        with closing(sqlite3.connect(runtime_state / "ai.sqlite3")) as db:
            db.execute("INSERT INTO reservations VALUES(?,?,?,?,?,?)",
                       (reservation_id, "2026-11", "0.10", cost if budget_status == "settled" else None,
                        budget_status, "2026-11-02T00:00:00+00:00"))
            db.commit()
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    assert snapshot(runtime_state, backup).returncode == 0
    result = command("restore", "--source", backup, "--target", restored)
    assert result.returncode == 0, result.stderr
    with closing(sqlite3.connect(restored / "ai-dispatch.sqlite3")) as db:
        assert db.execute("SELECT status,exact_cost FROM ai_dispatches").fetchone() == (dispatch_status, cost)
    with closing(sqlite3.connect(restored / "ai.sqlite3")) as db:
        reservation = db.execute("SELECT status FROM reservations").fetchone()
        assert reservation == (None if budget_status is None else (budget_status,))


@pytest.mark.parametrize("dispatch_status", [
    "CLAIMED", "DISPATCHING", "UNCERTAIN", "CHARGED", "SETTLED", "BLOCKED",
])
@pytest.mark.parametrize("budget_status", [None, "reserved", "uncertain", "settled"])
@pytest.mark.parametrize("exact_cost", [None, "0.01"])
def test_dispatch_budget_state_pairs_follow_persisted_charge_protocol(
    tmp_path: Path, dispatch_status: str, budget_status: str | None, exact_cost: str | None,
) -> None:
    module = runpy.run_path(str(TOOL))
    budget_path = tmp_path / "budget.sqlite3"
    with closing(sqlite3.connect(budget_path)) as budget:
        budget.execute(f"CREATE TABLE reservations ({TABLES['ai.sqlite3']['reservations']})")
        if budget_status is not None:
            budget.execute("INSERT INTO reservations VALUES(?,?,?,?,?,?)", (
                "dispatch_" + hashlib.sha256(b"fixture-request").hexdigest(), "2026-11",
                "0.10", "0.01" if budget_status == "settled" else None, budget_status, "fixture",
            ))
        budget.commit()
    valid = ((exact_cost is None and (
        dispatch_status == "BLOCKED"
        or dispatch_status == "CLAIMED" and budget_status in {None, "reserved", "uncertain"}
        or dispatch_status in {"DISPATCHING", "UNCERTAIN"} and budget_status == "uncertain"
    )) or (exact_cost is not None and (
        dispatch_status == "CHARGED" and budget_status in {"uncertain", "settled"}
        or dispatch_status == "SETTLED" and budget_status == "settled"
    )))
    with closing(sqlite3.connect(":memory:")) as dispatch:
        dispatch.row_factory = sqlite3.Row
        dispatch.execute("CREATE TABLE ai_dispatches(request_id,maximum,status,exact_cost)")
        dispatch.execute("INSERT INTO ai_dispatches VALUES(?,?,?,?)",
                         ("fixture-request", "0.10", dispatch_status, exact_cost))
        if valid:
            module["check_dispatch_budget"](dispatch, budget_path)
        else:
            with pytest.raises(module["SnapshotError"]):
                module["check_dispatch_budget"](dispatch, budget_path)


@pytest.mark.parametrize("dispatch_status", ["DISPATCHING", "UNCERTAIN"])
@pytest.mark.parametrize("actual", ["0", "0.01"])
def test_unknown_dispatch_cannot_publish_backup_or_restore_with_settled_budget(
    runtime_state: Path, tmp_path: Path, dispatch_status: str, actual: str,
) -> None:
    reservation_id = "dispatch_" + hashlib.sha256(b"fixture-request").hexdigest()
    with closing(sqlite3.connect(runtime_state / "ai-dispatch.sqlite3")) as db:
        db.execute("INSERT INTO ai_dispatches VALUES(?,?,?,?,?,?,?,?)",
                   ("fixture-request", "a" * 64, "0.10", dispatch_status, None, None,
                    "2026-11-02T00:00:00+00:00", "2026-11-02T00:00:00+00:00"))
        db.commit()
    with closing(sqlite3.connect(runtime_state / "ai.sqlite3")) as db:
        db.execute("INSERT INTO reservations VALUES(?,?,?,?,?,?)",
                   (reservation_id, "2026-11", "0.10", None, "uncertain",
                    "2026-11-02T00:00:00+00:00"))
        db.commit()
    backup = tmp_path / "backup"
    result = snapshot(runtime_state, backup)
    assert result.returncode == 0, result.stderr

    for path in (runtime_state / "ai.sqlite3", backup / "data/ai.sqlite3"):
        with closing(sqlite3.connect(path)) as db:
            db.execute("UPDATE reservations SET status='settled',actual=?", (actual,))
            db.commit()
    invalid_backup = tmp_path / "invalid-backup"
    assert snapshot(runtime_state, invalid_backup).returncode != 0
    assert not invalid_backup.exists()

    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest["files"]:
        if entry["path"] == "ai.sqlite3":
            content = (backup / "data/ai.sqlite3").read_bytes()
            entry.update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    restored = tmp_path / "restored"
    assert command("restore", "--source", backup, "--target", restored).returncode != 0
    assert not restored.exists()


@pytest.mark.parametrize("damage", ["missing_budget", "missing_history", "released", "different_cost"])
def test_mixed_ai_generations_are_refused_before_backup_publish(
    runtime_state: Path, tmp_path: Path, damage: str,
) -> None:
    reservation_id = "dispatch_" + hashlib.sha256(b"fixture-request").hexdigest()
    if damage != "missing_history":
        with closing(sqlite3.connect(runtime_state / "ai-dispatch.sqlite3")) as db:
            db.execute("INSERT INTO ai_dispatches VALUES(?,?,?,?,?,?,?,?)",
                       ("fixture-request", "a" * 64, "0.10", "SETTLED" if damage == "different_cost" else "DISPATCHING",
                        None, "0.01" if damage == "different_cost" else None,
                        "2026-11-02T00:00:00+00:00", "2026-11-02T00:00:00+00:00"))
            db.commit()
    if damage != "missing_budget":
        with closing(sqlite3.connect(runtime_state / "ai.sqlite3")) as db:
            db.execute("INSERT INTO reservations VALUES(?,?,?,?,?,?)",
                       (reservation_id, "2026-11", "0.10", "0.02" if damage == "different_cost" else None,
                        "settled" if damage == "different_cost" else "reserved",
                        "2026-11-02T00:00:00+00:00"))
            db.commit()
    assert snapshot(runtime_state, tmp_path / "backup").returncode != 0
    assert not (tmp_path / "backup").exists()


def test_dispatch_reservation_without_dispatch_file_cannot_be_disguised_as_legacy(
    state: Path, tmp_path: Path,
) -> None:
    with closing(sqlite3.connect(state / "ai.sqlite3")) as db:
        db.execute("INSERT INTO reservations VALUES(?,?,?,?,?,?)",
                   ("dispatch_" + "a" * 64, "2026-11", "0.10", None, "uncertain",
                    "2026-11-02T00:00:00+00:00"))
        db.commit()
    assert snapshot(state, tmp_path / "backup").returncode != 0
    assert not (tmp_path / "backup").exists()


@pytest.mark.parametrize("budget_status", ["reserved", "uncertain", "settled"])
def test_blocked_dispatch_preserves_conflicting_preexisting_budget_reservation(
    runtime_state: Path, tmp_path: Path, budget_status: str,
) -> None:
    reservation_id = "dispatch_" + hashlib.sha256(b"fixture-request").hexdigest()
    with closing(sqlite3.connect(runtime_state / "ai-dispatch.sqlite3")) as db:
        db.execute("INSERT INTO ai_dispatches VALUES(?,?,?,?,?,?,?,?)",
                   ("fixture-request", "a" * 64, "0.10", "BLOCKED", "EXISTING_RESERVATION", None,
                    "2026-11-02T00:00:00+00:00", "2026-11-02T00:00:00+00:00"))
        db.commit()
    with closing(sqlite3.connect(runtime_state / "ai.sqlite3")) as db:
        db.execute("INSERT INTO reservations VALUES(?,?,?,?,?,?)",
                   (reservation_id, "2026-11", "0.20", "0" if budget_status == "settled" else None,
                    budget_status, "2026-11-02T00:00:00+00:00"))
        db.commit()
    result = snapshot(runtime_state, tmp_path / "backup")
    assert result.returncode == 0, result.stderr


def test_restored_interrupted_command_keeps_reservation_until_independent_terminal_evidence(
    runtime_state: Path, tmp_path: Path,
) -> None:
    from decimal import Decimal

    from multi_exchange_trader.execution.journal import (
        CommandJournal,
        CommandKind,
        CommandState,
        ExecutionScope,
        JournalConflict,
        Reservation,
        ReservationLimits,
    )

    scope = ExecutionScope("backup-fixture", "DEMO")
    reservation = Reservation(Decimal(1), Decimal(2), Decimal(3))
    limits = ReservationLimits(Decimal(1), Decimal(2), Decimal(3), 1,
                               Decimal(0), Decimal(0), Decimal(0), 0)
    with CommandJournal(runtime_state / "execution.sqlite3", scope) as journal:
        journal.register("entry", CommandKind.ENTRY, {"owned": "fixture"},
                         reservation=reservation, limits=limits)
        journal.claim("entry")
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    assert snapshot(runtime_state, backup).returncode == 0
    result = command("restore", "--source", backup, "--target", restored, "--require-runtime")
    assert result.returncode == 0, result.stderr
    with CommandJournal(restored / "execution.sqlite3", scope) as journal:
        assert journal.get("entry").state == CommandState.SENDING
        journal.recover_sending(exclusive_owner=True)
        record = journal.get("entry")
        assert record.state == CommandState.UNKNOWN
        assert record.reservation == reservation
        assert not record.terminal_confirmed
        assert journal.claim("entry") is None
        with pytest.raises(JournalConflict):
            journal.release_reservation("entry", confirmed_terminal=True, terminal_evidence="no final readback")
