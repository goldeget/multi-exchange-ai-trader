"""Real temporary files with explicitly UNQUALIFIED simulated POSIX metadata."""

import argparse
import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_policy_sections import policy_fixture

from multi_exchange_trader.api import create_app
from multi_exchange_trader.execution import runtime_policy as rp
from multi_exchange_trader.execution import worker_cli
from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook


class UnqualifiedPosixMetadata:
    """Keep real file/link/dev/ino/FD behavior; simulate only POSIX uid/mode."""

    def __init__(self) -> None:
        self.descriptors: dict[int, Path] = {}
        self.overrides: dict[Path, tuple[int, int]] = {}
        self.link_counts: dict[Path, int] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(os, name)

    def metadata(self, value: os.stat_result, path: Path) -> os.stat_result:
        fields = list(value)
        fields[0], fields[4] = self.overrides.get(
            path, (stat.S_IFMT(value.st_mode) | 0o700, 0)
        )
        fields[3] = self.link_counts.get(path, value.st_nlink)
        return os.stat_result(fields)

    def lstat(self, path: str | Path) -> os.stat_result:
        return self.metadata(os.lstat(path), Path(path))

    def fstat(self, descriptor: int) -> os.stat_result:
        return self.metadata(os.fstat(descriptor), self.descriptors[descriptor])

    def open(self, path: str | Path, flags: int, mode: int = 0o777) -> int:
        descriptor = os.open(path, flags, mode)
        self.descriptors[descriptor] = Path(path)
        return descriptor

    def close(self, descriptor: int) -> None:
        os.close(descriptor)
        self.descriptors.pop(descriptor)


@dataclass
class SharedFixture:
    db: Path
    config: Path
    metadata: UnqualifiedPosixMetadata

    def load(self, explicit: Path | None = None) -> RuntimePolicyBook:
        book = RuntimePolicyBook._from_shared_target_v2(explicit)
        assert book is not None
        return book


@pytest.fixture
def shared_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SharedFixture]:
    db = tmp_path / "policy.sqlite3"
    book = RuntimePolicyBook(db)
    book.save_v2(0, policy_fixture(), expected_strategy_generation=None,  # type: ignore[no-untyped-call]
                 policy_sections_version=1)
    config = tmp_path / "policy-target.json"
    config.write_text(json.dumps({"schema_version": 1, "policy_db": str(db)}), encoding="utf-8")
    metadata = UnqualifiedPosixMetadata()
    # These are TEST-ONLY metadata/platform replacements, never a qualification.
    monkeypatch.setattr(rp, "os", metadata, raising=False)
    monkeypatch.setattr(rp, "sys", SimpleNamespace(platform="linux"), raising=False)
    monkeypatch.setattr(rp, "_SHARED_POLICY_TARGET_V2", config, raising=False)
    yield SharedFixture(db, config, metadata)
    assert not metadata.descriptors, "configured operation leaked a real pin descriptor"


def test_existing_bound_loader_reads_real_policy_and_refuses_raw_escape(
    shared_target: SharedFixture,
) -> None:
    book = shared_target.load(shared_target.db)
    assert book.read()["revision"] == 1
    assert book.read()["desired_state"] == "STOPPED"
    with pytest.raises(ValueError):
        book.connect()


def test_absent_config_and_non_linux_issue_no_binding(
    shared_target: SharedFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_target.config.unlink()
    assert RuntimePolicyBook._from_shared_target_v2(shared_target.db) is None
    monkeypatch.setattr(rp, "sys", SimpleNamespace(platform="win32"))
    assert RuntimePolicyBook._from_shared_target_v2(shared_target.db) is None


@pytest.mark.parametrize("raw", [
    b"{}", b"[]", b'{"schema_version":true,"policy_db":"/x"}',
    b'{"schema_version":1,"schema_version":1,"policy_db":"/x"}',
    b'{"schema_version":1,"policy_db":"relative.sqlite3"}',
    b'{"schema_version":1,"policy_db":NaN}', b"x" * 4097,
])
def test_present_invalid_config_never_falls_back(
    shared_target: SharedFixture, raw: bytes,
) -> None:
    shared_target.config.write_bytes(raw)
    with pytest.raises((ValueError, OSError)):
        shared_target.load()


def test_missing_target_is_not_created(shared_target: SharedFixture) -> None:
    shared_target.db.unlink()
    with pytest.raises((ValueError, OSError)):
        shared_target.load()
    assert not shared_target.db.exists()


def test_conflicting_explicit_path_is_rejected(shared_target: SharedFixture) -> None:
    with pytest.raises(ValueError):
        shared_target.load(shared_target.db.with_name("other.sqlite3"))


@pytest.mark.parametrize("target", ["config", "db", "parent"])
@pytest.mark.parametrize("fault", ["writable", "symlink", "wrong_uid"])
def test_unsafe_metadata_refuses_binding(
    shared_target: SharedFixture, target: str, fault: str,
) -> None:
    path = {"config": shared_target.config, "db": shared_target.db,
            "parent": shared_target.db.parent}[target]
    kind = stat.S_IFDIR if target == "parent" else stat.S_IFREG
    mode, uid = kind | 0o700, 0
    if fault == "writable":
        mode |= 0o022
    elif fault == "symlink":
        mode = stat.S_IFLNK | 0o700
    else:
        uid = 1234
    shared_target.metadata.overrides[path] = mode, uid
    # The DB itself may be owned by the non-root service user.
    if target == "db" and fault == "wrong_uid":
        assert shared_target.load().read()["revision"] == 1
    else:
        with pytest.raises((ValueError, OSError)):
            shared_target.load()


@pytest.mark.parametrize("target", ["config", "db"])
def test_actual_hardlink_refuses_binding(shared_target: SharedFixture, target: str) -> None:
    path = shared_target.config if target == "config" else shared_target.db
    link = path.with_name(path.name + ".hardlink")
    try:
        os.link(path, link)
    except OSError as error:
        if getattr(error, "winerror", None) in {1, 50, 1314}:
            pytest.skip("temporary filesystem cannot create hardlinks; Linux CI must verify")
        raise
    try:
        with pytest.raises((ValueError, OSError)):
            shared_target.load()
    finally:
        link.unlink()


@pytest.mark.parametrize("target", ["config", "db"])
def test_simulated_multi_link_metadata_refuses_binding(
    shared_target: SharedFixture, target: str,
) -> None:
    path = shared_target.config if target == "config" else shared_target.db
    shared_target.metadata.link_counts[path] = 2
    with pytest.raises(ValueError):
        shared_target.load()


def test_opened_descriptor_permissions_are_checked_as_well_as_name(
    shared_target: SharedFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fstat = shared_target.metadata.fstat

    def unsafe_opened(descriptor: int) -> os.stat_result:
        fields = list(fstat(descriptor))
        fields[0] = int(fields[0]) | 0o022
        return os.stat_result(fields)

    monkeypatch.setattr(shared_target.metadata, "fstat", unsafe_opened)
    with pytest.raises(ValueError):
        shared_target.load()


@pytest.mark.parametrize("change", ["config", "config_inode", "db", "equal_path"])
def test_original_book_rejects_target_replacement(
    shared_target: SharedFixture, change: str,
) -> None:
    book = shared_target.load()
    if change == "equal_path":
        replacement = Path(str(book.path))
        assert replacement == book.path and replacement is not book.path
        book.path = replacement
    elif change == "config":
        shared_target.config.write_text(shared_target.config.read_text() + " ", encoding="utf-8")
    elif change == "config_inode":
        raw = shared_target.config.read_bytes()
        shared_target.config.rename(shared_target.config.with_name("old-config.json"))
        shared_target.config.write_bytes(raw)
    else:
        old = shared_target.db.with_name("old.sqlite3")
        shared_target.db.rename(old)
        RuntimePolicyBook(shared_target.db)
    with pytest.raises((ValueError, OSError)):
        book.read()


def test_configured_api_uses_actual_shared_book_and_conflict_is_unavailable(
    shared_target: SharedFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUNTIME_POLICY_DB_PATH", raising=False)
    app = create_app()
    with TestClient(app, base_url="http://localhost:8000") as client:
        assert client.get("/api/v1/automatic/settings").json()["revision"] == 1
        assert app.state.runtime_policy.path == shared_target.db
    monkeypatch.setenv("RUNTIME_POLICY_DB_PATH", str(shared_target.db.with_name("wrong.sqlite3")))
    with TestClient(create_app(), base_url="http://localhost:8000") as client:
        assert client.get("/api/v1/automatic/settings").status_code == 503
    assert not shared_target.db.with_name("wrong.sqlite3").exists()


@pytest.mark.parametrize("explicit", [False, True])
def test_absent_shared_config_preserves_api_passive_fallback(
    shared_target: SharedFixture, monkeypatch: pytest.MonkeyPatch, explicit: bool,
) -> None:
    shared_target.config.unlink()
    expected = shared_target.db.with_name("passive.sqlite3")
    if explicit:
        monkeypatch.setenv("RUNTIME_POLICY_DB_PATH", str(expected))
    else:
        monkeypatch.delenv("RUNTIME_POLICY_DB_PATH", raising=False)
        expected = Path(os.environ["PAPER_DB_PATH"]).parent / "runtime-policy.sqlite3"
    app = create_app()
    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.get("/api/v1/automatic/settings")
        assert response.status_code == 200
        assert response.json()["revision"] == 0
        assert response.json()["desired_state"] == "STOPPED"
        assert app.state.runtime_policy.path == expected
        assert app.state.runtime_policy._target_binding_v2 is None
    assert expected.is_file()


def test_api_retains_original_unreturned_loader_owner_after_close_failure(
    shared_target: SharedFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect = sqlite3.connect
    connections: list[sqlite3.Connection] = []

    class FailingClose(sqlite3.Connection):
        def close(self) -> None:
            raise sqlite3.OperationalError("injected configured-loader close")

    def failing(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        if args and args[0] == shared_target.db.as_uri() + "?mode=rw":
            kwargs["factory"] = FailingClose
            # TEST disposal runs outside TestClient's lifespan thread. Production
            # keeps SQLite's normal same-thread enforcement unchanged.
            kwargs["check_same_thread"] = False
            result: sqlite3.Connection = connect(*args, **kwargs)
            connections.append(result)
            return result
        result = connect(*args, **kwargs)
        return result

    monkeypatch.setattr(sqlite3, "connect", failing)
    monkeypatch.setenv("RUNTIME_POLICY_DB_PATH", str(shared_target.db))
    app = create_app()
    try:
        with TestClient(app, base_url="http://localhost:8000") as client:
            assert client.get("/api/v1/automatic/settings").status_code == 503
            owner = getattr(app.state, "_runtime_policy_cleanup_owner_v2", None)
            assert isinstance(owner, RuntimePolicyBook)
            assert owner._target_binding_v2 is not None
            assert owner._target_binding_v2.book is owner
            assert owner._quarantined_resources_v2 is not None
            assert owner._quarantined_resources_v2.db is connections[0]
    finally:
        for connection in connections:
            sqlite3.Connection.close(connection)


async def test_cli_conflict_rejects_before_credential_resolution(
    shared_target: SharedFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CredentialsForbidden(RuntimeError):
        pass

    def forbidden(**_: object) -> None:
        raise CredentialsForbidden("test must stop before resolving any credential")

    monkeypatch.setattr(worker_cli, "resolve_worker_expectations", forbidden)
    args = argparse.Namespace(policy_db=str(shared_target.db.with_name("wrong.sqlite3")),
                              uid=None, parent_uid=None, is_master=None)
    with pytest.raises(ValueError):
        await worker_cli._run(args)
    args.policy_db = str(shared_target.db)
    with pytest.raises(CredentialsForbidden):
        await worker_cli._run(args)
