"""Synthetic-only persistence, publication and confidentiality acceptance tests."""

import base64
import importlib
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

PAIR = {"BYBIT_DEMO_API_KEY": "synthetic-demo-key", "BYBIT_DEMO_API_SECRET": "synthetic-secret"}


@pytest.fixture
def credentials() -> ModuleType:
    if importlib.util.find_spec("multi_exchange_trader.credentials") is None:
        pytest.fail("Managed credential persistence is not implemented")
    return importlib.import_module("multi_exchange_trader.credentials")


class IdentityProtector:
    def protect(self, value: bytes) -> bytes:
        return value

    def unprotect(self, value: bytes) -> bytes:
        return value


def private_store(credentials: ModuleType, tmp_path: Path) -> Any:
    return credentials.CredentialStore(tmp_path / "private" / "credentials.bin")


def write_payload(path: Path, raw: bytes) -> None:
    path.parent.mkdir(mode=0o700, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "protection": "test-injected",
                                "data": base64.b64encode(raw).decode("ascii")}), encoding="utf-8")
    path.chmod(0o600)


def test_read_does_not_create_store(credentials: ModuleType, tmp_path: Path) -> None:
    store = private_store(credentials, tmp_path)
    assert store.load() is None
    assert not (tmp_path / "private").exists()


def test_corrupt_store_never_returns_secret(credentials: ModuleType, tmp_path: Path) -> None:
    store = private_store(credentials, tmp_path)
    store.register(PAIR, expected_revision=None)
    store.path.write_bytes(b'{"secret":"synthetic-secret"}')
    with pytest.raises(credentials.CredentialStoreError) as failure:
        store.load()
    assert str(failure.value) == "INVALID_STORE"
    assert "synthetic" not in repr(failure.value)


def test_expected_revision_prevents_lost_update(credentials: ModuleType, tmp_path: Path) -> None:
    store = private_store(credentials, tmp_path)
    result = store.register(PAIR, expected_revision=None)
    assert result.revision == 1
    with pytest.raises(credentials.CredentialStoreError, match="REVISION_CONFLICT"):
        store.register({"ANTHROPIC_API_KEY": "synthetic-anthropic"}, expected_revision=None)
    with pytest.raises(credentials.CredentialStoreError, match="REVISION_CONFLICT"):
        store.register(PAIR, expected_revision=0)
    assert store.register({"ANTHROPIC_API_KEY": "synthetic-anthropic"},
                          expected_revision=1).revision == 2
    assert store.load().bybit_pair("demo") == ("synthetic-demo-key", "synthetic-secret")


def test_same_demo_live_pair_rejected(credentials: ModuleType, tmp_path: Path) -> None:
    store = private_store(credentials, tmp_path)
    store.register(PAIR, expected_revision=None)
    with pytest.raises(credentials.CredentialStoreError, match="INVALID_VALUES"):
        store.register({"BYBIT_API_KEY": "other-key", "BYBIT_API_SECRET": "synthetic-secret"},
                       expected_revision=1)
    assert store.load().revision == 1


@pytest.mark.parametrize("name", ["BYBIT_API_KEY", "BYBIT_API_SECRET", "BYBIT_DEMO_API_KEY",
                                  "BYBIT_DEMO_API_SECRET"])
def test_namespace_half_pair_rejected(credentials: ModuleType, tmp_path: Path, name: str) -> None:
    with pytest.raises(credentials.CredentialStoreError, match="INVALID_VALUES"):
        private_store(credentials, tmp_path).register({name: "synthetic"}, expected_revision=None)
    assert not (tmp_path / "private" / "credentials.bin").exists()


def test_snapshot_is_immutable_and_redacted(credentials: ModuleType, tmp_path: Path) -> None:
    store = private_store(credentials, tmp_path)
    result = store.register(PAIR, expected_revision=None)
    snapshot = store.load()
    assert snapshot.namespaces == ("BYBIT_DEMO",)
    assert snapshot.schema == result.schema == 1
    assert "synthetic" not in str(snapshot) + repr(result)
    assert str(tmp_path) not in repr(store) + repr(snapshot) + repr(result)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        snapshot.revision = 9
    with pytest.raises(TypeError):
        asdict(snapshot)
    store.register({"BYBIT_DEMO_API_KEY": "rotated", "BYBIT_DEMO_API_SECRET": "rotated-secret"},
                   expected_revision=1)
    assert snapshot.bybit_pair("demo") == ("synthetic-demo-key", "synthetic-secret")


@pytest.mark.parametrize("raw", [
    b'{"schema":1,"schema":1,"revision":1,"values":{}}',
    b'{"schema":2,"revision":1,"values":{}}',
    b'{"schema":true,"revision":1,"values":{}}',
    b'{"schema":1,"revision":true,"values":{}}',
    b'{"schema":1,"revision":0,"values":{}}',
    b'{"schema":1,"revision":1,"values":{},"other":"x"}',
    b'{"schema":1,"revision":1,"values":{"AI_PROVIDER":[]}}',
    b'{"schema":1,"revision":1,"values":{"AI_PROVIDER":"OPENROUTER\\u0000"}}',
    b'{"schema":1,"revision":1,"values":{"LIVE_TRADING_ENABLED":"true"}}',
    b'{"schema":1,"revision":1,"values":{"AI_PROVIDER":"' + b'x' * 17000 + b'"}}',
    b'not-json',
])
def test_strict_payload_validation(credentials: ModuleType, tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "private" / "store"
    write_payload(path, raw)
    store = credentials.CredentialStore(path, protector=IdentityProtector())
    with pytest.raises(credentials.CredentialStoreError, match="INVALID_STORE"):
        store.load()


@pytest.mark.parametrize("raw", [b'{}', b'{"schema":1,"schema":1}', b'x' * 65537,
                                  b'{"schema":1,"protection":"test-injected","data":"!"}'])
def test_strict_envelope_validation(credentials: ModuleType, tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "private" / "store"
    path.parent.mkdir(mode=0o700)
    path.write_bytes(raw)
    path.chmod(0o600)
    with pytest.raises(credentials.CredentialStoreError, match="INVALID_STORE"):
        credentials.CredentialStore(path).load()


def test_test_protector_cannot_be_silently_used_in_production(
    credentials: ModuleType, tmp_path: Path,
) -> None:
    path = tmp_path / "private" / "store"
    credentials.CredentialStore(path, protector=IdentityProtector()).register(
        PAIR, expected_revision=None)
    with pytest.raises(credentials.CredentialStoreError, match="INVALID_STORE"):
        credentials.CredentialStore(path).load()


@pytest.mark.parametrize("values", [
    {"AI_PROVIDER": "OTHER"}, {"ANTHROPIC_API_KEY": "bad key"},
    {"OPENROUTER_API_KEY": "bad-key"}, {"ANTHROPIC_MODEL": "not-a-model"},
    {"OPENROUTER_MODEL": "bad/model\t"}, {"AI_PROVIDER": "OPENROUTER\x7f"},
    {"BYBIT_API_KEY": "synthetic\u0085", "BYBIT_API_SECRET": "synthetic-secret"},
    {"BYBIT_API_KEY": "synthetic\u202e", "BYBIT_API_SECRET": "synthetic-secret"},
    {"LIVE_TRADING_ENABLED": "true"}, {"AI_PROVIDER": "x" * 17000},
    {"ANTHROPIC_API_KEY": 42},
])
def test_registration_rejects_invalid_values(
    credentials: ModuleType, tmp_path: Path, values: dict[str, Any],
) -> None:
    with pytest.raises(credentials.CredentialStoreError, match="INVALID_VALUES"):
        private_store(credentials, tmp_path).register(values, expected_revision=None)


@pytest.mark.parametrize("selector", ["ANTHROPIC", "OPENROUTER"])
def test_anthropic_model_cannot_contain_other_provider_credential(
    credentials: ModuleType, tmp_path: Path, selector: str,
) -> None:
    store = private_store(credentials, tmp_path)
    values = {"AI_PROVIDER": selector, "ANTHROPIC_API_KEY": "synthetic-anthropic",
              "ANTHROPIC_MODEL": "claude-sk-or-v1-synthetic1234",
              "OPENROUTER_API_KEY": "sk-or-v1-synthetic1234", "OPENROUTER_MODEL": "vendor/model"}
    with pytest.raises(credentials.CredentialStoreError, match="INVALID_VALUES"):
        store.register(values, expected_revision=None)
    assert not store.path.exists()


@pytest.mark.parametrize("raw", ["relative/store", "../store"])
def test_relative_and_parent_paths_rejected(credentials: ModuleType, raw: str) -> None:
    with pytest.raises(credentials.CredentialStoreError, match="UNSAFE_PATH"):
        credentials.CredentialStore(Path(raw)).load()


def test_parent_traversal_rejected(credentials: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(credentials.CredentialStoreError, match="UNSAFE_PATH"):
        credentials.CredentialStore(tmp_path / "private" / ".." / "store").load()


def test_hardlink_target_rejected(credentials: ModuleType, tmp_path: Path) -> None:
    store = private_store(credentials, tmp_path)
    store.register(PAIR, expected_revision=None)
    os.link(store.path, store.path.with_name("copy"))
    with pytest.raises(credentials.CredentialStoreError, match="UNSAFE_PATH"):
        store.load()


def test_nonregular_target_rejected(credentials: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "private" / "store"
    path.mkdir(parents=True, mode=0o700)
    with pytest.raises(credentials.CredentialStoreError, match="UNSAFE_PATH"):
        credentials.CredentialStore(path).load()


def test_parent_link_rejected(credentials: ModuleType, tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "linked"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(real, target_is_directory=True)
    with pytest.raises(credentials.CredentialStoreError, match="UNSAFE_PATH"):
        credentials.CredentialStore(link / "store").load()


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner/mode acceptance runs on Linux")
def test_posix_private_modes(credentials: ModuleType, tmp_path: Path) -> None:
    store = private_store(credentials, tmp_path)
    store.register(PAIR, expected_revision=None)
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700
    store.path.chmod(0o644)
    with pytest.raises(credentials.CredentialStoreError, match="UNSAFE_PATH"):
        store.load()
    store.path.chmod(0o600)
    store.path.parent.chmod(0o755)
    with pytest.raises(credentials.CredentialStoreError, match="UNSAFE_PATH"):
        store.load()


def test_failed_replace_retains_previous_revision(
    credentials: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = private_store(credentials, tmp_path)
    store.register(PAIR, expected_revision=None)

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic-secret must not escape")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(credentials.CredentialStoreError, match="PERSISTENCE_FAILED"):
        store.register({"ANTHROPIC_API_KEY": "synthetic-anthropic"}, expected_revision=1)
    assert store.load().revision == 1
    assert sorted(p.name for p in store.path.parent.iterdir()) == [".credentials.lock", "credentials.bin"]


def test_failed_fsync_never_reports_success(
    credentials: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = private_store(credentials, tmp_path)

    def fail(*args: object) -> None:
        raise OSError("synthetic-secret")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(credentials.CredentialStoreError, match="PERSISTENCE_FAILED"):
        store.register(PAIR, expected_revision=None)
    assert not store.path.exists()


def test_concurrent_revision_registration(credentials: ModuleType, tmp_path: Path) -> None:
    store = private_store(credentials, tmp_path)
    store.register(PAIR, expected_revision=None)

    def update(index: int) -> str:
        try:
            store.register({"ANTHROPIC_API_KEY": f"synthetic-key-{index}"}, expected_revision=1)
            return "ok"
        except credentials.CredentialStoreError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=4) as executor:
        outcomes = list(executor.map(update, range(4)))
    assert outcomes.count("ok") == 1
    assert outcomes.count("REVISION_CONFLICT") == 3
    assert store.load().revision == 2


def test_subprocess_registration_reuses_saved_revision(credentials: ModuleType, tmp_path: Path) -> None:
    store = private_store(credentials, tmp_path)
    store.register(PAIR, expected_revision=None)
    script = ("from pathlib import Path; from multi_exchange_trader.credentials import CredentialStore; "
              "import sys; s=CredentialStore(Path(sys.argv[1])); "
              "s.register({'ANTHROPIC_API_KEY':'synthetic-child'}, expected_revision=1); "
              "assert s.load().bybit_pair('demo') == ('synthetic-demo-key','synthetic-secret')")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[2] / "src"))
    completed = subprocess.run([sys.executable, "-c", script, str(store.path)],
                               capture_output=True, text=True, check=False, env=env)
    assert completed.returncode == 0, completed.stderr
    assert store.load().revision == 2


def test_concurrent_subprocess_revisions_do_not_overwrite(
    credentials: ModuleType, tmp_path: Path,
) -> None:
    store = private_store(credentials, tmp_path)
    store.register(PAIR, expected_revision=None)
    script = """from pathlib import Path
import sys
from multi_exchange_trader.credentials import CredentialStore, CredentialStoreError
try:
    CredentialStore(Path(sys.argv[1])).register(
        {'ANTHROPIC_API_KEY': 'synthetic-child-' + sys.argv[2]}, expected_revision=1)
    print('ok')
except CredentialStoreError as exc:
    print(str(exc))
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[2] / "src"))
    children = [subprocess.Popen([sys.executable, "-c", script, str(store.path), str(index)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                for index in range(2)]
    try:
        outputs = [child.communicate(timeout=10) for child in children]
        assert all(child.returncode == 0 for child in children)
        assert sorted(output.strip() for output, _ in outputs) == ["REVISION_CONFLICT", "ok"]
        assert all(not error for _, error in outputs)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
    assert store.load().revision == 2


def test_hardlinked_lock_is_not_used(credentials: ModuleType, tmp_path: Path) -> None:
    store = private_store(credentials, tmp_path)
    store.register(PAIR, expected_revision=None)
    os.link(store.path.parent / ".credentials.lock", tmp_path / "linked-lock")
    with pytest.raises(credentials.CredentialStoreError, match="UNSAFE_PATH"):
        store.register({"ANTHROPIC_API_KEY": "synthetic-anthropic"}, expected_revision=1)
    assert store.load().revision == 1


def test_target_replacement_during_write_is_detected(
    credentials: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = private_store(credentials, tmp_path)
    store.register(PAIR, expected_revision=None)
    original_fsync = os.fsync
    replacement = store.path.with_name("replacement")
    replacement.write_bytes(store.path.read_bytes())
    replacement.chmod(0o600)

    def replace_during_flush(descriptor: int) -> None:
        original_fsync(descriptor)
        if replacement.exists():
            os.replace(replacement, store.path)

    monkeypatch.setattr(os, "fsync", replace_during_flush)
    with pytest.raises(credentials.CredentialStoreError, match="REVISION_CONFLICT"):
        store.register({"ANTHROPIC_API_KEY": "synthetic-anthropic"}, expected_revision=1)
    assert store.load().revision == 1


def test_replaced_tempfile_cannot_destroy_previous_revision(
    credentials: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = private_store(credentials, tmp_path)
    store.register(PAIR, expected_revision=None)
    original_identity = credentials.current_identity
    replacement = store.path.with_name("replacement")
    replacement.write_bytes(b"broken-store")
    replacement.chmod(0o600)

    def replace_closed_tempfile(path: Path) -> object:
        candidates = list(store.path.parent.glob(".credentials-*"))
        if path == store.path and candidates and replacement.exists():
            os.replace(replacement, candidates[0])
        return original_identity(path)

    monkeypatch.setattr(credentials, "current_identity", replace_closed_tempfile)
    with pytest.raises(credentials.CredentialStoreError):
        store.register({"ANTHROPIC_API_KEY": "synthetic-anthropic"}, expected_revision=1)
    assert store.load().revision == 1


def test_tempfile_path_must_match_open_file_before_secret_write(
    credentials: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = private_store(credentials, tmp_path)
    original_mkstemp = tempfile.mkstemp
    actual: list[Path] = []

    def mismatched_tempfile(*args: Any, **kwargs: Any) -> tuple[int, str]:
        descriptor, name = original_mkstemp(*args, **kwargs)
        actual.append(Path(name))
        return descriptor, str(Path(name).with_suffix(".missing"))

    monkeypatch.setattr(tempfile, "mkstemp", mismatched_tempfile)
    with pytest.raises(credentials.CredentialStoreError):
        store.register(PAIR, expected_revision=None)
    assert actual[0].read_bytes() == b""


def test_lock_replacement_during_write_cannot_publish(
    credentials: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = private_store(credentials, tmp_path)
    store.register(PAIR, expected_revision=None)
    original_fsync = os.fsync
    replacement = store.path.with_name("replacement-lock")
    replacement.write_bytes(b"")
    replacement.chmod(0o600)

    replaced = False
    original_lstat = Path.lstat

    def changed_lock_identity(path: Path) -> os.stat_result:
        if replaced and path == store.path.parent / ".credentials.lock":
            return original_lstat(replacement)
        return original_lstat(path)

    def replace_during_flush(descriptor: int) -> None:
        nonlocal replaced
        original_fsync(descriptor)
        replaced = True

    # Windows prevents replacing an open lock. Inject the changed lstat identity
    # at the filesystem boundary so this also exercises the POSIX replacement case.
    monkeypatch.setattr(Path, "lstat", changed_lock_identity)
    monkeypatch.setattr(os, "fsync", replace_during_flush)
    with pytest.raises(credentials.CredentialStoreError):
        store.register({"ANTHROPIC_API_KEY": "synthetic-anthropic"}, expected_revision=1)
    assert store.load().revision == 1


def test_process_lock_has_bounded_wait_and_is_never_unlinked(
    credentials: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = private_store(credentials, tmp_path)
    store.register(PAIR, expected_revision=None)
    script = ("from pathlib import Path; import sys, time; "
              "from multi_exchange_trader._credential_files import private_lock; "
              "lock=private_lock(Path(sys.argv[1])); lock.__enter__(); "
              "print('locked', flush=True); time.sleep(2); lock.__exit__(None,None,None)")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[2] / "src"))
    with subprocess.Popen([sys.executable, "-c", script, str(store.path)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env) as child:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        files = importlib.import_module("multi_exchange_trader._credential_files")
        monkeypatch.setattr(files, "LOCK_TIMEOUT_SECONDS", 0.05)
        with pytest.raises(credentials.CredentialStoreError, match="LOCK_TIMEOUT"):
            store.register({"ANTHROPIC_API_KEY": "synthetic-anthropic"}, expected_revision=1)
        assert (store.path.parent / ".credentials.lock").exists()
        child.communicate(timeout=5)
        assert child.returncode == 0
    assert store.load().revision == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner acceptance runs on Linux")
def test_posix_wrong_owner_rejected(
    credentials: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = private_store(credentials, tmp_path)
    store.register(PAIR, expected_revision=None)
    uid = store.path.stat().st_uid
    monkeypatch.setattr(os, "getuid", lambda: uid + 1)
    with pytest.raises(credentials.CredentialStoreError, match="UNSAFE_PATH"):
        store.load()


@pytest.mark.skipif(os.name != "nt", reason="Actual current-user Windows DPAPI acceptance")
def test_real_windows_dpapi_roundtrip(credentials: ModuleType, tmp_path: Path) -> None:
    store = private_store(credentials, tmp_path)
    store.register(PAIR, expected_revision=None)
    raw = store.path.read_bytes()
    assert b"synthetic" not in raw
    assert b"windows-dpapi-user" in raw
    fresh = credentials.CredentialStore(store.path)
    assert fresh.load().bybit_pair("demo") == ("synthetic-demo-key", "synthetic-secret")


def test_default_selector_and_off(credentials: ModuleType, tmp_path: Path) -> None:
    assert credentials.default_credential_store({"TRADER_CREDENTIAL_FILE": "off"}) is None
    explicit = credentials.default_credential_store({"TRADER_CREDENTIAL_FILE": str(tmp_path / "s")})
    assert explicit.path == tmp_path / "s"
    env = {"LOCALAPPDATA": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path), "HOME": str(tmp_path)}
    default = credentials.default_credential_store(env)
    assert default.path.is_absolute()
    assert not list(tmp_path.iterdir())
