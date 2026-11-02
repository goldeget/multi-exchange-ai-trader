"""Validate original raw economics before any SDK cache substitution or event."""

from __future__ import annotations

import asyncio
import importlib
import json
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .history import HistoryWindow, amount
from .monitoring_models import (
    MonitoringError,
    MonitoringExecution,
    MonitoringOrder,
    MonitoringTarget,
)
from .monitoring_readback import parse_execution, parse_order
from .native_events import NativeOrderEvidence
from .private_monitor_stream import PrivateProvenance


def _identity(row: dict[str, Any], order: Any, instrument: Any) -> str:
    symbol = str(instrument.raw_symbol)
    if (
        row.get("category") != "linear"
        or row.get("symbol") != symbol
        or row.get("side") != ("Buy" if order.side.name == "BUY" else "Sell")
        or row.get("orderLinkId") != str(order.client_order_id)
        or row.get("orderId") != str(order.venue_order_id)
        or order.instrument_id != instrument.id
    ):
        raise MonitoringError("MONITORING_ORIGINAL_IDENTITY_MISMATCH")
    return symbol


def validate_execution(
    row: dict[str, Any], order: Any, instrument: Any, received_ms: int
) -> MonitoringExecution:
    symbol = _identity(row, order, instrument)
    execution = parse_execution(
        row, symbol, HistoryWindow(max(1, received_ms - 604800000), received_ms)
    )
    kind = "Limit" if order.order_type.name == "LIMIT" else "Market"
    price = amount(row.get("orderPrice"), nonnegative=True)
    if (
        row.get("orderType") != kind
        or row.get("stopOrderType") not in ("", "UNKNOWN")
        or row.get("createType") != "CreateByUser"
        or row.get("extraFees", "") not in ("", [])
        or row.get("blockTradeId", "") != ""
        or row.get("isLeverage", "0") not in ("", "0")
        or amount(row.get("orderQty"), positive=True) != order.quantity.as_decimal()
        or kind == "Limit"
        and price != order.price.as_decimal()
        or execution.quantity > order.quantity.as_decimal()
        # Fractions preserve the exact relationship even beyond Decimal's
        # ambient precision. Earlier fills may account for the remaining gap.
        or Fraction(execution.quantity) + Fraction(amount(row.get("leavesQty"), nonnegative=True))
        > Fraction(order.quantity.as_decimal())
        or execution.fee_currency != str(instrument.quote_currency)
        or execution.ts_event_ns < order.ts_init
        or execution.quantity % instrument.size_increment.as_decimal() != 0
        or execution.price % instrument.price_increment.as_decimal() != 0
    ):
        raise MonitoringError("MONITORING_ORIGINAL_EXECUTION_ECONOMICS")
    # Native constructors can round. Verify every constructed economic field.
    objects = importlib.import_module("nautilus_trader.model.objects")
    Money, Price, Quantity = objects.Money, objects.Price, objects.Quantity
    qty = Quantity.from_str(f"{execution.quantity:.{instrument.size_precision}f}")
    px = Price.from_str(f"{execution.price:.{instrument.price_precision}f}")
    fee = Money(execution.fee, instrument.quote_currency)
    if (
        qty.as_decimal() != execution.quantity
        or px.as_decimal() != execution.price
        or fee.as_decimal() != execution.fee
        or qty.precision > instrument.size_precision
        or px.precision > instrument.price_precision
    ):
        raise MonitoringError("MONITORING_ORIGINAL_PRECISION")
    return execution


def fill_report(
    execution: MonitoringExecution, order: Any, instrument: Any, received_ns: int
) -> Any:
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.execution.reports import FillReport
    from nautilus_trader.model.enums import LiquiditySide

    TradeId = importlib.import_module("nautilus_trader.model.identifiers").TradeId
    objects = importlib.import_module("nautilus_trader.model.objects")
    Money, Price, Quantity = objects.Money, objects.Price, objects.Quantity

    return FillReport(
        account_id=order.account_id,
        instrument_id=instrument.id,
        venue_order_id=order.venue_order_id,
        trade_id=TradeId(execution.execution_id),
        order_side=order.side,
        last_qty=Quantity.from_str(f"{execution.quantity:.{instrument.size_precision}f}"),
        last_px=Price.from_str(f"{execution.price:.{instrument.price_precision}f}"),
        commission=Money(execution.fee, instrument.quote_currency),
        liquidity_side=LiquiditySide.MAKER
        if execution.liquidity == "MAKER"
        else LiquiditySide.TAKER,
        report_id=UUID4(),
        ts_event=execution.ts_event_ns,
        ts_init=received_ns,
        client_order_id=order.client_order_id,
        venue_position_id=None,
    )


def _same(left: MonitoringExecution, right: MonitoringExecution) -> bool:
    fields = (
        "execution_id",
        "client_order_id",
        "venue_order_id",
        "symbol",
        "side",
        "quantity",
        "price",
        "fee",
        "fee_currency",
        "ts_event_ns",
        "liquidity",
        "execution_type",
    )
    return all(getattr(left, key) == getattr(right, key) for key in fields) and all(
        getattr(left, key) is None or getattr(left, key) == getattr(right, key)
        for key in ("seq", "value", "fee_rate", "closed_quantity")
    )


class ExecutionLedger:
    def __init__(self, originals: tuple[MonitoringExecution, ...]) -> None:
        self._applied = {(e.symbol, e.execution_id): e for e in originals}
        self._pending: dict[tuple[str, str], MonitoringExecution] = {}

    def reserve(self, execution: MonitoringExecution) -> bool:
        key = execution.symbol, execution.execution_id
        previous = self._applied.get(key) or self._pending.get(key)
        if previous is not None:
            if not _same(previous, execution):
                raise MonitoringError("MONITORING_EXECUTION_CONFLICT")
            return False
        if len(self._pending) >= 1024:
            raise MonitoringError("MONITORING_PENDING_LIMIT")
        self._pending[key] = execution
        return True

    def applied(self, execution: MonitoringExecution) -> None:
        key = execution.symbol, execution.execution_id
        if self._pending.get(key) is not execution:
            raise MonitoringError("MONITORING_PENDING_EXECUTION_REQUIRED")
        del self._pending[key]
        self._applied[key] = execution

    def originals(self) -> tuple[MonitoringExecution, ...]:
        return tuple(self._applied.values())


@dataclass(frozen=True)
class OriginalPrivateReceipt:
    provenance: PrivateProvenance
    original_json: str
    execution: MonitoringExecution | None


class OwnedPrivateReports:
    """Client-private event source, retaining exact originals through both ACKs."""

    def __init__(self, client: Any, target: MonitoringTarget) -> None:
        self._client, self._target = client, target
        assert target._witness is not None
        self._witness = target._witness
        self._cache = self._witness._assembly.node.cache
        self._owners = {
            r.request.owned.client_order_id: r.request for r in target.application.owned
        }
        self._orders = {str(order.client_order_id): order for order in self._cache.orders()}
        self._instruments = {str(i.id): i for i in self._cache.instruments()}
        self._ledger = ExecutionLedger(target.native.executions)
        self._receipts: list[OriginalPrivateReceipt] = []
        self._pending_status: dict[
            str, tuple[MonitoringOrder, asyncio.Future[None], asyncio.TimerHandle]
        ] = {}
        self._current: dict[str, Any] | None = None
        self._expected: dict[int, dict[str, Any]] = {}
        self._retired = False

    def retire(self) -> None:
        self._retired = True
        for _, future, timer in self._pending_status.values():
            timer.cancel()
            if not future.done():
                future.cancel()
        for pending in self._expected.values():
            if not pending["future"].done():
                pending["future"].cancel()

    def _guard(self) -> None:
        if self._retired:
            raise MonitoringError("MONITORING_REPORTS_RETIRED")
        self._client._event_guard()
        self._owners_guard()

    def _accepted_guard(self) -> None:
        """Already-posted exact events may finish while their owner is stopping."""
        witness, worker = self._witness, self._witness._worker
        if (
            self._retired
            or asyncio.get_running_loop() is not witness._loop
            or worker._runtime is not witness._runtime
            or worker._assembly is not witness._assembly
            or worker.session is not witness._session
            or worker._lock is not witness._lock
            or witness._assembly.channel is not witness._channel
            or not witness._lock.acquired
            or witness._lock.acquisition_id != witness._lease
            or witness._assembly.execution_client is not self._client
        ):
            raise MonitoringError("MONITORING_ACCEPTED_OWNER_CHANGED")
        self._client._require_event_session()
        witness._runtime._guard()
        self._owners_guard()

    def _owners_guard(self) -> None:
        # Exact durable request identity/economics remain fixed; command ACK and
        # genuine native event states can change without freezing the PRE digest.
        venues = tuple(
            (str(o.client_order_id), str(o.venue_order_id)) for o in self._orders.values()
        )
        current = self._witness._runtime.native_owned_requests(venues)
        if {r.request.owned.client_order_id: r.request for r in current} != self._owners:
            raise MonitoringError("MONITORING_DURABLE_OWNER_CHANGED")
        for order in self._orders.values():
            if self._cache.order(order.client_order_id) is not order:
                raise MonitoringError("MONITORING_NATIVE_ORDER_REPLACED")
        for instrument in self._instruments.values():
            if self._cache.instrument(instrument.id) is not instrument:
                raise MonitoringError("MONITORING_NATIVE_INSTRUMENT_REPLACED")

    async def drain_accepted(self) -> bool:
        current = self._current
        if current is None:
            return True
        try:
            async with asyncio.timeout(5):
                await asyncio.shield(current["future"])
            return True
        except TimeoutError:
            self._client._fault("MONITORING_ACCEPTED_DELIVERY_UNRESOLVED")
            self.retire()
            return False
        except asyncio.CancelledError:
            if current["future"].cancelled():
                self.retire()
                return False
            raise

    def _order(self, row: dict[str, Any]) -> tuple[Any, Any]:
        order = self._orders.get(str(row.get("orderLinkId")))
        if order is None:
            raise MonitoringError("MONITORING_UNOWNED_ORIGINAL")
        instrument = self._instruments.get(str(order.instrument_id))
        owner = self._owners.get(str(order.client_order_id))
        if (
            instrument is None
            or owner is None
            or str(order.trader_id) != self._witness._trader_id
            or str(order.strategy_id) != str(self._witness._assembly.strategy.id)
            or str(order.account_id) != self._witness._channel.native_account_id
            or owner.owned.instrument_id != str(instrument.id)
            or owner.owned.side != order.side.name
            or owner.owned.quantity != order.quantity.as_decimal()
            or owner.owned.reduce_only != order.is_reduce_only
        ):
            raise MonitoringError("MONITORING_OWNED_NATIVE_IDENTITY")
        _identity(row, order, instrument)
        return order, instrument

    async def dispatch(
        self, topic: str, rows: list[dict[str, Any]], provenance: PrivateProvenance
    ) -> asyncio.Future[Any] | None:
        try:
            self._client._monitor_stream._claim_dispatch(topic, rows, provenance)
            self._guard()
            stamp = self._client.monitoring_stamp()
            if (provenance.epoch, provenance.conn_id) != (stamp.epoch, stamp.conn_id):
                raise MonitoringError("MONITORING_ORIGINAL_EPOCH")
            delayed: list[asyncio.Future[None]] = []
            for row in rows:
                self._guard()
                if topic == "position.linear":
                    self._position(row)
                    continue
                order, instrument = self._order(row)
                if topic == "execution.linear":
                    execution = validate_execution(
                        row, order, instrument, provenance.received_ns // 1_000_000
                    )
                    if self._ledger.reserve(execution):
                        self._receipts.append(
                            OriginalPrivateReceipt(
                                provenance,
                                json.dumps(row, sort_keys=True, separators=(",", ":")),
                                execution,
                            )
                        )
                        await self._fill(execution, order, instrument, provenance.received_ns)
                        self._ledger.applied(execution)
                        await self._resolve_status(str(order.client_order_id))
                elif topic == "order.linear":
                    value = parse_order(
                        row,
                        str(instrument.raw_symbol),
                        HistoryWindow(
                            max(1, provenance.received_ns // 1_000_000 - 604800000),
                            provenance.received_ns // 1_000_000,
                        ),
                        "private",
                    )
                    self._validate_order(value, order)
                    self._receipts.append(
                        OriginalPrivateReceipt(
                            provenance, json.dumps(row, sort_keys=True, separators=(",", ":")), None
                        )
                    )
                    future = await self._status(value, order)
                    if future is not None:
                        delayed.append(future)
                else:
                    raise MonitoringError("MONITORING_TOPIC_UNSUPPORTED")
            return asyncio.gather(*delayed) if delayed else None
        except BaseException:
            self._client._fault("MONITORING_ORIGINAL_DELIVERY_FAILED")
            raise

    def _position(self, row: dict[str, Any]) -> None:
        size = amount(row.get("size"), nonnegative=True)
        if (
            row.get("category") != "linear"
            or row.get("symbol") not in self._target.symbols
            or type(row.get("positionIdx")) is not int
            or row["positionIdx"] != 0
            or size > 0
            and row.get("side") not in ("Buy", "Sell")
            or size == 0
            and row.get("side") not in ("", "Buy", "Sell")
        ):
            raise MonitoringError("MONITORING_PRIVATE_POSITION_UNSUPPORTED")
        # Observation only. Never infer a native flat/open/filled state.

    async def _fill(
        self, execution: MonitoringExecution, order: Any, instrument: Any, received_ns: int
    ) -> None:
        if (
            order.status.name not in ("ACCEPTED", "PARTIALLY_FILLED")
            or execution.quantity > order.leaves_qty.as_decimal()
        ):
            raise MonitoringError("MONITORING_EXECUTION_PREDECESSOR")
        position = self._cache.position_for_order(order.client_order_id)
        if position is not None:
            if (
                str(position.account_id) != str(order.account_id)
                or position.instrument_id != order.instrument_id
                or position.strategy_id != order.strategy_id
                or position.is_closed
            ):
                raise MonitoringError("MONITORING_POSITION_LIFETIME")
            opposite = (position.side.name == "LONG") != (order.side.name == "BUY")
            if opposite and (
                not order.is_reduce_only or execution.quantity > position.quantity.as_decimal()
            ):
                raise MonitoringError("MONITORING_POSITION_REVERSAL")
            if execution.closed_quantity != (execution.quantity if opposite else 0):
                raise MonitoringError("MONITORING_ORIGINAL_CLOSED_QUANTITY")
        elif order.is_reduce_only:
            raise MonitoringError("MONITORING_REDUCTION_POSITION_REQUIRED")
        elif execution.closed_quantity != 0 or self._cache.positions_open(
            instrument_id=order.instrument_id
        ):
            raise MonitoringError("MONITORING_POSITION_MAPPING_REQUIRED")
        report = fill_report(execution, order, instrument, received_ns)
        fields = {
            "strategy_id": order.strategy_id,
            "instrument_id": order.instrument_id,
            "client_order_id": order.client_order_id,
            "venue_order_id": order.venue_order_id,
            "venue_position_id": None,
            "trade_id": report.trade_id,
            "order_side": order.side,
            "order_type": order.order_type,
            "last_qty": report.last_qty,
            "last_px": report.last_px,
            "quote_currency": instrument.quote_currency,
            "commission": report.commission,
            "liquidity_side": report.liquidity_side,
            "ts_event": report.ts_event,
        }
        await self._emit("fill", fields, order, execution)

    def _validate_order(self, value: MonitoringOrder, order: Any) -> None:
        expected_tif = "IOC" if order.order_type.name == "MARKET" else order.time_in_force.name
        if (
            value.quantity != order.quantity.as_decimal()
            or value.reduce_only != order.is_reduce_only
            or value.order_type != ("Market" if order.order_type.name == "MARKET" else "Limit")
            or value.time_in_force != expected_tif
            or value.price
            != (None if order.order_type.name == "MARKET" else order.price.as_decimal())
            or value.cumulative < order.filled_qty.as_decimal()
            or value.status in ("New", "PartiallyFilled")
            and value.leaves + value.cumulative != value.quantity
            or value.status == "New"
            and value.cumulative != 0
            or value.status == "PartiallyFilled"
            and not 0 < value.cumulative < value.quantity
            or value.status in ("Filled", "Cancelled", "Rejected")
            and value.leaves != 0
        ):
            raise MonitoringError("MONITORING_PRIVATE_ORDER_UPDATE_UNSUPPORTED")

    async def _status(self, value: MonitoringOrder, order: Any) -> asyncio.Future[None] | None:
        if value.cumulative > order.filled_qty.as_decimal():
            key = str(order.client_order_id)
            prior = self._pending_status.get(key)
            if prior is not None:
                if prior[0] != value:
                    raise MonitoringError("MONITORING_PENDING_STATUS_CONFLICT")
                return prior[1]
            future = asyncio.get_running_loop().create_future()
            timer = asyncio.get_running_loop().call_later(5, self._terminal_timeout, key)
            self._pending_status[key] = value, future, timer
            return future
        expected = {
            "ACCEPTED": "New",
            "PARTIALLY_FILLED": "PartiallyFilled",
            "FILLED": "Filled",
            "CANCELED": "Cancelled",
            "REJECTED": "Rejected",
        }.get(order.status.name)
        if value.status == expected:
            return None
        if value.status == "Cancelled" and order.status.name in ("ACCEPTED", "PARTIALLY_FILLED"):
            await self._emit(
                "cancel",
                {
                    "strategy_id": order.strategy_id,
                    "instrument_id": order.instrument_id,
                    "client_order_id": order.client_order_id,
                    "venue_order_id": order.venue_order_id,
                    "ts_event": value.updated_ms * 1_000_000,
                },
                order,
                None,
            )
            return None
        raise MonitoringError("MONITORING_PRIVATE_STATUS_PREDECESSOR")

    def _terminal_timeout(self, key: str) -> None:
        self._client._fault("MONITORING_ORIGINAL_EXECUTIONS_MISSING")
        self.retire()

    async def _resolve_status(self, key: str) -> None:
        pending = self._pending_status.get(key)
        if pending is None or pending[0].cumulative != self._orders[key].filled_qty.as_decimal():
            return
        value, future, timer = self._pending_status.pop(key)
        timer.cancel()
        await self._status(value, self._orders[key])
        if not future.done():
            future.set_result(None)

    async def _emit(
        self, kind: str, fields: dict[str, Any], order: Any, execution: MonitoringExecution | None
    ) -> None:
        if self._current is not None:
            raise MonitoringError("MONITORING_SERIAL_DELIVERY_REQUIRED")
        future = asyncio.get_running_loop().create_future()
        expected = {
            "kind": kind,
            "order": order,
            "execution": execution,
            "future": future,
            "before_count": len(order.events),
            "native": False,
            "before_qty": order.filled_qty.as_decimal(),
        }
        position = self._cache.position_for_order(order.client_order_id)
        expected["before_position"] = (
            (position.quantity.as_decimal() * (-1 if position.side.name == "SHORT" else 1))
            if position is not None
            else 0
        )
        self._current = expected
        try:
            self._client._emit_original(kind, fields)
            if "event" not in expected:
                raise MonitoringError("MONITORING_NATIVE_EVENT_NOT_POSTED")
            async with asyncio.timeout(5):
                await future
            self._accepted_guard()
        finally:
            self._current = None
            if "event" in expected:
                self._expected.pop(id(expected["event"]), None)

    def expect_native(self, event: Any) -> None:
        current = self._current
        if current is None or "event" in current:
            raise MonitoringError("MONITORING_ORIGINAL_EVENT_REQUIRED")
        current["event"] = event
        self._expected[id(event)] = current

    def note_native(self, event: Any) -> None:
        self._accepted_guard()
        current = self._expected.get(id(event))
        if current is None or current["event"] is not event or current["native"]:
            self._client._fault("MONITORING_UNEXPECTED_NATIVE_EVENT")
            raise MonitoringError("MONITORING_UNEXPECTED_NATIVE_EVENT")
        order, execution = current["order"], current["execution"]
        if (
            len(order.events) != current["before_count"] + 1
            or order.events[-1] is not event
            or execution is not None
            and order.filled_qty.as_decimal() != current["before_qty"] + execution.quantity
            or execution is None
            and order.status.name != "CANCELED"
        ):
            self._client._fault("MONITORING_NATIVE_OUTCOME_MISMATCH")
            raise MonitoringError("MONITORING_NATIVE_OUTCOME_MISMATCH")
        if execution is not None:
            position = self._cache.position_for_order(order.client_order_id)
            delta = execution.quantity * (-1 if execution.side == "Sell" else 1)
            if (
                position is None
                or position.instrument_id != order.instrument_id
                or position.account_id != order.account_id
                or position.strategy_id != order.strategy_id
                or position.quantity.as_decimal() * (-1 if position.side.name == "SHORT" else 1)
                != current["before_position"] + delta
                or not any(item is event for item in position.events)
                or not self._cache.check_integrity()
            ):
                self._client._fault("MONITORING_NATIVE_POSITION_NOT_APPLIED")
                raise MonitoringError("MONITORING_NATIVE_POSITION_NOT_APPLIED")
        current["native"] = True

    def acknowledge(self, event: Any, evidence: object) -> None:
        self._accepted_guard()
        current = self._expected.get(id(event))
        if (
            current is None
            or current["event"] is not event
            or not current["native"]
            or current["future"].done()
            or type(evidence) is not NativeOrderEvidence
            or evidence.scope != self._target.scope
            or evidence.client_order_id != str(event.client_order_id)
            or evidence.venue_order_id != str(event.venue_order_id)
            or evidence.ts_event_ns != event.ts_event
        ):
            self._client._fault("MONITORING_APPLICATION_ACK_MISMATCH")
            raise MonitoringError("MONITORING_APPLICATION_ACK_MISMATCH")
        execution = current["execution"]
        if execution is not None:
            from .monitoring_reconciliation import _candidate_matches

            if (
                evidence.fill is None
                or not _candidate_matches(evidence.fill, str(event.venue_order_id), execution)
                or not any(
                    _candidate_matches(c.fill, c.venue_order_id, execution)
                    for c in self._witness._runtime.persisted_candidates()
                )
            ):
                self._client._fault("MONITORING_APPLICATION_FILL_NOT_QUARANTINED")
                raise MonitoringError("MONITORING_APPLICATION_FILL_NOT_QUARANTINED")
        elif evidence.kind != "CANCELED":
            raise MonitoringError("MONITORING_APPLICATION_CANCEL_OUTCOME")
        if self._witness._runtime.monitoring_state().conflicts:
            self._client._fault("MONITORING_APPLICATION_CONFLICT")
            raise MonitoringError("MONITORING_APPLICATION_CONFLICT")
        current["future"].set_result(None)
