"""Local Korean Bybit account routes. Order routes always refuse."""

from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from .bybit_readonly import BybitReadOnlyClient
from .config import live_trading_enabled
from .models import AccountView
from .service import build_account_view, publish


def is_local_request(request: Request) -> bool:
    try:
        parsed = urlsplit("http://" + request.headers.get("host", ""))
    except ValueError:
        return False
    if parsed.username or parsed.password:
        return False
    return parsed.hostname in {"localhost", "127.0.0.1", "::1"}


def _client(request: Request) -> BybitReadOnlyClient | None:
    client = getattr(request.app.state, "bybit_account", None)
    if isinstance(client, BybitReadOnlyClient):
        return client
    return None


def _problem(request: Request) -> str | None:
    problem = getattr(request.app.state, "bybit_account_problem", "missing_credentials")
    if problem is None or isinstance(problem, str):
        return problem
    return "config"


async def account_view(request: Request) -> AccountView:
    client = _client(request)
    problem = None if client is not None else _problem(request)
    view = await build_account_view(client, problem, live_trading=live_trading_enabled())
    return publish(view)


def refuse_mutation(_request: Request) -> JSONResponse:
    """Refuse orders and leverage changes.

    live_trading_enabled defaults to false. This read-only process is not an
    order sender even when the flag is true. Future orders belong to one
    NautilusTrader execution path, which is not started here.
    """
    enabled = live_trading_enabled()
    if enabled:
        reason = "read_only_client"
        message = (
            "실거래 설정이 켜져 있어도 이 클라이언트는 읽기 전용입니다. "
            "주문을 보내지 않았습니다. 나중 주문은 Nautilus 한 경로만 사용합니다."
        )
    else:
        reason = "live_trading_disabled"
        message = "실거래가 꺼져 있어 주문을 거부했습니다. 주문을 보내지 않았습니다."
    return JSONResponse(
        status_code=403,
        content={
            "받아들임": False,
            "실거래켜짐": enabled,
            "이유": reason,
            "메시지": message,
            "주문보냄": False,
        },
    )


def install(application: FastAPI) -> None:
    router = APIRouter(prefix="/api/v1/account/bybit")

    @application.middleware("http")
    async def local_account_boundary(request: Request, call_next: Any) -> Any:
        if request.url.path.startswith("/api/v1/account") and not is_local_request(request):
            return JSONResponse(
                status_code=403,
                content={
                    "상태코드": "local_only",
                    "연결상태": "오류",
                    "메시지": "계좌 조회는 이 컴퓨터에서만 볼 수 있습니다.",
                    "잔고": None,
                    "포지션": None,
                    "미체결주문": None,
                },
            )
        return await call_next(request)

    @router.get("/view", response_model=AccountView)
    async def view(request: Request) -> AccountView:
        return await account_view(request)

    @router.post("/orders")
    async def orders(request: Request) -> JSONResponse:
        return refuse_mutation(request)

    @router.post("/leverage")
    async def leverage(request: Request) -> JSONResponse:
        return refuse_mutation(request)

    application.include_router(router)
