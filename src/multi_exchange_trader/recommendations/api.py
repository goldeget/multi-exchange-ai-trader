"""Local recommendation routes. Bybit approval can place one practice order."""

from __future__ import annotations

import sqlite3
from typing import Any, cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from .book import RecommendationBook
from .service import RecommendationService, Refusal, envelope


def _book(request: Request) -> RecommendationBook:
    return cast(RecommendationBook, request.app.state.recommendation_book)


def _service(request: Request) -> RecommendationService:
    return RecommendationService(
        book=_book(request),
        paper=request.app.state.paper_store,
        market=request.app.state.market_data_service,
        lock=request.app.state.recommendation_lock,
        clock=getattr(request.app.state, "recommendation_clock", None),
        practice=getattr(request.app.state, "bybit_practice", None),
    )


async def _body(request: Request, allowed: set[str]) -> object:
    try:
        body = await request.json()
    except Exception as exc:
        raise Refusal("요청을 확인하세요.", 400) from exc
    if not isinstance(body, dict) or set(body) - allowed:
        raise Refusal("레버리지, 수량, 가격은 여기서 정하지 않습니다.", 400)
    return body


def install(application: FastAPI) -> None:
    router = APIRouter(prefix="/api/v1/recommendations")

    @router.get("")
    async def listing(request: Request) -> JSONResponse:
        try:
            return JSONResponse(content=await _service(request).listing())
        except (ValueError, TypeError, sqlite3.Error):
            return JSONResponse(status_code=503, content={"reason": "recommendations_unavailable"})

    @router.get("/{card_id}")
    async def detail(card_id: str, request: Request) -> JSONResponse:
        try:
            return JSONResponse(content=await _service(request).detail(card_id))
        except Refusal as exc:
            return JSONResponse(status_code=exc.status_code, content={"reason": "recommendation_not_found"})
        except (ValueError, TypeError, sqlite3.Error):
            return JSONResponse(status_code=503, content={"reason": "recommendations_unavailable"})

    @router.post("/preview")
    async def preview(request: Request) -> JSONResponse:
        try:
            body = await _body(request, {"거래소", "종목", "상품"})
            payload, status = await _service(request).preview(body)
        except Refusal as exc:
            payload, status = envelope(None, exc.message, False), exc.status_code
        return JSONResponse(status_code=status, content=cast(Any, payload))

    @router.post("/{card_id}/reject")
    async def reject(card_id: str, request: Request) -> JSONResponse:
        try:
            await _body(request, set())
            payload, status = await _service(request).reject(card_id)
        except Refusal as exc:
            payload, status = envelope(None, exc.message, False), exc.status_code
        return JSONResponse(status_code=status, content=cast(Any, payload))

    @router.post("/{card_id}/approve")
    async def approve(card_id: str, request: Request) -> JSONResponse:
        try:
            await _body(request, set())
            payload, status = await _service(request).approve(card_id)
        except Refusal as exc:
            payload, status = envelope(None, exc.message, False), exc.status_code
        return JSONResponse(status_code=status, content=cast(Any, payload))

    application.include_router(router)
