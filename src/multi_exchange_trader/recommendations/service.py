"""Turn a stored rule card into a virtual order, or refuse. No exchange order."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from multi_exchange_trader.market_data.adapters.base import MarketDataUnavailable
from multi_exchange_trader.market_data.models import MarketType
from multi_exchange_trader.market_data.service import MarketDataService
from multi_exchange_trader.paper.contract import FEE, SPECS, reserve
from multi_exchange_trader.paper.store import PaperStore

from .book import RecommendationBook
from .practice import PracticeCheck, PracticeUnavailable
from .rules import (
    CANDLE_LIMIT,
    EXCHANGE_KO,
    INSTRUMENT,
    INTERVAL,
    LEVERAGE,
    MARKET_KO,
    RULE_LABEL,
    SELECT_EXCHANGE,
    SELECT_MARKET,
    SELECT_SYMBOL,
    SIDE_KO,
    SYMBOL_KO,
    Plan,
    build_plan,
    plan_id,
)

SCENARIO = "reco-paper"
CARD_ID = re.compile(r"^[a-f0-9]{32}$")
NOTICE = (
    "수량과 레버리지는 규칙이 정합니다. 승인은 바이비트 연습 계좌로만 갑니다. "
    "실계좌로 주문을 보내지 않습니다. 실거래는 꺼져 있습니다."
)


class Refusal(Exception):
    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def envelope(card: dict[str, object] | None, message: str, virtual: bool) -> dict[str, object]:
    return {"카드": card, "메시지": message, "실계좌주문": False, "가상반영": virtual}


def parse_selection(body: object) -> tuple[str, str, str]:
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise Refusal("추천 요청을 확인하세요.", 400)
    if set(body) - {"거래소", "종목", "상품"}:
        raise Refusal("레버리지, 수량, 가격은 여기서 정하지 않습니다.", 400)
    if not body:
        return "bybit", "BTCUSDT", "perpetual"
    try:
        exchange = SELECT_EXCHANGE[str(body["거래소"])]
        symbol = SELECT_SYMBOL[str(body["종목"])]
        market_type = SELECT_MARKET[str(body["상품"])]
    except KeyError as exc:
        raise Refusal("거래소, 종목, 현물·선물을 확인하세요.", 400) from exc
    return exchange, symbol, market_type


def _paper_message(error: Exception) -> str:
    text = str(error)
    if "stale" in text or "tick time" in text:
        return "가상 가격 시각이 맞지 않습니다. 주문은 넣지 않았습니다."
    if "margin" in text or "notional" in text:
        return "가상 증거금이 부족하거나 주문 금액이 한도를 넘습니다. 주문은 넣지 않았습니다."
    if "position" in text or "exists" in text:
        return "가상 포지션이 이미 있습니다. 새 주문은 넣지 않았습니다."
    if "scenario limit" in text:
        return "가상 장부가 가득 찼습니다. 새 주문은 넣지 않았습니다."
    return "가상 주문을 넣지 못했습니다. 실계좌에는 보내지 않았습니다."


class RecommendationService:
    def __init__(
        self,
        book: RecommendationBook,
        paper: PaperStore,
        market: MarketDataService,
        lock: asyncio.Lock,
        clock: Any = None,
        practice: PracticeCheck | None = None,
    ) -> None:
        self.book = book
        self.paper = paper
        self.market = market
        self.lock = lock
        self.clock = clock
        self.practice = practice

    def now(self) -> datetime:
        if callable(self.clock):
            current = self.clock()
            if isinstance(current, datetime):
                return current if current.tzinfo else current.replace(tzinfo=UTC)
        return datetime.now(UTC)

    def present(self, record: dict[str, Any]) -> dict[str, object]:
        payload = record["payload"]
        status = str(record["status"])
        side = str(payload.get("side") or "flat")
        if status == "pending" and side == "flat":
            shown = "관망"
        else:
            shown = {
                "pending": "대기",
                "rejected": "거절",
                "expired": "만료",
                "approving": "처리 중",
                "approved": "반영",
            }.get(status, "확인 중")
        leverage = payload.get("leverage")
        expires = datetime.fromisoformat(str(payload["expires_at"]))
        approvable = status == "approving" or (
            status == "pending"
            and side in ("long", "short")
            and bool(payload.get("actionable"))
            and bool(payload.get("fresh"))
            and self.now() < expires
        )
        return {
            "번호": record["id"],
            "상태": shown,
            "거래소": EXCHANGE_KO[str(payload["exchange"])],
            "종목": SYMBOL_KO[str(payload["symbol"])],
            "상품": MARKET_KO[str(payload["market_type"])],
            "방향": SIDE_KO.get(side, "방향 확인"),
            "시세": str(payload.get("freshness") or "확인 중"),
            "진입": payload.get("entry") or "없음",
            "손절": payload.get("stop") or "없음",
            "익절": payload.get("take_profit") or "없음",
            "수량": payload.get("quantity") or "없음",
            "레버리지": f"{leverage}배" if isinstance(leverage, int) else "없음",
            "이유": str(payload.get("why") or ""),
            "반대이유": str(payload.get("why_not") or ""),
            "설명": RULE_LABEL,
            "만료": expires.isoformat(),
            "승인가능": approvable,
            "안내": NOTICE,
        }

    async def preview(self, body: object) -> tuple[dict[str, object], int]:
        exchange, symbol, market_type = parse_selection(body)
        plan = await self._plan(exchange, symbol, market_type)
        async with self.lock:
            record = self.book.upsert(plan)
            record = self.book.expire_if_needed(str(record["id"]), self.now())
        return envelope(self.present(record), "", False), 200

    async def reject(self, card_id: str) -> tuple[dict[str, object], int]:
        self._check_id(card_id)
        async with self.lock:
            record = self.book.get(card_id)
            if record is None:
                raise Refusal("추천을 찾지 못했습니다. 주문은 없습니다.", 404)
            record = self.book.expire_if_needed(card_id, self.now())
            status = str(record["status"])
            if status == "approved":
                return (
                    envelope(
                        self.present(record),
                        "이미 가상 장부에 반영했습니다. 거절로 지우지 않습니다. 실계좌 주문은 없습니다.",
                        True,
                    ),
                    409,
                )
            if status == "approving":
                return (
                    envelope(
                        self.present(record),
                        "가상 반영을 확인하는 중입니다. 거절로 지우지 않습니다. 실계좌 주문은 없습니다.",
                        False,
                    ),
                    409,
                )
            if status == "pending":
                record = self.book.mark(card_id, "rejected")
            message = {
                "rejected": "거절했습니다. 주문은 없습니다.",
                "expired": "만료된 추천입니다. 주문은 없습니다.",
            }.get(str(record["status"]), "주문은 없습니다.")
            return envelope(self.present(record), message, False), 200

    async def approve(self, card_id: str) -> tuple[dict[str, object], int]:
        self._check_id(card_id)
        async with self.lock:
            record = self.book.get(card_id)
            if record is None:
                raise Refusal("추천을 찾지 못했습니다. 주문은 없습니다.", 404)
            record = self.book.expire_if_needed(card_id, self.now())
            status = str(record["status"])
            if status == "rejected":
                return envelope(self.present(record), "거절한 추천입니다. 주문은 없습니다.", False), 409
            if status == "expired":
                return envelope(self.present(record), "만료된 추천입니다. 주문은 없습니다.", False), 409
            if status == "approved":
                return (
                    envelope(
                        self.present(record),
                        "이미 가상 장부에 반영되어 있습니다. 다시 넣지 않았습니다.",
                        True,
                    ),
                    200,
                )
            note = ""
            if status == "pending":
                payload = record["payload"]
                plan = await self._plan(
                    str(payload["exchange"]),
                    str(payload["symbol"]),
                    str(payload["market_type"]),
                )
                if not self._same(payload, plan) or not plan.actionable:
                    message = (
                        "시세가 바뀌었거나 오래되었습니다. 주문은 넣지 않았습니다. 추천을 다시 보세요."
                    )
                    return envelope(self.present(record), message, False), 409
                note = await self._practice_note(str(payload["exchange"]))
                await asyncio.to_thread(self._start_and_run, card_id, plan)
            elif status == "approving":
                note = await self._practice_note(str(record["payload"]["exchange"]))
                await asyncio.to_thread(self._replay, card_id)
            else:
                raise Refusal("추천 상태를 확인하지 못했습니다. 주문은 없습니다.")
            stored = self.book.get(card_id)
        if stored is None or stored["status"] != "approved":
            raise Refusal("가상 주문을 확인하지 못했습니다. 실계좌 주문은 없습니다.")
        message = "가상 장부에 반영했습니다. 실계좌 주문은 없습니다."
        if note:
            message = f"{message} {note}"
        return envelope(self.present(stored), message, True), 200

    async def _plan(self, exchange: str, symbol: str, market_type: str) -> Plan:
        try:
            candles = await self.market.get_candles(
                exchange, symbol, cast_market(market_type), INTERVAL, CANDLE_LIMIT
            )
        except MarketDataUnavailable as exc:
            if exc.reason == "geographic_restriction":
                raise Refusal(
                    "이 지역에서 시세 연결이 막혔습니다. 우회하지 않았습니다. 주문은 없습니다.",
                    503,
                ) from None
            raise Refusal("시세를 불러오지 못했습니다. 주문은 없습니다.", 503) from None
        except (KeyError, TypeError, ValueError):
            raise Refusal("시세를 불러오지 못했습니다. 주문은 없습니다.", 503) from None
        try:
            return build_plan(exchange, symbol, market_type, list(candles), self.now())
        except ValueError:
            raise Refusal("추천을 만들지 못했습니다. 주문은 없습니다.", 400) from None

    async def _practice_note(self, exchange: str) -> str:
        if exchange != "bybit" or self.practice is None:
            return ""
        try:
            note = await self.practice.confirm()
        except PracticeUnavailable as exc:
            raise Refusal(exc.message) from None
        if not note:
            raise Refusal("연습 계좌를 확인하지 못했습니다. 실계좌 주문은 없습니다.")
        return note

    def _same(self, payload: dict[str, Any], plan: Plan) -> bool:
        current = plan.identity()
        return all(payload.get(key) == value for key, value in current.items())

    def _start_and_run(self, card_id: str, plan: Plan) -> None:
        commands = self._commands(plan)
        started = self.book.begin(card_id, commands)
        self._execute(started["commands"], started["payload"])
        self.book.mark(card_id, "approved")

    def _replay(self, card_id: str) -> None:
        record = self.book.get(card_id)
        if record is None or record["status"] != "approving" or not record["commands"]:
            raise Refusal("가상 반영을 확인하지 못했습니다. 실계좌 주문은 없습니다.")
        self._execute(record["commands"], record["payload"])
        self.book.mark(card_id, "approved")

    def _commands(self, plan: Plan) -> list[dict[str, Any]]:
        if (
            plan.side not in ("long", "short")
            or plan.instrument is None
            or plan.entry is None
            or plan.stop is None
            or plan.take_profit is None
            or plan.quantity is None
            or plan.leverage != LEVERAGE
        ):
            raise Refusal("승인할 추천이 아닙니다. 주문은 넣지 않았습니다.")
        card_key = plan_id(plan)
        try:
            state = self.paper.state(SCENARIO)
        except ValueError as exc:
            if "not found" not in str(exc):
                raise Refusal(_paper_message(exc)) from None
            state = None
        commands: list[dict[str, Any]] = []
        if state is None:
            commands.append({"key": f"{card_key}-create", "command": {"kind": "create"}})
            sequence = 1
            available = Decimal(10000)
            positions: list[dict[str, Any]] = []
            last_tick: datetime | None = None
        else:
            sequence = int(state["latest_sequence"]) + 1
            available = Decimal(str(state["available_balance"]))
            positions = list(state["positions"])
            last_tick = datetime.fromisoformat(str(state["last_tick_at"]))
        if any(item.get("instrument") == plan.instrument for item in positions):
            raise Refusal(
                "이 가상 종목에 이미 포지션이 있습니다. 새 주문은 넣지 않았습니다. "
                "실계좌는 건드리지 않습니다."
            )
        self._margin(plan, available)
        tick_at = datetime.now(UTC) - timedelta(seconds=1)
        if last_tick is not None and tick_at <= last_tick:
            tick_at = last_tick + timedelta(seconds=1)
        if tick_at > datetime.now(UTC):
            raise Refusal(
                "가상 가격 시각을 맞추지 못했습니다. 잠시 뒤 다시 승인하세요. 주문은 넣지 않았습니다."
            )
        commands.extend(
            [
                {
                    "key": f"{card_key}-tick",
                    "command": {
                        "kind": "tick",
                        "instrument": plan.instrument,
                        "price": plan.entry,
                        "sequence": sequence,
                        "timestamp": tick_at.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                        "source": "LOCAL_SCENARIO_V1",
                    },
                },
                {
                    "key": f"{card_key}-enter",
                    "command": {
                        "kind": "enter",
                        "instrument": plan.instrument,
                        "side": plan.side,
                        "quantity": plan.quantity,
                        "leverage": LEVERAGE,
                    },
                },
                {
                    "key": f"{card_key}-protect",
                    "command": {
                        "kind": "protect",
                        "instrument": plan.instrument,
                        "sl": plan.stop,
                        "tp": plan.take_profit,
                    },
                },
            ]
        )
        return commands

    def _margin(self, plan: Plan, available: Decimal) -> None:
        if plan.instrument is None or plan.entry is None or plan.quantity is None:
            raise Refusal("승인할 추천이 아닙니다. 주문은 넣지 않았습니다.")
        tick = Decimal(SPECS[plan.instrument]["tick"])
        entry = Decimal(plan.entry)
        fill = entry + tick if plan.side == "long" else entry - tick
        quantity = Decimal(plan.quantity)
        if (
            fill <= 0
            or quantity * fill > Decimal(100000)
            or reserve(quantity, fill, LEVERAGE) + quantity * fill * FEE > available
        ):
            raise Refusal("가상 증거금이 부족하거나 주문 금액이 한도를 넘습니다. 주문은 넣지 않았습니다.")
        stop = Decimal(str(plan.stop))
        take_profit = Decimal(str(plan.take_profit))
        long = plan.side == "long"
        if long and not (stop < entry < take_profit):
            raise Refusal("손절·익절 위치가 맞지 않습니다. 주문은 넣지 않았습니다.")
        if not long and not (take_profit < entry < stop):
            raise Refusal("손절·익절 위치가 맞지 않습니다. 주문은 넣지 않았습니다.")

    def _execute(self, commands: list[dict[str, Any]] | None, payload: dict[str, Any]) -> None:
        if not commands:
            raise Refusal("가상 주문 내용이 없습니다. 실계좌 주문은 없습니다.")
        result: dict[str, Any] | None = None
        for item in commands:
            key = item.get("key")
            command = item.get("command")
            if not isinstance(key, str) or not isinstance(command, dict):
                raise Refusal("가상 주문 내용을 확인하지 못했습니다. 실계좌 주문은 없습니다.")
            self._allow(command, payload)
            try:
                result = self.paper.command(SCENARIO, key, command)
            except (ValueError, TypeError) as exc:
                raise Refusal(_paper_message(exc)) from None
        if result is None or result.get("mode") != "PAPER" or result.get("synthetic") is not True:
            raise Refusal("가상 장부 응답이 아니어서 멈췄습니다. 실계좌 주문은 없습니다.")

    def _allow(self, command: dict[str, Any], payload: dict[str, Any]) -> None:
        kind = command.get("kind")
        instrument = payload.get("instrument")
        if kind == "create":
            if set(command) != {"kind"}:
                raise Refusal("가상 주문 내용이 규칙과 달라서 넣지 않았습니다.")
            return
        if command.get("instrument") != instrument or instrument not in INSTRUMENT.values():
            raise Refusal("가상 주문 종목이 규칙과 달라서 넣지 않았습니다.")
        if kind == "tick":
            allowed = {"kind", "instrument", "price", "sequence", "timestamp", "source"}
            if set(command) != allowed or command.get("price") != payload.get("entry"):
                raise Refusal("가상 가격이 규칙과 달라서 넣지 않았습니다.")
            if command.get("source") != "LOCAL_SCENARIO_V1" or type(command.get("sequence")) is not int:
                raise Refusal("가상 가격이 규칙과 달라서 넣지 않았습니다.")
            return
        if kind == "enter":
            allowed = {"kind", "instrument", "side", "quantity", "leverage"}
            if (
                set(command) != allowed
                or command.get("side") != payload.get("side")
                or command.get("quantity") != payload.get("quantity")
                or command.get("leverage") != LEVERAGE
            ):
                raise Refusal("수량이나 레버리지가 규칙과 달라서 넣지 않았습니다.")
            return
        if kind == "protect":
            allowed = {"kind", "instrument", "sl", "tp"}
            if (
                set(command) != allowed
                or command.get("sl") != payload.get("stop")
                or command.get("tp") != payload.get("take_profit")
            ):
                raise Refusal("손절이나 익절이 규칙과 달라서 넣지 않았습니다.")
            return
        raise Refusal("알 수 없는 가상 주문은 넣지 않았습니다.")

    def _check_id(self, card_id: str) -> None:
        if not isinstance(card_id, str) or CARD_ID.fullmatch(card_id) is None:
            raise Refusal("추천 번호를 확인하지 못했습니다. 주문은 없습니다.", 404)


def cast_market(value: str) -> MarketType:
    if value == "spot":
        return "spot"
    if value == "perpetual":
        return "perpetual"
    raise Refusal("거래소, 종목, 현물·선물을 확인하세요.", 400)
