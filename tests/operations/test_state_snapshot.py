"""Exercise the portable recovery command with real files, SQLite and WAL."""

import hashlib
import json
import os
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
