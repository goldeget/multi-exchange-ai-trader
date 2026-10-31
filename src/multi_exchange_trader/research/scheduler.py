"""One local background loop. Only durable RUNNING synthetic sessions can advance."""

import asyncio
import contextlib
import sqlite3
import threading
import time
from datetime import UTC, datetime
from typing import Any

from .storage import ResearchStore


class TaskSlot:
    """Holds the current scheduler task so an explicit resume can replace it."""

    def __init__(self, task: asyncio.Task[None]) -> None:
        self.task = task


class _Watch:
    def __init__(self) -> None:
        self.inflight_mono: float | None = None
        self.finished: threading.Event | None = None
        self.holder: dict[str, Any] | None = None


def _step_thread(
    store: ResearchStore,
    run_id: str,
    cursor: int,
    holder: dict[str, Any],
    finished: threading.Event,
) -> None:
    try:
        holder["value"] = store.step(run_id, cursor, now=datetime.now(UTC))
    except Exception as exc:  # noqa: BLE001 - the caller classifies this after the thread ends
        holder["error"] = exc
    finally:
        finished.set()


async def _wait_thread(finished: threading.Event) -> bool:
    """Wait until the step thread ends. Cancellation does not stop that thread."""
    cancelled = False
    while not finished.is_set():
        try:
            await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            cancelled = True
            current = asyncio.current_task()
            if current is not None:
                current.uncancel()
    return cancelled


def _remember_step(health: dict[str, object], holder: dict[str, Any]) -> None:
    value = holder.get("value")
    if isinstance(value, dict):
        health["shutdown_checked"] = True
        health["shutdown_cursor"] = value.get("cursor")
        health["last_settled_cursor"] = value.get("cursor")
        health["message"] = (
            "종료할 때 진행 중이던 저장 결과를 확인했습니다. 취소로 되돌렸다고 보지 않습니다."
        )
        return
    health["shutdown_checked"] = False
    health["message"] = (
        "종료할 때 진행 중이던 저장 결과를 확인하지 못했습니다. 끝났다고 보지 않습니다."
    )


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _overdue(state: dict[str, Any], moment: datetime) -> bool:
    if state.get("status") != "RUNNING":
        return False
    raw = state.get("next_due_at")
    if not isinstance(raw, str):
        return True
    try:
        due = datetime.fromisoformat(raw)
    except ValueError:
        return True
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    return moment >= due


def _pause_message(exc: ValueError) -> str:
    text = str(exc)
    if "native worker" in text:
        return "가상 계산이 멈추어 포지션은 그대로 두고 일시정지했습니다."
    if "storage" in text:
        return "저장에 실패해서 포지션은 그대로 두고 일시정지했습니다."
    return "저장 오류가 나서 포지션은 그대로 두고 일시정지했습니다."


async def watch_scheduler(stop: asyncio.Event, health: dict[str, object], slot: TaskSlot) -> None:
    """Record a scheduler that ended without an explicit stop. Do not start it again."""
    while not stop.is_set():
        task = slot.task
        try:
            await task
        except Exception:  # noqa: BLE001 - record the crash, do not copy its text
            health["state"] = "FAIL_CLOSED"
            health["block_new_auto"] = True
            health["message"] = (
                "자동 진행이 예고 없이 멈췄습니다. 다시 시작 전에는 이어가지 않습니다."
            )
        if stop.is_set():
            health["task"] = "stopped"
            return
        if slot.task is not task:
            continue
        health["block_new_auto"] = True
        health["task"] = "exited"
        if health.get("state") not in {"FAIL_CLOSED", "STALLED", "STOPPED"}:
            health["state"] = "FAIL_CLOSED"
            health["message"] = (
                "자동 진행이 예고 없이 멈췄습니다. 다시 시작 전에는 이어가지 않습니다."
            )
        while slot.task is task and not stop.is_set():
            await asyncio.sleep(0.05)


async def scheduler(
    store: ResearchStore,
    stop: asyncio.Event,
    health: dict[str, object],
    *,
    idle_seconds: float = 1.0,
    stall_after: float = 20.0,
    stuck_after: float = 30.0,
    pulse_seconds: float = 0.5,
) -> None:
    """Advance synthetic runs until stop, stall, or an unhandled storage failure.

    ``stall_after`` and ``stuck_after`` are supervision bounds. They are not a
    recommended trading interval. A heartbeat alone does not mean work finished.
    """
    watch = _Watch()
    pulse_stop = asyncio.Event()
    last_progress = time.monotonic()

    async def pulse() -> None:
        while not pulse_stop.is_set():
            health["heartbeat_at"] = _iso(datetime.now(UTC))
            health["task"] = "alive"
            started = watch.inflight_mono
            if started is not None and time.monotonic() - started >= stuck_after:
                health["stall"] = True
                health["state"] = "STALLED"
                health["block_new_auto"] = True
                health["message"] = (
                    "자동 진행 신호는 살아 있는데 저장이 오래 끝나지 않습니다. 새 진행은 하지 않습니다."
                )
            try:
                await asyncio.wait_for(pulse_stop.wait(), timeout=pulse_seconds)
            except TimeoutError:
                continue

    pulser = asyncio.create_task(pulse())
    was_cancelled = False
    try:
        while not stop.is_set():
            if health.get("state") == "STALLED":
                return
            try:
                states = await asyncio.to_thread(store.runs)
            except ValueError:
                health.update(
                    state="FAIL_CLOSED",
                    block_new_auto=True,
                    message="저장 확인에 실패해서 자동 진행을 멈췄습니다.",
                )
                return
            except sqlite3.OperationalError as exc:
                _close_for_sql(health, exc)
                return
            except Exception:  # noqa: BLE001 - stop the loop without copying the error text
                health.update(
                    state="FAIL_CLOSED",
                    block_new_auto=True,
                    message="저장 확인 중 예기치 않은 오류로 자동 진행을 멈췄습니다.",
                )
                return
            running = [row for row in states if row.get("status") == "RUNNING"]
            health["stored_running"] = len(running)
            moment = datetime.now(UTC)
            overdue = any(_overdue(row, moment) for row in running)
            advanced = False
            for state in running:
                if stop.is_set() or health.get("state") == "STALLED":
                    break
                outcome = await _one_step(store, health, watch, state)
                if outcome == "stop":
                    return
                if outcome == "advanced":
                    advanced = True
                    health["last_completed_at"] = _iso(datetime.now(UTC))
                    health["stall"] = False
            if health.get("state") == "STALLED":
                return
            if overdue and not advanced:
                if time.monotonic() - last_progress >= stall_after:
                    health.update(
                        stall=True,
                        state="STALLED",
                        block_new_auto=True,
                        message=(
                            "자동 진행 신호는 살아 있는데 저장이 오래 멈춰 있습니다. "
                            "새 진행은 멈춥니다."
                        ),
                    )
                    return
            else:
                last_progress = time.monotonic()
            try:
                await asyncio.wait_for(stop.wait(), timeout=idle_seconds)
            except TimeoutError:
                continue
    except asyncio.CancelledError:
        was_cancelled = True
    finally:
        pulse_stop.set()
        await _settle_inflight(health, watch)
        health["task"] = "stopped"
        if not pulser.done():
            pulser.cancel()

            def _consume(done: asyncio.Task[None]) -> None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    done.exception()

            pulser.add_done_callback(_consume)
    if was_cancelled:
        raise asyncio.CancelledError


def _close_for_sql(health: dict[str, object], exc: sqlite3.OperationalError) -> None:
    locked = "locked" in str(exc).lower()
    health.update(
        state="FAIL_CLOSED",
        block_new_auto=True,
        work_differs_from_store=True,
        message=(
            "저장이 잠겨 있어 자동 진행을 멈췄습니다. 포지션은 그대로 둡니다."
            if locked
            else "저장 쓰기에 실패해서 자동 진행을 멈췄습니다. 포지션은 그대로 둡니다."
        ),
    )


async def _settle_inflight(health: dict[str, object], watch: _Watch) -> None:
    finished = watch.finished
    holder = watch.holder
    if finished is None or holder is None or finished.is_set():
        watch.inflight_mono = None
        return
    await _wait_thread(finished)
    watch.inflight_mono = None
    if health.get("shutdown_checked") is True:
        return
    error = holder.get("error")
    if error is not None and "value" not in holder:
        health["shutdown_checked"] = False
        health["message"] = (
            "종료할 때 진행 중이던 저장 결과를 확인하지 못했습니다. 끝났다고 보지 않습니다."
        )
        return
    value = holder.get("value")
    health["shutdown_checked"] = True
    if isinstance(value, dict):
        health["shutdown_cursor"] = value.get("cursor")
        health["last_settled_cursor"] = value.get("cursor")
    health["message"] = (
        "종료할 때 진행 중이던 저장 결과를 확인했습니다. 취소로 되돌렸다고 보지 않습니다."
    )


async def _one_step(
    store: ResearchStore,
    health: dict[str, object],
    watch: _Watch,
    state: dict[str, Any],
) -> str:
    """Return ``advanced``, ``paused``, or ``stop``."""
    run_id = state.get("run_id")
    cursor = state.get("cursor")
    if not isinstance(run_id, str) or not isinstance(cursor, int):
        health.update(
            state="FAIL_CLOSED",
            block_new_auto=True,
            message="저장된 자동 진행 형식이 맞지 않아 멈췄습니다.",
        )
        return "stop"
    for attempt in range(2):
        finished = threading.Event()
        holder: dict[str, Any] = {}
        threading.Thread(
            target=_step_thread,
            args=(store, run_id, cursor, holder, finished),
            daemon=True,
        ).start()
        watch.finished = finished
        watch.holder = holder
        watch.inflight_mono = time.monotonic()
        health["inflight_run"] = run_id
        if await _wait_thread(finished):
            _remember_step(health, holder)
            raise asyncio.CancelledError
        error = holder.get("error")
        result = holder.get("value")
        watch.inflight_mono = None
        health["inflight_run"] = None
        if isinstance(error, ValueError):
            await _pause_known(store, health, run_id, error)
            if health.get("state") == "FAIL_CLOSED":
                return "stop"
            return "paused"
        if isinstance(error, sqlite3.OperationalError):
            if attempt == 0 and "locked" in str(error).lower():
                continue
            _close_for_sql(health, error)
            return "stop"
        if error is not None:
            await _pause_unknown(store, health, run_id)
            return "stop"
        if health.get("state") == "STALLED" and isinstance(result, dict):
            health["shutdown_checked"] = True
            health["last_settled_cursor"] = result.get("cursor")
            health["message"] = (
                "자동 진행 신호는 살아 있는데 저장이 오래 걸렸습니다. "
                "끝난 저장 결과는 확인했고, 새 진행은 하지 않습니다."
            )
            return "stop"
        if isinstance(result, dict) and result.get("cursor") != cursor:
            health["last_settled_cursor"] = result.get("cursor")
            return "advanced"
        return "paused"
    return "stop"


async def _pause_known(
    store: ResearchStore, health: dict[str, object], run_id: str, exc: ValueError
) -> None:
    health.update(state="PAUSED_ERROR", message=_pause_message(exc))
    try:
        await asyncio.to_thread(store.pause_after_failure, run_id)
    except (ValueError, sqlite3.Error):
        health.update(
            state="FAIL_CLOSED",
            block_new_auto=True,
            work_differs_from_store=True,
            message="저장 상태를 바꾸지 못했습니다. 자동 진행을 멈춥니다. 포지션은 그대로 둡니다.",
        )


async def _pause_unknown(store: ResearchStore, health: dict[str, object], run_id: str) -> None:
    try:
        await asyncio.to_thread(store.pause_after_failure, run_id)
    except (ValueError, sqlite3.Error):
        health["work_differs_from_store"] = True
    health.update(
        state="FAIL_CLOSED",
        block_new_auto=True,
        message="예기치 않은 오류로 자동 진행을 멈췄습니다. 포지션은 그대로 둡니다.",
    )
