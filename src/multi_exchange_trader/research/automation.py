"""Risk decisions over existing native PAPER replay; not a matching/accounting engine."""

from decimal import Decimal
from typing import Any

from ..paper.contract import SEED, SPECS, money
from ..paper.sandbox import sandbox_replay
from .data import decimal, validate_dataset
from .strategy import config_check, signal

MAX_BARS = 80
MAX_EVENTS = 500
EXIT_RESERVE = 10
MAX_STEP = 6
TERMINAL = {"STOPPED", "HALTED"}


def ordinary_allowed(count: int) -> bool:
    return count + MAX_STEP + EXIT_RESERVE <= MAX_EVENTS


def streak_after(current: int, net: str) -> int:
    return current + 1 if Decimal(net) < 0 else 0


def default_auto_config(dataset_id: str, *, version: int = 1) -> dict[str, Any]:
    if type(version) is not int or version not in (1, 2):
        raise ValueError("automatic config version")
    config: dict[str, Any] = {
        "dataset_id": dataset_id,
        "bar_limit": 80,
        "quantity": "0.010",
        "fast": 3,
        "slow": 10,
        "max_exposure": "1000.00",
        "max_loss": "0.05",
        "max_drawdown": "0.05",
        "max_consecutive_losses": 3,
    }
    if version == 2:
        config.update(config_version=2, leverage=3)
    return config


def validate_config(config: dict[str, Any], dataset: dict[str, Any]) -> None:
    validate_dataset(dataset)
    version = config.get("config_version", 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError("automatic config version")
    if (
        set(config) != set(default_auto_config(dataset["dataset_id"], version=version))
        or config["dataset_id"] != dataset["dataset_id"]
        or not dataset["synthetic"]
        or dataset["market"] != "synthetic_perpetual"
    ):
        raise ValueError("automatic PAPER only accepts local synthetic perpetual data")
    if version == 2 and (
        type(config["leverage"]) is not int or not 1 <= config["leverage"] <= 20
    ):
        raise ValueError("synthetic PAPER leverage must be an integer within 1..20")
    config_check(
        {
            "strategy": "SMA_CROSS_V1",
            "quantity": config["quantity"],
            "fast": config["fast"],
            "slow": config["slow"],
        },
        dataset["symbol"],
    )
    if type(config["bar_limit"]) is not int or not 1 <= config["bar_limit"] <= min(
        MAX_BARS, len(dataset["bars"])
    ):
        raise ValueError("bounded automatic source maximum80bars")
    if (
        not 0 < decimal(config["max_loss"]) <= Decimal("0.2")
        or not 0 < decimal(config["max_drawdown"]) <= Decimal("0.2")
        or not 0 < decimal(config["max_exposure"]) <= 10000
    ):
        raise ValueError("automatic risk limits")
    if (
        type(config["max_consecutive_losses"]) is not int
        or not 1 <= config["max_consecutive_losses"] <= 10
    ):
        raise ValueError("automatic loss streak bound")


def exposure(native: dict[str, Any]) -> Decimal:
    return sum(
        (Decimal(p["quantity"]) * Decimal(p["mark"]) for p in native["positions"]), Decimal(0)
    )


def closed_outcomes(fills: list[dict[str, Any]]) -> list[str]:
    """Statistics from native fills; aggregate partial exits into completed roundtrips."""
    outcomes: list[str] = []
    remaining = Decimal(0)
    entry = fees = gross = Decimal(0)
    direction = 1
    for fill in fills:
        qty, price, fee = (Decimal(fill[k]) for k in ("quantity", "price", "fee"))
        if fill["role"] == "enter":
            if remaining:
                raise ValueError("native trade statistics overlap")
            remaining, entry, fees, gross = qty, price, fee, Decimal(0)
            direction = 1 if fill["side"] == "BUY" else -1
        else:
            if qty > remaining or not remaining:
                raise ValueError("native trade statistics quantity")
            gross += (price - entry) * qty * direction
            fees += fee
            remaining -= qty
            if not remaining:
                outcomes.append(money(gross - fees))
    return outcomes


def track_closed(state: dict[str, Any]) -> None:
    outcomes = closed_outcomes(state["native"]["fills"])
    processed = state.get("processed_closed_trades", 0)
    if type(processed) is not int or not 0 <= processed <= len(outcomes):
        raise ValueError("native completed trade prefix reconciliation")
    for net in outcomes[processed:]:
        state["loss_streak"] = streak_after(state["loss_streak"], net)
    state["processed_closed_trades"] = len(outcomes)
    state["last_closed_net"] = state["native"]["closed_net_pnl"]
    if not state["native"]["positions"] and sum(
        (Decimal(n) for n in outcomes), Decimal(0)
    ) != Decimal(state["last_closed_net"]):
        raise ValueError("native completed trade net reconciliation")


def flatten(state: dict[str, Any]) -> None:
    for p in list(state["native"]["positions"]):
        if len(state["native_events"]) + 1 >= MAX_EVENTS:
            raise ValueError("reserved exit capacity invariant violated")
        state["native_events"].append(
            {"kind": "close", "instrument": p["instrument"], "quantity": p["quantity"]}
        )
        state["native"] = sandbox_replay(state["native_events"])
    track_closed(state)
    state["exposure"] = "0"


def advance(state: dict[str, Any], dataset: dict[str, Any]) -> dict[str, Any]:
    config = state["config"]
    cursor = state["cursor"]
    symbol = dataset["symbol"]
    if not ordinary_allowed(len(state["native_events"]) + 1):
        state.update(status="HALTED", reason="event_budget")
        flatten(state)
        return state
    bar = dataset["bars"][cursor]

    def assess() -> bool:
        native = state["native"]
        equity = Decimal(native["equity"])
        state["high_water"] = str(max(Decimal(state["high_water"]), equity))
        track_closed(state)
        reason = None
        if SEED - equity >= SEED * Decimal(config["max_loss"]):
            reason = "loss_limit"
        elif (Decimal(state["high_water"]) - equity) / Decimal(state["high_water"]) >= Decimal(
            config["max_drawdown"]
        ):
            reason = "drawdown_limit"
        elif exposure(native) > Decimal(config["max_exposure"]):
            reason = "exposure_limit"
        elif state["loss_streak"] >= config["max_consecutive_losses"]:
            reason = "consecutive_loss_limit"
        state["exposure"] = str(exposure(native))
        if reason:
            state.update(status="HALTED", reason=reason)
            flatten(state)
            return False
        return True

    def emit(event: dict[str, Any]) -> bool:
        state["native_events"].append({"instrument": symbol, **event})
        state["native"] = sandbox_replay(state["native_events"])
        return assess()

    target = signal(dataset["bars"][:cursor], config["fast"], config["slow"], dataset["market"])
    phases: tuple[str, ...] = ("open", "low", "high", "close")
    offsets = {
        "open": 0,
        "low": dataset["interval_ms"] // 3,
        "high": 2 * dataset["interval_ms"] // 3,
        "close": dataset["interval_ms"] - 1,
    }
    # Flat warm-up has no exposure or fills: replay all four real native ticks together.
    # Once a position/entry exists, inspect every native mark before proceeding.
    if not state["native"]["positions"] and target == "flat":
        state["native_events"].extend(
            {"kind": "tick", "instrument": symbol, "price": bar[p]} for p in phases
        )
        state["native"] = sandbox_replay(state["native_events"])
        state["last_virtual_time_ms"] = bar["time_ms"] + offsets["close"]
        assess()
        phases = ()
    for phase in phases:
        state["last_virtual_time_ms"] = bar["time_ms"] + offsets[phase]
        if not emit({"kind": "tick", "price": bar[phase]}):
            break
        if phase == "open":
            target = signal(
                dataset["bars"][:cursor], config["fast"], config["slow"], dataset["market"]
            )
            positions = state["native"]["positions"]
            position = positions[0] if positions else None
            current = position["side"] if position else "flat"
            if current != target:
                if position and not emit({"kind": "close", "quantity": position["quantity"]}):
                    break
                if target != "flat" and state["status"] != "ENTRY_PAUSED":
                    adverse = Decimal(bar["open"]) + Decimal(SPECS[symbol]["tick"]) * (
                        1 if target == "long" else -1
                    )
                    if adverse * Decimal(config["quantity"]) > Decimal(config["max_exposure"]):
                        state.update(status="HALTED", reason="exposure_limit")
                        flatten(state)
                        break
                    if not emit(
                        {
                            "kind": "enter",
                            "side": target,
                            "quantity": config["quantity"],
                            "leverage": config.get("leverage", 10),
                        }
                    ):
                        break
    state["cursor"] = cursor + 1
    if state["status"] in {"RUNNING", "ENTRY_PAUSED"} and state["cursor"] >= config["bar_limit"]:
        state.update(status="STOPPED", reason="source_exhausted")
        flatten(state)
    return state
