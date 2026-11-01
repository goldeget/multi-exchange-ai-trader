"""SPOT-PAPER-v2 cash-only order contract; no leverage, borrowing or short commands."""

import re
from typing import Any

from .contract import instrument, price, quantity

VERSION = "SPOT-PAPER-v2"
TICK_SOURCE = "LOCAL_SPOT_SCENARIO_V2"
QUALIFIED = False  # Flip only after the full Linux native qualification suite passes.


def validate_spot_event(event: dict[str, Any]) -> None:
    symbol = instrument(event.get("instrument"))
    kind = event.get("kind")
    if kind == "tick":
        if set(event) != {"kind", "instrument", "price"}:
            raise ValueError("unsupported SPOT tick fields")
        price(event["price"], symbol)
    elif kind == "cancel":
        if set(event) != {"kind", "instrument", "order_id"}:
            raise ValueError("unsupported SPOT cancellation fields")
        order_id = event["order_id"]
        if not isinstance(order_id, str) or re.fullmatch(r"S\d{6}", order_id) is None:
            raise ValueError("SPOT native order identifier required")
    elif kind == "order":
        order_type = event.get("order_type")
        fields = {"kind", "instrument", "side", "quantity", "order_type"}
        if order_type == "LIMIT":
            fields.add("limit_price")
            price(event.get("limit_price"), symbol)
        elif order_type != "MARKET":
            raise ValueError("SPOT order type MARKET/LIMIT required")
        if set(event) != fields or event.get("side") not in {"buy", "sell"}:
            raise ValueError("SPOT cash buy/sell fields required")
        quantity(event.get("quantity"), symbol)
    else:
        raise ValueError("unsupported SPOT command")
