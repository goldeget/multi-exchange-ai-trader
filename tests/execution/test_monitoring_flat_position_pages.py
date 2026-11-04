"""Complete symbol page chains may repeat inactive zero-position placeholders."""

import importlib
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any, cast

import httpx
import pytest

from multi_exchange_trader.execution.monitoring_models import MonitoringError

pytest_plugins = ("test_monitoring_readback",)
read = cast(Callable[[Any], Awaitable[Any]],
            importlib.import_module("test_monitoring_readback").read)


def _flat(**changes: object) -> dict[str, object]:
    return {"symbol": "BTCUSDT", "positionIdx": 0, "side": "", "size": "0",
            "takeProfit": "0", "stopLoss": "0.00", "trailingStop": "", **changes}


def _install(core: Any, first: dict[str, object], second: dict[str, object], *,
             second_cursor: str = "") -> list[httpx.Request]:
    calls: list[httpx.Request] = []

    def page(request: httpx.Request) -> dict[str, object]:
        calls.append(request)
        later = "cursor" in request.url.params
        return {"category": "linear", "list": [second if later else first],
                "nextPageCursor": second_cursor if later else "followup"}

    core[4]["/v5/position/list"] = page
    return calls


async def test_redundant_flat_placeholders_require_and_complete_second_page(reader_core: Any) -> None:
    first = _flat(avgPrice="0", markPrice="100", seq="-1", leverage="10",
                  createdTime="1", updatedTime="1", tpslMode="Full")
    second = _flat(size="0.000", takeProfit="", stopLoss="", trailingStop="0.000",
                   avgPrice="", markPrice="", seq="3", leverage="", createdTime="",
                   updatedTime="", tpslMode="")
    calls = _install(reader_core, first, second)
    result = await read(reader_core)
    assert len(calls) == 2
    assert "cursor" not in calls[0].url.params
    assert calls[1].url.params["cursor"] == "followup"
    assert [(row.symbol, row.signed_quantity) for row in result.positions] == [
        ("BTCUSDT", Decimal(0))]


@pytest.mark.parametrize("first,second", [
    (_flat(), _flat(size="1", side="Buy")),
    (_flat(size="1", side="Buy"), _flat()),
    (_flat(size="1", side="Buy"), _flat(size="1", side="Buy")),
    (_flat(side="Buy"), _flat()),
    (_flat(), _flat(side="Sell")),
    (_flat(), _flat(positionIdx=1)),
    (_flat(), _flat(positionIdx=True)),
    (_flat(), _flat(symbol="ETHUSDT")),
])
async def test_positive_mixed_hedge_or_foreign_duplicates_remain_rejected(
    reader_core: Any, first: dict[str, object], second: dict[str, object],
) -> None:
    _install(reader_core, first, second)
    with pytest.raises(ValueError):
        await read(reader_core)


@pytest.mark.parametrize("field", ["takeProfit", "stopLoss", "trailingStop"])
@pytest.mark.parametrize("page_index,value", [(0, "1"), (1, "1"), (1, None), (1, "NaN")])
async def test_active_or_unknown_protection_is_not_deduplicated(
    reader_core: Any, field: str, page_index: int, value: object,
) -> None:
    rows = [_flat(), _flat()]
    rows[page_index][field] = value
    _install(reader_core, *rows)
    with pytest.raises(ValueError):
        await read(reader_core)


async def test_missing_protection_is_not_deduplicated(reader_core: Any) -> None:
    first, second = _flat(), _flat()
    second.pop("trailingStop")
    _install(reader_core, first, second)
    with pytest.raises(ValueError):
        await read(reader_core)


async def test_repeat_cursor_remains_incomplete(reader_core: Any) -> None:
    calls = _install(reader_core, _flat(), _flat(), second_cursor="followup")
    with pytest.raises(ValueError) as caught:
        await read(reader_core)
    assert isinstance(caught.value.__context__, MonitoringError)
    assert str(caught.value.__context__) == "MONITORING_CURSOR_INVALID"
    assert len(calls) == 2


async def test_late_failed_page_cannot_publish_first_short_page(reader_core: Any) -> None:
    calls: list[httpx.Request] = []

    def page(request: httpx.Request) -> dict[str, object]:
        calls.append(request)
        if "cursor" in request.url.params:
            raise httpx.NetworkError("synthetic page unavailable")
        return {"category": "linear", "list": [_flat()], "nextPageCursor": "followup"}

    reader_core[4]["/v5/position/list"] = page
    with pytest.raises((ValueError, RuntimeError)):
        await read(reader_core)
    assert len(calls) >= 2


async def test_cursor_page_cap_is_unchanged(reader_core: Any) -> None:
    calls: list[httpx.Request] = []

    def page(request: httpx.Request) -> dict[str, object]:
        calls.append(request)
        return {"category": "linear", "list": [_flat()],
                "nextPageCursor": "next-" + str(len(calls))}

    reader_core[4]["/v5/position/list"] = page
    with pytest.raises(ValueError) as caught:
        await read(reader_core)
    assert isinstance(caught.value.__context__, MonitoringError)
    assert str(caught.value.__context__) == "MONITORING_PAGE_CAP"
    assert len(calls) == 5


async def test_original_single_positive_position_path_is_preserved(reader_core: Any) -> None:
    def page(request: httpx.Request) -> dict[str, object]:
        return {"category": "linear", "list": [
            {"symbol": "BTCUSDT", "positionIdx": 0, "side": "Sell", "size": "2"}],
            "nextPageCursor": ""}

    reader_core[4]["/v5/position/list"] = page
    result = await read(reader_core)
    assert [(row.symbol, row.signed_quantity) for row in result.positions] == [
        ("BTCUSDT", Decimal(-2))]
