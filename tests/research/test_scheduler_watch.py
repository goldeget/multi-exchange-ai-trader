"""Scheduler supervision with a fake store. No exchange calls and no user database."""

import asyncio
import sqlite3
import threading
from types import SimpleNamespace

import httpx
import pytest

from multi_exchange_trader.api import create_app
from multi_exchange_trader.research.api import public_scheduler
from multi_exchange_trader.research.scheduler import scheduler


class Store:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.rows = [
            {
                "run_id": "run-1",
                "status": "RUNNING",
                "cursor": 0,
                "next_due_at": "2000-01-01T00:00:00+00:00",
            }
        ]
        self.step_calls = 0
        self.pause_calls = 0
        self.release = threading.Event()
        self.started = threading.Event()

    def runs(self) -> list[dict[str, object]]:
        return [dict(row) for row in self.rows]

    def step(self, run: str, cursor: int, now: object) -> dict[str, object]:
        self.step_calls += 1
        if self.mode == "hang":
            self.started.set()
            assert self.release.wait(2)
            self.rows[0]["cursor"] = 1
            return dict(self.rows[0])
        if self.mode == "native":
            raise ValueError("research native worker unavailable")
        if self.mode == "lock":
            raise sqlite3.OperationalError("database is locked")
        if self.mode == "boom":
            raise RuntimeError("boom")
        if self.mode == "storage":
            raise ValueError("Phase4 storage transaction failed")
        if self.mode == "advance":
            self.rows[0]["cursor"] = int(self.rows[0]["cursor"]) + 1
        return dict(self.rows[0])

    def pause_after_failure(self, run: str) -> None:
        self.pause_calls += 1
        if self.mode == "storage":
            raise ValueError("Phase4 storage transaction failed")
        self.rows[0]["status"] = "PAUSED_ERROR"

    def state(self, run: str) -> dict[str, object]:
        return dict(self.rows[0])


def _health() -> dict[str, object]:
    return {"state": "READY", "message": "", "block_new_auto": False, "stall": False}


async def test_native_failure_pauses_once() -> None:
    store = Store("native")
    stop = asyncio.Event()
    health = _health()
    task = asyncio.create_task(
        scheduler(
            store, stop, health, idle_seconds=0.01, stall_after=5, stuck_after=5, pulse_seconds=0.05
        )  # type: ignore[arg-type]
    )
    for _ in range(50):
        if store.pause_calls:
            break
        await asyncio.sleep(0.02)
    assert store.pause_calls == 1
    await asyncio.sleep(0.06)
    assert store.step_calls == 1
    assert health["state"] == "PAUSED_ERROR"
    assert "가상 계산" in str(health["message"])
    stop.set()
    await asyncio.wait_for(task, timeout=2)


async def test_storage_failure_stops_instead_of_retrying() -> None:
    store = Store("storage")
    stop = asyncio.Event()
    health = _health()
    task = asyncio.create_task(
        scheduler(
            store, stop, health, idle_seconds=0.01, stall_after=5, stuck_after=5, pulse_seconds=0.05
        )  # type: ignore[arg-type]
    )
    await asyncio.wait_for(task, timeout=2)
    assert store.step_calls == 1
    assert health["state"] == "FAIL_CLOSED"
    assert health["block_new_auto"] is True


async def test_database_lock_retries_once_then_stops() -> None:
    store = Store("lock")
    stop = asyncio.Event()
    health = _health()
    task = asyncio.create_task(
        scheduler(
            store, stop, health, idle_seconds=0.01, stall_after=5, stuck_after=5, pulse_seconds=0.05
        )  # type: ignore[arg-type]
    )
    await asyncio.wait_for(task, timeout=2)
    assert store.step_calls == 2
    assert health["state"] == "FAIL_CLOSED"
    assert "잠겨" in str(health["message"])


async def test_unexpected_error_stops_the_loop() -> None:
    store = Store("boom")
    stop = asyncio.Event()
    health = _health()
    task = asyncio.create_task(
        scheduler(
            store, stop, health, idle_seconds=0.01, stall_after=5, stuck_after=5, pulse_seconds=0.05
        )  # type: ignore[arg-type]
    )
    await asyncio.wait_for(task, timeout=2)
    assert store.step_calls == 1
    assert store.pause_calls == 1
    assert health["state"] == "FAIL_CLOSED"
    assert health["block_new_auto"] is True


async def test_heartbeat_can_be_alive_while_work_is_stalled() -> None:
    store = Store("noop")
    stop = asyncio.Event()
    health = _health()
    task = asyncio.create_task(
        scheduler(
            store,  # type: ignore[arg-type]
            stop,
            health,
            idle_seconds=0.01,
            stall_after=0.05,
            stuck_after=30,
            pulse_seconds=0.02,
        )
    )
    await asyncio.wait_for(task, timeout=2)
    assert health["state"] == "STALLED"
    assert isinstance(health["heartbeat_at"], str)
    assert health.get("last_completed_at") is None
    assert 1 < store.step_calls < 40
    assert health["block_new_auto"] is True


async def test_slow_step_is_not_treated_as_cancelled() -> None:
    store = Store("hang")
    stop = asyncio.Event()
    health = _health()
    task = asyncio.create_task(
        scheduler(
            store,  # type: ignore[arg-type]
            stop,
            health,
            idle_seconds=1,
            stall_after=30,
            stuck_after=0.05,
            pulse_seconds=0.02,
        )
    )
    assert await asyncio.to_thread(store.started.wait, 2)
    for _ in range(50):
        if health.get("state") == "STALLED":
            break
        await asyncio.sleep(0.02)
    assert health["state"] == "STALLED"
    assert store.rows[0]["cursor"] == 0
    store.release.set()
    await asyncio.wait_for(task, timeout=2)
    assert store.rows[0]["cursor"] == 1
    assert health.get("last_settled_cursor") == 1
    assert store.step_calls == 1
    assert "확인했고" in str(health["message"])


async def test_cancelling_the_loop_still_reads_the_finished_step() -> None:
    store = Store("hang")
    stop = asyncio.Event()
    health = _health()
    task = asyncio.create_task(
        scheduler(
            store,  # type: ignore[arg-type]
            stop,
            health,
            idle_seconds=1,
            stall_after=30,
            stuck_after=30,
            pulse_seconds=0.05,
        )
    )
    assert await asyncio.to_thread(store.started.wait, 2)
    task.cancel()
    await asyncio.sleep(0.05)
    assert store.rows[0]["cursor"] == 0
    store.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.rows[0]["cursor"] == 1
    assert health.get("shutdown_checked") is True
    assert health.get("shutdown_cursor") == 1
    assert "되돌렸다고 보지 않습니다" in str(health["message"])


def test_saved_running_without_a_live_loop_is_a_difference() -> None:
    state = SimpleNamespace(
        research_health={"state": "READY", "message": "대기"}, research_task=None
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    reported = public_scheduler(request, [{"status": "RUNNING"}])  # type: ignore[arg-type]
    assert reported["work_differs_from_store"] is True
    assert reported["state"] == "FAIL_CLOSED"
    assert state.research_health["block_new_auto"] is True


async def test_ready_is_not_trading_ready(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHASE4_DB_PATH", str(tmp_path / "research.sqlite3"))
    monkeypatch.setenv("PHASE4_DATA_ROOT", str(tmp_path / "datasets"))
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv("BYBIT_ENV_FILE", "off")
    monkeypatch.delenv("BYBIT_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_API_SECRET", raising=False)
    monkeypatch.setenv("MARKET_DATA_BLOCKED_HOSTS", "api.bybit.com,fapi.binance.com")
    app = create_app()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        assert (await client.get("/api/v1/health")).json() == {"status": "ok"}
        ready = (await client.get("/api/v1/ready")).json()
        assert ready["실거래준비"] is False
        assert ready["기능"]["시세"]["읽기화면차단"] is False
        assert "아닙니다" in ready["의미"]
        app.state.research_health["state"] = "FAIL_CLOSED"
        app.state.research_health["block_new_auto"] = True
        again = (await client.get("/api/v1/ready")).json()
        assert again["실거래준비"] is False
        assert again["기능"]["시세"]["읽기화면차단"] is False
        assert again["기능"]["계좌조회"]["읽기화면차단"] is False
        assert again["기능"]["가상자동실행"]["신규자동진행"] is False
