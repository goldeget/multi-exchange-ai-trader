"""Local synthetic PAPER mutation boundary and actual engine API integration."""

import httpx
import pytest

from multi_exchange_trader.api import create_app


@pytest.mark.asyncio
async def test_paper_json_origin_host_and_native_command_idempotency(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "api.sqlite3"))
    monkeypatch.setenv("MARKET_DATA_BLOCKED_HOSTS", "fapi.binance.com,api.bybit.com")
    app = create_app()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost:8000"
        ) as c,
    ):
        path = "/api/v1/paper/scenarios/demo/commands"
        payload = {"idempotency_key": "seed", "command": {"kind": "create"}}
        for headers in [
            {"Origin": "https://evil.test"},
            {"Origin": "null"},
            {"Host": "evil.test"},
            {"Sec-Fetch-Site": "cross-site"},
        ]:
            assert (await c.post(path, json=payload, headers=headers)).status_code == 403
        assert (
            await c.post(path, content="{}", headers={"Content-Type": "text/plain"})
        ).status_code == 415
        assert (
            await c.post(path, content="x" * 9000, headers={"Content-Type": "application/json"})
        ).status_code == 413
        good = await c.post(path, json=payload, headers={"Origin": "http://localhost:5173"})
        assert good.status_code == 200 and good.json()["wallet_balance"] == "10000.00000000"
        assert (await c.post(path, json=payload)).json() == good.json()
        body = {
            "idempotency_key": "entry",
            "command": {
                "kind": "enter",
                "instrument": "BTC-PAPER",
                "side": "long",
                "quantity": "0.100",
                "leverage": 10,
            },
        }
        opened = await c.post(path, json=body)
        assert opened.status_code == 200 and len(opened.json()["fills"]) == 1
        assert (
            await c.post(path, json=body | {"command": body["command"] | {"quantity": "0.200"}})
        ).status_code == 409
        assert (await c.get("/api/v1/paper/scenarios/demo")).json() == opened.json()
        assert (await c.get("/api/v1/paper/scenarios/demo/audit")).json()["funding_entries"] == 1
        assert (await c.post("/api/v1/paper/live", json={})).status_code == 404
        assert (await c.post("/api/v1/paper/reset", json={})).status_code == 404
