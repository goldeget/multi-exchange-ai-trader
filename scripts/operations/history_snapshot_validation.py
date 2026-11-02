"""Offline history v1 validator, using only Python 3.12's standard library.

The bounded record codec, SQL lexer and integrity rules intentionally mirror the
frozen application history v1 contract. Keep parity tests when either changes.
No application import, provider transport, writable source connection or clock.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from itertools import combinations
from typing import Any, Literal, cast


@dataclass(frozen=True)
class ProtectionScope:
    account_id: str
    environment: str


@dataclass(frozen=True)
class HistoryConflict:
    conflict_key: str
    kind: Literal["EXECUTION", "TRANSACTION", "ORDER", "SNAPSHOT"]
    record_id: str
    left_observation_key: str
    right_observation_key: str
    reason: str


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


def canonical_history_decimal(value: Decimal) -> str:
    """Exact, bounded plain notation without Decimal context rounding."""
    if type(value) is not Decimal or not value.is_finite():
        raise HistoryError("HISTORY_DECIMAL_REQUIRED")
    sign, digits, exponent = value.as_tuple()
    if len(digits) > 256 or not isinstance(exponent, int) or abs(exponent) > 1000:
        raise HistoryError("HISTORY_DECIMAL_REQUIRED")
    if not any(digits):
        return "0"
    text = "".join(str(digit) for digit in digits)
    stripped = text.rstrip("0")
    exponent += len(text) - len(stripped)
    point = len(stripped) + exponent
    if point > 30 or exponent < -18:
        raise HistoryError("HISTORY_DECIMAL_REQUIRED")
    if exponent >= 0:
        plain = stripped + "0" * exponent
    elif point > 0:
        plain = stripped[:point] + "." + stripped[point:]
    else:
        plain = "0." + "0" * -point + stripped
    return ("-" if sign else "") + plain


_HISTORY_FIELD_MAPS: dict[type[ExecutionRecord | OrderHistoryRecord | TransactionRecord], tuple[str, ...]] = {
    ExecutionRecord: ("execId", "orderId", "orderLinkId", "symbol", "side", "execType",
        "execTime", "execQty", "execPrice", "execValue", "execFee", "feeCurrency", "feeRate",
        "closedSize"),
    OrderHistoryRecord: ("orderId", "orderLinkId", "symbol", "side", "positionIdx", "orderStatus",
        "qty", "cumExecQty", "leavesQty", "reduceOnly", "createdTime", "updatedTime"),
    TransactionRecord: ("id", "tradeId", "orderId", "symbol", "category", "currency", "type",
        "transactionTime", "cashFlow", "funding", "fee", "change", "cashBalance"),
}


def validate_history_record(record: ExecutionRecord | OrderHistoryRecord | TransactionRecord,
                            *, symbol: str, window: HistoryWindow) -> None:
    """Validate constructed records using the same bounded REST field rules."""
    kind = type(record)
    if kind not in _HISTORY_FIELD_MAPS:
        raise HistoryError("HISTORY_RECORD_INVALID")
    row: dict[str, object] = {}
    primitives = {"str": str, "int": int, "bool": bool, "Decimal": Decimal}
    for item, remote in zip(fields(record), _HISTORY_FIELD_MAPS[kind], strict=True):
        value = getattr(record, item.name)
        if type(value) is not primitives[str(item.type)]:
            raise HistoryError("HISTORY_RECORD_INVALID")
        if item.type == "Decimal":
            value = canonical_history_decimal(cast(Decimal, value))
        elif item.name.endswith("_ms"):
            value = str(value)
        row[remote] = value
    if kind is ExecutionRecord:
        _execution(row, symbol, window)
    elif kind is OrderHistoryRecord:
        _order(row, symbol, window)
    else:
        row["transSubType"] = ""
        _transaction(row, window)


def validate_history_scope(scope: ProtectionScope) -> None:
    if (type(scope) is not ProtectionScope or type(scope.account_id) is not str
            or type(scope.environment) is not str or scope.environment not in {"DEMO", "LIVE"}):
        raise HistoryError("HISTORY_SCOPE_INVALID")
    _text(scope.account_id)


def validate_history_snapshot(snapshot: HistorySnapshot) -> None:
    """Validate original typed evidence without consulting a clock or granting authority."""
    if type(snapshot) is not HistorySnapshot or type(snapshot.window) is not HistoryWindow:
        raise HistoryError("HISTORY_SNAPSHOT_INVALID")
    validate_history_scope(snapshot.scope)
    _symbol(snapshot.symbol)
    _text(snapshot.snapshot_id)
    HistoryWindow(snapshot.window.start_ms, snapshot.window.end_ms)
    observed = utc(snapshot.observed_at)
    server = utc(snapshot.server_observed_at)
    observed_ms = milliseconds(observed.replace(microsecond=observed.microsecond // 1000 * 1000))
    if (not 0 < milliseconds(server) or snapshot.window.end_ms > observed_ms
            or server > observed or type(snapshot.transport_injected) is not bool
            or snapshot.page_complete is not True or snapshot.transactions_account_wide is not True
            or snapshot.venue_event_complete is not False or snapshot.flat_verified is not False):
        raise HistoryError("HISTORY_SNAPSHOT_INVALID")
    for records, kind, identity in (
        (snapshot.executions, ExecutionRecord, "execution_id"),
        (snapshot.orders, OrderHistoryRecord, "venue_order_id"),
        (snapshot.transactions, TransactionRecord, "transaction_id"),
    ):
        if type(records) is not tuple or len(records) > 10000:
            raise HistoryError("HISTORY_RECORDS_INVALID")
        identities: set[str] = set()
        for record in records:
            if type(record) is not kind:
                raise HistoryError("HISTORY_RECORD_INVALID")
            validate_history_record(record, symbol=snapshot.symbol, window=snapshot.window)
            key = getattr(record, identity)
            if key in identities:
                raise HistoryError("HISTORY_DUPLICATE_IDENTITY")
            identities.add(key)
    order_map = {order.venue_order_id: order for order in snapshot.orders}
    totals: dict[str, Decimal] = {}
    with localcontext() as context:
        context.prec = 100
        for execution in snapshot.executions:
            order = order_map.get(execution.venue_order_id)
            if order is not None:
                if execution.client_order_id != order.client_order_id or execution.side != order.side:
                    raise HistoryError("HISTORY_EXECUTION_ORDER_CONFLICT")
                if not order.created_ms <= execution.time_ms <= order.updated_ms:
                    raise HistoryError("HISTORY_EXECUTION_ORDER_TIME_CONFLICT")
            if execution.execution_type != "Funding":
                totals[execution.venue_order_id] = (totals.get(execution.venue_order_id, Decimal(0))
                                                   + execution.quantity)
    for order in snapshot.orders:
        # The snapshot retains MIN(page-server-time), not MAX; final observation
        # is the available upper bound. Never reject a valid later page update.
        if (order.updated_ms > observed_ms
                or totals.get(order.venue_order_id, Decimal(0)) > order.cumulative_filled):
            raise HistoryError("HISTORY_EXECUTION_ORDER_TOTAL_CONFLICT")


HistoryKind = Literal["EXECUTION", "TRANSACTION", "ORDER"]
HistoryRecord = ExecutionRecord | TransactionRecord | OrderHistoryRecord


SCHEMA_VERSION = 1
_DEFINITIONS = {
    "history_scope": "id INTEGER NOT NULL PRIMARY KEY CHECK(id=1), "
        "account TEXT NOT NULL, environment TEXT NOT NULL",
    "history_snapshots": "observation_key TEXT NOT NULL PRIMARY KEY, snapshot_id TEXT NOT NULL, "
        "symbol TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, "
        "observed_at TEXT NOT NULL, server_observed_at TEXT NOT NULL, "
        "transport_injected INTEGER NOT NULL",
    "history_variants": "kind TEXT NOT NULL, record_id TEXT NOT NULL, variant_hash TEXT NOT NULL, "
        "symbol TEXT NOT NULL, event_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL, "
        "payload TEXT NOT NULL, PRIMARY KEY(kind,record_id,variant_hash)",
    "history_members": "observation_key TEXT NOT NULL, kind TEXT NOT NULL, record_id TEXT NOT NULL, "
        "variant_hash TEXT NOT NULL, PRIMARY KEY(observation_key,kind,record_id), "
        "FOREIGN KEY(observation_key) REFERENCES history_snapshots(observation_key), "
        "FOREIGN KEY(kind,record_id,variant_hash) "
        "REFERENCES history_variants(kind,record_id,variant_hash)",
    "history_conflicts": "conflict_key TEXT NOT NULL PRIMARY KEY, kind TEXT NOT NULL, "
        "record_id TEXT NOT NULL, left_observation_key TEXT NOT NULL, "
        "right_observation_key TEXT NOT NULL, reason TEXT NOT NULL, "
        "FOREIGN KEY(left_observation_key) REFERENCES history_snapshots(observation_key), "
        "FOREIGN KEY(right_observation_key) REFERENCES history_snapshots(observation_key)",
}
_KEYS = {
    "history_scope": ("id",), "history_snapshots": ("observation_key",),
    "history_variants": ("kind", "record_id", "variant_hash"),
    "history_members": ("observation_key", "kind", "record_id"),
    "history_conflicts": ("conflict_key",),
}
_RECORD_TYPES: dict[str, type[ExecutionRecord | OrderHistoryRecord | TransactionRecord]] = {
    "EXECUTION": ExecutionRecord, "ORDER": OrderHistoryRecord, "TRANSACTION": TransactionRecord,
}
VariantKey = tuple[str, str, str]
VariantRow = tuple[str, str, str, str, int, int, str]


def _fail() -> None:
    raise HistoryError("HISTORY_DATABASE_INVALID")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False)


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _trigger(table: str, operation: str) -> str:
    return (f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'history is append-only'); END")


def _create_schema(db: sqlite3.Connection) -> None:
    for table, definition in _DEFINITIONS.items():
        db.execute(f"CREATE TABLE {table} ({definition})")
        for operation in ("UPDATE", "DELETE"):
            db.execute(_trigger(table, operation))
    # Lookup indexes have no identity role; equivalent existing schemas may omit them.
    for name, table, columns in (
        ("history_members_variant", "history_members", "kind,record_id,variant_hash,observation_key"),
        ("history_snapshots_id", "history_snapshots", "snapshot_id,observation_key"),
        ("history_conflicts_left", "history_conflicts", "left_observation_key"),
        ("history_conflicts_right", "history_conflicts", "right_observation_key"),
    ):
        db.execute(f"CREATE INDEX {name} ON {table} ({columns})")
    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def _references(db: sqlite3.Connection, table: str) -> set[tuple[tuple[Any, ...], ...]]:
    groups: dict[int, list[tuple[Any, ...]]] = {}
    for row in db.execute(f'PRAGMA foreign_key_list("{table}")'):
        groups.setdefault(row[0], []).append(tuple(row[1:]))
    return {tuple(sorted(rows)) for rows in groups.values()}


def _sql_tokens(sql: str) -> tuple[tuple[str, str], ...]:
    """Lex original SQLite DDL once; quoted content never becomes comment or syntax."""
    result: list[tuple[str, str]] = []
    cursor = 0
    while cursor < len(sql):
        start = cursor
        char = sql[cursor]
        if char.isspace():
            cursor += 1
        elif sql.startswith("--", cursor):
            end = sql.find("\n", cursor + 2)
            cursor = len(sql) if end == -1 else end + 1
        elif sql.startswith("/*", cursor):
            end = sql.find("*/", cursor + 2)
            if end == -1:
                _fail()
            cursor = end + 2
        elif char in "'\"`[":
            closing = "]" if char == "[" else char
            cursor += 1
            while cursor < len(sql):
                if sql[cursor] == closing:
                    cursor += 1
                    if closing != "]" and cursor < len(sql) and sql[cursor] == closing:
                        cursor += 1  # Doubled delimiters escape quotes, not comments.
                        continue
                    break
                cursor += 1
            else:
                _fail()
            result.append(("quoted", sql[start:cursor]))
        elif char.isalpha() or char == "_":
            cursor += 1
            while cursor < len(sql) and (sql[cursor].isalnum() or sql[cursor] in "_$"):
                cursor += 1
            result.append(("word", sql[start:cursor]))
        elif char.isdigit():
            cursor += 1
            while cursor < len(sql) and sql[cursor].isdigit():
                cursor += 1
            result.append(("number", sql[start:cursor]))
        else:
            cursor += 1
            result.append(("symbol", char))
    return tuple(result)


def _validate_binary_collations(sql: str) -> None:
    """Recognize actual COLLATE clauses, including comments and quoted names."""
    tokens = _sql_tokens(sql)
    for index, (kind, value) in enumerate(tokens):
        if kind != "word" or value.upper() != "COLLATE":
            continue
        if index + 1 == len(tokens):
            _fail()
        name_kind, name = tokens[index + 1]
        if name_kind == "quoted":
            delimiter = name[0]
            name = name[1:-1]
            if delimiter != "[":
                name = name.replace(delimiter * 2, delimiter)
        elif name_kind != "word":
            _fail()
        if name.lower() != "binary":
            _fail()


def _validate_schema(db: sqlite3.Connection) -> None:
    if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        _fail()
    objects = list(db.execute("SELECT name,type,sql FROM sqlite_master "
                              "WHERE substr(lower(name),1,7) != 'sqlite_'"))
    if {r[0] for r in objects if r[1] == "table"} != set(_DEFINITIONS):
        _fail()
    expected_triggers = {f"{table}_no_{op.lower()}": _trigger(table, op)
                         for table in _DEFINITIONS for op in ("UPDATE", "DELETE")}
    if {r[0] for r in objects if r[1] in {"trigger", "view"}} != set(expected_triggers):
        _fail()
    for name, kind, sql in objects:
        if kind == "trigger" and _sql_tokens(sql) != _sql_tokens(expected_triggers[name]):
            _fail()
        if kind == "view":
            _fail()
        if kind == "table":
            _validate_binary_collations(sql)
    scope_tokens = tuple((kind, value.upper() if kind == "word" else value)
        for kind, value in _sql_tokens(next(r[2] for r in objects if r[0] == "history_scope")))
    required_check = (("word", "CHECK"), ("symbol", "("), ("word", "ID"),
                      ("symbol", "="), ("number", "1"), ("symbol", ")"))
    if not any(scope_tokens[index:index + len(required_check)] == required_check
               for index in range(len(scope_tokens))):
        _fail()
    with closing(sqlite3.connect(":memory:")) as reference:
        _create_schema(reference)
        for table, required_key in _KEYS.items():
            columns = list(db.execute(f'PRAGMA table_xinfo("{table}")'))
            expected = list(reference.execute(f'PRAGMA table_xinfo("{table}")'))
            # Identity may be an equivalent full UNIQUE BINARY key, not necessarily PK.
            if [(r[1], r[2].upper(), r[3], r[4], r[6]) for r in columns] != [
                    (r[1], r[2].upper(), r[3], r[4], r[6]) for r in expected]:
                _fail()
            indexes = list(db.execute(f'PRAGMA index_list("{table}")'))
            keys: list[set[str]] = []
            for index in indexes:
                if index[2] and not index[4]:
                    parts = list(db.execute('SELECT name,coll FROM pragma_index_xinfo(?) '
                                            'WHERE "key"=1', (index[1],)))
                    if (len(parts) == len({p[0] for p in parts})
                            and all(p[0] is not None and p[1] == "BINARY" for p in parts)):
                        keys.append({p[0] for p in parts})
            primary = [r for r in columns if r[5]]
            if (len(primary) == 1 and primary[0][2].upper() == "INTEGER"
                    and not any(index[3] == "pk" for index in indexes)):
                keys.append({primary[0][1]})
            if set(required_key) not in keys or _references(db, table) != _references(reference, table):
                _fail()
            if len(list(db.execute(f'PRAGMA foreign_key_list("{table}")'))) != len(
                    list(reference.execute(f'PRAGMA foreign_key_list("{table}")'))):
                _fail()
    if ([tuple(row) for row in db.execute("PRAGMA quick_check")] != [("ok",)]
            or db.execute("PRAGMA foreign_key_check").fetchone() is not None):
        _fail()


def _payload(record: HistoryRecord) -> str:
    data = {item.name: (canonical_history_decimal(getattr(record, item.name))
                       if item.type == "Decimal" else getattr(record, item.name))
            for item in fields(record)}
    result = _json(data)
    if len(result.encode("utf-8")) > 65536:
        _fail()
    return result


def _variant(record: HistoryRecord) -> VariantRow:
    if type(record) is ExecutionRecord:
        kind, identity, event, updated = ("EXECUTION", record.execution_id,
                                          record.time_ms, record.time_ms)
    elif type(record) is TransactionRecord:
        kind, identity, event, updated = ("TRANSACTION", record.transaction_id,
                                          record.time_ms, record.time_ms)
    else:
        order = cast(OrderHistoryRecord, record)
        kind, identity, event, updated = ("ORDER", order.venue_order_id,
                                          order.created_ms, order.updated_ms)
    payload = _payload(record)
    return (kind, identity, hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            record.symbol, event, updated, payload)


def _decode_record(kind: str, payload: str) -> HistoryRecord:
    if kind not in _RECORD_TYPES or type(payload) is not str or len(payload.encode("utf-8")) > 65536:
        raise HistoryError("HISTORY_DATABASE_INVALID")
    # Canonical equality below also rejects duplicate keys and noncanonical decimal spellings.
    data = json.loads(payload)
    cls = _RECORD_TYPES[kind]
    if type(data) is not dict or set(data) != {item.name for item in fields(cls)}:
        raise HistoryError("HISTORY_DATABASE_INVALID")
    for item in fields(cls):
        if item.type == "Decimal":
            value = data[item.name]
            if type(value) is not str or len(value) > 64:
                _fail()
            try:
                data[item.name] = Decimal(value)
            except InvalidOperation:
                raise HistoryError("HISTORY_DATABASE_INVALID") from None
            if not data[item.name].is_finite():
                _fail()  # InvalidOperation may be untrapped in the caller's Decimal context.
    record = cls(**data)
    if _payload(record) != payload:
        _fail()
    event = record.created_ms if isinstance(record, OrderHistoryRecord) else record.time_ms
    validate_history_record(record, symbol=record.symbol, window=HistoryWindow(event, event))
    return record


def _metadata(snapshot: HistorySnapshot) -> tuple[str, str, int, int, str, str, int]:
    return (snapshot.snapshot_id, snapshot.symbol, snapshot.window.start_ms, snapshot.window.end_ms,
            snapshot.observed_at.astimezone(UTC).isoformat(),
            snapshot.server_observed_at.astimezone(UTC).isoformat(), int(snapshot.transport_injected))


def _observation_key(scope: ProtectionScope, metadata: tuple[Any, ...],
                     members: list[VariantKey]) -> str:
    return _hash([scope.account_id, scope.environment, list(metadata), sorted(members)])


def _conflict(kind: Literal["EXECUTION", "TRANSACTION", "ORDER", "SNAPSHOT"], identity: str,
              left: str, right: str, reason: str) -> HistoryConflict:
    left, right = sorted((left, right))
    return HistoryConflict(_hash([kind, identity, reason, left, right]), kind, identity,
                           left, right, reason)


def _reasons(left: HistoryRecord, right: HistoryRecord) -> tuple[str, ...]:
    if left == right:
        return ()
    if not isinstance(left, OrderHistoryRecord) or not isinstance(right, OrderHistoryRecord):
        return ("EVENT_CONTENT_CHANGED",)
    reasons = []
    if left.updated_ms == right.updated_ms:
        reasons.append("ORDER_UPDATE_COLLISION")
    if left.symbol != right.symbol or left.side != right.side:
        reasons.append("ORDER_IDENTITY_CHANGED")
    older, newer = sorted((left, right), key=lambda r: r.updated_ms)
    if older.updated_ms < newer.updated_ms and older.cumulative_filled > newer.cumulative_filled:
        reasons.append("ORDER_FILL_REGRESSION")
    return tuple(reasons)


def _required_conflicts(snapshots: dict[str, HistorySnapshot]) -> dict[str, HistoryConflict]:
    result: dict[str, HistoryConflict] = {}
    by_snapshot_id: dict[str, list[str]] = {}
    by_identity: dict[tuple[HistoryKind, str],
                      dict[str, tuple[HistoryRecord, set[str]]]] = {}
    for key, snapshot in snapshots.items():
        by_snapshot_id.setdefault(snapshot.snapshot_id, []).append(key)
        records: tuple[HistoryRecord, ...] = (*snapshot.executions, *snapshot.orders,
                                              *snapshot.transactions)
        for record in records:
            kind, identity, variant_hash, *_ = _variant(record)
            variants = by_identity.setdefault((cast(HistoryKind, kind), identity), {})
            if variant_hash in variants:
                if variants[variant_hash][0] != record:
                    _fail()
                variants[variant_hash][1].add(key)
            else:
                variants[variant_hash] = (record, {key})
    for snapshot_id, keys in by_snapshot_id.items():
        for left_key, right_key in combinations(keys, 2):
            conflict = _conflict("SNAPSHOT", snapshot_id, left_key, right_key, "SNAPSHOT_ID_REUSED")
            _add_conflict(result, conflict)
    for (kind, identity), variants in by_identity.items():
        for (left, left_keys), (right, right_keys) in combinations(variants.values(), 2):
            reasons = _reasons(left, right)
            if reasons:
                # Only real contradictions incur the unavoidable pair-evidence output cost.
                for left_key in left_keys:
                    for right_key in right_keys:
                        for reason in reasons:
                            _add_conflict(result, _conflict(kind, identity, left_key, right_key, reason))
    return result


def _add_conflict(result: dict[str, HistoryConflict], conflict: HistoryConflict) -> None:
    previous = result.get(conflict.conflict_key)
    if previous is not None and previous != conflict:
        _fail()
    result[conflict.conflict_key] = conflict


def _read_evidence(db: sqlite3.Connection, scope: ProtectionScope) -> dict[str, HistorySnapshot]:
    variants: dict[VariantKey, HistoryRecord] = {}
    for raw in db.execute("SELECT * FROM history_variants"):
        row = tuple(raw)
        record = _decode_record(row[0], row[6])
        if row != _variant(record) or row[:3] in variants:
            _fail()
        variants[row[:3]] = record
    members: dict[str, list[VariantKey]] = {}
    used: set[VariantKey] = set()
    for observation, kind, identity, variant_hash in db.execute("SELECT * FROM history_members"):
        key = (kind, identity, variant_hash)
        if key not in variants:
            _fail()
        members.setdefault(observation, []).append(key)
        used.add(key)
    snapshots: dict[str, HistorySnapshot] = {}
    for raw in db.execute("SELECT * FROM history_snapshots"):
        key, snapshot_id, symbol, start, end, observed, server, injected = tuple(raw)
        if type(injected) is not int or injected not in (0, 1):
            _fail()
        records = [variants[item] for item in members.get(key, [])]
        snapshot = HistorySnapshot(scope, symbol, snapshot_id, HistoryWindow(start, end),
            datetime.fromisoformat(observed), datetime.fromisoformat(server),
            tuple(r for r in records if type(r) is ExecutionRecord),
            tuple(r for r in records if type(r) is OrderHistoryRecord),
            tuple(r for r in records if type(r) is TransactionRecord), bool(injected))
        validate_history_snapshot(snapshot)
        if (tuple(raw)[1:] != _metadata(snapshot)
                or key != _observation_key(scope, _metadata(snapshot), members.get(key, []))
                or key in snapshots):
            _fail()
        snapshots[key] = snapshot
    if used != set(variants) or not set(members) <= set(snapshots):
        _fail()
    return snapshots


def validate_history_database(db: sqlite3.Connection, *,
                               scope: ProtectionScope | None = None) -> ProtectionScope:
    """Read-only validation of schema, typed contents, provenance and all conflict evidence."""
    try:
        _validate_schema(db)
        rows = list(db.execute("SELECT id,account,environment FROM history_scope"))
        if len(rows) != 1 or type(rows[0][0]) is not int or rows[0][0] != 1:
            _fail()
        persisted = ProtectionScope(rows[0][1], rows[0][2])
        validate_history_scope(persisted)
        if scope is not None:
            validate_history_scope(scope)
            if scope != persisted:
                raise HistoryError("HISTORY_SCOPE_MISMATCH")
        snapshots = _read_evidence(db, persisted)
        expected = _required_conflicts(snapshots)
        actual = {row[0]: tuple(row) for row in db.execute("SELECT * FROM history_conflicts")}
        if actual != {key: tuple(getattr(value, f.name) for f in fields(value))
                      for key, value in expected.items()}:
            _fail()
        return persisted
    except HistoryError:
        raise
    except (sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise HistoryError("HISTORY_DATABASE_INVALID") from None


def check_history(db: sqlite3.Connection) -> tuple[str, str]:
    """Validate without mutation; return the file's sole persisted account scope."""
    scope = validate_history_database(db)
    return scope.account_id, scope.environment
