"""Private, immutable startup selection; never activates clients or exports secrets."""

import os
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from .credentials import (
    CredentialSnapshot,
    CredentialStore,
    CredentialStoreError,
    default_credential_store,
)

_PAIRS = {"mainnet": ("BYBIT_API_KEY", "BYBIT_API_SECRET"),
          "demo": ("BYBIT_DEMO_API_KEY", "BYBIT_DEMO_API_SECRET")}
_ENVIRONMENTS: tuple[Literal["mainnet", "demo"], ...] = ("mainnet", "demo")
_BYBIT_NAMES = frozenset(name for pair in _PAIRS.values() for name in pair)
_AI_NAMES = ("AI_PROVIDER", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
             "OPENROUTER_API_KEY", "OPENROUTER_MODEL")
_DISABLED = {"", "off", "none", "0"}

if TYPE_CHECKING:
    from .ai.provider_config import AIProviderConfig


class MissingBybitField(CredentialStoreError):
    """Only an allowlisted field name, preserving legacy missing-field diagnostics."""

    def __init__(self, name: str) -> None:
        super().__init__("INVALID_VALUES")
        self.name = name


class BybitPairCollision(CredentialStoreError):
    def __init__(self) -> None:
        super().__init__("INVALID_VALUES")


class InvalidAIConfiguration(CredentialStoreError):
    """Retains only existing safe metadata, never a key/model candidate or path."""

    def __init__(self, config: "AIProviderConfig") -> None:
        super().__init__("INVALID_VALUES")
        self.config = config


class CredentialResolver:
    """One private store snapshot plus a copy of the startup environment.

    Construction performs no file I/O. An existing invalid store is remembered as
    a failure, so later consumers cannot switch to an old environment identity.
    """

    def __init__(self, *, store: CredentialStore | None, environ: Mapping[str, str]) -> None:
        self._store = store
        self._environ = MappingProxyType(dict(environ))
        self._loaded = False
        self._snapshot: CredentialSnapshot | None = None
        self._failure: CredentialStoreError | None = None
        self._bybit: dict[str, tuple[str, str]] | None = None
        self._bybit_failure: CredentialStoreError | None = None
        self._bybit_flags: Mapping[str, str] = self._environ
        self._ai: Mapping[str, str] | None = None
        self._ai_failure: CredentialStoreError | None = None

    @classmethod
    def from_environment(cls, *, environ: Mapping[str, str] | None = None) -> "CredentialResolver":
        source = dict(os.environ if environ is None else environ)
        return cls(store=default_credential_store(source), environ=source)

    def __repr__(self) -> str:
        return "CredentialResolver(<private>)"

    def _load(self) -> CredentialSnapshot | None:
        if not self._loaded:
            self._loaded = True
            try:
                self._snapshot = self._store.load() if self._store is not None else None
            except CredentialStoreError as exc:
                self._failure = CredentialStoreError(exc.code)
        if self._failure is not None:
            raise self._failure
        return self._snapshot

    def _legacy_bybit(self) -> dict[str, str]:
        # Preserve the legacy explicit/default dotenv fallback without mutating
        # os.environ. Only absent namespaces consult it; pair selection is atomic.
        values: dict[str, str] = {}
        raw = self._environ.get("BYBIT_ENV_FILE", ".env").strip()
        if raw.lower() not in _DISABLED:
            try:
                text = Path(raw).read_text(encoding="utf-8-sig")
            except OSError:
                text = ""
            except ValueError:
                raise CredentialStoreError("INVALID_VALUES") from None
            for line in text.splitlines():
                item = line.strip()
                if item.startswith("export "):
                    item = item[7:].strip()
                if not item or item.startswith("#") or "=" not in item:
                    continue
                name, value = item.split("=", 1)
                name, value = name.strip(), value.strip()
                if name not in _BYBIT_NAMES | {
                    "BYBIT_DEMO", "BYBIT_TESTNET"
                }:
                    continue
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                values.setdefault(name, value)
        return values

    def _resolve_bybit(self) -> None:
        if self._bybit_failure is not None:
            raise self._bybit_failure
        try:
            self._select_bybit()
        except CredentialStoreError as exc:
            self._bybit_failure = exc
            raise

    def _select_bybit(self) -> None:
        if self._bybit is not None:
            return
        snapshot = self._load()
        selected = {env: snapshot.bybit_pair(env) if snapshot else None for env in _ENVIRONMENTS}
        legacy = self._legacy_bybit() if any(pair is None for pair in selected.values()) else {}
        self._bybit_flags = MappingProxyType(legacy | {
            name: value for name, value in self._environ.items() if value.strip()
        })
        result: dict[str, tuple[str, str]] = {}
        for environment in _ENVIRONMENTS:
            names = _PAIRS[environment]
            pair = selected[environment]
            if pair is None:
                source = self._environ if any(self._environ.get(name, "").strip()
                                              for name in names) else legacy
                pair = (source.get(names[0], "").strip(), source.get(names[1], "").strip())
            key, secret = pair
            if bool(key) != bool(secret):
                raise MissingBybitField(names[1] if key else names[0])
            result[environment] = key, secret
        live, demo = result["mainnet"], result["demo"]
        if live[0] and demo[0] and (live[0] == demo[0] or live[1] == demo[1]):
            raise BybitPairCollision()
        self._bybit = result

    def bybit_pair(self, environment: Literal["mainnet", "demo"]) -> tuple[str, str]:
        if environment not in _PAIRS:
            raise CredentialStoreError("INVALID_VALUES")
        self._resolve_bybit()
        assert self._bybit is not None
        return self._bybit[environment]

    def bybit_flag(self, name: Literal["BYBIT_DEMO", "BYBIT_TESTNET"]) -> bool:
        self._resolve_bybit()
        value = self._bybit_flags.get(name, "").strip().lower()
        if value in {"", "0", "false", "no", "off"}:
            return False
        if value in {"1", "true", "yes", "on"}:
            return True
        raise ValueError(f"Invalid boolean environment flag: {name}")

    def ai_values(self) -> Mapping[str, str]:
        if self._ai_failure is not None:
            raise self._ai_failure
        try:
            return self._select_ai()
        except CredentialStoreError as exc:
            self._ai_failure = exc
            raise

    def _select_ai(self) -> Mapping[str, str]:
        if self._ai is not None:
            return self._ai
        snapshot = self._load()
        stored = dict(snapshot.ai_values()) if snapshot else {}
        # Late import avoids credentials -> validators -> resolver cycles.
        from .ai.provider_config import AIProviderConfig, _file_values, load_ai_provider_config

        selector = stored.get("AI_PROVIDER", self._environ.get("AI_PROVIDER", "")).strip()
        provider = "OPENROUTER" if selector == "OPENROUTER" else "ANTHROPIC"
        namespace = provider + "_API_KEY"
        raw = self._environ.get("AI_ENV_FILE", "off" if provider == "OPENROUTER" else
                                self._environ.get("ANTHROPIC_ENV_FILE", "off")).strip()
        file_values: dict[str, str] = {}
        if namespace not in stored and raw.lower() not in _DISABLED:
            try:
                file_values = _file_values(raw, shared="AI_ENV_FILE" in self._environ)
            except (OSError, ValueError):
                raise CredentialStoreError("INVALID_VALUES") from None
        values = dict(file_values)
        for name in _AI_NAMES:
            if self._environ.get(name, "").strip():
                values[name] = self._environ[name]
        # A registered provider owns its entire key/model namespace. A missing
        # stored model never borrows another identity's old environment model.
        for prefix in ("ANTHROPIC", "OPENROUTER"):
            if prefix + "_API_KEY" in stored:
                for suffix in ("_API_KEY", "_MODEL"):
                    values.pop(prefix + suffix, None)
        values.update(stored)
        values.update(AI_ENV_FILE="off", ANTHROPIC_ENV_FILE="off")
        candidates = [source.get(name, "").strip() for source in (file_values, self._environ, stored)
                      for name in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")]
        candidates.extend(self._environ.get(name, "").strip()
                          for names in _PAIRS.values() for name in names)
        if snapshot is not None:
            for environment in _ENVIRONMENTS:
                pair = snapshot.bybit_pair(environment)
                if pair is not None:
                    candidates.extend(pair)
        if self._bybit is not None:
            candidates.extend(value for pair in self._bybit.values() for value in pair)
        config = load_ai_provider_config(values)
        if config.status == "INVALID_CONFIGURATION":
            raise InvalidAIConfiguration(config)
        if config.model_id and any(
            key and key in config.model_id for key in candidates
        ):
            raise InvalidAIConfiguration(AIProviderConfig(
                config.provider, config.registered, "INVALID_CONFIGURATION"))
        self._ai = MappingProxyType(values)
        return self._ai


class CredentialStartup:
    """A closure-owned lazy resolver shared by app and router lifespans."""

    def __init__(self, resolver: CredentialResolver | None = None) -> None:
        self._resolver = resolver

    def get(self) -> CredentialResolver:
        if self._resolver is None:
            self._resolver = CredentialResolver.from_environment()
        return self._resolver

    def __repr__(self) -> str:
        return "CredentialStartup(<private>)"
