"""Turn a stored rule card into a virtual order. Bybit approval also uses the practice account."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from multi_exchange_trader.market_data.adapters.base import MarketDataUnavailable
from multi_exchange_trader.market_data.models import MarketType
from multi_exchange_trader.market_data.service import MarketDataService
from multi_exchange_trader.paper.contract import FEE, SPECS, reserve
from multi_exchange_trader.paper.store import PaperStore

from .book import RecommendationBook
from .execution import approval_metadata, demo_state
from .practice import (
    CLOSE_WARNING,
    OPEN_AND_CLOSE,
    UNCERTAIN_WARNING,
    PracticeCheck,
    PracticeCloseFailed,
    PracticeUnavailable,
    PracticeUncertain,
)
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
    "수량과 레버리지는 규칙이 정합니다. 독립 가상 장부에 반영합니다. 실계좌 주문은 없습니다."
)
DEMO_NOTICE = (
    "승인은 가상 장부 반영과 별도로 바이비트 연습 계좌의 비트코인 0.001 고정 매수·감축 연결 시험을 요청합니다. "
    "이 시험은 추천 방향을 따르지 않으며, 접수 응답만으로 체결·포지션 종료를 확인하지 않습니다. 실계좌 주문은 없습니다."
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

    def execution(self, payload: dict[str, Any]) -> dict[str, Any]:
        blocked = False
        if self.practice is not None and payload.get("exchange") == "bybit":
            summary = self.book.practice_summary()
            blocked = summary["uncertain_count"] > 0 or summary["close_failed_count"] > 0
        return approval_metadata(payload, self.practice is not None, globally_blocked=blocked)

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
        execution = self.execution(payload)
        approvable = payload.get("demo_close_failed") is not True and (
            status == "approving"
            or (
                status == "pending"
                and side in ("long", "short")
                and bool(payload.get("actionable"))
                and bool(payload.get("fresh"))
                and self.now() < expires
            )
        )
        approvable = approvable and not execution["approval_blocked"]
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
            "안내": DEMO_NOTICE if len(execution["approval_effects"]) > 1 else NOTICE,
            "승인대상": "가상 장부 + 연습 연결 시험" if len(execution["approval_effects"]) > 1 else "독립 가상 장부",
            "연습주문상태": {
                "PAPER_ONLY": "연습 주문 없음", "DEMO_REGISTERED_UNVERIFIED": "연습 요청 권한 확인 전",
                "DEMO_UNCERTAIN": "연습 결과 확인 불가", "DEMO_CLOSE_FAILED": "연습 감축 확인 필요",
                "DEMO_SUBMISSION_ACKNOWLEDGED": "연습 요청 접수 기록", "DEMO_TARGET_UNSUPPORTED": "연습 시험 대상 아님",
                "DEMO_BLOCKED_BY_UNRESOLVED_ATTEMPT": "다른 연습 요청 확인 필요",
            }[execution["approval_state"]],
        }

    def view(self, record: dict[str, Any]) -> dict[str, Any]:
        """Additive normalized read contract; existing Korean command replies stay intact."""
        payload = record["payload"]
        card = self.present(record)
        return {"id": record["id"], "source": "RULE", "snapshot": dict(payload),
                "side": payload.get("side", "flat"), "reason": payload.get("why", ""),
                "invalidation": payload.get("why_not", ""),
                "missingData": [] if payload.get("fresh") else [payload.get("freshness", "시세 확인 필요")],
                "expiresAt": payload["expires_at"], "reviewState": record["status"],
                "approvable": card["승인가능"], "card": card,
                "execution": "PAPER", "updatedAt": record["updated_at"],
                **self.execution(payload)}

    async def listing(self) -> dict[str, Any]:
        async with self.lock:
            records = self.book.recent()
            records = [self.book.expire_if_needed(record["id"], self.now()) for record in records]
            return {"recommendations": [self.view(record) for record in records],
                    "server_time": self.now().isoformat()}

    async def detail(self, card_id: str) -> dict[str, Any]:
        self._check_id(card_id)
        async with self.lock:
            if self.book.get(card_id) is None:
                raise Refusal("추천을 찾지 못했습니다. 주문은 없습니다.", 404)
            record = self.book.expire_if_needed(card_id, self.now())
            return {"recommendation": self.view(record), "server_time": self.now().isoformat()}

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
            external_state = demo_state(record["payload"])
            if external_state in {"DEMO_UNCERTAIN", "DEMO_CLOSE_FAILED"}:
                warning = CLOSE_WARNING if external_state == "DEMO_CLOSE_FAILED" else UNCERTAIN_WARNING
                return envelope(self.present(record), warning, False), 409
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
            if external_state == "DEMO_SUBMISSION_ACKNOWLEDGED":
                message = "가상 반영을 거절했습니다. 이미 접수된 연습 계좌 요청 기록은 취소되지 않습니다."
            return envelope(self.present(record), message, False), 200

    async def approve(self, card_id: str) -> tuple[dict[str, object], int]:
        self._check_id(card_id)
        async with self.lock:
            record = self.book.get(card_id)
            if record is None:
                raise Refusal("추천을 찾지 못했습니다. 주문은 없습니다.", 404)
            record = self.book.expire_if_needed(card_id, self.now())
            external_state = demo_state(record["payload"])
            if external_state in {"DEMO_UNCERTAIN", "DEMO_CLOSE_FAILED"}:
                warning = CLOSE_WARNING if external_state == "DEMO_CLOSE_FAILED" else UNCERTAIN_WARNING
                return envelope(self.present(record), warning, False), 409
            status = str(record["status"])
            if status == "rejected":
                if external_state == "DEMO_SUBMISSION_ACKNOWLEDGED":
                    return envelope(self.present(record), "가상 반영을 거절한 추천입니다. 연습 계좌 요청 접수 기록은 남아 있습니다.", False), 409
                return envelope(self.present(record), "거절한 추천입니다. 주문은 없습니다.", False), 409
            if status == "expired":
                if external_state == "DEMO_SUBMISSION_ACKNOWLEDGED":
                    return envelope(self.present(record), "추천은 만료되었습니다. 연습 계좌 요청 접수 기록은 남아 있습니다. 새 가상 주문은 없습니다.", False), 409
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
            if record["payload"].get("demo_close_failed") is True:
                return envelope(self.present(record), CLOSE_WARNING, False), 409
            note = ""
            try:
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
                    note = await self._practice_note(card_id, payload)
                    await asyncio.to_thread(self._start_and_run, card_id, plan)
                elif status == "approving":
                    note = await self._practice_note(card_id, record["payload"])
                    await asyncio.to_thread(self._replay, card_id)
                else:
                    raise Refusal("추천 상태를 확인하지 못했습니다. 주문은 없습니다.")
            except (PracticeCloseFailed, PracticeUncertain) as exc:
                stored_failure = self.book.get(card_id)
                shown = self.present(stored_failure) if stored_failure is not None else None
                return envelope(shown, exc.message, False), 409
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

    async def _practice_note(self, card_id: str, payload: dict[str, Any]) -> str:
        exchange = str(payload.get("exchange") or "")
        if exchange != "bybit" or self.practice is None:
            return ""
        symbol = str(payload.get("symbol") or "")
        market_type = str(payload.get("market_type") or "")
        if symbol != "BTCUSDT" or market_type != "perpetual":
            raise Refusal(
                "이 추천은 연습 계좌의 가장 작은 비트코인 선물 주문과 달라서 넣지 않았습니다. "
                "실계좌 주문은 없습니다."
            )
        if payload.get("demo_close_failed") is True:
            raise PracticeCloseFailed(CLOSE_WARNING)
        if payload.get("demo_round_trip") is True:
            return OPEN_AND_CLOSE
        claimed, current = self.book.claim_practice(card_id)
        if not claimed:
            state = demo_state(current["payload"])
            if state == "DEMO_SUBMISSION_ACKNOWLEDGED":
                return OPEN_AND_CLOSE
            if state == "DEMO_CLOSE_FAILED":
                raise PracticeCloseFailed(CLOSE_WARNING)
            raise PracticeUncertain(UNCERTAIN_WARNING)
        try:
            note = await self.practice.round_trip()
        except PracticeCloseFailed:
            self.book.remember_flag(card_id, "demo_close_failed")
            raise PracticeCloseFailed(CLOSE_WARNING) from None
        except asyncio.CancelledError:
            self.book.remember_flag(card_id, "demo_uncertain")
            raise
        except (PracticeUnavailable, httpx.HTTPError, TimeoutError, OSError, ValueError, TypeError, RuntimeError, ArithmeticError):
            self.book.remember_flag(card_id, "demo_uncertain")
            raise PracticeUncertain(UNCERTAIN_WARNING) from None
        if not note:
            self.book.remember_flag(card_id, "demo_uncertain")
            raise PracticeUncertain(UNCERTAIN_WARNING)
        self.book.remember_flag(card_id, "demo_round_trip")
        return OPEN_AND_CLOSE

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
