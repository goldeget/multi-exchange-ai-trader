"""Private observation status cannot enable trading or replace live account evidence."""

import asyncio
import importlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from multi_exchange_trader.account_profiles import AccountProfileStore
from multi_exchange_trader.execution.ownership import AccountWorkerLock
from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.monitoring_status import NativeMonitorPublisher, read_monitor_status

NOW = datetime(2026, 11, 4, 10, tzinfo=UTC)
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Server observation uses read-only Linux UID lock metadata")


@pytest.fixture
def status_core(tmp_path: Path) -> Any:
    fixtures: Any = importlib.import_module("account_profiles.test_store")
    profiles = AccountProfileStore(tmp_path / "private" / "profiles.json")
    fixtures.write(profiles.path, fixtures.payload(complete=True))
    snapshot = profiles.load()
    assert snapshot is not None
    expected = snapshot.resolve_expectations(supported_policies=frozenset({fixtures.POLICY}))
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    lease = AccountWorkerLock(data, account_id=expected._uid, environment="DEMO")
    lease.acquire()
    wall = [NOW]
    status = SimpleNamespace(phase="MONITORING", resources_retained=False,
        fresh_read_authority=False, trading_enabled=False, credit_authorized=False)
    worker = SimpleNamespace(_lock=lease, scope=ProtectionScope(expected._uid, "DEMO"),
        monitoring_status=status, session=SimpleNamespace())
    publisher = NativeMonitorPublisher(data, expected, worker, clock=lambda: wall[0])
    yield SimpleNamespace(data=data, profiles=profiles, expected=expected, worker=worker,
                          publisher=publisher, wall=wall, fixtures=fixtures)
    lease.release()


def test_observing_stream_with_expired_rest_is_not_a_financial_permission(status_core: Any) -> None:
    c = status_core
    assert c.publisher.observe()
    value = read_monitor_status(c.data, c.profiles, now=NOW)
    assert value["state"] == "OBSERVING" and value["rest_state"] == "EXPIRED"
    assert value["trading_enabled"] is False and value["financial_authority"] is False
    assert value["mode"] == "OBSERVATION_ONLY"
    assert not (c.data / "native-monitor.json").exists()
    assert c.publisher.path.parent == c.data.parent / "monitor-status"


def test_stopped_and_cleanup_unconfirmed_are_distinct_and_stale_values_expire(status_core: Any) -> None:
    c = status_core
    assert c.publisher.observe()
    c.worker._lock.release()
    assert c.publisher.finish("CLEANUP_UNCONFIRMED")
    assert read_monitor_status(c.data, c.profiles, now=NOW)["state"] == "CLEANUP_UNCONFIRMED"
    assert c.publisher.finish("STOPPED")
    assert read_monitor_status(c.data, c.profiles, now=NOW)["state"] == "STOPPED"
    assert read_monitor_status(c.data, c.profiles, now=NOW + timedelta(seconds=31))["state"] == "STALE"


def test_late_previous_owner_cannot_overwrite_the_next_run(status_core: Any) -> None:
    c = status_core
    assert c.publisher.observe()
    c.worker._lock.release()
    replacement_lock = AccountWorkerLock(c.data, account_id=c.expected._uid, environment="DEMO")
    replacement_lock.acquire()
    replacement_worker = SimpleNamespace(**vars(c.worker))
    replacement_worker._lock = replacement_lock
    replacement = NativeMonitorPublisher(c.data, c.expected, replacement_worker, clock=lambda: NOW)
    try:
        assert replacement.observe()
        before = replacement.path.read_bytes()
        assert not c.publisher.finish("STOPPED")
        assert replacement.path.read_bytes() == before
        assert read_monitor_status(c.data, c.profiles, now=NOW)["state"] == "OBSERVING"
    finally:
        replacement_lock.release()


def test_saved_profile_revision_change_rejects_old_scope_publication(status_core: Any) -> None:
    c = status_core
    assert c.publisher.observe()
    changed = c.fixtures.payload(complete=True)
    changed["revision"] += 1
    c.fixtures.write(c.profiles.path, changed)
    assert read_monitor_status(c.data, c.profiles, now=NOW)["state"] == "CHECK_REQUIRED"


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("environment", "LIVE"), ("account_id", "777"),
    ("state", "RUNNING"), ("trading_enabled", True), ("financial_authority", True),
    ("rest_expires_at", "invalid"), ("unknown", "untrusted-extra"),
])
def test_malformed_or_foreign_status_never_becomes_healthy(
    status_core: Any, field: str, value: object,
) -> None:
    c = status_core
    assert c.publisher.observe()
    raw = json.loads(c.publisher.path.read_text())
    raw[field] = value
    c.publisher.path.write_text(json.dumps(raw))
    result = read_monitor_status(c.data, c.profiles, now=NOW)
    assert result["state"] == "CHECK_REQUIRED" and result["financial_authority"] is False
    assert "untrusted-extra" not in json.dumps(result)


def test_missing_corrupt_and_future_publications_are_not_running(status_core: Any) -> None:
    c = status_core
    assert read_monitor_status(c.data, c.profiles, now=NOW)["state"] == "NOT_STARTED"
    assert c.publisher.observe()
    assert read_monitor_status(c.data, c.profiles, now=NOW - timedelta(seconds=1))["state"] == "CHECK_REQUIRED"
    c.publisher.path.write_text("{")
    assert read_monitor_status(c.data, c.profiles, now=NOW)["state"] == "CHECK_REQUIRED"


def test_rest_expiry_is_independent_of_a_fresh_heartbeat(status_core: Any) -> None:
    c = status_core
    c.worker.monitoring_status.fresh_read_authority = True
    c.worker.session = SimpleNamespace(_identity=SimpleNamespace(
        observed_at=NOW - timedelta(seconds=29), server_observed_at=NOW - timedelta(seconds=29)),
        _max_age=timedelta(seconds=30), _issued_mono=10.0, _mono=lambda: 39.0)
    assert c.publisher.observe()
    first = read_monitor_status(c.data, c.profiles, now=NOW)
    later = read_monitor_status(c.data, c.profiles, now=NOW + timedelta(seconds=2))
    assert first["state"] == later["state"] == "OBSERVING"
    assert first["rest_state"] == "FRESH" and later["rest_state"] == "EXPIRED"


@pytest.mark.skipif(os.name != "posix", reason="Private POSIX server permission contract")
def test_unsafe_permissions_or_symlink_cannot_be_read(status_core: Any) -> None:
    c = status_core
    assert c.publisher.observe()
    original = c.publisher.path
    original.chmod(0o644)
    assert read_monitor_status(c.data, c.profiles, now=NOW)["state"] == "CHECK_REQUIRED"

    original.chmod(0o600)
    renamed = original.with_name("old.json")
    original.rename(renamed)
    original.symlink_to(renamed)
    assert read_monitor_status(c.data, c.profiles, now=NOW)["state"] == "CHECK_REQUIRED"

async def test_actual_cli_lifecycle_publishes_packets_consumed_by_original_status_reader(
    status_core: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = status_core
    cli: Any = importlib.import_module("multi_exchange_trader.execution.worker_cli")
    stops: list[asyncio.Event] = []
    observed: list[str] = []
    original_observe = NativeMonitorPublisher.observe
    session_closed: list[bool] = []

    class Session:
        async def authenticate(self, **_: object) -> object:
            return object()

        async def aclose(self) -> None:
            session_closed.append(True)

    async def start() -> None:
        c.worker.monitoring_status.phase = "PASSIVE"

    async def connect() -> None:
        c.worker.monitoring_status.phase = "MONITORING"

    async def stop() -> None:
        c.worker._lock.release()
        c.worker.monitoring_status.phase = "STOPPED"

    def observe(publisher: NativeMonitorPublisher) -> bool:
        result = original_observe(publisher)
        state = read_monitor_status(c.data, c.profiles)["state"]
        observed.append(state)
        if state == "OBSERVING":
            stops[-1].set()
        return result

    c.worker.session = Session()
    c.worker.start_passive, c.worker.connect_monitoring, c.worker.stop = start, connect, stop
    c.worker._node_task = None
    monkeypatch.setattr(cli, "BybitDemoAccountSession", lambda **_: c.worker.session)
    monkeypatch.setattr(cli, "ExecutionWorker", lambda **_: c.worker)
    monkeypatch.setattr(cli, "resolve_worker_expectations", lambda **_: c.expected)
    monkeypatch.setattr(cli, "_load_shared_policy_target_v2", lambda _: None)
    monkeypatch.setattr(cli, "RuntimePolicyBook", lambda _: object())
    monkeypatch.setattr(cli.CredentialResolver, "from_environment", lambda **_: object())
    monkeypatch.setattr(cli, "require_demo_credentials", lambda **_: ("synthetic", "synthetic"))
    monkeypatch.setattr(cli, "install_signal_handlers", lambda _loop, event: stops.append(event))
    monkeypatch.setattr(asyncio.get_running_loop(), "remove_signal_handler", lambda _: True)
    monkeypatch.setattr(NativeMonitorPublisher, "observe", observe)
    args = SimpleNamespace(uid=None, parent_uid=None, is_master=None, directory=str(c.data),
        lock_directory=None, policy_db=str(c.data / "policy.sqlite3"), symbol=["BTCUSDT"], connect_monitoring=True)
    await asyncio.wait_for(cli._run(args), timeout=2)
    assert observed == ["STARTING", "OBSERVING"]
    assert read_monitor_status(c.data, c.profiles)["state"] == "STOPPED"
    assert session_closed == [True]


def test_previous_stop_before_next_first_publication_cannot_report_account_stopped(status_core: Any) -> None:
    c = status_core
    assert c.publisher.observe()
    c.worker._lock.release()
    next_owner = AccountWorkerLock(c.data, account_id=c.expected._uid, environment="DEMO")
    next_owner.acquire()
    try:
        # A genuine next UID owner exists but has not published any status yet.
        assert c.publisher.finish("STOPPED")
        assert read_monitor_status(c.data, c.profiles, now=NOW)["state"] == "CHECK_REQUIRED"
        assert next_owner.acquired
    finally:
        next_owner.release()


def test_unavailable_kernel_identity_is_not_treated_as_an_unlocked_or_healthy_engine(
    status_core: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader import monitoring_status

    c = status_core
    assert c.publisher.observe()

    def unavailable(*_: object) -> None:
        raise OSError("synthetic kernel metadata unavailable")

    monkeypatch.setattr(monitoring_status, "_kernel_owner", unavailable)
    assert read_monitor_status(c.data, c.profiles, now=NOW)["state"] == "CHECK_REQUIRED"


def test_kernel_lookup_time_cannot_refresh_an_old_heartbeat(
    status_core: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader import monitoring_status

    c = status_core
    assert c.publisher.observe()
    original_owner = monitoring_status._kernel_owner
    wall = [NOW]

    def slow_lookup(*args: Any) -> Any:
        result = original_owner(*args)
        wall[0] += timedelta(seconds=31)
        return result

    monkeypatch.setattr(monitoring_status, "_now", lambda: wall[0])
    monkeypatch.setattr(monitoring_status, "_kernel_owner", slow_lookup)
    assert read_monitor_status(c.data, c.profiles)["state"] == "STALE"
