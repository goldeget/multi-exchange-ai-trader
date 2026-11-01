"""Recommendation approval reaches the virtual ledger only."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from multi_exchange_trader.api import create_app
from multi_exchange_trader.market_data.models import Candle, MarketType
from multi_exchange_trader.market_data.service import MarketDataService
from multi_exchange_trader.recommendations.rules import INTERVAL_DELTA

NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)
PREVIEW = {"거래소": "바이비트", "종목": "BTC / USDT", "상품": "선물"}


def make_candles(closes: list[int], age: timedelta = timedelta(minutes=15)) -> list[Candle]:
    last_open = NOW - age - INTERVAL_DELTA
    first = last_open - INTERVAL_DELTA * (len(closes) - 1)
    rows: list[Candle] = []
    for index, close in enumerate(closes):
        price = Decimal(close)
        rows.append(
            Candle(
                timestamp=first + INTERVAL_DELTA * index,
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=Decimal(1),
                is_closed=True,
            )
        )
    return rows


RISING = make_candles([100 + index for index in range(30)])
FALLING = make_candles([200 - index for index in range(30)])


class Scripted:
    def __init__(self, rows: list[Candle]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, str, str]] = []

    async def get_ticker(self, symbol: str, market_type: MarketType) -> None:
        raise AssertionError(symbol or market_type)

    async def get_candles(
        self, symbol: str, market_type: MarketType, interval: str, limit: int
    ) -> list[Candle]:
        self.calls.append((symbol, market_type, interval))
        assert interval == "15m"
        return self.rows[-limit:]


class Clock:
    def __init__(self) -> None:
        self.at = NOW

    def __call__(self) -> datetime:
        return self.at


def _plain(text: str) -> None:
    assert "PAPER" not in text
    assert "leverage" not in text.lower()
    assert "mainnet" not in text.lower()


@asynccontextmanager
async def session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rows: list[Candle] | None = None
) -> AsyncIterator[tuple[httpx.AsyncClient, Scripted, object, Clock]]:
    monkeypatch.setenv("BYBIT_ENV_FILE", "off")
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv("RECOMMENDATION_DB_PATH", str(tmp_path / "reco.sqlite3"))
    script = Scripted(list(rows or RISING))
    clock = Clock()
    app = create_app(MarketDataService({"binance": script, "bybit": script, "okx": script}))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost:8000"
        ) as client,
    ):
        app.state.recommendation_clock = clock
        yield client, script, app, clock


async def preview(client: httpx.AsyncClient) -> dict[str, object]:
    response = await client.post("/api/v1/recommendations/preview", json=PREVIEW)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["실계좌주문"] is False
    assert body["가상반영"] is False
    _plain(response.text)
    card = body["카드"]
    assert isinstance(card, dict)
    return card


@pytest.mark.asyncio
async def test_reject_and_extra_numbers_do_not_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session(tmp_path, monkeypatch) as (client, _script, app, _clock):
        paper = app.state.paper_store
        card = await preview(client)
        assert card["설명"] == "규칙 설명"
        assert card["거래소"] == "바이비트"
        assert card["종목"] == "BTC / USDT"
        assert card["상품"] == "선물"
        assert card["방향"] == "오르면 이익"
        assert card["시세"] == "방금 마감된 봉"
        assert card["진입"] == "129"
        assert card["손절"] == "125"
        assert card["익절"] == "137"
        assert card["수량"] == "0.001"
        assert card["레버리지"] == "2배"
        assert card["승인가능"] is True
        assert "규칙으로 만든 설명" in str(card["반대이유"])
        assert paper.scenarios() == []
        blocked = await client.post(
            "/api/v1/recommendations/preview", json={**PREVIEW, "레버리지": 20}
        )
        assert blocked.status_code == 400
        refused = await client.post(
            f"/api/v1/recommendations/{card['번호']}/approve",
            json={"leverage": 50, "quantity": "10"},
        )
        assert refused.status_code == 400
        assert refused.json()["가상반영"] is False
        assert "레버리지" in refused.json()["메시지"]
        rejected = await client.post(f"/api/v1/recommendations/{card['번호']}/reject", json={})
        assert rejected.status_code == 200
        assert rejected.json()["카드"]["상태"] == "거절"
        assert rejected.json()["가상반영"] is False
        again = await client.post(f"/api/v1/recommendations/{card['번호']}/approve", json={})
        assert again.status_code == 409
        assert paper.scenarios() == []


@pytest.mark.asyncio
async def test_expired_card_does_not_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with session(tmp_path, monkeypatch) as (client, script, app, clock):
        card = await preview(client)
        before = len(script.calls)
        clock.at = NOW + timedelta(minutes=16)
        expired = await client.post(f"/api/v1/recommendations/{card['번호']}/approve", json={})
        assert expired.status_code == 409
        assert expired.json()["카드"]["상태"] == "만료"
        assert expired.json()["가상반영"] is False
        assert len(script.calls) == before
        assert app.state.paper_store.scenarios() == []


@pytest.mark.asyncio
async def test_changed_signal_does_not_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session(tmp_path, monkeypatch) as (client, script, app, _clock):
        card = await preview(client)
        before = len(script.calls)
        script.rows = FALLING
        changed = await client.post(f"/api/v1/recommendations/{card['번호']}/approve", json={})
        assert changed.status_code == 409
        assert changed.json()["가상반영"] is False
        assert "주문은 넣지 않았습니다" in changed.json()["메시지"]
        assert len(script.calls) == before + 1
        assert app.state.paper_store.scenarios() == []


@pytest.mark.asyncio
async def test_approve_creates_one_virtual_position(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session(tmp_path, monkeypatch) as (client, script, app, _clock):
        paper = app.state.paper_store
        card = await preview(client)
        approved = await client.post(f"/api/v1/recommendations/{card['번호']}/approve", json={})
        assert approved.status_code == 200, approved.text
        body = approved.json()
        assert body["실계좌주문"] is False
        assert body["가상반영"] is True
        assert body["카드"]["상태"] == "반영"
        assert "실계좌 주문은 없습니다" in body["메시지"]
        _plain(approved.text)
        state = paper.state("reco-paper")
        assert state["synthetic"] is True
        assert len(state["fills"]) == 1
        assert len(state["positions"]) == 1
        position = state["positions"][0]
        assert position["instrument"] == "BTC-PAPER"
        assert position["side"] == "long"
        assert position["leverage"] == 2
        assert Decimal(position["quantity"]) == Decimal("0.001")
        assert {"sl", "tp"} <= {order["role"] for order in state["orders"]}
        revision = state["revision"]
        repeat = await client.post(f"/api/v1/recommendations/{card['번호']}/approve", json={})
        assert repeat.status_code == 200
        assert "다시 넣지 않았습니다" in repeat.json()["메시지"]
        assert paper.state("reco-paper")["revision"] == revision
        assert script.calls


@pytest.mark.asyncio
async def test_remote_host_cannot_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with session(tmp_path, monkeypatch) as (client, script, app, _clock):
        response = await client.post(
            "/api/v1/recommendations/preview",
            json=PREVIEW,
            headers={"Host": "evil.test"},
        )
        assert response.status_code == 403
        assert app.state.paper_store.scenarios() == []
        assert script.calls == []


class RecordingPractice:
    def __init__(self) -> None:
        self.orders = 0

    async def confirm(self) -> str:
        return "연습 계좌 잔고를 확인했습니다. 연습 주문은 자동으로 넣지 않았습니다."


@pytest.mark.asyncio
async def test_bybit_approval_checks_practice_account_without_an_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session(tmp_path, monkeypatch) as (client, _script, app, _clock):
        app.state.bybit_practice = RecordingPractice()
        card = await preview(client)
        approved = await client.post(f"/api/v1/recommendations/{card['번호']}/approve", json={})
        body = approved.json()
        assert approved.status_code == 200
        assert body["실계좌주문"] is False
        assert "연습 계좌" in body["메시지"]
        assert "실계좌 주문은 없습니다" in body["메시지"]
        assert "자동으로 넣지 않았습니다" in body["메시지"]
        assert app.state.bybit_practice.orders == 0
