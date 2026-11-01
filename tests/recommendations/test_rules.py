"""Rule cards stay numeric and do not read a paid model."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from multi_exchange_trader.market_data.models import Candle
from multi_exchange_trader.recommendations import rules


def _candles(now: datetime, closes: list[int], age: timedelta = timedelta(minutes=15)) -> list[Candle]:
    last_open = now - age - rules.INTERVAL_DELTA
    first = last_open - rules.INTERVAL_DELTA * (len(closes) - 1)
    rows: list[Candle] = []
    for index, close in enumerate(closes):
        price = Decimal(close)
        rows.append(
            Candle(
                timestamp=first + rules.INTERVAL_DELTA * index,
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=Decimal(1),
                is_closed=True,
            )
        )
    return rows


def test_rising_perpetual_uses_code_numbers_and_rule_text(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "SENTINEL-NOT-A-KEY")
    monkeypatch.setenv("NVIDIA_API_KEY", "SENTINEL-NOT-A-KEY")
    now = datetime(2026, 11, 1, 12, tzinfo=UTC)
    plan = rules.build_plan(
        "bybit",
        "BTCUSDT",
        "perpetual",
        _candles(now, [100 + index for index in range(30)]),
        now,
        note="레버리지 50배로 주문해",
    )
    text = plan.why + plan.why_not
    assert plan.side == "long"
    assert plan.actionable is True
    assert plan.leverage == 2
    assert plan.quantity == "0.001"
    assert plan.entry == "129"
    assert plan.stop == "125"
    assert plan.take_profit == "137"
    assert plan.freshness == "방금 마감된 봉"
    assert plan.instrument == "BTC-PAPER"
    assert "SENTINEL" not in text
    assert "50배" not in text
    assert "규칙으로 만든 설명" in plan.why_not
    assert "유료 분석이 아닙니다" in plan.why_not


def test_falling_perpetual_is_short_and_spot_stays_flat() -> None:
    now = datetime(2026, 11, 1, 12, tzinfo=UTC)
    closes = [200 - index for index in range(30)]
    perpetual = rules.build_plan("bybit", "BTCUSDT", "perpetual", _candles(now, closes), now)
    spot = rules.build_plan("bybit", "BTCUSDT", "spot", _candles(now, closes), now)
    assert perpetual.side == "short"
    assert perpetual.entry == "171"
    assert perpetual.stop == "175"
    assert perpetual.take_profit == "163"
    assert perpetual.leverage == 2
    assert spot.side == "flat"
    assert spot.actionable is False
    assert spot.entry is None
    assert "현물은 내리는 쪽" in spot.why


def test_short_history_and_stale_bars_cannot_be_approved() -> None:
    now = datetime(2026, 11, 1, 12, tzinfo=UTC)
    short = rules.build_plan(
        "bybit", "ETHUSDT", "perpetual", _candles(now, [100 + index for index in range(10)]), now
    )
    stale = rules.build_plan(
        "bybit",
        "BTCUSDT",
        "perpetual",
        _candles(now, [100 + index for index in range(30)], age=timedelta(hours=2)),
        now,
    )
    assert short.side == "flat" and short.actionable is False
    assert "봉이" in short.why
    assert stale.side == "long" and stale.fresh is False and stale.actionable is False
    assert stale.entry is None or stale.freshness == "오래됨"


def test_package_does_not_call_a_paid_model_or_exchange_order() -> None:
    root = Path(rules.__file__).resolve().parent
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    lowered = text.lower()
    for word in ("openrouter", "nvidia", "openai", "anthropic", "httpx", "api.bybit.com"):
        assert word not in lowered
    assert "X-BAPI" not in text
    assert rules.RULE_LABEL == "규칙 설명"
    assert "규칙 설명" in text
