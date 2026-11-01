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
