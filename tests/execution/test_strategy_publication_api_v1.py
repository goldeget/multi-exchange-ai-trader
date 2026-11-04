"""Actual temporary policy publication through the existing HTTP settings boundary."""

import json
import sqlite3
from pathlib import Path

import httpx
from fastapi import FastAPI
from test_strategy_publication_v1 import artifact, fixture_book

from multi_exchange_trader.execution.modes import StrategyReference
from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook
from multi_exchange_trader.execution.settings_api import install
from multi_exchange_trader.execution.strategy_publication_v1 import StrategyPublicationOwnerV1
from multi_exchange_trader.paper.api import install as install_boundary


def application(book: RuntimePolicyBook) -> FastAPI:
    app = FastAPI()
    app.state.runtime_policy = book
    install_boundary(app)
    install(app)
    return app


async def test_inactive_publication_is_readable_without_creating_approval_or_starting(tmp_path: Path) -> None:
    book = RuntimePolicyBook(tmp_path / "policy.sqlite3")
    app = application(book)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost:8000") as client:
        result = await client.get("/api/v1/automatic/strategies")
        assert result.status_code == 200 and result.json() is None
        assert result.headers["X-Automatic-Strategy-Publication-Version"] == "1"
        assert (await client.get("/api/v1/automatic/settings")).json()["revision"] == 0
        assert (await client.post("/api/v1/automatic/strategies", json={})).status_code == 405
        assert (await client.get("/api/v1/automatic/strategies", headers={"host": "evil.test"})).status_code == 403


async def test_current_publication_settings_save_and_revocation_share_actual_generation(tmp_path: Path) -> None:
    book = fixture_book(tmp_path)
    owner = StrategyPublicationOwnerV1(book)
    owner.publish((artifact(),), expected_policy_revision=1, expected_generation=0)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(application(book)),
                                 base_url="http://localhost:8000") as client:
        publication = (await client.get("/api/v1/automatic/strategies")).json()
        settings = (await client.get("/api/v1/automatic/settings")).json()
        assert set(publication) == {"schema_version", "generation", "catalog"}
        assert publication["generation"] == 1
        assert settings["policy"]["strategy"]["catalog"] == publication["catalog"]
        assert "STRATEGY_CURRENT_PUBLICATION_REQUIRED" not in settings["blockers"]
        policy = json.loads(json.dumps(settings["policy"]))
        policy["legacy_v1"]["capital_usdt"] = "90.00"
        body = {"schema_version": 2, "revision": settings["revision"], "policy": policy,
                "expected_strategy_generation": None, "policy_sections_version": 1}
        assert (await client.put("/api/v1/automatic/settings", json=body)).status_code == 409
        body["expected_strategy_generation"] = 1
        saved = await client.put("/api/v1/automatic/settings", json=body)
        assert saved.status_code == 200
        assert saved.json()["desired_state"] == "STOPPED" and saved.json()["live_order_enabled"] is False
        owner.revoke(StrategyReference("fixture-alpha", "r1"),
                     expected_policy_revision=saved.json()["revision"], expected_generation=1)
        assert (await client.get("/api/v1/automatic/strategies")).json()["generation"] == 2
        assert (await client.put("/api/v1/automatic/settings", json=body)).status_code == 409
        assert book.read()["policy"]["legacy_v1"]["capital_usdt"] == "90.00"


async def test_incomplete_publication_returns_fixed_failure_without_artifact_bytes(tmp_path: Path) -> None:
    book = fixture_book(tmp_path)
    StrategyPublicationOwnerV1(book).publish((artifact(),), expected_policy_revision=1, expected_generation=0)
    with sqlite3.connect(book.path) as db:
        db.execute("DROP TABLE strategy_publication_artifacts_v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(application(book)),
                                 base_url="http://localhost:8000") as client:
        result = await client.get("/api/v1/automatic/strategies")
        assert result.status_code == 503
        assert result.json() == {"reason": "automatic_storage_unavailable"}
