"""Real ASGI route, original authentication and in-memory saved-store load seams."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from multi_exchange_trader.api import create_app

from . import test_demo_view_v1 as saved_fixtures
from .test_demo_view_v1 import KEY, SECRET

setup = saved_fixtures.setup

_SEND = httpx.AsyncClient.send


@pytest.fixture
def app(setup: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from multi_exchange_trader.account import demo_view_v1

    service, wire, resolver = setup
    wire.now = datetime.now(UTC)
    monkeypatch.setenv("PUBLIC_DASHBOARD_ORIGIN", "https://dashboard.example")
    monkeypatch.setattr(demo_view_v1, "default_account_profile_store",
                        lambda _source: service._profile_store)

    async def send(client: httpx.AsyncClient, request: httpx.Request,
                   **kwargs: Any) -> httpx.Response:
        if request.url.host == "api-demo.bybit.com":
            response = await wire.send(client, request, **kwargs)
            assert isinstance(response, httpx.Response)
            return response
        return await _SEND(client, request, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    application = create_app(credential_resolver=resolver)
    assert not wire.paths
    return application, wire, service


async def test_demo_endpoint_uses_original_session_and_public_account_boundary(app: Any) -> None:
    application, wire, _service = app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                base_url="https://dashboard.example") as client:
        for headers in ({"Host": "wrong.example"}, {"Origin": "https://wrong.example"}):
            assert (await client.get("/api/v1/account/bybit/demo/view", headers=headers)).status_code == 403
        assert not wire.paths
        response = await client.get("/api/v1/account/bybit/demo/view")
        assert response.status_code == 200
        body = response.json()
        assert body["상태코드"] == "ok" and body["환경코드"] == "demo"
        assert body["잔고"]["총자산평가"] == "100.5"
        assert body["주문보냄"] is False and body["실거래켜짐"] is False
        assert body["주문판단에사용"] is False
        assert KEY not in response.text and SECRET not in response.text
        assert all(owner.is_closed for owner in wire.clients)
        assert (await client.post("/api/v1/account/bybit/orders", json={})).status_code == 403
        # Legacy LIVE read remains a separate route and never reuses the DEMO result.
        legacy = await client.get("/api/v1/account/bybit/view")
        assert legacy.json()["잔고"] is None


@pytest.mark.parametrize("kind", ["missing-profile", "provider-error"])
async def test_missing_profile_and_provider_failure_publish_safe_empty_dto(
    app: Any, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    application, wire, service = app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                base_url="https://dashboard.example") as client:
        if kind == "missing-profile":
            monkeypatch.setattr(service._profile_store, "load", lambda: None)
        else:
            wire.wallet_error = True
        response = await client.get("/api/v1/account/bybit/demo/view")
        assert response.json()["상태코드"] == ("config" if kind == "missing-profile" else "error")
        assert response.json()["환경코드"] == "demo" and response.json()["잔고"] is None
        assert KEY not in response.text and SECRET not in response.text
        if kind == "missing-profile":
            assert not wire.paths
        else:
            assert all(owner.is_closed for owner in wire.clients)


async def test_cancelled_asgi_read_closes_request_owned_session(app: Any) -> None:
    application, wire, _service = app
    wire.block = True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                base_url="https://dashboard.example") as client:
        task = asyncio.create_task(client.get("/api/v1/account/bybit/demo/view"))
        await asyncio.wait_for(wire.entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert all(owner.is_closed for owner in wire.clients)
