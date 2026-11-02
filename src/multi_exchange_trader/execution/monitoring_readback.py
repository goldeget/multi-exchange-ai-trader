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


def _validate_positive_order(native: MonitoringOrder, row: MonitoringOrder) -> None:
    if (row.client_order_id != native.client_order_id
            or row.venue_order_id != native.venue_order_id):
        raise MonitoringError("MONITORING_POSITIVE_ORDER_REQUIRED")
    if any(
        getattr(row, key) != getattr(native, key)
        for key in ("symbol", "side", "order_type", "quantity", "price", "reduce_only")
    ):
        raise MonitoringError("MONITORING_POSITIVE_ORDER_SHAPE_MISMATCH")
    expected_tif = (
        "IOC" if native.order_type == "Market" and native.time_in_force == "GTC"
        else native.time_in_force
    )
    if row.time_in_force != expected_tif:
        raise MonitoringError("MONITORING_POSITIVE_ORDER_SHAPE_MISMATCH")


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
        owner_guard: Callable[[], None] = lambda: None,
    ) -> None:
        self.session, self.binding, self.target, self.guard = session, binding, target, guard
        self.owner_guard = owner_guard
        self.times: list[int] = []
        self.local_times: list[int] = []
        self.deadline = time.monotonic() + 20
        self.logical = 0
        self.last_wall = utc(session._clock())

    def require_owner(self) -> None:
        self.owner_guard()
        self.session.require_observation_binding(self.binding, scope=self.target.scope)
        wall = utc(self.session._clock())
        if wall < self.last_wall or time.monotonic() >= self.deadline:
            raise MonitoringError("MONITORING_READ_CLOCK_OR_DEADLINE")
        self.last_wall = wall

    def check(self) -> None:
        self.require_owner()
        self.guard()

    async def get(
        self, path: str, params: list[tuple[str, str]], *, signed: bool = True,
        validate: Callable[[dict[str, object]], None] = lambda _: None,
    ) -> dict[str, object]:
        self.check()
        if self.logical >= 32:
            raise MonitoringError("MONITORING_LOGICAL_GET_LIMIT")
        self.logical += 1
        client = self.session.read_client(self.binding, scope=self.target.scope)
        budget = client._operation.get()
        if not isinstance(budget, MonitoringReadBudget):
            raise MonitoringError("MONITORING_BUDGET_REQUIRED")

        def validate_response(result: dict[str, object], stamp: int | None) -> None:
            if type(stamp) is not int or stamp <= 0:
                raise MonitoringError("MONITORING_RESPONSE_TIME_REQUIRED")
            now = utc(self.session._clock())
            if (
                not timedelta(0)
                <= now - (EPOCH + timedelta(milliseconds=stamp))
                <= min(self.session._max_age, timedelta(seconds=30))
                or self.times and stamp < self.times[-1]
            ):
                raise MonitoringError("MONITORING_RESPONSE_TIME_INVALID")
            # Page and full economic row validation is synchronous and touches
            # only this read's scratch data, before a mutable target may retarget.
            validate(result)
            self.times.append(stamp)
            elapsed = now - EPOCH
            self.local_times.append(
                elapsed.days * 86400000 + elapsed.seconds * 1000 + elapsed.microseconds // 1000
            )

        with budget.validating_response(validate_response):
            result, _ = await client.read(path, params, signed=signed, deadline=self.deadline)
        self.check()
        return result

    async def pages(
        self, path: str, symbol: str, extra: list[tuple[str, str]], *,
        consume: Callable[[dict[str, object]], None],
        complete: Callable[[list[dict[str, object]]], None] = lambda _: None,
    ) -> None:
        cursor, seen, rows = "", set(), []
        for page_number in range(5):
            params = [("category", "linear"), ("symbol", symbol), ("limit", "50"), *extra]
            if cursor:
                params.append(("cursor", cursor))

            def validate_page(result: dict[str, object], page_number: int = page_number) -> None:
                nonlocal cursor
                page, cursor_value = result.get("list"), result.get("nextPageCursor")
                if (
                    result.get("category") != "linear"
                    or type(page) is not list or len(page) > 50
                    or type(cursor_value) is not str or len(cursor_value) > 512
                    or cursor_value and (not cursor_value.isascii()
                        or any(c.isspace() for c in cursor_value))
                ):
                    raise MonitoringError("MONITORING_PAGE_INVALID")
                if cursor_value and (not page or cursor_value in seen):
                    raise MonitoringError("MONITORING_CURSOR_INVALID")
                if cursor_value and page_number == 4:
                    raise MonitoringError("MONITORING_PAGE_CAP")
                for row in page:
                    if type(row) is not dict or row.get("symbol") != symbol:
                        raise MonitoringError("MONITORING_PAGE_SCOPE_INVALID")
                    consume(row)
                    rows.append(row)
                if not cursor_value:
                    complete(rows)
                else:
                    seen.add(cursor_value)
                cursor = cursor_value

            await self.get(path, params, validate=validate_page)
            if not cursor:
                return
        raise MonitoringError("MONITORING_PAGE_CAP")

    async def read(self) -> MonitoringObservation:
        client = self.session.read_client(self.binding, scope=self.target.scope)
        active = client._operation.get()
        budget = (
            active
            if isinstance(active, MonitoringReadBudget)
            else MonitoringReadBudget(guard=self.require_owner)
        )
        with client._monitoring_operation(budget), budget.receipt(
            guard=self.check, owner_guard=self.require_owner
        ):
            await self.get("/v5/market/time", [], signed=False)
            end = self.times[0]
            retention = HistoryWindow(max(1, end - MAX_WINDOW_MS), end)
            orders: dict[str, MonitoringOrder] = {}
            executions: dict[tuple[str, str], MonitoringExecution] = {}
            positions: list[MonitoringPosition] = []
            windows: list[tuple[str, int, int]] = []
            for symbol in self.target.symbols:
                position_start = len(positions)

                def consume_position(position: dict[str, object], symbol: str = symbol,
                                     position_start: int = position_start) -> None:
                    if len(positions) != position_start:
                        raise MonitoringError("MONITORING_EXPLICIT_POSITION_REQUIRED")
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

                def complete_position(rows: list[dict[str, object]]) -> None:
                    if len(rows) != 1:
                        raise MonitoringError("MONITORING_EXPLICIT_POSITION_REQUIRED")

                await self.pages("/v5/position/list", symbol, [],
                    consume=consume_position, complete=complete_position)
                for path, source, extra in (
                    ("/v5/order/realtime", "current", [("openOnly", "0")]),
                    (
                        "/v5/order/history",
                        "history",
                        [("startTime", str(retention.start_ms)), ("endTime", str(end))],
                    ),
                ):
                    def consume_order(raw: dict[str, object], symbol: str = symbol,
                                      source: str = source) -> None:
                        row = parse_order(raw, symbol, retention, source)
                        for native in self.target.native.orders:
                            if (native.client_order_id == row.client_order_id
                                    or native.venue_order_id == row.venue_order_id):
                                _validate_positive_order(native, row)
                        prior = orders.get(row.client_order_id)
                        if prior is not None:
                            if replace(row, source=prior.source) != prior:
                                raise MonitoringError("MONITORING_DUPLICATE_ORDER_CONFLICT")
                        else:
                            orders[row.client_order_id] = row
                    await self.pages(path, symbol, extra, consume=consume_order)
                own = tuple(row for row in self.target.native.orders if row.symbol == symbol)
                positive = []
                for native in own:
                    positive_row = orders.get(native.client_order_id)
                    if positive_row is None:
                        raise MonitoringError("MONITORING_POSITIVE_ORDER_REQUIRED")
                    _validate_positive_order(native, positive_row)
                    positive.append(positive_row.created_ms)
                start = min(positive) if positive else retention.start_ms
                window = HistoryWindow(start, end)
                windows.append((symbol, start, end))
                def consume_execution(raw: dict[str, object], symbol: str = symbol,
                                      window: HistoryWindow = window,
                                      own: tuple[MonitoringOrder, ...] = own) -> None:
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
                await self.pages("/v5/execution/list", symbol,
                    [("startTime", str(start)), ("endTime", str(end))], consume=consume_execution)
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
