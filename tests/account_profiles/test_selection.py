"""Saved startup selection fails before keys; fresh sessions pin a frozen source context."""

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from multi_exchange_trader.account_profiles import AccountProfileError, AccountProfileStore
from multi_exchange_trader.execution import worker_cli
from multi_exchange_trader.worker_account_selection import resolve_worker_expectations

from .test_store import PARENT, POLICY, PROFILE, UID, payload, write


def select(path: Path, **kwargs: Any) -> Any:
    return resolve_worker_expectations(uid=kwargs.get("uid"), parent_uid=kwargs.get("parent_uid"),
        is_master=kwargs.get("is_master"), account_profile=kwargs.get("account_profile"),
        profile_store=str(path), environ={"TRADER_ACCOUNT_PROFILE_FILE": "off"})


def test_complete_legacy_bypasses_profile_location_and_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(AccountProfileStore, "load", lambda *a: pytest.fail("profile was read"))
    expected = select(tmp_path, uid=UID, parent_uid=PARENT, is_master="false")
    assert (expected._uid, expected._parent_uid, expected._is_master) == (UID, PARENT, False)
    assert expected.basis == "EXPLICIT_LEGACY_EXPECTATIONS"
    assert UID not in repr(expected) and PARENT not in repr(expected)


@pytest.mark.parametrize("flags", [
    {"uid": UID}, {"parent_uid": PARENT}, {"is_master": "false"},
    {"uid": UID, "parent_uid": PARENT}, {"uid": UID, "is_master": "false"},
    {"uid": UID, "parent_uid": PARENT, "is_master": "false", "account_profile": PROFILE},
])
def test_partial_or_mixed_identity_rejects_before_profile_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: Any,
) -> None:
    monkeypatch.setattr(AccountProfileStore, "load", lambda *a: pytest.fail("profile was read"))
    with pytest.raises(AccountProfileError):
        select(tmp_path, **flags)


def test_selected_profile_requires_exact_uuid_and_preserves_source(tmp_path: Path) -> None:
    path = tmp_path / "private" / "profile.json"
    write(path, payload(complete=True))
    expected = select(path, account_profile=PROFILE)
    assert (expected.profile_id, expected.revision, expected.basis, expected.policy) == (
        PROFILE, 1, "PROVIDER_OBSERVED_OPERATOR_ADOPTED", POLICY)
    assert expected.source == "BYBIT_DEMO_QUERY_API"
    with pytest.raises(AccountProfileError):
        select(path, account_profile="83a05b72-f883-451a-9e58-639bd9909913")
    with pytest.raises(AccountProfileError):
        select(path, account_profile=PROFILE.upper())


@pytest.mark.parametrize("kind", ["absent", "pending", "unknown-policy", "corrupt"])
async def test_bad_saved_profile_stops_before_credential_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    path = tmp_path / "private" / "profile.json"
    if kind != "absent":
        data = payload(complete=kind != "pending")
        if kind == "unknown-policy":
            data["profiles"][0]["relationship"]["policy"] = "unsupported-v999"
        write(path, data)
        if kind == "corrupt":
            path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(worker_cli.CredentialResolver, "from_environment",
                        lambda **k: pytest.fail("keys resolved before profile"))
    args = argparse.Namespace(uid=None, parent_uid=None, is_master=None,
                              account_profile=None, profile_store=str(path))
    with pytest.raises(AccountProfileError):
        await worker_cli._run(args)


async def test_worker_start_and_refresh_keep_one_private_saved_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "private" / "profile.json"
    write(path, payload(complete=True))
    monkeypatch.setattr(worker_cli.CredentialResolver, "from_environment", lambda **k: object())
    monkeypatch.setattr(worker_cli, "require_demo_credentials", lambda **k: ("fake-key", "fake-secret"))
    calls: list[dict[str, Any]] = []

    class Session:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def authenticate(self, **kwargs: Any) -> object:
            calls.append(kwargs)
            return object()

        async def aclose(self) -> None:
            pass

    async def serve(worker: Any, *, refresh: Any, **kwargs: Any) -> None:
        assert worker.scope.account_id == UID
        changed = payload(complete=True)
        changed["profiles"][0]["relationship"]["parent_uid"] = "777"
        write(path, changed)
        await refresh()

    monkeypatch.setattr(worker_cli, "BybitDemoAccountSession", Session)
    monkeypatch.setattr(worker_cli, "ExecutionWorker", lambda **k: SimpleNamespace(**k))
    monkeypatch.setattr(worker_cli, "RuntimePolicyBook", lambda p: object())
    monkeypatch.setattr(worker_cli, "install_signal_handlers", lambda *a: None)
    monkeypatch.setattr(worker_cli, "serve_monitor", serve)
    # Exercise startup/refresh on Windows without claiming Linux signal/native acceptance.
    monkeypatch.setattr(asyncio.get_running_loop(), "remove_signal_handler", lambda sig: True)
    args = argparse.Namespace(uid=None, parent_uid=None, is_master=None, account_profile=PROFILE,
        profile_store=str(path), directory=str(tmp_path), lock_directory=None,
        policy_db=str(tmp_path / "policy.sqlite3"), symbol=["BTCUSDT"], connect_monitoring=False)
    await worker_cli._run(args)
    assert calls == [{"expected_uid": UID, "expected_parent_uid": PARENT,
                      "expected_is_master": False}] * 2
    await worker_cli._run(args)
    assert calls[-2:] == [{"expected_uid": UID, "expected_parent_uid": "777",
                          "expected_is_master": False}] * 2


def test_unsupported_platform_and_default_never_read_profile(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(AccountProfileStore, "load", lambda *a: pytest.fail("profile was read"))
    assert worker_cli.main([]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "DRY_CHECK"
    monkeypatch.setattr(worker_cli.sys, "platform", "win32")
    with pytest.raises(SystemExit):
        worker_cli.main(["--run-monitor", "--account-profile", PROFILE, "--profile-store", "private"])
    assert "private" not in capsys.readouterr().err


def test_worker_parser_error_never_echoes_identity_or_path(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        worker_cli.main(["--is-master", "raw-private-value", "--profile-store", "private-path"])
    assert "raw-private-value" not in capsys.readouterr().err
