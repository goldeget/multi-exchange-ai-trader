"""Deterministic recommendation numbers. Untrusted text is ignored."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from typing import Any

from multi_exchange_trader.market_data.models import Candle
from multi_exchange_trader.paper.contract import SPECS
from multi_exchange_trader.research.strategy import signal as sma_signal

FAST = 5
SLOW = 20
LEVERAGE = 2
INTERVAL = "15m"
INTERVAL_DELTA = timedelta(minutes=15)
FRESH_LIMIT = timedelta(minutes=45)
CARD_TTL = timedelta(minutes=15)
ATR_BARS = 14
STOP_ATR = Decimal(2)
TARGET_R = Decimal(2)
CANDLE_LIMIT = 80
RULE_LABEL = "규칙 설명"
WHY_NOT = (
    "규칙으로 만든 설명입니다. 유료 분석이 아닙니다. 수익을 보장하지 않습니다. "
    "가격이 다시 뒤집히거나, 시세가 오래되거나, 만료 시각이 지나면 승인할 수 없습니다."
)

EXCHANGES = ("binance", "bybit", "okx")
SYMBOLS = ("BTCUSDT", "ETHUSDT")
MARKETS = ("spot", "perpetual")
INSTRUMENT = {"BTCUSDT": "BTC-PAPER", "ETHUSDT": "ETH-PAPER"}
EXCHANGE_KO = {"binance": "바이낸스", "bybit": "바이비트", "okx": "OKX"}
SYMBOL_KO = {"BTCUSDT": "BTC / USDT", "ETHUSDT": "ETH / USDT"}
MARKET_KO = {"spot": "현물", "perpetual": "선물"}
SIDE_KO = {"long": "오르면 이익", "short": "내리면 이익", "flat": "관망"}
SELECT_EXCHANGE = {"바이낸스": "binance", "바이비트": "bybit", "OKX": "okx"}
SELECT_SYMBOL = {"BTC / USDT": "BTCUSDT", "ETH / USDT": "ETHUSDT"}
SELECT_MARKET = {"현물": "spot", "선물": "perpetual"}


@dataclass(frozen=True)
class Plan:
    exchange: str
    symbol: str
    market_type: str
    side: str
    entry: str | None
    stop: str | None
    take_profit: str | None
    quantity: str | None
    leverage: int | None
    fresh: bool
    freshness: str
    why: str
    why_not: str
    expires_at: datetime
    closed_at: datetime | None
    actionable: bool
    instrument: str | None

    def identity(self) -> dict[str, object]:
        closed = None if self.closed_at is None else self.closed_at.isoformat()
        return {
            "v": 1,
            "exchange": self.exchange,
            "symbol": self.symbol,
            "market_type": self.market_type,
            "side": self.side,
            "entry": self.entry,
            "stop": self.stop,
            "take_profit": self.take_profit,
            "quantity": self.quantity,
            "leverage": self.leverage,
            "closed_at": closed,
        }

    def payload(self) -> dict[str, object]:
        stored = self.identity()
        stored.update(
            {
                "fresh": self.fresh,
                "freshness": self.freshness,
                "why": self.why,
                "why_not": self.why_not,
                "expires_at": self.expires_at.isoformat(),
                "actionable": self.actionable,
                "instrument": self.instrument,
                "explanation": RULE_LABEL,
            }
        )
        return stored


def plan_id(plan: Plan) -> str:
    raw = json.dumps(plan.identity(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _show(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def _align(value: Decimal, tick: Decimal, rounding: str) -> Decimal:
    mode = {"down": ROUND_FLOOR, "up": ROUND_CEILING, "near": ROUND_HALF_EVEN}[rounding]
    return (value / tick).to_integral_value(rounding=mode) * tick


def _explain(
    side: str,
    market_type: str,
    fast_mean: Decimal | None,
    slow_mean: Decimal | None,
    bar_count: int,
) -> tuple[str, str]:
    if bar_count < SLOW or fast_mean is None or slow_mean is None:
        why = (
            f"마감된 봉이 {bar_count}개뿐입니다. 비교에는 {SLOW}개가 필요합니다. "
            "들어가라는 추천이 아닙니다."
        )
        return why, WHY_NOT
    short = _show(fast_mean)
    longer = _show(slow_mean)
    if side == "long":
        why = f"짧은 평균이 긴 평균보다 높습니다. 짧은 평균 {short}, 긴 평균 {longer}."
    elif side == "short":
        why = (
            "짧은 평균이 긴 평균보다 낮습니다. 선물에서는 내리는 쪽을 봅니다. "
            f"짧은 평균 {short}, 긴 평균 {longer}."
        )
    elif market_type == "spot" and fast_mean < slow_mean:
        why = (
            "짧은 평균이 긴 평균보다 낮습니다. 현물은 내리는 쪽 추천을 만들지 않습니다. "
            f"짧은 평균 {short}, 긴 평균 {longer}."
        )
    else:
        why = (
            "짧은 평균과 긴 평균이 같습니다. 들어가라는 추천이 아닙니다. "
            f"짧은 평균 {short}, 긴 평균 {longer}."
        )
    return why, WHY_NOT


def _blank(
    exchange: str,
    symbol: str,
    market_type: str,
    now: datetime,
    why: str,
    why_not: str,
    freshness: str,
) -> Plan:
    return Plan(
        exchange=exchange,
        symbol=symbol,
        market_type=market_type,
        side="flat",
        entry=None,
        stop=None,
        take_profit=None,
        quantity=None,
        leverage=None,
        fresh=False,
        freshness=freshness,
        why=why,
        why_not=why_not,
        expires_at=now + CARD_TTL,
        closed_at=None,
        actionable=False,
        instrument=None,
    )


def build_plan(
    exchange: str,
    symbol: str,
    market_type: str,
    candles: list[Candle],
    now: datetime,
    note: str = "",
) -> Plan:
    """Build one plan. `note` is untrusted and cannot change quantity or leverage."""
    del note
    if exchange not in EXCHANGES or symbol not in SYMBOLS or market_type not in MARKETS:
        raise ValueError("selection")
    instrument = INSTRUMENT[symbol]
    tick = Decimal(SPECS[instrument]["tick"])
    closed = tuple(
        sorted((item for item in candles if item.is_closed), key=lambda item: item.timestamp)
    )
    if any(closed[index].timestamp == closed[index - 1].timestamp for index in range(1, len(closed))):
        why, why_not = _explain("flat", market_type, None, None, 0)
        return _blank(exchange, symbol, market_type, now, "같은 시각의 봉이 겹쳤습니다.", why_not, "봉이 겹침")
    gapped = any(
        closed[index].timestamp - closed[index - 1].timestamp != INTERVAL_DELTA
        for index in range(1, len(closed))
    )
    why, why_not = _explain("flat", market_type, None, None, len(closed))
    if len(closed) < SLOW:
        freshness = "봉이 빠졌습니다" if gapped else "봉이 부족합니다"
        return _blank(exchange, symbol, market_type, now, why, why_not, freshness)

    last = closed[-1]
    close_at = last.timestamp + INTERVAL_DELTA
    age = now - close_at
    if age < timedelta(seconds=-5):
        freshness, fresh = "시각이 맞지 않음", False
    elif age <= INTERVAL_DELTA:
        freshness, fresh = "방금 마감된 봉", True
    elif age <= FRESH_LIMIT:
        freshness, fresh = "조금 지난 봉", True
    else:
        freshness, fresh = "오래됨", False
    if gapped:
        fresh = False
        freshness = "봉이 빠졌습니다"
        why_not = f"{why_not} 봉이 이어지지 않아서 승인할 수 없습니다."

    bars: list[dict[str, Any]] = [{"close": _text(item.close)} for item in closed]
    market_key = "synthetic_perpetual" if market_type == "perpetual" else "spot"
    side = sma_signal(bars, FAST, SLOW, market_key)
    fast_mean = sum((Decimal(bar["close"]) for bar in bars[-FAST:]), Decimal(0)) / FAST
    slow_mean = sum((Decimal(bar["close"]) for bar in bars[-SLOW:]), Decimal(0)) / SLOW
    why, base_why_not = _explain(side, market_type, fast_mean, slow_mean, len(closed))
    if gapped:
        why_not = f"{base_why_not} 봉이 이어지지 않아서 승인할 수 없습니다."
    else:
        why_not = base_why_not
    if not fresh:
        why_not = f"{why_not} 시세가 오래되었거나 시각이 맞지 않아서 승인할 수 없습니다."
    expires = min(now + CARD_TTL, close_at + timedelta(minutes=30))
    if side not in ("long", "short"):
        return Plan(
            exchange=exchange,
            symbol=symbol,
            market_type=market_type,
            side="flat",
            entry=None,
            stop=None,
            take_profit=None,
            quantity=None,
            leverage=None,
            fresh=fresh,
            freshness=freshness,
            why=why,
            why_not=why_not,
            expires_at=expires,
            closed_at=close_at,
            actionable=False,
            instrument=None,
        )

    ranges = [item.high - item.low for item in closed[-ATR_BARS:]]
    distance = max(sum(ranges, Decimal(0)) / Decimal(len(ranges)) * STOP_ATR, tick * 2)
    entry = _align(last.close, tick, "near")
    if side == "long":
        stop = _align(entry - distance, tick, "down")
        take_profit = _align(entry + distance * TARGET_R, tick, "up")
        if stop >= entry:
            stop = entry - tick
        if take_profit <= entry:
            take_profit = entry + tick
    else:
        stop = _align(entry + distance, tick, "up")
        take_profit = _align(entry - distance * TARGET_R, tick, "down")
        if stop <= entry:
            stop = entry + tick
        if take_profit >= entry:
            take_profit = entry - tick

    def valid(price: Decimal) -> bool:
        return Decimal(1) <= price <= Decimal(1_000_000) and price % tick == 0

    prices_ok = valid(entry) and valid(stop) and valid(take_profit)
    actionable = bool(prices_ok and fresh and not gapped and expires > now)
    if not prices_ok:
        why = "가격을 주문 칸에 맞추지 못했습니다. 들어가라는 추천이 아닙니다."
        return Plan(
            exchange=exchange,
            symbol=symbol,
            market_type=market_type,
            side=side,
            entry=None,
            stop=None,
            take_profit=None,
            quantity=None,
            leverage=None,
            fresh=fresh,
            freshness=freshness,
            why=why,
            why_not=why_not,
            expires_at=expires,
            closed_at=close_at,
            actionable=False,
            instrument=None,
        )
    return Plan(
        exchange=exchange,
        symbol=symbol,
        market_type=market_type,
        side=side,
        entry=_text(entry),
        stop=_text(stop),
        take_profit=_text(take_profit),
        quantity=SPECS[instrument]["lot"],
        leverage=LEVERAGE,
        fresh=fresh,
        freshness=freshness,
        why=why,
        why_not=why_not,
        expires_at=expires,
        closed_at=close_at,
        actionable=actionable,
        instrument=instrument,
    )
