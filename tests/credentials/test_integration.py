"""Synthetic saved credentials compose existing clients without enabling effects."""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from multi_exchange_trader.credential_resolution import CredentialResolver
from multi_exchange_trader.credentials import CredentialStore, CredentialStoreError, import_env

LIVE = {"BYBIT_API_KEY": "synthetic-live-key", "BYBIT_API_SECRET": "synthetic-live-secret"}
DEMO = {"BYBIT_DEMO_API_KEY": "synthetic-demo-key",
        "BYBIT_DEMO_API_SECRET": "synthetic-demo-secret"}
AI = {"AI_PROVIDER": "OPENROUTER", "OPENROUTER_API_KEY": "sk-or-v1-synthetic-only-key",
      "OPENROUTER_MODEL": "anthropic/claude-test"}


def resolver(store: CredentialStore | None, environ: dict[str, str]) -> CredentialResolver:
    return CredentialResolver(store=store, environ=environ)


def saved(tmp_path: Path, values: dict[str, str]) -> CredentialStore:
    store = CredentialStore(tmp_path / "private" / "credentials.bin")
    store.register(values, expected_revision=None)
    return store


async def test_default_clients_reuse_selected_store(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    from multi_exchange_trader.account.bybit_readonly import BybitReadOnlyClient
    from multi_exchange_trader.ai.provider_config import load_ai_provider_config

    store = saved(tmp_path, LIVE | AI)
    monkeypatch.setenv("TRADER_CREDENTIAL_FILE", str(store.path))
    client = BybitReadOnlyClient.from_environment()
    try:
        assert client._api_key == LIVE["BYBIT_API_KEY"]
        assert load_ai_provider_config().registered is True
    finally:
        await client.aclose()


async def test_saved_pairs_construct_clients_without_env_file(tmp_path: Path) -> None:
    from multi_exchange_trader.account.bybit_demo_order import BybitDemoOrderClient
    from multi_exchange_trader.account.bybit_readonly import BybitReadOnlyClient

    source = tmp_path / "source.env"
    source.write_text("\n".join(f"{key}={value}" for key, value in (LIVE | DEMO).items()))
    store = CredentialStore(tmp_path / "private" / "credentials.bin")
    import_env(source, store, expected_revision=None)
    source.unlink()
    context = resolver(store, {"BYBIT_DEMO": "true", "BYBIT_ENV_FILE": str(source)})

    def refuse(request: httpx.Request) -> httpx.Response:
        pytest.fail("Construction must not authenticate or order")

    transport = httpx.MockTransport(refuse)
    clients: list[BybitReadOnlyClient | BybitDemoOrderClient] = [
               BybitReadOnlyClient.from_environment(transport, resolver=context),
               BybitReadOnlyClient.for_demo(transport, resolver=context),
               BybitDemoOrderClient.from_environment(transport, resolver=context)]
    try:
        assert clients[0]._api_key == LIVE["BYBIT_API_KEY"]
        assert clients[1]._api_key == DEMO["BYBIT_DEMO_API_KEY"]
        assert clients[2]._api_secret == DEMO["BYBIT_DEMO_API_SECRET"]
    finally:
        for client in clients:
            await client.aclose()


def test_fresh_startup_context_reuses_store_without_environment_mutation(tmp_path: Path) -> None:
    store = saved(tmp_path, LIVE | DEMO | AI)
    before = dict(os.environ)
    first = resolver(store, {"BYBIT_ENV_FILE": "off"})
    assert first.bybit_pair("mainnet") == tuple(LIVE.values())
    store.register({"BYBIT_API_KEY": "rotated-live-key", "BYBIT_API_SECRET": "rotated-live-secret"},
                   expected_revision=1)
    assert first.bybit_pair("mainnet") == tuple(LIVE.values())
    assert first.ai_values()["OPENROUTER_API_KEY"] == AI["OPENROUTER_API_KEY"]
    assert resolver(store, {}).bybit_pair("mainnet")[0] == "rotated-live-key"
    assert dict(os.environ) == before
    assert AI["OPENROUTER_API_KEY"] not in repr(first)
    assert str(store.path) not in repr(first)


def test_explicit_mapping_loader_does_not_discover_store(monkeypatch: pytest.MonkeyPatch) -> None:
    from multi_exchange_trader.ai.config import load_anthropic_config
    from multi_exchange_trader.ai.provider_config import load_ai_provider_config

    def refuse(*args: object, **kwargs: object) -> None:
        pytest.fail("An explicit mapping must not discover a store")

    monkeypatch.setattr("multi_exchange_trader.credentials.default_credential_store", refuse)
    monkeypatch.setattr(CredentialResolver, "from_environment", refuse)
    assert load_ai_provider_config(AI).registered
    assert load_anthropic_config({"ANTHROPIC_API_KEY": "synthetic-anthropic"}).registered


def test_corrupt_store_cannot_fallback_to_env(tmp_path: Path) -> None:
    store = saved(tmp_path, LIVE)
    store.path.write_bytes(b"corrupt")
    context = resolver(store, LIVE | AI | {"BYBIT_ENV_FILE": "off"})
    with pytest.raises(CredentialStoreError):
        context.bybit_pair("mainnet")
    with pytest.raises(CredentialStoreError):
        context.ai_values()


def test_store_namespace_absence_preserves_legacy_setup(tmp_path: Path) -> None:
    store = saved(tmp_path, AI)
    source = tmp_path / "legacy.env"
    source.write_text("\n".join(f"{key}={value}" for key, value in (LIVE | DEMO).items()))
    before = dict(os.environ)
    context = resolver(store, {"BYBIT_ENV_FILE": str(source)})
    assert context.bybit_pair("mainnet") == tuple(LIVE.values())
    assert context.bybit_pair("demo") == tuple(DEMO.values())
    assert dict(os.environ) == before


@pytest.mark.parametrize("field", ["BYBIT_DEMO_API_KEY", "BYBIT_DEMO_API_SECRET"])
def test_selected_pair_collision_across_sources(tmp_path: Path, field: str) -> None:
    store = saved(tmp_path, LIVE)
    demo = DEMO | {field: LIVE["BYBIT_API_KEY" if field.endswith("KEY") else "BYBIT_API_SECRET"]}
    context = resolver(store, demo | {"BYBIT_ENV_FILE": "off"})
    with pytest.raises(CredentialStoreError, match="INVALID_VALUES"):
        context.bybit_pair("mainnet")


async def test_store_does_not_enable_demo_or_ai(tmp_path: Path) -> None:
    from multi_exchange_trader.account.bybit_readonly import BybitReadOnlyClient
    from multi_exchange_trader.account.config import MissingBybitCredentials
    from multi_exchange_trader.ai.provider_config import load_ai_provider_config

    context = resolver(saved(tmp_path, LIVE | DEMO | AI), {"BYBIT_ENV_FILE": "off"})
    with pytest.raises(MissingBybitCredentials, match="not enabled"):
        BybitReadOnlyClient.for_demo(resolver=context)
    assert load_ai_provider_config(resolver=context).status == "REGISTERED_UNVERIFIED"


def test_default_worker_cli_does_not_open_vault(monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    from multi_exchange_trader.execution.worker_cli import main

    def refuse(*args: object, **kwargs: object) -> None:
        pytest.fail("Default DRY_CHECK must not open a store")

    monkeypatch.setattr(CredentialStore, "load", refuse)
    assert main([]) == 0
    assert '"mode": "DRY_CHECK"' in capsys.readouterr().out


def test_private_factory_has_no_provider_effects(tmp_path: Path) -> None:
    from multi_exchange_trader.ai.budget import BudgetBook
    from multi_exchange_trader.ai.private_factory import create_openrouter_clients
    context = resolver(saved(tmp_path, AI), {})
    budget = BudgetBook(tmp_path / "budget.sqlite3")
    before = budget.settings()

    def refuse(request: httpx.Request) -> httpx.Response:
        pytest.fail("Factory must not authenticate, quote or generate")

    bundle = create_openrouter_clients(resolver=context, budget=budget,
        dispatch_path=tmp_path / "dispatch.sqlite3", transport=httpx.MockTransport(refuse))
    assert bundle.generation.model_id == AI["OPENROUTER_MODEL"]
    assert bundle.dispatcher.client is bundle.generation
    assert budget.settings() == before
    assert AI["OPENROUTER_API_KEY"] not in repr(bundle)
    assert bundle.dispatcher.path.read_bytes().find(AI["OPENROUTER_API_KEY"].encode()) == -1


async def test_connections_dto_contains_no_secret_or_path(tmp_path: Path) -> None:
    from multi_exchange_trader.api import create_app
    from multi_exchange_trader.market_data.service import MarketDataService

    store = saved(tmp_path, LIVE | DEMO | AI)
    app = create_app(MarketDataService({}), credential_resolver=resolver(store, {}))
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://localhost:8000"
    ) as client:
        response = await client.get("/api/v1/connections/status")
        assert response.status_code == 200
        value = response.json()
        assert value["bybit"]["registered"] is True
        assert value["ai"]["registered"] is True
        assert value["live_order_enabled"] is False
        assert value["ai"]["external_calls"] is False
        for secret in (LIVE | DEMO | AI).values():
            if secret not in ("OPENROUTER", AI["OPENROUTER_MODEL"]):
                assert secret not in response.text
        assert str(store.path) not in response.text


async def test_corrupt_store_isolates_connections_from_unrelated_api(tmp_path: Path) -> None:
    from multi_exchange_trader.api import create_app
    from multi_exchange_trader.market_data.service import MarketDataService

    store = saved(tmp_path, LIVE | AI)
    store.path.write_bytes(b"corrupt")
    app = create_app(MarketDataService({}), credential_resolver=resolver(store, LIVE | AI))
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://localhost:8000"
    ) as client:
        assert (await client.get("/api/v1/health")).status_code == 200
        paper = await client.get("/api/v1/paper/scenarios")
        research = await client.get("/api/v1/research/datasets")
        assert paper.status_code == research.status_code == 200
        assert paper.json()["scenarios"] == []
        assert isinstance(research.json()["datasets"], list)
        assert research.json()["scope"] == "PHASE4_LOCAL"
        value = (await client.get("/api/v1/connections/status")).json()
        assert value["bybit"]["status"] == "INVALID_CONFIGURATION"
        assert value["ai"]["status"] == "INVALID_CONFIGURATION"


def test_saved_pair_reused_by_fresh_process_after_source_unavailable(tmp_path: Path) -> None:
    source = tmp_path / "source.env"
    source.write_text("\n".join(f"{key}={value}" for key, value in (LIVE | DEMO | AI).items()))
    store = CredentialStore(tmp_path / "private" / "credentials.bin")
    import_env(source, store, expected_revision=None)
    source.unlink()
    environ = dict(os.environ, TRADER_CREDENTIAL_FILE=str(store.path),
                   BYBIT_ENV_FILE=str(source), AI_ENV_FILE=str(source), BYBIT_DEMO="true",
                   PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"))
    code = """
import asyncio
from multi_exchange_trader.account.bybit_readonly import BybitReadOnlyClient
from multi_exchange_trader.ai.provider_config import load_ai_provider_config
async def check():
    live = BybitReadOnlyClient.from_environment()
    demo = BybitReadOnlyClient.for_demo()
    assert live.environment == 'mainnet' and demo.environment == 'demo'
    assert load_ai_provider_config().status == 'REGISTERED_UNVERIFIED'
    await live.aclose()
    await demo.aclose()
asyncio.run(check())
"""
    result = subprocess.run([sys.executable, "-c", code], env=environ,
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, "Fresh client process failed"
    assert result.stdout == result.stderr == ""


async def test_create_app_is_lazy_and_lifespans_share_one_store_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.api import create_app
    from multi_exchange_trader.market_data.service import MarketDataService

    store = saved(tmp_path, LIVE | DEMO | AI)
    reads = 0
    original = store.load

    def counted() -> Any:
        nonlocal reads
        reads += 1
        return original()

    monkeypatch.setattr(store, "load", counted)
    context = resolver(store, {"BYBIT_ENV_FILE": "off"})
    app = create_app(MarketDataService({}), credential_resolver=context)
    assert reads == 0
    async with app.router.lifespan_context(app):
        assert reads == 1
        store.register({"OPENROUTER_MODEL": "anthropic/rotated-model"}, expected_revision=1)
        reads_after_registration = reads
        assert app.state.ai_provider_config.model_id == AI["OPENROUTER_MODEL"]
        assert context.ai_values()["OPENROUTER_MODEL"] == AI["OPENROUTER_MODEL"]
        assert reads == reads_after_registration


async def test_stored_credentials_guard_existing_account_publication(tmp_path: Path) -> None:
    from starlette.requests import Request

    from multi_exchange_trader.account.api import account_view
    from multi_exchange_trader.account.bybit_readonly import BybitReadOnlyClient
    from multi_exchange_trader.account.service import blank_view

    # A numeric secret can pass decimal DTO parsing, so the publication guard
    # must recognize client credentials even when no environment contains them.
    numeric = "12345678901234567890"
    store = saved(tmp_path, LIVE | {"BYBIT_API_SECRET": numeric})
    client = BybitReadOnlyClient.from_environment(resolver=resolver(store, {}))
    view = blank_view("ok", "mainnet", live_trading=False).model_copy(update={"메시지": numeric})

    # Exercise the real publication path with a synthetic provider result only.
    from unittest.mock import patch

    from multi_exchange_trader.account import api

    request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(
        bybit_account=client, account_read_gate=api.AccountReadGate(0)))})

    async def synthetic_view(*args: object, **kwargs: object) -> Any:
        return view

    try:
        with patch.object(api, "build_account_view", synthetic_view):
            published = await account_view(request)
        assert numeric not in published.model_dump_json()
        assert published.오류코드 == "credential_leak"
    finally:
        await client.aclose()


def test_pair_sources_are_atomic_and_stored_namespace_wins(tmp_path: Path) -> None:
    store = saved(tmp_path, LIVE)
    context = resolver(store, {"BYBIT_API_KEY": "superseded-half-key", "BYBIT_ENV_FILE": "off"})
    assert context.bybit_pair("mainnet") == tuple(LIVE.values())
    legacy = tmp_path / "legacy.env"
    legacy.write_text("\n".join(f"{key}={value}" for key, value in DEMO.items()))
    context = resolver(store, {"BYBIT_DEMO_API_KEY": "half-key", "BYBIT_ENV_FILE": str(legacy)})
    with pytest.raises(CredentialStoreError, match="INVALID_VALUES"):
        context.bybit_pair("demo")


def test_saved_ai_namespace_never_borrows_environment_model(tmp_path: Path) -> None:
    from multi_exchange_trader.ai.provider_config import load_ai_provider_config

    store = saved(tmp_path, {name: value for name, value in AI.items() if name != "OPENROUTER_MODEL"})
    context = resolver(store, AI | {"AI_ENV_FILE": str(tmp_path / "absent.env")})
    config = load_ai_provider_config(resolver=context)
    assert config.registered is True
    assert config.model_id is None


async def test_explicit_monitor_uses_saved_pair_and_preserves_identity_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution import worker_cli

    store = saved(tmp_path, DEMO)
    monkeypatch.setenv("TRADER_CREDENTIAL_FILE", str(store.path))
    monkeypatch.setenv("BYBIT_DEMO", "true")
    seen: dict[str, Any] = {}

    class StopBeforeNetwork(RuntimeError):
        pass

    class SyntheticSession:
        def __init__(self, **kwargs: str) -> None:
            seen.update(kwargs)

        async def authenticate(self, **kwargs: Any) -> None:
            seen["identity"] = kwargs
            raise StopBeforeNetwork

        async def aclose(self) -> None:
            seen["closed"] = True

    monkeypatch.setattr(worker_cli, "BybitDemoAccountSession", SyntheticSession)
    with pytest.raises(StopBeforeNetwork):
        await worker_cli._run(argparse.Namespace(uid="synthetic-uid", parent_uid="synthetic-parent",
                                                 is_master="false"))
    assert seen == {"api_key": DEMO["BYBIT_DEMO_API_KEY"],
                   "api_secret": DEMO["BYBIT_DEMO_API_SECRET"], "closed": True,
                   "identity": {"expected_uid": "synthetic-uid",
                                "expected_parent_uid": "synthetic-parent",
                                "expected_is_master": False}}


@pytest.mark.parametrize("namespace", ["bybit", "ai"])
def test_invalid_legacy_source_cannot_change_identity_within_context(
    tmp_path: Path, namespace: str,
) -> None:
    path = tmp_path / "legacy.env"
    path.write_text("BYBIT_API_KEY=half-key\nOPENROUTER_API_KEY=invalid-key\nAI_PROVIDER=OPENROUTER\n")
    context = resolver(None, {"BYBIT_ENV_FILE": str(path), "AI_ENV_FILE": str(path),
                              "AI_PROVIDER": "OPENROUTER"})
    with pytest.raises(CredentialStoreError):
        if namespace == "bybit":
            context.bybit_pair("mainnet")
        else:
            context.ai_values()
    path.write_text("\n".join(f"{key}={value}" for key, value in (LIVE | AI).items()))
    with pytest.raises(CredentialStoreError):
        if namespace == "bybit":
            context.bybit_pair("mainnet")
        else:
            context.ai_values()


def test_ai_model_cannot_publish_stored_bybit_credential(tmp_path: Path) -> None:
    from multi_exchange_trader.ai.provider_config import load_ai_provider_config

    bybit = LIVE | {"BYBIT_API_SECRET": "synthetic-bybit-model-fragment"}
    values = bybit | AI | {"OPENROUTER_MODEL": "anthropic/synthetic-bybit-model-fragment"}
    context = resolver(saved(tmp_path, values), {})
    config = load_ai_provider_config(resolver=context)
    assert config.status == "INVALID_CONFIGURATION"
    assert config.model_id is None
    assert bybit["BYBIT_API_SECRET"] not in repr(config)


def test_factory_storage_failure_is_fixed_and_does_not_change_budget(tmp_path: Path) -> None:
    from multi_exchange_trader.ai.budget import BudgetBook
    from multi_exchange_trader.ai.private_factory import create_openrouter_clients

    context = resolver(saved(tmp_path, AI), {})
    budget = BudgetBook(tmp_path / "budget.sqlite3")
    before = budget.settings()
    directory = tmp_path / "synthetic-private-directory"
    directory.mkdir()
    with pytest.raises(CredentialStoreError, match="PERSISTENCE_FAILED") as caught:
        create_openrouter_clients(resolver=context, budget=budget, dispatch_path=directory)
    assert str(directory) not in str(caught.value)
    assert budget.settings() == before


def test_invalid_legacy_bybit_encoding_is_safe_and_frozen(tmp_path: Path) -> None:
    path = tmp_path / "legacy.env"
    path.write_bytes(b"\xff\xfeinvalid")
    context = resolver(None, {"BYBIT_ENV_FILE": str(path)})
    with pytest.raises(CredentialStoreError, match="INVALID_VALUES"):
        context.bybit_pair("mainnet")
    path.write_text("\n".join(f"{key}={value}" for key, value in LIVE.items()))
    with pytest.raises(CredentialStoreError, match="INVALID_VALUES"):
        context.bybit_pair("mainnet")
