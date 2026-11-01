"""A real OS lock must arbitrate account workers, including across processes."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from multi_exchange_trader.execution.ownership import AccountWorkerLock, WorkerAlreadyRunning


def test_same_account_has_one_owner_and_release_preserves_lockfile(tmp_path: Path) -> None:
    first = AccountWorkerLock(tmp_path, account_id="account-a", environment="DEMO")
    second = AccountWorkerLock(tmp_path, account_id="account-a", environment="DEMO")
    with first:
        assert first.path.is_file()
        with pytest.raises(WorkerAlreadyRunning):
            second.acquire()
    assert first.path.exists()
    with second:
        assert second.acquired
    assert not second.acquired


def test_scope_is_explicit_and_distinct(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        AccountWorkerLock(tmp_path, account_id="account-a", environment="")
    with pytest.raises(ValueError):
        AccountWorkerLock(tmp_path, account_id="", environment="LIVE")
    with (
        AccountWorkerLock(tmp_path, account_id="../../outside", environment="DEMO") as demo,
        AccountWorkerLock(tmp_path, account_id="../../outside", environment="LIVE") as live,
    ):
        assert demo.path.parent == tmp_path
        assert demo.path != live.path


def test_reentry_is_rejected_without_releasing_original(tmp_path: Path) -> None:
    first = AccountWorkerLock(tmp_path, account_id="a", environment="DEMO")
    with first:
        with pytest.raises(RuntimeError):
            first.acquire()
        with pytest.raises(WorkerAlreadyRunning):
            AccountWorkerLock(tmp_path, account_id="a", environment="DEMO").acquire()
    first.release()


@pytest.mark.parametrize("crash", [False, True])
def test_other_process_cannot_acquire_until_owner_exits(tmp_path: Path, crash: bool) -> None:
    script = """
import sys
from pathlib import Path
from multi_exchange_trader.execution.ownership import AccountWorkerLock
with AccountWorkerLock(Path(sys.argv[1]), account_id='a', environment='DEMO'):
    print('LOCKED', flush=True)
    sys.stdin.read(1)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")},
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "LOCKED"
        contender = AccountWorkerLock(tmp_path, account_id="a", environment="DEMO")
        with pytest.raises(WorkerAlreadyRunning):
            contender.acquire()
        if crash:
            process.kill()
        process.communicate(input="x" if not crash else None, timeout=10)
        with contender:
            assert contender.acquired
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_linked_lockfile_is_rejected_without_altering_target(tmp_path: Path) -> None:
    lock = AccountWorkerLock(tmp_path, account_id="a", environment="DEMO")
    target = tmp_path / "do-not-change"
    target.write_bytes(b"sentinel")
    os.link(target, lock.path)
    with pytest.raises(ValueError, match="regular|link"):
        lock.acquire()
    assert target.read_bytes() == b"sentinel"


def test_empty_scope_never_creates_files(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        AccountWorkerLock(tmp_path, account_id=" ", environment="DEMO")
    assert list(tmp_path.iterdir()) == []


def test_scope_check_requires_live_lock_for_exact_identity(tmp_path: Path) -> None:
    lock = AccountWorkerLock(tmp_path, account_id="a", environment="DEMO")
    assert not lock.matches_scope(account_id="a", environment="DEMO")
    with lock:
        assert lock.matches_scope(account_id="a", environment="DEMO")
        assert not lock.matches_scope(account_id="a", environment="LIVE")
        assert not lock.matches_scope(account_id="b", environment="DEMO")
    assert not lock.matches_scope(account_id="a", environment="DEMO")


def test_acquisition_epoch_changes_only_after_a_new_successful_lease(tmp_path: Path) -> None:
    lock = AccountWorkerLock(tmp_path, account_id="a", environment="DEMO")
    assert lock.acquisition_id == 0
    with lock:
        first = lock.acquisition_id
        assert first > 0
        with pytest.raises(RuntimeError):
            lock.acquire()
        assert lock.acquisition_id == first
    with lock:
        assert lock.acquisition_id == first + 1
