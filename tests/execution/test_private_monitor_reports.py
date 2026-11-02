"""Original raw prevalidation against actual SDK orders and instruments."""

import importlib
from dataclasses import replace
from decimal import Decimal

import pytest
from test_native_restart_codec import fixture


def module():
    spec = importlib.util.find_spec("multi_exchange_trader.execution.private_monitor_reports")
    assert spec is not None, "original private prevalidation is required"
    return importlib.import_module(spec.name)


def execution_row():
    return {
        "category": "linear",
        "symbol": "XRPUSDT",
        "side": "Buy",
        "orderLinkId": "ec_0123456789abcdef0123456789abcdef",
        "orderId": "venue-1",
        "execId": "original-1",
        "execType": "Trade",
        "execQty": "2",
        "execPrice": "0.4900",
        "execFee": "0.00049",
        "feeCurrency": "USDT",
        "isMaker": True,
        "execTime": "1000",
        "execValue": "0.9800",
        "feeRate": "0.0005",
        "closedSize": "0",
        "seq": 7,
        "orderQty": "10",
        "orderType": "Limit",
        "orderPrice": "0.5000",
        "leavesQty": "8",
        "stopOrderType": "",
        "createType": "CreateByUser",
        "extraFees": "",
    }


def test_original_execution_retains_seq_before_real_report_conversion():
    mod = module()
    native = fixture("ACCEPTED")
    result = mod.validate_execution(execution_row(), native.order, native.instrument, 2000)
    assert result.seq == 7 and result.quantity == Decimal(2)
    assert result.ts_event_ns == 1_000_000_000
    report = mod.fill_report(result, native.order, native.instrument, 2_000_000_000)
    from nautilus_trader.core import nautilus_pyo3 as p

    assert type(report.to_pyo3()) is p.FillReport
    assert report.last_qty.as_decimal() == Decimal(2)
    assert report.commission.as_decimal() == Decimal("0.00049")
    assert native.order.filled_qty.as_decimal() == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("symbol", "BTCUSDT"),
        ("side", "Sell"),
        ("orderLinkId", "foreign"),
        ("orderId", "foreign"),
        ("category", "spot"),
        ("execFee", None),
        ("execQty", "11"),
        ("execQty", "0.0000000001"),
        ("execType", "Funding"),
        ("execValue", "99"),
        ("seq", True),
        ("orderQty", "11"),
        ("orderPrice", "0.6000"),
        ("stopOrderType", "StopLoss"),
        ("leavesQty", "-1"),
        ("leavesQty", "11"),
        ("createType", "CreateByTakeProfit"),
        ("extraFees", [{"fee": "1", "feeCoin": "USDT"}]),
    ],
)
def test_original_raw_mismatch_is_rejected_before_sdk_cache_substitution(field, value):
    mod = module()
    native = fixture("ACCEPTED")
    row = execution_row()
    row[field] = value
    with pytest.raises(ValueError):
        mod.validate_execution(row, native.order, native.instrument, 2000)
    assert native.order.filled_qty.as_decimal() == 0


def test_pending_and_applied_dedupe_preserve_conflicting_original_economics():
    mod = module()
    native = fixture("ACCEPTED")
    execution = mod.validate_execution(execution_row(), native.order, native.instrument, 2000)
    ledger = mod.ExecutionLedger(())
    assert ledger.reserve(execution) is True
    assert ledger.reserve(execution) is False
    with pytest.raises(ValueError):
        ledger.reserve(replace(execution, fee=Decimal("0.9")))
    ledger.applied(execution)
    assert ledger.reserve(execution) is False
    with pytest.raises(ValueError):
        ledger.reserve(replace(execution, seq=8))


def test_native_fill_without_seq_can_dedupe_but_cannot_invent_original_seq():
    mod = module()
    native = fixture("ACCEPTED")
    execution = mod.validate_execution(execution_row(), native.order, native.instrument, 2000)
    original = replace(execution, seq=None, value=None, fee_rate=None, closed_quantity=None)
    ledger = mod.ExecutionLedger((original,))
    assert ledger.reserve(execution) is False
    assert ledger.originals()[0].seq is None


def test_equivalent_decimal_scale_is_exactly_representable():
    mod = module()
    native = fixture("ACCEPTED")
    row = execution_row()
    row["execQty"], row["execPrice"] = "2.000", "0.490000"
    assert mod.validate_execution(row, native.order, native.instrument, 2000).quantity == Decimal(2)


def test_documented_unknown_stop_marker_requires_ordinary_create_type():
    mod = module()
    native = fixture("ACCEPTED")
    row = execution_row()
    row["stopOrderType"] = "UNKNOWN"
    assert (
        mod.validate_execution(row, native.order, native.instrument, 2000).execution_type == "Trade"
    )


def test_market_execution_protection_price_does_not_invent_native_limit():
    mod = module()
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.model.orders import MarketOrder

    native = fixture("ACCEPTED")
    limit = native.order
    order = MarketOrder(
        trader_id=limit.trader_id,
        strategy_id=limit.strategy_id,
        instrument_id=limit.instrument_id,
        client_order_id=limit.client_order_id,
        order_side=limit.side,
        quantity=limit.quantity,
        init_id=UUID4(),
        ts_init=1,
    )
    for event in limit.events[1:]:
        order.apply(event)
    row = execution_row()
    row.update(orderType="Market", orderPrice="0.5200", stopOrderType="UNKNOWN")
    result = mod.validate_execution(row, order, native.instrument, 2000)
    assert result.price == Decimal("0.4900")
    assert not hasattr(order, "price")
