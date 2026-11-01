"""Additive local overview, capabilities, diagnostics and offline AI settings endpoints."""

import asyncio
import sqlite3
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from .ai.budget import BudgetBook
from .capabilities import capabilities
from .read_models import read_overview
from .recommendations.execution import demo_capability
from .research.api import public_scheduler


def _budget(request: Request) -> BudgetBook:
    value = getattr(request.app.state, "ai_budget", None)
    if not isinstance(value, BudgetBook):
        raise TypeError("AI storage unavailable")
    return value


def _connections(request: Request) -> dict[str, Any]:
    registered = getattr(request.app.state, "bybit_account", None) is not None
    problem = getattr(request.app.state, "bybit_account_problem", "missing_credentials")
    status = "REGISTERED_UNVERIFIED" if registered else "NOT_CONFIGURED"
    if not registered and problem == "config":
        status = "INVALID_CONFIGURATION"
    return {"schema_version": 1, "live_order_enabled": False,
            "bybit": {"registered": registered, "status": status,
                      "permission": "READ_ONLY_CLIENT", "verified_at": None,
                      "execution": "NONE", "source": "ACCOUNT_READONLY"},
            "bybit_demo": demo_capability(getattr(request.app.state, "bybit_practice", None) is not None,
                                          blocked=_practice_blocked(request)),
            "ai": {"registered": False, "status": "NOT_CONFIGURED" if isinstance(
                       getattr(request.app.state, "ai_budget", None), BudgetBook
                   ) else "STORAGE_UNAVAILABLE", "verified_at": None,
                   "mode": "OFFLINE_TEST", "external_calls": False}}


def _practice_records(request: Request) -> dict[str, Any]:
    try:
        return dict(request.app.state.recommendation_book.practice_summary())
    except (ValueError, TypeError, AttributeError, sqlite3.Error):
        return {"uncertain_count": None, "close_failed_count": None, "acknowledged_count": None}


def _practice_blocked(request: Request) -> bool:
    if getattr(request.app.state, "bybit_practice", None) is None:
        return False
    records = _practice_records(request)
    return (records["uncertain_count"] is None or records["close_failed_count"] is None
            or records["uncertain_count"] > 0 or records["close_failed_count"] > 0)


def _scheduler(request: Request, value: dict[str, Any]) -> dict[str, object]:
    status = public_scheduler(request, value["automation"]["runs"])
    if any(error["component"] == "automation" for error in value["errors"]):
        status.update(state="FAIL_CLOSED", block_new_auto=True,
                      message="자동 실행 저장 상태를 확인할 수 없습니다.")
    elif not status.get("task_alive") and status.get("state") == "READY":
        status.update(state="STOPPED", block_new_auto=True,
                      message="자동 실행 작업이 중단되어 있습니다.")
    return status


def install(application: FastAPI) -> None:
    router = APIRouter(prefix="/api/v1")

    @router.get("/capabilities")
    async def support(request: Request) -> dict[str, Any]:
        value = capabilities(
            account_registered=getattr(request.app.state, "bybit_account", None) is not None,
            practice_registered=getattr(request.app.state, "bybit_practice", None) is not None,
            practice_blocked=_practice_blocked(request),
            research_v2=getattr(request.app.state, "research_v2_supported", False) is True,
            spot_qualified=(getattr(request.app.state, "spot_paper_qualified", False) is True
                            and getattr(request.app.state, "spot_paper_store", None) is not None),
        )
        if getattr(request.app.state, "order_reviews", None) is None:
            value["manual_paper"]["synthetic_perpetual"]["order_preview"] = False
        return value

    @router.get("/overview")
    async def overview(request: Request) -> dict[str, Any]:
        value = await asyncio.to_thread(read_overview, request.app.state.paper_store,
                                        request.app.state.research_store,
                                        spot=getattr(request.app.state, "spot_paper_store", None))
        value["automation"]["scheduler"] = _scheduler(request, value)
        return value

    @router.get("/connections/status")
    async def connections(request: Request) -> dict[str, Any]:
        return _connections(request)

    @router.get("/settings/ai")
    async def settings(request: Request) -> Any:
        try:
            return await asyncio.to_thread(_budget(request).settings)
        except (ValueError, TypeError, sqlite3.Error):
            return JSONResponse(status_code=503, content={"reason": "ai_storage_unavailable"})

    @router.put("/settings/ai")
    async def save_settings(request: Request) -> Any:
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {"revision", "mode", "monthly_limit"}:
                raise ValueError("AI settings fields")
            if body["mode"] != "OFFLINE_TEST":
                return JSONResponse(status_code=409, content={"reason": "ai_provider_not_configured"})
            return await asyncio.to_thread(_budget(request).configure, body["revision"], body["monthly_limit"])
        except (ValueError, TypeError):
            return JSONResponse(status_code=409, content={"reason": "ai_settings_conflict_or_invalid"})
        except sqlite3.Error:
            return JSONResponse(status_code=503, content={"reason": "ai_storage_unavailable"})

    @router.get("/ai/budget")
    async def budget(request: Request) -> Any:
        try:
            return await asyncio.to_thread(_budget(request).summary)
        except (ValueError, TypeError, sqlite3.Error):
            return JSONResponse(status_code=503, content={"reason": "ai_storage_unavailable"})

    @router.get("/diagnostics")
    async def diagnostics(request: Request) -> dict[str, Any]:
        value = await asyncio.to_thread(read_overview, request.app.state.paper_store,
                                        request.app.state.research_store,
                                        spot=getattr(request.app.state, "spot_paper_store", None))
        scheduler = _scheduler(request, value)
        try:
            from .paper.api import reviews

            pending = await asyncio.to_thread(reviews(request).unresolved_count)
        except (ValueError, TypeError, sqlite3.Error):
            pending = None
        practice_records = await asyncio.to_thread(_practice_records, request)
        return {"schema_version": 1, "live_order_enabled": False,
                "components": {
                    "server": {"state": "RESPONDING", "meaning": "LIVENESS_ONLY"},
                    "automation": scheduler,
                    "manual_paper": {"state": "UNAVAILABLE" if any(
                        error["component"] == "manual_paper" for error in value["errors"]
                    ) else "READABLE", "unresolved_confirmations": pending,
                                     "review_state": "UNAVAILABLE" if pending is None else "READABLE"},
                    "account": _connections(request)["bybit"],
                    "bybit_demo": {**_connections(request)["bybit_demo"], **practice_records},
                    "ai": _connections(request)["ai"],
                }, "errors": value["errors"]}

    application.include_router(router)
