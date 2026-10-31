"""Local synthetic SQLite transactions/restart; no live market data."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest


def store(path):
    from multi_exchange_trader.paper.store import PaperStore

    return PaperStore(path)


def tick(price="61000.0", sequence=1):
    return {
        "kind": "tick",
        "instrument": "BTC-PAPER",
        "price": price,
        "sequence": sequence,
        "timestamp": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        "source": "LOCAL_SCENARIO_V1",
    }


def test_seed_once_idempotency_conflict_restart_and_reconciliation(tmp_path):
    path = tmp_path / "paper.sqlite3"
    s = store(path)
    a = s.command("lesson", "create", {"kind": "create"})
    assert a["wallet_balance"] == "10000.00000000"
    assert s.command("lesson", "create", {"kind": "create"}) == a
    body = {
        "kind": "enter",
        "instrument": "BTC-PAPER",
        "side": "long",
        "quantity": "0.100",
        "leverage": 10,
    }
    b = s.command("lesson", "entry", body)
    assert b["wallet_balance"] == "9997.59999600"
    assert store(path).state("lesson") == b
    assert store(path).command("lesson", "entry", body) == b
    with pytest.raises(ValueError, match="idempotency"):
        s.command("lesson", "entry", body | {"quantity": "0.200"})
    assert len(s.audit("lesson")["events"]) == 2
    assert s.audit("lesson")["funding_entries"] == 1


def test_sql_failure_rolls_back_native_candidate_without_duplicate_fills(tmp_path):
    path = tmp_path / "paper.sqlite3"
    s = store(path)
    a = s.command("lesson", "seed", {"kind": "create"})
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TRIGGER inject_failure BEFORE INSERT ON fills BEGIN SELECT RAISE(ABORT,'injected failure'); END;"
        )
    body = {
        "kind": "enter",
        "instrument": "BTC-PAPER",
        "side": "long",
        "quantity": "0.100",
        "leverage": 10,
    }
    with pytest.raises(ValueError, match="storage"):
        s.command("lesson", "entry", body)
    assert store(path).state("lesson") == a
    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER inject_failure")
    b = s.command("lesson", "entry", body)
    assert len(b["fills"]) == 1
    assert len(s.audit("lesson")["events"]) == 2


def test_concurrent_same_key_seed_and_entry_only_once(tmp_path):
    path = tmp_path / "paper.sqlite3"
    s = store(path)
    s.command("lesson", "seed", {"kind": "create"})
    body = {
        "kind": "enter",
        "instrument": "BTC-PAPER",
        "side": "short",
        "quantity": "0.100",
        "leverage": 10,
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: s.command("lesson", "same", body), range(4)))
    assert all(r == results[0] for r in results)
    assert len(results[0]["fills"]) == 1
    assert len(s.audit("lesson")["events"]) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"sequence": 0},
        {"sequence": True},
        {"sequence": 2},
        {"price": 1.1},
        {"price": "NaN"},
        {"timestamp": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
        {"timestamp": (datetime.now(UTC) - timedelta(hours=1)).isoformat()},
        {"source": "BINANCE"},
        {"timestamp": "2026-10-31"},
    ],
)
def test_invalid_tick_does_not_change_ledger(tmp_path, change):
    s = store(tmp_path / "paper.sqlite3")
    a = s.command("lesson", "seed", {"kind": "create"})
    with pytest.raises(ValueError):
        s.command("lesson", "tick", tick() | change)
    assert s.state("lesson") == a


def test_duplicate_reverse_tick_protection_and_replay_after_restart(tmp_path):
    path = tmp_path / "paper.sqlite3"
    s = store(path)
    s.command("lesson", "seed", {"kind": "create"})
    s.command(
        "lesson",
        "entry",
        {
            "kind": "enter",
            "instrument": "BTC-PAPER",
            "side": "long",
            "quantity": "0.100",
            "leverage": 10,
        },
    )
    s.command(
        "lesson",
        "protect",
        {"kind": "protect", "instrument": "BTC-PAPER", "sl": "59000.0", "tp": "61000.0"},
    )
    t = tick("61500.0")
    closed = s.command("lesson", "tick", t)
    assert len(closed["fills"]) == 2 and closed["positions"] == []
    assert store(path).state("lesson") == closed
    with pytest.raises(ValueError):
        store(path).command("lesson", "duplicate", t)
    assert store(path).command("lesson", "tick", t) == closed


def test_audit_append_only_unknown_schema_and_corruption_fail_closed(tmp_path):
    path = tmp_path / "paper.sqlite3"
    s = store(path)
    s.command("lesson", "seed", {"kind": "create"})
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.DatabaseError):
            db.execute("DELETE FROM events")
        db.execute("PRAGMA user_version=999")
    with pytest.raises(ValueError, match="schema"):
        store(path)
    bad = tmp_path / "broken.sqlite3"
    bad.write_bytes(b"not a sqlite database")
    with pytest.raises(ValueError, match="storage"):
        store(bad)


def test_snapshot_mismatch_fails_closed(tmp_path):
    path = tmp_path / "paper.sqlite3"
    s = store(path)
    s.command("lesson", "seed", {"kind": "create"})
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE scenarios SET snapshot=? WHERE id='lesson'",
            (json.dumps({"wallet_balance": "9999999"}),),
        )
    with pytest.raises(ValueError, match="reconciliation"):
        store(path).state("lesson")


def test_native_worker_os_network_denial_and_real_engine_result():
    import subprocess
    import sys

    from multi_exchange_trader.paper.sandbox import sandbox_replay

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "from multi_exchange_trader.paper.sandbox import deny_network;import socket;deny_network();socket.socket(socket.AF_INET,socket.SOCK_STREAM)",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode != 0 and "Operation not permitted" in probe.stderr
    state = sandbox_replay(
        [
            {
                "kind": "enter",
                "instrument": "BTC-PAPER",
                "side": "long",
                "quantity": "0.100",
                "leverage": 10,
            }
        ]
    )
    assert state["wallet_balance"] == "9997.59999600" and len(state["fills"]) == 1


def test_native_order_state_audit_is_append_only_and_reconciled(tmp_path):
    path = tmp_path / "orders.sqlite3"
    s = store(path)
    s.command("lesson", "seed", {"kind": "create"})
    s.command(
        "lesson",
        "entry",
        {
            "kind": "enter",
            "instrument": "BTC-PAPER",
            "side": "long",
            "quantity": "0.100",
            "leverage": 10,
        },
    )
    s.command(
        "lesson",
        "protect",
        {"kind": "protect", "instrument": "BTC-PAPER", "sl": "59000.0", "tp": "61000.0"},
    )
    s.command(
        "lesson", "cancel", {"kind": "protect", "instrument": "BTC-PAPER", "sl": None, "tp": None}
    )
    audit = s.audit("lesson")
    states = [json.loads(r["payload"])["order"]["status"] for r in audit["orders"]]
    assert "ACCEPTED" in states and "CANCELED" in states and "FILLED" in states
    with sqlite3.connect(path) as db, pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE orders SET payload='{}'")
    assert store(path).state("lesson")["wallet_balance"] == "9997.59999600"
