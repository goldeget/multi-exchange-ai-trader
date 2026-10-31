"""Versioned educational assumptions; strict string Decimal inputs."""

import re
from decimal import ROUND_CEILING, Decimal
from typing import Any

VERSION = "PAPER-v1"
SEED = Decimal(10000)
FEE = Decimal("0.0004")
MMR = Decimal("0.005")
UNIT = Decimal("0.00000001")
SPECS = {
    "BTC-PAPER": {"tick": "0.1", "lot": "0.001", "max_qty": "10.000", "seed": "60000.0"},
    "ETH-PAPER": {"tick": "0.01", "lot": "0.01", "max_qty": "100.00", "seed": "3000.00"},
}


def decimal(value: Any) -> Decimal:
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d{1,8})?", value):
        raise ValueError("decimal string required")
    result = Decimal(value)
    if not result.is_finite() or result <= 0:
        raise ValueError("positive decimal required")
    return result


def instrument(value: Any) -> str:
    if not isinstance(value, str) or value not in SPECS:
        raise ValueError("unsupported PAPER instrument")
    return str(value)


def price(value: Any, symbol: str) -> Decimal:
    result = decimal(value)
    if result < 1 or result > 1000000 or result % Decimal(SPECS[symbol]["tick"]):
        raise ValueError("price tick or bounds")
    return result


def quantity(value: Any, symbol: str) -> Decimal:
    result = decimal(value)
    if (
        result < Decimal(SPECS[symbol]["lot"])
        or result > Decimal(SPECS[symbol]["max_qty"])
        or result % Decimal(SPECS[symbol]["lot"])
    ):
        raise ValueError("quantity lot or bounds")
    return result


def leverage(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 20:
        raise ValueError("leverage integer1..20")
    return value


def money(value: Decimal) -> str:
    return format(value.quantize(UNIT), "f")


def reserve(qty: Decimal, entry: Decimal, lev: int) -> Decimal:
    return (qty * entry / Decimal(lev)).quantize(UNIT, rounding=ROUND_CEILING)


def validate_event(e: dict[str, Any]) -> None:
    symbol = instrument(e.get("instrument"))
    kind = e.get("kind")
    allowed = {
        "enter": {"kind", "instrument", "side", "quantity", "leverage"},
        "close": {"kind", "instrument", "quantity"},
        "protect": {"kind", "instrument", "sl", "tp"},
        "tick": {"kind", "instrument", "price"},
    }
    if not isinstance(kind, str) or kind not in allowed or set(e) - allowed[kind]:
        raise ValueError("unsupported PAPER command fields")
    if kind == "enter":
        if e.get("side") not in ("long", "short"):
            raise ValueError("side long/short")
        quantity(e.get("quantity"), symbol)
        leverage(e.get("leverage"))
    elif kind == "close":
        quantity(e.get("quantity"), symbol)
    elif kind == "tick":
        price(e.get("price"), symbol)
    elif kind == "protect":
        for field in ("sl", "tp"):
            if e.get(field) is not None:
                price(e[field], symbol)
