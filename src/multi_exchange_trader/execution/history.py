"""Authenticated, bounded GET history; page exhaustion is not event completeness.

Schemas verified 2026-11-02 against Bybit's primary documentation:
https://bybit-exchange.github.io/docs/v5/order/execution
https://bybit-exchange.github.io/docs/v5/order/order-list
https://bybit-exchange.github.io/docs/v5/account/transaction-log
https://bybit-exchange.github.io/docs/v5/market/risk-limit
https://bybit-exchange.github.io/docs/v5/market/instrument
https://bybit-exchange.github.io/docs/v5/enum#type-uta-translog

All history endpoints allow at most seven days per explicit request. Order
history is eventually consistent and cancelled/rejected records have a shorter
retention period. Empty results never prove flatness or absence of late fills.
The supported accounting domain is USDT linear; other economic activity blocks
account-wide accounting instead of being filtered away. Delivery executions block
until their closing cash flow and fee counterpart schema is supported. No deployment authority
is issued here, including when the authenticated session uses a real transport.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Literal
from uuid import uuid4

from .account_binding import AuthenticatedAccountBinding, BybitDemoAccountSession
from .protection import ProtectionScope
from .risk import RiskTier

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MAX_WINDOW_MS = 7 * 86400 * 1000


class HistoryError(ValueError):
    """Fixed local failure codes; provider payloads never escape."""


@dataclass(frozen=True)
class HistoryWindow:
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if (type(self.start_ms) is not int or type(self.end_ms) is not int
                or not 0 < self.start_ms <= self.end_ms < 253402300800000
                or self.end_ms - self.start_ms > MAX_WINDOW_MS):
            raise HistoryError("HISTORY_WINDOW_INVALID")


@dataclass(frozen=True)
class ExecutionRecord:
    execution_id: str
    venue_order_id: str
    client_order_id: str
    symbol: str
    side: str
    execution_type: str
    time_ms: int
    quantity: Decimal
    price: Decimal
    value: Decimal
    fee: Decimal
    fee_currency: str
    fee_rate: Decimal
    closed_quantity: Decimal


@dataclass(frozen=True)
class OrderHistoryRecord:
    venue_order_id: str
    client_order_id: str
    symbol: str
    side: str
    position_idx: int
    status: str
    quantity: Decimal
    cumulative_filled: Decimal
    leaves_quantity: Decimal
    reduce_only: bool
    created_ms: int
    updated_ms: int


@dataclass(frozen=True)
class TransactionRecord:
    transaction_id: str
    trade_id: str
    venue_order_id: str
    symbol: str
    category: str
    currency: str
    transaction_type: str
    time_ms: int
    cash_flow: Decimal
    funding: Decimal
    fee: Decimal
    change: Decimal
    cash_balance: Decimal

    @property
    def is_transfer(self) -> bool:
        return self.transaction_type in {"TRANSFER_IN", "TRANSFER_OUT"}


@dataclass(frozen=True)
class HistorySnapshot:
    scope: ProtectionScope
    symbol: str
    snapshot_id: str
    window: HistoryWindow
    observed_at: datetime
    server_observed_at: datetime
    executions: tuple[ExecutionRecord, ...]
    orders: tuple[OrderHistoryRecord, ...]
    transactions: tuple[TransactionRecord, ...]
    transport_injected: bool
    page_complete: Literal[True] = field(default=True, init=False)
    transactions_account_wide: Literal[True] = field(default=True, init=False)
    venue_event_complete: Literal[False] = field(default=False, init=False)
    flat_verified: Literal[False] = field(default=False, init=False)


@dataclass(frozen=True)
class RiskLimitSnapshot:
    scope: ProtectionScope
    symbol: str
    snapshot_id: str
    observed_at: datetime
    server_observed_at: datetime
    min_leverage: Decimal
    max_leverage: Decimal
    leverage_step: Decimal
    tiers: tuple[RiskTier, ...]
    transport_injected: bool
    page_complete: Literal[True] = field(default=True, init=False)
    qualified: Literal[False] = field(default=False, init=False)


def utc(value: datetime) -> datetime:
    if (type(value) is not datetime or value.tzinfo is None
            or value.utcoffset() != timedelta(0)):
        raise HistoryError("HISTORY_UTC_REQUIRED")
    return value


def milliseconds(value: datetime) -> int:
    delta = utc(value) - EPOCH
    if value.microsecond % 1000:
        raise HistoryError("HISTORY_MILLISECOND_CLOCK_REQUIRED")
    return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000


def amount(value: object, *, nonnegative: bool = False, positive: bool = False) -> Decimal:
    # Bound arithmetic precision independently of the process Decimal context.
    if type(value) is not str or re.fullmatch(r"-?[0-9]{1,30}(?:\.[0-9]{1,18})?", value) is None:
        raise HistoryError("HISTORY_DECIMAL_REQUIRED")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise HistoryError("HISTORY_DECIMAL_REQUIRED") from None
    if (not result.is_finite() or (nonnegative and result < 0) or (positive and result <= 0)):
        raise HistoryError("HISTORY_DECIMAL_REQUIRED")
    return result


def _text(value: object, *, empty: bool = False) -> str:
    if (type(value) is not str or len(value) > 256 or (not value and not empty)
            or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise HistoryError("HISTORY_TEXT_INVALID")
    return value


def _symbol(value: object) -> str:
    text = _text(value)
    if re.fullmatch(r"[A-Z0-9]{2,48}", text) is None:
        raise HistoryError("HISTORY_SYMBOL_INVALID")
    return text


def _timestamp(value: object) -> int:
    if type(value) is not str or re.fullmatch(r"[0-9]{1,15}", value) is None:
        raise HistoryError("HISTORY_TIME_INVALID")
    result = int(value)
    if not 0 < result < 253402300800000:
        raise HistoryError("HISTORY_TIME_INVALID")
    return result


def _in_window(value: object, window: HistoryWindow) -> int:
    timestamp = _timestamp(value)
    if not window.start_ms <= timestamp <= window.end_ms:
        raise HistoryError("HISTORY_TIME_OUTSIDE_WINDOW")
    return timestamp


def _side(value: object) -> str:
    if value not in ("Buy", "Sell"):
        raise HistoryError("HISTORY_SIDE_INVALID")
    return str(value)


def _execution(row: dict[str, object], symbol: str, window: HistoryWindow) -> ExecutionRecord:
    if row.get("symbol") != symbol:
        raise HistoryError("HISTORY_SYMBOL_MISMATCH")
    kind = _text(row.get("execType"))
    if kind == "Delivery":
        # This is a position-closing economic event, not an ignorable history row.
        raise HistoryError("HISTORY_DELIVERY_ACCOUNTING_UNSUPPORTED")
    if kind not in {"Trade", "AdlTrade", "BustTrade", "Funding"}:
        raise HistoryError("HISTORY_EXECUTION_TYPE_UNSUPPORTED")
    # Funding execFee has the opposite sign from transaction-log funding.
    funding = kind == "Funding"
    quantity = amount(row.get("execQty"), nonnegative=True, positive=not funding)
    closed = amount(row.get("closedSize"), nonnegative=True)
    if closed > quantity:
        raise HistoryError("HISTORY_CLOSED_QUANTITY_INVALID")
    if row.get("feeCurrency") != "USDT":
        raise HistoryError("HISTORY_FEE_CURRENCY_UNSUPPORTED")
    return ExecutionRecord(_text(row.get("execId")),
        _text(row.get("orderId"), empty=funding), _text(row.get("orderLinkId"), empty=True),
        symbol, _side(row.get("side")), kind, _in_window(row.get("execTime"), window),
        quantity, amount(row.get("execPrice"), nonnegative=True, positive=not funding),
        amount(row.get("execValue"), nonnegative=True), amount(row.get("execFee")), "USDT",
        amount(row.get("feeRate")), closed)


def _order(row: dict[str, object], symbol: str, window: HistoryWindow) -> OrderHistoryRecord:
    idx, reduce = row.get("positionIdx"), row.get("reduceOnly")
    if (row.get("symbol") != symbol or type(idx) is not int or idx not in (0, 1, 2)
            or type(reduce) is not bool):
        raise HistoryError("HISTORY_ORDER_SCOPE_INVALID")
    status = _text(row.get("orderStatus"))
    if status not in {"New", "PartiallyFilled", "Untriggered", "Rejected", "PartiallyFilledCanceled",
                      "Filled", "Cancelled", "Triggered", "Deactivated"}:
        raise HistoryError("HISTORY_ORDER_STATUS_INVALID")
    quantity = amount(row.get("qty"), positive=True)
    filled = amount(row.get("cumExecQty"), nonnegative=True)
    leaves = amount(row.get("leavesQty"), nonnegative=True)
    created = _in_window(row.get("createdTime"), window)
    updated = _timestamp(row.get("updatedTime"))
    if filled > quantity or leaves > quantity or updated < created:
        raise HistoryError("HISTORY_ORDER_QUANTITY_OR_TIME_INVALID")
    with localcontext() as context:
        context.prec = 80
        if filled + leaves > quantity or (status == "Filled" and filled != quantity):
            raise HistoryError("HISTORY_ORDER_QUANTITY_OR_TIME_INVALID")
    return OrderHistoryRecord(_text(row.get("orderId")), _text(row.get("orderLinkId"), empty=True),
        symbol, _side(row.get("side")), idx, status, quantity, filled, leaves, reduce, created, updated)


def _transaction(row: dict[str, object], window: HistoryWindow) -> TransactionRecord:
    kind = _text(row.get("type"))
    transfer = kind in {"TRANSFER_IN", "TRANSFER_OUT"}
    if kind not in {"TRADE", "SETTLEMENT", "LIQUIDATION", "ADL", "TRANSFER_IN", "TRANSFER_OUT"}:
        raise HistoryError("HISTORY_TRANSACTION_TYPE_UNSUPPORTED")
    category = _text(row.get("category"), empty=transfer)
    symbol = _text(row.get("symbol"), empty=transfer)
    if row.get("currency") != "USDT" or (not transfer and category != "linear"):
        raise HistoryError("HISTORY_ACCOUNT_ACTIVITY_UNSUPPORTED")
    if not transfer:
        _symbol(symbol)
    if row.get("transSubType") != "":
        raise HistoryError("HISTORY_TRANSACTION_SUBTYPE_UNSUPPORTED")
    cash, fee, change, balance = (
        amount(row.get(key)) for key in ("cashFlow", "fee", "change", "cashBalance"))
    # Bybit's documented TRADE response uses "" for non-applicable funding.
    # Missing funding and blank amounts for every other type/field still block.
    funding_value = row.get("funding")
    funding = Decimal(0) if kind == "TRADE" and funding_value == "" else amount(funding_value)
    with localcontext() as context:
        context.prec = 80
        if change != cash + funding - fee:
            raise HistoryError("HISTORY_TRANSACTION_EQUATION_INVALID")
    if transfer and (fee != 0 or funding != 0 or symbol != ""
                     or (kind == "TRANSFER_IN" and cash <= 0)
                     or (kind == "TRANSFER_OUT" and cash >= 0)):
        raise HistoryError("HISTORY_TRANSFER_AMBIGUOUS")
    if (kind == "SETTLEMENT" and (cash != 0 or fee != 0)) or (kind != "SETTLEMENT" and funding != 0):
        raise HistoryError("HISTORY_FUNDING_AMBIGUOUS")
    return TransactionRecord(_text(row.get("id")), _text(row.get("tradeId"), empty=True),
        _text(row.get("orderId"), empty=True), symbol, category, "USDT", kind,
        _in_window(row.get("transactionTime"), window), cash, funding, fee, change, balance)


class BybitHistoryReader:
    """The session authenticates UID/host/credentials; no arbitrary client is accepted."""

    def __init__(self, session: BybitDemoAccountSession, binding: AuthenticatedAccountBinding, *,
                 scope: ProtectionScope, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 max_age: timedelta = timedelta(seconds=30), max_pages: int = 20) -> None:
        if (type(session) is not BybitDemoAccountSession or type(scope) is not ProtectionScope
                or type(max_age) is not timedelta or not timedelta(0) < max_age <= timedelta(minutes=5)
                or type(max_pages) is not int or not 1 <= max_pages <= 100):
            raise HistoryError("HISTORY_READER_CONFIGURATION_INVALID")
        self._session, self._binding, self.scope = session, binding, scope
        self._clock, self._max_age, self._max_pages = clock, max_age, max_pages
        session.require_binding(binding, scope=scope)

    def _fresh(self, observed: datetime) -> None:
        if not timedelta(0) <= utc(self._clock()) - observed <= self._max_age:
            raise HistoryError("HISTORY_STALE")

    async def _pages(self, path: str, params: list[tuple[str, str]], *, signed: bool,
                     category: bool, limit: int | None, deadline: float,
                     times: list[datetime]) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        seen: set[str] = set()
        cursor = ""
        for _ in range(self._max_pages):
            client = self._session.read_client(self._binding, scope=self.scope)
            query = list(params)
            if limit is not None:
                query.append(("limit", str(limit)))
            if cursor:
                query.append(("cursor", cursor))
            result, server_ms = await client.read(path, query, signed=signed, deadline=deadline)
            self._session.require_binding(self._binding, scope=self.scope)
            if type(server_ms) is not int or not 0 < server_ms < 253402300800000:
                raise HistoryError("HISTORY_SERVER_TIME_REQUIRED")
            observed = EPOCH + timedelta(milliseconds=server_ms)
            self._fresh(observed)
            times.append(observed)
            page = result.get("list")
            if ((category and result.get("category") != "linear") or type(page) is not list
                    or len(page) > (limit if limit is not None else 10000)):
                raise HistoryError("HISTORY_PAGE_INVALID")
            for row in page:
                if type(row) is not dict or any(type(key) is not str for key in row):
                    raise HistoryError("HISTORY_ROW_INVALID")
                rows.append(row)
            cursor = _text(result.get("nextPageCursor"), empty=True)
            if not cursor:
                return rows
            if cursor in seen or not page:
                raise HistoryError("HISTORY_CURSOR_INVALID")
            seen.add(cursor)
        raise HistoryError("HISTORY_TRUNCATED")

    async def read_history(self, symbol: str, *, start_ms: int, end_ms: int) -> HistorySnapshot:
        symbol = _symbol(symbol)
        window = HistoryWindow(start_ms, end_ms)
        clock_now = utc(self._clock())
        if end_ms > milliseconds(clock_now.replace(microsecond=clock_now.microsecond // 1000 * 1000)):
            # The end is an explicit watermark, never an assumed future window.
            raise HistoryError("HISTORY_FUTURE_WINDOW")
        times: list[datetime] = []
        deadline = time.monotonic() + min(20.0, self._max_age.total_seconds())
        interval = [("startTime", str(start_ms)), ("endTime", str(end_ms))]
        params = [("category", "linear"), ("symbol", symbol)] + interval
        try:
            executions = tuple(_execution(row, symbol, window) for row in await self._pages(
                "/v5/execution/list", params, signed=True, category=True, limit=100,
                deadline=deadline, times=times))
            orders = tuple(_order(row, symbol, window) for row in await self._pages(
                "/v5/order/history", params, signed=True, category=True, limit=50,
                deadline=deadline, times=times))
            # No category/currency/symbol/type filters: unsupported account activity blocks.
            transactions = tuple(_transaction(row, window) for row in await self._pages(
                "/v5/account/transaction-log", [("accountType", "UNIFIED")] + interval,
                signed=True, category=False, limit=50, deadline=deadline, times=times))
            for identities in ([item.execution_id for item in executions],
                               [item.venue_order_id for item in orders],
                               [item.transaction_id for item in transactions]):
                if len(set(identities)) != len(identities):
                    raise HistoryError("HISTORY_DUPLICATE_IDENTITY")
            order_map = {item.venue_order_id: item for item in orders}
            filled_by_order: dict[str, Decimal] = {}
            for execution in executions:
                order = order_map.get(execution.venue_order_id)
                if order is not None and (execution.client_order_id != order.client_order_id
                                           or execution.side != order.side):
                    raise HistoryError("HISTORY_EXECUTION_ORDER_CONFLICT")
                if order is not None and not order.created_ms <= execution.time_ms <= order.updated_ms:
                    raise HistoryError("HISTORY_EXECUTION_ORDER_TIME_CONFLICT")
                if execution.execution_type != "Funding":
                    with localcontext() as context:
                        context.prec = 100
                        filled_by_order[execution.venue_order_id] = (
                            filled_by_order.get(execution.venue_order_id, Decimal(0))
                            + execution.quantity)
            for order in orders:
                if (order.updated_ms > milliseconds(max(times))
                        or filled_by_order.get(order.venue_order_id, Decimal(0))
                        > order.cumulative_filled):
                    raise HistoryError("HISTORY_EXECUTION_ORDER_TOTAL_CONFLICT")
            identity = self._session.require_binding(self._binding, scope=self.scope)
            self._fresh(min(times))
            return HistorySnapshot(self.scope, symbol, uuid4().hex, window, utc(self._clock()),
                min(times), executions, orders, transactions, identity.transport_injected)
        except HistoryError:
            raise
        except Exception:  # noqa: BLE001, S110 - never expose provider payloads or credentials.
            pass
        raise HistoryError("HISTORY_READ_FAILED") from None

    async def read_risk_limits(self, symbol: str) -> RiskLimitSnapshot:
        symbol = _symbol(symbol)
        times: list[datetime] = []
        deadline = time.monotonic() + min(20.0, self._max_age.total_seconds())
        params = [("category", "linear"), ("symbol", symbol)]
        try:
            instruments = await self._pages("/v5/market/instruments-info", params, signed=False,
                category=True, limit=1, deadline=deadline, times=times)
            if (len(instruments) != 1 or instruments[0].get("symbol") != symbol
                    or instruments[0].get("status") != "Trading"
                    or instruments[0].get("settleCoin") != "USDT"):
                raise HistoryError("HISTORY_CURRENT_INSTRUMENT_REQUIRED")
            leverage = instruments[0].get("leverageFilter")
            if type(leverage) is not dict:
                raise HistoryError("HISTORY_LEVERAGE_FILTER_REQUIRED")
            minimum, maximum, step = (amount(leverage.get(key), positive=True) for key in
                                      ("minLeverage", "maxLeverage", "leverageStep"))
            if minimum > maximum:
                raise HistoryError("HISTORY_LEVERAGE_RANGE_INVALID")
            rows = await self._pages("/v5/market/risk-limit", params, signed=False,
                category=True, limit=None, deadline=deadline, times=times)
            tiers: list[RiskTier] = []
            ids: set[str] = set()
            lower = Decimal(0)
            # Sort by notional; IDs are identifiers, not guaranteed tier ordering.
            for row in sorted(rows, key=lambda row: amount(row.get("riskLimitValue"), positive=True)):
                rid = row.get("id")
                if type(rid) is not int or rid <= 0 or str(rid) in ids or row.get("symbol") != symbol:
                    raise HistoryError("HISTORY_RISK_TIER_IDENTITY_INVALID")
                ids.add(str(rid))
                upper = amount(row.get("riskLimitValue"), positive=True)
                max_leverage = amount(row.get("maxLeverage"), positive=True)
                # These two fields are JSON numbers by venue schema. Decimal(str(...))
                # preserves the decoded decimal spelling, with bounded finite validation.
                mmr, imr = (amount(str(row.get(key)), positive=True) for key in
                            ("maintenanceMargin", "initialMargin"))
                lowest = row.get("isLowestRisk")
                if (upper <= lower or not minimum <= max_leverage <= maximum
                        or not 0 < mmr <= imr <= 1 or type(lowest) is not int
                        or lowest != (1 if not tiers else 0)):
                    raise HistoryError("HISTORY_RISK_TIER_RANGE_INVALID")
                amount(row.get("mmDeduction"), nonnegative=True)
                tiers.append(RiskTier(str(rid), lower, upper, minimum, max_leverage, mmr))
                lower = upper
            if not tiers:
                raise HistoryError("HISTORY_RISK_TIERS_EMPTY")
            identity = self._session.require_binding(self._binding, scope=self.scope)
            self._fresh(min(times))
            return RiskLimitSnapshot(self.scope, symbol, uuid4().hex, utc(self._clock()), min(times),
                                     minimum, maximum, step, tuple(tiers), identity.transport_injected)
        except HistoryError:
            raise
        except Exception:  # noqa: BLE001, S110 - provider details must not escape.
            pass
        raise HistoryError("HISTORY_RISK_LIMIT_READ_FAILED") from None
