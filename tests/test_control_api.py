"""ASGI control routes with test-only read services; never run exchange or native code."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from multi_exchange_trader.ai.budget import BudgetBook
from multi_exchange_trader.paper.api import install as install_boundary


@pytest.fixture
def control_app(tmp_path):
    from multi_exchange_trader.controls import install

    app = FastAPI()
    app.state.paper_store = SimpleNamespace(scenarios=list)
    app.state.research_store = SimpleNamespace(runs=lambda: [{"run_id": "one", "status": "RUNNING"}])
    app.state.research_health = {"state": "READY", "block_new_auto": False}
    app.state.bybit_account = None
    app.state.bybit_account_problem = "missing_credentials"
    app.state.ai_budget = BudgetBook(tmp_path / "ai.sqlite3")
    app.state.order_reviews = SimpleNamespace(unresolved_count=lambda: 0)
    install_boundary(app)
    install(app)
    return app


async def test_partial_worker_failure_not_reported_trade_ready(control_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(control_app), base_url="http://localhost:8000") as client:
        overview = (await client.get("/api/v1/overview")).json()
        assert overview["automation"]["scheduler"]["block_new_auto"] is True
        assert overview["automation"]["scheduler"]["work_differs_from_store"] is True
        diagnostics = (await client.get("/api/v1/diagnostics")).json()
        assert diagnostics["live_order_enabled"] is False
        assert diagnostics["components"]["automation"]["state"] == "FAIL_CLOSED"
        assert diagnostics["components"]["server"]["state"] == "RESPONDING"


async def test_settings_revision_disabled_provider_and_local_boundary(control_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(control_app), base_url="http://localhost:8000") as client:
        body = {"revision": 0, "mode": "OFFLINE_TEST", "monthly_limit": "8.00"}
        saved = await client.put("/api/v1/settings/ai", json=body)
        assert saved.status_code == 200
        assert saved.json()["provider_status"] == "NOT_CONFIGURED"
        assert (await client.put("/api/v1/settings/ai", json=body)).status_code == 409
        assert (await client.put("/api/v1/settings/ai", json=body | {"revision": 1, "mode": "OPENROUTER_PAPER"})).status_code == 409
        assert (await client.get("/api/v1/ai/budget")).json()["provider_balance"] is None
        for route in ("capabilities", "overview", "diagnostics", "settings/ai", "ai/budget", "connections/status"):
            assert (await client.get("/api/v1/" + route, headers={"host": "evil.example"})).status_code == 403
        blocked = await client.put("/api/v1/settings/ai", json=body, headers={"origin": "https://evil.example"})
        assert blocked.status_code == 403


async def test_worker_running_does_not_override_block_new_auto(control_app):
    task = asyncio.create_task(asyncio.Event().wait())
    control_app.state.research_task = task
    control_app.state.research_health.update(state="STALLED", block_new_auto=True)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(control_app), base_url="http://localhost:8000") as client:
            diagnostics = (await client.get("/api/v1/diagnostics")).json()
            assert diagnostics["components"]["automation"]["block_new_auto"] is True
            assert diagnostics["components"]["automation"]["state"] == "STALLED"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_research_store_failure_blocks_new_work_and_preserves_other_reads(control_app):
    def unavailable():
        raise ValueError("private stack trace")

    task = asyncio.create_task(asyncio.Event().wait())
    control_app.state.research_task = task
    control_app.state.research_store.runs = unavailable
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(control_app), base_url="http://localhost:8000") as client:
            response = await client.get("/api/v1/overview")
            assert response.status_code == 200
            assert response.json()["automation"]["scheduler"]["block_new_auto"] is True
            assert response.json()["automation"]["scheduler"]["state"] == "FAIL_CLOSED"
            assert "private stack" not in response.text
            assert (await client.get("/api/v1/settings/ai")).status_code == 200
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_ai_storage_failure_does_not_block_overview_or_claim_zero_budget(control_app):
    control_app.state.ai_budget = None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(control_app), base_url="http://localhost:8000") as client:
        failed = await client.get("/api/v1/ai/budget")
        assert failed.status_code == 503
        assert "remaining" not in failed.json()
        assert (await client.get("/api/v1/overview")).status_code == 200


async def test_unqualified_spot_server_refuses_funding_ticks_and_order_review(control_app, tmp_path):
    from multi_exchange_trader.paper.order_preview import OrderReviews
    from multi_exchange_trader.paper.spot_api import install

    control_app.state.spot_paper_qualified = False
    control_app.state.order_reviews = OrderReviews(tmp_path / "reviews.sqlite3", None)
    install(control_app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(control_app), base_url="http://localhost:8000") as client:
        capabilities = (await client.get("/api/v1/capabilities")).json()
        assert capabilities["manual_paper"]["spot_limit"]["supported"] is False
        route = "/api/v1/paper/spot/scenarios/manual/commands"
        create = await client.post(route, json={"idempotency_key": "seed", "command": {"kind": "create"}})
        assert create.status_code == 409
        assert create.json()["reason"] == "spot_not_qualified"
        order = {"kind": "order", "instrument": "BTC-PAPER", "side": "buy", "quantity": "0.001", "order_type": "MARKET"}
        bypass = await client.post(route, json={"idempotency_key": "bypass", "command": order})
        assert bypass.status_code == 409
        assert bypass.json()["reason"] == "spot_order_review_required"
        preview = await client.post("/api/v1/paper/order-previews", json={"scenario_id": "manual", "product": "SPOT",
            "order_type": "MARKET", "config_revision": "SPOT-PAPER-v2", "command": order})
        assert preview.status_code == 400


async def test_anthropic_snapshot_is_registered_without_transport_or_paid_mode(control_app, monkeypatch):
    from multi_exchange_trader.controls import install

    def no_client(*args, **kwargs):
        raise AssertionError("Anthropic readiness must not create an HTTP client")

    monkeypatch.setenv("ANTHROPIC_ENV_FILE", "off")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-test-anthropic-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-example-20260101")
    fresh = FastAPI()
    fresh.state.ai_budget = control_app.state.ai_budget
    install(fresh)
    with monkeypatch.context() as transport_guard:
        transport_guard.setattr(httpx, "Client", no_client)
        transport_guard.setattr(httpx, "AsyncClient", no_client)
        async with fresh.router.lifespan_context(fresh):
            pass
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_MODEL", "")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(fresh), base_url="http://localhost:8000") as client:
        response = await client.get("/api/v1/connections/status")
        ai = response.json()["ai"]
        assert ai["registered"] is True
        assert ai["status"] == "REGISTERED_UNVERIFIED"
        assert ai["provider"] == "ANTHROPIC"
        assert ai["model_id"] == "claude-example-20260101"
        assert ai["mode"] == "OFFLINE_TEST"
        assert ai["external_calls"] is False
        assert ai["verified_at"] is None
        assert "fake-test-anthropic-key" not in response.text
        assert (await client.get("/api/v1/settings/ai")).json()["provider_status"] == "NOT_CONFIGURED"
        fresh.state.ai_budget = None
        failed = (await client.get("/api/v1/connections/status")).json()["ai"]
        assert failed["status"] == "STORAGE_UNAVAILABLE"
        assert failed["external_calls"] is False


async def test_anthropic_invalid_file_errors_do_not_leak_in_status_or_diagnostics(control_app, tmp_path, monkeypatch):
    from multi_exchange_trader.controls import install

    path = tmp_path / "private-fake-key-config.env"
    path.write_text("ANTHROPIC_MODEL=sk-ant-fake-private-value", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_ENV_FILE", str(path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    fresh = FastAPI()
    fresh.state.ai_budget = control_app.state.ai_budget
    fresh.state.paper_store = control_app.state.paper_store
    fresh.state.research_store = control_app.state.research_store
    install(fresh)
    async with fresh.router.lifespan_context(fresh):
        pass
    async with httpx.AsyncClient(transport=httpx.ASGITransport(fresh), base_url="http://localhost:8000") as client:
        for route in ["connections/status", "diagnostics"]:
            response = await client.get("/api/v1/" + route)
            assert response.status_code == 200
            ai = response.json().get("ai") or response.json()["components"]["ai"]
            assert ai["status"] == "INVALID_CONFIGURATION"
            assert ai["registered"] is False
            assert ai["model_id"] is None
            assert "sk-ant-fake-private-value" not in response.text
            assert str(path) not in response.text


def test_install_does_not_read_credentials_before_startup(monkeypatch):
    from multi_exchange_trader.controls import install

    def refuse_read(*args, **kwargs):
        raise AssertionError("Creating an app must not read credential files")

    monkeypatch.setattr("multi_exchange_trader.controls.load_anthropic_config", refuse_read)
    install(FastAPI())


@pytest.mark.parametrize("model", [
    "claude-fake-review-key-suffix",
    "claude-prefix-claude-fake-review-key",
    "claude-prefix-claude-fake-review-key-suffix",
])
@pytest.mark.parametrize("override_file_key", [False, True])
async def test_model_never_exposes_current_or_overridden_keys(
    control_app, tmp_path, monkeypatch, model, override_file_key
):
    original_key = "claude-fake-review-key"
    env_key = "different-fake-environment-key" if override_file_key else original_key
    monkeypatch.setenv("ANTHROPIC_API_KEY", env_key)
    monkeypatch.setenv("ANTHROPIC_MODEL", model)
    if override_file_key:
        path = tmp_path / "credentials.env"
        path.write_text(f"ANTHROPIC_API_KEY={original_key}\nANTHROPIC_MODEL={model}\n",
                        encoding="utf-8")
        monkeypatch.setenv("ANTHROPIC_ENV_FILE", str(path))
        monkeypatch.delenv("ANTHROPIC_MODEL")
    async with control_app.router.lifespan_context(control_app), httpx.AsyncClient(
        transport=httpx.ASGITransport(control_app), base_url="http://localhost:8000"
    ) as client:
        for storage_available in (True, False):
            if not storage_available:
                control_app.state.ai_budget = None
            for route in ("connections/status", "diagnostics"):
                response = await client.get("/api/v1/" + route)
                assert response.status_code == 200
                payload = response.json()
                ai = payload["ai"] if "ai" in payload else payload["components"]["ai"]
                expected = "INVALID_CONFIGURATION" if storage_available else "STORAGE_UNAVAILABLE"
                assert ai["status"] == expected
                assert ai["registered"] is True
                assert ai["model_id"] is None
                for secret in (original_key, env_key):
                    assert secret not in response.text
                    assert secret not in repr(control_app.state.anthropic_config)
