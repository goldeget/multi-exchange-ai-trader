"""Anonymous local-only PAPER routes; no reset, live mode, account or exchange API."""

import asyncio
from typing import Any, cast
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from .store import PaperStore

ORIGINS = {
    f"http://{host}:{port}" for host in ("localhost", "127.0.0.1", "[::1]") for port in (5173, 8000)
}


def failure(error: Exception) -> JSONResponse:
    text = str(error)
    if any(word in text for word in ("storage", "schema", "reconciliation", "worker")):
        status, reason, message = (
            503,
            "paper_fail_closed",
            "PAPER 저장/엔진 검증이 실패해 중단했습니다. 원장을 보존하고 운영 환경을 확인하세요.",
        )
    elif "not found" in text:
        status, reason, message = 404, "paper_not_found", "가상 시나리오를 찾을 수 없습니다."
    elif "idempotency" in text:
        status, reason, message = (
            409,
            "paper_idempotency_conflict",
            "같은 명령 키에 다른 내용은 사용할 수 없습니다.",
        )
    elif "margin" in text or "notional" in text:
        status, reason, message = (
            409,
            "paper_margin",
            "가상 가용증거금 또는 모의 명목금액 한도를 확인하세요.",
        )
    elif "stale" in text:
        status, reason, message = (
            409,
            "paper_stale_mark",
            "가상 시세가 오래됐습니다. 새 로컬 가상 tick을 추가하세요.",
        )
    elif any(word in text for word in ("position", "reduce-only", "protective", "exists")):
        status, reason, message = (
            409,
            "paper_risk",
            "가상 포지션·수량·보호조건 또는 기존 시나리오를 확인하세요.",
        )
    else:
        status, reason, message = (
            400,
            "paper_input",
            "PAPER 입력·정밀도·시퀀스·시각 규칙을 확인하세요.",
        )
    return JSONResponse(
        status_code=status, content={"reason": reason, "message": message, "mode": "PAPER"}
    )


def service(request: Request) -> PaperStore:
    return cast(PaperStore, request.app.state.paper_store)


def install(application: FastAPI) -> None:
    router = APIRouter(prefix="/api/v1/paper")

    @application.middleware("http")
    async def local_paper_boundary(request: Request, call_next: Any) -> Any:
        if request.url.path.startswith(("/api/v1/paper", "/api/v1/research")):
            try:
                host = urlsplit("http://" + request.headers.get("host", ""))
                if (
                    host.hostname not in ("localhost", "127.0.0.1", "::1")
                    or host.username
                    or host.password
                ):
                    raise ValueError("nonlocal host")
                _ = host.port
            except ValueError:
                return JSONResponse(status_code=403, content={"reason": "paper_local_only"})
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                origin = request.headers.get("origin")
                if (origin is not None and origin not in ORIGINS) or request.headers.get(
                    "sec-fetch-site"
                ) == "cross-site":
                    return JSONResponse(status_code=403, content={"reason": "paper_local_only"})
                if (
                    request.headers.get("content-type", "").split(";")[0].strip().lower()
                    != "application/json"
                ):
                    return JSONResponse(status_code=415, content={"reason": "paper_json_required"})
                try:
                    size = int(request.headers.get("content-length", "0"))
                except ValueError:
                    return JSONResponse(status_code=400, content={"reason": "paper_input"})
                if size > 8192 or len(await request.body()) > 8192:
                    return JSONResponse(status_code=413, content={"reason": "paper_input_limit"})
        return await call_next(request)

    @router.get("/scenarios")
    async def scenarios(request: Request) -> Any:
        try:
            return {
                "scenarios": await asyncio.to_thread(service(request).scenarios),
                "mode": "PAPER",
                "synthetic": True,
            }
        except (ValueError, TypeError) as exc:
            return failure(exc)

    @router.get("/scenarios/{scenario}")
    async def state(scenario: str, request: Request) -> Any:
        try:
            return await asyncio.to_thread(service(request).state, scenario)
        except (ValueError, TypeError) as exc:
            return failure(exc)

    @router.get("/scenarios/{scenario}/audit")
    async def audit(scenario: str, request: Request) -> Any:
        try:
            return await asyncio.to_thread(service(request).audit, scenario)
        except (ValueError, TypeError) as exc:
            return failure(exc)

    @router.post("/scenarios/{scenario}/commands")
    async def command(scenario: str, request: Request) -> Any:
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {"idempotency_key", "command"}:
                raise ValueError("PAPER JSON command fields")
            if not isinstance(body["command"], dict):
                raise TypeError("PAPER command object required")
            return await asyncio.to_thread(
                service(request).command, scenario, body["idempotency_key"], body["command"]
            )
        except (ValueError, TypeError) as exc:
            return failure(exc)

    application.include_router(router)
