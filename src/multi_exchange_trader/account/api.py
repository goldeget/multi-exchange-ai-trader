"""Local Korean Bybit account routes. Order routes always refuse."""

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from .bybit_readonly import BybitReadOnlyClient
from .config import live_trading_enabled
from .freshness import CACHE_TTL_SECONDS, freshness_label
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


def cache_ttl_seconds() -> float:
    """Short shared-read window. Zero disables the saved copy. Age is still shown."""
    raw = os.environ.get("ACCOUNT_VIEW_CACHE_SECONDS", "").strip()
    if not raw:
        return CACHE_TTL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return CACHE_TTL_SECONDS
    if value < 0 or value > 60:
        return CACHE_TTL_SECONDS
    return value


def _age_seconds(now: float, stored_at: float) -> int:
    return max(0, int(now - stored_at))


def _decorate(view: AccountView, *, age: int, cache_hit: bool, shared: bool) -> AccountView:
    return view.model_copy(
        update={
            "캐시사용": cache_hit,
            "캐시나이초": age if cache_hit else 0,
            "데이터나이초": age,
            "동시요청공유": shared,
            "신선도표시": freshness_label(age, view.상태코드),
            "주문판단에사용": False,
        }
    )


class AccountReadGate:
    """One in-flight account read, then a short saved copy. The age is not hidden."""

    def __init__(self, ttl_seconds: float) -> None:
        self.ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()
        self._stored_at: float | None = None
        self._stored: AccountView | None = None

    def _fresh(self, now: float) -> bool:
        if self._stored is None or self._stored_at is None or self.ttl_seconds <= 0:
            return False
        return now - self._stored_at < self.ttl_seconds

    async def get(self, loader: Callable[[], Awaitable[AccountView]]) -> AccountView:
        now = time.monotonic()
        if self._fresh(now) and self._stored is not None and self._stored_at is not None:
            return _decorate(
                self._stored,
                age=_age_seconds(now, self._stored_at),
                cache_hit=True,
                shared=False,
            )
        shared = self._lock.locked()
        async with self._lock:
            now = time.monotonic()
            if self._fresh(now) and self._stored is not None and self._stored_at is not None:
                return _decorate(
                    self._stored,
                    age=_age_seconds(now, self._stored_at),
                    cache_hit=True,
                    shared=shared,
                )
            view = await loader()
            self._stored = view
            self._stored_at = time.monotonic()
            return _decorate(view, age=0, cache_hit=False, shared=shared)


async def account_view(request: Request) -> AccountView:
    gate = getattr(request.app.state, "account_read_gate", None)
    if not isinstance(gate, AccountReadGate):
        gate = AccountReadGate(cache_ttl_seconds())
        request.app.state.account_read_gate = gate

    async def load() -> AccountView:
        client = _client(request)
        problem = None if client is not None else _problem(request)
        view = await build_account_view(client, problem, live_trading=live_trading_enabled())
        return publish(view)

    return await gate.get(load)


def refuse_mutation(_request: Request) -> JSONResponse:
    """Refuse orders and leverage changes.

    live_trading_enabled defaults to false. This read-only process is not an
    order sender even when the flag is true. Future orders belong to one
    NautilusTrader execution path, which is not started here.
    """
    enabled = live_trading_enabled()
    if enabled:
        reason = "read_only_client"
        message = "실거래가 켜져 있어도 이 화면은 읽기만 합니다. 주문을 보내지 않았습니다."
    else:
        reason = "live_trading_disabled"
        message = "실거래 꺼짐. 주문을 보내지 않았습니다."
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
    application.state.account_read_gate = AccountReadGate(cache_ttl_seconds())
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
