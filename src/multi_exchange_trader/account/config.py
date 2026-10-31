"""Bybit credentials and the live-trading gate. Values come from the environment only."""

import os
from pathlib import Path
from typing import Literal

EnvironmentName = Literal["mainnet", "demo", "testnet"]

_ALLOWED_ENV_KEYS = frozenset({"BYBIT_API_KEY", "BYBIT_API_SECRET", "BYBIT_TESTNET", "BYBIT_DEMO"})
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})
_HOSTS: dict[EnvironmentName, str] = {
    "mainnet": "https://api.bybit.com",
    "demo": "https://api-demo.bybit.com",
    "testnet": "https://api-testnet.bybit.com",
}


class MissingBybitCredentials(RuntimeError):
    """The account client must not start. The message never includes secret values."""


def load_bybit_env_file() -> None:
    """Fill allowlisted Bybit keys from a dotenv file when they are still empty.

    The path defaults to ``.env``. ``BYBIT_ENV_FILE=off`` disables the file.
    Existing non-empty environment values win. The file contents are never logged.
    """
    raw = os.environ.get("BYBIT_ENV_FILE", ".env").strip()
    if raw.lower() in {"", "off", "none", "0"}:
        return
    try:
        text = Path(raw).read_text(encoding="utf-8-sig")
    except OSError:
        return
    for line in text.splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        if item.startswith("export "):
            item = item[len("export ") :].strip()
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        if key not in _ALLOWED_ENV_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if os.environ.get(key, "").strip():
            continue
        os.environ[key] = value


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    # Do not include the raw value: the wrong variable might hold a secret.
    raise ValueError(f"Invalid boolean environment flag: {name}")


def resolve_environment() -> tuple[EnvironmentName, str]:
    """Return the selected Bybit environment and its fixed read-only host."""
    demo = _flag("BYBIT_DEMO", False)
    testnet = _flag("BYBIT_TESTNET", False)
    if demo and testnet:
        raise ValueError("BYBIT_DEMO and BYBIT_TESTNET cannot both be enabled")
    if demo:
        return "demo", _HOSTS["demo"]
    if testnet:
        return "testnet", _HOSTS["testnet"]
    return "mainnet", _HOSTS["mainnet"]


def allowed_host(environment: EnvironmentName) -> str:
    return _HOSTS[environment]


def require_bybit_credentials() -> tuple[str, str]:
    """Return the key and secret, or refuse to start the account client."""
    load_bybit_env_file()
    key = os.environ.get("BYBIT_API_KEY", "").strip()
    secret = os.environ.get("BYBIT_API_SECRET", "").strip()
    if not secret:
        raise MissingBybitCredentials("BYBIT_API_SECRET is not set")
    if not key:
        raise MissingBybitCredentials("BYBIT_API_KEY is not set")
    return key, secret


def live_trading_enabled() -> bool:
    """Return the live-trading flag. The default is false.

    While this is false, order endpoints are refused. Setting it true still
    does not send orders from the read-only Bybit client: that client has no
    order method. A later order must use one NautilusTrader execution path,
    which this milestone does not start. Unrecognized values fail closed and
    are not echoed, because the wrong variable might contain a secret.
    """
    raw = os.environ.get("LIVE_TRADING_ENABLED", "false").strip().lower()
    return raw in _TRUE


def text_contains_credential(text: str) -> bool:
    """True when the text includes a configured Bybit key or secret."""
    for name in ("BYBIT_API_KEY", "BYBIT_API_SECRET"):
        value = os.environ.get(name, "").strip()
        if len(value) >= 8 and value in text:
            return True
    return False
