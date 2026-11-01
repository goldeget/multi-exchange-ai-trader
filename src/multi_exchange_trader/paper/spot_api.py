"""Explicit separate cash ledger. Spot placement only reaches the review/confirm contract."""

import asyncio
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from .api import failure
from .spot_contract import VERSION
from .spot_store import SpotPaperStore


def service(request: Request) -> SpotPaperStore:
    value = getattr(request.app.state, "spot_paper_store", None)
    if not isinstance(value, SpotPaperStore):
        raise TypeError("SPOT storage unavailable")
    return value


def install(app: FastAPI) -> None:
    router = APIRouter(prefix="/api/v1/paper/spot/scenarios")

    @router.get("")
    async def scenarios(request: Request) -> Any:
        try:
            return {"scenarios": await asyncio.to_thread(service(request).scenarios),
                    "product": "SPOT", "config_revision": VERSION, "mode": "PAPER", "synthetic": True}
        except (ValueError, TypeError) as exc:
            return failure(exc)

    @router.get("/{scenario}")
    async def state(scenario: str, request: Request) -> Any:
        try:
            return await asyncio.to_thread(service(request).state, scenario)
        except (ValueError, TypeError) as exc:
            return failure(exc)

    @router.get("/{scenario}/audit")
    async def audit(scenario: str, request: Request) -> Any:
        try:
            return await asyncio.to_thread(service(request).audit, scenario)
        except (ValueError, TypeError) as exc:
            return failure(exc)

    @router.post("/{scenario}/commands")
    async def command(scenario: str, request: Request) -> Any:
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {"idempotency_key", "command"}:
                raise ValueError("SPOT command envelope")
            payload = body["command"]
            if not isinstance(payload, dict) or payload.get("kind") not in {"create", "tick", "cancel"}:
                return JSONResponse(status_code=409, content={"reason": "spot_order_review_required", "mode": "PAPER"})
            if payload["kind"] != "cancel" and getattr(request.app.state, "spot_paper_qualified", False) is not True:
                return JSONResponse(status_code=409, content={"reason": "spot_not_qualified", "mode": "PAPER"})
            return await asyncio.to_thread(service(request).command, scenario, body["idempotency_key"], payload)
        except (ValueError, TypeError) as exc:
            return failure(exc)

    app.include_router(router)
