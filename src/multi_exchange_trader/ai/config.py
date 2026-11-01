"""Server-only configuration presence; no provider client or credential retention."""

import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_KEYS = frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"})
_MAX_BYTES = 16_384
_MODEL = re.compile(r"claude-[a-z0-9][a-z0-9.-]{0,119}", re.ASCII)
_KEY = re.compile(r"[A-Za-z0-9_.-]{8,512}", re.ASCII)


@dataclass(frozen=True)
class AnthropicConfig:
    """Safe to represent: holds presence and validated model syntax, never a key."""

    registered: bool = False
    status: Literal["NOT_CONFIGURED", "REGISTERED_UNVERIFIED", "INVALID_CONFIGURATION"] = (
        "NOT_CONFIGURED"
    )
    model_id: str | None = None


def _file_values(raw: str) -> dict[str, str]:
    path = Path(raw).absolute()
    # Reject links in every component, including Windows directory junctions.
    for item in (path, *path.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError("Invalid Anthropic configuration")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_BYTES:
            raise ValueError("Invalid Anthropic configuration")
        content = stream.read(_MAX_BYTES + 1)
    if len(content) > _MAX_BYTES:
        raise ValueError("Invalid Anthropic configuration")
    text = content.decode("utf-8-sig")
    if "\x00" in text or len(text.splitlines()) > 256:
        raise ValueError("Invalid Anthropic configuration")
    values: dict[str, str] = {}
    for line in text.splitlines():
        item_text = line.strip()
        if not item_text or item_text.startswith("#"):
            continue
        if item_text.startswith("export "):
            item_text = item_text[7:].strip()
        if "=" not in item_text:
            raise ValueError("Invalid Anthropic configuration")
        name, value = item_text.split("=", 1)
        name = name.strip()
        if name not in _KEYS:
            continue  # A future shared credential file may also contain Bybit keys.
        if name in values:
            raise ValueError("Invalid Anthropic configuration")
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError("Invalid Anthropic configuration")
            value = value[1:-1]
        values[name] = value
    return values


def load_anthropic_config(environ: Mapping[str, str] | None = None) -> AnthropicConfig:
    """Read an explicit file plus nonblank environment overrides without exporting them.

    No default .env search. Empty/off/none/0 disables ANTHROPIC_ENV_FILE.
    Invalid input returns only a safe status; raw values and paths never escape.
    Presence and model syntax cannot establish provider access or paid permission.
    """
    source = os.environ if environ is None else environ
    try:
        raw_path = source.get("ANTHROPIC_ENV_FILE", "off").strip()
        values = {} if raw_path.lower() in {"", "off", "none", "0"} else _file_values(raw_path)
        # A model may accidentally contain the file key even when the process
        # key overrides it. Keep both candidates local until redaction is done.
        credential_candidates = (
            values.get("ANTHROPIC_API_KEY", "").strip(),
            source.get("ANTHROPIC_API_KEY", "").strip(),
        )
        for name in _KEYS:
            value = source.get(name, "").strip()
            if value:
                values[name] = value
        key = values.get("ANTHROPIC_API_KEY", "").strip()
        model = values.get("ANTHROPIC_MODEL", "").strip()
        if key and _KEY.fullmatch(key) is None:
            raise ValueError("Invalid Anthropic configuration")
        if model and (_MODEL.fullmatch(model) is None or any(
            candidate and candidate in model for candidate in credential_candidates
        )):
            return AnthropicConfig(registered=bool(key), status="INVALID_CONFIGURATION")
        return AnthropicConfig(bool(key), "REGISTERED_UNVERIFIED" if key else "NOT_CONFIGURED",
                               model or None)
    except (OSError, ValueError):
        return AnthropicConfig(status="INVALID_CONFIGURATION")
