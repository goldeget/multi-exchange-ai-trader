"""Offline policy contention only; these fixtures grant no trading qualification."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from test_policy_shared_target_v2 import SharedFixture
from test_policy_shared_target_v2 import shared_target as _shared_target
from test_runtime_policy import legacy, typed_scope

from multi_exchange_trader.execution import runtime_policy as rp
from multi_exchange_trader.execution.risk_v2_contracts import RiskScopeV2, risk_policy_sha256_v2
from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook

shared_target = _shared_target


def pins(book: RuntimePolicyBook) -> tuple[RiskScopeV2, int, str]:
    scope: RiskScopeV2 = typed_scope()  # type: ignore[no-untyped-call]
    policy = book.risk_policy_v2(scope)
    return scope, int(policy.revision), risk_policy_sha256_v2(policy)


def test_reservation_blocks_real_writer_until_rollback_and_uses_same_connection(
    shared_target: SharedFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    book, writer = shared_target.load(), shared_target.load()
    scope, revision, digest = pins(book)
    before = shared_target.db.read_bytes()
    started, committed = Event(), Event()
    actual_read = book._read
    seen: list[sqlite3.Connection] = []

    def read(db: sqlite3.Connection) -> dict[str, Any]:
        seen.append(db)
        assert db.in_transaction
        return actual_read(db)

    def forbidden(*_: object) -> None:
        pytest.fail("admission opened a second public read/connection")

    actual_open = writer._open_bound_connection_v2

    def traced_open(resources: rp._PolicyResourcesV2, *, timeout: float) -> None:
        actual_open(resources, timeout=timeout)
        assert resources.db is not None
        assert resources.db.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
        resources.db.set_trace_callback(
            lambda sql: started.set() if sql == "BEGIN IMMEDIATE" else None)

    def save() -> int:
        snapshot = writer.read()
        result = writer.save_v2(revision, snapshot["policy"], expected_strategy_generation=None,
                                policy_sections_version=2)
        committed.set()
        return int(result["revision"])

    monkeypatch.setattr(book, "_read", read)
    monkeypatch.setattr(book, "read", forbidden)
    monkeypatch.setattr(book, "connect", forbidden)
    monkeypatch.setattr(writer, "_open_bound_connection_v2", traced_open)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with book._private_admission_snapshot_v2(scope, revision, digest) as actual:
            assert actual.book is book and actual.revision == 1
            assert actual.policy_sha256 == digest and actual.policy.revision == "1"
            assert len(seen) == 1
            assert seen[0].execute("PRAGMA busy_timeout").fetchone()[0] == 0
            assert seen[0].execute("PRAGMA synchronous").fetchone()[0] == 2
            assert seen[0].total_changes == 0
            future = pool.submit(save)
            assert started.wait(2)
            assert not committed.wait(0.1)
            assert shared_target.db.read_bytes() == before
        assert future.result(timeout=3) == 2
    assert committed.is_set()


def test_prior_equal_save_invalidates_original_admission(shared_target: SharedFixture) -> None:
    book, writer = shared_target.load(), shared_target.load()
    scope, revision, digest = pins(book)
    snapshot = writer.read()
    writer.save_v2(revision, snapshot["policy"], expected_strategy_generation=None,
                   policy_sections_version=2)
    with (
        pytest.raises(ValueError),
        book._private_admission_snapshot_v2(scope, revision, digest),
    ):
        pytest.fail("stale equal-valued policy was admitted")
    assert book.read()["revision"] == 2


def test_busy_zero_and_nested_instances_refuse_without_changing_rows(
    shared_target: SharedFixture,
) -> None:
    book, other = shared_target.load(), shared_target.load()
    scope, revision, digest = pins(book)
    before = shared_target.db.read_bytes()
    with closing(sqlite3.connect(shared_target.db, timeout=0)) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        with (
            pytest.raises(sqlite3.OperationalError),
            book._private_admission_snapshot_v2(scope, revision, digest),
        ):
            pytest.fail("busy admission must not yield")
        blocker.rollback()
    with book._private_admission_snapshot_v2(scope, revision, digest):
        with (
            pytest.raises(ValueError),
            book._private_admission_snapshot_v2(scope, revision, digest),
        ):
            pytest.fail("same-book nesting must refuse")
        with (
            pytest.raises(sqlite3.OperationalError),
            other._private_admission_snapshot_v2(scope, revision, digest),
        ):
            pytest.fail("different-book same-file nesting must refuse")
    assert shared_target.db.read_bytes() == before
    # Busy/nesting is a conflict, not uncertain cleanup: a later attempt works.
    with other._private_admission_snapshot_v2(scope, revision, digest):
        pass


def test_plain_book_and_forged_revision_or_hash_cannot_admit(
    shared_target: SharedFixture,
) -> None:
    configured = shared_target.load()
    scope, revision, digest = pins(configured)
    with (
        pytest.raises(ValueError),
        RuntimePolicyBook(shared_target.db)._private_admission_snapshot_v2(
        scope, revision, digest
        ),
    ):
        pytest.fail("unconfigured book admitted")
    for wrong_revision, wrong_digest in ((True, digest), (revision, "0" * 64), (revision, "bad")):
        with (
            pytest.raises(ValueError),
            configured._private_admission_snapshot_v2(scope, wrong_revision, wrong_digest),
        ):
            pytest.fail("incorrect pin admitted")


@pytest.mark.parametrize("phase", ["rollback", "close", "both"])
def test_cleanup_failure_preserves_primary_and_poison_prevents_new_entry(
    shared_target: SharedFixture, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    book = shared_target.load()
    scope, revision, digest = pins(book)
    connect = sqlite3.connect
    connections: list[sqlite3.Connection] = []

    class FailingConnection(sqlite3.Connection):
        def rollback(self) -> None:
            if phase in {"rollback", "both"}:
                raise sqlite3.OperationalError("injected rollback")
            super().rollback()

        def close(self) -> None:
            if phase in {"close", "both"}:
                raise sqlite3.OperationalError("injected close")
            super().close()

    def failing(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        kwargs["factory"] = FailingConnection
        result: sqlite3.Connection = connect(*args, **kwargs)
        connections.append(result)
        return result

    monkeypatch.setattr(sqlite3, "connect", failing)
    try:
        with (
            pytest.raises(RuntimeError, match="primary admission failure"),
            book._private_admission_snapshot_v2(scope, revision, digest),
        ):
            raise RuntimeError("primary admission failure")
        assert len(book._cleanup_failures_v2) == (2 if phase == "both" else 1)
        assert (book._quarantined_resources_v2 is not None) == (phase != "rollback")
        assert not shared_target.metadata.descriptors
        with (
            pytest.raises(ValueError),
            book._private_admission_snapshot_v2(scope, revision, digest),
        ):
            pytest.fail("cleanup uncertainty was reused")
    finally:
        # Explicit TEST disposal bypasses the deliberately failing method.
        for connection in connections:
            sqlite3.Connection.close(connection)
    with closing(connect(shared_target.db, timeout=0)) as independent:
        independent.execute("BEGIN IMMEDIATE")
        independent.rollback()


def test_changed_config_during_reservation_poisoned_after_independent_cleanup(
    shared_target: SharedFixture,
) -> None:
    book = shared_target.load()
    scope, revision, digest = pins(book)
    original = shared_target.config.read_bytes()
    with (
        pytest.raises(ValueError),
        book._private_admission_snapshot_v2(scope, revision, digest),
    ):
        shared_target.config.write_bytes(original + b" ")
    shared_target.config.write_bytes(original)
    with (
        pytest.raises(ValueError),
        book._private_admission_snapshot_v2(scope, revision, digest),
    ):
        pytest.fail("uncertain identity restored into authority")
    with closing(sqlite3.connect(shared_target.db, timeout=0)) as independent:
        independent.execute("BEGIN IMMEDIATE")
        independent.rollback()


def test_close_failure_after_successful_body_is_not_reported_as_success(
    shared_target: SharedFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    book = shared_target.load()
    scope, revision, digest = pins(book)
    connect = sqlite3.connect
    connections: list[sqlite3.Connection] = []

    class FailingClose(sqlite3.Connection):
        def close(self) -> None:
            raise sqlite3.OperationalError("injected successful-body close")

    def failing(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        kwargs["factory"] = FailingClose
        result: sqlite3.Connection = connect(*args, **kwargs)
        connections.append(result)
        return result

    monkeypatch.setattr(sqlite3, "connect", failing)
    try:
        with (
            pytest.raises(sqlite3.OperationalError, match="successful-body close"),
            book._private_admission_snapshot_v2(scope, revision, digest),
        ):
            pass
        with (
            pytest.raises(ValueError),
            book._private_admission_snapshot_v2(scope, revision, digest),
        ):
            pytest.fail("a success-body cleanup failure did not retire the owner")
        assert not shared_target.metadata.descriptors
    finally:
        for connection in connections:
            sqlite3.Connection.close(connection)


def test_pin_close_failure_attempts_all_cleanup_and_retains_only_uncertain_pin(
    shared_target: SharedFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    book = shared_target.load()
    scope, revision, digest = pins(book)
    close = shared_target.metadata.close

    def fail_config_pin(descriptor: int) -> None:
        if shared_target.metadata.descriptors[descriptor] == shared_target.config:
            raise OSError("injected config pin close")
        close(descriptor)

    monkeypatch.setattr(shared_target.metadata, "close", fail_config_pin)
    try:
        with (
            pytest.raises(RuntimeError, match="primary"),
            book._private_admission_snapshot_v2(scope, revision, digest),
        ):
            raise RuntimeError("primary")
        assert list(shared_target.metadata.descriptors.values()) == [shared_target.config]
        with (
            pytest.raises(ValueError),
            book._private_admission_snapshot_v2(scope, revision, digest),
        ):
            pytest.fail("uncertain descriptor was reused")
        with closing(sqlite3.connect(shared_target.db, timeout=0)) as independent:
            independent.execute("BEGIN IMMEDIATE")
            independent.rollback()
    finally:
        for descriptor in list(shared_target.metadata.descriptors):
            close(descriptor)


def test_configured_save_checks_identity_before_commit_and_rolls_back(
    shared_target: SharedFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    book = shared_target.load()
    original_read = book._read
    snapshot = book.read()
    original_config = shared_target.config.read_bytes()
    reads = 0

    def changed_after_write(db: sqlite3.Connection) -> dict[str, Any]:
        nonlocal reads
        result = original_read(db)
        reads += 1
        if reads == 2:
            shared_target.config.write_bytes(original_config + b" ")
        return result

    monkeypatch.setattr(book, "_read", changed_after_write)
    with pytest.raises(ValueError):
        book.save_v2(1, snapshot["policy"], expected_strategy_generation=None,
                     policy_sections_version=2)
    shared_target.config.write_bytes(original_config)
    assert RuntimePolicyBook(shared_target.db).read()["revision"] == 1
    scope, revision, digest = pins(RuntimePolicyBook(shared_target.db))
    with (
        pytest.raises(ValueError),
        book._private_admission_snapshot_v2(scope, revision, digest),
    ):
        pytest.fail("uncertain writer owner admitted")


def test_configured_schema1_migration_keeps_original_archive_and_fences(
    shared_target: SharedFixture, tmp_path: Path,
) -> None:
    old_path, old, draft, raw = legacy(tmp_path / "legacy")  # type: ignore[no-untyped-call]
    # Use a second real original schema1 file, with the fixed temporary config.
    shared_target.config.write_text(
        '{"schema_version":1,"policy_db":' + json.dumps(str(old_path)) + '}',
        encoding="utf-8",
    )
    book = shared_target.load()
    scope: RiskScopeV2 = typed_scope()  # type: ignore[no-untyped-call]
    with (
        pytest.raises(ValueError, match="POLICY_V1_MIGRATION_REQUIRED"),
        book._private_admission_snapshot_v2(scope, 7, "0" * 64),
    ):
        pytest.fail("schema1 admission")
    assert book.migrate_v1(7)["revision"] == 8
    with closing(sqlite3.connect(old_path)) as db:
        assert db.execute("SELECT payload FROM runtime_policy_v1_archive").fetchone()[0] == raw
    with pytest.raises(sqlite3.IntegrityError):
        old.save(7, draft)


@pytest.mark.parametrize("fault", ["caps", "strategy", "spot"])
def test_same_projection_preserves_financial_rejections(
    shared_target: SharedFixture, fault: str,
) -> None:
    book = shared_target.load()
    scope, revision, _ = pins(book)
    snapshot = book.read()
    if fault == "caps":
        snapshot["policy"]["risk_v2"]["caps"]["max_account_admission_notional"] = None
    elif fault == "strategy":
        snapshot["policy"]["strategy"]["selected"] = []
    else:
        snapshot["policy"]["scope"].update(selected_product="SPOT", allowed_products=["SPOT"],
                                          allowed_instruments=None, selected_instruments=None)
        snapshot["policy"]["strategy"].update(allowed=None, selected=None)
        snapshot["policy"]["risk_v2"] = None
    book.save_v2(revision, snapshot["policy"], expected_strategy_generation=None,
                 policy_sections_version=2)
    revision += 1
    with pytest.raises(ValueError):
        book.risk_policy_v2(scope)
    with (
        pytest.raises(ValueError),
        book._private_admission_snapshot_v2(scope, revision, "0" * 64),
    ):
        pytest.fail("projection rejection bypassed")



def _lifetime_operation(
    book: RuntimePolicyBook, operation: str, scope: RiskScopeV2, revision: int,
    digest: str, policy: dict[str, Any],
) -> int:
    if operation == "admission":
        with book._private_admission_snapshot_v2(scope, revision, digest) as data:
            return data.revision
    if operation == "save":
        result = book.save_v2(revision, policy, expected_strategy_generation=None,
                              policy_sections_version=2)
    elif operation == "migrate":
        result = book.migrate_v1(revision)
    else:
        assert operation == "read"
        result = book.read()
    return int(result["revision"])


def _lifetime_book(
    shared: SharedFixture, tmp_path: Path, active: str,
) -> tuple[RuntimePolicyBook, RiskScopeV2, int, str, dict[str, Any]]:
    book = shared.load()
    policy = book.read()["policy"]
    scope, revision, digest = pins(book)
    if active == "migrate":
        path, _, _, _ = legacy(tmp_path / "lifetime-legacy")  # type: ignore[no-untyped-call]
        shared.config.write_text(
            '{"schema_version":1,"policy_db":' + json.dumps(str(path)) + '}',
            encoding="utf-8",
        )
        book, revision = shared.load(), 7
    return book, scope, revision, digest, policy


@pytest.mark.parametrize("second", ["read", "admission", "save", "migrate"])
@pytest.mark.parametrize("active", ["read", "admission", "save", "migrate"])
def test_configured_operation_lifetime_excludes_second_bundle_before_open(
    shared_target: SharedFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    active: str, second: str,
) -> None:
    # A gate limited to admission or released before cleanup loses this contract.
    book, scope, revision, digest, policy = _lifetime_book(shared_target, tmp_path, active)
    entered, release = Event(), Event()
    actual_read, actual_open = book._read, shared_target.metadata.open
    opened: list[Path] = []
    first = True

    def opened_pin(path: str | Path, flags: int, mode: int = 0o777) -> int:
        opened.append(Path(path))
        return actual_open(path, flags, mode)

    def held_read(db: sqlite3.Connection) -> dict[str, Any]:
        nonlocal first
        result = actual_read(db)
        if first:
            first = False
            assert db.in_transaction
            entered.set()
            assert release.wait(5), "test did not release first actual operation"
        return result

    monkeypatch.setattr(shared_target.metadata, "open", opened_pin)
    monkeypatch.setattr(book, "_read", held_read)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_lifetime_operation, book, active, scope, revision, digest, policy)
        try:
            assert entered.wait(5), "first actual operation did not enter SQLite read"
            assert len(opened) == 2 and len(shared_target.metadata.descriptors) == 2
            with pytest.raises(ValueError, match="already reserved"):
                _lifetime_operation(book, second, scope, revision, digest, policy)
            assert len(opened) == 2, "rejected operation acquired a second pin bundle"
        finally:
            release.set()
        expected = revision + 1 if active in {"save", "migrate"} else revision
        assert future.result(timeout=5) == expected
    assert not shared_target.metadata.descriptors
    assert book.read()["revision"] == expected  # A fresh later operation remains usable.


@pytest.mark.parametrize("active", ["read", "admission", "save", "migrate"])
def test_configured_operation_lifetime_refuses_nested_operations_without_double_lock(
    shared_target: SharedFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    active: str,
) -> None:
    book, scope, revision, digest, policy = _lifetime_book(shared_target, tmp_path, active)
    actual_read, actual_open = book._read, shared_target.metadata.open
    opened: list[Path] = []
    checked = False

    def opened_pin(path: str | Path, flags: int, mode: int = 0o777) -> int:
        opened.append(Path(path))
        return actual_open(path, flags, mode)

    def nested_read(db: sqlite3.Connection) -> dict[str, Any]:
        nonlocal checked
        result = actual_read(db)
        if not checked:
            checked = True
            for second in ("read", "admission", "save", "migrate"):
                with pytest.raises(ValueError, match="already reserved"):
                    _lifetime_operation(book, second, scope, revision, digest, policy)
            assert len(opened) == 2
        return result

    monkeypatch.setattr(shared_target.metadata, "open", opened_pin)
    monkeypatch.setattr(book, "_read", nested_read)
    expected = revision + 1 if active in {"save", "migrate"} else revision
    assert _lifetime_operation(book, active, scope, revision, digest, policy) == expected
    assert checked and not shared_target.metadata.descriptors
    assert book.read()["revision"] == expected


def test_configured_gate_spans_uncertain_cleanup_and_retains_only_original_bundle(
    shared_target: SharedFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Real SQLite read lock survives injected cleanup; disposal stays on its creator.
    book = shared_target.load()
    entered, release_body, cleaning, release_cleanup, cleaned, dispose = (
        Event(), Event(), Event(), Event(), Event(), Event())
    actual_connect, actual_read = sqlite3.connect, book._read
    connections: list[sqlite3.Connection] = []
    facts: dict[str, Any] = {}

    class UncertainConnection(sqlite3.Connection):
        def rollback(self) -> None:
            cleaning.set()
            assert release_cleanup.wait(5), "cleanup was not released"
            raise sqlite3.OperationalError("injected original rollback uncertainty")

        def close(self) -> None:
            raise sqlite3.OperationalError("injected original close uncertainty")

    def uncertain_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        kwargs["factory"] = UncertainConnection
        connection: sqlite3.Connection = actual_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    def failed_read(db: sqlite3.Connection) -> dict[str, Any]:
        actual_read(db)
        entered.set()
        assert release_body.wait(5), "body was not released"
        raise RuntimeError("original configured read failure")

    def run_first() -> BaseException:
        try:
            book.read()
        except BaseException as error:  # noqa: BLE001 - creator must dispose uncertain connection.
            facts["in_transaction"] = connections[0].in_transaction
            facts["select"] = connections[0].execute("SELECT 1").fetchone()[0]
            cleaned.set()
            try:
                assert dispose.wait(5), "creator-thread disposal was not requested"
            finally:
                sqlite3.Connection.rollback(connections[0])
                sqlite3.Connection.close(connections[0])
                facts["explicit_same_thread_disposal"] = True
            return error
        raise AssertionError("first failure was swallowed")

    monkeypatch.setattr(sqlite3, "connect", uncertain_connect)
    monkeypatch.setattr(book, "_read", failed_read)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_first)
        try:
            assert entered.wait(5)
            with pytest.raises(ValueError, match="already reserved"):
                book.read()
            assert len(connections) == 1
            release_body.set()
            assert cleaning.wait(5)
            with pytest.raises(ValueError, match="already reserved"):
                book.read()
            assert len(connections) == 1 and len(shared_target.metadata.descriptors) == 2
            release_cleanup.set()
            assert cleaned.wait(5)
            retained = book._quarantined_resources_v2
            assert retained is not None and retained.db is connections[0]
            assert retained.descriptors == [] and not shared_target.metadata.descriptors
            assert book._admission_poisoned_v2
            with pytest.raises(ValueError):
                book.read()
            assert book._quarantined_resources_v2 is retained and len(connections) == 1
            assert facts["in_transaction"] and facts["select"] == 1
            with (
                closing(actual_connect(shared_target.db, timeout=0)) as writer,
                pytest.raises(sqlite3.OperationalError, match="locked"),
            ):
                writer.execute("BEGIN EXCLUSIVE")
        finally:
            release_body.set()
            release_cleanup.set()
            dispose.set()
        error = future.result(timeout=5)
    assert type(error) is RuntimeError and str(error) == "original configured read failure"
    assert vars(error)["_policy_cleanup_owner_v2"] is book
    assert len(book._cleanup_failures_v2) == 2 and facts["explicit_same_thread_disposal"]
    with closing(actual_connect(shared_target.db, timeout=0)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        writer.rollback()
