"""Control crash/ownership boundaries; every transport is explicitly simulated."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from test_risk import NOW, inputs
from test_runtime import (
    consume,
    enable_fake_handoff,
    queue,
    selection,
)
from test_runtime import setup as setup  # noqa: PLC0414 - export shared pytest fixture

from multi_exchange_trader.execution import codec
from multi_exchange_trader.execution.execution_provenance import FillQuarantineRecord
from multi_exchange_trader.execution.journal import CommandState
from multi_exchange_trader.execution.modes import AutomaticMode
from multi_exchange_trader.execution.native_controls import NativeLeverageControl
from multi_exchange_trader.execution.protection import ProtectionPolicy
from multi_exchange_trader.execution.risk import assess_entry
from multi_exchange_trader.execution.runtime import RuntimeBlocked
from multi_exchange_trader.execution.venue_readback import (
    BybitVenueReadback,
    VenuePosition,
    VenueSnapshot,
)


def controls(worker: Any, journal: Any, monkeypatch: pytest.MonkeyPatch, *,
             fail: bool = False, mismatch: bool = False,
             exposure: bool = False) -> tuple[Any, Any, list[tuple[str, Decimal]]]:
    """Fake effect and GET boundaries; real runtime/journal/validation run unchanged."""
    controller = object.__new__(NativeLeverageControl)
    controller.scope, controller.native_account_id = worker.bridge.scope, "BYBIT-UNIFIED"
    adapter = object.__new__(BybitVenueReadback)
    adapter.scope = worker.bridge.scope
    calls: list[tuple[str, Decimal]] = []
    cycles: list[str] = []

    async def apply(_self: Any, symbol: str, leverage: Decimal) -> None:
        assert journal.get("leverage").state is CommandState.SENDING
        calls.append((symbol, leverage))
        if fail:
            raise TimeoutError("SECRET_TRANSPORT_TEXT")

    async def snapshot(_self: Any, symbol: str) -> VenueSnapshot:
        cycles.append(str(len(cycles)))
        return VenueSnapshot(worker.bridge.scope, symbol, cycles[-1], NOW, NOW, True,
            (VenuePosition(symbol, 0, None, Decimal(1 if exposure else 0),
                Decimal("2" if mismatch else "1.8"), None, None, None),), (), ())

    monkeypatch.setattr(NativeLeverageControl, "apply", apply)
    monkeypatch.setattr(BybitVenueReadback, "snapshot", snapshot)
    return controller, adapter, calls


async def test_claim_native_fractional_setting_readback_then_fresh_entry_risk(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, journal, _, strategy, _, _ = setup
    original = worker.queue_entry("entry", "position-1", plan_id="plan-1",
        plan=assess_entry(**inputs()),
        protection_policy=ProtectionPolicy("policy-1", 0, Decimal(90), Decimal(120), "MarkPrice"),
        mode=AutomaticMode.FULL_AUTO, selection_request=selection())
    monkeypatch.setattr(worker, "_require_qualification", lambda: None)
    worker.start()
    controller, adapter, calls = controls(worker, journal, monkeypatch)
    with pytest.raises(RuntimeBlocked, match="FRESH_IMMUTABLE_SUBMIT_RISK"):
        worker.dispatch("entry")
    record = await worker.apply_leverage("leverage", "entry", control=controller, adapter=adapter)
    assert calls == [("BTCUSDT", Decimal("1.8"))]
    assert record.state is CommandState.ACKNOWLEDGED and record.terminal_confirmed
    assert journal.get("entry").reservation == original.reservation
    assert not journal.get("entry").terminal_confirmed
    assert worker._leverage_readbacks["entry"].qualified is False
    await worker.apply_leverage("leverage", "entry", control=controller, adapter=adapter)
    assert len(calls) == 1
    assert worker.dispatch("entry").state is CommandState.SENDING
    assert len(strategy.commands) == 1


@pytest.mark.parametrize("failure", ["timeout", "mismatched_readback"])
async def test_unknown_setting_never_replays_and_read_only_recovery_retains_entry_reservation(
    setup: Any, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    worker, journal, _, _, kwargs, module = setup
    original = queue(worker)
    monkeypatch.setattr(worker, "_require_qualification", lambda: None)
    controller, adapter, calls = controls(worker, journal, monkeypatch,
        fail=failure == "timeout", mismatch=failure == "mismatched_readback")
    with pytest.raises(RuntimeBlocked, match="AMBIGUOUS_OR_UNVERIFIED") as error:
        await worker.apply_leverage("leverage", "entry", control=controller, adapter=adapter)
    assert "SECRET" not in str(error.value)
    assert journal.get("leverage").state is CommandState.UNKNOWN
    worker.shutdown()
    restarted = module.NativeExecutionRuntime(**kwargs)
    monkeypatch.setattr(restarted, "_require_qualification", lambda: None)
    await restarted.apply_leverage("leverage", "entry", control=controller, adapter=adapter)
    assert len(calls) == 1
    _, adapter, recovered_calls = controls(restarted, journal, monkeypatch)
    assert (await restarted.reconcile_leverage("leverage", adapter)).terminal_confirmed
    assert not recovered_calls
    assert journal.get("entry").reservation == original.reservation
    assert not journal.get("entry").terminal_confirmed
    restarted.shutdown()


async def test_manual_position_blocks_leverage_before_claim_and_never_changes_quantity(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, journal, _, _, _, _ = setup
    queue(worker)
    monkeypatch.setattr(worker, "_require_qualification", lambda: None)
    controller, adapter, calls = controls(worker, journal, monkeypatch, exposure=True)
    with pytest.raises(ValueError, match="EXPOSURE_FORBIDS"):
        await worker.apply_leverage("leverage", "entry", control=controller, adapter=adapter)
    assert not calls
    assert journal.get("leverage").state is CommandState.PENDING
    assert worker.projection("position-1").owned_quantity == 0


async def test_stale_risk_after_setting_cannot_submit_and_default_qualification_stays_closed(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, journal, _, strategy, _, _ = setup
    queue(worker)
    controller, adapter, calls = controls(worker, journal, monkeypatch)
    with pytest.raises(RuntimeBlocked, match="QUALIFICATION"):
        await worker.apply_leverage("leverage", "entry", control=controller, adapter=adapter)
    assert not calls and journal.get("leverage") is None
    monkeypatch.setattr(worker, "_require_qualification", lambda: None)
    worker.start()
    await worker.apply_leverage("leverage", "entry", control=controller, adapter=adapter)
    original = worker.entry_risk
    worker.entry_risk = lambda *args: replace(original(*args), leverage=Decimal(2))
    with pytest.raises(RuntimeBlocked):
        worker.dispatch("entry")
    assert not strategy.commands


def cached_target(worker: Any, strategy: Any, client_id: str) -> Any:
    order = strategy.commands[0][0]
    order.account_id, order.strategy_id = "BYBIT-UNIFIED", "strategy-1"
    order.venue_order_id = "venue-1"
    order.is_closed = order.is_pending_cancel = False
    strategy.id = "strategy-1"
    strategy.cache = SimpleNamespace(order=lambda key: order if key == client_id else None)
    return order


def test_cancel_claim_actual_cached_target_then_cancel_fill_race_preserves_reservation(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, journal, _, strategy, _, _ = setup
    entry = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    consume(worker, entry.client_order_id, "OrderAccepted")
    order = cached_target(worker, strategy, entry.client_order_id)
    worker.queue_cancel("cancel", entry.client_order_id)
    calls: list[object] = []

    def cancel(target: object, *, params: dict[str, object]) -> None:
        assert journal.get("cancel").state is CommandState.SENDING
        assert target is order
        calls.append(target)
        consume(worker, entry.client_order_id, "OrderFilled")
        consume(worker, entry.client_order_id, "OrderCanceled")

    monkeypatch.setattr(strategy, "cancel_order", cancel)
    assert worker.dispatch_cancel("cancel").state is CommandState.ACKNOWLEDGED
    worker.dispatch_cancel("cancel")
    assert len(calls) == 1
    assert worker.projection("position-1").owned_quantity == 0
    assert worker.projection("position-1").executions == ()
    assert worker.projection("position-1").entries_blocked
    quarantined = [value for row in worker.store.list_position_events("position-1")
                   if type(value := codec.decode(row.payload_json)) is FillQuarantineRecord]
    assert len(quarantined) == 1
    assert quarantined[0].candidate.fill.quantity == Decimal("0.4")
    assert quarantined[0].candidate.fill.client_order_id == entry.client_order_id
    assert journal.get("entry").reservation == entry.reservation
    assert not journal.get("entry").terminal_confirmed
    assert not journal.get("cancel").terminal_confirmed


def test_cancel_rejects_external_or_changed_cache_and_unknown_never_replays(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, _journal, _, strategy, kwargs, module = setup
    entry = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    consume(worker, entry.client_order_id, "OrderAccepted")
    order = cached_target(worker, strategy, entry.client_order_id)
    with pytest.raises(RuntimeBlocked, match="OWNERSHIP"):
        worker.queue_cancel("external", "manual-order")
    order.account_id = "OTHER"
    with pytest.raises(ValueError, match="SCOPED"):
        worker.queue_cancel("cancel", entry.client_order_id)
    order.account_id = "BYBIT-UNIFIED"
    worker.queue_cancel("cancel", entry.client_order_id)
    strategy.fail = True
    assert worker.dispatch_cancel("cancel").state is CommandState.UNKNOWN
    worker.shutdown()
    restarted = module.NativeExecutionRuntime(**kwargs)
    assert restarted.dispatch_cancel("cancel").state is CommandState.UNKNOWN
    assert len(strategy.commands) == 2  # one entry, one uncertain cancellation
    restarted.shutdown()


@pytest.mark.parametrize("venue_id", ["WRONG-VENUE-ID", None])
@pytest.mark.parametrize("kind", ["OrderCanceled", "OrderExpired"])
def test_cancel_requires_durable_venue_identity_and_keeps_racing_owned_fills(
    setup: Any, monkeypatch: pytest.MonkeyPatch, venue_id: str | None, kind: str,
) -> None:
    worker, journal, store, strategy, kwargs, module = setup
    entry = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    consume(worker, entry.client_order_id, "OrderAccepted")
    cached_target(worker, strategy, entry.client_order_id)
    worker.queue_cancel("cancel", entry.client_order_id)
    assert worker.dispatch_cancel("cancel").state is CommandState.SENDING
    for _ in range(2):
        consume(worker, entry.client_order_id, kind, venue_order_id=venue_id)
    state = worker.projection("position-1")
    assert journal.get("cancel").state is CommandState.UNKNOWN
    assert [item.reason for item in state.conflicts] == ["CANCEL_IDENTITY_UNVERIFIED"]
    assert not state.cancelled_order_ids
    assert worker.status.entries_paused
    before = len(store.list_position_events("position-1"))
    consume(worker, entry.client_order_id, kind, venue_order_id="ANOTHER-WRONG-ID")
    assert len(store.list_position_events("position-1")) == before
    assert len(worker.projection("position-1").conflicts) == 1
    with pytest.raises(RuntimeBlocked, match="CANCEL_ALREADY_UNRESOLVED"):
        worker.queue_cancel("cancel-2", entry.client_order_id)
    assert journal.get("cancel-2") is None
    # Identity uncertainty must never discard a fill candidate racing cancellation.
    consume(worker, entry.client_order_id, "OrderFilled")
    assert worker.projection("position-1").owned_quantity == 0
    assert worker.projection("position-1").executions == ()
    assert worker.projection("position-1").entries_blocked
    quarantined = [value for row in worker.store.list_position_events("position-1")
                   if type(value := codec.decode(row.payload_json)) is FillQuarantineRecord]
    assert len(quarantined) == 1
    assert quarantined[0].candidate.fill.quantity == Decimal("0.4")
    assert quarantined[0].candidate.fill.client_order_id == entry.client_order_id
    assert journal.get("entry").reservation == entry.reservation
    assert not journal.get("entry").terminal_confirmed
    assert not journal.get("cancel").terminal_confirmed
    before_restart = worker.projection("position-1")
    worker.shutdown()
    restarted = module.NativeExecutionRuntime(**kwargs)
    assert restarted.dispatch_cancel("cancel").state is CommandState.UNKNOWN
    assert restarted.projection("position-1") == before_restart
    assert len(strategy.commands) == 2  # entry plus one cancel, never replayed
    restarted.shutdown()


def test_valid_owned_expiry_keeps_cancel_unconfirmed_and_racing_fill_reservation(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, journal, _, strategy, _, _ = setup
    entry = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    consume(worker, entry.client_order_id, "OrderAccepted")
    cached_target(worker, strategy, entry.client_order_id)
    worker.queue_cancel("cancel", entry.client_order_id)
    assert worker.dispatch_cancel("cancel").state is CommandState.SENDING
    consume(worker, entry.client_order_id, "OrderExpired")
    state = worker.projection("position-1")
    assert state.cancelled_order_ids == (entry.client_order_id,)
    assert not state.conflicts
    assert journal.get("cancel").state is CommandState.SENDING
    consume(worker, entry.client_order_id, "OrderFilled")
    assert worker.projection("position-1").owned_quantity == 0
    assert worker.projection("position-1").executions == ()
    assert worker.projection("position-1").entries_blocked
    quarantined = [value for row in worker.store.list_position_events("position-1")
                   if type(value := codec.decode(row.payload_json)) is FillQuarantineRecord]
    assert len(quarantined) == 1
    assert quarantined[0].candidate.fill.quantity == Decimal("0.4")
    assert quarantined[0].candidate.fill.client_order_id == entry.client_order_id
    assert journal.get("entry").reservation == entry.reservation
    assert not journal.get("entry").terminal_confirmed
    assert not journal.get("cancel").terminal_confirmed


def test_duplicate_cancel_key_is_rejected_before_journal_write_and_restart_can_dispatch(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, journal, _, strategy, kwargs, module = setup
    entry = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    consume(worker, entry.client_order_id, "OrderAccepted")
    cached_target(worker, strategy, entry.client_order_id)
    first = worker.queue_cancel("cancel-1", entry.client_order_id)
    assert worker.queue_cancel("cancel-1", entry.client_order_id) == first
    with pytest.raises(RuntimeBlocked, match="CANCEL_ALREADY_UNRESOLVED"):
        worker.queue_cancel("cancel-2", entry.client_order_id)
    assert journal.get("cancel-2") is None
    worker.shutdown()
    restarted = module.NativeExecutionRuntime(**kwargs)
    monkeypatch.setattr(restarted, "_require_qualification", lambda: None)
    with pytest.raises(RuntimeBlocked, match="CANCEL_ALREADY_UNRESOLVED"):
        restarted.queue_cancel("cancel-2", entry.client_order_id)
    assert journal.get("cancel-2") is None
    assert restarted.dispatch_cancel("cancel-1").state is CommandState.SENDING
    restarted.dispatch_cancel("cancel-1")
    with pytest.raises(RuntimeBlocked, match="CANCEL_ALREADY_UNRESOLVED"):
        restarted.queue_cancel("cancel-2", entry.client_order_id)
    consume(restarted, entry.client_order_id, "OrderCanceled")
    assert journal.get("cancel-1").state is CommandState.ACKNOWLEDGED
    assert len(strategy.commands) == 2
    assert journal.get("entry").reservation == entry.reservation
    restarted.shutdown()


def test_cancel_queue_requires_current_account_worker_lease(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, journal, _, strategy, kwargs, _ = setup
    entry = queue(worker)
    enable_fake_handoff(worker, monkeypatch)
    worker.dispatch("entry")
    consume(worker, entry.client_order_id, "OrderAccepted")
    cached_target(worker, strategy, entry.client_order_id)
    lock = kwargs["worker_lock"]
    lock.release()
    lock.acquire()
    with pytest.raises(RuntimeBlocked, match="ACCOUNT_WORKER_LEASE_REQUIRED"):
        worker.queue_cancel("cancel", entry.client_order_id)
    assert journal.get("cancel") is None


def test_leverage_risk_recheck_uses_one_comparison_instant_with_advancing_clock(
    setup: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, _, _, _, _, _ = setup
    queue(worker)
    monkeypatch.setattr(worker, "_require_qualification", lambda: None)
    ticks = iter(NOW + timedelta(microseconds=index) for index in range(100))
    worker._clock = lambda: next(ticks)
    assert worker._leverage_plan("entry", "leverage").leverage == Decimal("1.8")


@pytest.mark.parametrize("leverage", [Decimal(2), Decimal("1.80")])
async def test_native_leverage_boundary_uses_exact_existing_client_without_rounding(
    leverage: Decimal,
) -> None:
    calls: list[dict[str, Any]] = []

    async def effect(**kwargs: Any) -> None:
        calls.append(kwargs)

    http = SimpleNamespace(set_leverage=effect)
    client = SimpleNamespace(_http_client=http, set_leverage=effect)
    # Only binding is simulated; the actual production apply implementation runs.
    controller = object.__new__(NativeLeverageControl)
    controller._client, controller._http_client, controller._linear = client, http, "LINEAR"
    await controller.apply("BTCUSDT", leverage)
    expected = ({"symbol": "BTCUSDT-LINEAR", "leverage": 2} if leverage == 2 else
        {"product_type": "LINEAR", "symbol": "BTCUSDT", "buy_leverage": "1.80",
         "sell_leverage": "1.80"})
    assert calls == [expected]


async def test_unsupported_fractional_native_transport_has_no_integer_or_http_fallback() -> None:
    calls: list[dict[str, Any]] = []

    async def public_integer_api(**kwargs: Any) -> None:
        calls.append(kwargs)

    http = SimpleNamespace()  # Intentionally missing pinned fractional capability.
    client = SimpleNamespace(_http_client=http, set_leverage=public_integer_api)
    controller = object.__new__(NativeLeverageControl)
    controller._client, controller._http_client, controller._linear = client, http, "LINEAR"
    with pytest.raises(AttributeError):
        await controller.apply("BTCUSDT", Decimal("1.8"))
    assert not calls
