import json

import httpx
import pytest
from fastapi import FastAPI, Request
from fixtures.runtime_policy_v1 import load_v1

from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook
from multi_exchange_trader.execution.settings_api import install
from multi_exchange_trader.paper.api import install as install_boundary


async def test_typed_capability_header_union_and_finite_old_browser_refusal(tmp_path):
    from test_policy_sections import policy_fixture
    app = FastAPI()
    app.state.runtime_policy = RuntimePolicyBook(tmp_path / "typed.sqlite3")
    install(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost:8000") as client:
        initial = await client.get("/api/v1/automatic/settings")
        assert initial.headers.get("X-Automatic-Policy-Sections-Version") == "2"
        body = {"schema_version": 2, "revision": 0, "policy": policy_fixture(),
                "expected_strategy_generation": None, "policy_sections_version": 2}
        for marker in (None, True, "2", 1):
            bad = await client.put("/api/v1/automatic/settings", json=body | {"policy_sections_version": marker})
            assert bad.status_code == 409
            assert bad.json() == {"reason": "automatic_settings_conflict_or_invalid"}
        result = await client.put("/api/v1/automatic/settings", json=body)
        assert result.status_code == 200
        assert result.headers.get("X-Automatic-Policy-Sections-Version") == "2"
        assert len(result.json()) == 8 and result.json()["missing_fields"] == []
        assert result.json()["policy"]["risk_v2"]["max_stop_gap_quote_per_base"] == "1.00"
        old = {"schema_version": 2, "revision": 1, "policy": initial.json()["policy"], "expected_strategy_generation": None}
        refused = await client.put("/api/v1/automatic/settings", json=old)
        assert refused.status_code == 409
        assert (await client.get("/api/v1/automatic/settings")).json() == result.json()


async def test_settings_are_local_revisioned_drafts(tmp_path):
    app = FastAPI()
    app.state.runtime_policy = RuntimePolicyBook(tmp_path / "runtime-policy.sqlite3")
    install_boundary(app)
    install(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost:8000") as client:
        initial = (await client.get("/api/v1/automatic/settings")).json()
        body = {"schema_version": 2, "revision": 0, "expected_strategy_generation": None,
                "policy": initial["policy"] | {"legacy_v1": initial["policy"]["legacy_v1"] | {"capital_usdt": "100"}}}
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
        policy = initial["policy"] | {"legacy_v1": initial["policy"]["legacy_v1"] | {key: entries for key in (
            "allowed_symbols", "allowed_strategies", "selected_symbols", "selected_strategies")}}
        body = json.dumps({"schema_version": 2, "revision": 0, "policy": policy,
                           "expected_strategy_generation": None}).encode()
        assert 32768 < len(body) < 65536

        async def chunks():
            for offset in range(0, len(body), 4096):
                yield body[offset:offset + 4096]

        response = await client.put("/api/v1/automatic/settings", content=chunks(),
                                    headers={"content-type": "application/json"})
        assert response.status_code == 200
        assert response.json()["policy"] == policy
        assert response.json()["revision"] == 1


async def test_explicit_migration_http_union_and_no_automatic_upgrade(tmp_path):
    path = tmp_path / "old.sqlite3"
    load_v1()(path)
    app = FastAPI()
    app.state.runtime_policy = RuntimePolicyBook(path)
    install_boundary(app)
    install(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost:8000") as client:
        initial = (await client.get("/api/v1/automatic/settings")).json()
        assert initial["schema_version"] == 1
        for body in ({"revision": 0, "policy": initial["policy"]},
                     {"schema_version": 1, "revision": 0, "policy": initial["policy"]}):
            assert (await client.put("/api/v1/automatic/settings", json=body)).status_code == 409
        response = await client.post("/api/v1/automatic/settings/migrate", json={"schema_version": 1, "revision": 0})
        assert response.status_code == 200
        assert response.headers.get("X-Automatic-Policy-Sections-Version") == "2"
        migrated = response.json()
        assert migrated["schema_version"] == 2
        assert migrated["revision"] == 1
        assert migrated["policy"]["legacy_v1"] == initial["policy"]
        assert migrated["desired_state"] == "STOPPED"
        assert migrated["worker_running"] is migrated["live_order_enabled"] is False
        repeated = await client.post("/api/v1/automatic/settings/migrate", json={"schema_version": 1, "revision": 0})
        assert repeated.status_code == 409
        assert repeated.json() == {"reason": "automatic_settings_conflict_or_invalid"}
        assert (await client.get("/api/v1/automatic/settings")).json() == migrated


@pytest.mark.parametrize("method,path", [("PUT", "/api/v1/automatic/settings"),
    ("POST", "/api/v1/automatic/settings/migrate")])
async def test_both_writes_bound_duplicate_json_and_safe_errors_without_boundary(tmp_path, method, path):
    app = FastAPI()
    app.state.runtime_policy = RuntimePolicyBook(tmp_path / "synthetic.sqlite3")
    install(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost:8000") as client:
        before = app.state.runtime_policy.read()
        for body in (b'{"schema_version":1,"revision":0,"nested":{"x":1,"x":2}}',
                     b'NaN', b'{"secret-path-test":'):
            response = await client.request(method, path, content=body, headers={"content-type": "application/json"})
            assert response.status_code == 409
            assert response.json() == {"reason": "automatic_settings_conflict_or_invalid"}
        consumed = 0
        async def chunks():
            nonlocal consumed
            for _ in range(100):
                consumed += 1
                yield b"x" * 4096
        response = await client.request(method, path, content=chunks(), headers={"content-type": "application/json"})
        assert response.status_code == 413
        assert consumed == 17
        assert app.state.runtime_policy.read() == before


@pytest.mark.parametrize("method,path", [("PUT", "/api/v1/automatic/settings"),
    ("POST", "/api/v1/automatic/settings/migrate")])
async def test_both_write_routes_keep_host_origin_and_content_type_boundary(tmp_path, method, path):
    app = FastAPI()
    app.state.runtime_policy = RuntimePolicyBook(tmp_path / "synthetic.sqlite3")
    install_boundary(app)
    install(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost:8000") as client:
        for headers, status in [({"host": "evil.test", "content-type": "application/json"}, 403),
                                ({"origin": "https://evil.test", "content-type": "application/json"}, 403),
                                ({"content-type": "text/plain"}, 415)]:
            assert (await client.request(method, path, content="{}", headers=headers)).status_code == status


@pytest.mark.parametrize("method,path", [("GET", "/api/v1/automatic/settings"),
    ("PUT", "/api/v1/automatic/settings"), ("POST", "/api/v1/automatic/settings/migrate")])
async def test_unavailable_storage_returns_only_safe_503(method, path):
    app = FastAPI()
    install(app)
    body = {"schema_version": 1, "revision": 0} if method == "POST" else {
        "schema_version": 2, "revision": 0, "policy": {}, "expected_strategy_generation": None}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost:8000") as client:
        response = await client.request(method, path, json=body)
        assert response.status_code == 503
        assert response.json() == {"reason": "automatic_storage_unavailable"}


async def test_nested_duplicate_in_otherwise_valid_v2_cannot_save(tmp_path):
    app = FastAPI()
    app.state.runtime_policy = RuntimePolicyBook(tmp_path / "synthetic.sqlite3")
    install(app)
    initial = app.state.runtime_policy.read()
    body = json.dumps({"schema_version": 2, "revision": 0, "policy": initial["policy"],
                       "expected_strategy_generation": None}).replace(
                           '"capital_usdt": null', '"capital_usdt": null,"capital_usdt": "100"')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost:8000") as client:
        response = await client.put("/api/v1/automatic/settings", content=body,
                                    headers={"content-type": "application/json"})
        assert response.status_code == 409
        assert app.state.runtime_policy.read() == initial


async def test_duplicate_revision_in_otherwise_valid_v1_cannot_migrate(tmp_path):
    path = tmp_path / "old.sqlite3"
    load_v1()(path)
    app = FastAPI()
    app.state.runtime_policy = RuntimePolicyBook(path)
    install(app)
    initial = app.state.runtime_policy.read()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost:8000") as client:
        response = await client.post("/api/v1/automatic/settings/migrate",
            content='{"schema_version":1,"revision":0,"revision":0}',
            headers={"content-type": "application/json"})
        assert response.status_code == 409
        assert app.state.runtime_policy.read() == initial
