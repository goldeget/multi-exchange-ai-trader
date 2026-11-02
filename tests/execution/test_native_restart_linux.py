"""Exactly six actual pinned restart contracts under OS network isolation.

The worker tests take the production construction path: no assembly_factory,
builder replacement, run_async, connection or engine command/event injection.
Original synthetic native events seed real Cache objects solely as test inputs.
Synthetic session identities are private fixtures, never authentication evidence.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import json
import runpy
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_bybit_native_linux import isolated_native_runtime, module  # noqa: F401
from test_native_restart_codec import closed_fixture, fixture
from test_risk import NOW, inputs
from test_runtime import SCOPE, selection
from test_worker import stage
from worker_receipt_support import synthetic_session

from multi_exchange_trader.execution.journal import (
    CommandJournal,
    CommandKind,
    CommandState,
    ExecutionScope,
)
from multi_exchange_trader.execution.native_restart_codec import (
    capture_native_cache,
    install_native_cache,
    prepare_native_cache,
)
from multi_exchange_trader.execution.native_restart_store import (
    NativeRestartError,
    NativeRestartStore,
    application_digest,
    canonical_bytes,
    decode_document,
)
from multi_exchange_trader.execution.protection import ProtectionPolicy
from multi_exchange_trader.execution.risk import assess_entry
from multi_exchange_trader.execution.runtime import RuntimeBlocked
from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook
from multi_exchange_trader.execution.worker import (
    ExecutionWorker,
    RegisteredPlan,
    TrustedPlanRegistry,
)

ROOT = Path(__file__).resolve().parents[2]


def actual_worker(directory: Path, previous: Any = None) -> Any:
    """Build synthetic app inputs; leave the actual worker/native builder untouched."""
    directory.mkdir(parents=True, exist_ok=True)
    lock_directory = directory.parent / "leases"
    lock_directory.mkdir(mode=0o700, exist_ok=True)
    book = RuntimePolicyBook(directory / "runtime-policy.sqlite3")
    core = synthetic_session(SCOPE, NOW)
    # Grant only the synthetic fixture identity the permissions needed to build
    # an unconnected adapter. This does not authenticate a real account.
    core.permit_fake_monitoring()
    if previous is None:
        data = inputs()
        draft = book.read()["policy"]
        for key, value in asdict(data["policy"]).items():
            if key in draft:
                draft[key] = str(value) if isinstance(value, Decimal) else value
        draft.update(capital_usdt="1000", mode="FULL_AUTO", environment="DEMO",
            allowed_symbols=["BTCUSDT"], selected_symbols=["BTCUSDT"],
            allowed_strategies=["strategy-1"], selected_strategies=["strategy-1"])
        book.save(0, draft)
        data["policy"] = book.risk_policy(data["policy"].scope)
        plan = assess_entry(**data)
        request = selection()
        request = replace(request, policy=replace(request.policy, revision="1"),
            candidates=tuple(replace(c, policy_revision="1") for c in request.candidates))
        protection = ProtectionPolicy("1", 0, Decimal(90), Decimal(120), "MarkPrice")
        registry = TrustedPlanRegistry((RegisteredPlan("plan-1", request.candidates[0].strategy,
            plan, protection),))
    else:
        plan, request, registry = previous.plan, previous.request, previous.registry
    worker = ExecutionWorker(directory=directory, scope=SCOPE, session=core.session,
        binding=core.binding, symbols=("BTCUSDT",), trader_id="WORKER-001", policy_book=book,
        registry=registry, entry_risk=lambda _plan, _now, _reservations: plan,
        clock=lambda: core.wall[0], max_age=timedelta(seconds=10),
        lock_directory=lock_directory)
    assert worker._factory is None and worker._decoder is None
    return SimpleNamespace(worker=worker, core=core, session=core.session, book=book,
        request=request, registry=registry, plan=plan)


async def start(h: Any) -> None:
    await h.worker.start_passive()
    h.assembly = h.worker._assembly
    assert type(h.assembly.node) is module("live.node").TradingNode
    assert type(h.assembly.node.cache) is module("cache.cache").Cache
    assert type(h.assembly.strategy) is module("trading.strategy").Strategy
    assert type(h.assembly.execution_client) is module("adapters.bybit.execution").BybitExecutionClient
    assert h.assembly.strategy.cache is h.assembly.node.cache
    assert h.assembly.node.is_built() and not h.assembly.node.is_running()
    assert h.assembly.node.kernel.exec_engine.command_count == 0
    assert h.assembly.node.kernel.exec_engine.event_count == 0
    assert h.worker._node_task is None and h.core.reads == []


def no_effects(h: Any) -> None:
    kernel = h.assembly.node.kernel
    assert kernel.exec_engine.command_count == kernel.exec_engine.event_count == 0
    assert kernel.exec_engine.cmd_qsize() == kernel.exec_engine.evt_qsize() == 0
    assert kernel.exec_engine.check_disconnected() and kernel.data_engine.check_disconnected()
    assert h.worker.callback_failures == 0 and not h.assembly.bridge._submissions
    assert h.worker._node_task is None and not h.assembly.node.is_running()
    assert h.core.reads == [] and h.book.read()["desired_state"] == "STOPPED"


def seed_original_order(h: Any, record: Any) -> Any:
    """Use the actual assembly's factory and identities; events are fixture inputs."""
    request = h.worker.runtime._request(record)
    ids, enums, obj = module("model.identifiers"), module("model.enums"), module("model.objects")
    template = module("test_kit.providers").TestInstrumentProvider.xrpusdt_linear_bybit()
    fields = template.to_dict(template)
    fields.update(id=request.owned.instrument_id, raw_symbol="BTCUSDT", base_currency="BTC")
    instrument = module("model.instruments").CryptoPerpetual.from_dict(fields)
    arguments = {"instrument_id": instrument.id,
        "order_side": enums.OrderSide.BUY if request.owned.side == "BUY" else enums.OrderSide.SELL,
        "quantity": obj.Quantity.from_str(str(request.owned.quantity)),
        "client_order_id": ids.ClientOrderId(record.client_order_id),
        "reduce_only": request.owned.reduce_only}
    factory = h.assembly.strategy.order_factory
    if request.order_type == "MARKET":
        order = factory.market(**arguments)
    else:
        order = factory.limit(**arguments, price=obj.Price.from_str(str(request.limit_price)))
    assert order.trader_id == h.assembly.strategy.trader_id
    assert order.strategy_id == h.assembly.strategy.id
    events, uuid = module("model.events.order"), module("core.uuid").UUID4
    original = {"trader_id": order.trader_id, "strategy_id": order.strategy_id,
        "instrument_id": order.instrument_id, "client_order_id": order.client_order_id,
        "account_id": ids.AccountId(h.assembly.channel.native_account_id),
        "ts_event": order.ts_init + 1, "ts_init": order.ts_init + 1}
    order.apply(events.OrderSubmitted(**original, event_id=uuid()))
    order.apply(events.OrderAccepted(**{**original, "ts_event": order.ts_init + 2,
        "ts_init": order.ts_init + 2}, venue_order_id=ids.VenueOrderId("native-venue"), event_id=uuid()))
    cache = h.assembly.node.cache
    cache.add_instrument(instrument)
    cache.add_order(order, client_id=ids.ClientId("BYBIT"))
    cache.build_index()
    assert cache.check_integrity() is True
    return order


def seed_original_position(cache: Any, order: Any) -> Any:
    """Original fixture fill populates real native order/Position only, never runtime."""
    instrument = cache.instrument(order.instrument_id)
    ids, enums, obj = module("model.identifiers"), module("model.enums"), module("model.objects")
    quantity = obj.Quantity.from_str(format(order.quantity.as_decimal(),
        f".{instrument.size_precision}f"))
    assert quantity.as_decimal() == order.quantity.as_decimal()
    fill = module("model.events.order").OrderFilled(trader_id=order.trader_id,
        strategy_id=order.strategy_id, instrument_id=order.instrument_id,
        client_order_id=order.client_order_id, account_id=order.account_id,
        venue_order_id=order.venue_order_id, trade_id=ids.TradeId("original-worker-fill"),
        position_id=ids.PositionId(str(order.instrument_id) + "-" + str(order.strategy_id)),
        order_side=order.side, order_type=order.order_type, last_qty=quantity,
        last_px=obj.Price.from_str(format(Decimal(100), f".{instrument.price_precision}f")),
        currency=instrument.quote_currency, commission=obj.Money(0.01, instrument.quote_currency),
        liquidity_side=enums.LiquiditySide.TAKER, event_id=module("core.uuid").UUID4(),
        ts_event=order.ts_init + 3, ts_init=order.ts_init + 3)
    order.apply(fill)
    position = module("model.position").Position(instrument, fill)
    cache.update_order(order)
    cache.add_position(position, enums.OmsType.NETTING)
    cache.add_position_id(position.id, order.instrument_id.venue, order.client_order_id,
        order.strategy_id)
    cache.build_index()
    assert cache.check_integrity() is True
    assert cache.position_for_order(order.client_order_id) is position
    assert cache.position_id(order.client_order_id) == order.position_id == position.id
    return position


def complete_offline_ledgers(directory: Path) -> dict[str, Any]:
    tool = runpy.run_path(str(ROOT / "scripts/operations/state_snapshot.py"))
    (directory / "datasets").mkdir()
    for name, schema in tool["SCHEMAS"].items():
        with closing(sqlite3.connect(directory / name)) as db:
            for table, columns in schema.items():
                db.execute(f'CREATE TABLE "{table}" (' + ",".join(
                    '"' + column + '" TEXT' for column in columns.split()) + ")")
            if name in tool["METADATA"]:
                db.execute("PRAGMA user_version=1")
                db.execute("INSERT INTO metadata VALUES (?)", (json.dumps(tool["METADATA"][name]),))
            db.commit()
    with closing(sqlite3.connect(directory / "ai-dispatch.sqlite3")) as db:
        db.execute("PRAGMA user_version=1")
        db.execute("CREATE TABLE ai_dispatches(request_id TEXT PRIMARY KEY,binding_hash TEXT,"
            "maximum TEXT,status TEXT,failure TEXT,exact_cost TEXT,created_at TEXT,updated_at TEXT)")
        db.commit()
    return tool


def change_document(path: Path, change: Any) -> bytes:
    document = decode_document(path.read_bytes())
    change(document)
    document.pop("content_hash")
    document["content_hash"] = hashlib.sha256(canonical_bytes(document)).hexdigest()
    raw = canonical_bytes(document)
    path.write_bytes(raw)
    return raw


def assert_cache_roundtrip(source: Any) -> Any:
    payload = capture_native_cache(source.cache, identity=source.identity, owners=source.owners)
    prepared = prepare_native_cache(payload, identity=source.identity, owners=source.owners)
    target = module("cache.cache").Cache()
    result = install_native_cache(target, prepared)
    assert result.production_supported is False and result.missing_owned_orders == 0
    assert target.check_integrity() is True
    for original in source.cache.orders():
        restored = target.order(original.client_order_id)
        assert restored is not original and restored.to_dict() == original.to_dict()
        assert [e.to_dict(e) for e in restored.events] == [e.to_dict(e) for e in original.events]
        assert target.client_id(restored.client_order_id) == source.cache.client_id(original.client_order_id)
    return target


def reopened_native_lifetime() -> Any:
    """A genuine native FLAT->open reset is an unsupported test input."""
    source = closed_fixture()
    ids, enums, obj = module("model.identifiers"), module("model.enums"), module("model.objects")
    events, uuid = module("model.events.order"), module("core.uuid").UUID4
    order = module("model.orders").MarketOrder(trader_id=source.order.trader_id,
        strategy_id=source.order.strategy_id, instrument_id=source.order.instrument_id,
        client_order_id=ids.ClientOrderId("ec_" + "b" * 32), order_side=enums.OrderSide.BUY,
        quantity=obj.Quantity.from_str("10"), init_id=uuid(), ts_init=10)
    fields = {"trader_id": order.trader_id, "strategy_id": order.strategy_id,
        "instrument_id": order.instrument_id, "client_order_id": order.client_order_id,
        "account_id": source.position.account_id, "ts_event": 11, "ts_init": 11}
    order.apply(events.OrderSubmitted(**fields, event_id=uuid()))
    order.apply(events.OrderAccepted(**fields, venue_order_id=ids.VenueOrderId("reopened"),
        event_id=uuid()))
    fill = events.OrderFilled(**fields, venue_order_id=order.venue_order_id,
        trade_id=ids.TradeId("reopened-trade"), position_id=source.position.id,
        order_side=order.side, order_type=order.order_type, last_qty=order.quantity,
        last_px=obj.Price.from_str("0.4900"), currency=source.instrument.quote_currency,
        commission=obj.Money(0.01, source.instrument.quote_currency),
        liquidity_side=enums.LiquiditySide.TAKER, event_id=uuid())
    order.apply(fill)
    source.position.apply(fill)
    assert source.position.is_open and source.position.opening_order_id == order.client_order_id
    source.cache.add_order(order, position_id=source.position.id, client_id=ids.ClientId("BYBIT"))
    source.cache.update_position(source.position)
    source.cache.build_index()
    assert source.cache.check_integrity() is True
    first = source.owners[0]
    owned = replace(first.request.owned, client_order_id=str(order.client_order_id))
    source.owners += (replace(first, request=replace(first.request, owned=owned,
        order_type="MARKET", limit_price=None), venue_order_id="reopened"),)
    return source


def test_real_original_orders_position_and_all_cache_indexes() -> None:
    source = fixture("PARTIALLY_FILLED")
    target = assert_cache_roundtrip(source)
    order = target.order(source.order.client_order_id)
    position = target.position_for_order(order.client_order_id)
    assert type(position) is module("model.position").Position
    assert position is target.position(source.position.id) and position is not source.position
    assert target.position_id(order.client_order_id) == source.position.id
    assert position.to_dict() == source.position.to_dict()
    assert [e.to_dict(e) for e in position.events] == [e.to_dict(e) for e in source.position.events]
    assert position.opening_order_id == source.order.client_order_id
    assert target.positions_open() == [position] and target.positions_closed() == []
    assert target.orders_open() == [order] and target.orders_closed() == []
    assert target.instrument(source.instrument.id).to_dict(target.instrument(source.instrument.id)) == (
        source.instrument.to_dict(source.instrument))


async def test_actual_worker_clean_stop_v4_restore_new_lease_channel_no_callbacks(tmp_path: Path) -> None:
    from test_history_store import execution, snapshot
    source = actual_worker(tmp_path / "source")
    await start(source)
    assert source.worker.native_restart_status.status == "FRESH_EMPTY_CACHE"
    record = await stage(source)
    original = seed_original_order(source, record)
    source.worker.runtime.history_store.ingest(replace(snapshot(executions=(execution(
        client_order_id=record.client_order_id, venue_order_id="native-venue"),)), scope=SCOPE))
    assert source.worker.runtime.native_owned_requests()[0].venue_order_ids == frozenset({"native-venue"})
    original_position = seed_original_position(source.assembly.node.cache, original)
    original_instrument = source.assembly.node.cache.instrument(original.instrument_id)
    source.worker.runtime.journal.claim("entry")
    before_command = source.worker.runtime.journal.get("entry")
    assert before_command.state is CommandState.SENDING
    before_reservations = source.worker.runtime.reservation_totals(source.plan.scope)
    channel, lease = source.assembly.channel, source.worker._lock
    native_state = original.to_dict()
    original_events = [e.to_dict(e) for e in original.events]
    position_state = original_position.to_dict()
    position_events = [e.to_dict(e) for e in original_position.events]
    instrument_state = original_instrument.to_dict(original_instrument)
    assert source.worker.runtime.projection("position-1").owned_quantity == 0
    assert len(source.worker.runtime.store.list_position_events("position-1")) == 1
    no_effects(source)
    await source.worker.stop()
    assert decode_document((source.worker.directory / "native-restart.json").read_bytes())["state"] == "sealed"
    tool = complete_offline_ledgers(source.worker.directory)
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    assert tool["main"](["snapshot", "--source", str(source.worker.directory), "--target",
        str(backup), "--writers-stopped", "--require-native"]) == 0
    assert json.loads((backup / "manifest.json").read_text())["format_version"] == 4
    assert tool["main"](["verify", "--source", str(backup), "--require-native"]) == 0
    assert tool["main"](["restore", "--source", str(backup), "--target", str(restored),
        "--require-native"]) == 0
    next_worker = actual_worker(restored, source)
    await start(next_worker)
    try:
        assert next_worker.worker.native_restart_status.status == "RESTORED_LOCAL_CACHE"
        assert next_worker.worker.native_restart_status.orders == 1
        assert next_worker.worker.native_restart_status.positions == 1
        assert next_worker.worker.native_restart_status.missing_owned_orders == 0
        assert next_worker.assembly.channel is not channel and next_worker.assembly.channel != channel
        assert next_worker.worker._lock is not lease and next_worker.worker._lock.acquired
        cache = next_worker.assembly.node.cache
        order = cache.order(original.client_order_id)
        assert order.to_dict() == native_state
        assert [e.to_dict(e) for e in order.events] == original_events
        position = cache.position_for_order(order.client_order_id)
        assert type(position) is module("model.position").Position
        assert position is cache.position(original_position.id) and position is not original_position
        assert position.id == cache.position_id(order.client_order_id) == order.position_id
        assert position.to_dict() == position_state
        assert [e.to_dict(e) for e in position.events] == position_events
        assert position.opening_order_id == order.client_order_id == original_position.opening_order_id
        assert position.instrument_id == order.instrument_id == original_instrument.id
        assert cache.positions_open() == [position] and cache.positions_closed() == []
        assert cache.orders_open() == [] and cache.orders_closed() == [order]
        instrument = cache.instrument(position.instrument_id)
        assert type(instrument) is type(original_instrument) and instrument is not original_instrument
        assert instrument.to_dict(instrument) == instrument_state
        assert cache.check_integrity() is True
        assert next_worker.worker.runtime.journal.get("entry") == replace(
            before_command, state=CommandState.UNKNOWN)
        assert not next_worker.worker.runtime.journal.get("entry").terminal_confirmed
        reservations = next_worker.worker.runtime.reservation_totals(source.plan.scope)
        assert replace(reservations, snapshot_id=before_reservations.snapshot_id) == before_reservations
        assert next_worker.worker.runtime.projection("position-1").owned_quantity == 0
        assert len(next_worker.worker.runtime.store.list_position_events("position-1")) == 1
        next_worker.worker.post_native(channel, original.last_event)
        await asyncio.sleep(0)
        assert next_worker.worker.rejected_channels == 1
        no_effects(next_worker)
    finally:
        await next_worker.worker.stop()


async def test_actual_uncertainty_control_liabilities_and_fixed_authority_blocks(tmp_path: Path) -> None:
    for state in (CommandState.SENDING, CommandState.UNKNOWN):
        h = actual_worker(tmp_path / state.value)
        await start(h)
        record = await stage(h)
        seed_original_order(h, record)
        reservation = record.reservation
        await h.worker.stop()
        seals = NativeRestartStore(h.worker.directory)
        identity = h.worker._restart.identity
        generation = seals.load(identity)
        assert generation is not None
        seals.mark_active(identity, generation)
        # Fixture-only stopped SQL generation exercises SENDING startup separately
        # from the clean-stop transition which already preserves UNKNOWN.
        with CommandJournal(h.worker.directory / "execution.sqlite3", ExecutionScope(
                SCOPE.account_id, SCOPE.environment)) as journal:
            keys = ["entry"]
            for kind in (CommandKind.CANCEL, CommandKind.LEVERAGE, CommandKind.PROTECTION):
                key = kind.value.lower()
                journal.register(key, kind, {"entry_key": "entry"})
                keys.append(key)
            for key in keys:
                journal.claim(key)
                if state is CommandState.UNKNOWN:
                    journal.mark_unknown(key, "SYNTHETIC_UNCERTAINTY")
            before = {key: journal.get(key) for key in keys}
        seals.seal(identity, generation.payload)
        new = actual_worker(h.worker.directory, h)
        await start(new)
        try:
            for key in keys:
                after = new.worker.runtime.journal.get(key)
                assert after == replace(before[key], state=CommandState.UNKNOWN)
                assert not after.terminal_confirmed
            assert new.worker.runtime.journal.get("entry").reservation == reservation
            assert new.worker.runtime.projection("position-1").owned_quantity == 0
            assert len(new.worker.runtime.store.list_position_events("position-1")) == 1
            assert new.worker.runtime.store.get_position("position-1").reconciliation_required
            with pytest.raises(RuntimeBlocked, match="NATIVE_RESTART_RECONCILIATION_REQUIRED"):
                await new.worker.connect_monitoring()
            with pytest.raises(RuntimeBlocked, match="QUALIFICATION"):
                new.worker.runtime.start()
            no_effects(new)
        finally:
            await new.worker.stop()


async def test_actual_stale_active_mixed_generations_refuse_before_app_mutation(tmp_path: Path) -> None:
    for defect in ("active", "stale", "mixed", "missing"):
        h = actual_worker(tmp_path / defect)
        await start(h)
        await stage(h)
        await h.worker.stop()
        marker = h.worker.directory / "native-restart.json"
        if defect == "active":
            change_document(marker, lambda document: document.update(state="active"))
        elif defect == "stale":
            with CommandJournal(h.worker.directory / "execution.sqlite3", ExecutionScope(
                    SCOPE.account_id, SCOPE.environment)) as journal:
                journal.claim("entry")
        elif defect == "mixed":
            donor = actual_worker(tmp_path / "donor")
            await start(donor)
            await donor.worker.stop()
            marker.write_bytes((donor.worker.directory / "native-restart.json").read_bytes())
        else:
            marker.unlink()
        before = application_digest(h.worker.directory)
        native_before = marker.read_bytes() if marker.exists() else None
        new = actual_worker(h.worker.directory, h)
        with pytest.raises(NativeRestartError, match="^NATIVE_RESTART_BLOCKED$"):
            await new.worker.start_passive()
        assert application_digest(h.worker.directory) == before
        assert (marker.read_bytes() if marker.exists() else None) == native_before
        assert new.worker._runtime is None and new.worker._stopped
        assert not new.worker._lock.acquired
        assert new.worker._assembly.node.cache.orders() == []
        assert not new.worker._assembly.node.is_running() and new.core.reads == []


def test_actual_foreign_manual_ambiguous_lifetime_and_unsupported_streams_redacted() -> None:
    for defect in ("foreign", "account", "ambiguous", "updated", "split", "reversed"):
        source = fixture()
        payload = capture_native_cache(source.cache, identity=source.identity, owners=source.owners)
        owners = source.owners
        if defect == "foreign":
            owners = ()
        elif defect == "account":
            owners = (replace(owners[0], native_account_id="BYBIT-SECRET"),)
        elif defect == "ambiguous":
            extra = replace(owners[0].request.owned, client_order_id="ec_" + "a" * 32,
                position_id="different-lifetime")
            owners += (replace(owners[0], request=replace(owners[0].request, owned=extra)),)
        elif defect == "updated":
            events, obj, uuid = module("model.events.order"), module("model.objects"), module("core.uuid").UUID4
            order = source.order
            event = events.OrderUpdated(trader_id=order.trader_id, strategy_id=order.strategy_id,
                instrument_id=order.instrument_id, client_order_id=order.client_order_id,
                venue_order_id=order.venue_order_id, account_id=order.account_id,
                quantity=order.quantity, price=obj.Price.from_str("0.5000"), trigger_price=None,
                event_id=uuid(), ts_event=8, ts_init=8)
            serializer = module("serialization.serializer").MsgSpecSerializer(
                importlib.import_module("msgspec").msgpack, timestamps_as_str=True)
            payload["orders"][0]["events"].append(base64.b64encode(serializer.serialize(event)).decode())
        elif defect == "split":
            event = source.position.events[0]
            fields = event.to_dict(event)
            fields["last_qty"] = "1"
            serializer = module("serialization.serializer").MsgSpecSerializer(
                importlib.import_module("msgspec").msgpack, timestamps_as_str=True)
            payload["positions"][0]["events"][0] = base64.b64encode(
                serializer.serialize(type(event).from_dict(fields))).decode()
        else:
            payload["positions"][0]["events"].reverse()
        target = module("cache.cache").Cache()
        with pytest.raises(NativeRestartError, match="^NATIVE_RESTART_BLOCKED$"):
            prepare_native_cache(payload, identity=source.identity, owners=owners)
        assert target.orders() == target.positions() == target.instruments() == []
    manual = fixture()
    with pytest.raises(NativeRestartError, match="^NATIVE_RESTART_BLOCKED$"):
        capture_native_cache(manual.cache, identity=manual.identity, owners=())
    reopened = reopened_native_lifetime()
    with pytest.raises(NativeRestartError, match="^NATIVE_RESTART_BLOCKED$"):
        capture_native_cache(reopened.cache, identity=reopened.identity, owners=reopened.owners)
    adjusted = fixture()
    adjustment = module("model.events.position").PositionAdjusted(
        trader_id=adjusted.position.trader_id, strategy_id=adjusted.position.strategy_id,
        instrument_id=adjusted.position.instrument_id, position_id=adjusted.position.id,
        account_id=adjusted.position.account_id,
        adjustment_type=next(iter(module("model.enums").PositionAdjustmentType)),
        quantity_change=None, pnl_change=module("model.objects").Money(
            0.01, adjusted.instrument.quote_currency), reason="fixture",
        event_id=module("core.uuid").UUID4(), ts_event=10, ts_init=10)
    adjusted.position.apply_adjustment(adjustment)
    with pytest.raises(NativeRestartError, match="^NATIVE_RESTART_BLOCKED$"):
        capture_native_cache(adjusted.cache, identity=adjusted.identity, owners=adjusted.owners)


async def test_actual_original_pending_closed_isolated_failure_and_missing_uncertainty(tmp_path: Path) -> None:
    for case in ("execution-mismatch", "execution-missing", "native-order-foreign",
                 "native-order-blank", "native-execution-foreign", "native-execution-blank"):
        await assert_actual_history_identity_refusal(tmp_path / case, case)
    for state in ("INITIALIZED", "PENDING_CANCEL", "CANCELED"):
        source = fixture(state)
        target = assert_cache_roundtrip(source)
        assert target.order(source.order.client_order_id).status == source.order.status
    closed = closed_fixture()
    cache = assert_cache_roundtrip(closed)
    position = cache.position_for_order(closed.order.client_order_id)
    assert position.to_dict() == closed.position.to_dict() and position.is_closed
    assert cache.positions_open() == [] and cache.positions_closed() == [position]
    assert cache.orders_open() == [] and len(cache.orders_closed()) == 2
    assert position.opening_order_id == closed.order.client_order_id

    missing = actual_worker(tmp_path / "missing-cache")
    await start(missing)
    await stage(missing)
    missing.worker.runtime.journal.claim("entry")
    await missing.worker.stop()
    new = actual_worker(missing.worker.directory, missing)
    await start(new)
    try:
        assert new.worker.native_restart_status.missing_owned_orders == 1
        assert new.assembly.node.cache.orders() == new.assembly.node.cache.positions() == []
        assert new.worker.runtime.journal.get("entry").state is CommandState.UNKNOWN
        assert new.worker.runtime.projection("position-1").owned_quantity == 0
        no_effects(new)
    finally:
        await new.worker.stop()

    invalid = actual_worker(tmp_path / "isolated-stage-failure")
    await start(invalid)
    record = await stage(invalid)
    seed_original_order(invalid, record)
    await invalid.worker.stop()
    marker = invalid.worker.directory / "native-restart.json"
    native_before = change_document(marker,
        lambda document: document["payload"]["orders"][0]["state"].update(quantity="999"))
    before = application_digest(invalid.worker.directory)
    refused = actual_worker(invalid.worker.directory, invalid)
    with pytest.raises(NativeRestartError, match="^NATIVE_RESTART_BLOCKED$"):
        await refused.worker.start_passive()
    assert application_digest(invalid.worker.directory) == before and marker.read_bytes() == native_before
    target = refused.worker._assembly.node.cache
    assert target.orders() == target.positions() == target.instruments() == []
    assert refused.worker._runtime is None and refused.worker._stopped
    assert refused.core.reads == [] and not refused.worker._assembly.node.is_running()


async def assert_actual_history_identity_refusal(directory: Path, case: str) -> None:
    """Both publication and consumption refuse using the untouched actual builder."""
    from test_history_store import execution, order, snapshot

    from multi_exchange_trader.execution.native_restart import NativeRestartCoordinator
    source = actual_worker(directory)
    await start(source)
    record = await stage(source)
    if case != "execution-missing":
        seed_original_order(source, record)
    coordinator = NativeRestartCoordinator(directory, source.assembly, SCOPE,
        source.worker.symbols, source.worker.trader_id)
    requests = source.worker.runtime.native_owned_requests()
    owners, missing = coordinator._expectations(requests)
    payload = capture_native_cache(source.assembly.node.cache, identity=coordinator.identity,
        owners=owners, allowed_missing_ids=missing)
    assert len(payload["orders"]) == (0 if case == "execution-missing" else 1)
    venue = "history-venue" if case == "execution-mismatch" else "native-venue"
    client = ("" if case.endswith("blank") else "foreign-client"
        if case.startswith("native-") else record.client_order_id)
    rows = ({"orders": (order(client_order_id=client, venue_order_id=venue,
        quantity=requests[0].request.owned.quantity),)} if "order" in case else
        {"executions": (execution(client_order_id=client, venue_order_id=venue),)})
    source.worker.runtime.history_store.ingest(replace(snapshot(**rows), scope=SCOPE))
    no_effects(source)
    marker = directory / "native-restart.json"
    with pytest.raises((NativeRestartError, RuntimeBlocked)):
        await source.worker.stop()
    assert source.worker._stopped and decode_document(marker.read_bytes())["state"] == "active"
    with CommandJournal(directory / "execution.sqlite3", ExecutionScope(SCOPE.account_id, "DEMO")) as journal:
        for kind in (CommandKind.CANCEL, CommandKind.LEVERAGE):
            journal.register(kind.value.lower(), kind, {"entry_key": "entry"})
        for key in ("entry", "cancel", "leverage"):
            journal.claim(key)
        commands = journal.list_commands()
    # Matching application digest ensures historical identity is the blocker.
    coordinator.store.seal(coordinator.identity, payload)
    before, sealed = application_digest(directory), marker.read_bytes()
    refused = actual_worker(directory, source)
    with pytest.raises((NativeRestartError, RuntimeBlocked)):
        await refused.worker.start_passive()
    assert application_digest(directory) == before and marker.read_bytes() == sealed
    assert refused.worker._runtime is None and refused.worker._stopped
    target = refused.worker._assembly.node.cache
    assert target.orders() == target.positions() == target.instruments() == []
    assert refused.core.reads == [] and not refused.worker._assembly.node.is_running()
    assert refused.worker._assembly.node.kernel.exec_engine.event_count == 0
    assert refused.worker._assembly.node.kernel.exec_engine.command_count == 0
    with CommandJournal(directory / "execution.sqlite3", ExecutionScope(SCOPE.account_id, "DEMO")) as journal:
        assert journal.list_commands() == commands
