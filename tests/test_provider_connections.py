"""Connection registration is a startup snapshot, never an authenticated request."""

import httpx
import pytest
from fastapi import FastAPI

from multi_exchange_trader.ai.budget import BudgetBook

KEY = "sk-or-v1-fake-openrouter-tests-only"
MODEL = "anthropic/claude-haiku-5.5"


@pytest.fixture
def provider_app(tmp_path, monkeypatch):
    from multi_exchange_trader.controls import install

    for name in ("AI_PROVIDER", "OPENROUTER_API_KEY", "OPENROUTER_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AI_ENV_FILE", "off")
    app = FastAPI()
    app.state.ai_budget = BudgetBook(tmp_path / "provider-ai.sqlite3")
    install(app)
    return app


async def test_openrouter_startup_snapshot_and_status_never_authenticate(provider_app, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "OPENROUTER")
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.setenv("OPENROUTER_MODEL", MODEL)

    def refuse_network(*args, **kwargs):
        raise AssertionError("Registration must not create a network client")

    with monkeypatch.context() as guard:
        guard.setattr(httpx, "Client", refuse_network)
        guard.setattr(httpx, "AsyncClient", refuse_network)
        async with provider_app.router.lifespan_context(provider_app):
            pass
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("OPENROUTER_MODEL", "")
    monkeypatch.setattr("multi_exchange_trader.controls.load_ai_provider_config", refuse_network)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(provider_app),
                                 base_url="http://localhost:8000") as client:
        response = await client.get("/api/v1/connections/status")
        assert response.json()["ai"] == {"registered": True, "status": "REGISTERED_UNVERIFIED",
            "provider": "OPENROUTER", "model_id": MODEL, "verified_at": None,
            "mode": "OFFLINE_TEST", "external_calls": False}
        assert KEY not in response.text
        assert KEY not in repr(provider_app.state.ai_provider_config)
        assert provider_app.state.anthropic_config.registered is False
        provider_app.state.ai_budget = None
        failed = (await client.get("/api/v1/connections/status")).json()["ai"]
        assert failed["status"] == "STORAGE_UNAVAILABLE"
        assert failed["external_calls"] is False


@pytest.mark.parametrize("key,status", [("", "NOT_CONFIGURED"),
                                      ("fake-anthropic-key", "INVALID_CONFIGURATION")])
async def test_missing_or_wrong_openrouter_key_does_not_register_anthropic(
    provider_app, monkeypatch, key, status
):
    monkeypatch.setenv("AI_PROVIDER", "OPENROUTER")
    monkeypatch.setenv("OPENROUTER_API_KEY", key)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic-key")
    async with provider_app.router.lifespan_context(provider_app), httpx.AsyncClient(
        transport=httpx.ASGITransport(provider_app), base_url="http://localhost:8000"
    ) as client:
        ai = (await client.get("/api/v1/connections/status")).json()["ai"]
        assert ai["registered"] is False
        assert ai["provider"] == "OPENROUTER"
        assert ai["status"] == status


def test_app_install_does_not_load_provider_settings(monkeypatch):
    from multi_exchange_trader.controls import install

    def refuse(*args, **kwargs):
        raise AssertionError("Settings are only read during startup")

    monkeypatch.setattr("multi_exchange_trader.controls.load_ai_provider_config", refuse)
    install(FastAPI())


async def test_existing_anthropic_state_callers_still_get_safe_default_provider(provider_app):
    from multi_exchange_trader.ai.config import AnthropicConfig

    provider_app.state.anthropic_config = AnthropicConfig(True, "REGISTERED_UNVERIFIED", "claude-test")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(provider_app),
                                 base_url="http://localhost:8000") as client:
        ai = (await client.get("/api/v1/connections/status")).json()["ai"]
        assert ai["provider"] == "ANTHROPIC"
        assert ai["registered"] is True
        assert ai["model_id"] == "claude-test"
        provider_app.state.ai_provider_config = object()
        invalid = (await client.get("/api/v1/connections/status")).json()["ai"]
        assert invalid["registered"] is False
        assert invalid["status"] == "INVALID_CONFIGURATION"
        assert invalid["model_id"] is None
