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
from multi_exchange_trader.recommendations.practice import OPEN_AND_CLOSE, PracticeCloseFailed
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
    def __init__(self, *, fail_close: bool = False) -> None:
        self.calls = 0
        self.fail_close = fail_close

    async def round_trip(self) -> str:
        self.calls += 1
        if self.fail_close:
            raise PracticeCloseFailed(
                "연습 주문을 넣었지만 바로 닫지 못했습니다. 다시 넣지 않았습니다. 실계좌 주문은 없습니다."
            )
        return OPEN_AND_CLOSE


@pytest.mark.asyncio
async def test_bybit_approval_places_one_practice_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session(tmp_path, monkeypatch) as (client, _script, app, _clock):
        practice = RecordingPractice()
        app.state.bybit_practice = practice
        card = await preview(client)
        approved = await client.post(f"/api/v1/recommendations/{card['번호']}/approve", json={})
        body = approved.json()
        assert approved.status_code == 200, approved.text
        assert body["실계좌주문"] is False
        assert "실제 체결과 포지션 종료는 확인하지 않았습니다" in body["메시지"]
        assert "실계좌 주문은 없습니다" in body["메시지"]
        assert practice.calls == 1
        repeat = await client.post(f"/api/v1/recommendations/{card['번호']}/approve", json={})
        assert repeat.status_code == 200
        assert practice.calls == 1


@pytest.mark.asyncio
async def test_practice_close_failure_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session(tmp_path, monkeypatch) as (client, _script, app, _clock):
        practice = RecordingPractice(fail_close=True)
        app.state.bybit_practice = practice
        card = await preview(client)
        failed = await client.post(f"/api/v1/recommendations/{card['번호']}/approve", json={})
        body = failed.json()
        assert failed.status_code == 409
        assert "바로 닫지 못했습니다" in body["메시지"]
        assert "다시 넣지 않았습니다" in body["메시지"]
        assert body["가상반영"] is False
        assert body["카드"]["승인가능"] is False
        assert app.state.paper_store.scenarios() == []
        again = await client.post(f"/api/v1/recommendations/{card['번호']}/approve", json={})
        assert again.status_code == 409
        assert practice.calls == 1


async def test_list_detail_are_rule_sourced_and_expire_without_new_market_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session(tmp_path, monkeypatch) as (client, script, app, clock):
        card = await preview(client)
        calls = len(script.calls)
        listing = await client.get("/api/v1/recommendations")
        assert listing.status_code == 200
        view = listing.json()["recommendations"][0]
        assert view["id"] == card["번호"]
        assert view["source"] == "RULE"
        assert view["reviewState"] == "pending"
        detail = await client.get(f"/api/v1/recommendations/{card['번호']}")
        assert detail.json()["recommendation"]["id"] == card["번호"]
        clock.at += timedelta(minutes=16)
        expired = await client.get(f"/api/v1/recommendations/{card['번호']}")
        assert expired.json()["recommendation"]["reviewState"] == "expired"
        assert expired.json()["recommendation"]["approvable"] is False
        assert len(script.calls) == calls
        assert app.state.paper_store.scenarios() == []
        assert (await client.get("/api/v1/recommendations/not-a-card")).status_code == 404


async def test_demo_timeout_has_durable_uncertain_claim_and_no_retry_after_expiry_restart(tmp_path, monkeypatch):
    from multi_exchange_trader.recommendations.book import RecommendationBook

    class TimeoutPractice:
        calls = 0

        async def round_trip(self):
            self.calls += 1
            persisted = app.state.recommendation_book.get(card["번호"])
            assert persisted["payload"]["demo_attempt_started"] is True
            raise httpx.ReadTimeout("private transport detail")

    async with session(tmp_path, monkeypatch) as (client, _script, app, clock):
        practice = TimeoutPractice()
        app.state.bybit_practice = practice
        card = await preview(client)
        failed = await client.post(f"/api/v1/recommendations/{card['번호']}/approve", json={})
        assert failed.status_code == 409
        assert "확인할 수 없습니다" in failed.json()["메시지"]
        assert "private transport" not in failed.text
        assert failed.json()["카드"]["승인가능"] is False
        app.state.recommendation_book = RecommendationBook(tmp_path / "reco.sqlite3")
        clock.at += timedelta(minutes=16)
        again = await client.post(f"/api/v1/recommendations/{card['번호']}/approve", json={})
        assert again.status_code == 409
        assert "확인할 수 없습니다" in again.json()["메시지"]
        rejected = await client.post(f"/api/v1/recommendations/{card['번호']}/reject", json={})
        assert rejected.status_code == 409
        detail = (await client.get(f"/api/v1/recommendations/{card['번호']}")).json()["recommendation"]
        assert detail["approval_state"] == "DEMO_UNCERTAIN"
        diagnostics = (await client.get("/api/v1/diagnostics")).json()
        assert diagnostics["components"]["bybit_demo"]["uncertain_count"] == 1
        assert practice.calls == 1
        assert app.state.paper_store.scenarios() == []


async def test_practice_claim_is_atomic_across_independent_book_connections(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from multi_exchange_trader.recommendations.book import RecommendationBook

    async with session(tmp_path, monkeypatch) as (client, _script, app, _clock):
        card = await preview(client)
        path = tmp_path / "reco.sqlite3"
        books = [RecommendationBook(path) for _ in range(4)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda book: book.claim_practice(card["번호"])[0], books))
        assert sum(results) == 1
        assert app.state.recommendation_book.get(card["번호"])["payload"]["demo_attempt_started"] is True


async def test_short_rule_approval_metadata_is_explicit_fixed_demo_buy_connectivity_test(tmp_path, monkeypatch):
    async with session(tmp_path, monkeypatch, FALLING) as (client, _script, app, _clock):
        app.state.bybit_practice = RecordingPractice()
        card = await preview(client)
        detail = (await client.get(f"/api/v1/recommendations/{card['번호']}")).json()["recommendation"]
        assert detail["source"] == "RULE"
        assert detail["side"] == "short"
        demo = next(effect for effect in detail["approval_effects"] if effect["execution"] == "DEMO")
        assert demo["side"] == "BUY_THEN_REDUCE_ONLY_SELL"
        assert demo["follows_recommendation_side"] is False
        assert demo["verified_closed"] is False
        connections = (await client.get("/api/v1/connections/status")).json()
        assert connections["bybit_demo"]["status"] == "REGISTERED_UNVERIFIED"
        assert connections["bybit_demo"]["execution"] == "DEMO"
        capabilities = (await client.get("/api/v1/capabilities")).json()
        assert capabilities["demo_order_enabled"] is True
        assert capabilities["live_order_enabled"] is False


async def test_demo_acknowledgement_receipt_is_preserved_without_another_attempt(tmp_path, monkeypatch):
    from multi_exchange_trader.recommendations.service import RecommendationService

    async with session(tmp_path, monkeypatch) as (client, _script, app, clock):
        card = await preview(client)
        record = app.state.recommendation_book.remember_flag(card["번호"], "demo_round_trip")
        practice = RecordingPractice()
        service = RecommendationService(app.state.recommendation_book, app.state.paper_store,
            app.state.market_data_service, app.state.recommendation_lock, clock=clock, practice=practice)
        assert await service._practice_note(card["번호"], record["payload"]) == OPEN_AND_CLOSE
        assert practice.calls == 0
        assert service.view(record)["approval_state"] == "DEMO_SUBMISSION_ACKNOWLEDGED"
        rejected = await client.post(f"/api/v1/recommendations/{card['번호']}/reject", json={})
        assert rejected.status_code == 200
        assert "연습 계좌 요청 기록은 취소되지 않습니다" in rejected.json()["메시지"]


async def test_cancelled_demo_attempt_is_durable_and_never_repeated(tmp_path, monkeypatch):
    import asyncio

    from multi_exchange_trader.recommendations.book import RecommendationBook
    from multi_exchange_trader.recommendations.practice import PracticeUncertain
    from multi_exchange_trader.recommendations.service import RecommendationService

    class CancelledPractice:
        calls = 0

        async def round_trip(self):
            self.calls += 1
            raise asyncio.CancelledError

    async with session(tmp_path, monkeypatch) as (client, _script, app, clock):
        card = await preview(client)
        practice = CancelledPractice()
        book = app.state.recommendation_book
        service = RecommendationService(book, app.state.paper_store, app.state.market_data_service,
            app.state.recommendation_lock, clock=clock, practice=practice)
        with pytest.raises(asyncio.CancelledError):
            await service._practice_note(card["번호"], book.get(card["번호"])["payload"])
        reopened = RecommendationBook(tmp_path / "reco.sqlite3")
        service.book = reopened
        with pytest.raises(PracticeUncertain):
            await service._practice_note(card["번호"], reopened.get(card["번호"])["payload"])
        assert practice.calls == 1
        assert reopened.practice_summary()["uncertain_count"] == 1


async def test_registered_demo_does_not_offer_unsupported_eth_spot_approval(tmp_path, monkeypatch):
    async with session(tmp_path, monkeypatch) as (client, _script, app, _clock):
        practice = RecordingPractice()
        app.state.bybit_practice = practice
        response = await client.post("/api/v1/recommendations/preview", json={
            "거래소": "바이비트", "종목": "ETH / USDT", "상품": "현물"})
        card = response.json()["카드"]
        assert card["승인가능"] is False
        assert (await client.post(f"/api/v1/recommendations/{card['번호']}/approve", json={})).status_code == 409
        assert practice.calls == 0


async def test_uncertain_demo_cannot_be_retried_through_a_different_card(tmp_path, monkeypatch):
    from multi_exchange_trader.recommendations.book import RecommendationBook

    async with session(tmp_path, monkeypatch) as (client, script, app, _clock):
        app.state.bybit_practice = RecordingPractice()
        first = await preview(client)
        app.state.recommendation_book.claim_practice(first["번호"])
        app.state.recommendation_book.remember_flag(first["번호"], "demo_uncertain")
        script.rows = FALLING
        second = await preview(client)
        assert second["번호"] != first["번호"]
        assert second["승인가능"] is False
        app.state.recommendation_book = RecommendationBook(tmp_path / "reco.sqlite3")
        assert app.state.recommendation_book.claim_practice(second["번호"])[0] is False
        blocked = await client.post(f"/api/v1/recommendations/{second['번호']}/approve", json={})
        assert blocked.status_code == 409
        assert app.state.bybit_practice.calls == 0
        assert app.state.recommendation_book.practice_summary()["uncertain_count"] == 1
        capability = (await client.get("/api/v1/capabilities")).json()
        assert capability["demo_order_enabled"] is False
        assert capability["recommendations"]["demo_connectivity_test"]["blocked_by_unresolved_attempt"] is True


async def test_different_demo_cards_cannot_claim_concurrently_in_two_books(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from multi_exchange_trader.recommendations.book import RecommendationBook

    async with session(tmp_path, monkeypatch) as (client, script, _app, _clock):
        first = await preview(client)
        script.rows = FALLING
        second = await preview(client)
        inputs = [(RecommendationBook(tmp_path / "reco.sqlite3"), card["번호"]) for card in (first, second)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pair: pair[0].claim_practice(pair[1])[0], inputs))
        assert sum(results) == 1
