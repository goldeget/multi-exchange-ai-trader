"""Private, expiring UI observations; never order or financial authority."""

import hashlib
import json
import math
import os
import re
import stat
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import uuid4

from .account_profiles import AccountExpectations, AccountProfileError, AccountProfileStore
from .execution.ownership import AccountWorkerLock

if TYPE_CHECKING:
    from .execution.worker import ExecutionWorker

_STATES = frozenset({"STARTING", "OBSERVING", "PASSIVE", "STOPPED", "CLEANUP_UNCONFIRMED", "CHECK_REQUIRED"})
_FIELDS = frozenset({"schema_version", "account_id", "environment", "expectations_sha256",
    "profile_id", "profile_revision", "run_id", "observed_at", "state", "rest_expires_at",
    "mode", "trading_enabled", "financial_authority", "owner_pid", "owner_start_ticks",
    "engine_lock_kind", "engine_device", "engine_inode", "engine_lease_held"})
_POLICIES = frozenset({"independent-v1", "uid-anchored-adoption-v1"})
_MESSAGES = {
    "STARTING": "계정 관측 연결을 준비하고 있습니다.",
    "OBSERVING": "계정 관측 신호를 받고 있습니다. 자동매매는 차단되어 있습니다.",
    "PASSIVE": "계정을 조회하고 있습니다. 실시간 관측 연결은 시작하지 않았습니다.",
    "STOPPED": "관측 종료를 확인했습니다. 자동매매는 차단되어 있습니다.",
    "CLEANUP_UNCONFIRMED": "종료 정리를 확인하지 못했습니다. 재시작 전에 상태 확인이 필요합니다.",
    "CHECK_REQUIRED": "엔진의 현재 상태를 확인하지 못했습니다.",
    "NOT_STARTED": "저장된 계정의 관측 신호가 아직 없습니다.",
    "STALE": "최신 관측 신호가 없습니다. 현재 실행 상태를 확인해 주세요.",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("MONITOR_STATUS_TIME_INVALID")
    return value.astimezone(UTC)


def _text(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse(value: object) -> datetime:
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", value):
        raise ValueError("MONITOR_STATUS_TIME_INVALID")
    return _utc(datetime.fromisoformat(value))


def _identity(expected: AccountExpectations) -> str:
    values = [expected.profile_id, expected.revision, expected.basis, expected.source,
              expected.policy, expected._uid, expected._parent_uid, expected._is_master]
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def _directory(data: Path) -> Path:
    original = data.absolute()
    if ".." in original.parts:
        raise ValueError("MONITOR_STATUS_PATH_INVALID")
    folder = original.parent / "monitor-status"
    for item in (folder, *folder.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError("MONITOR_STATUS_PATH_INVALID")
    return folder


def _private(path: Path, *, directory: bool = False) -> os.stat_result:
    metadata = path.lstat()
    if path.is_symlink() or path.is_junction() or not (
        stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    ):
        raise ValueError("MONITOR_STATUS_PATH_INVALID")
    if os.name == "posix" and (metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077):
        raise ValueError("MONITOR_STATUS_NOT_PRIVATE")
    if not directory and metadata.st_nlink != 1:
        raise ValueError("MONITOR_STATUS_PATH_INVALID")
    return metadata


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("MONITOR_STATUS_DUPLICATE_FIELD")
        result[key] = value
    return result


def _read(path: Path) -> dict[str, Any]:
    _private(path.parent, directory=True)
    named = _private(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_size > 4096
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
            raise ValueError("MONITOR_STATUS_FILE_INVALID")
        raw = stream.read(4097)
        after = _private(path)
        if (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino) or len(raw) > 4096:
            raise ValueError("MONITOR_STATUS_FILE_CHANGED")
    value = json.loads(raw, object_pairs_hook=_pairs)
    if (type(value) is not dict or set(value) != _FIELDS or type(value["schema_version"]) is not int
            or value["schema_version"] != 1 or value["environment"] != "DEMO"
            or type(value["account_id"]) is not str or not re.fullmatch(r"[0-9]{1,32}", value["account_id"])
            or type(value["profile_id"]) is not str or len(value["profile_id"]) > 36
            or type(value["profile_revision"]) is not int or not 0 <= value["profile_revision"] < 2**63
            or type(value["expectations_sha256"]) is not str or not re.fullmatch(r"[a-f0-9]{64}", value["expectations_sha256"])
            or type(value["run_id"]) is not str or not re.fullmatch(r"[a-f0-9]{32}", value["run_id"])
            or type(value["state"]) is not str or value["state"] not in _STATES
            or type(value["owner_pid"]) is not int or not 0 < value["owner_pid"] < 2**31
            or type(value["owner_start_ticks"]) is not int or not 0 <= value["owner_start_ticks"] < 2**64
            or type(value["engine_device"]) is not int or not 0 <= value["engine_device"] < 2**64
            or type(value["engine_inode"]) is not int or not 0 < value["engine_inode"] < 2**64
            or type(value["engine_lock_kind"]) is not str or value["engine_lock_kind"] not in {"DATA_LOCAL", "SERVER_SHARED"}
            or type(value["engine_lease_held"]) is not bool
            or value["mode"] != "OBSERVATION_ONLY" or value["trading_enabled"] is not False
            or value["financial_authority"] is not False):
        raise ValueError("MONITOR_STATUS_RECORD_INVALID")
    observed = _parse(value["observed_at"])
    if value["rest_expires_at"] is not None:
        expires = _parse(value["rest_expires_at"])
        if value["state"] != "OBSERVING" or not timedelta(0) <= expires - observed <= timedelta(seconds=30):
            raise ValueError("MONITOR_STATUS_REST_TIME_INVALID")
    return cast(dict[str, Any], value)


def _path(data: Path, uid: str) -> Path:
    return _directory(data) / (hashlib.sha256(("DEMO:" + uid).encode()).hexdigest() + ".json")


def _process_ticks(pid: int) -> int:
    # Linux proc_pid_stat(5), field 22. Never read cmdline or environ.
    if sys.platform != "linux":
        raise ValueError("MONITOR_STATUS_KERNEL_UNAVAILABLE")
    with (Path("/proc") / str(pid) / "stat").open("rb") as stream:
        raw = stream.read(4097)
    if len(raw) > 4096 or raw.split(b"(", 1)[0].strip() != str(pid).encode():
        raise ValueError("MONITOR_STATUS_PROCESS_INVALID")
    value = raw.rsplit(b")", 1)[1].split()[19]
    if not value.isdigit() or len(value) > 20:
        raise ValueError("MONITOR_STATUS_PROCESS_INVALID")
    return int(value)


def _kernel_owner(data: Path, uid: str, value: dict[str, Any]) -> tuple[int, int] | None:
    """Read the genuine UID lock without acquiring, duplicating or rewriting it.

    The deployed CLI runs one worker per process. PID plus process start ticks
    identifies that process, not a reusable financial or lock-generation token.
    """
    if sys.platform != "linux":
        raise ValueError("MONITOR_STATUS_KERNEL_UNAVAILABLE")
    directory = data.absolute() if value["engine_lock_kind"] == "DATA_LOCAL" else data.absolute().parent / "locks"
    for part in (directory, *directory.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("MONITOR_STATUS_ENGINE_PATH_INVALID")
    _private(directory, directory=True)
    # The constructor/path property is pure; acquire() is deliberately never called.
    lock_path = AccountWorkerLock(directory, account_id=uid, environment="DEMO").path
    original = _private(lock_path)
    if (original.st_dev, original.st_ino) != (value["engine_device"], value["engine_inode"]):
        raise ValueError("MONITOR_STATUS_ENGINE_IDENTITY_CHANGED")
    with Path("/proc/locks").open("rb") as stream:
        raw = stream.read(262145)
    if len(raw) > 262144:
        raise ValueError("MONITOR_STATUS_KERNEL_LIMIT")
    owners: list[int] = []
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) > 1 and fields[1] == b"->":
            continue  # A blocked waiter is not the current owner.
        if len(fields) < 8:
            raise ValueError("MONITOR_STATUS_KERNEL_INVALID")
        major, minor, inode = fields[5].split(b":")
        if (int(major, 16), int(minor, 16), int(inode)) != (
            os.major(original.st_dev), os.minor(original.st_dev), original.st_ino
        ):
            continue
        if fields[1:4] != [b"FLOCK", b"ADVISORY", b"WRITE"] or fields[6:] != [b"0", b"EOF"]:
            raise ValueError("MONITOR_STATUS_KERNEL_INVALID")
        owners.append(int(fields[4]))
    after = _private(lock_path)
    if (after.st_dev, after.st_ino) != (original.st_dev, original.st_ino) or len(owners) > 1:
        raise ValueError("MONITOR_STATUS_ENGINE_IDENTITY_CHANGED")
    return (owners[0], _process_ticks(owners[0])) if owners else None


def _view(state: str, *, observed: str | None = None, age_ms: int | None = None,
          rest_remaining_ms: int | None = None) -> dict[str, Any]:
    rest = ("FRESH" if rest_remaining_ms else "EXPIRED") if state == "OBSERVING" else "UNAVAILABLE"
    return {"state": state, "mode": "OBSERVATION_ONLY", "observed_at": observed,
            "age_ms": age_ms, "rest_state": rest, "rest_remaining_ms": rest_remaining_ms,
            "trading_enabled": False, "financial_authority": False, "message": _MESSAGES[state]}


def read_monitor_status(data: Path, profiles: AccountProfileStore | None, *,
                        now: datetime | None = None) -> dict[str, Any]:
    """Read the original saved scope twice; do not touch credentials or refresh a packet."""
    try:
        if profiles is None or (snapshot := profiles.load()) is None:
            return _view("CHECK_REQUIRED")
        expected = snapshot.resolve_expectations(supported_policies=_POLICIES)
        try:
            value = _read(_path(data, expected._uid))
        except FileNotFoundError:
            return _view("NOT_STARTED")
        if (value["account_id"] != expected._uid or value["profile_id"] != expected.profile_id
                or value["profile_revision"] != expected.revision
                or value["expectations_sha256"] != _identity(expected)):
            return _view("CHECK_REQUIRED")
        wall = _utc(_now() if now is None else now)
        elapsed = wall - _parse(value["observed_at"])
        if elapsed < timedelta(0):
            return _view("CHECK_REQUIRED")
        age = elapsed.days * 86400000 + elapsed.seconds * 1000 + (elapsed.microseconds + 999) // 1000
        if age > 30000:
            return _view("STALE", observed=value["observed_at"], age_ms=age)
        owner = _kernel_owner(data, expected._uid, value)
        identity = (value["owner_pid"], value["owner_start_ticks"])
        if value["state"] == "STOPPED":
            if owner is not None:
                return _view("CHECK_REQUIRED")
        elif value["state"] in {"OBSERVING", "STARTING", "PASSIVE"}:
            if owner != identity or value["engine_lease_held"] is not True:
                return _view("CHECK_REQUIRED")
        elif owner is not None and (owner != identity or value["engine_lease_held"] is not True):
            return _view("CHECK_REQUIRED")
        current = profiles.load()
        if current is None or current.resolve_expectations(supported_policies=_POLICIES) != expected:
            return _view("CHECK_REQUIRED")
        final_wall = _utc(_now()) if now is None else wall
        if final_wall < wall:
            return _view("CHECK_REQUIRED")
        elapsed = final_wall - _parse(value["observed_at"])
        age = elapsed.days * 86400000 + elapsed.seconds * 1000 + (elapsed.microseconds + 999) // 1000
        if age > 30000:
            return _view("STALE", observed=value["observed_at"], age_ms=age)
        remaining = None
        if value["state"] == "OBSERVING":
            remaining = max(0, int((_parse(value["rest_expires_at"]) - final_wall).total_seconds() * 1000)) if value["rest_expires_at"] else 0
        return _view(value["state"], observed=value["observed_at"], age_ms=age, rest_remaining_ms=remaining)
    except (AccountProfileError, OSError, ValueError, TypeError, OverflowError, RecursionError, IndexError):
        return _view("CHECK_REQUIRED")


class NativeMonitorPublisher:
    """Best-effort CLI observer, serialized separately from every recovery database."""

    def __init__(self, data: Path, expected: AccountExpectations, worker: "ExecutionWorker", *,
                 clock: Callable[[], datetime] = _now, connected: bool = True) -> None:
        self.path, self.expected, self.worker = _path(data, expected._uid), expected, worker
        self.data = data.absolute()
        self.clock, self.connected, self.run_id = clock, connected, uuid4().hex
        self._lease, self._epoch = worker._lock, worker._lock.acquisition_id if worker._lock else 0
        self._pid, self._ticks = os.getpid(), _process_ticks(os.getpid())
        self._published, self._retired = False, False

    def _owns_lease(self) -> bool:
        lease = self._lease
        return (type(lease) is AccountWorkerLock and self.worker._lock is lease and lease.acquired
                and lease.acquisition_id == self._epoch
                and lease.matches_scope(account_id=self.expected._uid, environment="DEMO")
                and self.worker.scope.account_id == self.expected._uid and self.worker.scope.environment == "DEMO")

    def _publish(self, state: str, expires: datetime | None = None) -> bool:
        try:
            owned = self._owns_lease()
            if self._retired or not self._published and not owned:
                return False
            self.path.parent.mkdir(mode=0o700, exist_ok=True)
            _private(self.path.parent, directory=True)
            with AccountWorkerLock(self.path.parent, account_id=self.expected._uid, environment="DEMO"):
                try:
                    previous = _read(self.path)
                except (OSError, ValueError, TypeError, RecursionError):
                    if not owned:
                        return False
                    previous = None
                if self._published and previous is not None and previous["run_id"] != self.run_id:
                    self._retired = True
                    return False
                wall = _utc(self.clock())
                if expires is not None and expires <= wall:
                    expires = None
                lease = self._lease
                if lease is None:
                    return False
                engine_directory = lease.path.parent.absolute()
                if engine_directory == self.data:
                    kind = "DATA_LOCAL"
                elif engine_directory == self.data.parent / "locks":
                    kind = "SERVER_SHARED"
                else:
                    return False
                engine = _private(lease.path)
                value = {"schema_version": 1, "account_id": self.expected._uid, "environment": "DEMO",
                    "profile_id": self.expected.profile_id, "profile_revision": self.expected.revision,
                    "expectations_sha256": _identity(self.expected), "run_id": self.run_id,
                    "observed_at": _text(wall), "state": state,
                    "rest_expires_at": _text(expires) if expires else None,
                    "owner_pid": self._pid, "owner_start_ticks": self._ticks,
                    "engine_lock_kind": kind, "engine_device": engine.st_dev,
                    "engine_inode": engine.st_ino, "engine_lease_held": owned,
                    "mode": "OBSERVATION_ONLY", "trading_enabled": False, "financial_authority": False}
                encoded = json.dumps(value, separators=(",", ":")).encode()
                if len(encoded) > 4096:
                    return False
                descriptor, temporary = tempfile.mkstemp(prefix=".status-", dir=self.path.parent)
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        output.write(encoded)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, self.path)
                finally:
                    Path(temporary).unlink(missing_ok=True)
                self._published = True
            return True
        except Exception:  # noqa: BLE001 - optional status cannot interfere with shutdown or print private errors.
            return False

    def observe(self) -> bool:
        try:
            status = self.worker.monitoring_status
            if status.resources_retained:
                return self._publish("CLEANUP_UNCONFIRMED")
            if status.trading_enabled is not False or status.credit_authorized is not False:
                return self._publish("CHECK_REQUIRED")
            if status.phase == "PASSIVE":
                return self._publish("STARTING" if self.connected else "PASSIVE")
            if status.phase != "MONITORING" or not self._owns_lease():
                return self._publish("CHECK_REQUIRED")
            expires = None
            if status.fresh_read_authority is True:
                session, wall = self.worker.session, _utc(self.clock())
                identity = session._identity
                if identity is not None:
                    seconds = min(30.0, (identity.observed_at + session._max_age - wall).total_seconds(),
                        (identity.server_observed_at + session._max_age - wall).total_seconds(),
                        session._max_age.total_seconds() - (session._mono() - session._issued_mono))
                    if math.isfinite(seconds) and seconds > 0:
                        expires = wall + timedelta(milliseconds=int(seconds * 1000))
            return self._publish("OBSERVING", expires)
        except Exception:  # noqa: BLE001 - fixed observation only; no provider error or credential serialization.
            return self._publish("CHECK_REQUIRED")

    def finish(self, state: Literal["STOPPED", "CLEANUP_UNCONFIRMED"]) -> bool:
        return self._publish(state)
