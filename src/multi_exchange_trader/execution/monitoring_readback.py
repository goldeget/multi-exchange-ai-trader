"""Fresh bounded broad symbol reads. Every returned economic row is validated."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext
from typing import TYPE_CHECKING

from .history import EPOCH, MAX_WINDOW_MS, HistoryWindow, _execution, _order, amount, utc
from .monitoring_budget import MonitoringReadBudget
from .monitoring_models import (
    MonitoringError,
    MonitoringExecution,
    MonitoringObservation,
    MonitoringOrder,
    MonitoringPosition,
    MonitoringTarget,
)

if TYPE_CHECKING:
    from .account_binding import AuthenticatedAccountBinding, BybitDemoAccountSession


def _ordinary(row: dict[str, object]) -> None:
    if (
        row.get("stopOrderType") != ""
        or row.get("triggerPrice") not in ("", "0", "0.0")
        or type(row.get("triggerDirection")) is not int
        or row.get("triggerDirection") != 0
        or row.get("closeOnTrigger") is not False
        or row.get("takeProfit") not in ("", "0", "0.0")
        or row.get("stopLoss") not in ("", "0", "0.0")
        or row.get("createType") not in ("CreateByUser", "")
    ):
        raise MonitoringError("MONITORING_ORDINARY_ORDER_REQUIRED")


def parse_order(
    row: dict[str, object], symbol: str, window: HistoryWindow, source: str
) -> MonitoringOrder:
    _ordinary(row)
    parsed = _order(row, symbol, window)
    kind, tif = row.get("orderType"), row.get("timeInForce")
    if (
        parsed.position_idx != 0
        or kind not in ("Market", "Limit")
        or tif not in ("GTC", "IOC")
        or parsed.status not in ("New", "PartiallyFilled", "Filled", "Cancelled", "Rejected")
        or not parsed.client_order_id
        or parsed.updated_ms > window.end_ms
    ):
        raise MonitoringError("MONITORING_ORDER_SHAPE_UNSUPPORTED")
    price = amount(row.get("price"), nonnegative=True)
    if kind == "Limit" and (price <= 0 or tif != "GTC") or kind == "Market" and price != 0:
        raise MonitoringError("MONITORING_ORDER_ECONOMICS_INVALID")
    if (
        parsed.status in ("Cancelled", "Rejected")
        and parsed.cumulative_filled == 0
        and window.end_ms - parsed.created_ms > 86400000
    ):
        raise MonitoringError("MONITORING_ZERO_FILL_RETENTION")
    return MonitoringOrder(
        parsed.client_order_id,
        parsed.venue_order_id,
        symbol,
        parsed.side,
        str(kind),
        str(tif),
        parsed.quantity,
        None if kind == "Market" else price,
        parsed.reduce_only,
        parsed.status,
        parsed.cumulative_filled,
        parsed.leaves_quantity,
        parsed.created_ms,
        parsed.updated_ms,
        source,
    )


def parse_execution(
    row: dict[str, object], symbol: str, window: HistoryWindow
) -> MonitoringExecution:
    parsed = _execution(row, symbol, window)
    seq, maker = row.get("seq"), row.get("isMaker")
    if (
        parsed.execution_type != "Trade"
        or not parsed.client_order_id
        or type(maker) is not bool
        or type(seq) not in (int, str)
        or not str(seq).isdigit()
        or len(str(seq)) > 20
    ):
        raise MonitoringError("MONITORING_EXECUTION_SHAPE_UNSUPPORTED")
    with localcontext() as context:
        context.prec = 100
        if parsed.value != parsed.quantity * parsed.price:
            raise MonitoringError("MONITORING_EXECUTION_VALUE_MISMATCH")
    return MonitoringExecution(
        parsed.execution_id,
        parsed.client_order_id,
        parsed.venue_order_id,
        symbol,
        parsed.side,
        parsed.quantity,
        parsed.price,
        parsed.fee,
        parsed.fee_currency,
        parsed.time_ms * 1_000_000,
        "MAKER" if maker else "TAKER",
        parsed.execution_type,
        int(str(seq)),
        parsed.value,
        parsed.fee_rate,
        parsed.closed_quantity,
    )


class BybitMonitoringReader:
    def __init__(
        self,
        session: BybitDemoAccountSession,
        binding: AuthenticatedAccountBinding,
        *,
        target: MonitoringTarget,
        guard: Callable[[], None],
    ) -> None:
        self.session, self.binding, self.target, self.guard = session, binding, target, guard
        self.times: list[int] = []
        self.local_times: list[int] = []
        self.deadline = time.monotonic() + 20
        self.logical = 0
        self.last_wall = utc(session._clock())

    def check(self) -> None:
        self.guard()
        self.session.require_observation_binding(self.binding, scope=self.target.scope)
        wall = utc(self.session._clock())
        if wall < self.last_wall or time.monotonic() >= self.deadline:
            raise MonitoringError("MONITORING_READ_CLOCK_OR_DEADLINE")
        self.last_wall = wall

    async def get(
        self, path: str, params: list[tuple[str, str]], *, signed: bool = True
    ) -> dict[str, object]:
        self.check()
        if self.logical >= 32:
            raise MonitoringError("MONITORING_LOGICAL_GET_LIMIT")
        self.logical += 1
        client = self.session.read_client(self.binding, scope=self.target.scope)
        result, stamp = await client.read(path, params, signed=signed, deadline=self.deadline)
        self.check()
        if type(stamp) is not int or stamp <= 0:
            raise MonitoringError("MONITORING_RESPONSE_TIME_REQUIRED")
        now = utc(self.session._clock())
        if (
            not timedelta(0)
            <= now - (EPOCH + timedelta(milliseconds=stamp))
            <= min(self.session._max_age, timedelta(seconds=30))
            or self.times
            and stamp < self.times[-1]
        ):
            raise MonitoringError("MONITORING_RESPONSE_TIME_INVALID")
        self.times.append(stamp)
        elapsed = now - EPOCH
        self.local_times.append(
            elapsed.days * 86400000 + elapsed.seconds * 1000 + elapsed.microseconds // 1000
        )
        return result

    async def pages(
        self, path: str, symbol: str, extra: list[tuple[str, str]]
    ) -> list[dict[str, object]]:
        cursor, seen, rows = "", set(), []
        for _ in range(5):
            params = [("category", "linear"), ("symbol", symbol), ("limit", "50"), *extra]
            if cursor:
                params.append(("cursor", cursor))
            result = await self.get(path, params)
            page, cursor_value = result.get("list"), result.get("nextPageCursor")
            if (
                result.get("category") != "linear"
                or type(page) is not list
                or len(page) > 50
                or type(cursor_value) is not str
                or len(cursor_value) > 512
                or cursor_value
                and (not cursor_value.isascii() or any(c.isspace() for c in cursor_value))
            ):
                raise MonitoringError("MONITORING_PAGE_INVALID")
            for row in page:
                if type(row) is not dict or row.get("symbol") != symbol:
                    raise MonitoringError("MONITORING_PAGE_SCOPE_INVALID")
                rows.append(row)
            if not cursor_value:
                return rows
            if not page or cursor_value in seen:
                raise MonitoringError("MONITORING_CURSOR_INVALID")
            seen.add(cursor_value)
            cursor = cursor_value
        raise MonitoringError("MONITORING_PAGE_CAP")

    async def read(self) -> MonitoringObservation:
        client = self.session.read_client(self.binding, scope=self.target.scope)
        active = client._operation.get()
        budget = (
            active
            if isinstance(active, MonitoringReadBudget)
            else MonitoringReadBudget(guard=self.check)
        )
        with client._monitoring_operation(budget), budget.receipt(guard=self.check):
            await self.get("/v5/market/time", [], signed=False)
            end = self.times[0]
            retention = HistoryWindow(max(1, end - MAX_WINDOW_MS), end)
            orders: dict[str, MonitoringOrder] = {}
            executions: dict[tuple[str, str], MonitoringExecution] = {}
            positions: list[MonitoringPosition] = []
            windows: list[tuple[str, int, int]] = []
            for symbol in self.target.symbols:
                position_rows = await self.pages("/v5/position/list", symbol, [])
                if len(position_rows) != 1:
                    raise MonitoringError("MONITORING_EXPLICIT_POSITION_REQUIRED")
                position = position_rows[0]
                size = amount(position.get("size"), nonnegative=True)
                side = position.get("side")
                if (
                    type(position.get("positionIdx")) is not int
                    or position.get("positionIdx") != 0
                    or size > 0
                    and side not in ("Buy", "Sell")
                    or size == 0
                    and side not in ("", "Buy", "Sell")
                ):
                    raise MonitoringError("MONITORING_ONE_WAY_POSITION_REQUIRED")
                positions.append(MonitoringPosition(symbol, size * (-1 if side == "Sell" else 1)))
                for path, source, extra in (
                    ("/v5/order/realtime", "current", [("openOnly", "0")]),
                    (
                        "/v5/order/history",
                        "history",
                        [("startTime", str(retention.start_ms)), ("endTime", str(end))],
                    ),
                ):
                    for raw in await self.pages(path, symbol, extra):
                        row = parse_order(raw, symbol, retention, source)
                        prior = orders.get(row.client_order_id)
                        if prior is not None:
                            if replace(row, source=prior.source) != prior:
                                raise MonitoringError("MONITORING_DUPLICATE_ORDER_CONFLICT")
                        else:
                            orders[row.client_order_id] = row
                own = tuple(row for row in self.target.native.orders if row.symbol == symbol)
                positive = []
                for native in own:
                    positive_row = orders.get(native.client_order_id)
                    if positive_row is None or positive_row.venue_order_id != native.venue_order_id:
                        raise MonitoringError("MONITORING_POSITIVE_ORDER_REQUIRED")
                    if any(
                        getattr(positive_row, key) != getattr(native, key)
                        for key in (
                            "symbol",
                            "side",
                            "order_type",
                            "quantity",
                            "price",
                            "reduce_only",
                        )
                    ):
                        raise MonitoringError("MONITORING_POSITIVE_ORDER_SHAPE_MISMATCH")
                    expected_tif = (
                        "IOC"
                        if native.order_type == "Market" and native.time_in_force == "GTC"
                        else native.time_in_force
                    )
                    if positive_row.time_in_force != expected_tif:
                        raise MonitoringError("MONITORING_POSITIVE_ORDER_SHAPE_MISMATCH")
                    positive.append(positive_row.created_ms)
                start = min(positive) if positive else retention.start_ms
                window = HistoryWindow(start, end)
                windows.append((symbol, start, end))
                for raw in await self.pages(
                    "/v5/execution/list", symbol, [("startTime", str(start)), ("endTime", str(end))]
                ):
                    execution = parse_execution(raw, symbol, window)
                    if not any(
                        row.client_order_id == execution.client_order_id
                        and row.venue_order_id == execution.venue_order_id
                        for row in own
                    ):
                        raise MonitoringError("MONITORING_UNOWNED_EXECUTION")
                    owned = next(
                        row for row in own if row.client_order_id == execution.client_order_id
                    )
                    if (
                        raw.get("orderType") != owned.order_type
                        or raw.get("stopOrderType") != ""
                        or amount(raw.get("orderQty"), positive=True) != owned.quantity
                        or amount(raw.get("orderPrice"), nonnegative=True)
                        != (owned.price or Decimal(0))
                        or amount(raw.get("leavesQty"), nonnegative=True) > owned.quantity
                        or execution.closed_quantity
                        != (execution.quantity if owned.reduce_only else Decimal(0))
                    ):
                        raise MonitoringError("MONITORING_EXECUTION_ORDER_SHAPE_MISMATCH")
                    key = (symbol, execution.execution_id)
                    if key in executions and executions[key] != execution:
                        raise MonitoringError("MONITORING_DUPLICATE_EXECUTION_CONFLICT")
                    executions[key] = execution
            self.check()
            return MonitoringObservation(
                self.target.scope,
                self.target.symbols,
                tuple(orders.values()),
                tuple(executions.values()),
                tuple(positions),
                tuple(self.times),
                tuple(windows),
                tuple(self.local_times),
            )
