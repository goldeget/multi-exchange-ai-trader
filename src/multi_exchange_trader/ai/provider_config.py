"""Explicit, key-free provider registration; loading never contacts a provider."""

import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import load_anthropic_config

Provider = Literal["ANTHROPIC", "OPENROUTER"]
RegistrationStatus = Literal["NOT_CONFIGURED", "REGISTERED_UNVERIFIED", "INVALID_CONFIGURATION"]
_KEYS = frozenset({"AI_PROVIDER", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
                   "OPENROUTER_API_KEY", "OPENROUTER_MODEL"})
_LEGACY_KEYS = frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"})
_CREDENTIAL_KEYS = ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")
_DISABLED = frozenset({"", "off", "none", "0"})
_MAX_BYTES = 16_384
_OPENROUTER_KEY = re.compile(r"sk-or-v1-[A-Za-z0-9_-]{8,480}", re.ASCII)
_OPENROUTER_MODEL = re.compile(
    r"[a-z0-9][a-z0-9_-]{0,63}/[a-z0-9][a-z0-9._-]{0,127}", re.ASCII
)


@dataclass(frozen=True)
class AIProviderConfig:
    """Only safe registration metadata survives loading, never keys or file paths."""

    provider: Provider = "ANTHROPIC"
    registered: bool = False
    status: RegistrationStatus = "NOT_CONFIGURED"
    model_id: str | None = None


def _file_values(raw: str, *, shared: bool) -> dict[str, str]:
    path = Path(raw).absolute()
    for item in (path, *path.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError("Invalid AI configuration")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_BYTES:
            raise ValueError("Invalid AI configuration")
        content = stream.read(_MAX_BYTES + 1)
    if len(content) > _MAX_BYTES:
        raise ValueError("Invalid AI configuration")
    text = content.decode("utf-8-sig")
    # Shared files retain other control separators for field validation, while
    # legacy files keep splitlines() compatibility. Do not count a final EOL twice.
    lines = re.split(r"\r\n|\r|\n", text) if shared else text.splitlines()
    if shared and lines[-1] == "":
        lines.pop()
    if "\x00" in text or len(lines) > 256:
        raise ValueError("Invalid AI configuration")
    values: dict[str, str] = {}
    recognized_keys = _KEYS if shared else _LEGACY_KEYS
    for line in lines:
        # Retain the assignment's trailing characters until validation. In
        # particular, an unquoted model must not silently lose a trailing TAB.
        item_text = line.lstrip()
        if not item_text or item_text.startswith("#"):
            continue
        if item_text.startswith("export "):
            item_text = item_text[7:].lstrip()
        if "=" not in item_text:
            raise ValueError("Invalid AI configuration")
        name, value = item_text.split("=", 1)
        name = name.strip()
        if name not in recognized_keys:
            continue
        if name in values:
            raise ValueError("Invalid AI configuration")
        if name == "OPENROUTER_MODEL" and any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Invalid AI configuration")
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError("Invalid AI configuration")
            value = value[1:-1]
        values[name] = value
    return values


def load_ai_provider_config(environ: Mapping[str, str] | None = None) -> AIProviderConfig:
    """Read an explicit shared file and nonblank overrides; never search for .env.

    An absent selector defaults to Anthropic. AI_ENV_FILE, including an explicit
    disabled value, takes priority over the legacy Anthropic file setting.
    Explicit OpenRouter never reads ANTHROPIC_ENV_FILE or uses an Anthropic key.
    Invalid input returns fixed metadata without any raw values or exceptions.
    """
    source = os.environ if environ is None else environ
    provider: Provider = "ANTHROPIC"
    try:
        selector = source.get("AI_PROVIDER", "").strip()
        if selector and selector not in {"ANTHROPIC", "OPENROUTER"}:
            return AIProviderConfig(status="INVALID_CONFIGURATION")
        if selector == "OPENROUTER":
            provider = "OPENROUTER"
        raw_path = source.get("AI_ENV_FILE", "off" if provider == "OPENROUTER" else
                              source.get("ANTHROPIC_ENV_FILE", "off")).strip()
        values = {} if raw_path.lower() in _DISABLED else _file_values(
            raw_path, shared="AI_ENV_FILE" in source
        )
        # Scan recognized file credentials and both environment namespaces before
        # overrides can hide a credential accidentally pasted into the model.
        candidates = tuple(values.get(name, "").strip() for name in _CREDENTIAL_KEYS) + tuple(
            source.get(name, "").strip() for name in _CREDENTIAL_KEYS
        )
        for name in _KEYS:
            value = source.get(name, "").strip()
            if value:
                values[name] = source[name] if name == "OPENROUTER_MODEL" else value
        selector = values.get("AI_PROVIDER", "").strip()
        if selector not in {"", "ANTHROPIC", "OPENROUTER"}:
            return AIProviderConfig(status="INVALID_CONFIGURATION")
        provider = "OPENROUTER" if selector == "OPENROUTER" else "ANTHROPIC"
        if provider == "ANTHROPIC":
            legacy = load_anthropic_config(values)
            model = legacy.model_id
            if model and any(candidate and candidate in model for candidate in candidates):
                return AIProviderConfig(provider, legacy.registered, "INVALID_CONFIGURATION")
            return AIProviderConfig(provider, legacy.registered, legacy.status, model)
        key = values.get("OPENROUTER_API_KEY", "").strip()
        raw_model = values.get("OPENROUTER_MODEL", "")
        model = raw_model.strip()
        if key and _OPENROUTER_KEY.fullmatch(key) is None:
            return AIProviderConfig(provider, status="INVALID_CONFIGURATION")
        if model and (any(ord(char) < 32 or ord(char) == 127 for char in raw_model)
                      or _OPENROUTER_MODEL.fullmatch(model) is None or ".." in model or any(
            candidate and candidate in model for candidate in candidates
        )):
            return AIProviderConfig(provider, bool(key), "INVALID_CONFIGURATION")
        return AIProviderConfig(provider, bool(key),
                                "REGISTERED_UNVERIFIED" if key else "NOT_CONFIGURED", model or None)
    except (OSError, ValueError):
        return AIProviderConfig(provider, status="INVALID_CONFIGURATION")
