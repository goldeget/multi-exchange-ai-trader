"""One local background loop; only durable RUNNING synthetic sessions can advance."""

import asyncio
from datetime import UTC, datetime

from .storage import ResearchStore


async def scheduler(store: ResearchStore, stop: asyncio.Event, health: dict[str, str]) -> None:
    while not stop.is_set():
        try:
            states = await asyncio.to_thread(store.runs)
        except ValueError:
            health.update(state="FAIL_CLOSED", message="저장 검증 실패 · 자동 실행 중단")
            return
        for state in states:
            if stop.is_set():
                break
            if state["status"] != "RUNNING":
                continue
            try:
                await asyncio.to_thread(
                    store.step, state["run_id"], state["cursor"], now=datetime.now(UTC)
                )
            except ValueError:
                health.update(
                    state="PAUSED_ERROR", message="native/저장 오류 · 포지션을 보존하고 일시정지"
                )
                try:
                    await asyncio.to_thread(store.pause_after_failure, state["run_id"])
                except ValueError:
                    health.update(state="FAIL_CLOSED", message="상태 확인 실패 · 자동 실행 중단")
                    return
        # Actual native work + SQL commit completes before this minimum idle wait.
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except TimeoutError:
            pass
