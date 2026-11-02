"""Real temporary worker stores use the offline recovery tool's canonical layout."""

import asyncio
import json
import runpy
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from test_worker import fill, harness, stage

from multi_exchange_trader.execution import worker_cli
from multi_exchange_trader.execution.journal import CommandState
from multi_exchange_trader.execution.ownership import AccountWorkerLock
from multi_exchange_trader.execution.runtime import RuntimeBlocked
from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook


async def test_worker_creates_canonical_execution_journal(tmp_path: Path) -> None:
    h = harness(tmp_path)
    await h.worker.start_passive()
    try:
        assert (tmp_path / "execution.sqlite3").is_file()
        assert (tmp_path / "runtime-state.sqlite3").is_file()
        assert not (tmp_path / "journal.sqlite3").exists()
    finally:
        await h.worker.stop()


@pytest.mark.parametrize("legacy", ["journal.sqlite3", "journal.sqlite3-wal",
                                   "journal.sqlite3-shm", "journal.sqlite3-journal"])
@pytest.mark.parametrize("canonical_exists", [False, True])
async def test_legacy_worker_store_refused_without_opening_or_creating_stores(
    tmp_path: Path, legacy: str, canonical_exists: bool,
) -> None:
    h = harness(tmp_path)
    old = tmp_path / legacy
    old.write_bytes(b"legacy fixture must remain untouched")
    canonical = tmp_path / "execution.sqlite3"
    if canonical_exists:
        canonical.write_bytes(b"existing canonical generation must remain untouched")
    h.worker._factory = lambda _sink: pytest.fail("refuse before native construction")
    with pytest.raises(RuntimeBlocked, match="LEGACY_WORKER_JOURNAL_REQUIRES_OPERATOR_RESTORE"):
        await h.worker.start_passive()
    assert old.read_bytes() == b"legacy fixture must remain untouched"
    if canonical_exists:
        assert canonical.read_bytes() == b"existing canonical generation must remain untouched"
    else:
        assert not canonical.exists()
    assert not (tmp_path / "runtime-state.sqlite3").exists()
    lock = AccountWorkerLock(tmp_path, account_id=h.worker.scope.account_id, environment="DEMO")
    lock.acquire()
    lock.release()


@pytest.mark.parametrize("filled", [False, True])
async def test_restored_canonical_journal_and_state_reuse_ownership_without_replay(
    tmp_path: Path, filled: bool,
) -> None:
    source, restored = tmp_path / "source", tmp_path / "restored"
    locks, restored_locks = tmp_path / "locks", tmp_path / "restored-locks"
    source.mkdir()
    restored.mkdir()
    locks.mkdir()
    restored_locks.mkdir()
    h = harness(source, lock_directory=locks)
    await h.worker.start_passive()
    assert h.worker._lock.path.parent == locks
    assert h.worker._lock.acquired
    assert not list(source.glob("*.lock"))
    record = await stage(h)
    h.worker.runtime.journal.claim("entry")
    if filled:
        h.worker.post_native(h.assembly.channel, fill(record.client_order_id))
        await asyncio.sleep(0)
    assert h.worker.callback_failures == 0
    await h.worker.stop()
    # An operator-selected restored generation: real closed SQLite files, never
    # a fresh empty journal. Snapshot manifest validation is covered by operations tests.
    for name in ("execution.sqlite3", "runtime-state.sqlite3"):
        shutil.copy2(source / name, restored / name)
    worker = harness(restored, lock_directory=restored_locks).worker
    assert not worker._factory(worker.post_native).bridge._requests
    await worker.start_passive()
    try:
        recovered = worker.runtime.journal.get("entry")
        assert recovered.client_order_id == record.client_order_id
        assert recovered.state is (CommandState.ACKNOWLEDGED if filled else CommandState.UNKNOWN)
        assert worker.runtime.projection("position-1").owned_quantity == (
            Decimal("0.4") if filled else Decimal(0))
        assert worker.runtime.bridge._requests[record.client_order_id].owned.position_id == "position-1"
        assert not worker.runtime.bridge._submissions
        assert worker.runtime.status.entries_paused
        assert worker._lock.path.parent == restored_locks
        assert not list(restored.glob("*.lock"))
        assert not (restored / "journal.sqlite3").exists()
    finally:
        await worker.stop()


def test_cli_reports_fixed_legacy_layout_diagnostic(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    async def legacy_failure(_args: object) -> None:
        raise RuntimeBlocked("LEGACY_WORKER_JOURNAL_REQUIRES_OPERATOR_RESTORE")

    monkeypatch.setattr(worker_cli, "_run", legacy_failure)
    monkeypatch.setattr(worker_cli.sys, "platform", "linux")
    assert worker_cli.main(["--run-monitor", "--uid", "fixture", "--parent-uid", "fixture",
        "--is-master", "true", "--symbol", "BTCUSDT", "--directory", "unused",
        "--policy-db", "unused"]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == (
        "LEGACY_WORKER_JOURNAL_REQUIRES_OPERATOR_RESTORE")


def test_cli_accepts_explicit_separate_lease_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[object] = []

    async def capture(args: object) -> None:
        captured.append(vars(args)["lock_directory"])

    monkeypatch.setattr(worker_cli, "_run", capture)
    monkeypatch.setattr(worker_cli.sys, "platform", "linux")
    assert worker_cli.main(["--run-monitor", "--uid", "fixture", "--parent-uid", "fixture",
        "--is-master", "true", "--symbol", "BTCUSDT", "--directory", "/var/lib/trader/data",
        "--policy-db", "/var/lib/trader/data/runtime-policy.sqlite3",
        "--lock-directory", "/var/lib/trader/locks"]) == 0
    assert captured == ["/var/lib/trader/locks"]


@pytest.mark.parametrize("filled", [False, True])
async def test_real_worker_common_root_snapshot_verify_restore(tmp_path: Path, filled: bool) -> None:
    # Reuse the operations suite's six-ledger fixture and real offline CLI without
    # editing that suite or loosening the snapshot inventory to admit lease files.
    root = Path(__file__).resolve().parents[2]
    operations = runpy.run_path(str(root / "tests/operations/test_state_snapshot.py"))
    source = operations["state"].__wrapped__(tmp_path)
    locks = tmp_path / "locks"
    locks.mkdir()
    h = harness(source, lock_directory=locks)
    await h.worker.start_passive()
    record = await stage(h)
    h.worker.runtime.journal.claim("entry")
    if filled:
        h.worker.post_native(h.assembly.channel, fill(record.client_order_id))
        await asyncio.sleep(0)
    assert h.worker.callback_failures == 0
    await h.worker.stop()
    assert list(locks.glob("*.lock")) and not list(source.glob("*.lock"))
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    result = operations["snapshot"](source, backup)
    assert result.returncode == 0, result.stderr
    result = operations["command"]("verify", "--source", backup)
    assert result.returncode == 0, result.stderr
    result = operations["command"]("restore", "--source", backup, "--target", restored)
    assert result.returncode == 0, result.stderr
    template = tmp_path / "fresh-worker-fixture"
    template.mkdir()
    fresh = harness(template, lock_directory=locks)
    fresh.worker.directory = restored
    fresh.worker.policy_book = RuntimePolicyBook(restored / "runtime-policy.sqlite3")
    assert not fresh.assembly.bridge._requests
    await fresh.worker.start_passive()
    try:
        recovered = fresh.worker.runtime.journal.get("entry")
        assert recovered.client_order_id == record.client_order_id
        assert recovered.state is (CommandState.ACKNOWLEDGED if filled else CommandState.UNKNOWN)
        assert fresh.worker.runtime.projection("position-1").owned_quantity == (
            Decimal("0.4") if filled else Decimal(0))
        assert record.client_order_id in fresh.assembly.bridge._requests
        assert not fresh.assembly.bridge._submissions
        assert fresh.worker.runtime.status.entries_paused
        assert fresh.worker._lock.path.parent == locks
        assert not list(restored.glob("*.lock"))
    finally:
        await fresh.worker.stop()
