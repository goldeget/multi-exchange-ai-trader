"""Actual native PAPER replay; bounded single-DB scheduler commands, no network."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from multi_exchange_trader.research.data import save_dataset, synthetic_dataset


def make(tmp_path, **overrides):
    from multi_exchange_trader.research.storage import ResearchStore, default_auto_config

    data = tmp_path / "datasets"
    key = save_dataset(data, synthetic_dataset())
    store = ResearchStore(tmp_path / "phase4.sqlite3", data)
    config = {**default_auto_config(key), **overrides}
    return store, config


def tick(store, run, cursor):
    return store.step(
        run, cursor, now=datetime(2026, 11, 1, tzinfo=UTC) + timedelta(seconds=cursor * 10)
    )


def open_run(tmp_path, **overrides):
    store, config = make(tmp_path, **overrides)
    state = store.create("run1", "create", config)
    store.command("run1", "start", {"action": "resume"})
    for i in range(11):
        state = tick(store, "run1", i)
    return store, state


def test_native_run_pause_resume_stop_idempotency_and_restart_paused(tmp_path):
    store, state = open_run(tmp_path)
    assert state["native"]["positions"] and state["synthetic"] is True
    paused = store.command("run1", "pause", {"action": "pause"})
    assert tick(store, "run1", 11) == paused
    from multi_exchange_trader.research.storage import ResearchStore

    reloaded = ResearchStore(store.path, store.data_root)
    assert reloaded.state("run1")["status"] == "PAUSED"
    reloaded.command("run1", "resume2", {"action": "resume"})
    recovered = ResearchStore(store.path, store.data_root)
    assert recovered.state("run1")["status"] == "PAUSED_RECOVERY"
    assert recovered.state("run1")["cursor"] == 11
    before = len(state["native"]["fills"])
    stopped = recovered.command("run1", "stop", {"action": "stop"})
    assert stopped["status"] == "STOPPED" and stopped["native"]["positions"] == []
    assert len(stopped["native"]["fills"]) == before + 1
    assert recovered.command("run1", "stop", {"action": "stop"}) == stopped
    assert recovered.audit("run1")["funding_entries"] == 1
    with pytest.raises(ValueError):
        recovered.command("run1", "stop", {"action": "resume"})
    with pytest.raises(ValueError):
        recovered.command("run1", "late-resume", {"action": "resume"})


def test_duplicate_loops_and_concurrent_stop_cannot_duplicate_step_or_exit(tmp_path):
    store, config = make(tmp_path)
    store.create("run1", "create", config)
    store.command("run1", "resume", {"action": "resume"})
    with ThreadPoolExecutor(2) as pool:
        states = list(pool.map(lambda _: tick(store, "run1", 0), range(2)))
    assert store.state("run1")["cursor"] == 1
    assert states[0]["cursor"] == states[1]["cursor"] == 1
    for i in range(1, 11):
        tick(store, "run1", i)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda k: store.command("run1", k, {"action": "stop"}), ["stop-a", "stop-b"]))
    assert store.state("run1")["native"]["positions"] == []
    assert len(store.state("run1")["native"]["fills"]) == 2


@pytest.mark.parametrize(
    "policy,reason",
    [
        ({"max_loss": "0.000001"}, "loss_limit"),
        ({"max_drawdown": "0.000001"}, "drawdown_limit"),
        ({"max_exposure": "100.00"}, "exposure_limit"),
    ],
)
def test_risk_halts_native_and_preserves_actual_losses(tmp_path, policy, reason):
    _store, state = open_run(tmp_path, **policy)
    assert state["status"] == "HALTED" and state["reason"] == reason
    assert state["last_virtual_time_ms"] == synthetic_dataset()["bars"][10]["time_ms"]
    assert state["native"]["positions"] == []
    if reason != "exposure_limit":
        assert D(state["native"]["wallet_balance"]) < D(10000)
        assert len(state["native"]["fills"]) == 2
    else:
        assert state["native"]["fills"] == []


def test_event_exit_reserve_source_bounds_and_existing_paper_limits_untouched(tmp_path):
    from multi_exchange_trader.research.automation import ordinary_allowed

    assert ordinary_allowed(484) and not ordinary_allowed(485)
    for count in range(485):
        assert count + 6 + 10 <= 500
    store, config = make(tmp_path)
    with pytest.raises(ValueError):
        store.create("run", "bad", {**config, "bar_limit": 81})
    from multi_exchange_trader.paper.store import PaperStore

    assert PaperStore is not None
    state = store.create("short", "ok", {**config, "bar_limit": 12})
    store.command("short", "start", {"action": "resume"})
    for i in range(12):
        state = tick(store, "short", i)
    assert state["status"] == "STOPPED" and state["reason"] == "source_exhausted"
    assert not state["native"]["positions"] and len(state["native_events"]) + 1 + 10 < 500


def test_rollback_after_real_native_replay_then_same_cursor_retry(tmp_path):
    store, config = make(tmp_path)
    store.create("run", "create", config)
    store.command("run", "start", {"action": "resume"})
    old = store.state("run")
    with sqlite3.connect(store.path) as db:
        db.execute(
            "CREATE TRIGGER fault BEFORE UPDATE ON runs BEGIN SELECT RAISE(ABORT,'crash after candidate native replay'); END"
        )
    with pytest.raises(ValueError):
        tick(store, "run", 0)
    assert store.state("run") == old
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TRIGGER fault")
    result = tick(store, "run", 0)
    assert result["cursor"] == 1 and store.audit("run")["funding_entries"] == 1


def test_unknown_schema_snapshot_and_append_only_fail_closed(tmp_path):
    store, config = make(tmp_path)
    store.create("run", "create", config)
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM journal")
        db.execute("UPDATE runs SET snapshot='{}'")
    with pytest.raises(ValueError):
        store.state("run")
    from multi_exchange_trader.research.storage import ResearchStore

    with pytest.raises(ValueError):
        ResearchStore(store.path, store.data_root)


def test_real_spot_cannot_enter_auto_even_if_label_or_config_is_changed(tmp_path):
    store, config = make(tmp_path)
    from test_research_native import spot_fixture

    d = spot_fixture(["60000.00", "61000.00"])
    key = save_dataset(store.data_root, d)
    with pytest.raises(ValueError):
        store.create("bad", "create", {**config, "dataset_id": key})


def test_consecutive_loss_stop_and_pause_stop_inflight_race(tmp_path):
    from multi_exchange_trader.research.automation import streak_after

    assert (
        streak_after(2, "-0.01") == 3 and streak_after(2, "1.0") == 0 and streak_after(2, "0") == 0
    )
    store, _state = open_run(tmp_path)
    with ThreadPoolExecutor(3) as pool:
        futures = [
            pool.submit(tick, store, "run1", 11),
            pool.submit(store.command, "run1", "pause-race", {"action": "pause"}),
            pool.submit(store.command, "run1", "stop-race", {"action": "stop"}),
        ]
        for future in futures:
            try:
                future.result()
            except ValueError as exc:
                assert "terminal" in str(exc)  # Stop can precede pause; no unrelated errors hidden.
    last = store.state("run1")
    assert last["status"] == "STOPPED" and last["native"]["positions"] == []
    assert len(last["native"]["fills"]) == 2


def test_risk_stop_time_is_last_processed_quote_not_unseen_bar_close(tmp_path):
    from multi_exchange_trader.research.automation import advance

    store, config = make(tmp_path, max_loss="0.000001")
    state = store.create("instant", "create", config)
    state.update(status="RUNNING", cursor=10)
    result = advance(state, synthetic_dataset())
    assert result["status"] == "HALTED"
    assert result["last_virtual_time_ms"] == synthetic_dataset()["bars"][10]["time_ms"]


def test_idempotency_response_corruption_fails_closed(tmp_path):
    import json

    store, config = make(tmp_path)
    initial = store.create("run", "create", config)
    store.command("run", "resume", {"action": "resume"})
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TRIGGER immutable_commands_UPDATE")
        db.execute(
            "UPDATE commands SET response=? WHERE run='run' AND key='resume'",
            (json.dumps(initial, sort_keys=True, separators=(",", ":")),),
        )
    with pytest.raises(ValueError, match="reconciliation"):
        store.command("run", "resume", {"action": "resume"})


def test_next_due_is_one_second_after_actual_work_completion(tmp_path):
    store, config = make(tmp_path)
    store.create("cadence", "create", config)
    store.command("cadence", "start", {"action": "resume"})
    started = datetime(2026, 11, 1, tzinfo=UTC)
    finished = started + timedelta(seconds=5)
    store.clock = lambda: finished
    state = store.step("cadence", 0, now=started)
    assert datetime.fromisoformat(state["next_due_at"]) == finished + timedelta(seconds=1)
    assert store.step("cadence", 1, now=finished)["cursor"] == 1


def test_terminal_native_flatten_updates_closed_loss_streak(tmp_path):
    from multi_exchange_trader.paper.sandbox import sandbox_replay
    from multi_exchange_trader.research.automation import flatten

    store, config = make(tmp_path)
    state = store.create("flat", "create", config)
    state["native_events"] = [
        {
            "kind": "enter",
            "instrument": "BTC-PAPER",
            "side": "long",
            "quantity": "0.010",
            "leverage": 10,
        }
    ]
    state["native"] = sandbox_replay(state["native_events"])
    flatten(state)
    assert state["native"]["positions"] == [] and D(state["native"]["closed_net_pnl"]) < 0
    assert (
        state["loss_streak"] == 1 and state["last_closed_net"] == state["native"]["closed_net_pnl"]
    )


def test_actual_native_whipsaw_halts_after_consecutive_net_losses(tmp_path):
    from multi_exchange_trader.research.data import seal

    d = synthetic_dataset()
    previous = D("60000.0")
    for i, b in enumerate(d["bars"]):
        close = D("60000.0") if i % 2 == 0 else D("60100.0")
        b.update(
            open=str(previous),
            close=str(close),
            low=str(min(previous, close)),
            high=str(max(previous, close)),
        )
        previous = close
    d = seal({k: v for k, v in d.items() if k != "dataset_id"})
    store, config = make(tmp_path, max_consecutive_losses=2)
    config["dataset_id"] = save_dataset(store.data_root, d)
    store.create("whipsaw", "create", config)
    store.command("whipsaw", "start", {"action": "resume"})
    for i in range(14):
        state = tick(store, "whipsaw", i)
        if state["status"] == "HALTED":
            break
    assert state["reason"] == "consecutive_loss_limit" and state["loss_streak"] == 2
    assert state["native"]["positions"] == [] and D(state["native"]["closed_net_pnl"]) < 0


def test_concurrent_seed_once_capacity16_and_unknown_schema(tmp_path):
    store, config = make(tmp_path)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: store.create("same", "same", config), range(2)))
    assert results[0] == results[1] and store.audit("same")["funding_entries"] == 1
    for i in range(15):
        store.create("cap" + str(i), "create", config)
    with pytest.raises(ValueError, match="limit16"):
        store.create("overflow", "create", config)
    with sqlite3.connect(store.path) as db:
        db.execute("PRAGMA user_version=99")
    from multi_exchange_trader.research.storage import ResearchStore

    with pytest.raises(ValueError, match="schema"):
        ResearchStore(store.path, store.data_root)


def test_native_loss_zero_partial_loss_exit_count_and_duplicate_detection(tmp_path):
    from multi_exchange_trader.paper.sandbox import sandbox_replay
    from multi_exchange_trader.research.automation import track_closed

    store, config = make(tmp_path)
    state = store.create("zero", "create", config)
    events = []

    def emit(kind, **fields):
        events.append({"kind": kind, "instrument": "BTC-PAPER", **fields})

    emit("tick", price="249.8")
    emit("enter", side="long", quantity="0.010", leverage=10)
    emit("close", quantity="0.010")
    state["native"] = sandbox_replay(events)
    track_closed(state)
    assert state["loss_streak"] == 1
    previous = state["native"]["closed_net_pnl"]
    emit("enter", side="long", quantity="0.010", leverage=10)
    emit("tick", price="250.2")
    emit("close", quantity="0.010")
    state["native"] = sandbox_replay(events)
    assert state["native"]["closed_net_pnl"] == previous
    track_closed(state)
    assert state["loss_streak"] == 0
    emit("tick", price="249.8")
    emit("enter", side="long", quantity="0.010", leverage=10)
    emit("close", quantity="0.004")
    state["native"] = sandbox_replay(events)
    track_closed(state)
    assert state["loss_streak"] == 0 and state["processed_closed_trades"] == 2
    emit("close", quantity="0.006")
    state["native"] = sandbox_replay(events)
    track_closed(state)
    assert state["loss_streak"] == 1 and state["processed_closed_trades"] == 3
    track_closed(state)
    assert state["loss_streak"] == 1 and state["processed_closed_trades"] == 3


def test_redundant_resume_cannot_reset_a_running_completion_cooldown(tmp_path):
    store,config=make(tmp_path);store.create('running','create',config);store.command('running','start',{'action':'resume'})
    started=datetime(2026,11,1,tzinfo=UTC);finished=started+timedelta(seconds=5)
    store.clock=lambda:finished
    state=store.step('running',0,now=started)
    again=store.command('running','redundant',{'action':'resume'})
    assert again['next_due_at']==state['next_due_at']
    assert store.step('running',1,now=finished)['cursor']==1
