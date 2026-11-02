"""Profile registration/status never expose identity or touch credentials/network."""

import importlib
import json
import socket
from pathlib import Path
from types import ModuleType

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
