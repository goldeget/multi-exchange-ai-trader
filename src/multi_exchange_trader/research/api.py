"""Loopback-only research and synthetic PAPER controls; no collection/live endpoint."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from .scheduler import TaskSlot, scheduler, watch_scheduler
from .storage import ResearchStore


def failure(error: Exception) -> JSONResponse:
    text = str(error)
    if any(
        word in text
        for word in ("storage", "reconciliation", "checksum", "schema", "native worker")
    ):
        status, reason, message = (
            503,
            "research_fail_closed",
            "자료나 기록 확인에 실패해서 멈췄습니다. 상태는 그대로 둡니다.",
        )
    elif "idempotency" in text:
        status, reason, message = (
            409,
            "research_idempotency",
            "같은 요청에 다른 내용은 넣을 수 없습니다.",
        )
    elif "not found" in text or "dataset unavailable" in text:
        status, reason, message = (
            404,
            "research_not_found",
            "자료 또는 연습 자동매매를 찾을 수 없습니다.",
        )
    elif "limit" in text or "terminal" in text or "exists" in text:
        status, reason, message = (
            409,
            "research_state",
            "한도, 이미 끝난 상태, 같은 이름을 확인하세요. 기록을 지우거나 실거래로 바꾸지는 않습니다.",
        )
    else:
        status, reason, message = (
            400,
            "research_input",
            "자료, 연습 설정, 숫자 자릿수, 고정된 명령을 확인하세요.",
        )
    return JSONResponse(
        status_code=status, content={"scope": "PHASE4_LOCAL", "reason": reason, "message": message}
    )


def service(request: Request) -> ResearchStore:
    return cast(ResearchStore, request.app.state.research_store)


def public_scheduler(request: Request, runs: list[dict[str, Any]]) -> dict[str, object]:
    """Describe the loop without treating a live process as permission to trade."""
    raw = getattr(request.app.state, "research_health", {})
    health: dict[str, object] = dict(raw) if isinstance(raw, dict) else {}
    task = getattr(request.app.state, "research_task", None)
    alive = isinstance(task, asyncio.Task) and not task.done()
    stored = sum(1 for row in runs if row.get("status") in {"RUNNING", "ENTRY_PAUSED"})
    state = health.get("state")
    differs = stored > 0 and (not alive or state in {"FAIL_CLOSED", "STALLED", "STOPPED"})
    health["task_alive"] = alive
    health["stored_running"] = stored
    health["work_differs_from_store"] = differs
    if differs and state == "READY":
        message = "저장된 상태는 진행 중인데 자동 진행은 멈춰 있습니다. 다시 시작 전에는 이어가지 않습니다."
        health["state"] = "FAIL_CLOSED"
        health["block_new_auto"] = True
        health["message"] = message
        if isinstance(raw, dict):
            raw.update(
                state="FAIL_CLOSED",
                block_new_auto=True,
                message=message,
                work_differs_from_store=True,
            )
    return {key: value for key, value in health.items() if not str(key).startswith("_")}


def install(app: FastAPI) -> None:
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        async with previous(application):
            application.state.research_store = await asyncio.to_thread(
                ResearchStore,
                Path(os.environ.get("PHASE4_DB_PATH", "work/phase4-data/research.sqlite3")),
                Path(os.environ.get("PHASE4_DATA_ROOT", "work/phase4-data/datasets")),
            )
            application.state.research_stop = asyncio.Event()
            application.state.research_health = {
                "state": "READY",
                "message": "가상 거래만 자동으로 진행합니다.",
                "heartbeat_at": None,
                "last_completed_at": None,
                "task": "starting",
                "stored_running": 0,
                "stall": False,
                "block_new_auto": False,
                "work_differs_from_store": False,
                "shutdown_checked": False,
                "task_alive": True,
            }
            application.state.research_task = asyncio.create_task(
                scheduler(
                    application.state.research_store,
                    application.state.research_stop,
                    application.state.research_health,
                )
            )
            application.state.research_slot = TaskSlot(application.state.research_task)
            application.state.research_watch = asyncio.create_task(
                watch_scheduler(
                    application.state.research_stop,
                    application.state.research_health,
                    application.state.research_slot,
                )
            )
            try:
                yield
            finally:
                application.state.research_stop.set()
                await application.state.research_slot.task
                watch = application.state.research_watch
                try:
                    await asyncio.wait_for(watch, timeout=2)
                except TimeoutError:
                    watch.cancel()
                    await asyncio.gather(watch, return_exceptions=True)

    app.router.lifespan_context = lifespan
    router = APIRouter(prefix="/api/v1/research")

    @router.get("/datasets")
    async def datasets(request: Request) -> Any:
        try:
            return {
                "datasets": await asyncio.to_thread(service(request).datasets),
                "scope": "PHASE4_LOCAL",
            }
        except (ValueError, TypeError):
            return failure(ValueError("dataset checksum"))

    @router.get("/datasets/{dataset_id}")
    async def dataset(dataset_id: str, request: Request) -> Any:
        try:
            return await asyncio.to_thread(service(request).dataset, dataset_id)
        except (ValueError, TypeError) as e:
            return failure(e)

    @router.get("/evaluations")
    async def evaluations(request: Request) -> Any:
        try:
            return {"evaluations": await asyncio.to_thread(service(request).reports)}
        except (ValueError, TypeError) as e:
            return failure(e)

    @router.get("/evaluations/{report_id}")
    async def report(report_id: str, request: Request) -> Any:
        try:
            return await asyncio.to_thread(service(request).report, report_id)
        except (ValueError, TypeError) as e:
            return failure(e)

    @router.post("/evaluations")
    async def evaluate(request: Request) -> Any:
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) not in (
                {"idempotency_key", "dataset_id"}, {"idempotency_key", "dataset_id", "config"}
            ) or ("config" in body and not isinstance(body["config"], dict)):
                raise ValueError("research body")
            return await asyncio.to_thread(
                service(request).evaluate, body["idempotency_key"], body["dataset_id"], body.get("config")
            )
        except (ValueError, TypeError) as e:
            return failure(e)

    @router.get("/runs")
    async def runs(request: Request) -> Any:
        try:
            rows = await asyncio.to_thread(service(request).runs)
            return {"runs": rows, "scheduler": public_scheduler(request, rows)}
        except (ValueError, TypeError) as e:
            return failure(e)

    @router.get("/runs/{run}")
    async def state(run: str, request: Request) -> Any:
        try:
            return await asyncio.to_thread(service(request).state, run)
        except (ValueError, TypeError) as e:
            return failure(e)

    @router.get("/runs/{run}/audit")
    async def audit(run: str, request: Request) -> Any:
        try:
            return await asyncio.to_thread(service(request).audit, run)
        except (ValueError, TypeError) as e:
            return failure(e)

    @router.post("/runs/{run}/commands")
    async def command(run: str, request: Request) -> Any:
        try:
            body = await request.json()
            if (
                not isinstance(body, dict)
                or set(body) != {"idempotency_key", "command"}
                or not isinstance(body["command"], dict)
            ):
                raise ValueError("research command")
            cmd = body["command"]
            if cmd.get("action") == "create":
                if set(cmd) != {"action", "config"} or not isinstance(cmd["config"], dict):
                    raise ValueError("research create fields")
                result = await asyncio.to_thread(
                    service(request).create, run, body["idempotency_key"], cmd["config"]
                )
            else:
                result = await asyncio.to_thread(
                    service(request).command, run, body["idempotency_key"], cmd
                )
                if cmd["action"] == "resume":
                    request.app.state.research_health.update(
                        state="READY",
                        message="다시 시작했습니다.",
                        block_new_auto=False,
                        stall=False,
                        work_differs_from_store=False,
                    )
                    slot = request.app.state.research_slot
                    if slot.task.done() and not request.app.state.research_stop.is_set():
                        slot.task = asyncio.create_task(
                            scheduler(
                                service(request),
                                request.app.state.research_stop,
                                request.app.state.research_health,
                            )
                        )
                        request.app.state.research_task = slot.task
            return result
        except (ValueError, TypeError) as e:
            return failure(e)

    app.include_router(router)
