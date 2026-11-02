"""Bounded GET-only Bybit position/order evidence, never trading authority.

Reuses the existing signed read client; no key loading, HTTP write, node startup,
or implicit network call. Pagination completeness is scoped to one symbol and
two endpoints, not complete account/execution history or authenticated UID proof.
Position TP/SL values alone cannot prove owned protective order coverage.

Schema references (2026-11-02): https://bybit-exchange.github.io/docs/v5/position
and https://bybit-exchange.github.io/docs/v5/order/open-order .
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from ..account.bybit_readonly import BybitReadOnlyClient
from .native_events import bounded_decimal
from .protection import ProtectionScope, Side


@dataclass(frozen=True)
class VenuePosition:
    symbol: str
    position_idx: int
    side: Side | None
    quantity: Decimal
    leverage: Decimal | None
    stop_loss: Decimal | None
    take_profit: Decimal | None
    liquidation_price: Decimal | None


@dataclass(frozen=True)
class VenueOrder:
    venue_order_id: str
    client_order_id: str
    symbol: str
    position_idx: int
    side: Side
    quantity: Decimal
    leaves_quantity: Decimal
    cumulative_filled: Decimal
    reduce_only: bool
    status: str
    stop_order_type: str
    trigger_price: Decimal | None
    trigger_source: str | None


@dataclass(frozen=True)
class VenueSnapshot:
    scope: ProtectionScope
    symbol: str
    cycle_id: str
    observed_at: datetime
    server_observed_at: datetime | None
    complete: bool
    positions: tuple[VenuePosition, ...]
    orders: tuple[VenueOrder, ...]
    blockers: tuple[str, ...]
    identity_verified: Literal[False] = False
    flat_verified: Literal[False] = False


def _utc(value: datetime) -> None:
    if (type(value) is not datetime or value.tzinfo is None
            or value.utcoffset() != timedelta(0)):
        raise ValueError("READBACK_UTC_REQUIRED")


def _text(value: object, *, empty: bool = False) -> str:
    if (type(value) is not str or len(value) > 256 or (not value and not empty)
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("READBACK_INVALID_FIELD")
    return value


def _amount(value: object, *, optional: bool = False) -> Decimal | None:
    if optional and value in (None, "", "0"):
        return None
    result = bounded_decimal(value, positive=False)
    if result < 0:
        raise ValueError("READBACK_INVALID_FIELD")
    return result


def _required_amount(value: object) -> Decimal:
    result = _amount(value)
    assert result is not None
    return result


def _side(value: object) -> Side:
    if value == "Buy":
        return "BUY"
    if value == "Sell":
        return "SELL"
    raise ValueError("READBACK_INVALID_SIDE")


def _index(row: dict[str, object], symbol: str) -> int:
    idx = row.get("positionIdx")
    if row.get("symbol") != symbol or type(idx) is not int or idx not in (0, 1, 2):
        raise ValueError("READBACK_SCOPE_MISMATCH")
    return idx


def _position(row: dict[str, object], symbol: str) -> VenuePosition:
    idx = _index(row, symbol)
    quantity = _required_amount(row.get("size"))
    side = None if quantity == 0 and row.get("side") in ("", None) else _side(row.get("side"))
    return VenuePosition(symbol, idx, side, quantity,
        _amount(row.get("leverage"), optional=True), _amount(row.get("stopLoss"), optional=True),
        _amount(row.get("takeProfit"), optional=True), _amount(row.get("liqPrice"), optional=True))


def _order(row: dict[str, object], symbol: str) -> VenueOrder:
    idx = _index(row, symbol)
    reduce = row.get("reduceOnly")
    if type(reduce) is not bool:
        raise ValueError("READBACK_EXPLICIT_REDUCE_ONLY_REQUIRED")
    quantity = _required_amount(row.get("qty"))
    leaves = _required_amount(row.get("leavesQty"))
    filled = _required_amount(row.get("cumExecQty"))
    if leaves > quantity or filled > quantity:
        raise ValueError("READBACK_ORDER_QUANTITY_CONFLICT")
    trigger_source = row.get("triggerBy")
    return VenueOrder(_text(row.get("orderId")), _text(row.get("orderLinkId"), empty=True),
        symbol, idx, _side(row.get("side")), quantity, leaves, filled, reduce,
        _text(row.get("orderStatus")), _text(row.get("stopOrderType", ""), empty=True),
        _amount(row.get("triggerPrice"), optional=True),
        None if trigger_source in (None, "") else _text(trigger_source))


class BybitVenueReadback:
    """Caller supplies explicit client/scope; scope is not authenticated identity.

    The common client may retry GETs. A bounded overall deadline, five-page limit,
    repeated cursor detection and every-page server timestamps bound this cycle.
    """

    def __init__(self, *, client: BybitReadOnlyClient, scope: ProtectionScope,
                 clock: Callable[[], datetime], max_evidence_age: timedelta) -> None:
        if (type(client) is not BybitReadOnlyClient or type(scope) is not ProtectionScope
                or scope.environment != "DEMO" or client.environment != "demo"
                or type(max_evidence_age) is not timedelta
                or max_evidence_age <= timedelta(0)):
            raise ValueError("READBACK_EXPLICIT_DEMO_SCOPE_REQUIRED")
        self._client = client
        self.scope = scope
        self._clock = clock
        self._max_age = max_evidence_age

    async def _pages(self, path: str, symbol: str, deadline: float,
                     times: list[datetime]) -> list[dict[str, object]]:
        cursor = ""
        seen: set[str] = set()
        rows: list[dict[str, object]] = []
        for _ in range(5):
            params = [("category", "linear"), ("symbol", symbol), ("limit", "50")]
            if path == "/v5/order/realtime":
                params.append(("openOnly", "0"))
            if cursor:
                params.append(("cursor", cursor))
            result, server_ms = await self._client.read(path, params, signed=True, deadline=deadline)
            now = self._clock()
            _utc(now)
            if type(server_ms) is not int or not 0 < server_ms < 253402300800000:
                raise ValueError("READBACK_SERVER_TIME_REQUIRED")
            observed = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=server_ms)
            if not timedelta(0) <= now - observed <= self._max_age:
                raise ValueError("READBACK_STALE_SERVER_TIME")
            times.append(observed)
            page = result.get("list")
            if result.get("category") != "linear" or type(page) is not list or len(page) > 50:
                raise ValueError("READBACK_INVALID_PAGE")
            for row in page:
                if type(row) is not dict or any(type(key) is not str for key in row):
                    raise ValueError("READBACK_INVALID_ROW")
                rows.append(row)
            cursor = _text(result.get("nextPageCursor"), empty=True)
            if not cursor:
                return rows
            if cursor in seen:
                raise ValueError("READBACK_REPEATED_CURSOR")
            seen.add(cursor)
        raise ValueError("READBACK_TRUNCATED")

    async def snapshot(self, symbol: str) -> VenueSnapshot:
        if type(symbol) is not str or re.fullmatch(r"[A-Z0-9]{2,48}", symbol) is None:
            raise ValueError("READBACK_SYMBOL_REQUIRED")
        now = self._clock()
        _utc(now)
        cycle = uuid4().hex
        times: list[datetime] = []
        try:
            deadline = time.monotonic() + min(20.0, self._max_age.total_seconds())
            positions = tuple(_position(row, symbol) for row in
                await self._pages("/v5/position/list", symbol, deadline, times))
            orders = tuple(_order(row, symbol) for row in
                await self._pages("/v5/order/realtime", symbol, deadline, times))
            if (len({item.position_idx for item in positions}) != len(positions)
                    or len({item.venue_order_id for item in orders}) != len(orders)):
                raise ValueError("READBACK_DUPLICATE_IDENTITY")
            now = self._clock()
            _utc(now)
            if not times or not timedelta(0) <= now - min(times) <= self._max_age:
                raise ValueError("READBACK_EXPIRED_CYCLE")
        except Exception:  # noqa: BLE001 - never persist provider bodies or exception text.
            return VenueSnapshot(self.scope, symbol, cycle, now, None, False, (), (),
                                 ("VENUE_READBACK_INCOMPLETE",))
        return VenueSnapshot(self.scope, symbol, cycle, now, min(times), True, positions, orders, ())
