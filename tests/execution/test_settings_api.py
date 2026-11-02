import json

import httpx
import pytest
from fastapi import FastAPI, Request

from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook
from multi_exchange_trader.execution.settings_api import install
from multi_exchange_trader.paper.api import install as install_boundary


async def test_settings_are_local_revisioned_drafts(tmp_path):
    app = FastAPI()
    app.state.runtime_policy = RuntimePolicyBook(tmp_path / "runtime-policy.sqlite3")
    install_boundary(app)
    install(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost:8000") as client:
        initial = (await client.get("/api/v1/automatic/settings")).json()
        body = {"revision": 0, "policy": initial["policy"] | {"capital_usdt": "100"}}
        response = await client.put("/api/v1/automatic/settings", json=body)
        assert response.status_code == 200
        assert response.json()["worker_running"] is False
        assert response.json()["live_order_enabled"] is False
        assert (await client.put("/api/v1/automatic/settings", json=body)).status_code == 409
        assert (await client.put("/api/v1/automatic/settings", content='{"revision":0,"revision":1,"policy":{}}',
                                 headers={"content-type": "application/json"})).status_code == 409
        assert (await client.put("/api/v1/automatic/settings", content="x" * 65537,
                                 headers={"content-type": "application/json"})).status_code == 413
        assert (await client.get("/api/v1/automatic/settings", headers={"host": "evil.test"})).status_code == 403
        assert (await client.put("/api/v1/automatic/settings", json=body,
                                headers={"origin": "https://evil.test"})).status_code == 403


@pytest.mark.parametrize("path,limit", [
    ("/api/v1/automatic/settings", 65536),
    ("/api/v1/paper/echo", 8192),
])
async def test_boundary_stops_chunked_body_before_buffering_all_input(path, limit):
    app = FastAPI()
    install_boundary(app)
    install(app)
    consumed = 0

    async def chunks():
        nonlocal consumed
        for _ in range(100):
            consumed += 1
            yield b"x" * 4096

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost:8000") as client:
        response = await client.put(path, content=chunks(), headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert consumed == limit // 4096 + 1


async def test_boundary_caches_streamed_body_for_existing_json_routes():
    app = FastAPI()
    install_boundary(app)

    @app.post("/api/v1/paper/echo")
    async def echo(request: Request):
        return await request.json()

    async def chunks():
        yield b'{"value":'
        yield b'"retained"}'

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost:8000") as client:
        response = await client.post("/api/v1/paper/echo", content=chunks(), headers={"content-type": "application/json"})
        assert response.status_code == 200
        assert response.json() == {"value": "retained"}
        assert (await client.post("/api/v1/paper/echo", json={"value": "x" * 8192})).status_code == 413


async def test_automatic_settings_accepts_all_maximum_length_allowlists(tmp_path):
    app = FastAPI()
    app.state.runtime_policy = RuntimePolicyBook(tmp_path / "runtime-policy.sqlite3")
    install_boundary(app)
    install(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost:8000") as client:
        initial = (await client.get("/api/v1/automatic/settings")).json()
        entries = [f"S{index:03d}" + "x" * 92 for index in range(100)]
        policy = initial["policy"] | {key: entries for key in (
            "allowed_symbols", "allowed_strategies", "selected_symbols", "selected_strategies")}
        body = json.dumps({"revision": 0, "policy": policy}).encode()
        assert 32768 < len(body) < 65536

        async def chunks():
            for offset in range(0, len(body), 4096):
                yield body[offset:offset + 4096]

        response = await client.put("/api/v1/automatic/settings", content=chunks(),
                                    headers={"content-type": "application/json"})
        assert response.status_code == 200
        assert response.json()["policy"] == policy
        assert response.json()["revision"] == 1
