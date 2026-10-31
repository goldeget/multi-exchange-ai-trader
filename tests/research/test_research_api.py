"""Real local native API/scheduler tests. No exchange requests or credentials."""

import asyncio

import httpx

from multi_exchange_trader.api import create_app
from multi_exchange_trader.research.automation import default_auto_config


async def test_research_boundaries_native_report_and_exact_resource_ids(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_BLOCKED_HOSTS", "fapi.binance.com,api.bybit.com")
    app = create_app()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8000"
        ) as c,
    ):
        root = "/api/v1/research"
        response = await c.get(root + "/datasets")
        assert response.status_code == 200
        datasets = response.json()["datasets"]
        assert len(datasets) == 2 and all(d["synthetic"] for d in datasets)
        key = next(d["dataset_id"] for d in datasets if d["symbol"] == "BTC-PAPER")
        detail = (await c.get(root + "/datasets/" + key)).json()
        assert detail["dataset_id"] == key and len(detail["bars"]) == 240
        payload = {"idempotency_key": "evaluation", "dataset_id": key}
        for headers in [{"Origin": "https://evil.test"}, {"Host": "evil.test"}, {"Origin": "null"}]:
            assert (
                await c.post(root + "/evaluations", json=payload, headers=headers)
            ).status_code == 403
        assert (
            await c.post(
                root + "/evaluations", content="{}", headers={"Content-Type": "text/plain"}
            )
        ).status_code == 415
        assert (
            await c.post(
                root + "/evaluations",
                content="x" * 9000,
                headers={"Content-Type": "application/json"},
            )
        ).status_code == 413
        r = await c.post(root + "/evaluations", json=payload)
        assert r.status_code == 200
        report = r.json()
        assert report["dataset_id"] == key and report["engine_version"] == "1.231.0"
        assert "net_return" in report["test"] and "max_consecutive_losses" in report["test"]
        assert (await c.post(root + "/evaluations", json=payload)).json() == report
        rid = report["reproducibility_hash"]
        assert (await c.get(root + "/evaluations/" + rid)).json() == report
        assert len((await c.get(root + "/evaluations")).json()["evaluations"]) == 1
        other = next(d["dataset_id"] for d in datasets if d["dataset_id"] != key)
        assert (
            await c.post(root + "/evaluations", json=payload | {"dataset_id": other})
        ).status_code == 409
        for path in ["/live", "/reset", "/collect", "/config"]:
            assert (await c.post(root + path, json={})).status_code == 404
        assert (await c.get(root + "/datasets/not-a-hash")).status_code == 400


async def test_background_start_pause_stop_and_restart_paused(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_BLOCKED_HOSTS", "fapi.binance.com,api.bybit.com")
    app = create_app()
    root = "/api/v1/research/runs/qa/commands"
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost:8000"
        ) as c,
    ):
        ds = (await c.get("/api/v1/research/datasets")).json()["datasets"]
        key = next(d["dataset_id"] for d in ds if d["symbol"] == "BTC-PAPER")
        command = {
            "idempotency_key": "create",
            "command": {"action": "create", "config": default_auto_config(key) | {"bar_limit": 12}},
        }
        created = await c.post(root, json=command)
        assert (
            created.status_code == 200
            and created.json()["native"]["wallet_balance"] == "10000.00000000"
        )
        assert (await c.post(root, json=command)).json() == created.json()
        assert (
            await c.post(root, json={"idempotency_key": "start", "command": {"action": "resume"}})
        ).status_code == 200
        for _ in range(40):
            current = (await c.get("/api/v1/research/runs/qa")).json()
            if current["cursor"] > 0:
                break
            await asyncio.sleep(0.1)
        assert current["cursor"] >= 1
        paused = (
            await c.post(root, json={"idempotency_key": "pause", "command": {"action": "pause"}})
        ).json()
        await asyncio.sleep(1.2)
        assert (await c.get("/api/v1/research/runs/qa")).json()["cursor"] == paused["cursor"]
        assert (
            await c.post(root, json={"idempotency_key": "start2", "command": {"action": "resume"}})
        ).status_code == 200
    app2 = create_app()
    async with (
        app2.router.lifespan_context(app2),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app2), base_url="http://localhost:8000"
        ) as c,
    ):
        recovered = (await c.get("/api/v1/research/runs/qa")).json()
        assert recovered["status"] == "PAUSED_RECOVERY"
        await asyncio.sleep(1.2)
        assert (await c.get("/api/v1/research/runs/qa")).json()["cursor"] == recovered["cursor"]
        stopped = (
            await c.post(root, json={"idempotency_key": "stop", "command": {"action": "stop"}})
        ).json()
        assert stopped["status"] == "STOPPED" and not stopped["native"]["positions"]
        assert (await c.get("/api/v1/research/runs/qa/audit")).json()["funding_entries"] == 1


async def test_scheduler_native_failure_is_not_mislabeled_operator_pause(monkeypatch):
    from multi_exchange_trader.research import automation

    monkeypatch.setenv("MARKET_DATA_BLOCKED_HOSTS", "fapi.binance.com,api.bybit.com")
    app = create_app()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost:8000"
        ) as c,
    ):
        ds = (await c.get("/api/v1/research/datasets")).json()["datasets"]
        key = next(d["dataset_id"] for d in ds if d["symbol"] == "BTC-PAPER")
        path = "/api/v1/research/runs/failure/commands"
        assert (
            await c.post(
                path,
                json={
                    "idempotency_key": "create",
                    "command": {"action": "create", "config": default_auto_config(key)},
                },
            )
        ).status_code == 200
        original = automation.sandbox_replay

        def unavailable(_):
            raise ValueError("PAPER native worker unavailable")

        monkeypatch.setattr(automation, "sandbox_replay", unavailable)
        await c.post(path, json={"idempotency_key": "start", "command": {"action": "resume"}})
        for _ in range(40):
            state = (await c.get("/api/v1/research/runs/failure")).json()
            if state["status"] != "RUNNING":
                break
            await asyncio.sleep(0.1)
        assert state["status"] == "PAUSED_ERROR" and state["reason"] == "native_or_storage_failure"
        assert state["cursor"] == 0 and state["native"]["fills"] == []
        monkeypatch.setattr(automation, "sandbox_replay", original)
        await c.post(path, json={"idempotency_key": "resume", "command": {"action": "resume"}})
        for _ in range(40):
            state = (await c.get("/api/v1/research/runs/failure")).json()
            if state["cursor"] > 0:
                break
            await asyncio.sleep(0.1)
        assert state["cursor"] >= 1
