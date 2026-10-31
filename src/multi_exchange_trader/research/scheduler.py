"""One local background loop; only durable RUNNING synthetic sessions can advance."""

import asyncio
from datetime import UTC, datetime

from .storage import ResearchStore


async def scheduler(store: ResearchStore, stop: asyncio.Event, health: dict[str, str]) -> None:
    while not stop.is_set():
        try:
            states = await asyncio.to_thread(store.runs)
        except ValueError:
            health.update(state="FAIL_CLOSED", message="저장 확인에 실패해서 자동 진행을 멈췄습니다.")
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
                    state="PAUSED_ERROR", message="저장 오류가 나서 포지션은 그대로 두고 일시정지했습니다."
                )
                try:
                    await asyncio.to_thread(store.pause_after_failure, state["run_id"])
                except ValueError:
                    health.update(state="FAIL_CLOSED", message="상태 확인에 실패해서 자동 진행을 멈췄습니다.")
                    return
        # Actual native work + SQL commit completes before this minimum idle wait.
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except TimeoutError:
            pass
