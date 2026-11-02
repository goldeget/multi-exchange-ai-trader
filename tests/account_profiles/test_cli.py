"""Profile registration/status never expose identity or touch credentials/network."""

import importlib
import json
import socket
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from .test_store import UID


@pytest.fixture
def cli() -> ModuleType:
    if importlib.util.find_spec("multi_exchange_trader.account_profiles_cli") is None:
        pytest.fail("Account profile CLI has not been implemented")
    return importlib.import_module("multi_exchange_trader.account_profiles_cli")


def test_default_cli_no_io(cli: ModuleType, monkeypatch: pytest.MonkeyPatch,
                           capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(Path, "lstat", lambda *a, **k: pytest.fail("default probed files"))
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("network"))
    assert cli.main([]) == 0
    assert capsys.readouterr().out.strip() == "DRY_CHECK"


def test_explicit_cli_safe_pending_status_and_errors(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    credentials = importlib.import_module("multi_exchange_trader.credentials")
    monkeypatch.setattr(credentials.CredentialStore, "load",
                        lambda *a: pytest.fail("vault touched"))
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("network"))
    path = tmp_path / "private" / "profile.json"
    prefix = ["--profile-store", str(path)]
    assert cli.main([*prefix, "status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"state": "NO_PROFILE"}
    assert not path.parent.exists()
    assert cli.main([*prefix, "register-uid", "--uid", UID]) == 0
    result = json.loads(capsys.readouterr().out)
    assert set(result) == {"schema", "revision", "profile_id", "state", "basis"}
    assert result["state"] == "PENDING"
    assert cli.main([*prefix, "status"]) == 0
    printed = capsys.readouterr()
    assert json.loads(printed.out) == result
    assert UID not in printed.out and str(path) not in printed.out
    assert cli.main([*prefix, "register-uid", "--uid", UID]) == 1
    assert capsys.readouterr().err.strip() == "REVISION_CONFLICT"
    for arguments in (["--uid", UID], ["register-uid", "--uid", "invalid-secret"],
                      ["--profile-store", "relative-secret", "status"],
                      [*prefix, "register-uid", "--uid", UID, "--unexpected-secret"],
                      [*prefix, "register-uid", "--uid", UID, "--expected-revision", "raw-secret"]):
        assert cli.main(arguments) == 1
        output = capsys.readouterr()
        assert UID not in output.out + output.err
        assert "secret" not in output.out + output.err


def test_enrollment_cli_requires_named_policy_and_intent_before_keys(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from multi_exchange_trader.credential_resolution import CredentialResolver

    monkeypatch.setattr(CredentialResolver, "from_environment",
                        lambda **k: pytest.fail("keys before intent/profile"))
    prefix = ["--profile-store", str(tmp_path / "profile.json"), "enroll-observed"]
    for extra in ([], ["--policy", "uid-anchored-adoption-v1"],
                  ["--policy", "wrong-private-policy", "--adopt-observed-relationship"]):
        assert cli.main(prefix + extra) == 1
        assert "private" not in capsys.readouterr().err


def test_enrollment_cli_performs_separate_observe_then_explicit_adopt_in_alive_session(
    cli: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from multi_exchange_trader.account_enrollment import BybitDemoEnrollmentSession
    from multi_exchange_trader.account_profiles import AccountProfileResult

    calls: list[str] = []
    handle = object()

    class Owner:
        async def observe(self) -> object:
            calls.append("observe")
            return handle

        def adopt(self, candidate: object, **kwargs: Any) -> AccountProfileResult:
            assert candidate is handle
            assert kwargs == {"policy": "uid-anchored-adoption-v1", "adopt_observed_relationship": True}
            calls.append("adopt")
            return AccountProfileResult(2, "83a05b72-f883-451a-9e58-639bd9909912", "COMPLETE",
                                        "PROVIDER_OBSERVED_OPERATOR_ADOPTED")

        async def aclose(self) -> None:
            calls.append("close")

    monkeypatch.setattr(BybitDemoEnrollmentSession, "from_environment", lambda **k: Owner())
    assert cli.main(["--profile-store", str(tmp_path / "profile.json"), "enroll-observed",
        "--policy", "uid-anchored-adoption-v1", "--adopt-observed-relationship"]) == 0
    assert calls == ["observe", "adopt", "close"]
    assert json.loads(capsys.readouterr().out)["state"] == "COMPLETE"
