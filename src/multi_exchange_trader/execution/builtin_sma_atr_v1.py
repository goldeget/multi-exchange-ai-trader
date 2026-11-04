"""Finite installed SMA/true-range implementation; derived prices are not native orders.

Only this installed Python implementation executes. Published bytes are compared,
never imported/evaluated. Exact rational outputs require later venue-grid/risk
validation; no capital, quantity, leverage, fee or financial authority is supplied.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from typing import Literal

from ..market_data.adapters.base import INTERVAL_SECONDS
from ..market_data.models import Candle

ENGINE_CONTRACT_V1 = "SMA_ATR_SIGNAL_V1"
_MAX_BYTES = 64 * 1024
_IMPLEMENTATION_PATH = Path(__file__)
with _IMPLEMENTATION_PATH.open("rb") as _source:
    _IMPLEMENTATION_BYTES = _source.read(_MAX_BYTES + 1)


def implementation_bytes_v1() -> bytes:
    """Actual installed source bytes; changing the loaded source fails closed."""
    with _IMPLEMENTATION_PATH.open("rb") as stream:
        current = stream.read(_MAX_BYTES + 1)
    if not 0 < len(current) <= _MAX_BYTES or current != _IMPLEMENTATION_BYTES:
        raise ValueError("BUILTIN_IMPLEMENTATION_CHANGED")
    return _IMPLEMENTATION_BYTES


@dataclass(frozen=True)
class BuiltinConfigurationV1:
    interval: str
    fast: int
    slow: int
    atr_bars: int
    stop_atr_multiple: Fraction
    target_r: Fraction
    max_bar_age_seconds: int
    signal_ttl_seconds: int

    @property
    def required_bars(self) -> int:
        return max(self.slow, self.atr_bars + 1)


@dataclass(frozen=True)
class BuiltinDirectionV1:
    side: Literal["LONG", "SHORT", "FLAT"]
    entry_reference_price: Fraction
    stop_price: Fraction | None
    take_profit_price: Fraction | None


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("BUILTIN_CONFIGURATION_INVALID")
        result[key] = value
    return result


def parse_configuration_v1(raw: bytes) -> BuiltinConfigurationV1:
    if type(raw) is not bytes or not 0 < len(raw) <= _MAX_BYTES:
        raise ValueError("BUILTIN_CONFIGURATION_INVALID")
    try:
        data = json.loads(raw, object_pairs_hook=_pairs)
        names = {"schema_version", "interval", "fast", "slow", "atr_bars",
                 "stop_atr_multiple", "target_r", "max_bar_age_seconds", "signal_ttl_seconds"}
        if type(data) is not dict or set(data) != names:
            raise ValueError
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError
        if type(data["interval"]) is not str or data["interval"] not in INTERVAL_SECONDS:
            raise ValueError
        for name in ("fast", "slow", "atr_bars", "max_bar_age_seconds", "signal_ttl_seconds"):
            if type(data[name]) is not int:
                raise ValueError
        if not (1 <= data["fast"] < data["slow"] <= 499 and 1 <= data["atr_bars"] <= 498
                and 1 <= data["max_bar_age_seconds"] <= 86400
                and 1 <= data["signal_ttl_seconds"] <= 300):
            raise ValueError
        ratios = []
        for name in ("stop_atr_multiple", "target_r"):
            text = data[name]
            if type(text) is not str or re.fullmatch(r"(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,8})?", text) is None:
                raise ValueError
            ratio = Fraction(text)
            if ratio <= 0:
                raise ValueError
            ratios.append(ratio)
        return BuiltinConfigurationV1(data["interval"], data["fast"], data["slow"],
            data["atr_bars"], ratios[0], ratios[1], data["max_bar_age_seconds"],
            data["signal_ttl_seconds"])
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ValueError("BUILTIN_CONFIGURATION_INVALID") from None


def derive_direction_v1(candles: tuple[Candle, ...], configuration: BuiltinConfigurationV1) -> BuiltinDirectionV1:
    """SMA direction and exact mean true-range protection; consumes no live quote."""
    if type(configuration) is not BuiltinConfigurationV1 or type(candles) is not tuple:
        raise ValueError("BUILTIN_SIGNAL_INPUT_INVALID")
    if len(candles) < configuration.required_bars or any(type(bar) is not Candle or not bar.is_closed for bar in candles):
        raise ValueError("BUILTIN_SIGNAL_INPUT_INVALID")
    close = tuple(Fraction(bar.close) for bar in candles)
    fast = sum(close[-configuration.fast:], Fraction(0)) / configuration.fast
    slow = sum(close[-configuration.slow:], Fraction(0)) / configuration.slow
    entry = close[-1]
    if fast == slow:
        return BuiltinDirectionV1("FLAT", entry, None, None)
    recent = candles[-configuration.atr_bars - 1:]
    ranges = tuple(max(Fraction(right.high) - Fraction(right.low),
        abs(Fraction(right.high) - Fraction(left.close)),
        abs(Fraction(right.low) - Fraction(left.close))) for left, right in pairwise(recent))
    distance = sum(ranges, Fraction(0)) / configuration.atr_bars * configuration.stop_atr_multiple
    direction = 1 if fast > slow else -1
    stop, target = entry - direction * distance, entry + direction * distance * configuration.target_r
    if distance <= 0 or stop <= 0 or target <= 0:
        raise ValueError("BUILTIN_SIGNAL_PRICE_INVALID")
    return BuiltinDirectionV1("LONG" if direction == 1 else "SHORT", entry, stop, target)
