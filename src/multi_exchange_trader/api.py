"""Typed public market API; no account, credential, or execution routes."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal, cast

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from .market_data.adapters.base import MarketDataUnavailable
from .market_data.adapters.binance import BinanceAdapter
from .market_data.adapters.bybit import BybitAdapter
from .market_data.adapters.okx import OKXAdapter
from .market_data.models import Candle, MarketType, Ticker
from .market_data.public_client import PublicMarketClient, parse_blocked_hosts
from .market_data.service import MarketDataService
from .paper.api import install as install_paper
from .paper.store import PaperStore

Exchange = Literal["binance", "bybit", "okx"]
Symbol = Literal["BTCUSDT", "ETHUSDT"]
Interval = Literal["1m", "5m", "15m", "1h", "4h", "1d"]


def get_market_data_service(request: Request) -> MarketDataService:
    return cast(MarketDataService, request.app.state.market_data_service)


Service = Annotated[MarketDataService, Depends(get_market_data_service)]


def create_app(service: MarketDataService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.paper_store = PaperStore(
            os.environ.get("PAPER_DB_PATH", "work/phase3-data/paper.sqlite3")
        )
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
        return {"status": "ok"}

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
    return application


app = create_app()
