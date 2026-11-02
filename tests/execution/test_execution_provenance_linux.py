"""Actual native event metadata; only the existing isolated Linux wheel job runs it."""

from decimal import Decimal
from typing import Any

import pytest
from test_bybit_native_linux import (
    CLIENT_ID,
    INSTRUMENT,
    SCOPE,
    TS_EVENT,
    TS_INIT,
    TS_RECEIVED,
    isolated_native_runtime,
    module,
    native_harness,
    request,
)

from multi_exchange_trader.execution.execution_provenance import (
    NativeExecutionCandidate,
    VenueExecutionObservation,
    compare_execution_candidate,
)

# Imported fixtures retain their actual namespace and socket-denial checks.
__all__ = ["isolated_native_runtime", "native_harness"]


@pytest.mark.parametrize("kind,reconciliation,source", [
    ("OrderFilled", False, "NATIVE_ORDER_FILLED"),
    ("OrderFilled", True, "NATIVE_ORDER_FILLED"),
    ("FillReport", None, "NATIVE_FILL_REPORT"),
])
def test_actual_native_fill_metadata_never_authorizes_credit(
    native_harness: Any, kind: str, reconciliation: bool | None, source: str,
) -> None:
    harness = native_harness
    harness.bridge.register_owned(request())
    ids, enums, objects = module("model.identifiers"), module("model.enums"), module("model.objects")
    uuid = module("core.uuid").UUID4
    common = {"account_id": ids.AccountId("BYBIT-UNIFIED"),
        "instrument_id": ids.InstrumentId.from_str(INSTRUMENT),
        "client_order_id": ids.ClientOrderId(CLIENT_ID), "venue_order_id": ids.VenueOrderId("venue-1"),
        "trade_id": ids.TradeId("exec-1"), "order_side": enums.OrderSide.BUY,
        "last_qty": objects.Quantity.from_str("0.004"), "last_px": objects.Price.from_str("50001.5"),
        "commission": objects.Money(Decimal("-0.01"), module("model.currencies").USDT),
        "liquidity_side": enums.LiquiditySide.MAKER, "ts_event": TS_EVENT, "ts_init": TS_INIT}
    if kind == "OrderFilled":
        event = module("model.events.order").OrderFilled(**common, trader_id=harness.trader,
            strategy_id=harness.strategy.id, position_id=None, order_type=enums.OrderType.LIMIT,
            currency=module("model.currencies").USDT, event_id=uuid(),
            reconciliation=reconciliation)
        assert event.reconciliation is reconciliation
    else:
        event = module("execution.reports").FillReport(**common, report_id=uuid())
        assert not hasattr(event, "reconciliation")
    evidence = harness.bridge.consume(event, harness.context)
    assert evidence.fill.source_kind == source
    assert evidence.fill.native_reconciliation is reconciliation
    assert (evidence.fill.execution_id, evidence.venue_order_id,
            evidence.fill.commission, evidence.fill.ts_event_ns, evidence.fill.ts_init_ns,
            evidence.fill.received_at_ns) == (
        "exec-1", "venue-1", Decimal("-0.01"), TS_EVENT, TS_INIT, TS_RECEIVED)
    candidate = NativeExecutionCandidate(evidence.fill, evidence.venue_order_id)
    observation = VenueExecutionObservation(SCOPE, "untrusted-binding", "untrusted-batch",
        "exec-1", CLIENT_ID, "venue-1", INSTRUMENT, "BUY", Decimal("0.004"),
        Decimal("50001.5"), Decimal("-0.01"), "USDT", TS_EVENT)
    result = compare_execution_candidate(candidate, observation)
    assert result.state == "MATCH"
    assert result.credit_authorized is False
