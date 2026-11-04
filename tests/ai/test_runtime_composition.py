"""Application startup shares durable AI history without starting generation."""

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from multi_exchange_trader.ai.budget import BudgetBook
from multi_exchange_trader.ai.dispatch import AIDispatcher
from multi_exchange_trader.ai.generation import OpenRouterGenerationClient
from multi_exchange_trader.api import create_app
from multi_exchange_trader.credential_resolution import CredentialResolver
from multi_exchange_trader.market_data.service import MarketDataService

AI = {"AI_PROVIDER": "OPENROUTER", "OPENROUTER_API_KEY": "sk-or-v1-synthetic-runtime-only",
      "OPENROUTER_MODEL": "anthropic/claude-haiku-5.5", "BYBIT_ENV_FILE": "off"}


@pytest.fixture(autouse=True)
def refuse_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> Any:
        pytest.fail("Runtime composition must not perform a network request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


def _app(values: dict[str, str] | None = None) -> Any:
    return create_app(service=MarketDataService({}), credential_resolver=CredentialResolver(
        store=None, environ=AI if values is None else values))


async def test_startup_uses_same_budget_and_canonical_sibling_dispatch_without_enabling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = tmp_path / "canonical" / "ai.sqlite3"
    monkeypatch.setenv("AI_DB_PATH", str(canonical))
    expected = BudgetBook(canonical)
    before = expected.settings(), expected.summary()
    app = _app()
    assert not hasattr(app.state, "ai_clients")
    async with app.router.lifespan_context(app):
        clients = app.state.ai_clients
        assert clients is not None
        assert clients.dispatcher.budget is app.state.ai_budget
        assert clients.dispatcher.path == canonical.with_name("ai-dispatch.sqlite3")
        assert clients.generation.model_id == AI["OPENROUTER_MODEL"]
        assert (app.state.ai_budget.settings(), app.state.ai_budget.summary()) == before
        assert AI["OPENROUTER_API_KEY"] not in repr(clients)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                     base_url="http://localhost:8000") as http:
            for route in ("/api/v1/ai/budget", "/api/v1/settings/ai", "/api/v1/connections/status"):
                result = await http.get(route)
                assert result.status_code == 200
                assert AI["OPENROUTER_API_KEY"] not in result.text
                assert str(canonical) not in result.text
            assert (await http.get("/api/v1/settings/ai")).json()["llm_enabled"] is False
    assert app.state.ai_clients is None


async def test_existing_settled_cost_and_uncertain_charge_survive_two_app_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = tmp_path / "canonical" / "ai.sqlite3"
    monkeypatch.setenv("AI_DB_PATH", str(canonical))
    book = BudgetBook(canonical)
    book.configure(0, "0.05")
    client = OpenRouterGenerationClient(AI["OPENROUTER_API_KEY"], AI["OPENROUTER_MODEL"])
    dispatch_path = canonical.with_name("ai-dispatch.sqlite3")
    dispatcher = AIDispatcher(dispatch_path, book, client)
    stamp = datetime.now(UTC).isoformat()
    for identifier, state, cost in (("prior-haiku", "SETTLED", "0.00016450"),
                                    ("prior-qwen", "UNCERTAIN", None)):
        reservation = "dispatch_" + hashlib.sha256(identifier.encode()).hexdigest()
        book.reserve(reservation, "0.01")
        if cost is None:
            book.uncertain(reservation)
        else:
            book.settle(reservation, cost)
        with sqlite3.connect(dispatcher.path) as db:
            db.execute("INSERT INTO ai_dispatches VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (identifier, "synthetic-binding", "0.01000000", state,
                        None, cost, stamp, stamp, None, None))
    # An imported canonical policy may retain limit zero alongside prior liabilities.
    with sqlite3.connect(canonical) as db:
        db.execute("UPDATE ai_settings SET monthly_limit='0.00000000'")
    before = book.settings(), book.summary()
    for _ in range(2):
        app = _app()
        async with app.router.lifespan_context(app):
            active = app.state.ai_clients.dispatcher
            settled, uncertain = active.status("prior-haiku"), active.status("prior-qwen")
            assert settled is not None and settled.status == "SETTLED"
            assert settled.exact_cost_usd == "0.00016450"
            assert uncertain is not None and uncertain.status == "UNCERTAIN"
            assert uncertain.exact_cost_usd is None
            assert (app.state.ai_budget.settings(), app.state.ai_budget.summary()) == before
            assert app.state.ai_budget.summary()["uncertain"] == "0.01000000"
            assert app.state.ai_budget.summary()["remaining"] == "0.00000000"


async def test_broken_dispatch_storage_does_not_replace_history_or_stop_readonly_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = tmp_path / "canonical" / "ai.sqlite3"
    monkeypatch.setenv("AI_DB_PATH", str(canonical))
    before = BudgetBook(canonical).summary()
    dispatch = canonical.with_name("ai-dispatch.sqlite3")
    dispatch.write_bytes(b"invalid preserved dispatch store")
    app = _app()
    async with app.router.lifespan_context(app):
        assert app.state.ai_clients is None
        assert app.state.ai_budget.summary() == before
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                     base_url="http://localhost:8000") as http:
            assert (await http.get("/api/v1/health")).status_code == 200
    assert dispatch.read_bytes() == b"invalid preserved dispatch store"
    assert sorted(path.name for path in canonical.parent.iterdir()) == [
        "ai-dispatch.sqlite3", "ai.sqlite3"]


async def test_missing_ai_credentials_leave_readonly_startup_available(tmp_path: Path) -> None:
    app = _app({"BYBIT_ENV_FILE": "off", "AI_ENV_FILE": "off", "ANTHROPIC_ENV_FILE": "off"})
    async with app.router.lifespan_context(app):
        assert app.state.ai_clients is None
        assert not app.state.ai_budget.path.with_name("ai-dispatch.sqlite3").exists()


async def test_startup_failure_drops_private_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    from multi_exchange_trader.market_data.public_client import PublicMarketClient

    async def refuse_start(self: PublicMarketClient) -> Any:
        raise ValueError("synthetic startup failure")

    monkeypatch.setattr(PublicMarketClient, "__aenter__", refuse_start)
    app = create_app(credential_resolver=CredentialResolver(store=None, environ=AI))
    with pytest.raises(ValueError, match="synthetic startup failure"):
        async with app.router.lifespan_context(app):
            pytest.fail("Startup must fail before yielding")
    assert app.state.ai_clients is None
