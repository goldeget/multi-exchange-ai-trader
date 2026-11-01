"""One worker per account/environment on one designated host.

The directory must be private and local (not a shared/network filesystem).
The OS owns the lock lifetime; the file is never unlinked, avoiding two-inode
locks. This is not fencing against a second computer using the same account.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from types import TracebackType
from typing import Self

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class WorkerAlreadyRunning(RuntimeError):
    """Another process owns this account/environment on the designated host."""


class AccountWorkerLock:
    def __init__(self, directory: Path, *, account_id: str, environment: str) -> None:
        if not isinstance(account_id, str) or not account_id.strip() or len(account_id) > 256:
            raise ValueError("explicit bounded account_id required")
        if environment not in ("DEMO", "LIVE"):
            raise ValueError("explicit DEMO or LIVE environment required")
        self._scope = (account_id, environment)
        scope = json.dumps([account_id, environment], ensure_ascii=True).encode("ascii")
        self._path = directory / (hashlib.sha256(scope).hexdigest() + ".lock")
        self._fd: int | None = None
        self._acquisition_id = 0

    @property
    def path(self) -> Path:
        return self._path

    @property
    def acquired(self) -> bool:
        return self._fd is not None

    @property
    def acquisition_id(self) -> int:
        """Process-local lease epoch; a new lease requires fresh reconciliation."""
        return self._acquisition_id

    def matches_scope(self, *, account_id: str, environment: str) -> bool:
        """Check the live descriptor still names the exact scoped lock file.

        All workers must use the same configured private lock directory. This
        does not discover locks created in another directory or on another host.
        """
        if self._fd is None or self._scope != (account_id, environment):
            return False
        try:
            opened = os.fstat(self._fd)
            named = self.path.lstat()
            return (
                stat.S_ISREG(named.st_mode)
                and opened.st_nlink == named.st_nlink == 1
                and (opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino)
            )
        except OSError:
            return False

    def acquire(self) -> None:
        if self._fd is not None:
            raise RuntimeError("worker lock already acquired by this instance")
        # Do not create directories or follow links. Operator supplies private parent.
        if self.path.is_symlink() or self.path.is_junction():
            raise ValueError("worker lock must be a regular single-link file")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("worker lock must be a regular single-link file")
            if info.st_size == 0:
                os.write(fd, b"\0")
                os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                if sys.platform == "win32":
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise WorkerAlreadyRunning("account worker lock unavailable") from exc
            self._fd = fd
            self._acquisition_id += 1
        except BaseException:
            os.close(fd)
            raise

    def release(self) -> None:
        fd = self._fd
        if fd is None:
            return
        self._fd = None
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            if sys.platform == "win32":
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
