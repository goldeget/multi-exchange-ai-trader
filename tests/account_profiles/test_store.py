"""Synthetic-only strict profile storage and initial-adoption publication tests."""

import copy
import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

UID = "12345678901234567890"
PARENT = "9876543210"
PROFILE = "83a05b72-f883-451a-9e58-639bd9909912"
EVIDENCE = "a24bd44b-422d-409c-8563-91458ed1eb18"
STAMP = "2026-11-03T00:00:00Z"
POLICY = "uid-anchored-adoption-v1"


@pytest.fixture
def profiles() -> ModuleType:
    if importlib.util.find_spec("multi_exchange_trader.account_profiles") is None:
        pytest.fail("Account profile store has not been implemented")
    return importlib.import_module("multi_exchange_trader.account_profiles")


def payload(*, complete: bool = False) -> dict[str, Any]:
    return {"schema": 1, "revision": 1, "selected_profile_id": PROFILE, "profiles": [{
        "profile_id": PROFILE, "exchange": "BYBIT", "environment": "DEMO", "uid": UID,
        "uid_evidence": {"basis": "OPERATOR_SELECTED", "evidence_id": EVIDENCE,
                         "confirmed_at": STAMP},
        "relationship": relationship() if complete else None,
        "last_observation": observation() if complete else None,
    }]}


def relationship() -> dict[str, Any]:
    return {"parent_uid": PARENT, "is_master": False,
            "basis": "PROVIDER_OBSERVED_OPERATOR_ADOPTED", "source": "BYBIT_DEMO_QUERY_API",
            "evidence_id": EVIDENCE, "confirmed_at": STAMP,
            "provider_observed_at": STAMP, "policy": POLICY}


def observation() -> dict[str, Any]:
    return {"source": "BYBIT_DEMO_QUERY_API", "parent_uid": PARENT, "is_master": False,
            "observed_at": STAMP, "server_observed_at": STAMP}


def write(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def store_for(profiles: ModuleType, tmp_path: Path) -> Any:
    return profiles.AccountProfileStore(tmp_path / "private" / "account-profiles.json")


def adopt(store: Any, snapshot: Any, *, guard: Any = lambda: None) -> Any:
    return store._adopt_initial(profile_id=snapshot.profile_id,
                                expected_uid=snapshot._uid,
                                expected_revision=snapshot.revision,
                                relationship=relationship(), last_observation=observation(),
                                guard=guard)


def test_absent_read_and_selector_do_not_create_or_probe(
    profiles: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_for(profiles, tmp_path)
    assert store.load() is None
    assert not store.path.parent.exists()
    monkeypatch.setattr(Path, "lstat", lambda *a, **k: pytest.fail("selector probed files"))
    monkeypatch.setattr(Path, "stat", lambda *a, **k: pytest.fail("selector probed files"))
    selected = profiles.default_account_profile_store({"TRADER_ACCOUNT_PROFILE_FILE": str(store.path)})
    assert selected.path == store.path
    assert profiles.default_account_profile_store({"TRADER_ACCOUNT_PROFILE_FILE": "off"}) is None
    base = {"LOCALAPPDATA": str(tmp_path), "HOME": str(tmp_path)}
    assert profiles.default_account_profile_store(base).path.name == "account-profiles.json"
    with pytest.raises(profiles.AccountProfileError, match="UNSAFE_PATH"):
        profiles.default_account_profile_store({"TRADER_ACCOUNT_PROFILE_FILE": "relative"})


def test_one_time_pending_registration_fresh_process_and_redaction(
    profiles: ModuleType, tmp_path: Path,
) -> None:
    store = store_for(profiles, tmp_path)
    result = store.register_uid(UID, expected_revision=None)
    snapshot = store.load()
    assert (result.schema, result.revision, result.state, result.basis) == (
        1, 1, "PENDING", "OPERATOR_SELECTED")
    assert snapshot.profile_id == result.profile_id
    assert snapshot._uid == UID
    with pytest.raises(profiles.AccountProfileError, match="PROFILE_PENDING"):
        snapshot.resolve_expectations(supported_policies=frozenset({POLICY}))
    for revision in (None, 1, True, 0):
        with pytest.raises(profiles.AccountProfileError, match="REVISION_CONFLICT"):
            store.register_uid(UID, expected_revision=revision)
    assert store.load().revision == 1
    assert UID not in repr(result) + repr(snapshot) + repr(store)
    assert str(tmp_path) not in repr(result) + repr(snapshot) + repr(store)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        snapshot.revision = 99
    script = ("import sys; from pathlib import Path; "
              "from multi_exchange_trader.account_profiles import AccountProfileStore; "
              "s=AccountProfileStore(Path(sys.argv[1])).load(); "
              "assert s._uid == sys.argv[2]; assert s.state == 'PENDING'; print(s)")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[2] / "src"),
               TRADER_ACCOUNT_PROFILE_FILE="off", TRADER_CREDENTIAL_FILE="off")
    run = subprocess.run([sys.executable, "-c", script, str(store.path), UID], env=env,
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    assert UID not in run.stdout + run.stderr


@pytest.mark.parametrize("uid", ["0", "01", "1" * 21, "１２", "1\x00", " 1", 1, True, None])
def test_registration_uid_validation_precedes_storage(
    profiles: ModuleType, tmp_path: Path, uid: object,
) -> None:
    store = store_for(profiles, tmp_path)
    with pytest.raises(profiles.AccountProfileError, match="INVALID_VALUES") as failure:
        store.register_uid(uid, expected_revision=None)
    assert str(failure.value) == "INVALID_VALUES"
    assert not store.path.parent.exists()


@pytest.mark.parametrize(("path", "value"), [
    (("schema",), True), (("schema",), 2), (("revision",), True), (("revision",), 0),
    (("revision",), 2**63), (("selected_profile_id",), EVIDENCE),
    (("selected_profile_id",), PROFILE.upper()), (("extra",), "secret"),
    (("profiles",), []), (("profiles",), [payload()["profiles"][0]] * 2),
    (("profiles", 0, "profile_id"), "83a05b72-f883-151a-9e58-639bd9909912"),
    (("profiles", 0, "exchange"), "OTHER"), (("profiles", 0, "environment"), "LIVE"),
    (("profiles", 0, "uid"), "01"), (("profiles", 0, "uid"), "1\u0085"),
    (("profiles", 0, "api_key"), "synthetic-key"),
    (("profiles", 0, "uid_evidence", "basis"), "OTHER"),
    (("profiles", 0, "uid_evidence", "confirmed_at"), "2026-11-03T00:00:00+00:00"),
    (("profiles", 0, "uid_evidence", "confirmed_at"), "2026-02-30T00:00:00Z"),
    (("profiles", 0, "uid_evidence", "confirmed_at"), "2026-11-03T00:00:00.1Z"),
    (("profiles", 0, "relationship", "parent_uid"), UID),
    (("profiles", 0, "relationship", "is_master"), 0),
    (("profiles", 0, "relationship", "policy"), "unknown-v1"),
    (("profiles", 0, "relationship", "source"), "BYBIT_UI"),
    (("profiles", 0, "relationship", "provider_observed_at"), None),
    (("profiles", 0, "relationship", "key_hash"), "synthetic-hash"),
    (("profiles", 0, "last_observation", "source"), "BYBIT_UI"),
    (("profiles", 0, "last_observation", "parent_uid"), "00"),
    (("profiles", 0, "last_observation", "is_master"), "false"),
    (("profiles", 0, "last_observation", "server_observed_at"), float("nan")),
])
def test_strict_schema_rejects_invalid_nested_fields(
    profiles: ModuleType, tmp_path: Path, path: tuple[Any, ...], value: object,
) -> None:
    data = payload(complete=True)
    target: Any = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = copy.deepcopy(value)
    store = store_for(profiles, tmp_path)
    write(store.path, data)
    with pytest.raises(profiles.AccountProfileError, match="INVALID_STORE") as failure:
        store.load()
    assert str(failure.value) == "INVALID_STORE"


@pytest.mark.parametrize("raw", [b"not-json", b"\xff", b"x" * 65537,
                                  b'{"schema":1,"schema":1}',
                                  b'{"schema":NaN}', b'{"schema":Infinity}'])
def test_raw_parser_limits(profiles: ModuleType, tmp_path: Path, raw: bytes) -> None:
    store = store_for(profiles, tmp_path)
    write(store.path, {})
    store.path.write_bytes(raw)
    with pytest.raises(profiles.AccountProfileError, match="INVALID_STORE"):
        store.load()


@pytest.mark.parametrize("section", ["root", "profile", "uid_evidence", "relationship",
                                     "last_observation"])
def test_every_required_field_is_required(profiles: ModuleType, tmp_path: Path,
                                          section: str) -> None:
    original = payload(complete=True)
    target = (original if section == "root" else original["profiles"][0] if section == "profile"
              else original["profiles"][0][section])
    for field in tuple(target):
        data = copy.deepcopy(original)
        selected = (data if section == "root" else data["profiles"][0] if section == "profile"
                    else data["profiles"][0][section])
        del selected[field]
        store = store_for(profiles, tmp_path)
        write(store.path, data)
        with pytest.raises(profiles.AccountProfileError, match="INVALID_STORE"):
            store.load()


def test_maximum_size_and_nested_duplicate_rules(profiles: ModuleType, tmp_path: Path) -> None:
    store = store_for(profiles, tmp_path)
    raw = json.dumps(payload()).encode()
    write(store.path, {})
    store.path.write_bytes(raw + b" " * (65536 - len(raw)))
    assert store.load().state == "PENDING"
    store.path.write_bytes(raw + b" " * (65537 - len(raw)))
    with pytest.raises(profiles.AccountProfileError, match="INVALID_STORE"):
        store.load()
    store.path.write_bytes(raw.replace(b'"basis": "OPERATOR_SELECTED"',
                                      b'"basis":"OPERATOR_SELECTED","basis":"OPERATOR_SELECTED"'))
    with pytest.raises(profiles.AccountProfileError, match="INVALID_STORE"):
        store.load()


def test_complete_provenance_is_explicit_and_observation_is_not_expectation(
    profiles: ModuleType, tmp_path: Path,
) -> None:
    store = store_for(profiles, tmp_path)
    data = payload(complete=True)
    data["profiles"][0]["last_observation"]["parent_uid"] = "0"
    write(store.path, data)
    snapshot = store.load()
    with pytest.raises(profiles.AccountProfileError, match="UNSUPPORTED_POLICY"):
        snapshot.resolve_expectations(supported_policies=frozenset({"independent-v1"}))
    expectations = snapshot.resolve_expectations(supported_policies=frozenset({POLICY}))
    assert (expectations._uid, expectations._parent_uid, expectations._is_master) == (
        UID, PARENT, False)
    assert expectations.basis == "PROVIDER_OBSERVED_OPERATOR_ADOPTED"
    assert UID not in repr(expectations) and PARENT not in repr(expectations)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        expectations._uid = "1"
    for source in ("BYBIT_UI", "BYBIT_SUPPORT", "PRIOR_VERIFIED_RECORD"):
        relation = data["profiles"][0]["relationship"]
        relation.update(basis="INDEPENDENT_CONFIRMED", policy="independent-v1", source=source,
                        provider_observed_at=None, parent_uid="0", is_master=False)
        write(store.path, data)
        assert store.load().resolve_expectations(
            supported_policies=frozenset({"independent-v1"}))._is_master is False


def test_guarded_adoption_rechecks_and_never_overwrites_complete(
    profiles: ModuleType, tmp_path: Path,
) -> None:
    store = store_for(profiles, tmp_path)
    store.register_uid(UID, expected_revision=None)
    snapshot = store.load()
    checks: list[int] = []
    adopted = adopt(store, snapshot, guard=lambda: checks.append(1))
    assert len(checks) >= 3
    assert adopted.state == "COMPLETE" and adopted.revision == 2
    assert snapshot.state == "PENDING"
    assert store.load().resolve_expectations(
        supported_policies=frozenset({POLICY}))._parent_uid == PARENT
    with pytest.raises(profiles.AccountProfileError, match="REVISION_CONFLICT"):
        adopt(store, snapshot)
    with pytest.raises(profiles.AccountProfileError, match="PROFILE_COMPLETE"):
        adopt(store, store.load())
    assert store.load().revision == 2


@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_failed_guard_cannot_publish(
    profiles: ModuleType, tmp_path: Path, fail_at: int,
) -> None:
    store = store_for(profiles, tmp_path)
    store.register_uid(UID, expected_revision=None)
    snapshot = store.load()
    before = store.path.read_bytes()
    calls = 0

    def guard() -> None:
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise profiles.AccountProfileError("INVALID_VALUES")

    with pytest.raises(profiles.AccountProfileError):
        adopt(store, snapshot, guard=guard)
    assert store.path.read_bytes() == before


def test_wrong_target_and_unapproved_relationship_cannot_publish(
    profiles: ModuleType, tmp_path: Path,
) -> None:
    store = store_for(profiles, tmp_path)
    store.register_uid(UID, expected_revision=None)
    with pytest.raises(profiles.AccountProfileError, match="REVISION_CONFLICT"):
        store._adopt_initial(profile_id=PROFILE, expected_revision=1,
                             expected_uid=UID,
                             relationship=relationship(), last_observation=observation(),
                             guard=lambda: None)
    relation = relationship()
    relation.update(basis="INDEPENDENT_CONFIRMED", policy="independent-v1", source="BYBIT_UI",
                    provider_observed_at=None)
    with pytest.raises(profiles.AccountProfileError, match="INVALID_VALUES"):
        store._adopt_initial(profile_id=store.load().profile_id, expected_revision=1,
                             expected_uid=UID,
                             relationship=relation, last_observation=observation(),
                             guard=lambda: None)
    assert store.load().state == "PENDING"


def test_uid_changed_without_revision_does_not_adopt_other_account(
    profiles: ModuleType, tmp_path: Path,
) -> None:
    store = store_for(profiles, tmp_path)
    store.register_uid(UID, expected_revision=None)
    snapshot = store.load()
    data = json.loads(store.path.read_bytes())
    data["profiles"][0]["uid"] = "654321"
    write(store.path, data)
    with pytest.raises(profiles.AccountProfileError, match="REVISION_CONFLICT"):
        adopt(store, snapshot)
    assert store.load().state == "PENDING"


def test_snapshot_defensively_copies_all_nested_mappings(profiles: ModuleType) -> None:
    from types import MappingProxyType

    data = payload(complete=True)["profiles"][0]
    backing = data["relationship"]
    data["relationship"] = MappingProxyType(backing)
    snapshot = profiles.AccountProfileSnapshot(1, PROFILE, data)
    backing["parent_uid"] = "0"
    assert snapshot.resolve_expectations(supported_policies=frozenset({POLICY}))._parent_uid == PARENT
    for nested in ("uid_evidence", "relationship", "last_observation"):
        with pytest.raises(TypeError):
            snapshot._profile[nested]["extra"] = "value"


def test_guard_runtime_error_has_no_private_diagnostics(
    profiles: ModuleType, tmp_path: Path,
) -> None:
    store = store_for(profiles, tmp_path)
    store.register_uid(UID, expected_revision=None)

    def guard() -> None:
        raise RuntimeError("private diagnostic " + UID)

    with pytest.raises(profiles.AccountProfileError, match="PERSISTENCE_FAILED") as error:
        adopt(store, store.load(), guard=guard)
    assert UID not in repr(error.value)
    assert store.load().state == "PENDING"


def test_confirmation_failure_cannot_report_success(
    profiles: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_for(profiles, tmp_path)
    store.register_uid(UID, expected_revision=None)
    snapshot = store.load()
    original = store.load
    calls = 0

    def load() -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise profiles.AccountProfileError("INVALID_STORE")
        return original()

    monkeypatch.setattr(store, "load", load)
    with pytest.raises(profiles.AccountProfileError, match="PERSISTENCE_FAILED"):
        adopt(store, snapshot)
    assert original().state == "COMPLETE"


def test_concurrent_registration_has_exactly_one_winner(profiles: ModuleType, tmp_path: Path) -> None:
    store = store_for(profiles, tmp_path)

    def register() -> str:
        try:
            store.register_uid(UID, expected_revision=None)
            return "ok"
        except profiles.AccountProfileError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: register(), range(2))) == ["REVISION_CONFLICT", "ok"]
    assert store.load().revision == 1
    assert (store.path.parent / ".account-profiles.lock").is_file()
    assert not (store.path.parent / ".credentials.lock").exists()


def test_replace_failure_is_safe_and_does_not_retry(
    profiles: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_for(profiles, tmp_path)
    store.register_uid(UID, expected_revision=None)
    snapshot = store.load()
    before = store.path.read_bytes()
    original_replace = os.replace
    calls = 0

    def ambiguous_replace(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        original_replace(source, target)
        raise OSError("sensitive-path-and-input")

    monkeypatch.setattr(os, "replace", ambiguous_replace)
    with pytest.raises(profiles.AccountProfileError, match="PERSISTENCE_FAILED") as error:
        adopt(store, snapshot)
    assert "sensitive" not in repr(error.value)
    assert calls == 1
    assert store.path.read_bytes() != before
    assert store.load().state == "COMPLETE"
    assert not list(store.path.parent.glob(".account-profiles-*"))


def test_file_fsync_failure_leaves_pending_and_cleans_temporary(
    profiles: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_for(profiles, tmp_path)
    store.register_uid(UID, expected_revision=None)
    snapshot = store.load()
    before = store.path.read_bytes()

    def fail(descriptor: int) -> None:
        raise OSError("private-file-path")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(profiles.AccountProfileError, match="PERSISTENCE_FAILED"):
        adopt(store, snapshot)
    assert store.path.read_bytes() == before
    assert not list(store.path.parent.glob(".account-profiles-*"))


def test_target_replacement_before_publication_is_detected(
    profiles: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_for(profiles, tmp_path)
    store.register_uid(UID, expected_revision=None)
    snapshot = store.load()
    replacement = store.path.with_name("replacement")
    replacement.write_bytes(store.path.read_bytes())
    replacement.chmod(0o600)
    real_fsync = os.fsync

    def swap(descriptor: int) -> None:
        real_fsync(descriptor)
        if replacement.exists():
            os.replace(replacement, store.path)

    monkeypatch.setattr(os, "fsync", swap)
    with pytest.raises(profiles.AccountProfileError, match="REVISION_CONFLICT"):
        adopt(store, snapshot)
    assert store.load().state == "PENDING"


def test_temporary_open_identity_is_checked_before_payload_write(
    profiles: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_for(profiles, tmp_path)
    real_mkstemp = tempfile.mkstemp
    actual: list[Path] = []

    def mismatch(*args: Any, **kwargs: Any) -> tuple[int, str]:
        descriptor, name = real_mkstemp(*args, **kwargs)
        actual.append(Path(name))
        return descriptor, name + ".absent"

    monkeypatch.setattr(tempfile, "mkstemp", mismatch)
    with pytest.raises(profiles.AccountProfileError, match="UNSAFE_PATH"):
        store.register_uid(UID, expected_revision=None)
    assert actual[0].read_bytes() == b""
    assert store.load() is None


def test_replaced_temporary_cannot_publish(
    profiles: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_for(profiles, tmp_path)
    store.register_uid(UID, expected_revision=None)
    snapshot = store.load()
    real_identity = profiles.current_identity
    replacement = store.path.with_name("replacement-temp")
    replacement.write_bytes(b"corrupt")
    replacement.chmod(0o600)

    def swap(path: Path) -> Any:
        temporaries = list(store.path.parent.glob(".account-profiles-*"))
        if path == store.path and temporaries and replacement.exists():
            os.replace(replacement, temporaries[0])
        return real_identity(path)

    monkeypatch.setattr(profiles, "current_identity", swap)
    with pytest.raises(profiles.AccountProfileError, match="UNSAFE_PATH"):
        adopt(store, snapshot)
    assert store.load().state == "PENDING"


def test_replaced_profile_lock_cannot_publish(
    profiles: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_for(profiles, tmp_path)
    store.register_uid(UID, expected_revision=None)
    snapshot = store.load()
    replacement = store.path.with_name("replacement-lock")
    replacement.write_bytes(b"")
    replacement.chmod(0o600)
    changed = False
    real_fsync = os.fsync
    real_lstat = Path.lstat

    def lstat(path: Path) -> os.stat_result:
        if changed and path == store.path.parent / ".account-profiles.lock":
            return real_lstat(replacement)
        return real_lstat(path)

    def fsync(descriptor: int) -> None:
        nonlocal changed
        real_fsync(descriptor)
        changed = True

    # Windows prevents replacing an open lock; inject changed identity at its
    # lstat boundary to exercise the rejection, without claiming real replacement.
    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(profiles.AccountProfileError, match="UNSAFE_PATH"):
        adopt(store, snapshot)
    assert store.load().state == "PENDING"


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory fsync gate runs on Linux")
def test_directory_fsync_failure_is_ambiguous_and_never_retried(
    profiles: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stat

    store = store_for(profiles, tmp_path)
    store.register_uid(UID, expected_revision=None)
    snapshot = store.load()
    real_fsync = os.fsync

    def fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("private-directory-path")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(profiles.AccountProfileError, match="PERSISTENCE_FAILED"):
        adopt(store, snapshot)
    assert store.load().state == "COMPLETE"
    assert store.load().revision == 2


def test_cross_process_profile_lock_wait_is_fixed_and_bounded(
    profiles: ModuleType, tmp_path: Path,
) -> None:
    store = store_for(profiles, tmp_path)
    store.register_uid(UID, expected_revision=None)
    script = ("from pathlib import Path; import sys,time; "
              "from multi_exchange_trader._credential_files import private_lock; "
              "lock=private_lock(Path(sys.argv[1]),lock_name='.account-profiles.lock'); "
              "lock.__enter__(); print('locked',flush=True); time.sleep(9)")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[2] / "src"),
               TRADER_ACCOUNT_PROFILE_FILE="off", TRADER_CREDENTIAL_FILE="off")
    child = subprocess.Popen([sys.executable, "-c", script, str(store.path)], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        start = time.monotonic()
        with pytest.raises(profiles.AccountProfileError, match="LOCK_TIMEOUT"):
            adopt(store, store.load())
        elapsed = time.monotonic() - start
        assert 4.8 <= elapsed < 7.0
        assert (store.path.parent / ".account-profiles.lock").exists()
        assert store.load().state == "PENDING"
    finally:
        child.kill()
        child.communicate(timeout=5)


def test_concurrent_adoption_processes_never_overwrite(
    profiles: ModuleType, tmp_path: Path,
) -> None:
    store = store_for(profiles, tmp_path)
    store.register_uid(UID, expected_revision=None)
    script = """import json,sys
from pathlib import Path
from multi_exchange_trader.account_profiles import AccountProfileStore,AccountProfileError
store=AccountProfileStore(Path(sys.argv[1]))
try:
    store._adopt_initial(profile_id=sys.argv[2],expected_uid=sys.argv[3],expected_revision=1,
        relationship=json.loads(sys.argv[4]),last_observation=json.loads(sys.argv[5]),guard=lambda:None)
    print('ok')
except AccountProfileError as error:
    print(error.code)
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[2] / "src"),
               TRADER_ACCOUNT_PROFILE_FILE="off", TRADER_CREDENTIAL_FILE="off")
    children = [subprocess.Popen([sys.executable, "-c", script, str(store.path),
                store.load().profile_id, UID, json.dumps(relationship()), json.dumps(observation())],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        outputs = [child.communicate(timeout=10) for child in children]
        assert all(child.returncode == 0 for child in children)
        assert sorted(out.strip() for out, _ in outputs) == ["REVISION_CONFLICT", "ok"]
        assert all(not error for _, error in outputs)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
    assert store.load().revision == 2


@pytest.mark.parametrize("kind", ["hardlink", "directory", "lock-hardlink"])
def test_unsafe_actual_files_rejected(profiles: ModuleType, tmp_path: Path, kind: str) -> None:
    store = store_for(profiles, tmp_path)
    store.register_uid(UID, expected_revision=None)
    if kind == "hardlink":
        os.link(store.path, tmp_path / "alias")
        action = store.load
    elif kind == "directory":
        store.path.unlink()
        store.path.mkdir()
        action = store.load
    else:
        os.link(store.path.parent / ".account-profiles.lock", tmp_path / "alias")
        action = lambda: adopt(store, store.load())
    with pytest.raises(profiles.AccountProfileError, match="UNSAFE_PATH"):
        action()


def test_relative_parent_traversal_and_symlink_rejected(
    profiles: ModuleType, tmp_path: Path,
) -> None:
    for path in (Path("relative"), tmp_path / ".." / "outside"):
        with pytest.raises(profiles.AccountProfileError, match="UNSAFE_PATH"):
            profiles.AccountProfileStore(path).load()
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("OS account cannot create symbolic links; hardlink/nonregular gates run separately")
    with pytest.raises(profiles.AccountProfileError, match="UNSAFE_PATH"):
        profiles.AccountProfileStore(link / "profile").load()


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows reparse directory junction")
def test_windows_junction_is_rejected(profiles: ModuleType, tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    junction = tmp_path / "junction"
    run = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(real)],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, "synthetic junction creation failed"
    with pytest.raises(profiles.AccountProfileError, match="UNSAFE_PATH"):
        profiles.AccountProfileStore(junction / "profile").load()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions and owner gate run on Linux")
def test_posix_permissions_and_owner(profiles: ModuleType, tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    store = store_for(profiles, tmp_path)
    store.register_uid(UID, expected_revision=None)
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700
    store.path.chmod(0o644)
    with pytest.raises(profiles.AccountProfileError, match="UNSAFE_PATH"):
        store.load()
    store.path.chmod(0o600)
    monkeypatch.setattr(os, "getuid", lambda: store.path.stat().st_uid + 1)
    with pytest.raises(profiles.AccountProfileError, match="UNSAFE_PATH"):
        store.load()
