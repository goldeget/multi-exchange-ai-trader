"""Operator policy drafts: no process or venue activation side effects."""

import asyncio
import sqlite3
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from .runtime_policy import RuntimePolicyBook, parse_json

AUTOMATIC_BODY_LIMIT = 64 * 1024


def install(application: FastAPI) -> None:
    router = APIRouter(prefix="/api/v1/automatic")

    def book(request: Request) -> RuntimePolicyBook:
        value = getattr(request.app.state, "runtime_policy", None)
        if not isinstance(value, RuntimePolicyBook):
            raise OSError("policy storage unavailable")
        return value

    @router.get("/settings")
    async def read(request: Request) -> Any:
        try:
            return await asyncio.to_thread(book(request).read)
        except (ValueError, TypeError, OSError, sqlite3.Error):
            return JSONResponse(status_code=503, content={"reason": "automatic_storage_unavailable"})

    @router.put("/settings")
    async def save(request: Request) -> Any:
        return await write(request, migration=False)

    @router.post("/settings/migrate")
    async def migrate(request: Request) -> Any:
        return await write(request, migration=True)

    async def write(request: Request, *, migration: bool) -> Any:
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return JSONResponse(status_code=415, content={"reason": "paper_json_required"})
        try:
            # Bound bytes before JSON parsing, including requests without Content-Length.
            chunks = bytearray()
            async for chunk in request.stream():
                if len(chunks) + len(chunk) > AUTOMATIC_BODY_LIMIT:
                    return JSONResponse(status_code=413, content={"reason": "automatic_body_limit"})
                chunks.extend(chunk)
            value = parse_json(bytes(chunks))
            expected = {"schema_version", "revision"} if migration else {
                "schema_version", "revision", "policy", "expected_strategy_generation"}
            if (type(value) is not dict or set(value) != expected
                    or type(value["schema_version"]) is not int
                    or value["schema_version"] != (1 if migration else 2)):
                raise ValueError("settings fields")
            if migration:
                return await asyncio.to_thread(book(request).migrate_v1, value["revision"])
            return await asyncio.to_thread(book(request).save_v2, value["revision"], value["policy"],
                                           expected_strategy_generation=value["expected_strategy_generation"])
        except (ValueError, TypeError):
            return JSONResponse(status_code=409, content={"reason": "automatic_settings_conflict_or_invalid"})
        except (OSError, sqlite3.Error):
            return JSONResponse(status_code=503, content={"reason": "automatic_storage_unavailable"})

    application.include_router(router)
