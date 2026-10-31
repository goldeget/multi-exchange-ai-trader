"""Leakage, chronological isolation, deterministic reporting; offline data only."""

import copy
from decimal import Decimal as D

from multi_exchange_trader.research.data import seal, synthetic_dataset


def api():
    from multi_exchange_trader.research import evaluation

    return evaluation


def test_metrics_hand_drawdown_net_wins_and_uncertainty_zero_cases():
    m = api().metrics(
        ["10000", "11000", "9900", "10500"], [{"net_pnl": "5.0"}, {"net_pnl": "-2.0"}]
    )
    assert D(m["max_drawdown"]) == D("0.1") and m["trade_count"] == 2 and m["wins"] == 1
    assert D(m["win_rate"]) == D("0.5")
    assert D(m["win_rate_95_low"]) < D("0.5") < D(m["win_rate_95_high"])
    z = api().metrics(["10000"], [])
    assert z["trade_count"] == 0 and z["win_rate"] is None and z["win_rate_95_low"] is None


def test_splits_disjoint_final_holdout_and_training_selection_only():
    d = synthetic_dataset()
    r = api().evaluate(d)
    assert r["network_sandbox"] == "direct_function_not_OS_isolated"
    assert r["splits"] == {"train": [0, 144], "validation": [144, 192], "test": [192, 240]}
    assert [w["test_range"] for w in r["walk_forward"]] == [[120, 144], [144, 168], [168, 192]]
    assert all(
        w["train_range"][1] == w["test_range"][0] and w["test_range"][1] <= 192
        for w in r["walk_forward"]
    )
    changed = copy.deepcopy(d)
    for b in changed["bars"][192:]:
        for k in ("open", "high", "low", "close"):
            b[k] = str(D(b[k]) + D("2000.0"))
    changed = seal({k: v for k, v in changed.items() if k != "dataset_id"})
    after = api().evaluate(changed)
    assert (
        r["selected_config"] == after["selected_config"]
        and r["training_candidates"] == after["training_candidates"]
    )
    assert r["performance_status"] == "SYNTHETIC_ONLY_NOT_REAL_FUTURES_VALIDATION"
    assert r["portfolio_scope"] == "independent_single_instrument"
    assert api().evaluate(d) == r


def test_native_worker_has_no_outbound_and_produces_actual_report():
    from multi_exchange_trader.research.sandbox import sandbox_evaluate

    result = sandbox_evaluate(synthetic_dataset())
    assert result["engine_version"] == "1.231.0"
    assert result["test"]["native_fill_count"] == len(result["test"]["fills"])
    assert result["network_sandbox"] == "Linux_x86_64_socket_denied"
