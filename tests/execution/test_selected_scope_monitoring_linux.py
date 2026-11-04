"""Genuine SDK observation remains scoped and financially forbidden under isolation."""

import importlib
from pathlib import Path
from typing import Any

import pytest

from multi_exchange_trader.execution.monitoring_models import MonitoringError
from multi_exchange_trader.execution.observation_instrument_v1 import (
    SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
)

isolated_native_runtime = importlib.import_module("test_bybit_native_linux").isolated_native_runtime
fresh_case = importlib.import_module("test_fresh_monitoring_linux").fresh_case
connect = importlib.import_module("test_fresh_monitoring_linux").connect


def other_order(symbol: str = "ETHUSDT") -> dict[str, object]:
    stamp = importlib.import_module("restored_monitoring_io_support").milliseconds() - 1
    return {"symbol": symbol, "orderId": "unmanaged-other", "orderLinkId": "",
        "positionIdx": 0, "side": "Sell", "reduceOnly": True, "orderStatus": "Untriggered",
        "qty": "1", "cumExecQty": "0", "leavesQty": "1",
        "createdTime": str(stamp - 1000), "updatedTime": str(stamp)}


async def test_actual_sdk_observer_keeps_other_symbols_unmanaged_without_global_flatness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with fresh_case(tmp_path, monkeypatch,
                          metadata_profile=SDK1231_OBSERVATION_UNKNOWN_FEES_V1) as h:
        h.io.whole_positions = [{"symbol": "ETHUSDT", "positionIdx": 0, "side": "Buy", "size": "1"}]
        h.io.whole_orders = [other_order()]
        controller = await connect(h)
        status = h.worker.monitoring_status
        assert status.unmanaged_usdt_linear_activity is True
        assert not any((status.trading_enabled, status.credit_authorized, status.production_supported))
        assert not h.assembly.node.cache.orders() and not h.assembly.node.cache.positions()
        assert not h.worker.runtime.journal.list_commands()
        assert h.session._linear_positions_current is not None
        assert h.session._linear_orders_current is not None
        assert all(page.source.query[0] == ("category", "linear") for page in (
            *h.session._linear_positions_current.pages, *h.session._linear_orders_current.pages))
        # Observation is not the existing account-flat proof, even with the same owner.
        with pytest.raises(MonitoringError, match="EXTERNAL_ACTIVITY"):
            await controller._whole_account_flat()


async def test_actual_sdk_default_and_selected_activity_still_refuse_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with fresh_case(tmp_path / "default", monkeypatch) as h:
        h.io.whole_positions = [{"symbol": "ETHUSDT", "positionIdx": 0, "side": "Buy", "size": "1"}]
        with pytest.raises((ValueError, RuntimeError)):
            await h.worker.connect_monitoring()
        assert not h.io.wires
    for endpoint in ("positions", "orders"):
        async with fresh_case(tmp_path / endpoint, monkeypatch,
                              metadata_profile=SDK1231_OBSERVATION_UNKNOWN_FEES_V1) as h:
            if endpoint == "positions":
                h.io.whole_positions = [{"symbol": "BTCUSDT", "positionIdx": 0, "side": "Buy", "size": "1"}]
            else:
                h.io.whole_orders = [other_order("BTCUSDT")]
            with pytest.raises((ValueError, RuntimeError)):
                await h.worker.connect_monitoring()
            assert not h.io.wires


async def test_actual_sdk_observer_receipt_retains_whole_sources_and_activity_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with fresh_case(tmp_path, monkeypatch,
                          metadata_profile=SDK1231_OBSERVATION_UNKNOWN_FEES_V1) as h:
        h.io.whole_positions = [{"symbol": "ETHUSDT", "positionIdx": 0, "side": "Buy", "size": "1"}]
        controller: Any = await connect(h)
        target = controller.capture()
        receipt = await controller.read_receipt(target, "POST")
        slot = controller._receipts[receipt]
        assert slot.unmanaged_usdt_linear_activity is True
        assert {endpoint for endpoint, _sources, _slot in slot.whole_sources} == {"POSITIONS", "ORDERS"}
        assert all(sources for _endpoint, sources, _slot in slot.whole_sources)
        object.__setattr__(slot, "unmanaged_usdt_linear_activity", False)
        with pytest.raises(MonitoringError, match="ACCOUNT_OBSERVATION_CHANGED"):
            controller.require_receipt(receipt, target, "POST")
        assert receipt not in controller._receipts
