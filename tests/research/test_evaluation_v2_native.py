"""Configured costs and time windows executed by the actual isolated native engine."""

from decimal import Decimal

import pytest

from multi_exchange_trader.research.data import checksum, save_dataset, synthetic_dataset
from multi_exchange_trader.research.storage import ResearchStore


def test_actual_native_settings_costs_and_durable_report_identity(tmp_path):
    dataset = synthetic_dataset()
    config = {
        "config_version": 2, "start_ms": dataset["start_ms"] + 20 * 60000,
        "end_ms": dataset["end_ms"], "fast": 3, "slow": 10,
        "quantity": "0.010", "fee_rate": "0.001", "slippage_ticks": 2, "leverage": 3,
        "initial_equity": "25000", "train_percent": 50, "validation_percent": 20,
    }
    root = tmp_path / "datasets"
    key = save_dataset(root, dataset)
    store = ResearchStore(tmp_path / "research.sqlite3", root)
    costly = store.evaluate("configured", key, config)
    assert costly["schema"] == "RESEARCH_REPORT_V2"
    assert costly["config_hash"] == checksum(config)
    assert costly["requested_range"] == [config["start_ms"], config["end_ms"]]
    assert costly["splits"]["train"][0] == 20
    assert costly["selected_config"]["fast"] == 3 and costly["selected_config"]["slow"] == 10
    assert costly["test"]["config"]["leverage"] == 3
    assert costly["test"]["initial_equity"] == "25000.00000000"
    assert costly["assumptions"]["seed"] == "25000.00000000"
    assert costly["splits"]["train"][1] == 130
    assert all(fold["test_range"][1] <= costly["splits"]["test"][0] for fold in costly["walk_forward"])
    assert costly["network_sandbox"] == "Linux_x86_64_socket_denied"
    assert Decimal(costly["buy_hold_test"]["fees"]) > 0
    for segment in (costly["validation"], costly["test"], costly["buy_hold_test"]):
        for fill in segment["fills"]:
            assert abs(Decimal(fill["fee"]) - Decimal(fill["quantity"]) * Decimal(fill["price"]) * Decimal("0.001")) <= Decimal("0.00000001")
    restarted = ResearchStore(store.path, store.data_root)
    assert restarted.evaluate("configured", key, config) == costly
    with pytest.raises(ValueError, match="idempotency"):
        restarted.evaluate("configured", key, {**config, "fee_rate": "0"})
    free = restarted.evaluate("no-cost", key, {**config, "fee_rate": "0", "slippage_ticks": 0})
    assert free["config_hash"] != costly["config_hash"]
    assert Decimal(free["buy_hold_test"]["fees"]) == 0
    assert Decimal(free["buy_hold_test"]["final_equity"]) > Decimal(costly["buy_hold_test"]["final_equity"])
