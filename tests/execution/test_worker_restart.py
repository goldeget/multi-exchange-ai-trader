"""Synthetic app ledgers and real SDK Cache; no real node qualification."""
from __future__ import annotations

import asyncio
import importlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from test_worker import SCOPE, harness, stage

from multi_exchange_trader.execution import worker as worker_module
from multi_exchange_trader.execution.journal import CommandKind, CommandState
from multi_exchange_trader.execution.native_restart_store import (
    NativeRestartError,
    application_digest,
    decode_document,
)
from multi_exchange_trader.execution.runtime import RuntimeBlocked


def real_cache_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    h = harness(tmp_path / ("policy-fixture-" + uuid4().hex))
    h.worker.directory = tmp_path
    h.worker.lock_directory = tmp_path
    cache = importlib.import_module("nautilus_trader.cache.cache").Cache()
    h.assembly.strategy.id = "OwnedExecution-001"
    h.assembly.strategy.trader_id = "WORKER-001"
    h.assembly.node = SimpleNamespace(cache=cache)
    h.trace = []
    h.assembly.dispose_local = lambda: h.trace.append("dispose")
    h.assembly.require_quiesced = lambda: h.trace.append("quiesced")
    h.assembly.close_event_channel = lambda: h.trace.append("closed")
    h.worker._factory = None

    def build(**kwargs: Any) -> Any:
        assert kwargs["restart_safe"] is True
        return h.assembly

    monkeypatch.setattr(worker_module, "build_native_node", build)
    return h


async def test_fresh_active_marker_clean_seal_and_restored_connection_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = real_cache_boundary(tmp_path, monkeypatch)
    await h.worker.start_passive()
    marker = tmp_path / "native-restart.json"
    assert decode_document(marker.read_bytes())["state"] == "active"
    assert h.worker.native_restart_status.status == "FRESH_EMPTY_CACHE"
    lease, channel = h.worker._lock, h.assembly.channel
    record = await stage(h)
    h.worker.runtime.journal.claim(record.command_key)
    before = h.worker.runtime.reservation_totals(h.registry.resolve(h.request.candidates[0]).plan.scope)
    await h.worker.stop()
    assert h.trace == ["quiesced", "closed", "dispose"]
    assert decode_document(marker.read_bytes())["state"] == "sealed"
    new = real_cache_boundary(tmp_path, monkeypatch)
    await new.worker.start_passive()
    assert new.worker.native_restart_status.status == "RESTORED_LOCAL_CACHE"
    assert new.worker.native_restart_status.missing_owned_orders == 1
    assert new.worker._lock is not lease and new.worker._lock.acquisition_id > 0
    assert new.assembly.channel is not channel
    assert new.worker.runtime.journal.get("entry").state is CommandState.UNKNOWN
    totals = new.worker.runtime.reservation_totals(new.registry.resolve(new.request.candidates[0]).plan.scope)
    assert replace(totals, snapshot_id=before.snapshot_id) == before
    assert new.worker.runtime.store.get_position("position-1").reconciliation_required
    assert new.worker.runtime.projection("position-1").owned_quantity == 0
    def expired(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("restored connect must block before binding/network")
    monkeypatch.setattr(new.session, "require_execution_binding", expired)
    with pytest.raises(ValueError, match="MONITORING_"):
        await new.worker.connect_monitoring()
    assert new.worker._node_task is None
    assert not new.assembly.bridge._submissions
    await new.worker.stop()


@pytest.mark.parametrize("failure", ["missing", "corrupt", "active", "stale", "install"])
async def test_invalid_generation_refuses_before_recovery_and_disposes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    h = real_cache_boundary(tmp_path, monkeypatch)
    await h.worker.start_passive()
    await stage(h)
    await h.worker.stop()
    marker = tmp_path / "native-restart.json"
    if failure == "missing":
        marker.unlink()
    elif failure == "corrupt":
        marker.write_bytes(b"{")
    elif failure == "active":
        from multi_exchange_trader.execution.native_restart import NativeRestartCoordinator
        coordinator = NativeRestartCoordinator(tmp_path, h.assembly, h.worker.scope,
            h.worker.symbols, h.worker.trader_id)
        coordinator.store.mark_active(coordinator.identity, coordinator.store.load(coordinator.identity))
    elif failure == "stale":
        # A new durable handoff after the seal makes the sealed digest stale.
        from multi_exchange_trader.execution.journal import CommandJournal, ExecutionScope
        with CommandJournal(tmp_path / "execution.sqlite3", ExecutionScope(SCOPE.account_id, "DEMO")) as journal:
            journal.claim("entry")
    else:
        import multi_exchange_trader.execution.native_restart as restart
        monkeypatch.setattr(restart, "install_native_cache", lambda *_: (_ for _ in ()).throw(NativeRestartError()))
    before = application_digest(tmp_path)
    new = real_cache_boundary(tmp_path, monkeypatch)
    with pytest.raises(NativeRestartError, match="^NATIVE_RESTART_BLOCKED$"):
        await new.worker.start_passive()
    assert application_digest(tmp_path) == before
    assert new.worker._runtime is None and new.worker._stopped
    assert new.trace == ["dispose"]
    assert new.worker.native_restart_status.status == "BLOCKED"


async def test_stop_drains_posted_callbacks_and_transport_loss_before_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = real_cache_boundary(tmp_path, monkeypatch)
    await h.worker.start_passive()
    await stage(h)
    h.worker.runtime.journal.claim("entry")
    import multi_exchange_trader.execution.native_restart as restart
    original = restart.capture_native_cache

    def capture(*args: Any, **kwargs: Any) -> Any:
        assert h.trace == ["quiesced", "closed", "callback"]
        assert h.worker.runtime.journal.get("entry").state is CommandState.UNKNOWN
        assert h.worker._lock.acquired and h.worker._stopping
        return original(*args, **kwargs)

    monkeypatch.setattr(restart, "capture_native_cache", capture)
    asyncio.get_running_loop().call_soon(h.trace.append, "callback")
    await h.worker.stop()
    assert decode_document((tmp_path / "native-restart.json").read_bytes())["state"] == "sealed"


@pytest.mark.parametrize("failure", ["quiescence", "capture"])
async def test_stop_failure_never_publishes_seal(tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    h = real_cache_boundary(tmp_path, monkeypatch)
    await h.worker.start_passive()
    if failure == "quiescence":
        def fail() -> None:
            raise RuntimeError("test quiescence")
        h.assembly.require_quiesced = fail
    else:
        import multi_exchange_trader.execution.native_restart as restart
        monkeypatch.setattr(restart, "capture_native_cache", lambda *a, **k: (_ for _ in ()).throw(NativeRestartError()))
    with pytest.raises((RuntimeError, NativeRestartError)):
        await h.worker.stop()
    assert decode_document((tmp_path / "native-restart.json").read_bytes())["state"] == "active"
    if failure == "quiescence":
        assert h.worker._lock.acquired and not h.worker._stopped
        h.assembly.require_quiesced = lambda: None
        await h.worker.stop()


@pytest.mark.parametrize("kind", [CommandKind.ENTRY, CommandKind.CANCEL, CommandKind.LEVERAGE])
@pytest.mark.parametrize("unknown", [False, True])
async def test_sending_and_unknown_recovery_never_resends_or_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: CommandKind, unknown: bool,
) -> None:
    h = real_cache_boundary(tmp_path, monkeypatch)
    await h.worker.start_passive()
    await stage(h)
    journal = h.worker.runtime.journal
    reservation = journal.get("entry").reservation
    key = "entry" if kind is CommandKind.ENTRY else kind.value.lower()
    if kind is not CommandKind.ENTRY:
        journal.register(key, kind, {"entry_key": "entry"})
    if unknown:
        journal.claim(key)
        journal.mark_unknown(key, "SYNTHETIC_UNCERTAINTY")
    await h.worker.stop()
    if not unknown:
        # Synthetic durable fixture: a matching sealed snapshot with SENDING,
        # exercising startup recovery independently of clean-stop transport loss.
        from multi_exchange_trader.execution.journal import CommandJournal, ExecutionScope
        from multi_exchange_trader.execution.native_restart import NativeRestartCoordinator
        coordinator = NativeRestartCoordinator(tmp_path, h.assembly, h.worker.scope,
            h.worker.symbols, h.worker.trader_id)
        generation = coordinator.store.load(coordinator.identity)
        coordinator.store.mark_active(coordinator.identity, generation)
        with CommandJournal(tmp_path / "execution.sqlite3", ExecutionScope(SCOPE.account_id, "DEMO")) as opened:
            opened.claim(key)
            assert opened.get(key).state is CommandState.SENDING
        coordinator.store.seal(coordinator.identity, generation.payload)
    new = real_cache_boundary(tmp_path, monkeypatch)
    await new.worker.start_passive()
    record = new.worker.runtime.journal.get(key)
    assert record.state is CommandState.UNKNOWN and not record.terminal_confirmed
    assert new.worker.runtime.journal.get("entry").reservation == reservation
    assert not new.assembly.strategy.commands and not new.assembly.bridge._submissions
    assert new.worker.runtime.projection("position-1").owned_quantity == 0
    assert new.book.read()["desired_state"] == "STOPPED"
    with pytest.raises(RuntimeBlocked, match="QUALIFICATION"):
        new.worker.runtime.start()
    await new.worker.stop()


def test_restart_safe_config_disables_cache_load_and_reconciliation_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution import native_node
    from multi_exchange_trader.execution.bybit_native import build_demo_exec_config
    modules = {key: importlib.import_module(path) for key, path in {
        "config": "nautilus_trader.config", "bybit": "nautilus_trader.adapters.bybit.config",
        "identifiers": "nautilus_trader.model.identifiers", "native": "nautilus_trader.core.nautilus_pyo3",
        "structs": "msgspec.structs",
    }.items()}
    monkeypatch.setattr(native_node, "_native_modules", lambda: modules)
    config = build_demo_exec_config(api_key="fixture", api_secret="fixture")
    for restart_safe in (False, True):
        node = native_node.build_node_config(config, symbols=("BTCUSDT",),
            trader_id="WORKER-001", restart_safe=restart_safe)
        assert node.exec_engine.load_cache is (not restart_safe)
        assert node.exec_engine.reconciliation is (not restart_safe)
        assert node.timeout_post_stop == 10.0
    monitor = native_node.build_node_config(config, symbols=("BTCUSDT",),
        trader_id="WORKER-001", restart_safe=True, monitor_only=True)
    assert monitor.timeout_post_stop == 0.05
    assert monitor.exec_engine.reconciliation is False


def test_history_order_evidence_allows_exact_client_only_lookup(tmp_path: Path) -> None:
    from test_history_store import SCOPE, order, snapshot

    from multi_exchange_trader.execution.history_store import HistoryStore
    with HistoryStore(tmp_path / "history.sqlite3", SCOPE) as store:
        store.ingest(snapshot(orders=(order(), order(venue_order_id="other", client_order_id="other"))))
        before = store._db.total_changes
        evidence = store.order_evidence(client_order_id="c1", venue_order_ids=())
        assert [item.record.venue_order_id for item in evidence] == ["o1"]
        assert store.order_evidence(client_order_id="absent", venue_order_ids=()) == ()
        assert store._db.total_changes == before


def add_original_native_order(h: Any, record: Any, state: str = "ACCEPTED") -> Any:
    from test_native_restart_codec import module
    request = h.worker.runtime._request(record)
    ids, enums, objects = module("model.identifiers"), module("model.enums"), module("model.objects")
    uuid, events = module("core.uuid").UUID4, module("model.events.order")
    instrument_type = module("model.instruments").CryptoPerpetual
    template = module("test_kit.providers").TestInstrumentProvider.xrpusdt_linear_bybit()
    fields = template.to_dict(template)
    fields.update(id=request.owned.instrument_id, raw_symbol="BTCUSDT", base_currency="BTC")
    instrument = instrument_type.from_dict(fields)
    native = module("model.orders").MarketOrder(
        trader_id=ids.TraderId("WORKER-001"), strategy_id=ids.StrategyId("OwnedExecution-001"),
        instrument_id=instrument.id, client_order_id=ids.ClientOrderId(record.client_order_id),
        order_side=enums.OrderSide.BUY, quantity=objects.Quantity.from_str(str(request.owned.quantity)),
        init_id=uuid(), ts_init=1)
    common = {"trader_id": native.trader_id, "strategy_id": native.strategy_id,
        "instrument_id": native.instrument_id, "client_order_id": native.client_order_id,
        "account_id": ids.AccountId("BYBIT-UNIFIED"), "ts_event": 2, "ts_init": 2}
    if state != "INITIALIZED":
        native.apply(events.OrderSubmitted(**common, event_id=uuid()))
        native.apply(events.OrderAccepted(**common, venue_order_id=ids.VenueOrderId("native-venue"), event_id=uuid()))
    if state in ("PENDING_CANCEL", "CANCELED"):
        event_type = events.OrderPendingCancel if state == "PENDING_CANCEL" else events.OrderCanceled
        native.apply(event_type(**common, venue_order_id=native.venue_order_id, event_id=uuid()))
    cache = h.assembly.node.cache
    cache.add_instrument(instrument)
    cache.add_order(native, client_id=ids.ClientId("BYBIT"))
    cache.build_index()
    return native


@pytest.mark.parametrize("state", ["INITIALIZED", "ACCEPTED", "PENDING_CANCEL", "CANCELED"])
async def test_original_native_state_is_restored_without_callbacks_or_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    h = real_cache_boundary(tmp_path, monkeypatch)
    await h.worker.start_passive()
    record = await stage(h)
    native = add_original_native_order(h, record, state)
    h.worker.runtime.journal.claim("entry")
    original = native.to_dict()
    original_events = [e.to_dict(e) for e in native.events]
    await h.worker.stop()
    new = real_cache_boundary(tmp_path, monkeypatch)
    await new.worker.start_passive()
    restored = new.assembly.node.cache.order(native.client_order_id)
    assert restored.to_dict() == original
    assert [e.to_dict(e) for e in restored.events] == original_events
    assert new.worker.runtime.journal.get("entry").state is CommandState.UNKNOWN
    assert not new.worker.runtime.journal.get("entry").terminal_confirmed
    assert new.worker.runtime.projection("position-1").owned_quantity == 0
    assert len(new.worker.runtime.store.list_position_events("position-1")) == 1
    assert new.worker.callback_failures == 0 and not new.assembly.strategy.commands
    await new.worker.stop()


async def test_known_history_venue_never_allows_missing_native_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_history_store import order, snapshot

    from multi_exchange_trader.execution.history_store import HistoryStore
    from multi_exchange_trader.execution.native_restart import NativeRestartCoordinator
    h = real_cache_boundary(tmp_path, monkeypatch)
    await h.worker.start_passive()
    record = await stage(h)
    request = h.worker.runtime.native_owned_requests()[0].request
    await h.worker.stop()
    coordinator = NativeRestartCoordinator(tmp_path, h.assembly, h.worker.scope,
        h.worker.symbols, h.worker.trader_id)
    generation = coordinator.store.load(coordinator.identity)
    coordinator.store.mark_active(coordinator.identity, generation)
    with HistoryStore(tmp_path / "history.sqlite3", SCOPE) as history:
        history.ingest(replace(snapshot(orders=(order(client_order_id=record.client_order_id,
            quantity=request.owned.quantity),)), scope=SCOPE))
    coordinator.store.seal(coordinator.identity, generation.payload)
    before = application_digest(tmp_path)
    new = real_cache_boundary(tmp_path, monkeypatch)
    with pytest.raises(NativeRestartError):
        await new.worker.start_passive()
    assert application_digest(tmp_path) == before and new.trace == ["dispose"]


@pytest.mark.parametrize("restored", [False, True])
async def test_target_instrument_inventory_must_be_empty_before_seal_consumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored: bool,
) -> None:
    from test_native_restart_codec import module
    if restored:
        h = real_cache_boundary(tmp_path, monkeypatch)
        await h.worker.start_passive()
        await h.worker.stop()
    h = real_cache_boundary(tmp_path, monkeypatch)
    instrument = module("test_kit.providers").TestInstrumentProvider.xrpusdt_linear_bybit()
    h.assembly.node.cache.add_instrument(instrument)
    marker = tmp_path / "native-restart.json"
    prior = marker.read_bytes() if marker.exists() else None
    with pytest.raises(NativeRestartError):
        await h.worker.start_passive()
    assert (marker.read_bytes() if marker.exists() else None) == prior
    assert h.trace == ["dispose"] and h.worker._runtime is None


@pytest.mark.parametrize("variant", ["recorded-conflict", "changed-client", "missing-client"])
async def test_learned_history_venue_closes_all_variants_before_capture_and_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str,
) -> None:
    from test_history_store import order, snapshot

    from multi_exchange_trader.execution.journal import CommandJournal, ExecutionScope
    from multi_exchange_trader.execution.native_restart import NativeRestartCoordinator
    h = real_cache_boundary(tmp_path, monkeypatch)
    await h.worker.start_passive()
    record = await stage(h)
    native = add_original_native_order(h, record)
    owned = h.worker.runtime._request(record).owned
    assert not h.worker.runtime.native_owned_requests()[0].venue_order_ids
    first = order(client_order_id=owned.client_order_id, venue_order_id="native-venue",
        quantity=owned.quantity)
    second = replace(first, client_order_id="" if variant == "missing-client" else "foreign-client",
        updated_ms=first.updated_ms if variant == "recorded-conflict" else first.updated_ms + 1)
    history = h.worker._history_store
    history.ingest(replace(snapshot(orders=(first,)), scope=SCOPE))
    history.ingest(replace(snapshot(snapshot_id="changed", orders=(second,)), scope=SCOPE))
    evidence = history.order_evidence(client_order_id=owned.client_order_id, venue_order_ids=())
    assert len(evidence) == 1
    assert bool(evidence[0].conflict_keys) == (variant == "recorded-conflict")
    assert len(history.order_evidence(client_order_id=owned.client_order_id,
        venue_order_ids=("native-venue",))) == 2
    marker = tmp_path / "native-restart.json"
    with pytest.raises(RuntimeBlocked, match="DURABLE_IMMUTABLE_MAPPING_REQUIRED"):
        await h.worker.stop()
    assert decode_document(marker.read_bytes())["state"] == "active"
    assert h.worker._stopped and "dispose" in h.trace

    # Synthetic matching sealed fixture retains an actual native order so only
    # the omitted restrictive history evidence can cause this startup refusal.
    coordinator = NativeRestartCoordinator(tmp_path, h.assembly, h.worker.scope,
        h.worker.symbols, h.worker.trader_id)
    from multi_exchange_trader.execution.native_recovery import OwnedCacheExpectation
    from multi_exchange_trader.execution.native_restart_codec import capture_native_cache
    request = h.assembly.bridge._requests[record.client_order_id]
    owner = OwnedCacheExpectation(request, "BYBIT-UNIFIED", "WORKER-001",
        "OwnedExecution-001", "native-venue", None)
    payload = capture_native_cache(h.assembly.node.cache, identity=coordinator.identity,
        owners=(owner,))
    assert str(native.client_order_id) == owned.client_order_id
    with CommandJournal(tmp_path / "execution.sqlite3", ExecutionScope(SCOPE.account_id, "DEMO")) as journal:
        journal.claim("entry")
        assert journal.get("entry").state is CommandState.SENDING
    coordinator.store.seal(coordinator.identity, payload)
    before = application_digest(tmp_path)
    sealed = marker.read_bytes()
    new = real_cache_boundary(tmp_path, monkeypatch)
    with pytest.raises(RuntimeBlocked, match="DURABLE_IMMUTABLE_MAPPING_REQUIRED"):
        await new.worker.start_passive()
    assert application_digest(tmp_path) == before and marker.read_bytes() == sealed
    assert new.worker._runtime is None and new.trace == ["dispose"]
    with CommandJournal(tmp_path / "execution.sqlite3", ExecutionScope(SCOPE.account_id, "DEMO")) as journal:
        assert journal.get("entry").state is CommandState.SENDING


@pytest.mark.parametrize("case", [
    "execution-mismatch", "execution-missing", "execution-conflict",
    "execution-learns-foreign-order", "order-learns-foreign-execution",
    "native-order-foreign", "native-order-blank",
    "native-execution-foreign", "native-execution-blank",
])
@pytest.mark.parametrize("phase", ["stop", "start"])
async def test_restrictive_history_closure_refuses_stop_and_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, phase: str,
) -> None:
    from test_history_store import execution, order, snapshot

    from multi_exchange_trader.execution.journal import CommandJournal, ExecutionScope
    from multi_exchange_trader.execution.native_recovery import OwnedCacheExpectation
    from multi_exchange_trader.execution.native_restart import NativeRestartCoordinator
    from multi_exchange_trader.execution.native_restart_codec import capture_native_cache
    h = real_cache_boundary(tmp_path, monkeypatch)
    await h.worker.start_passive()
    record = await stage(h)
    request = h.worker.runtime._request(record)
    native = None if case == "execution-missing" else add_original_native_order(h, record)
    venue = "history-venue" if case == "execution-mismatch" else "native-venue"
    fill = execution(client_order_id=record.client_order_id, venue_order_id=venue)
    order_row = order(client_order_id=record.client_order_id, venue_order_id=venue,
        quantity=request.owned.quantity)
    history = h.worker._history_store
    if case.startswith("native-"):
        client = "" if case.endswith("blank") else "foreign-client"
        rows = ({"orders": (replace(order_row, client_order_id=client),)}
            if "order" in case else {"executions": (replace(fill, client_order_id=client),)})
        history.ingest(replace(snapshot(**rows), scope=SCOPE))
    else:
        rows = {"orders": (order_row,)} if case.startswith("order-") else {"executions": (fill,)}
        history.ingest(replace(snapshot(**rows), scope=SCOPE))
        if case == "execution-conflict":
            changed = replace(fill, price=fill.price + 1)
            history.ingest(replace(snapshot(snapshot_id="changed", executions=(changed,)), scope=SCOPE))
        elif case.endswith("foreign-order"):
            history.ingest(replace(snapshot(snapshot_id="changed",
                orders=(replace(order_row, client_order_id="foreign-client"),)), scope=SCOPE))
        elif case.endswith("foreign-execution"):
            history.ingest(replace(snapshot(snapshot_id="changed",
                executions=(replace(fill, client_order_id="foreign-client"),)), scope=SCOPE))
    marker = tmp_path / "native-restart.json"
    if phase == "stop":
        with pytest.raises((RuntimeBlocked, NativeRestartError)):
            await h.worker.stop()
        assert decode_document(marker.read_bytes())["state"] == "active"
    else:
        try:
            await h.worker.stop()
        except (RuntimeBlocked, NativeRestartError):
            pass  # Startup fixture is independent of clean-stop refusal.
    assert h.worker._stopped and "dispose" in h.trace

    # Synthetic, digest-matching seal isolates ownership validation from stale
    # digest and missing-cache blockers. No events are synthesized by recovery.
    coordinator = NativeRestartCoordinator(tmp_path, h.assembly, h.worker.scope,
        h.worker.symbols, h.worker.trader_id)
    if decode_document(marker.read_bytes())["state"] == "sealed":
        coordinator.store.mark_active(coordinator.identity, coordinator.store.load(coordinator.identity))
    owner = OwnedCacheExpectation(request, "BYBIT-UNIFIED", "WORKER-001",
        "OwnedExecution-001", None, None)
    payload = capture_native_cache(h.assembly.node.cache, identity=coordinator.identity,
        owners=(owner,), allowed_missing_ids=frozenset({record.client_order_id}))
    assert len(payload["orders"]) == (0 if native is None else 1)
    with CommandJournal(tmp_path / "execution.sqlite3", ExecutionScope(SCOPE.account_id, "DEMO")) as journal:
        for kind in (CommandKind.CANCEL, CommandKind.LEVERAGE):
            journal.register(kind.value.lower(), kind, {"entry_key": "entry"})
        for key in ("entry", "cancel", "leverage"):
            journal.claim(key)
        before_commands = journal.list_commands()
    coordinator.store.seal(coordinator.identity, payload)
    before, sealed = application_digest(tmp_path), marker.read_bytes()
    new = real_cache_boundary(tmp_path, monkeypatch)
    with pytest.raises((RuntimeBlocked, NativeRestartError)):
        await new.worker.start_passive()
    assert application_digest(tmp_path) == before and marker.read_bytes() == sealed
    assert new.worker._runtime is None and new.trace == ["dispose"]
    assert new.assembly.node.cache.orders() == []
    assert not new.assembly.bridge._submissions and not new.assembly.strategy.commands
    with CommandJournal(tmp_path / "execution.sqlite3", ExecutionScope(SCOPE.account_id, "DEMO")) as journal:
        assert journal.list_commands() == before_commands


async def test_matching_uncredited_execution_identity_preserves_original_native_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_history_store import execution, snapshot
    h = real_cache_boundary(tmp_path, monkeypatch)
    await h.worker.start_passive()
    record = await stage(h)
    native = add_original_native_order(h, record)
    original = [event.to_dict(event) for event in native.events]
    h.worker._history_store.ingest(replace(snapshot(executions=(execution(
        client_order_id=record.client_order_id, venue_order_id="native-venue"),)), scope=SCOPE))
    before = application_digest(tmp_path)
    assert h.worker.runtime.native_owned_requests()[0].venue_order_ids == frozenset({"native-venue"})
    assert application_digest(tmp_path) == before
    h.worker.runtime.journal.claim("entry")
    reservation = h.worker.runtime.journal.get("entry").reservation
    await h.worker.stop()
    new = real_cache_boundary(tmp_path, monkeypatch)
    await new.worker.start_passive()
    restored = new.assembly.node.cache.order(native.client_order_id)
    assert [event.to_dict(event) for event in restored.events] == original
    assert new.worker.runtime.journal.get("entry").state is CommandState.UNKNOWN
    assert new.worker.runtime.journal.get("entry").reservation == reservation
    assert new.worker.runtime.projection("position-1").owned_quantity == 0
    assert len(new.worker.runtime.store.list_position_events("position-1")) == 1
    assert not new.assembly.strategy.commands and not new.assembly.bridge._submissions
    await new.worker.stop()
