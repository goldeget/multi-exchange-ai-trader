"""Pure comparison fixtures are inert data, never receipt authority."""

import importlib
from dataclasses import replace
from decimal import Decimal

import pytest


def sample():
    assert importlib.util.find_spec("multi_exchange_trader.execution.monitoring_models")
    from multi_exchange_trader.execution.monitoring_models import (
        ApplicationMonitoringSummary,
        MonitoringObservation,
        MonitoringOrder,
        MonitoringPosition,
        MonitoringTarget,
        NativeMonitoringSummary,
        StreamStamp,
    )
    from multi_exchange_trader.execution.protection import ProtectionScope

    order = MonitoringOrder(
        "client-1",
        "venue-1",
        "BTCUSDT",
        "Buy",
        "Limit",
        "GTC",
        Decimal(2),
        Decimal(100),
        False,
        "ACCEPTED",
        Decimal(0),
        Decimal(2),
    )
    native = NativeMonitoringSummary(
        (order,), (), (MonitoringPosition("BTCUSDT", Decimal(0)),), "native"
    )
    app = ApplicationMonitoringSummary((), (), (), (), (), (), (), "app")
    target = MonitoringTarget(
        ProtectionScope("12345", "DEMO"),
        ("BTCUSDT",),
        "restore-1",
        "1",
        native,
        app,
        StreamStamp(0, "", 0, 0),
        None,
    )
    row = replace(order, status="New", created_ms=1000, updated_ms=1000, source="current")
    observation = MonitoringObservation(
        target.scope,
        target.symbols,
        (row,),
        (),
        (MonitoringPosition("BTCUSDT", Decimal(0)),),
        (1000,),
        (("BTCUSDT", 1000, 1000),),
    )
    return target, observation


def test_exact_accepted_and_explicit_zero_match_without_mutation():
    target, observation = sample()
    from multi_exchange_trader.execution.monitoring_reconciliation import compare_restored_state

    before = repr(target)
    result = compare_restored_state(target, observation)
    assert result.status.value == "MATCHED"
    assert repr(target) == before
    assert result.credit_authorized is False


@pytest.mark.parametrize(
    "change",
    [
        "missing-position",
        "extra-order",
        "wrong-tif",
        "wrong-price",
        "missing-order",
        "wrong-status",
    ],
)
def test_mismatches_fail_even_when_totals_match(change):
    target, observation = sample()
    from multi_exchange_trader.execution.monitoring_reconciliation import compare_restored_state

    row = observation.orders[0]
    if change == "missing-position":
        observation = replace(observation, positions=())
    elif change == "extra-order":
        observation = replace(
            observation,
            orders=(row, replace(row, client_order_id="manual", venue_order_id="extra")),
        )
    elif change == "wrong-tif":
        observation = replace(observation, orders=(replace(row, time_in_force="IOC"),))
    elif change == "wrong-price":
        observation = replace(observation, orders=(replace(row, price=Decimal(101)),))
    elif change == "missing-order":
        observation = replace(observation, orders=())
    else:
        observation = replace(observation, orders=(replace(row, status="Filled"),))
    assert compare_restored_state(target, observation).status.value == "BLOCKED"


def test_market_gtc_to_ioc_is_directional_and_limit_remains_strict():
    target, observation = sample()
    from multi_exchange_trader.execution.monitoring_reconciliation import compare_restored_state

    target = replace(
        target,
        native=replace(
            target.native,
            orders=(replace(target.native.orders[0], order_type="Market", price=None),),
        ),
    )
    observation = replace(
        observation,
        orders=(
            replace(observation.orders[0], order_type="Market", price=None, time_in_force="IOC"),
        ),
    )
    assert compare_restored_state(target, observation).status.value == "MATCHED"
    target = replace(
        target,
        native=replace(
            target.native, orders=(replace(target.native.orders[0], time_in_force="IOC"),)
        ),
    )
    observation = replace(
        observation, orders=(replace(observation.orders[0], time_in_force="GTC"),)
    )
    assert compare_restored_state(target, observation).status.value == "BLOCKED"


def test_typed_runtime_summary_changes_on_journal_mutation_without_credit(setup):  # noqa: F811 - pytest fixture
    from test_runtime import queue

    runtime, journal, _store, *_ = setup
    assert hasattr(runtime, "monitoring_state")
    empty = runtime.monitoring_state()
    record = queue(runtime)
    staged = runtime.monitoring_state()
    assert empty.digest != staged.digest
    assert staged.commands[0].reservation == record.reservation
    assert staged.positions[0].signed_quantity == 0
    before = staged.digest
    journal.claim(record.command_key)
    assert runtime.monitoring_state().digest != before
    assert runtime.monitoring_state().positions[0].signed_quantity == 0


def test_genuine_native_summary_preserves_original_fills_and_nanoseconds():
    from test_native_restart_codec import fixture

    import multi_exchange_trader.execution.monitoring_reconciliation as module

    assert hasattr(module, "_native_summary")
    native = fixture("PARTIALLY_FILLED")
    result = module._native_summary(native.cache, ("XRPUSDT",), "digest")
    assert result.orders[0].status == "PARTIALLY_FILLED"
    assert result.orders[0].cumulative == Decimal(5)
    assert [row.ts_event_ns for row in result.executions] == [3, 4]
    assert result.positions[0].signed_quantity == Decimal(5)


from test_runtime import setup  # noqa: F401 - shared real durable-store fixture


def filled_sample():
    target, observation = sample()
    from multi_exchange_trader.execution.monitoring_models import (
        MonitoringExecution,
        MonitoringPosition,
    )

    fill = MonitoringExecution(
        "fill-1",
        "client-1",
        "venue-1",
        "BTCUSDT",
        "Buy",
        Decimal(2),
        Decimal(100),
        Decimal("0.1"),
        "USDT",
        1000_000_000,
        "MAKER",
    )
    native = replace(
        target.native,
        orders=(
            replace(
                target.native.orders[0], status="FILLED", cumulative=Decimal(2), leaves=Decimal(0)
            ),
        ),
        executions=(fill,),
        positions=(MonitoringPosition("BTCUSDT", Decimal(2)),),
    )
    target = replace(target, native=native)
    observation = replace(
        observation,
        orders=(
            replace(
                observation.orders[0],
                status="Filled",
                cumulative=Decimal(2),
                leaves=Decimal(0),
                source="history",
            ),
        ),
        executions=(replace(fill, seq=10),),
        positions=native.positions,
    )
    return target, observation


def test_exact_native_fills_with_app_zero_are_uncredited_without_mutation():
    from multi_exchange_trader.execution.monitoring_reconciliation import compare_restored_state

    target, observation = filled_sample()
    before = target.application
    result = compare_restored_state(target, observation)
    assert result.status.value == "MATCHED_NATIVE_WITH_UNCREDITED_FILLS"
    assert target.application is before and not before.credits and not before.candidates


@pytest.mark.parametrize(
    "change", ["unaligned", "time", "fee", "currency", "liquidity", "id", "unknown-fill", "seq"]
)
def test_original_execution_identity_is_exact(change):
    from multi_exchange_trader.execution.monitoring_reconciliation import compare_restored_state

    target, observation = filled_sample()
    fill = observation.executions[0]
    if change == "unaligned":
        target = replace(
            target,
            native=replace(
                target.native,
                executions=(
                    replace(target.native.executions[0], ts_event_ns=fill.ts_event_ns + 1),
                ),
            ),
        )
    elif change == "time":
        observation = replace(
            observation, executions=(replace(fill, ts_event_ns=fill.ts_event_ns + 1000000),)
        )
    elif change == "fee":
        observation = replace(observation, executions=(replace(fill, fee=Decimal("0.2")),))
    elif change == "currency":
        observation = replace(observation, executions=(replace(fill, fee_currency="BTC"),))
    elif change == "liquidity":
        observation = replace(observation, executions=(replace(fill, liquidity="TAKER"),))
    elif change == "id":
        observation = replace(observation, executions=(replace(fill, execution_id="other"),))
    elif change == "unknown-fill":
        observation = replace(observation, executions=(fill, replace(fill, execution_id="extra")))
    else:
        target = replace(
            target,
            native=replace(
                target.native, executions=(replace(target.native.executions[0], seq=11),)
            ),
        )
    assert compare_restored_state(target, observation).status.value == "BLOCKED"


def test_terminal_leaves_and_original_lifecycle_bounds_are_checked():
    from multi_exchange_trader.execution.monitoring_reconciliation import compare_restored_state

    target, observation = filled_sample()
    for row in (
        replace(observation.orders[0], leaves=Decimal(1)),
        replace(observation.orders[0], created_ms=1001),
    ):
        assert (
            compare_restored_state(target, replace(observation, orders=(row,))).status.value
            == "BLOCKED"
        )


def test_actual_original_native_fill_chain_matches_without_invented_sequence():
    from test_native_restart_codec import fixture, module

    from multi_exchange_trader.execution.monitoring_models import (
        ApplicationMonitoringSummary,
        MonitoringObservation,
        MonitoringTarget,
        StreamStamp,
    )
    from multi_exchange_trader.execution.monitoring_reconciliation import (
        _native_summary,
        compare_restored_state,
    )
    from multi_exchange_trader.execution.native_restart_codec import capture_native_cache

    source = fixture("PARTIALLY_FILLED")
    originals = []
    for event in source.order.events:
        raw = event.to_dict(event)
        if type(event).__name__ == "OrderFilled":
            raw["ts_event"] = raw["ts_init"] = int(raw["ts_event"]) * 1_000_000
        originals.append(type(event).from_dict(raw))
    order = module("model.orders").OrderUnpacker.from_init(originals[0])
    position = None
    for event in originals[1:]:
        order.apply(event)
        if type(event).__name__ == "OrderFilled":
            if position is None:
                position = module("model.position").Position(source.instrument, event)
            else:
                position.apply(event)
    cache = module("cache.cache").Cache()
    cache.add_instrument(source.instrument)
    cache.add_order(
        order, position_id=position.id, client_id=module("model.identifiers").ClientId("BYBIT")
    )
    cache.add_position(position, module("model.enums").OmsType.NETTING)
    cache.build_index()
    capture_native_cache(cache, identity=source.identity, owners=source.owners)
    native = _native_summary(cache, ("XRPUSDT",), "original-native")
    app = ApplicationMonitoringSummary((), (), (), (), (), (), (), "app-zero")
    target = MonitoringTarget(
        source.identity.scope,
        ("XRPUSDT",),
        "restore",
        "1",
        native,
        app,
        StreamStamp(0, "", 0, 0),
        None,
    )
    observation = MonitoringObservation(
        target.scope,
        target.symbols,
        (
            replace(
                native.orders[0],
                status="PartiallyFilled",
                source="current",
                created_ms=2,
                updated_ms=4,
            ),
        ),
        tuple(replace(fill, seq=7) for fill in native.executions),
        native.positions,
        (5,),
        (("XRPUSDT", 2, 5),),
    )
    assert all(fill.seq is None for fill in native.executions)
    assert (
        compare_restored_state(target, observation).status.value
        == "MATCHED_NATIVE_WITH_UNCREDITED_FILLS"
    )
    assert order.filled_qty.as_decimal() == Decimal(5)
    assert target.application.positions == ()


@pytest.mark.parametrize("part", ["native", "application"])
async def test_current_target_rejects_public_summary_substitution(target_core, part):
    from multi_exchange_trader.execution.monitoring_reconciliation import require_current_target

    owner = target_core[0][0]
    original = target_core[1]
    if part == "native":
        altered = replace(original, native=replace(original.native, positions=()))
    else:
        altered = replace(
            original, application=replace(original.application, conflicts=("PROJECTION_CONFLICT",))
        )
    assert altered.fingerprint == original.fingerprint
    require_current_target(original, owner, "PRE")
    with pytest.raises(ValueError, match="MONITORING_CAPTURED_TARGET_REQUIRED"):
        require_current_target(altered, owner, "PRE")


from test_monitoring_receipts import session as session  # noqa: PLC0414
from test_monitoring_receipts import target_core as target_core  # noqa: PLC0414
