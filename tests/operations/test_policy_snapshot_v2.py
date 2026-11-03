"""Real current policy books through the portable offline recovery boundary."""

import json
import runpy
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from test_state_snapshot import ROOT, TOOL, command, rehash_manifest, snapshot, state_bytes
from test_state_snapshot import state as state_fixture

from multi_exchange_trader.execution.runtime_policy import (
    RuntimePolicyBook,
    parse_json,
    validate_v2,
)

OFFLINE = runpy.run_path(str(TOOL))
state = state_fixture


def typed_policy():
    fixtures = runpy.run_path(str(ROOT / "tests/execution/test_policy_sections.py"))
    return fixtures["policy_fixture"]()


@pytest.mark.parametrize("fault", [None, "partial", "orphan", "spot", "future", "hash", "global", "decimal", "size"])
def test_typed_pure_application_offline_parity(fault):
    policy = typed_policy()
    accepted = fault in {None, "partial", "orphan", "spot"}
    if fault == "partial":
        policy["risk_v2"]["caps"] = None
        policy["scope"]["account"] = None
    elif fault == "orphan":
        key = {"venue": "BYBIT", "product": "LINEAR", "symbol": "SOLUSDT"}
        policy["scope"]["allowed_instruments"].append(key)
        policy["scope"]["selected_instruments"].append(key)
    elif fault == "spot":
        policy["scope"].update(selected_product="SPOT", allowed_products=["SPOT"],
                               allowed_instruments=None, selected_instruments=None)
        policy["risk_v2"] = policy["strategy"] = None
    elif fault == "future":
        policy["strategy"]["section_version"] = 2
    elif fault == "hash":
        policy["strategy"]["catalog"]["entries"][0]["implementation_sha256"] = "d" * 64
    elif fault == "global":
        policy["strategy"]["allowed"].append({"strategy_id": "unknown", "revision": "r1"})
    elif fault == "decimal":
        policy["risk_v2"]["per_trade_loss"] = "1e2"
    elif fault == "size":
        import copy

        from multi_exchange_trader.execution.policy_sections import catalog_from_wire

        policy["legacy_v1"]["allowed_symbols"] = [f"A{i:03}" + "x" * 92 for i in range(100)]
        policy["legacy_v1"]["selected_symbols"] = policy["legacy_v1"]["allowed_symbols"]
        policy["legacy_v1"]["allowed_strategies"] = policy["legacy_v1"]["allowed_symbols"]
        policy["legacy_v1"]["selected_strategies"] = policy["legacy_v1"]["allowed_symbols"]
        policy["scope"]["account"]["account_id"] = "한" * 256
        original = policy["strategy"]["catalog"]["entries"][0]
        entries = []
        for index in range(100):
            entry = copy.deepcopy(original)
            entry["reference"]["strategy_id"] = f"strategy{index:03}" + "x" * 80
            entries.append(entry)
        policy["strategy"]["catalog"]["entries"] = entries
        policy["strategy"]["catalog"]["revision"] = catalog_from_wire(policy["strategy"]["catalog"], check_revision=False).revision
        policy["strategy"]["allowed"] = policy["strategy"]["selected"] = []
    results = []
    for check in (validate_v2, OFFLINE["check_policy_sections"]):
        try:
            check(policy)
        except ValueError:
            results.append(False)
        else:
            results.append(True)
    assert results == [accepted, accepted]


@pytest.mark.parametrize("section,field,value", [
    ("scope", "account", {"account_id": " fixture ", "environment": "DEMO"}),
    ("scope", "mode", True), ("scope", "selected_product", "OTHER"),
    ("scope", "allowed_products", ["LINEAR", "LINEAR"]),
    ("scope", "permission_profile_revision", "future"),
    ("scope", "selected_instruments", [{"venue": "BYBIT", "product": "LINEAR", "symbol": "SOLUSDT"}]),
    ("strategy", "allowed", [{"strategy_id": "alpha", "revision": "latest"}]),
    ("strategy", "selected", [{"strategy_id": "unknown", "revision": "r1"}]),
    ("risk_v2", "section_version", True), ("risk_v2", "product", "SPOT"),
    ("risk_v2", "per_trade_loss", "0"), ("risk_v2", "per_trade_loss", "01"),
    ("risk_v2", "per_trade_loss", "10000000000000000"),
    ("risk_v2", "per_trade_loss", "1.123456789"), ("risk_v2", "max_positions", 1000001),
    ("risk_v2", "liquidation_buffer_fraction", "1"), ("risk_v2", "position_mode", "HEDGE"),
])
def test_typed_scalar_refusals_match_finite_application_offline_boundaries(section, field, value):
    policy = typed_policy()
    policy[section][field] = value
    with pytest.raises(ValueError):
        validate_v2(policy)
    with pytest.raises(OFFLINE["SnapshotError"], match="Unsupported policy V2 sections"):
        OFFLINE["check_policy_sections"](policy)


def test_typed_catalog_and_global_order_remains_semantic_and_roundtrippable():
    policy = typed_policy()
    policy["strategy"]["catalog"]["entries"].reverse()
    policy["strategy"]["allowed"].reverse()
    policy["scope"]["mode"] = "ALL_IN_ONE"
    policy["scope"]["selected_instruments"] = None
    policy["strategy"]["selected"] = None
    assert validate_v2(policy) == policy
    OFFLINE["check_policy_sections"](policy)


@pytest.mark.parametrize("kind", ["typed", "partial", "spot"])
def test_three_file_bundle_recovers_typed_sections_without_application(state, tmp_path, kind):
    book, _ = policy_book(state, "migrated")
    policy = typed_policy()
    policy["legacy_v1"]["capital_usdt"] = "90.00"
    if kind == "partial":
        policy["risk_v2"]["caps"] = None
    elif kind == "spot":
        policy["scope"].update(selected_product="SPOT", allowed_products=["SPOT"],
                               allowed_instruments=None, selected_instruments=None)
        policy["risk_v2"] = policy["strategy"] = None
    expected = book.save_v2(2, policy, expected_strategy_generation=None, policy_sections_version=1)
    before = state_bytes(state)
    bundle, elsewhere = tmp_path / "bundle", tmp_path / "elsewhere"
    bundle.mkdir()
    elsewhere.mkdir()
    names = ("state_snapshot.py", "history_snapshot_validation.py", "native_restart_format.py")
    for name in names:
        shutil.copyfile(TOOL.with_name(name), bundle / name)
    executions = []
    for args in (("snapshot", "--source", state, "--target", tmp_path / "backup", "--writers-stopped"),
                 ("verify", "--source", tmp_path / "backup"),
                 ("restore", "--source", tmp_path / "backup", "--target", tmp_path / "restored")):
        result = subprocess.run([sys.executable, "-I", "-S", "-B", str(bundle / "state_snapshot.py"), *map(str, args)],
                                cwd=elsewhere, capture_output=True, text=True, timeout=60, check=False)
        executions.append({"argv": result.args, "cwd": str(elsewhere), "stdout": result.stdout,
                           "stderr": result.stderr, "exit": result.returncode})
        (tmp_path / "bundle-execution.json").write_text(json.dumps(executions, indent=2), encoding="utf-8")
        assert result.returncode == 0, result.stderr
    assert {path.name for path in bundle.iterdir()} == set(names)
    assert state_bytes(state) == before == state_bytes(tmp_path / "restored")
    assert RuntimePolicyBook(tmp_path / "restored/runtime-policy.sqlite3").read() == expected


def policy_book(directory: Path, kind: str) -> tuple[RuntimePolicyBook, str | None]:
    path = directory / "runtime-policy.sqlite3"
    if kind == "fresh":
        book = RuntimePolicyBook(path)
        draft = book.read()["policy"]
        draft["legacy_v1"].update(capital_usdt="100.00", liquidation_buffer_fraction="0.010")
        book.save_v2(0, draft, expected_strategy_generation=None)
        return book, None
    original = runpy.run_path(str(ROOT / "tests/execution/fixtures/runtime_policy_v1.py"))
    writer = original["load_v1"]()(path)
    draft = writer.read()["policy"]
    draft.update(capital_usdt="100.00", liquidation_buffer_fraction="0.010", mode="FULL_AUTO",
                 environment="DEMO", allowed_symbols=["BTCUSDT", "ETHUSDT"], selected_symbols=["ETHUSDT"],
                 allowed_strategies=[], selected_strategies=[])
    writer.save(0, draft)
    raw = json.dumps(dict(reversed(list(draft.items()))), indent=3) + "\n "
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE runtime_policy SET payload=?", (raw,))
    book = RuntimePolicyBook(path)
    if kind in {"migrated", "typed"}:
        book.migrate_v1(1)
    if kind == "typed":
        book.save_v2(2, typed_policy(), expected_strategy_generation=None, policy_sections_version=1)
    return book, raw


@pytest.mark.parametrize("kind", ["v1", "fresh", "migrated"])
def test_real_policy_snapshot_verify_restore_preserves_bytes_and_stopped_state(state, tmp_path, kind):
    book, raw = policy_book(state, kind)
    expected = book.read()
    before = state_bytes(state)
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    result = snapshot(state, backup)
    assert result.returncode == 0, result.stderr
    assert command("verify", "--source", backup).returncode == 0
    result = command("restore", "--source", backup, "--target", restored)
    assert result.returncode == 0, result.stderr
    assert state_bytes(restored) == before == state_bytes(state)
    actual = RuntimePolicyBook(restored / "runtime-policy.sqlite3").read()
    assert actual == expected
    assert actual["desired_state"] == "STOPPED"
    assert actual["worker_running"] is actual["live_order_enabled"] is False
    if kind == "migrated":
        with closing(sqlite3.connect(restored / "runtime-policy.sqlite3")) as db:
            assert db.execute("SELECT * FROM runtime_policy").fetchall() == [(1, 1, raw, "STOPPED")]
            assert db.execute("SELECT * FROM runtime_policy_v1_archive").fetchall() == [(1, 1, raw, "STOPPED")]
            assert db.execute("SELECT revision FROM runtime_policy_v2").fetchone() == (2,)
            assert len(db.execute("SELECT sql FROM sqlite_master WHERE type='trigger'").fetchall()) == 6


@pytest.mark.parametrize("kind", ["fresh", "migrated"])
def test_three_file_minimal_bundle_recovers_current_policy_without_application(state, tmp_path, kind):
    book, _ = policy_book(state, kind)
    expected = book.read()
    before = state_bytes(state)
    bundle, elsewhere = tmp_path / "bundle", tmp_path / "elsewhere"
    bundle.mkdir()
    elsewhere.mkdir()
    names = ("state_snapshot.py", "history_snapshot_validation.py", "native_restart_format.py")
    for name in names:
        shutil.copyfile(TOOL.with_name(name), bundle / name)
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    for args in (("snapshot", "--source", state, "--target", backup, "--writers-stopped"),
                 ("verify", "--source", backup), ("restore", "--source", backup, "--target", restored)):
        result = subprocess.run([sys.executable, "-I", "-S", "-B", str(bundle / "state_snapshot.py"),
                                 *map(str, args)], cwd=elsewhere, capture_output=True, text=True,
                                timeout=35, check=False)
        assert result.returncode == 0, result.stderr
    assert {path.name for path in bundle.iterdir()} == set(names)
    assert state_bytes(restored) == before == state_bytes(state)
    assert RuntimePolicyBook(restored / "runtime-policy.sqlite3").read() == expected


def test_migration_wal_roundtrip_preserves_raw_archive_fences_and_source(state, tmp_path):
    _, raw = policy_book(state, "v1")
    path = state / "runtime-policy.sqlite3"
    program = """import os,sqlite3,sys
from pathlib import Path
from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook
keeper=sqlite3.connect(sys.argv[1])
assert keeper.execute('PRAGMA journal_mode=WAL').fetchone()==('wal',)
keeper.execute('BEGIN')
keeper.execute('SELECT * FROM runtime_policy').fetchall()
RuntimePolicyBook(Path(sys.argv[1])).migrate_v1(1)
assert Path(sys.argv[1]+'-wal').stat().st_size>0
os._exit(0)
"""
    subprocess.run([sys.executable, "-B", "-c", program, str(path)], check=True, timeout=35)
    before = state_bytes(state)
    assert before["runtime-policy.sqlite3-wal"]
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    assert snapshot(state, backup).returncode == 0
    assert command("verify", "--source", backup).returncode == 0
    assert command("restore", "--source", backup, "--target", restored).returncode == 0
    assert state_bytes(state) == before == state_bytes(restored)
    with closing(sqlite3.connect(restored / path.name)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
        assert db.execute("SELECT payload,revision FROM runtime_policy_v1_archive").fetchone() == (raw, 1)
        fences = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
    from multi_exchange_trader.execution.runtime_policy import _fences
    assert fences == _fences()
    assert state_bytes(state) == before


def test_current_legacy_changes_do_not_replace_the_immutable_archive(state, tmp_path):
    book, raw = policy_book(state, "migrated")
    draft = book.read()["policy"]
    draft["legacy_v1"]["capital_usdt"] = "90.00"
    expected = book.save_v2(2, draft, expected_strategy_generation=None)
    before = state_bytes(state)
    assert snapshot(state, tmp_path / "backup").returncode == 0
    assert command("restore", "--source", tmp_path / "backup", "--target", tmp_path / "restored").returncode == 0
    assert state_bytes(state) == before == state_bytes(tmp_path / "restored")
    assert RuntimePolicyBook(tmp_path / "restored/runtime-policy.sqlite3").read() == expected
    with closing(sqlite3.connect(tmp_path / "restored/runtime-policy.sqlite3")) as db:
        assert db.execute("SELECT payload,revision FROM runtime_policy").fetchone() == (raw, 1)
        assert db.execute("SELECT payload,revision FROM runtime_policy_v1_archive").fetchone() == (raw, 1)


def damage_policy(path: Path, defect: str) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        if defect.startswith("version"):
            db.execute("PRAGMA user_version=" + defect[-1])
        elif defect.startswith("missing_") and defect[8:] in {"v2", "original", "archive"}:
            table = {"v2": "runtime_policy_v2", "original": "runtime_policy", "archive": "runtime_policy_v1_archive"}[defect[8:]]
            db.execute(f"DROP TABLE {table}")
        elif defect in {"extra_table", "extra_view", "extra_trigger", "extra_index"}:
            db.execute({"extra_table": "CREATE TABLE extra(id INTEGER)",
                        "extra_view": "CREATE VIEW extra AS SELECT * FROM runtime_policy_v2",
                        "extra_trigger": "CREATE TRIGGER extra AFTER INSERT ON runtime_policy_v2 BEGIN SELECT 1; END",
                        "extra_index": "CREATE UNIQUE INDEX extra ON runtime_policy_v2(revision) WHERE revision>0"}[defect])
        elif defect in {"missing_fence", "wrong_fence", "trigger_literal"}:
            name, sql = db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name LIMIT 1").fetchone()
            db.execute(f"DROP TRIGGER {name}")
            if defect != "missing_fence":
                db.execute(sql.replace("BEFORE", "AFTER") if defect == "wrong_fence" else
                           sql.replace("policy immutable", "policy  immutable"))
        elif defect.startswith("archive_"):
            name, sql = db.execute("SELECT name,sql FROM sqlite_master WHERE name='runtime_policy_v1_archive_immutable_update'").fetchone()
            db.execute(f"DROP TRIGGER {name}")
            db.execute("UPDATE runtime_policy_v1_archive SET " + {"archive_text": "payload=payload||' '",
                       "archive_revision": "revision=revision+1", "archive_invalid": "payload='{}'"}[defect])
            if defect == "archive_invalid":
                original_sql = db.execute("SELECT sql FROM sqlite_master WHERE name='runtime_policy_immutable_update'").fetchone()[0]
                db.execute("DROP TRIGGER runtime_policy_immutable_update")
                db.execute("UPDATE runtime_policy SET payload='{}'")
                db.execute(original_sql)
            db.execute(sql)
        elif defect in {"empty", "bad_id", "state_running", "revision_negative", "revision_float", "revision_unsafe", "revision_archive"}:
            db.execute("PRAGMA ignore_check_constraints=ON")
            if defect == "empty":
                db.execute("DELETE FROM runtime_policy_v2")
            else:
                db.execute("UPDATE runtime_policy_v2 SET " + {"bad_id": "id=2", "state_running": "desired_state='RUNNING'",
                    "revision_negative": "revision=-1", "revision_float": "revision=0.5",
                    "revision_unsafe": "revision=9007199254740992", "revision_archive": "revision=1"}[defect])
        elif defect in {"missing_key", "partial_key", "expression_key", "nocase_key", "duplicate_rows",
                        "column_type", "not_null", "id_check", "state_check", "hidden", "foreign_key"}:
            sql = db.execute("SELECT sql FROM sqlite_master WHERE name='runtime_policy_v2'").fetchone()[0]
            rows = db.execute("SELECT * FROM runtime_policy_v2").fetchall()
            if defect in {"missing_key", "partial_key", "expression_key", "nocase_key", "duplicate_rows"}:
                sql = sql.replace(" PRIMARY KEY", "")
            else:
                sql = {"column_type": sql.replace("payload TEXT", "payload BLOB"),
                       "not_null": sql.replace("payload TEXT NOT NULL", "payload TEXT"),
                       "id_check": sql.replace("CHECK(id=1)", "CHECK(id>0)"),
                       "state_check": sql.replace("CHECK(desired_state='STOPPED')", "CHECK(1)"),
                       "hidden": sql[:-1] + ",extra TEXT GENERATED ALWAYS AS ('hidden') VIRTUAL)",
                       "foreign_key": sql[:-1] + ",FOREIGN KEY(id) REFERENCES missing(id))"}[defect]
            db.execute("DROP TABLE runtime_policy_v2")
            db.execute(sql)
            db.executemany("INSERT INTO runtime_policy_v2(id,revision,payload,desired_state) VALUES(?,?,?,?)",
                           rows * (2 if defect == "duplicate_rows" else 1))
            if defect in {"partial_key", "expression_key", "nocase_key"}:
                expression = {"partial_key": "id) WHERE revision>0", "expression_key": "abs(id))",
                              "nocase_key": "id COLLATE NOCASE)"}[defect]
                db.execute("CREATE UNIQUE INDEX misleading ON runtime_policy_v2(" + expression)
        else:
            policy = json.loads(db.execute("SELECT payload FROM runtime_policy_v2").fetchone()[0])
            if defect in {"scope", "strategy", "risk_v2"}:
                policy[defect] = {"future": "DO_NOT_PUBLISH"}
            elif defect == "missing_section":
                del policy["scope"]
            elif defect == "extra_section":
                policy["future"] = None
            elif defect == "legacy_partial":
                policy["legacy_v1"] = {"capital_usdt": "100.00"}
            raw = json.dumps(policy)
            if defect == "duplicate_json":
                raw = raw.replace('"scope": null', '"scope": null,"scope": null')
            elif defect == "nonfinite_json":
                raw = raw.replace('"scope": null', '"scope": NaN')
            elif defect == "oversize_json":
                raw += " " * 65536
            db.execute("UPDATE runtime_policy_v2 SET payload=?", (raw,))


ARCHIVE_DEFECTS = {"missing_original", "missing_archive", "missing_fence", "wrong_fence", "trigger_literal",
                   "archive_text", "archive_revision", "archive_invalid", "revision_archive"}
DEFECTS = sorted(ARCHIVE_DEFECTS | {"version0", "version1", "version3", "missing_v2", "extra_table", "extra_view",
    "extra_trigger", "extra_index", "empty", "bad_id", "state_running", "revision_negative", "revision_float",
    "revision_unsafe", "missing_key", "partial_key", "expression_key", "nocase_key", "duplicate_rows", "column_type",
    "not_null", "id_check", "state_check", "hidden", "foreign_key", "scope", "strategy", "risk_v2",
    "missing_section", "extra_section", "legacy_partial", "duplicate_json", "nonfinite_json", "oversize_json"})


@pytest.mark.parametrize("defect", DEFECTS)
def test_invalid_current_policy_refuses_snapshot_and_rehashed_restore_before_publication(state, tmp_path, defect, capsys):
    policy_book(state, "migrated" if defect in ARCHIVE_DEFECTS else "fresh")
    invoke = OFFLINE["main"]
    backup = tmp_path / "backup"
    assert invoke(["snapshot", "--source", str(state), "--target", str(backup), "--writers-stopped"]) == 0
    for path in (state / "runtime-policy.sqlite3", backup / "data/runtime-policy.sqlite3"):
        damage_policy(path, defect)
    source_before, backup_before = state_bytes(state), state_bytes(backup / "data")
    invalid, restored = tmp_path / "invalid", tmp_path / "restored"
    assert invoke(["snapshot", "--source", str(state), "--target", str(invalid), "--writers-stopped"]) == 1
    assert not invalid.exists()
    rehash_manifest(backup)
    assert invoke(["restore", "--source", str(backup), "--target", str(restored)]) == 1
    assert not restored.exists()
    assert state_bytes(state) == source_before
    assert state_bytes(backup / "data") == backup_before
    errors = capsys.readouterr().err
    assert "Recovery refused:" in errors
    assert "DO_NOT_PUBLISH" not in errors and str(state) not in errors


@pytest.mark.parametrize("patch,accepted", [
    ({}, True), ({"capital_usdt": "9999999999999999.12345678"}, True),
    ({"liquidation_buffer_fraction": "0.010", "max_quote_age_seconds": "0"}, True),
    ({"max_positions": 1000000, "allowed_symbols": [], "selected_symbols": []}, True),
    ({"mode": "ALL_IN_ONE", "environment": "LIVE"}, True),
    ({"capital_usdt": "0"}, False), ({"capital_usdt": True}, False), ({"capital_usdt": 100}, False),
    ({"capital_usdt": "01"}, False), ({"capital_usdt": "1e2"}, False),
    ({"capital_usdt": "1.000000001"}, False), ({"capital_usdt": "10000000000000000"}, False),
    ({"capital_usdt": "NaN"}, False), ({"max_positions": True}, False), ({"max_positions": 0}, False),
    ({"max_positions": 1000001}, False), ({"max_positions": 1.5}, False), ({"mode": "OTHER"}, False),
    ({"allowed_symbols": ["BTCUSDT", "BTCUSDT"]}, False), ({"allowed_symbols": [[]]}, False),
    ({"allowed_symbols": ["bad symbol"]}, False), ({"allowed_symbols": ["S"] * 101}, False),
    ({"allowed_symbols": [], "selected_symbols": ["BTCUSDT"]}, False),
    ({"leverage_min": "3", "leverage_max": "2"}, False),
    ({"capital_usdt": "100", "max_total_margin": "101"}, False),
    ({"capital_usdt": "100", "margin_allocation": "101"}, False),
    ({"liquidation_buffer_fraction": "1"}, False), ({"unknown": None}, False),
])
def test_complete_bounded_v2_policy_codec_matches_current_application(tmp_path, patch, accepted):
    book = RuntimePolicyBook(tmp_path / "policy.sqlite3")
    policy = book.read()["policy"]
    policy["legacy_v1"].update(patch)
    raw = json.dumps(policy)
    results = []
    for validate in (lambda: validate_v2(parse_json(raw)),
                     lambda: OFFLINE["check_policy_legacy"](OFFLINE["json_payload"](raw)["legacy_v1"])):
        try:
            validate()
        except ValueError:
            results.append(False)
        else:
            results.append(True)
    assert results == [accepted, accepted]


@pytest.mark.parametrize("section,value", [("scope", {}), ("strategy", []), ("risk_v2", False),
    ("legacy_v1", {}), ("legacy_v1", None), ("scope", "later-pin")])
def test_complete_v2_section_refusals_match_application_and_offline(tmp_path, section, value):
    book = RuntimePolicyBook(tmp_path / "policy.sqlite3")
    policy = book.read()["policy"] | {section: value}
    raw = json.dumps(policy)
    with closing(sqlite3.connect(book.path)) as db, db:
        db.execute("UPDATE runtime_policy_v2 SET payload=?", (raw,))
    with pytest.raises(ValueError):
        validate_v2(parse_json(raw))
    with closing(sqlite3.connect(book.path)) as db, pytest.raises(OFFLINE["SnapshotError"]):
        OFFLINE["check_policy_v2"](db)


@pytest.mark.parametrize("kind", ["fresh", "migrated", "typed"])
def test_real_recovery_opens_only_disposable_readonly_copies(state, tmp_path, monkeypatch, kind):
    policy_book(state, kind)
    before = state_bytes(state)
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    original = sqlite3.connect
    connections, statements = [], []
    def guarded(database, *args, **kwargs):
        uri = str(database)
        assert "trader-sqlite-check-" in uri and "mode=ro" in uri
        assert state.as_uri() not in uri and backup.as_uri() not in uri
        connections.append(uri)
        db = original(database, *args, **kwargs)
        db.set_trace_callback(statements.append)
        return db
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", guarded)
        invoke = OFFLINE["main"]
        assert invoke(["snapshot", "--source", str(state), "--target", str(backup), "--writers-stopped"]) == 0
        assert invoke(["verify", "--source", str(backup)]) == 0
        assert invoke(["restore", "--source", str(backup), "--target", str(restored)]) == 0
    assert connections and statements
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "REPLACE", "VACUUM"))
                   or "WAL_CHECKPOINT" in sql.upper() for sql in statements)
    assert state_bytes(state) == before == state_bytes(backup / "data") == state_bytes(restored)


@pytest.mark.parametrize("defect", ["future_section", "typed_hash", "extra_view"])
def test_rehashed_invalid_typed_policy_never_publishes_or_modifies_inputs(state, tmp_path, defect, capsys):
    policy_book(state, "typed")
    backup = tmp_path / "backup"
    invoke = OFFLINE["main"]
    assert invoke(["snapshot", "--source", str(state), "--target", str(backup), "--writers-stopped"]) == 0
    for path in (state / "runtime-policy.sqlite3", backup / "data/runtime-policy.sqlite3"):
        with sqlite3.connect(path) as db:
            if defect == "extra_view":
                db.execute("CREATE VIEW extra AS SELECT * FROM runtime_policy_v2")
            else:
                raw = json.loads(db.execute("SELECT payload FROM runtime_policy_v2").fetchone()[0])
                if defect == "future_section":
                    raw["scope"]["section_version"] = 2
                else:
                    raw["strategy"]["catalog"]["entries"][1]["config_sha256"] = "d" * 64
                db.execute("UPDATE runtime_policy_v2 SET payload=?", (json.dumps(raw),))
    before_source, before_backup = state_bytes(state), state_bytes(backup / "data")
    rehash_manifest(backup)
    invalid, restored = tmp_path / "invalid", tmp_path / "restored"
    assert invoke(["snapshot", "--source", str(state), "--target", str(invalid), "--writers-stopped"]) == 1
    assert invoke(["restore", "--source", str(backup), "--target", str(restored)]) == 1
    assert not invalid.exists() and not restored.exists()
    assert state_bytes(state) == before_source
    assert state_bytes(backup / "data") == before_backup
    assert "Recovery refused:" in capsys.readouterr().err


def test_portable_policy_codec_source_matches_the_application_stdlib_region():
    source = (ROOT / "src/multi_exchange_trader/execution/policy_sections.py").read_text(encoding="utf-8")
    offline = TOOL.read_text(encoding="utf-8")
    begin, end = "# BEGIN PORTABLE POLICY SECTIONS", "# END PORTABLE POLICY SECTIONS"
    assert source[source.index(begin):source.index(end)] == offline[offline.index(begin):offline.index(end)]


def test_typed_committed_wal_roundtrip_preserves_source_and_archive(state, tmp_path):
    book, raw = policy_book(state, "migrated")
    policy = typed_policy()
    program = """import json,os,sqlite3,sys
from pathlib import Path
from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook
keeper=sqlite3.connect(sys.argv[1])
assert keeper.execute('PRAGMA journal_mode=WAL').fetchone()==('wal',)
keeper.execute('BEGIN')
keeper.execute('SELECT * FROM runtime_policy_v2').fetchall()
RuntimePolicyBook(Path(sys.argv[1])).save_v2(2,json.loads(sys.argv[2]),expected_strategy_generation=None,policy_sections_version=1)
assert Path(sys.argv[1]+'-wal').stat().st_size>0
os._exit(0)
"""
    result = subprocess.run([sys.executable, "-B", "-c", program, str(book.path), json.dumps(policy)],
                            check=False, timeout=60, capture_output=True, text=True)
    (tmp_path / "wal-execution.json").write_text(json.dumps({"argv": result.args, "stdout": result.stdout,
        "stderr": result.stderr, "exit": result.returncode}, indent=2), encoding="utf-8")
    assert result.returncode == 0, result.stderr
    before = state_bytes(state)
    assert before["runtime-policy.sqlite3-wal"]
    invoke = OFFLINE["main"]
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    assert invoke(["snapshot", "--source", str(state), "--target", str(backup), "--writers-stopped"]) == 0
    assert invoke(["restore", "--source", str(backup), "--target", str(restored)]) == 0
    assert state_bytes(state) == before == state_bytes(restored)
    actual = RuntimePolicyBook(restored / book.path.name).read()
    assert actual["revision"] == 3 and actual["policy"] == policy
    with sqlite3.connect(restored / book.path.name) as db:
        assert db.execute("SELECT payload FROM runtime_policy_v1_archive").fetchone() == (raw,)
