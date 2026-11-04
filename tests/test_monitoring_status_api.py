"""The existing diagnostics consumer reads a real private publication, not a status stub."""

import importlib
import sys
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from multi_exchange_trader import controls, monitoring_status

pytest_plugins = ("test_monitoring_status",)
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Server observation uses read-only Linux UID lock metadata")


def app_for(core: Any, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("TRADER_ACCOUNT_PROFILE_FILE", str(core.profiles.path))
    monkeypatch.setattr(monitoring_status, "_now", lambda: core.wall[0])
    monkeypatch.setattr(controls, "read_overview", lambda *a, **k: {"errors": [], "automation": {"runs": []}})
    monkeypatch.setattr(controls, "_scheduler", lambda *a: {"state": "STOPPED"})
    app = FastAPI()
    app.state.paper_store = SimpleNamespace(path=core.data / "paper.sqlite3")
    app.state.research_store = SimpleNamespace()
    controls.install(app)
    return app


async def test_existing_diagnostics_observes_real_packet_and_expires_without_refreshing_it(
    status_core: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = status_core
    assert c.publisher.observe()
    original = c.publisher.path.read_bytes()
    app = app_for(c, monkeypatch)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://localhost:8000",
    ) as http:
        response = await http.get("/api/v1/diagnostics")
        assert response.status_code == 200
        result = response.json()["components"]["native_monitor"]
        assert result["state"] == "OBSERVING" and result["rest_state"] == "EXPIRED"
        assert result["financial_authority"] is False and result["trading_enabled"] is False
        assert c.expected._uid not in response.text and str(c.publisher.path) not in response.text
        c.wall[0] += timedelta(seconds=31)
        stale = await http.get("/api/v1/diagnostics")
        assert stale.status_code == 200
        assert stale.json()["components"]["native_monitor"]["state"] == "STALE"
        assert stale.json()["components"]["server"]["state"] == "RESPONDING"
    assert c.publisher.path.read_bytes() == original


async def test_profile_or_packet_failure_preserves_other_diagnostics(
    status_core: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = status_core
    assert c.publisher.observe()
    app = app_for(c, monkeypatch)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://localhost:8000",
    ) as http:
        c.publisher.path.write_text("{corrupt")
        broken = await http.get("/api/v1/diagnostics")
        assert broken.status_code == 200
        assert broken.json()["components"]["native_monitor"]["state"] == "CHECK_REQUIRED"
        fixtures: Any = importlib.import_module("account_profiles.test_store")
        fixtures.write(c.profiles.path, fixtures.payload(complete=False))
        pending = await http.get("/api/v1/diagnostics")
        assert pending.status_code == 200
        assert pending.json()["components"]["native_monitor"]["state"] == "CHECK_REQUIRED"
        assert pending.json()["components"]["automation"]["state"] == "STOPPED"
