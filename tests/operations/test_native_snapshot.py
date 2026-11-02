"""Synthetic complete generations and portable, source-safe native validation."""
from __future__ import annotations

import hashlib
import json
import runpy
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from test_state_snapshot import (
    ROOT,
    TOOL,
    command,
    rehash_manifest,
    runtime_state,  # noqa: F401
    snapshot,
    state,  # noqa: F401
    state_bytes,
)
from test_state_snapshot import history_state as history_state_fixture

from multi_exchange_trader.execution import native_restart_store as store
from multi_exchange_trader.execution.protection import ProtectionScope

history_state = history_state_fixture

CANONICAL = ROOT / "src/multi_exchange_trader/execution/native_restart_format.py"
PORTABLE = TOOL.with_name("native_restart_format.py")


def identity() -> store.NativeRestartIdentity:
    return store.NativeRestartIdentity(ProtectionScope("backup-fixture", "DEMO"),
        "BYBIT-UNIFIED", "WORKER-001", "OwnedExecution-001", ("BTCUSDT-LINEAR.BYBIT",))


def payload() -> dict[str, object]:
    return {"schema": 1, "encoding": "msgpack-base64", "instruments": [],
        "orders": [], "positions": []}


@pytest.fixture
def native_state(history_state: Path) -> Path:
    store.NativeRestartStore(history_state).seal(identity(), payload())
    return history_state


def rewrite(path: Path, change: Any) -> None:
    document = json.loads(path.read_bytes())
    change(document)
    document.pop("content_hash")
    document["content_hash"] = hashlib.sha256(store.canonical_bytes(document)).hexdigest()
    path.write_bytes(store.canonical_bytes(document))


def test_v4_complete_roundtrip_preserves_seal_and_source(native_state: Path, tmp_path: Path) -> None:
    before = state_bytes(native_state)
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    result = snapshot(native_state, backup)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((backup / "manifest.json").read_text())
    assert manifest["format_version"] == 4
    assert manifest["native_files"] == ["native-restart.json"]
    assert manifest["history_files"] == ["history.sqlite3"]
    assert len(manifest["runtime_files"]) == 4
    assert command("verify", "--source", backup, "--require-native").returncode == 0
    result = command("restore", "--source", backup, "--target", restored, "--require-native")
    assert result.returncode == 0, result.stderr
    assert state_bytes(restored) == before == state_bytes(native_state)
    assert store.NativeRestartStore(restored).load(identity()) is not None


@pytest.mark.parametrize("missing", ["native-restart.json", "execution.sqlite3", "runtime-state.sqlite3",
    "runtime-policy.sqlite3", "ai-dispatch.sqlite3", "history.sqlite3"])
def test_native_requires_every_generation_member(native_state: Path, tmp_path: Path, missing: str) -> None:
    (native_state / missing).unlink()
    result = command("snapshot", "--source", native_state, "--target", tmp_path / "backup",
        "--writers-stopped", "--require-native")
    assert result.returncode == 1
    assert not (tmp_path / "backup").exists()
    if missing != "native-restart.json":
        assert snapshot(native_state, tmp_path / "automatic").returncode == 1
        assert not (tmp_path / "automatic").exists()


@pytest.mark.parametrize("defect", ["active", "stale", "mixed", "identity", "unknown", "nested",
    "duplicate", "nonfinite", "oversize", "corrupt"])
def test_native_invalid_refuses_even_without_flag_before_publication(
    native_state: Path, tmp_path: Path, defect: str,
) -> None:
    path = native_state / "native-restart.json"
    if defect == "active":
        rewrite(path, lambda d: d.update(state="active"))
    elif defect == "stale":
        with closing(sqlite3.connect(native_state / "execution.sqlite3")) as db:
            db.execute("INSERT INTO projections VALUES ('backup-fixture','DEMO','extra',1,'{}')")
            db.commit()
    elif defect == "mixed":
        rewrite(path, lambda d: d.update(application_digest=store.application_digest(tmp_path)))
    elif defect == "identity":
        rewrite(path, lambda d: d["identity"]["scope"].update(account_id="foreign"))
    elif defect == "unknown":
        rewrite(path, lambda d: d.update(extra="SECRET"))
    elif defect == "nested":
        rewrite(path, lambda d: d["payload"].update(orders=[{"events": ["eA=="], "state": {}}]))
    else:
        path.write_bytes({"duplicate": b'{"schema":1,"schema":1}', "nonfinite": b'{"x":NaN}',
            "oversize": b" " * (store.MAX_BYTES + 1), "corrupt": b"{"}[defect])
    before = state_bytes(native_state)
    result = snapshot(native_state, tmp_path / "backup")
    assert result.returncode == 1
    assert "SECRET" not in result.stderr
    assert not (tmp_path / "backup").exists()
    assert state_bytes(native_state) == before


@pytest.mark.parametrize("defect", ["downgrade", "omitted", "invented", "missing-native"])
def test_v4_manifest_cannot_hide_native_generation(native_state: Path, tmp_path: Path, defect: str) -> None:
    backup = tmp_path / "backup"
    assert snapshot(native_state, backup).returncode == 0
    manifest = json.loads((backup / "manifest.json").read_text())
    if defect == "downgrade":
        manifest["format_version"] = 3
        manifest.pop("native_files")
    elif defect == "omitted":
        manifest.pop("native_files")
    elif defect == "invented":
        manifest["native_files"] = ["native-other.json"]
    else:
        (backup / "data/native-restart.json").unlink()
        manifest["files"] = [item for item in manifest["files"] if item["path"] != "native-restart.json"]
    (backup / "manifest.json").write_text(json.dumps(manifest))
    rehash_manifest(backup)
    result = command("restore", "--source", backup, "--target", tmp_path / "restored")
    assert result.returncode == 1 and not (tmp_path / "restored").exists()


def test_require_native_rejects_legacy_verify_and_restore(history_state: Path, tmp_path: Path) -> None:
    backup = tmp_path / "backup"
    assert snapshot(history_state, backup).returncode == 0
    assert command("verify", "--source", backup).returncode == 0
    assert command("verify", "--source", backup, "--require-native").returncode == 1
    assert command("restore", "--source", backup, "--target", tmp_path / "out",
        "--require-native").returncode == 1
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("defect", ["active", "mixed", "nested", "scope"])
def test_rehashed_native_backup_still_requires_semantic_generation_validation(
    native_state: Path, tmp_path: Path, defect: str,
) -> None:
    backup = tmp_path / "backup"
    assert snapshot(native_state, backup).returncode == 0
    changes = {
        "active": lambda d: d.update(state="active"),
        "mixed": lambda d: d.update(application_digest="0" * 64),
        "nested": lambda d: d["payload"].update(positions=[{"events": [], "state": {"x": 1}}]),
        "scope": lambda d: d["identity"]["scope"].update(account_id="foreign"),
    }
    rewrite(backup / "data/native-restart.json", changes[defect])
    rehash_manifest(backup)
    before = state_bytes(backup)
    assert command("verify", "--source", backup).returncode == 1
    result = command("restore", "--source", backup, "--target", tmp_path / "out")
    assert result.returncode == 1 and not (tmp_path / "out").exists()
    assert state_bytes(backup) == before


def test_extraction_golden_outputs_and_byte_identical_portable_copy(tmp_path: Path) -> None:
    assert CANONICAL.read_bytes() == PORTABLE.read_bytes()
    pure = runpy.run_path(str(PORTABLE))
    value = {"unicode": "한글", "nested": [None, True, 1, 1.25]}
    assert store.canonical_bytes(value) == b'{"nested":[null,true,1,1.25],"unicode":"\\ud55c\\uae00"}'
    assert pure["canonical_bytes"](value) == store.canonical_bytes(value)
    missing = "1a569e86e9c19989b9cac5c0923f557c94417bc2debeba61ee33d87b96c8fdad"
    assert store.application_digest(tmp_path) == pure["application_digest"](tmp_path) == missing
    document = {"schema": 1, "state": "sealed", "identity": store.identity_document(identity()),
        "generation_id": "00000000-0000-0000-0000-000000000001", "application_digest": missing,
        "payload": payload()}
    expected = "519c17f8e4f9e4e826acc01d02eff429e8f5863e30c2f23872d7c1d85d079bde"
    assert hashlib.sha256(store.canonical_bytes(document)).hexdigest() == expected
    document["content_hash"] = expected
    assert store.validate_generation_document(document).application_digest == missing
    pure["validate_generation_document"](document, expected_digest=missing)
    with closing(sqlite3.connect(tmp_path / "execution.sqlite3")) as db:
        db.execute("CREATE TABLE typed (n,b,i,t,r)")
        db.execute("PRAGMA user_version=7")
        db.execute("PRAGMA application_id=12")
        db.execute("INSERT INTO typed VALUES (?,?,?,?,?)", (None, b"\x00\xff", -7, "한글", 1.25))
        db.commit()
        typed = "d4cbf513a404b8f70b9cb94ebed622b3734f10ba44175330f72968e54ff5f5c3"
        assert store.application_digest(tmp_path) == pure["application_digest"](tmp_path) == typed
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("INSERT INTO typed VALUES (?,?,?,?,?)", (None, b"", 9, "text", -0.5))
        db.commit()
        before = store.application_digest(tmp_path)
        assert before == pure["application_digest"](tmp_path) != typed
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert store.application_digest(tmp_path) == pure["application_digest"](tmp_path) == before
        db.execute("CREATE INDEX typed_idx ON typed (i)")
        db.commit()
        assert store.application_digest(tmp_path) != before
    empty = tmp_path / "empty"
    empty.mkdir()
    with closing(sqlite3.connect(empty / "execution.sqlite3")):
        pass
    assert store.application_digest(empty) != missing


def test_copied_minimal_bundle_runs_isolated_without_source_or_site_packages(
    native_state: Path, tmp_path: Path,
) -> None:
    bundle, cwd = tmp_path / "bundle", tmp_path / "arbitrary"
    bundle.mkdir()
    cwd.mkdir()
    for name in ("state_snapshot.py", "history_snapshot_validation.py", "native_restart_format.py"):
        shutil.copyfile(TOOL.with_name(name), bundle / name)
    for arguments in (("snapshot", "--source", native_state, "--target", tmp_path / "backup",
                       "--writers-stopped", "--require-native"),
                      ("verify", "--source", tmp_path / "backup", "--require-native"),
                      ("restore", "--source", tmp_path / "backup", "--target", tmp_path / "out",
                       "--require-native")):
        result = subprocess.run([sys.executable, "-I", "-S", str(bundle / "state_snapshot.py"),
            *map(str, arguments)], cwd=cwd, capture_output=True, text=True, timeout=35, check=False)
        assert result.returncode == 0, result.stderr
    assert state_bytes(tmp_path / "out") == state_bytes(native_state)


def test_native_wal_digest_uses_disposable_scratch_and_checkpoint_is_stable(
    native_state: Path, tmp_path: Path,
) -> None:
    api = runpy.run_path(str(TOOL))
    native = api["_NATIVE_VALIDATOR"]
    original = native["application_digest"]
    calls: list[Path] = []

    def scratch_digest(path: Path) -> str:
        assert path != native_state and native_state not in path.parents
        calls.append(path)
        return str(original(path))

    native["application_digest"] = scratch_digest
    seals = store.NativeRestartStore(native_state)
    seals.mark_active(identity(), seals.load(identity()))
    with closing(sqlite3.connect(native_state / "execution.sqlite3")) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("INSERT INTO projections VALUES ('backup-fixture','DEMO','wal',1,'{}')")
        db.commit()
        seals.seal(identity(), payload())
        before = state_bytes(native_state)
        assert before["execution.sqlite3-wal"]
        assert api["main"](["snapshot", "--source", str(native_state), "--target",
            str(tmp_path / "backup"), "--writers-stopped", "--require-native"]) == 0
        assert calls and state_bytes(native_state) == before
        logical = store.application_digest(native_state)
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert store.application_digest(native_state) == logical
        assert seals.load(identity()) is not None
        assert snapshot(native_state, tmp_path / "checkpointed").returncode == 0


@pytest.mark.parametrize("name", ["native-restart.json-wal", "native-restart.json.tmp", "native-other.json"])
def test_unknown_native_variants_refused(native_state: Path, tmp_path: Path, name: str) -> None:
    (native_state / name).write_bytes(b"fixture")
    assert snapshot(native_state, tmp_path / "backup").returncode == 1
    assert not (tmp_path / "backup").exists()


def test_unsigned_native_artifact_does_not_authenticate_trader_identity(
    native_state: Path, tmp_path: Path,
) -> None:
    # A private-directory writer can replace both the declaration and its hash.
    # Portable checks deliberately cannot establish the real authenticated trader.
    rewrite(native_state / "native-restart.json",
        lambda document: document["identity"].update(trader_id="OTHER-001"))
    assert snapshot(native_state, tmp_path / "backup").returncode == 0
    with pytest.raises(store.NativeRestartError):
        store.NativeRestartStore(native_state).load(identity())
