"""Finite STORAGE_ONLY recovery of genuine synthetic J V2 admissions.

The residual test deliberately distinguishes portable framing from typed replay.
No fixture or assertion grants current source, venue, or financial authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import runpy
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from decimal import localcontext
from pathlib import Path
from typing import Any, cast

import pytest
from test_native_snapshot import identity, payload
from test_state_snapshot import ROOT, TOOL, rehash_manifest, state_bytes
from test_state_snapshot import history_state as history_fixture
from test_state_snapshot import runtime_state as runtime_fixture
from test_state_snapshot import state as state_fixture

from multi_exchange_trader.execution import native_restart_store as native
from multi_exchange_trader.execution import risk_v2_contracts as contracts
from multi_exchange_trader.execution.journal import CommandJournal, CommandKind, ExecutionScope
from multi_exchange_trader.execution.risk_v2_codec import (
    DurableEntryIntentV2,
    encode_entry_intent_v2,
)

state = state_fixture
runtime_state = runtime_fixture
history_state = history_fixture
OFFLINE = runpy.run_path(str(TOOL))
SCOPE = ExecutionScope("한글", "DEMO")
OFF = ("TRADER_CREDENTIAL_FILE", "TRADER_ACCOUNT_PROFILE_FILE", "BYBIT_ENV_FILE",
       "AI_ENV_FILE", "ANTHROPIC_ENV_FILE")
sys.path.insert(0, str(ROOT / "tests/execution"))
try:
    FIXTURES = runpy.run_path(str(ROOT / "tests/execution/test_reservations_v2.py"))
finally:
    sys.path.pop(0)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def cli(*args: object, tool: Path = TOOL, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update({name: "off" for name in OFF})
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([sys.executable, "-I", "-S", "-B", str(tool), *map(str, args)],
                          cwd=cwd, env=env, text=True, capture_output=True, timeout=45,
                          check=False)


def take(source: Path, target: Path, *, tool: Path = TOOL, cwd: Path = ROOT) -> None:
    result = cli("snapshot", "--source", source, "--target", target, "--writers-stopped",
                 tool=tool, cwd=cwd)
    assert result.returncode == 0, result.stderr


def sql(directory: Path, statement: str, parameters: tuple[object, ...] = ()) -> None:
    with closing(sqlite3.connect(directory / "execution.sqlite3")) as db:
        db.execute(statement, parameters)
        db.commit()


def admit(directory: Path, key: str = "COMMAND", *, phase: str = "PENDING",
          margin_only: bool = False) -> DurableEntryIntentV2:
    values = FIXTURES["fixture_values"](key, margin_only=margin_only)
    intent = cast(DurableEntryIntentV2, values[0])
    with CommandJournal(directory / "execution.sqlite3", SCOPE) as journal:
        if journal._db.execute("SELECT name FROM sqlite_master WHERE name='reservation_meta_v2'").fetchone() is None:
            FIXTURES["bound"](journal)
        FIXTURES["register"](journal, values)
        if phase != "PENDING":
            FIXTURES["claim"](journal, intent)
        if phase == "UNKNOWN":
            journal.mark_unknown(key, "synthetic uncertainty")
        elif phase in {"ACKNOWLEDGED", "TERMINAL"}:
            journal.acknowledge(key, "synthetic acknowledgement")
            if phase == "TERMINAL":
                journal.release_reservation(key, confirmed_terminal=True,
                                            terminal_evidence="synthetic final observation")
        elif phase == "REJECTED":
            journal.reject(key, "synthetic rejection")
        assert journal.list_commands()
    return intent


@pytest.fixture
def v2(runtime_state: Path) -> Path:
    admit(runtime_state)
    return runtime_state


def rewrite(directory: Path, change: Callable[[dict[str, Any]], None], *,
            format_change: Callable[[bytes], bytes] | None = None) -> None:
    with closing(sqlite3.connect(directory / "execution.sqlite3")) as db:
        frame = json.loads(db.execute("SELECT payload FROM commands WHERE command_key='COMMAND'").fetchone()[0])
        change(frame)
        frame.pop("content_sha256", None)
        frame["content_sha256"] = hashlib.sha256(canonical(frame)).hexdigest()
        wire = canonical(frame)
        if format_change is not None:
            wire = format_change(wire)
        text = wire.decode("utf-8")
        db.execute("UPDATE commands SET payload=?,payload_hash=? WHERE command_key='COMMAND'",
                   (text, hashlib.sha256(wire).hexdigest()))
        db.commit()


def refusal(source: Path, target: Path) -> None:
    before = state_bytes(source)
    result = cli("snapshot", "--source", source, "--target", target, "--writers-stopped")
    assert result.returncode == 1
    assert not target.exists()
    assert "SECRET" not in result.stderr
    assert state_bytes(source) == before


def test_true_j_whitelist_roundtrip_three_file_isolated_bundle(v2: Path, tmp_path: Path) -> None:
    bundle, elsewhere = tmp_path / "bundle", tmp_path / "elsewhere"
    bundle.mkdir()
    elsewhere.mkdir()
    names = ("state_snapshot.py", "history_snapshot_validation.py", "native_restart_format.py")
    # Hash before each source copy; the receipt harness independently freezes owned inputs.
    pins = {name: hashlib.sha256(TOOL.with_name(name).read_bytes()).hexdigest() for name in names}
    for name in names:
        assert hashlib.sha256(TOOL.with_name(name).read_bytes()).hexdigest() == pins[name]
        shutil.copyfile(TOOL.with_name(name), bundle / name)
    (tmp_path / "copy-source-pins.json").write_text(json.dumps(pins), encoding="utf-8")
    before = state_bytes(v2)
    take(v2, tmp_path / "backup", tool=bundle / "state_snapshot.py", cwd=elsewhere)
    assert cli("verify", "--source", tmp_path / "backup", tool=bundle / "state_snapshot.py",
               cwd=elsewhere).returncode == 0
    assert cli("restore", "--source", tmp_path / "backup", "--target", tmp_path / "restored",
               tool=bundle / "state_snapshot.py", cwd=elsewhere).returncode == 0
    assert state_bytes(v2) == before == state_bytes(tmp_path / "restored")
    assert {path.name for path in bundle.iterdir()} == set(names)
    with CommandJournal(tmp_path / "restored/execution.sqlite3", SCOPE) as journal:
        restored = journal.list_commands()
        assert len(restored) == 1 and restored[0].reservation_v2 is not None


@pytest.mark.parametrize("version", [None, 1, 3, True, "2", "MISSING"])
def test_reserved_marker_cannot_downgrade_without_pair(runtime_state: Path, tmp_path: Path,
                                                       version: object) -> None:
    frame: dict[str, object] = {"schema": "RISK_V2_DURABLE_INTENT"}
    if version != "MISSING":
        frame["wire_version"] = version
    with CommandJournal(runtime_state / "execution.sqlite3", SCOPE) as journal:
        journal.register("COMMAND", CommandKind.ENTRY, {"legacy": True})
    raw = canonical(frame)
    sql(runtime_state, "UPDATE commands SET payload=?,payload_hash=? WHERE command_key='COMMAND'",
        (raw.decode("ascii"), hashlib.sha256(raw).hexdigest()))
    refusal(runtime_state, tmp_path / "refused")


@pytest.mark.parametrize("phase", ["PENDING", "SENDING", "UNKNOWN", "ACKNOWLEDGED", "REJECTED", "TERMINAL"])
@pytest.mark.parametrize("margin_only", [False, True])
def test_genuine_states_and_plan_with_margin(runtime_state: Path, tmp_path: Path,
                                             phase: str, margin_only: bool) -> None:
    admit(runtime_state, phase=phase, margin_only=margin_only)
    before = state_bytes(runtime_state)
    take(runtime_state, tmp_path / "backup")
    assert state_bytes(runtime_state) == before


@pytest.mark.parametrize("kind", ["absent", "empty", "meta", "max-meta"])
def test_lazy_pair_storage_lifecycle(runtime_state: Path, tmp_path: Path, kind: str) -> None:
    if kind != "absent":
        with CommandJournal(runtime_state / "execution.sqlite3", SCOPE) as journal:
            FIXTURES["bound"](journal)
        if kind == "empty":
            sql(runtime_state, "DELETE FROM reservation_meta_v2")
        elif kind == "max-meta":
            sql(runtime_state, "UPDATE reservation_meta_v2 SET capacity_revision=?,owner_epoch=?",
                (2**63 - 1, "A" * 96))
        else:
            sql(runtime_state, "UPDATE reservation_meta_v2 SET capacity_revision=0")
    take(runtime_state, tmp_path / "backup")


@pytest.mark.parametrize("defect", ["partial", "extra-table", "view", "check", "nocase", "extra-index",
    "replacement-unique", "orphan", "missing-meta", "meta-version", "meta-epoch", "meta-revision",
    "row-version", "row-key", "row-symbol", "row-hash", "row-q", "row-risk", "row-margin",
    "legacy-amount", "kind", "client", "terminal", "delete-reservation"])
def test_schema_and_join_refusals_snapshot_and_rehashed_backup(v2: Path, tmp_path: Path,
                                                               defect: str) -> None:
    backup = tmp_path / "backup"
    take(v2, backup)

    def corrupt(directory: Path) -> None:
        if defect == "partial":
            sql(directory, "DROP TABLE reservation_meta_v2")
        elif defect == "extra-table":
            sql(directory, "CREATE TABLE SECRET(x)")
        elif defect == "view":
            sql(directory, "CREATE VIEW SECRET AS SELECT * FROM entry_reservations_v2")
        elif defect in {"check", "nocase", "replacement-unique"}:
            with closing(sqlite3.connect(directory / "execution.sqlite3")) as db:
                ddl = db.execute("SELECT sql FROM sqlite_master WHERE name='entry_reservations_v2'").fetchone()[0]
                rows = db.execute("SELECT * FROM entry_reservations_v2").fetchall()
                db.execute("DROP TABLE entry_reservations_v2")
                ddl = ddl.replace("version INTEGER NOT NULL", "version INTEGER NOT NULL CHECK(version=2)") if defect == "check" else ddl.replace("command_key TEXT NOT NULL", "command_key TEXT NOT NULL COLLATE NOCASE") if defect == "nocase" else ddl.replace("PRIMARY KEY", "UNIQUE")
                db.execute(ddl)
                db.executemany("INSERT INTO entry_reservations_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
                db.commit()
        elif defect == "extra-index":
            sql(directory, "CREATE INDEX extra ON entry_reservations_v2(command_key)")
        elif defect == "orphan":
            sql(directory, "DELETE FROM commands WHERE command_key='COMMAND'")
        elif defect == "missing-meta":
            sql(directory, "DELETE FROM reservation_meta_v2")
        elif defect.startswith("meta-"):
            field, value = {"meta-version": ("schema_version", 3), "meta-epoch": ("owner_epoch", "SECRET "),
                            "meta-revision": ("capacity_revision", -1)}[defect]
            sql(directory, f"UPDATE reservation_meta_v2 SET {field}=?", (value,))
        elif defect.startswith("row-"):
            field, value = {"row-version": ("version", 3), "row-key": ("command_key", "OTHER"),
                "row-symbol": ("instrument_symbol", "other"), "row-hash": ("envelope_sha256", "0" * 64),
                "row-q": ("base_quantity", "1.0"), "row-risk": ("risk_amount", "12.00"),
                "row-margin": ("margin_amount", "0")}[defect]
            sql(directory, f"UPDATE entry_reservations_v2 SET {field}=?", (value,))
        elif defect == "delete-reservation":
            sql(directory, "DELETE FROM entry_reservations_v2")
        else:
            field, value = {"legacy-amount": ("risk", "1"), "kind": ("kind", "EXIT"),
                            "client": ("client_id", "ec_OTHER"), "terminal": ("terminal", 2)}[defect]
            sql(directory, f"UPDATE commands SET {field}=? WHERE command_key='COMMAND'", (value,))

    corrupt(v2)
    refusal(v2, tmp_path / "refused")
    corrupt(backup / "data")
    rehash_manifest(backup)
    before = state_bytes(backup)
    assert cli("verify", "--source", backup).returncode == 1
    assert cli("restore", "--source", backup, "--target", tmp_path / "out").returncode == 1
    assert not (tmp_path / "out").exists() and state_bytes(backup) == before


@pytest.mark.parametrize("defect", ["version", "missing-version", "record", "outer-extra", "root-extra", "root-version",
    "key", "strategy", "hashes", "protection", "generation", "quantity", "scope", "pin", "time",
    "binding", "minimum", "sizing-envelope", "valuation", "tier", "risk", "margin", "decimal",
    "source-bound", "float", "integer", "depth", "oversize", "duplicate", "noncanonical", "checksum",
    "raw-commitment"])
def test_wire_refusals_after_rehashing_both_paths(v2: Path, tmp_path: Path, defect: str) -> None:
    backup = tmp_path / "backup"
    take(v2, backup)

    def change(frame: dict[str, Any]) -> None:
        fields = frame["fields"]
        envelope = fields["envelope"]
        prep = fields["assessment"]["preparation"]
        if defect == "version": frame["wire_version"] = True
        elif defect == "missing-version": frame.pop("wire_version")
        elif defect == "record": frame["record_type"] = "SECRET"
        elif defect == "outer-extra": frame["SECRET"] = 1
        elif defect == "root-extra": fields["SECRET"] = 1
        elif defect == "root-version": fields["schema_version"] = False
        elif defect == "key": fields["command_key"] = "OTHER"
        elif defect == "strategy": fields["strategy"]["revision"] = "OTHER"
        elif defect == "hashes": fields["source_record_sha256s"] *= 2
        elif defect == "protection": fields["protection_policy"]["revision"] = "OTHER"
        elif defect == "generation": fields["protection_policy"]["generation"] = 2**63
        elif defect == "quantity": envelope["base_quantity"] = "1.0"
        elif defect == "scope": envelope["scope"]["product_scope"]["account"]["account_id"] = "OTHER"
        elif defect == "pin": envelope["instrument_pin"]["native_instrument_id"] = "OTHER"
        elif defect == "time": envelope["valid_until"] = "2026-11-03T00:00:01Z"
        elif defect == "binding": prep["binding"]["envelope_sha256"] = "0" * 64
        elif defect == "minimum": prep["minimum_amount"]["envelope_sha256"] = "0" * 64
        elif defect == "sizing-envelope": prep["sizing"]["envelope"]["origin_quote_id"] = "OTHER"
        elif defect == "valuation": prep["valuation"]["scope"]["strategy"]["revision"] = "OTHER"
        elif defect == "tier": prep["tier_bound"]["instrument"]["symbol"] = "OTHER"
        elif defect == "raw-commitment":
            envelope["origin_quote_id"] = "OTHER"
            prep["sizing"]["envelope"] = json.loads(json.dumps(envelope))
            commitment = hashlib.sha256(canonical({"schema": "RISK_V2_CANONICAL_V1",
                "record_type": "FixedLimitEnvelopeV2", "fields": envelope})).hexdigest()
            prep["binding"]["envelope_sha256"] = commitment
            prep["minimum_amount"]["envelope_sha256"] = commitment
        elif defect == "risk": prep["sizing"]["estimated_loss_quote"] = "0"
        elif defect == "margin": fields["assessment"]["required_reservation_quote"] = None
        elif defect == "decimal": fields["protection_policy"]["stop_price"] = "090.00"
        elif defect == "source-bound": fields["protection_policy"]["take_profit_price"] = "1E+1001"
        elif defect == "float": fields["assessment"]["opaque"] = 1.5
        elif defect == "integer": fields["assessment"]["opaque"] = 10**19
        elif defect == "depth": fields["assessment"]["opaque"] = [[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]
        elif defect == "oversize": fields["assessment"]["opaque"] = "SECRET" * 12000

    def formatting(raw: bytes) -> bytes:
        if defect == "duplicate": return raw.replace(b'"wire_version":2', b'"wire_version":2,"wire_version":2')
        if defect == "noncanonical": return b" " + raw
        if defect == "checksum":
            tree = json.loads(raw)
            tree["content_sha256"] = "0" * 64
            return canonical(tree)
        return raw

    for directory in (v2, backup / "data"):
        rewrite(directory, change, format_change=formatting)
    refusal(v2, tmp_path / "refused")
    rehash_manifest(backup)
    before = state_bytes(backup)
    assert cli("verify", "--source", backup).returncode == 1
    assert cli("restore", "--source", backup, "--target", tmp_path / "out").returncode == 1
    assert state_bytes(backup) == before and not (tmp_path / "out").exists()


def test_storage_only_arithmetic_residual_fails_genuine_restored_j_replay(v2: Path, tmp_path: Path) -> None:
    rewrite(v2, lambda frame: frame["fields"]["assessment"]["preparation"]["sizing"].update(unit_loss_quote="11.0000"))
    before = state_bytes(v2)
    take(v2, tmp_path / "backup")
    assert cli("verify", "--source", tmp_path / "backup").returncode == 0
    assert cli("restore", "--source", tmp_path / "backup", "--target", tmp_path / "restored").returncode == 0
    assert state_bytes(tmp_path / "restored") == before == state_bytes(v2)
    with (CommandJournal(tmp_path / "restored/execution.sqlite3", SCOPE) as journal,
          pytest.raises(contracts.RiskV2Rejected)):
        journal.list_commands()


@pytest.mark.parametrize("table", ["reservation_meta_v2", "entry_reservations_v2"])
def test_all_v2_scopes_join_history_even_meta_and_terminal(history_state: Path, tmp_path: Path,
                                                          table: str) -> None:
    # Isolate a meta-only foreign scope from all legacy rows for that case.
    if table == "reservation_meta_v2":
        with CommandJournal(history_state / "execution.sqlite3", ExecutionScope("backup-fixture", "DEMO")) as journal:
            FIXTURES["bound"](journal)
        sql(history_state, "INSERT INTO reservation_meta_v2 VALUES ('foreign','LIVE',2,0,'OWNER_1')")
    else:
        admit(history_state, phase="TERMINAL")
    refusal(history_state, tmp_path / "refused")


def test_multiple_linked_scopes_without_history_and_legacy_version_two(v2: Path, tmp_path: Path) -> None:
    with CommandJournal(v2 / "execution.sqlite3", ExecutionScope("legacy", "LIVE")) as journal:
        FIXTURES["bound"](journal)
        journal.register("legacy", CommandKind.ENTRY, {"schema_version": 2})
    take(v2, tmp_path / "backup")


def test_v2_digest_empty_meta_and_stale_native_seal(history_state: Path, tmp_path: Path) -> None:
    baseline = native.application_digest(history_state)
    with CommandJournal(history_state / "execution.sqlite3", ExecutionScope("backup-fixture", "DEMO")) as journal:
        FIXTURES["bound"](journal)
    meta = native.application_digest(history_state)
    assert meta != baseline
    sql(history_state, "DELETE FROM reservation_meta_v2")
    empty = native.application_digest(history_state)
    assert empty != meta and empty != baseline
    seals = native.NativeRestartStore(history_state)
    seals.seal(identity(), payload())
    sql(history_state, "INSERT INTO reservation_meta_v2 VALUES ('backup-fixture','DEMO',2,0,'OWNER_2')")
    assert native.application_digest(history_state) != empty
    refusal(history_state, tmp_path / "refused")


def test_terminal_reservation_row_remains_in_native_all_row_digest(runtime_state: Path) -> None:
    admit(runtime_state, phase="TERMINAL")
    before = native.application_digest(runtime_state)
    portable = OFFLINE["_NATIVE_VALIDATOR"]
    assert before == portable["application_digest"](runtime_state)
    sql(runtime_state, "UPDATE entry_reservations_v2 SET risk_amount='0'")
    after = native.application_digest(runtime_state)
    assert after != before and after == portable["application_digest"](runtime_state)


def test_committed_v2_wal_checks_open_only_disposable_readonly_scratch(v2: Path, tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    original = sqlite3.connect
    calls: list[str] = []
    with closing(original(v2 / "execution.sqlite3")) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE reservation_meta_v2 SET owner_epoch='OWNER_2'")
        writer.commit()
        before = state_bytes(v2)
        assert before["execution.sqlite3-wal"]

        def readonly(database: str, *args: Any, **kwargs: Any) -> sqlite3.Connection:
            assert "mode=ro" in str(database)
            assert str(v2).replace("\\", "/") not in str(database)
            calls.append(str(database))
            db = original(database, *args, **kwargs)

            def guard(action: int, first: str | None, second: str | None,
                      _database: str | None, _trigger: str | None) -> int:
                if action == sqlite3.SQLITE_PRAGMA and first == "wal_checkpoint":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            db.set_authorizer(guard)
            return cast(sqlite3.Connection, db)

        monkeypatch.setattr(sqlite3, "connect", readonly)
        assert OFFLINE["main"](["snapshot", "--source", str(v2), "--target", str(tmp_path / "backup"),
                                "--writers-stopped"]) == 0
        assert calls and state_bytes(v2) == before


def test_tree_depth_includes_scalar_beyond_deepest_container(v2: Path) -> None:
    nested: object = 0
    for _ in range(29):
        nested = [nested]
    rewrite(v2, lambda frame: frame["fields"]["assessment"].update(opaque=nested))
    with closing(sqlite3.connect(v2 / "execution.sqlite3")) as db:
        wire = db.execute("SELECT payload FROM commands WHERE command_key='COMMAND'").fetchone()[0]
    with pytest.raises(OFFLINE["SnapshotError"]):
        OFFLINE["_v2_frame"](wire)


def test_genuine_no_positive_root_and_max_generation(runtime_state: Path, tmp_path: Path) -> None:
    values = list(FIXTURES["fixture_values"]())
    intent = cast(DurableEntryIntentV2, values[0])
    other = cast(DurableEntryIntentV2, FIXTURES["actual_intent"]("NO_POSITIVE_ROOT"))
    assert intent.assessment.liquidation is not None and other.assessment.liquidation is not None
    liquidation = replace(intent.assessment.liquidation, outcome=other.assessment.liquidation.outcome)
    values[0] = replace(intent, assessment=replace(intent.assessment, liquidation=liquidation,
                        liquidation_buffer_result="MODEL_NO_POSITIVE_TRIGGER"),
                        protection_policy=replace(intent.protection_policy, generation=2**63 - 1))
    with CommandJournal(runtime_state / "execution.sqlite3", SCOPE) as journal:
        FIXTURES["bound"](journal)
        FIXTURES["register"](journal, values)
        assert journal.list_commands()[0].reservation_v2 is not None
    take(runtime_state, tmp_path / "backup")


def test_genuine_exact_65536_byte_wire_and_65537_refusal(runtime_state: Path, tmp_path: Path) -> None:
    values = list(FIXTURES["fixture_values"]())
    intent = replace(cast(DurableEntryIntentV2, values[0]), plan_id="P", source_record_sha256s=("0" * 64,))
    baseline = len(encode_entry_intent_v2(intent))
    count = 1 + (65536 - baseline) // 67
    intent = replace(intent, source_record_sha256s=tuple(f"{index:064x}" for index in range(count)))
    padding = 65536 - len(encode_entry_intent_v2(intent))
    values[0] = replace(intent, plan_id="P" + "x" * padding)
    assert len(encode_entry_intent_v2(values[0]).encode("utf-8")) == 65536
    with CommandJournal(runtime_state / "execution.sqlite3", SCOPE) as journal:
        FIXTURES["bound"](journal)
        FIXTURES["register"](journal, values)
        assert journal.list_commands()
    take(runtime_state, tmp_path / "backup")
    rewrite(runtime_state, lambda frame: frame["fields"].update(plan_id=frame["fields"]["plan_id"] + "x"))
    with closing(sqlite3.connect(runtime_state / "execution.sqlite3")) as db:
        assert len(db.execute("SELECT payload FROM commands").fetchone()[0].encode("utf-8")) == 65537
    refusal(runtime_state, tmp_path / "refused")


@pytest.mark.parametrize("value,source", [("-0.00", False), ("0E+65536", False),
    ("1E-65536", False), ("1E+1000", True), ("123456789.123456789", True)])
def test_decimal_identity_keeps_scale_signed_zero_and_context_independence(value: str, source: bool) -> None:
    with localcontext() as context:
        context.prec = 2
        OFFLINE["_v2_decimal"](value, source=source)


@pytest.mark.parametrize("value,source", [("00", False), ("1.0E+0", False), ("1E+000001", False),
    ("1E+65537", False), ("1E+1001", True), ("-1", False), ("NaN", False), ("1e+2", False),
    ("1E+99999999", False)])
def test_decimal_noncanonical_and_boundaries_refuse(value: str, source: bool) -> None:
    with pytest.raises(OFFLINE["SnapshotError"]):
        OFFLINE["_v2_decimal"](value, source=source)
