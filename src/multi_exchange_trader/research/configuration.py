"""Explicit evaluation V2 inputs, separate from immutable V1 dataset provenance."""

from decimal import Decimal
from typing import Any

from .data import decimal, validate_dataset
from .strategy import config_check

FIELDS = {
    "config_version", "start_ms", "end_ms", "fast", "slow", "quantity",
    "fee_rate", "slippage_ticks", "leverage",
}
OPTIONAL_FIELDS = {"initial_equity", "train_percent", "validation_percent"}


def evaluation_config(
    config: dict[str, Any], dataset: dict[str, Any]
) -> tuple[dict[str, Any], int, int]:
    validate_dataset(dataset)
    if (not FIELDS <= set(config) <= FIELDS | OPTIONAL_FIELDS
            or type(config["config_version"]) is not int or config["config_version"] != 2):
        raise ValueError("evaluation config version/fields")
    start, end, step = config["start_ms"], config["end_ms"], dataset["interval_ms"]
    if (
        type(start) is not int or type(end) is not int
        or not dataset["start_ms"] <= start < end <= dataset["end_ms"]
        or (start - dataset["start_ms"]) % step or (end - start) % step
        or (end - start) // step < 100
    ):
        raise ValueError("evaluation aligned range needs at least100available closed bars")
    config_check(
        {"strategy": "SMA_CROSS_V1", **{k: config[k] for k in ("fast", "slow", "quantity")}},
        dataset["symbol"],
    )
    if decimal(config["fee_rate"], zero=True) > Decimal("0.01"):
        raise ValueError("evaluation fee rate within0..0.01 required")
    if type(config["slippage_ticks"]) is not int or not 0 <= config["slippage_ticks"] <= 20:
        raise ValueError("evaluation slippage ticks within0..20 required")
    if type(config["leverage"]) is not int or not 1 <= config["leverage"] <= 20:
        raise ValueError("evaluation synthetic leverage within1..20 required")
    if dataset["market"] == "spot" and config["leverage"] != 1:
        raise ValueError("spot CASH evaluation requires leverage1")
    first, last = (start - dataset["start_ms"]) // step, (end - dataset["start_ms"]) // step
    capital = decimal(config.get("initial_equity", "10000"))
    if not Decimal(100) <= capital <= Decimal(1000000):
        raise ValueError("evaluation initial equity within100..1000000 required")
    train, validation = config.get("train_percent", 60), config.get("validation_percent", 20)
    if (type(train) is not int or type(validation) is not int
            or not 40 <= train <= 70 or not 10 <= validation <= 30 or train + validation > 90):
        raise ValueError("evaluation chronological split percentages")
    tick = Decimal("0.1") if dataset["symbol"] == "BTC-PAPER" else Decimal("0.01")
    if any(Decimal(bar["low"]) <= tick * config["slippage_ticks"] for bar in dataset["bars"][first:last]):
        raise ValueError("evaluation slippage would produce nonpositive quotes")
    return dict(config), first, last
