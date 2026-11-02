"""Explicit one-time import cannot import activation, identity or executable input."""

import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def credentials() -> ModuleType:
    if importlib.util.find_spec("multi_exchange_trader.credentials") is None:
        pytest.fail("Managed credential import is not implemented")
    return importlib.import_module("multi_exchange_trader.credentials")


def source_file(tmp_path: Path, content: str) -> Path:
    source = tmp_path / "synthetic.env"
    source.write_text(content, encoding="utf-8")
    source.chmod(0o600)
    return source


def test_import_survives_unavailable_source_and_fresh_load(
    credentials: ModuleType, tmp_path: Path,
) -> None:
    source = source_file(tmp_path, "BYBIT_DEMO_API_KEY=synthetic-demo\n"
                         "BYBIT_DEMO_API_SECRET='synthetic-secret'\n")
    original = source.read_bytes()
    path = tmp_path / "private" / "store"
    credentials.import_env(source, credentials.CredentialStore(path), expected_revision=None)
    assert source.read_bytes() == original
    source.rename(tmp_path / "unavailable")
    assert credentials.CredentialStore(path).load().bybit_pair("demo") == (
        "synthetic-demo", "synthetic-secret")
    assert os.environ.get("BYBIT_DEMO_API_KEY") is None


def test_import_does_not_import_activation_or_identity_claims(
    credentials: ModuleType, tmp_path: Path,
) -> None:
    source = source_file(tmp_path, "BYBIT_DEMO_API_KEY=synthetic-demo\n"
                         "BYBIT_DEMO_API_SECRET=synthetic-secret\nBYBIT_DEMO=true\n"
                         "LIVE_TRADING_ENABLED=true\nBYBIT_EXPECTED_UID=123456\n"
                         "BYBIT_API_PERMISSIONS=SpotTrade\nAI_PROVIDER_DISPATCH_ENABLED=true\n"
                         "PAPER_DB_PATH=/tmp/something\n")
    store = credentials.CredentialStore(tmp_path / "private" / "store")
    result = credentials.import_env(source, store, expected_revision=None)
    assert result.namespaces == ("BYBIT_DEMO",)
    assert store.load().ai_values() == {}


@pytest.mark.parametrize("content", [
    "ANTHROPIC_API_KEY=synthetic-key\nANTHROPIC_API_KEY=other-key\n",
    "this-is-not-assignment\n",
    "ANTHROPIC_API_KEY='unterminated\n",
    "OPENROUTER_MODEL=vendor/model\t\n",
    "ANTHROPIC_API_KEY=synthetic\x00\n",
    "# comment\n" * 257,
    "#" + "x" * 16384,
    "ANTHROPIC_API_KEY=$(do-not-execute)\n",
])
def test_import_rejects_malformed_duplicate_control_and_size(
    credentials: ModuleType, tmp_path: Path, content: str,
) -> None:
    source = source_file(tmp_path, content)
    store = credentials.CredentialStore(tmp_path / "private" / "store")
    with pytest.raises(credentials.CredentialStoreError):
        credentials.import_env(source, store, expected_revision=None)
    assert not store.path.exists()


def test_provider_validation_applies_to_both_namespaces(
    credentials: ModuleType, tmp_path: Path,
) -> None:
    source = source_file(tmp_path, "AI_PROVIDER=OPENROUTER\n"
                         "OPENROUTER_API_KEY=sk-or-v1-synthetic1234\n"
                         "OPENROUTER_MODEL=vendor/model\nANTHROPIC_API_KEY=bad key\n")
    store = credentials.CredentialStore(tmp_path / "private" / "store")
    with pytest.raises(credentials.CredentialStoreError, match="INVALID_VALUES"):
        credentials.import_env(source, store, expected_revision=None)


def test_redaction_in_repr_cli_and_failure(credentials: ModuleType, tmp_path: Path) -> None:
    source = source_file(tmp_path, "BYBIT_DEMO_API_KEY=synthetic-demo\n"
                         "BYBIT_DEMO_API_SECRET=synthetic-secret\n")
    path = tmp_path / "private" / "store"
    command = [sys.executable, "-m", "multi_exchange_trader.credentials_cli", "--import-env",
               str(source), "--store", str(path)]
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[2] / "src"))
    completed = subprocess.run(command, capture_output=True, text=True, check=False, env=env)
    assert completed.returncode == 0, completed.stderr
    assert '"revision": 1' in completed.stdout
    assert '"BYBIT_DEMO"' in completed.stdout
    failed = subprocess.run(command, capture_output=True, text=True, check=False, env=env)
    assert failed.returncode == 1
    assert "REVISION_CONFLICT" in failed.stderr
    output = completed.stdout + completed.stderr + failed.stdout + failed.stderr
    assert "synthetic" not in output
    assert str(tmp_path) not in output


def test_cli_does_not_echo_unknown_secret_arguments(tmp_path: Path) -> None:
    if importlib.util.find_spec("multi_exchange_trader.credentials_cli") is None:
        pytest.fail("Managed credential CLI is not implemented")
    completed = subprocess.run([sys.executable, "-m", "multi_exchange_trader.credentials_cli",
                                "--api-key", "synthetic-secret"],
                               capture_output=True, text=True, check=False,
                               env=dict(os.environ, PYTHONPATH=str(Path(__file__).parents[2] / "src")))
    assert completed.returncode != 0
    assert "synthetic" not in completed.stdout + completed.stderr
