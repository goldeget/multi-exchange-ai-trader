"""Public market API, a read-only live Bybit view, and one practice-account order path."""

import asyncio
import os
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal, cast

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from .account.api import install as install_account
from .account.bybit_demo_order import BybitDemoOrderClient
from .account.bybit_readonly import BybitReadOnlyClient
from .account.config import MissingBybitCredentials
from .ai.budget import BudgetBook
from .controls import install as install_controls
from .market_data.adapters.base import MarketDataUnavailable
from .market_data.adapters.binance import BinanceAdapter
from .market_data.adapters.bybit import BybitAdapter
from .market_data.adapters.okx import OKXAdapter
from .market_data.models import Candle, MarketType, Ticker
from .market_data.public_client import PublicMarketClient, parse_blocked_hosts
from .market_data.service import MarketDataService
from .paper.api import install as install_paper
from .paper.order_preview import OrderReviews
from .paper.spot_api import install as install_spot
from .paper.spot_contract import QUALIFIED as SPOT_QUALIFIED
from .paper.spot_store import SpotPaperStore
from .paper.store import PaperStore
from .recommendations.api import install as install_recommendations
from .recommendations.book import RecommendationBook
from .recommendations.practice import BybitPracticeGate, PracticeUnavailable
from .research.api import install as install_research

Exchange = Literal["binance", "bybit", "okx"]
Symbol = Literal["BTCUSDT", "ETHUSDT"]
Interval = Literal["1m", "5m", "15m", "1h", "4h", "1d"]


def get_market_data_service(request: Request) -> MarketDataService:
    return cast(MarketDataService, request.app.state.market_data_service)


Service = Annotated[MarketDataService, Depends(get_market_data_service)]


def create_app(service: MarketDataService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        # V2 native leverage/pause/replay and evaluation capital/splits passed CI49.
        # This declares the contract; capabilities separately reports native availability.
        application.state.research_v2_supported = True
        application.state.paper_store = PaperStore(
            os.environ.get("PAPER_DB_PATH", "work/phase3-data/paper.sqlite3")
        )
        sidecar_root = application.state.paper_store.path.parent
        application.state.order_reviews = None
        application.state.ai_budget = None
        application.state.spot_paper_store = None
        application.state.spot_paper_qualified = SPOT_QUALIFIED
        try:
            application.state.spot_paper_store = SpotPaperStore(
                Path(os.environ.get("PAPER_SPOT_DB_PATH", str(sidecar_root / "spot-paper.sqlite3")))
            )
        except (ValueError, sqlite3.Error, OSError):
            pass  # A separate product never replaces or funds the original PAPER ledger.
        try:
            application.state.order_reviews = OrderReviews(
                Path(os.environ.get("PAPER_REVIEW_DB_PATH", str(sidecar_root / "order-reviews.sqlite3"))),
                application.state.paper_store,
                spot=application.state.spot_paper_store, spot_qualified=SPOT_QUALIFIED,
            )
        except (ValueError, sqlite3.Error, OSError):
            pass  # Isolate review sidecar failure from existing ledger reads and risk reduction.
        try:
            application.state.ai_budget = BudgetBook(
                Path(os.environ.get("AI_DB_PATH", str(sidecar_root / "ai.sqlite3")))
            )
        except (ValueError, sqlite3.Error, OSError):
            pass  # Optional AI state must not stop the PAPER scheduler or other reads.
        application.state.recommendation_book = RecommendationBook(
            os.environ.get("RECOMMENDATION_DB_PATH", "work/recommendations.sqlite3")
        )
        application.state.recommendation_lock = asyncio.Lock()
        account: BybitReadOnlyClient | None = None
        practice: BybitReadOnlyClient | None = None
        practice_orders: BybitDemoOrderClient | None = None
        application.state.bybit_account = None
        application.state.bybit_practice = None
        application.state.bybit_account_problem = "missing_credentials"
        try:
            account = BybitReadOnlyClient.from_environment()
        except MissingBybitCredentials:
            account = None
        except ValueError:
            application.state.bybit_account_problem = "config"
        else:
            application.state.bybit_account = account
            application.state.bybit_account_problem = None
        try:
            practice = BybitReadOnlyClient.for_demo()
            practice_orders = BybitDemoOrderClient.from_environment()
            application.state.bybit_practice = BybitPracticeGate(practice, practice_orders)
        except (MissingBybitCredentials, ValueError, PracticeUnavailable):
            if practice is not None:
                await practice.aclose()
            if practice_orders is not None:
                await practice_orders.aclose()
            practice = None
            practice_orders = None
            application.state.bybit_practice = None
        try:
            if service is not None:
                yield
                return
            blocked = parse_blocked_hosts(os.environ.get("MARKET_DATA_BLOCKED_HOSTS", ""))
            async with PublicMarketClient(blocked_hosts=blocked) as client:
                application.state.public_market_client = client
                application.state.market_data_service = MarketDataService(
                    {
                        "binance": BinanceAdapter(client),
                        "bybit": BybitAdapter(client),
                        "okx": OKXAdapter(client),
                    }
                )
                yield
        finally:
            if account is not None:
                await account.aclose()
            if practice is not None:
                await practice.aclose()
            if practice_orders is not None:
                await practice_orders.aclose()

    application = FastAPI(
        title="Public market data",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    if service is not None:
        application.state.market_data_service = service

    @application.exception_handler(MarketDataUnavailable)
    async def unavailable(request: Request, error: MarketDataUnavailable) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"reason": error.reason, "message": "Market data is unavailable."},
        )

    @application.get("/api/v1/health")
    async def health() -> dict[str, str]:
        # Liveness only. A 200 here is not permission to trade.
        return {"status": "ok"}

    @application.get("/api/v1/ready")
    async def ready(request: Request) -> dict[str, object]:
        research_state = "확인 안 됨"
        differs = False
        block = True
        alive = False
        health = getattr(request.app.state, "research_health", None)
        task = getattr(request.app.state, "research_task", None)
        if isinstance(health, dict):
            research_state = str(health.get("state", "확인 안 됨"))
            differs = bool(health.get("work_differs_from_store"))
            block = bool(health.get("block_new_auto"))
        alive = isinstance(task, asyncio.Task) and not task.done()
        return {
            "status": "ok",
            "의미": "서버가 응답합니다. 실거래 준비가 아닙니다.",
            "실거래준비": False,
            "기능": {
                "시세": {
                    "상태": "이 응답만으로 거래소 연결을 보장하지 않습니다",
                    "읽기화면차단": False,
                },
                "계좌조회": {"상태": "선택 기능", "읽기화면차단": False},
                "가상자동실행": {
                    "상태": research_state,
                    "동작중": alive,
                    "신규자동진행": alive and not block and research_state == "READY",
                    "저장과다름": differs,
                    "읽기화면차단": False,
                },
            },
        }

    @application.get("/api/v1/market/ticker", response_model=Ticker)
    async def ticker(
        service: Service, exchange: Exchange, symbol: Symbol, market_type: MarketType
    ) -> Ticker:
        return await service.get_ticker(exchange, symbol, market_type)

    @application.get("/api/v1/market/candles", response_model=list[Candle])
    async def candles(
        service: Service,
        exchange: Exchange,
        symbol: Symbol,
        market_type: MarketType,
        interval: Interval,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> list[Candle]:
        return await service.get_candles(exchange, symbol, market_type, interval, limit)

    install_paper(application)
    install_research(application)
    install_recommendations(application)
    install_account(application)
    install_controls(application)
    install_spot(application)
    return application


app = create_app()
