"""Chronological training/validation/final holdout and expanding walk-forward."""

from decimal import Decimal
from typing import Any

from ..paper.contract import money
from .data import checksum, validate_dataset


def metrics(equities: list[str], trades: list[dict[str, Any]]) -> dict[str, Any]:
    peak = Decimal(equities[0])
    drawdown = Decimal(0)
    for value in equities:
        equity = Decimal(value)
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak if peak > 0 else Decimal(0))
    initial, final = Decimal(equities[0]), Decimal(equities[-1])
    if initial <= 0:
        raise ValueError("positive initial equity required")
    streak = max_streak = 0
    for trade in trades:
        streak = streak + 1 if Decimal(trade["net_pnl"]) < 0 else 0
        max_streak = max(max_streak, streak)
    n = len(trades)
    wins = sum(Decimal(t["net_pnl"]) > 0 for t in trades)
    low = high = rate = None
    if n:
        count = Decimal(n)
        p = Decimal(wins) / count
        z = Decimal("1.959963984540054")
        den = 1 + z * z / count
        center = (p + z * z / (2 * count)) / den
        radius = z * ((p * (1 - p) / count + z * z / (4 * count * count)).sqrt()) / den
        rate = str(p)
        low = str(max(Decimal(0), center - radius))
        high = str(min(Decimal(1), center + radius))
    return {
        "net_return": str((final - initial) / initial),
        "max_consecutive_losses": max_streak,
        "breakeven_policy": "zero_net_resets_streak",
        "max_drawdown": str(drawdown),
        "trade_count": n,
        "wins": wins,
        "losses": sum(Decimal(t["net_pnl"]) < 0 for t in trades),
        "win_rate": rate,
        "win_rate_95_low": low,
        "win_rate_95_high": high,
        "uncertainty": "Wilson95% interval assumes independent trials; trades are dependent, sample short; no profitability/live-validation inference",
    }


def evaluate(dataset: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    from .native import run_segment

    validate_dataset(dataset)
    first, last = 0, len(dataset["bars"])
    if settings is not None:
        from .configuration import evaluation_config

        settings, first, last = evaluation_config(settings, dataset)
    n = last - first
    if n < 100:
        raise ValueError("at least100closed bars for chronological evaluation")
    quantity = settings["quantity"] if settings else (
        "0.010" if dataset["symbol"].startswith("BTC") else "0.10"
    )
    train_end = first + n * 6 // 10
    test_start = first + n * 8 // 10

    def run(config: dict[str, Any], start: int, end: int) -> dict[str, Any]:
        result = run_segment(dataset, config, start, end)
        result.update(
            metrics(
                [result["initial_equity"]] + [p["equity"] for p in result["curve"]],
                result["trades"],
            )
        )
        return result

    def select(end: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        candidates = []
        windows = [(settings["fast"], settings["slow"])] if settings else [(3, 10), (5, 20)]
        for fast, slow in windows:
            config = {"strategy": "SMA_CROSS_V1", "quantity": quantity, "fast": fast, "slow": slow}
            if settings:
                config.update({k: settings[k] for k in (
                    "config_version", "fee_rate", "slippage_ticks", "leverage"
                )})
            result = run(config, first, end)
            candidates.append(
                {
                    "config": config,
                    "net_pnl": result["net_pnl"],
                    "trade_count": result["trade_count"],
                }
            )
        selected = min(
            candidates,
            key=lambda c: (-Decimal(c["net_pnl"]), c["config"]["fast"], c["config"]["slow"]),
        )["config"]
        return selected, candidates

    selected, candidates = select(train_end)
    walk = []
    for pct in (50, 60, 70):
        train = first + n * pct // 100
        end = first + n * (pct + 10) // 100
        config, choices = select(train)
        walk.append(
            {
                "train_range": [first, train],
                "test_range": [train, end],
                "selected_config": config,
                "training_candidates": choices,
                "result": run(config, train, end),
            }
        )
    report = {
        "schema": "RESEARCH_REPORT_V1",
        "engine_version": "1.231.0",
        "dataset_id": dataset["dataset_id"],
        "market": dataset["market"],
        "symbol": dataset["symbol"],
        "synthetic": dataset["synthetic"],
        "portfolio_scope": "independent_single_instrument",
        "shared_portfolio_status": "UNRUN",
        "performance_status": "SYNTHETIC_ONLY_NOT_REAL_FUTURES_VALIDATION"
        if dataset["synthetic"]
        else "SPOT_RESEARCH_SHORT_FUNCTIONAL_SAMPLE_NOT_PROFITABILITY_VALIDATION",
        "sample_warning": "30days/720hourly bars (or shorter synthetic fixture) is a short functional sample; not profitability, live readiness or perpetual-data validation",
        "splits": {
            "train": [first, train_end],
            "validation": [train_end, test_start],
            "test": [test_start, last],
        },
        "selected_config": selected,
        "training_candidates": candidates,
        "validation": run(selected, train_end, test_start),
        "test": run(selected, test_start, last),
        "walk_forward": walk,
        "buy_hold_test": run({**selected, "strategy": "BUY_HOLD_V1"}, test_start, last),
        "assumptions": {
            "seed": money(Decimal(10000)),
            "fee_rate": "0.0004",
            "adverse_slippage": "1native instrument tick",
            "funding": "unmodeled0, not futures funding verification",
            "path": "O-L-H-C inferred path",
            "signal": "closed-only SMA; next executable bar open; past-only warmup; flat initial segment account",
            "selection": "training only; validation report only; final20% untouched; two candidates introduces selection bias",
            "leverage": None if dataset["market"] == "spot" else 10,
        },
        "network_sandbox": "direct_function_not_OS_isolated",
    }
    if settings:
        report.update(
            schema="RESEARCH_REPORT_V2", evaluation_config=settings,
            config_hash=checksum(settings), requested_range=[settings["start_ms"], settings["end_ms"]],
        )
        report["assumptions"].update(
            fee_rate=settings["fee_rate"], adverse_slippage=f"{settings['slippage_ticks']}native ticks",
            leverage=None if dataset["market"] == "spot" else settings["leverage"],
            selection="explicit frozen settings; no optimization; chronological holdout and walk-forward",
        )
    report["reproducibility_hash"] = checksum(report)
    return report
