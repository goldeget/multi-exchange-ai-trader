"""Actual Linux native V2 execution, durable entry pause, leverage and recovery."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from multi_exchange_trader.paper.contract import reserve
from multi_exchange_trader.research.data import checksum, save_dataset, synthetic_dataset
from multi_exchange_trader.research.storage import ResearchStore, default_auto_config


def test_native_v2_leverage_pause_restart_and_reduce_only_stop(tmp_path):
    data = tmp_path / "datasets"
    dataset = synthetic_dataset()
    key = save_dataset(data, dataset)
    store = ResearchStore(tmp_path / "research.sqlite3", data)
    config = default_auto_config(key, version=2)
    config["bar_limit"] = 14
    created = store.create("version2", "create", config)
    store.command("version2", "start", {"action": "resume"})
    anchor = datetime.now(UTC) + timedelta(days=1)
    for cursor in range(11):
        state = store.step("version2", cursor, now=anchor + timedelta(seconds=cursor * 10))
    position = state["native"]["positions"][0]
    assert position["leverage"] == 3
    assert Decimal(position["reserved_margin"]) == reserve(
        Decimal(position["quantity"]), Decimal(position["entry_price"]), 3
    )
    store.command("version2", "pause-entry", {"action": "pause_entries"})
    entry_count = sum(event["kind"] == "enter" for event in state["native_events"])
    advanced = store.step("version2", 11, now=anchor + timedelta(minutes=5))
    assert advanced["cursor"] == 12 and advanced["status"] == "ENTRY_PAUSED"
    assert sum(e["kind"] == "enter" for e in advanced["native_events"]) == entry_count
    assert advanced["config_hash"] == created["config_hash"] == checksum(config)
    recovered = ResearchStore(store.path, store.data_root)
    assert recovered.state("version2")["status"] == "PAUSED_RECOVERY"
    resumed = recovered.command("version2", "resume-loop", {"action": "resume"})
    assert resumed["status"] == "ENTRY_PAUSED"
    resumed = recovered.command("version2", "resume-entry", {"action": "resume_entries"})
    assert resumed["status"] == "RUNNING"
    stopped = recovered.command("version2", "stop", {"action": "stop"})
    assert stopped["native"]["positions"] == [] and stopped["status"] == "STOPPED"
    assert recovered.command("version2", "stop", {"action": "stop"}) == stopped
    assert recovered.audit("version2")["funding_entries"] == 1


def test_v1_rejects_new_controls_and_retains_legacy_snapshot(tmp_path):
    data = tmp_path / "data"
    key = save_dataset(data, synthetic_dataset())
    store = ResearchStore(tmp_path / "legacy.sqlite3", data)
    state = store.create("old", "create", default_auto_config(key))
    assert "config_version" not in state and "config_hash" not in state
    store.command("old", "start", {"action": "resume"})
    with pytest.raises(ValueError, match="V2"):
        store.command("old", "pause-entry", {"action": "pause_entries"})
    assert store.state("old")["status"] == "RUNNING"
