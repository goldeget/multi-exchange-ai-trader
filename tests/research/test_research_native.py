"""Actual native spot/futures research execution over synthetic local candles."""

import copy
from decimal import Decimal as D

import pytest

from multi_exchange_trader.research.data import (
    HOUR,
    SCHEMA,
    SPOT_URL,
    seal,
    synthetic_dataset,
    validate_dataset,
)


def native():
    from multi_exchange_trader.research.native import run_segment

    return run_segment


def spot_fixture(prices):
    import hashlib
    import json

    start = 1767225600000
    rows = [
        [
            start + i * HOUR,
            str(p),
            str(p),
            str(p),
            str(p),
            "1.00",
            start + (i + 1) * HOUR - 1,
            "0",
            1,
            "0",
            "0",
            "0",
        ]
        for i, p in enumerate(prices)
    ]
    raw = json.dumps(rows)
    from multi_exchange_trader.research.data import rows_to_bars

    return validate_dataset(
        seal(
            {
                "schema": SCHEMA,
                "market": "spot",
                "symbol": "BTCUSDT",
                "synthetic": False,
                "interval_ms": HOUR,
                "start_ms": start,
                "end_ms": start + len(rows) * HOUR,
                "collected_at": "2026-02-01T00:00:00+00:00",
                "source": {
                    "url": SPOT_URL,
                    "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                    "http_status": 200,
                    "request": {
                        "symbol": "BTCUSDT",
                        "interval": "1h",
                        "startTime": start,
                        "endTime": start + len(rows) * HOUR - 1,
                        "limit": 1000,
                    },
                },
                "raw_body": raw,
                "bars": rows_to_bars(rows),
            }
        )
    )


def test_spot_native_buy_hold_exact_cash_inventory_fees_and_repeat():
    d = spot_fixture(["60000.00", "61000.00"])
    config = {"strategy": "BUY_HOLD_V1", "quantity": "0.100", "fast": 3, "slow": 10}
    r = native()(d, config, 0, 2)
    assert [f["price"] for f in r["fills"]] == ["60000.01", "60999.99"]
    assert r["final_equity"] == "10095.15800000" and r["fees"] == "4.84000000"
    assert r["account_type"] == "CASH" and r["inventory"] == "0.00000000"
    assert r["trades"][0]["net_pnl"] == "95.15800000"
    assert native()(d, config, 0, 2) == r


def test_next_open_not_signal_close_and_no_warmup_orders():
    d = spot_fixture(
        ["60000.00", "59000.00", "58000.00", "61000.00", "62000.00", "65000.00", "64000.00"]
    )
    config = {"strategy": "SMA_CROSS_V1", "quantity": "0.010", "fast": 2, "slow": 3}
    r = native()(d, config, 0, 7)
    assert r["fills"][0]["bar_index"] == 4
    assert r["fills"][0]["phase"] == "open"
    assert r["fills"][0]["price"] == "62000.01"
    assert all(f["bar_index"] >= 3 for f in r["fills"])


def test_future_mutation_cannot_change_prefix_decisions_or_fills():
    d = synthetic_dataset()
    changed = copy.deepcopy(d)
    for b in changed["bars"][100:]:
        for k in ("open", "high", "low", "close"):
            b[k] = str(D(b[k]) + D("1000.0"))
    changed = seal({k: v for k, v in changed.items() if k != "dataset_id"})
    c = {"strategy": "SMA_CROSS_V1", "quantity": "0.010", "fast": 3, "slow": 10}
    a = native()(d, c, 0, 120)
    b = native()(changed, c, 0, 120)
    assert [f for f in a["fills"] if f["bar_index"] < 100] == [
        f for f in b["fills"] if f["bar_index"] < 100
    ]
    assert a["market"] == "synthetic_perpetual" and a["synthetic"] is True


def test_futures_native_matches_existing_paper_engine_for_buy_hold():
    from multi_exchange_trader.paper.native import replay

    d = synthetic_dataset()
    c = {"strategy": "BUY_HOLD_V1", "quantity": "0.100", "fast": 3, "slow": 10}
    r = native()(d, c, 0, 1)
    p = d["bars"][0]
    events = [
        {"kind": "tick", "instrument": "BTC-PAPER", "price": p["open"]},
        {
            "kind": "enter",
            "instrument": "BTC-PAPER",
            "side": "long",
            "quantity": "0.100",
            "leverage": 10,
        },
    ]
    events += [
        {"kind": "tick", "instrument": "BTC-PAPER", "price": p[k]} for k in ("low", "high", "close")
    ]
    events += [{"kind": "close", "instrument": "BTC-PAPER", "quantity": "0.100"}]
    state = replay(events)
    assert r["final_equity"] == state["equity"] and r["fees"] == state["fees"]
    assert [f["price"] for f in r["fills"]] == [f["price"] for f in state["fills"]]


def test_spot_short_or_invalid_config_rejected_and_no_fake_portfolio():
    d = spot_fixture(["60000.00", "61000.00"])
    for c in [
        {"strategy": "LIVE", "quantity": "0.1", "fast": 3, "slow": 10},
        {"strategy": "SMA_CROSS_V1", "quantity": 0.1, "fast": 3, "slow": 10},
    ]:
        with pytest.raises((ValueError, TypeError)):
            native()(d, c, 0, 2)
    r = native()(d, {"strategy": "SMA_CROSS_V1", "quantity": "0.010", "fast": 3, "slow": 10}, 0, 2)
    assert r["fills"] == [] and r["final_equity"] == "10000.00000000"
    assert r["portfolio_scope"] == "independent_single_instrument"
