"""Observed terminal history without a client identity, never managed ownership."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, fields
from decimal import Decimal, localcontext
from typing import TYPE_CHECKING, Literal

from .history import (
    MAX_WINDOW_MS,
    ExecutionRecord,
    HistoryWindow,
    OrderHistoryRecord,
    _execution,
    _order,
    amount,
)
from .monitoring_models import MonitoringError

if TYPE_CHECKING:
    from .fresh_monitoring_v1 import FreshMonitoringTargetV1
    from .monitoring_models import MonitoringObservation, MonitoringTarget


@dataclass(frozen=True, repr=False)
class UnattributedTerminalHistoryOrder:
    record: OrderHistoryRecord
    order_type: str
    time_in_force: str
    price: Decimal
    raw_row_sha256: str
    attribution: Literal["UNATTRIBUTED"] = field(default="UNATTRIBUTED", init=False)
    source: Literal["history"] = field(default="history", init=False)
    managed: Literal[False] = field(default=False, init=False)
    financial_qualified: Literal[False] = field(default=False, init=False)


@dataclass(frozen=True, repr=False)
class UnattributedHistoryExecution:
    record: ExecutionRecord
    is_maker: bool
    seq: int
    raw_row_sha256: str
    attribution: Literal["UNATTRIBUTED"] = field(default="UNATTRIBUTED", init=False)
    source: Literal["history"] = field(default="history", init=False)
    managed: Literal[False] = field(default=False, init=False)
    financial_qualified: Literal[False] = field(default=False, init=False)


def _terminal(record: OrderHistoryRecord, window: HistoryWindow) -> None:
    if (record.client_order_id != "" or record.position_idx != 0
            or record.status not in ("Filled", "Cancelled", "Rejected", "PartiallyFilledCanceled")
            or record.leaves_quantity != 0 or record.updated_ms > window.end_ms
            or record.status == "Rejected" and record.cumulative_filled != 0
            or record.status == "PartiallyFilledCanceled"
            and not 0 < record.cumulative_filled < record.quantity):
        raise MonitoringError("MONITORING_UNATTRIBUTED_TERMINAL_HISTORY_REQUIRED")


def parse_unattributed_terminal_history_order(
    row: dict[str, object], symbol: str, window: HistoryWindow,
) -> UnattributedTerminalHistoryOrder:
    """Only the original history path may call this; current/owned parsing stays strict."""
    record = _order(row, symbol, window)
    _terminal(record, window)
    kind, tif = row.get("orderType"), row.get("timeInForce")
    price = amount(row.get("price"), nonnegative=True)
    if (type(kind) is not str or kind not in ("Market", "Limit")
            or type(tif) is not str or tif not in ("GTC", "IOC")
            or kind == "Limit" and price <= 0):
        raise MonitoringError("MONITORING_UNATTRIBUTED_TERMINAL_HISTORY_REQUIRED")
    digest = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return UnattributedTerminalHistoryOrder(record, kind, tif, price, digest)


def parse_unattributed_history_execution(
    row: dict[str, object], symbol: str, window: HistoryWindow,
) -> UnattributedHistoryExecution:
    """Keep every original supported execution kind, including non-Trade events."""
    record = _execution(row, symbol, window)
    maker, seq = row.get("isMaker"), row.get("seq")
    if (record.client_order_id != "" or type(maker) is not bool
            or type(seq) not in (str, int) or not str(seq).isascii()
            or not str(seq).isdigit() or len(str(seq)) > 20):
        raise MonitoringError("MONITORING_UNATTRIBUTED_EXECUTION_REQUIRED")
    with localcontext() as context:
        context.prec = 100
        if record.execution_type == "Trade" and record.value != record.quantity * record.price:
            raise MonitoringError("MONITORING_EXECUTION_VALUE_MISMATCH")
    digest = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return UnattributedHistoryExecution(record, maker, int(str(seq)), digest)


def known_monitoring_venue_ids(target: MonitoringTarget | FreshMonitoringTargetV1) -> frozenset[str]:
    return frozenset((*(row.venue_order_id for row in target.native.orders),
        *(row.venue_order_id for row in target.native.executions),
        *(identifier for owned in target.application.owned for identifier in owned.venue_order_ids)))


def known_monitoring_execution_ids(
    target: MonitoringTarget | FreshMonitoringTargetV1,
) -> frozenset[tuple[str, str]]:
    return frozenset((row.symbol, row.execution_id) for row in target.native.executions)


def unresolved_monitoring_commands(target: MonitoringTarget | FreshMonitoringTargetV1) -> bool:
    for row in target.application.commands:
        if row.state in ("PENDING", "SENDING", "UNKNOWN"):
            return True
        if row.state == "ACKNOWLEDGED" and not row.terminal_confirmed:
            bindings = tuple(owned for owned in target.application.owned
                if owned.request.owned.client_order_id == row.client_order_id
                and owned.command_state == row.state)
            if (len(bindings) != 1 or type(bindings[0].venue_order_ids) is not frozenset
                    or not bindings[0].venue_order_ids
                    or any(type(identifier) is not str or not identifier
                        for identifier in bindings[0].venue_order_ids)):
                return True
    return False


def unattributed_history_sha256(observation: MonitoringObservation) -> str:
    """Closed typed data hash only; the original receipt privately retains the digest."""
    def scalar(value: object) -> object:
        if type(value) is str and len(value) <= 512 or type(value) in (bool, type(None)):
            return value
        if type(value) is int and -10**32 < value < 10**32:
            return value
        if (type(value) is Decimal and value.is_finite() and len(value.as_tuple().digits) <= 48
                and -18 <= int(value.as_tuple().exponent) <= 30):
            return ("decimal", format(value, "f"))
        raise MonitoringError("MONITORING_UNATTRIBUTED_HISTORY_INVALID")

    payload = []
    for rows, expected, record_type in (
        (observation.unattributed_history_orders, UnattributedTerminalHistoryOrder, OrderHistoryRecord),
        (observation.unattributed_history_executions, UnattributedHistoryExecution, ExecutionRecord),
    ):
        if type(rows) is not tuple or len(rows) > 2000:
            raise MonitoringError("MONITORING_UNATTRIBUTED_HISTORY_INVALID")
        group = []
        for row in rows:
            if type(row) is not expected or type(row.record) is not record_type:
                raise MonitoringError("MONITORING_UNATTRIBUTED_HISTORY_INVALID")
            group.append((tuple((item.name, scalar(getattr(row.record, item.name)))
                for item in fields(record_type)), tuple((item.name, scalar(getattr(row, item.name)))
                for item in fields(expected) if item.name != "record")))
        payload.append(group)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def unattributed_history_valid(
    target: MonitoringTarget | FreshMonitoringTargetV1, observation: MonitoringObservation,
) -> bool:
    """Validate explicit metadata independently of managed matching; issue no capability."""
    rows, fills = observation.unattributed_history_orders, observation.unattributed_history_executions
    if type(rows) is not tuple or type(fills) is not tuple or len(rows) + len(fills) > 2000:
        return False
    if not rows and not fills:
        return True
    try:
        if unresolved_monitoring_commands(target):
            return False
        windows = {symbol: HistoryWindow(start, end) for symbol, start, end in observation.windows}
        if len(windows) != len(observation.windows):
            return False
        known, seen = known_monitoring_venue_ids(target), set()
        for row in rows:
            if (type(row) is not UnattributedTerminalHistoryOrder
                    or type(row.record) is not OrderHistoryRecord
                    or row.attribution != "UNATTRIBUTED" or row.source != "history"
                    or row.managed is not False or row.financial_qualified is not False
                    or type(row.attribution) is not str or type(row.source) is not str
                    or type(row.raw_row_sha256) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", row.raw_row_sha256) is None
                    or type(row.price) is not Decimal or not row.price.is_finite()
                    or row.record.symbol not in observation.symbols
                    or row.record.venue_order_id in known or row.record.venue_order_id in seen):
                return False
            record = row.record
            if (type(record.created_ms) is not int or type(record.updated_ms) is not int
                    or any(type(value) is not str for value in (record.symbol, record.venue_order_id,
                        record.client_order_id, record.side, record.status, row.order_type, row.time_in_force))
                    or any(type(value) is not Decimal or not value.is_finite()
                        or len(value.as_tuple().digits) > 48
                        or not -18 <= int(value.as_tuple().exponent) <= 30 for value in (
                            record.quantity, record.cumulative_filled, record.leaves_quantity, row.price))):
                return False
            end = windows[record.symbol].end_ms
            window = HistoryWindow(max(1, end - MAX_WINDOW_MS), end)
            reconstructed = {"symbol": record.symbol, "orderId": record.venue_order_id,
                "orderLinkId": record.client_order_id, "side": record.side,
                "positionIdx": record.position_idx, "reduceOnly": record.reduce_only,
                "orderStatus": record.status, "qty": format(record.quantity, "f"),
                "cumExecQty": format(record.cumulative_filled, "f"),
                "leavesQty": format(record.leaves_quantity, "f"),
                "createdTime": str(record.created_ms), "updatedTime": str(record.updated_ms),
                "orderType": row.order_type, "timeInForce": row.time_in_force, "price": format(row.price, "f")}
            checked = parse_unattributed_terminal_history_order(reconstructed, record.symbol, window)
            if checked.record != record:
                return False
            seen.add(record.venue_order_id)
        own_executions, seen_executions = known_monitoring_execution_ids(target), set()
        for fill in fills:
            if (type(fill) is not UnattributedHistoryExecution or type(fill.record) is not ExecutionRecord
                    or type(fill.attribution) is not str or fill.attribution != "UNATTRIBUTED"
                    or type(fill.source) is not str or fill.source != "history"
                    or fill.managed is not False or fill.financial_qualified is not False
                    or type(fill.raw_row_sha256) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", fill.raw_row_sha256) is None
                    or type(fill.is_maker) is not bool or type(fill.seq) is not int
                    or not 0 <= fill.seq < 10**20):
                return False
            execution = fill.record
            key = execution.symbol, execution.execution_id
            if (execution.symbol not in observation.symbols or execution.venue_order_id in known
                    or key in own_executions or key in seen_executions
                    or type(execution.time_ms) is not int
                    or any(type(value) is not str for value in (execution.symbol, execution.execution_id,
                        execution.venue_order_id, execution.client_order_id, execution.side,
                        execution.execution_type, execution.fee_currency))
                    or any(type(value) is not Decimal or not value.is_finite()
                        or len(value.as_tuple().digits) > 48
                        or not -18 <= int(value.as_tuple().exponent) <= 30 for value in (
                            execution.quantity, execution.price, execution.value, execution.fee,
                            execution.fee_rate, execution.closed_quantity))):
                return False
            execution_row = {"symbol": execution.symbol, "execId": execution.execution_id,
                "orderId": execution.venue_order_id, "orderLinkId": execution.client_order_id,
                "side": execution.side, "execType": execution.execution_type,
                "execTime": str(execution.time_ms), "execQty": format(execution.quantity, "f"),
                "execPrice": format(execution.price, "f"), "execValue": format(execution.value, "f"),
                "execFee": format(execution.fee, "f"), "feeCurrency": execution.fee_currency,
                "feeRate": format(execution.fee_rate, "f"), "closedSize": format(execution.closed_quantity, "f"),
                "isMaker": fill.is_maker, "seq": fill.seq}
            checked_fill = parse_unattributed_history_execution(execution_row, execution.symbol,
                windows[execution.symbol])
            if checked_fill.record != execution:
                return False
            seen_executions.add(key)
        return True
    except (ValueError, TypeError, KeyError, AttributeError):
        return False
