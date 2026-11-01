"""Anonymous local-only PAPER routes; no reset, live mode, account or exchange API."""

import asyncio
import sqlite3
from typing import Any, cast
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from .order_preview import OrderReviews
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
            "가상 거래 저장 확인에 실패해서 멈췄습니다. 기록은 그대로 둡니다.",
        )
    elif "not found" in text:
        status, reason, message = 404, "paper_not_found", "가상 장부를 찾을 수 없습니다."
    elif "idempotency" in text:
        status, reason, message = (
            409,
            "paper_idempotency_conflict",
            "같은 요청에 다른 내용은 넣을 수 없습니다.",
        )
    elif "review" in text and ("revision" in text or "expired" in text):
        status, reason, message = (
            409, "paper_review_changed_or_expired", "가상 주문 검토가 만료되었거나 장부가 바뀌었습니다. 다시 검토하세요.",
        )
    elif "margin" in text or "notional" in text:
        status, reason, message = (
            409,
            "paper_margin",
            "가상 증거금이 부족하거나 주문 금액 한도를 넘었습니다.",
        )
    elif "stale" in text:
        status, reason, message = (
            409,
            "paper_stale_mark",
            "가상 가격이 오래되었습니다. 새 가상 가격을 넣으세요.",
        )
    elif any(word in text for word in ("position", "reduce-only", "protective", "exists")):
        status, reason, message = (
            409,
            "paper_risk",
            "가상 포지션, 수량, 손절·익절 또는 이미 있는 장부를 확인하세요.",
        )
    else:
        status, reason, message = (
            400,
            "paper_input",
            "입력한 숫자, 순서, 시각을 확인하세요.",
        )
    return JSONResponse(
        status_code=status, content={"reason": reason, "message": message, "mode": "PAPER"}
    )


def service(request: Request) -> PaperStore:
    return cast(PaperStore, request.app.state.paper_store)


def reviews(request: Request) -> OrderReviews:
    value = getattr(request.app.state, "order_reviews", None)
    if not isinstance(value, OrderReviews):
        raise TypeError("PAPER review storage unavailable")
    return value


def install(application: FastAPI) -> None:
    router = APIRouter(prefix="/api/v1/paper")

    @application.middleware("http")
    async def local_paper_boundary(request: Request, call_next: Any) -> Any:
        if request.url.path.startswith(
            ("/api/v1/paper", "/api/v1/research", "/api/v1/recommendations",
             "/api/v1/settings", "/api/v1/ai", "/api/v1/overview",
             "/api/v1/diagnostics", "/api/v1/connections", "/api/v1/capabilities")
        ):
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

    @router.post("/order-previews")
    async def order_preview(request: Request) -> Any:
        try:
            return await asyncio.to_thread(reviews(request).preview, await request.json())
        except (ValueError, TypeError) as exc:
            return failure(exc)
        except sqlite3.Error:
            return failure(ValueError("PAPER review storage unavailable"))

    @router.post("/order-confirmations")
    async def order_confirmation(request: Request) -> Any:
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {"preview_id", "idempotency_key"}:
                raise ValueError("PAPER confirmation fields")
            return await asyncio.to_thread(reviews(request).confirm,
                                           body["preview_id"], body["idempotency_key"])
        except (ValueError, TypeError) as exc:
            return failure(exc)
        except sqlite3.Error:
            return failure(ValueError("PAPER review storage unavailable"))

    application.include_router(router)
