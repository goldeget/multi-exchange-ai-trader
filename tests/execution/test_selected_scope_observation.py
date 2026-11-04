"""Pure scope classification cannot grant ownership, global flatness, or finance."""

import importlib
from typing import Any

import pytest


def classify(endpoint: str, rows: list[dict[str, object]], *, observer: bool = True,
             positions: set[tuple[str, int]] | None = None,
             orders: set[str] | None = None) -> bool:
    function = importlib.import_module(
        "multi_exchange_trader.execution.fresh_monitoring_v1")._whole_scope_activity_v1
    return bool(function(endpoint, rows, selected_symbols=("BTCUSDT",),
        allow_unmanaged_other_symbols=observer, server_time_ms=1_000_000,
        positions_seen=set() if positions is None else positions,
        orders_seen=set() if orders is None else orders))


def position(**changes: object) -> dict[str, object]:
    return {"symbol": "ETHUSDT", "positionIdx": 0, "side": "Buy", "size": "1", **changes}


def order(**changes: object) -> dict[str, object]:
    return {"symbol": "ETHUSDT", "positionIdx": 0, "side": "Sell", "orderId": "unmanaged-venue",
        "orderLinkId": "", "reduceOnly": True, "orderStatus": "Untriggered", "qty": "1",
        "cumExecQty": "0", "leavesQty": "1", "createdTime": "1000", "updatedTime": "999000", **changes}


def test_other_symbol_activity_is_explicit_data_only_and_selected_zero_stays_zero() -> None:
    assert classify("POSITIONS", [position()])
    assert classify("ORDERS", [order()])
    assert not classify("POSITIONS", [position(symbol="BTCUSDT", side="", size="0")])


@pytest.mark.parametrize("endpoint,rows,observer", [
    ("POSITIONS", [position()], False), ("ORDERS", [order()], False),
    ("POSITIONS", [position(symbol="BTCUSDT")], True),
    ("ORDERS", [order(symbol="BTCUSDT")], True),
])
def test_default_or_selected_activity_remains_blocked(endpoint: str, rows: list[dict[str, object]],
                                                    observer: bool) -> None:
    with pytest.raises(ValueError):
        classify(endpoint, rows, observer=observer)


@pytest.mark.parametrize("changes", [{"size": "NaN"}, {"positionIdx": True}, {"side": ""},
                                      {"symbol": ""}, {"size": "-1"}])
def test_unknown_position_activity_is_not_observation_permission(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        classify("POSITIONS", [position(**changes)])


@pytest.mark.parametrize("changes", [{"qty": "-1"}, {"orderStatus": "Unknown"},
    {"leavesQty": "2"}, {"updatedTime": "1000001"}, {"createdTime": "1000001"},
    {"reduceOnly": "true"}, {"orderId": ""}, {"orderLinkId": None}])
def test_unknown_order_activity_is_not_observation_permission(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        classify("ORDERS", [order(**changes)])


def test_duplicates_across_retained_pages_remain_ambiguous() -> None:
    positions: set[tuple[str, int]] = set()
    assert classify("POSITIONS", [position()], positions=positions)
    with pytest.raises(ValueError):
        classify("POSITIONS", [position()], positions=positions)
    orders: set[str] = set()
    assert classify("ORDERS", [order()], orders=orders)
    with pytest.raises(ValueError):
        classify("ORDERS", [order()], orders=orders)


def test_activity_marker_does_not_change_financial_status() -> None:
    from multi_exchange_trader.execution.monitoring_models import WorkerMonitoringStatus
    from multi_exchange_trader.execution.protection import ProtectionScope

    status: Any = WorkerMonitoringStatus("MONITORING", ProtectionScope("12345", "DEMO"),
        ("BTCUSDT",), unmanaged_usdt_linear_activity=True)
    assert status.unmanaged_usdt_linear_activity is True
    assert not status.trading_enabled and not status.credit_authorized and not status.production_supported
