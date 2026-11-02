"""Explicit provider registration uses fake credentials and local temp files only."""

import os
from dataclasses import FrozenInstanceError, asdict

import pytest

OR_KEY = "sk-or-v1-fake-openrouter-test-only"
ANT_KEY = "fake-anthropic-test-only"
MODEL = "anthropic/claude-haiku-5.5"


def load(environ):
    from multi_exchange_trader.ai.provider_config import load_ai_provider_config

    return load_ai_provider_config(environ)


def test_explicit_openrouter_is_key_free_frozen_and_unverified():
    result = load({"AI_PROVIDER": "OPENROUTER", "OPENROUTER_API_KEY": OR_KEY,
                   "OPENROUTER_MODEL": MODEL, "ANTHROPIC_API_KEY": ANT_KEY})
    assert asdict(result) == {"provider": "OPENROUTER", "registered": True,
                              "status": "REGISTERED_UNVERIFIED", "model_id": MODEL}
    assert OR_KEY not in repr(result)
    assert ANT_KEY not in repr(result)
    with pytest.raises(FrozenInstanceError):
        result.registered = False


@pytest.mark.parametrize("length,registered,status", [
    (489, True, "REGISTERED_UNVERIFIED"), (490, False, "INVALID_CONFIGURATION")
])
def test_openrouter_key_length_matches_metadata_client_boundary(length, registered, status):
    key = "sk-or-v1-" + "x" * (length - 9)
    result = load({"AI_PROVIDER": "OPENROUTER", "OPENROUTER_API_KEY": key,
                   "OPENROUTER_MODEL": MODEL})
    assert result.registered is registered
    assert result.status == status
    assert key not in repr(result)


@pytest.mark.parametrize("key,expected", [("", "NOT_CONFIGURED"), (ANT_KEY, "INVALID_CONFIGURATION"),
                                        ("sk-or-v1-line\nbreak", "INVALID_CONFIGURATION")])
def test_selected_openrouter_never_falls_back_to_anthropic(key, expected):
    result = load({"AI_PROVIDER": "OPENROUTER", "OPENROUTER_API_KEY": key,
                   "ANTHROPIC_API_KEY": ANT_KEY, "ANTHROPIC_MODEL": "claude-test"})
    assert result.provider == "OPENROUTER"
    assert result.registered is False
    assert result.status == expected


@pytest.mark.parametrize("selector", ["OTHER", "openrouter", "sk-or-v1-fake-private", "x\ny"])
def test_unknown_selector_fails_closed_with_safe_label(selector):
    result = load({"AI_PROVIDER": selector, "ANTHROPIC_API_KEY": ANT_KEY,
                   "OPENROUTER_API_KEY": OR_KEY})
    assert asdict(result) == {"provider": "ANTHROPIC", "registered": False,
                              "status": "INVALID_CONFIGURATION", "model_id": None}
    assert selector not in repr(result)


def test_legacy_default_and_explicit_anthropic_remain_compatible(tmp_path):
    from multi_exchange_trader.ai.config import load_anthropic_config

    path = tmp_path / "legacy.env"
    path.write_text(f"ANTHROPIC_API_KEY={ANT_KEY}\nANTHROPIC_MODEL=claude-test\n", encoding="utf-8")
    for source in ({}, {"ANTHROPIC_API_KEY": ANT_KEY, "ANTHROPIC_MODEL": "claude-test"},
                   {"ANTHROPIC_ENV_FILE": str(path)}):
        assert asdict(load(source)) == {"provider": "ANTHROPIC", **asdict(load_anthropic_config(source))}
    explicit = load({"AI_PROVIDER": "ANTHROPIC", "ANTHROPIC_API_KEY": ANT_KEY,
                     "ANTHROPIC_MODEL": "claude-test", "OPENROUTER_API_KEY": OR_KEY})
    assert explicit.registered is True
    assert explicit.model_id == "claude-test"


def test_shared_file_selector_and_nonblank_overrides_without_export(tmp_path, monkeypatch):
    path = tmp_path / "fake-shared.env"
    path.write_text("# local fake values\nexport AI_PROVIDER='OPENROUTER'\n"
                    f"OPENROUTER_API_KEY={OR_KEY}\nOPENROUTER_MODEL={MODEL}\n"
                    "BYBIT_API_KEY=ignored-fake-value\n", encoding="utf-8-sig")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    result = load({"AI_ENV_FILE": str(path), "AI_PROVIDER": " ", "OPENROUTER_MODEL": " "})
    assert result.provider == "OPENROUTER"
    assert result.model_id == MODEL
    assert result.registered is True
    overridden = load({"AI_ENV_FILE": str(path), "AI_PROVIDER": "ANTHROPIC",
                       "ANTHROPIC_API_KEY": ANT_KEY, "ANTHROPIC_MODEL": "claude-override"})
    assert overridden.provider == "ANTHROPIC"
    assert overridden.model_id == "claude-override"
    assert "OPENROUTER_API_KEY" not in os.environ
    assert "AI_PROVIDER" not in os.environ


@pytest.mark.parametrize("disabled", ["", "off", "OFF", "none", "0"])
def test_disabled_ai_file_prevents_implicit_and_legacy_search(tmp_path, monkeypatch, disabled):
    path = tmp_path / ".env"
    path.write_text(f"AI_PROVIDER=OPENROUTER\nOPENROUTER_API_KEY={OR_KEY}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert load({"AI_ENV_FILE": disabled, "ANTHROPIC_ENV_FILE": str(path)}).registered is False
    assert load({}).registered is False


def test_explicit_openrouter_does_not_read_legacy_file(tmp_path):
    result = load({"AI_PROVIDER": "OPENROUTER", "ANTHROPIC_ENV_FILE": str(tmp_path / "missing"),
                   "OPENROUTER_API_KEY": OR_KEY})
    assert result.status == "REGISTERED_UNVERIFIED"


def test_legacy_file_cannot_implicitly_select_another_provider(tmp_path):
    path = tmp_path / "legacy.env"
    path.write_text(f"AI_PROVIDER=OPENROUTER\nOPENROUTER_API_KEY={OR_KEY}\n"
                    f"ANTHROPIC_API_KEY={ANT_KEY}\nANTHROPIC_MODEL=claude-test\n", encoding="utf-8")
    result = load({"ANTHROPIC_ENV_FILE": str(path)})
    assert result.provider == "ANTHROPIC"
    assert result.registered is True
    assert result.model_id == "claude-test"


@pytest.mark.parametrize("extra", ["AI_PROVIDER=OPENROUTER\nAI_PROVIDER=ANTHROPIC\n",
                                   "OPENROUTER_MODEL='unclosed\n"])
def test_only_shared_file_validates_fields_ignored_by_legacy_parser(tmp_path, extra):
    from multi_exchange_trader.ai.config import load_anthropic_config

    path = tmp_path / "fake-provider-settings.env"
    path.write_text(f"ANTHROPIC_API_KEY={ANT_KEY}\nANTHROPIC_MODEL=claude-test\n" + extra,
                    encoding="utf-8")
    source = {"ANTHROPIC_ENV_FILE": str(path)}
    expected = load_anthropic_config(source)
    assert expected.status == "REGISTERED_UNVERIFIED"
    assert asdict(load(source)) == {"provider": "ANTHROPIC", **asdict(expected)}
    shared = load({"AI_ENV_FILE": str(path)})
    assert shared.status == "INVALID_CONFIGURATION"
    assert shared.registered is False
    assert shared.model_id is None


@pytest.mark.parametrize("prefix", ["", "export "])
@pytest.mark.parametrize("quoted", [False, True])
def test_shared_file_model_rejects_trailing_tab_before_whitespace_normalization(
    tmp_path, prefix, quoted
):
    path = tmp_path / "fake-openrouter.env"
    model = f'"{MODEL}\t"' if quoted else MODEL + "\t"
    path.write_text(f"AI_PROVIDER=OPENROUTER\nOPENROUTER_API_KEY={OR_KEY}\n"
                    f"{prefix}OPENROUTER_MODEL={model}\n", encoding="utf-8")
    result = load({"AI_ENV_FILE": str(path)})
    assert result.status == "INVALID_CONFIGURATION"
    assert result.registered is False
    assert result.model_id is None


@pytest.mark.parametrize("prefix", ["", "export "])
@pytest.mark.parametrize("control", ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e"])
def test_shared_file_model_rejects_trailing_control_line_separators(tmp_path, prefix, control):
    path = tmp_path / "fake-openrouter.env"
    path.write_bytes((f"AI_PROVIDER=OPENROUTER\nOPENROUTER_API_KEY={OR_KEY}\n"
                      f"{prefix}OPENROUTER_MODEL={MODEL}{control}\n").encode())
    result = load({"AI_ENV_FILE": str(path)})
    assert result.status == "INVALID_CONFIGURATION"
    assert result.registered is False
    assert result.model_id is None


@pytest.mark.parametrize("newline", ["\r", "\n", "\r\n"])
def test_shared_file_accepts_standard_line_endings_at_line_limit(tmp_path, newline):
    path = tmp_path / "fake-openrouter.env"
    lines = ["AI_PROVIDER=OPENROUTER", f"OPENROUTER_API_KEY={OR_KEY}",
             f"OPENROUTER_MODEL={MODEL}"] + ["# comment"] * 253
    path.write_bytes((newline.join(lines) + newline).encode())
    result = load({"AI_ENV_FILE": str(path)})
    assert result.status == "REGISTERED_UNVERIFIED"
    assert result.registered is True
    assert result.model_id == MODEL


@pytest.mark.parametrize("control", ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e"])
def test_legacy_file_retains_control_line_separator_behavior(tmp_path, control):
    from multi_exchange_trader.ai.config import load_anthropic_config

    path = tmp_path / "fake-legacy.env"
    path.write_bytes((f"ANTHROPIC_API_KEY={ANT_KEY}{control}"
                      "export ANTHROPIC_MODEL=claude-test\n").encode())
    source = {"ANTHROPIC_ENV_FILE": str(path)}
    expected = load_anthropic_config(source)
    assert expected.status == "REGISTERED_UNVERIFIED"
    assert asdict(load(source)) == {"provider": "ANTHROPIC", **asdict(expected)}
    assert load(source).model_id == "claude-test"


@pytest.mark.parametrize("model", ["https://example.test/model", "anthropic/model?key=x",
    "anthropic/../private", "anthropic/model/extra", "anthropic/", "/model", "anthropic/model\nsecret",
    "anthropic/" + "x" * 201, "anthropic/model#fragment", "anthropic/model%2fprivate"])
def test_unsafe_model_is_redacted(model):
    result = load({"AI_PROVIDER": "OPENROUTER", "OPENROUTER_API_KEY": OR_KEY,
                   "OPENROUTER_MODEL": model})
    assert result.status == "INVALID_CONFIGURATION"
    assert result.registered is True
    assert result.model_id is None
    assert model not in repr(result)


@pytest.mark.parametrize("control", ["\n", "\r", "\t", "\x00", "\x7f"])
def test_openrouter_model_rejects_boundary_control_characters(control):
    result = load({"AI_PROVIDER": "OPENROUTER", "OPENROUTER_API_KEY": OR_KEY,
                   "OPENROUTER_MODEL": MODEL + control})
    assert result.status == "INVALID_CONFIGURATION"
    assert result.model_id is None


def test_environment_key_and_model_override_file_values(tmp_path):
    path = tmp_path / "fake.env"
    path.write_text("AI_PROVIDER=OPENROUTER\nOPENROUTER_API_KEY=invalid-file-key\n"
                    "OPENROUTER_MODEL=anthropic/file-model\n", encoding="utf-8")
    result = load({"AI_ENV_FILE": str(path), "OPENROUTER_API_KEY": OR_KEY,
                   "OPENROUTER_MODEL": MODEL})
    assert result.status == "REGISTERED_UNVERIFIED"
    assert result.model_id == MODEL


def test_anthropic_model_rejects_other_provider_candidate_credentials(tmp_path):
    path = tmp_path / "fake.env"
    path.write_text("OPENROUTER_API_KEY=reflected-fake-key\n", encoding="utf-8")
    result = load({"AI_ENV_FILE": str(path), "ANTHROPIC_API_KEY": ANT_KEY,
                   "ANTHROPIC_MODEL": "claude-reflected-fake-key"})
    assert result.registered is True
    assert result.status == "INVALID_CONFIGURATION"
    assert result.model_id is None


@pytest.mark.parametrize("key_name", ["OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"])
@pytest.mark.parametrize("file_candidate", [False, True])
def test_model_rejects_any_candidate_key_even_when_overridden(tmp_path, key_name, file_candidate):
    secret = "fake-reflected-key"
    source = {"AI_PROVIDER": "OPENROUTER", "OPENROUTER_API_KEY": OR_KEY,
              "OPENROUTER_MODEL": "anthropic/model-" + secret}
    if file_candidate:
        path = tmp_path / "fake.env"
        path.write_text(f"{key_name}={secret}\n", encoding="utf-8")
        source["AI_ENV_FILE"] = str(path)
    else:
        source[key_name] = secret
    result = load(source)
    assert result.status == "INVALID_CONFIGURATION"
    assert result.model_id is None
    assert secret not in repr(result)


@pytest.mark.parametrize("content", [b"AI_PROVIDER=OPENROUTER\nAI_PROVIDER=ANTHROPIC\n",
    b"OPENROUTER_MODEL=a/b\nOPENROUTER_MODEL=c/d\n",
    b"ANTHROPIC_API_KEY=one\nANTHROPIC_API_KEY=two\n", b"OPENROUTER_API_KEY='unclosed\n",
    b"OPENROUTER_MODEL\n", b"#" * 16385, b"#\n" * 257, b"\xff", b"\x00"])
def test_malformed_or_oversized_file_fails_closed(tmp_path, content):
    path = tmp_path / "fake-private.env"
    path.write_bytes(content)
    result = load({"AI_ENV_FILE": str(path), "AI_PROVIDER": "OPENROUTER"})
    assert result.status == "INVALID_CONFIGURATION"
    assert result.registered is False
    assert result.model_id is None
    assert str(path) not in repr(result)


def test_missing_directory_and_link_files_fail_closed(tmp_path):
    for path in (tmp_path / "missing", tmp_path):
        assert load({"AI_ENV_FILE": str(path)}).status == "INVALID_CONFIGURATION"
    target = tmp_path / "target.env"
    target.write_text("AI_PROVIDER=OPENROUTER", encoding="utf-8")
    link = tmp_path / "link.env"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    assert load({"AI_ENV_FILE": str(link)}).status == "INVALID_CONFIGURATION"
