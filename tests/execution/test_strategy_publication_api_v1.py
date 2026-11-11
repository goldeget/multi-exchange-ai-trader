"""Actual temporary policy publication through the existing HTTP settings boundary."""

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

import httpx
from fastapi import FastAPI
from test_strategy_publication_v1 import artifact, fixture_book

from multi_exchange_trader.execution.modes import StrategyReference
from multi_exchange_trader.execution.operator_auth_v1 import (
    VerifiedOperatorPrincipalV1,
)
from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook
from multi_exchange_trader.execution.settings_api import install
from multi_exchange_trader.execution.strategy_publication_v1 import StrategyPublicationOwnerV1
from multi_exchange_trader.paper.api import install as install_boundary
from multi_exchange_trader.public_dashboard_origin import PublicDashboardOrigin


def application(book: RuntimePolicyBook, *, public_origin: PublicDashboardOrigin | None = None,
                trusted_principal: Callable[[], VerifiedOperatorPrincipalV1 | None] | None = None) -> FastAPI:
    app = FastAPI()
    app.state.runtime_policy = book
    if trusted_principal is not None:
        @app.middleware("http")
        async def test_auth_middleware(request, call_next):
            request.state.verified_operator_principal_v1 = trusted_principal()
            return await call_next(request)

    install_boundary(app, public_origin=public_origin)
    install(app, public_origin=public_origin)
    return app


def same_origin_headers(origin: str = "https://dashboard.example") -> dict[str, str]:
    return {"origin": origin, "sec-fetch-site": "same-origin"}


def verified_principal(*, principal_id: str = "trader-1", evidence_reference: str = "session-42",
                       evidence_sha256: str = "a" * 64) -> VerifiedOperatorPrincipalV1:
    return VerifiedOperatorPrincipalV1(principal_id, evidence_reference, evidence_sha256, "nginx-basic-auth")


def fixed_principal(principal: VerifiedOperatorPrincipalV1) -> Callable[[], VerifiedOperatorPrincipalV1]:
    return lambda: principal


def builtin_publication_body() -> dict[str, object]:
    return {
        "schema_version": 1,
        "expected_policy_revision": 1,
        "expected_generation": 0,
        "symbol": "BTCUSDT",
        "configuration": {
            "schema_version": 1,
            "interval": "5m",
            "fast": 9,
            "slow": 21,
            "atr_bars": 14,
            "stop_atr_multiple": "2",
            "target_r": "2",
            "max_bar_age_seconds": 900,
            "signal_ttl_seconds": 60,
        },
    }


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
                "expected_strategy_generation": None, "policy_sections_version": 2}
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


async def test_spoofed_operator_header_cannot_replace_trusted_principal_or_origin(tmp_path: Path) -> None:
    book = fixture_book(tmp_path)
    origin = PublicDashboardOrigin("https://dashboard.example", "dashboard.example")
    app = application(book, public_origin=origin)
    body = builtin_publication_body()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=origin.origin) as client:
        missing = await client.post("/api/v1/automatic/strategies/preview", json=body,
                                    headers={"origin": origin.origin})
        wrong_user = await client.post("/api/v1/automatic/strategies/preview", json=body,
                                       headers={**same_origin_headers(), "x-trader-operator": "trader"})
        missing_origin = await client.post("/api/v1/automatic/strategies/preview", json=body,
                                           headers={"x-trader-operator": "trader"})
        cross_origin = await client.post("/api/v1/automatic/strategies/preview", json=body,
                                         headers=same_origin_headers("https://evil.example"))
        forwarded_auth = await client.post("/api/v1/automatic/strategies/preview", json=body,
                                           headers={**same_origin_headers(), "authorization": "Basic untrusted"})
    assert all(result.status_code == 403 for result in
        (missing, wrong_user, missing_origin, cross_origin, forwarded_auth))
    assert book.read()["revision"] == 1 and book.publication_snapshot_v1() is None


async def test_explicit_operator_preview_then_publish_is_cas_bound_and_never_starts_trading(tmp_path: Path) -> None:
    book = fixture_book(tmp_path)
    origin = PublicDashboardOrigin("https://dashboard.example", "dashboard.example")
    principal = verified_principal()
    app = application(book, public_origin=origin, trusted_principal=fixed_principal(principal))
    body = builtin_publication_body()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=origin.origin) as client:
        preview = await client.post("/api/v1/automatic/strategies/preview", json=body,
                                    headers={**same_origin_headers(), "x-trader-operator": "forged"})
        assert preview.status_code == 200
        preview_value = preview.json()
        assert preview_value["reference"] == {"strategy_id": "builtin-sma-atr", "revision": "r1"}
        assert preview_value["instrument"] == {"venue": "BYBIT", "product": "LINEAR", "symbol": "BTCUSDT"}
        assert preview_value["configuration"] == body["configuration"]
        assert len(preview_value["implementation_sha256"]) == 64
        assert len(preview_value["configuration_sha256"]) == 64
        assert preview_value["approval_method"] == "APP_AUTHENTICATED_PRINCIPAL_SELF_APPROVAL_V1"
        assert preview_value["authenticated_operator"] == principal.confirmation_payload()
        assert book.read()["revision"] == 1 and book.publication_snapshot_v1() is None

        bad_confirmation = await client.post("/api/v1/automatic/strategies/publish",
            json={**body, "confirmation_fingerprint": "0" * 64}, headers=same_origin_headers())
        assert bad_confirmation.status_code == 409
        assert book.read()["revision"] == 1 and book.publication_snapshot_v1() is None

        published = await client.post("/api/v1/automatic/strategies/publish",
            json={**body, "confirmation_fingerprint": preview_value["confirmation_fingerprint"]},
            headers={**same_origin_headers(), "x-trader-operator": "forged"})
        assert published.status_code == 200
        result = published.json()
        assert (result["generation"], result["policy_revision"]) == (1, 2)
        assert result["implementation_sha256"] == preview_value["implementation_sha256"]
        assert result["configuration_sha256"] == preview_value["configuration_sha256"]
        descriptor = result["catalog"]["entries"][0]
        assert descriptor["approval_source_reference"] == principal.approval_source_reference
        assert descriptor["approval_reference"] == (
            "operator-self-approval:" + preview_value["confirmation_fingerprint"])
        assert descriptor["approval_environments"] == ["DEMO"]
        assert book.read()["desired_state"] == "STOPPED"
        assert book.read()["live_order_enabled"] is False
        assert book.read()["worker_running"] is False
        assert book.read()["policy"]["scope"] is None
        assert book.read()["policy"]["risk_v2"] is None
        assert book.read()["policy"]["strategy"]["allowed"] is None
        assert book.read()["policy"]["strategy"]["selected"] is None

        replay = await client.post("/api/v1/automatic/strategies/publish",
            json={**body, "confirmation_fingerprint": preview_value["confirmation_fingerprint"]},
            headers=same_origin_headers())
        assert replay.status_code == 409


async def test_publication_confirmation_is_bound_to_verified_principal_and_auth_evidence(
    tmp_path: Path,
) -> None:
    book = fixture_book(tmp_path)
    origin = PublicDashboardOrigin("https://dashboard.example", "dashboard.example")
    active_principal = [verified_principal()]

    app = application(book, public_origin=origin, trusted_principal=lambda: active_principal[0])
    body = builtin_publication_body()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=origin.origin) as client:
        preview = await client.post("/api/v1/automatic/strategies/preview", json=body,
                                    headers=same_origin_headers())
        assert preview.status_code == 200
        active_principal[0] = verified_principal(principal_id="trader-2",
            evidence_reference="session-43", evidence_sha256="b" * 64)
        result = await client.post("/api/v1/automatic/strategies/publish",
            json={**body, "confirmation_fingerprint": preview.json()["confirmation_fingerprint"]},
            headers={**same_origin_headers(), "x-trader-operator": "trader"})

    assert result.status_code == 409
    assert book.read()["revision"] == 1 and book.publication_snapshot_v1() is None


async def test_operator_publication_rejects_stale_policy_revision_and_generation(tmp_path: Path) -> None:
    book = fixture_book(tmp_path)
    origin = PublicDashboardOrigin("https://dashboard.example", "dashboard.example")
    principal = verified_principal()
    app = application(book, public_origin=origin, trusted_principal=fixed_principal(principal))
    body = builtin_publication_body()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=origin.origin) as client:
        wrong_generation = await client.post("/api/v1/automatic/strategies/preview",
            json={**body, "expected_generation": 1}, headers=same_origin_headers())
        assert wrong_generation.status_code == 409
        preview = await client.post("/api/v1/automatic/strategies/preview", json=body,
                                    headers=same_origin_headers())
        assert preview.status_code == 200
        policy = book.read()["policy"]
        policy["legacy_v1"]["capital_usdt"] = "90.00"
        book.save_v2(1, policy, expected_strategy_generation=None)
        result = await client.post("/api/v1/automatic/strategies/publish",
            json={**body, "confirmation_fingerprint": preview.json()["confirmation_fingerprint"]},
            headers=same_origin_headers())
    assert result.status_code == 409
    assert book.read()["revision"] == 2 and book.publication_snapshot_v1() is None


async def test_operator_publication_rejects_requests_that_bypass_loopback_nginx(tmp_path: Path) -> None:
    book = fixture_book(tmp_path)
    origin = PublicDashboardOrigin("https://dashboard.example", "dashboard.example")
    app = application(book, public_origin=origin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app, client=("203.0.113.9", 4312)),
        base_url=origin.origin,
    ) as client:
        result = await client.post("/api/v1/automatic/strategies/preview",
            json=builtin_publication_body(), headers=same_origin_headers())
    assert result.status_code == 403
    assert book.read()["revision"] == 1 and book.publication_snapshot_v1() is None
