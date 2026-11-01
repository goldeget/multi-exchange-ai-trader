"""Past-only deterministic baseline; closed bars are the entire indicator input."""

from decimal import Decimal
from typing import Any

from .data import decimal


def config_check(config: dict[str, Any], symbol: str) -> None:
    fields = {"strategy", "quantity", "fast", "slow"}
    extra = {"config_version", "fee_rate", "slippage_ticks", "leverage"}
    if set(config) not in (fields, fields | extra) or config["strategy"] not in (
        "SMA_CROSS_V1",
        "BUY_HOLD_V1",
    ):
        raise ValueError("research strategy/config")
    if set(config) == fields | extra and (
        type(config["config_version"]) is not int or config["config_version"] != 2
        or decimal(config["fee_rate"], zero=True) > Decimal("0.01")
        or type(config["slippage_ticks"]) is not int or not 0 <= config["slippage_ticks"] <= 20
        or type(config["leverage"]) is not int or not 1 <= config["leverage"] <= 20
        or (not symbol.endswith("-PAPER") and config["leverage"] != 1)
    ):
        raise ValueError("research V2 execution costs")
    fast, slow = config["fast"], config["slow"]
    if type(fast) is not int or type(slow) is not int or not 1 <= fast < slow <= 100:
        raise ValueError("SMA windows")
    quantity = decimal(config["quantity"])
    lot = Decimal("0.001") if symbol.startswith("BTC") else Decimal("0.01")
    if quantity % lot or quantity > (10 if symbol.startswith("BTC") else 100):
        raise ValueError("research lot/quantity")


def signal(closed: list[dict[str, Any]], fast: int, slow: int, market: str) -> str:
    if len(closed) < slow:
        return "flat"
    fast_mean = sum((Decimal(b["close"]) for b in closed[-fast:]), Decimal(0)) / fast
    slow_mean = sum((Decimal(b["close"]) for b in closed[-slow:]), Decimal(0)) / slow
    if fast_mean > slow_mean:
        return "long"
    return "short" if fast_mean < slow_mean and market == "synthetic_perpetual" else "flat"
