"""Local readiness must never retain credentials or verify a provider."""

import os
from dataclasses import asdict

import pytest

FAKE_KEY = "fake-anthropic-key-for-tests-only"
MODEL = "claude-example-20260101"


def load(environ):
    from multi_exchange_trader.ai.config import load_anthropic_config

    return load_anthropic_config(environ)


@pytest.mark.parametrize("environ", [{}, {"ANTHROPIC_API_KEY": "  "}, {"ANTHROPIC_MODEL": ""}])
def test_absent_or_blank_configuration_is_not_registered(environ):
    result = load(environ)
    assert result.registered is False
    assert result.status == "NOT_CONFIGURED"
    assert result.model_id is None


def test_key_presence_is_unverified_and_never_retained():
    result = load({"ANTHROPIC_API_KEY": FAKE_KEY, "ANTHROPIC_MODEL": MODEL})
    assert result.registered is True
    assert result.status == "REGISTERED_UNVERIFIED"
    assert result.model_id == MODEL
    assert FAKE_KEY not in repr(result)
    assert FAKE_KEY not in repr(asdict(result))


@pytest.mark.parametrize("environ", [
    {"ANTHROPIC_API_KEY": "line\nbreak"},
    {"ANTHROPIC_API_KEY": FAKE_KEY, "ANTHROPIC_MODEL": "sk-ant-private-model-mistake"},
    {"ANTHROPIC_API_KEY": FAKE_KEY, "ANTHROPIC_MODEL": "5.5 하이쿠"},
    {"ANTHROPIC_API_KEY": FAKE_KEY, "ANTHROPIC_MODEL": "claude-" + "x" * 200},
])
def test_invalid_values_fail_closed_without_echoing_values(environ):
    result = load(environ)
    assert result.status == "INVALID_CONFIGURATION"
    assert result.registered is (environ.get("ANTHROPIC_API_KEY") == FAKE_KEY)
    assert result.model_id is None
    for value in environ.values():
        assert value not in repr(result)


def test_explicit_file_allowlist_precedence_and_no_environment_export(tmp_path, monkeypatch):
    path = tmp_path / "credentials.env"
    path.write_text("# private config\nexport ANTHROPIC_API_KEY='file-fake-test-key'\n"
                    'ANTHROPIC_MODEL="claude-file-test"\nUNRELATED=test-only\n', encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("UNRELATED", raising=False)
    result = load({"ANTHROPIC_ENV_FILE": str(path), "ANTHROPIC_API_KEY": FAKE_KEY,
                   "ANTHROPIC_MODEL": MODEL})
    assert result.status == "REGISTERED_UNVERIFIED"
    assert result.model_id == MODEL
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert "UNRELATED" not in os.environ
    from_file = load({"ANTHROPIC_ENV_FILE": str(path), "ANTHROPIC_MODEL": " "})
    assert from_file.model_id == "claude-file-test"
    assert from_file.registered is True


@pytest.mark.parametrize("disabled", ["off", "OFF", "none", "0", ""])
def test_file_disabled_does_not_read_implicit_dotenv(tmp_path, monkeypatch, disabled):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=" + FAKE_KEY, encoding="utf-8")
    assert load({"ANTHROPIC_ENV_FILE": disabled}).status == "NOT_CONFIGURED"
    assert load({}).status == "NOT_CONFIGURED"


@pytest.mark.parametrize("content", [
    b"ANTHROPIC_API_KEY='fake-private-unclosed\n",
    b"ANTHROPIC_MODEL\n",
    b"ANTHROPIC_API_KEY=one\nANTHROPIC_API_KEY=two\n",
    b"ANTHROPIC_MODEL=claude-${PRIVATE}\n",
    b"ANTHROPIC_API_KEY=fake\x00private\n",
    b"ANTHROPIC_API_KEY=${FAKE_PRIVATE_VALUE}\n",
    b"#" * 16385,
    b"\xff",
])
def test_malformed_file_is_redacted_invalid_configuration(tmp_path, content):
    path = tmp_path / "fake-private-file.env"
    path.write_bytes(content)
    result = load({"ANTHROPIC_ENV_FILE": str(path)})
    assert result.status == "INVALID_CONFIGURATION"
    assert result.model_id is None
    assert result.registered is False
    assert str(path) not in repr(result)
    assert "private" not in repr(result)


def test_explicit_missing_or_directory_file_is_invalid(tmp_path):
    for path in [tmp_path / "missing.env", tmp_path]:
        assert load({"ANTHROPIC_ENV_FILE": str(path)}).status == "INVALID_CONFIGURATION"


def test_symlink_file_is_rejected(tmp_path):
    target = tmp_path / "target.env"
    target.write_text("ANTHROPIC_API_KEY=" + FAKE_KEY, encoding="utf-8")
    link = tmp_path / "link.env"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symlink creation unavailable on this host")
    assert load({"ANTHROPIC_ENV_FILE": str(link)}).status == "INVALID_CONFIGURATION"


def test_file_io_error_is_not_raised_or_represented(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("fake-private-path-and-secret")

    monkeypatch.setattr(os, "open", fail)
    result = load({"ANTHROPIC_ENV_FILE": "fake-credentials.env"})
    assert result.status == "INVALID_CONFIGURATION"
    assert "fake-private" not in repr(result)


@pytest.mark.parametrize("model", [
    "claude-fake-review-key-suffix",
    "claude-prefix-claude-fake-review-key",
    "claude-prefix-claude-fake-review-key-suffix",
])
@pytest.mark.parametrize("override_file_key", [False, True])
def test_model_containing_any_source_key_is_never_retained(tmp_path, model, override_file_key):
    file_key = "claude-fake-review-key"
    env_key = "different-fake-environment-key" if override_file_key else file_key
    environ = {"ANTHROPIC_API_KEY": env_key, "ANTHROPIC_MODEL": model}
    if override_file_key:
        path = tmp_path / "credentials.env"
        path.write_text(f"ANTHROPIC_API_KEY={file_key}\nANTHROPIC_MODEL={model}\n",
                        encoding="utf-8")
        environ = {"ANTHROPIC_ENV_FILE": str(path), "ANTHROPIC_API_KEY": env_key}
    result = load(environ)
    assert result.status == "INVALID_CONFIGURATION"
    assert result.registered is True
    assert result.model_id is None
    for secret in (file_key, env_key):
        assert secret not in repr(result)
        assert secret not in repr(asdict(result))
