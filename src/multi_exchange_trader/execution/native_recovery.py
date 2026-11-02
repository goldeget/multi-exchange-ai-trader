"""Read-only pinned native-cache comparisons, never recovery or execution authority.

Equal checkpoint labels are caller-supplied data, not authenticated backups.
CONSISTENT establishes local agreement only, not durability, restored exposure,
complete history, or a stable snapshot. The future worker still needs an
exclusive lock/snapshot and independently authenticated execution evidence.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from importlib.metadata import version
from typing import Literal, Protocol

from .bybit_native import NATIVE_VERSION, NativeBindings, NativeOrderRequest
from .native_events import bounded_decimal
from .protection import ProtectionScope


class NativeCacheReader(Protocol):
    """Only these three reads are available to the diagnostic inspector."""

    def order(self, client_order_id: object) -> object | None: ...

    def position_id(self, client_order_id: object) -> object | None: ...

    def orders(self) -> list[object]: ...


def _text(value: object) -> None:
    if type(value) is not str or not value.strip() or len(value) > 256:
        raise ValueError("INVALID_RECOVERY_RECORD")


def _revision(value: object) -> bool:
    return type(value) is int and 0 <= value <= 2**63 - 1


@dataclass(frozen=True)
class RecoveryCheckpoint:
    scope: ProtectionScope
    generation_id: str
    journal_revision: int
    ownership_digest: str
    native_version: str
    trader_id: str
    strategy_id: str

    def __post_init__(self) -> None:
        if (type(self.scope) is not ProtectionScope or self.scope.environment != "DEMO"
                or not _revision(self.journal_revision)):
            raise ValueError("INVALID_RECOVERY_RECORD")
        for value in (self.scope.account_id, self.generation_id, self.ownership_digest,
                      self.native_version, self.trader_id, self.strategy_id):
            _text(value)


@dataclass(frozen=True)
class OwnedCacheExpectation:
    request: NativeOrderRequest
    native_account_id: str
    trader_id: str
    strategy_id: str
    venue_order_id: str | None
    expected_native_position_id: str | None

    def __post_init__(self) -> None:
        if type(self.request) is not NativeOrderRequest:
            raise ValueError("INVALID_RECOVERY_RECORD")
        for value in (self.native_account_id, self.trader_id, self.strategy_id):
            _text(value)
        for optional in (self.venue_order_id, self.expected_native_position_id):
            if optional is not None:
                _text(optional)


RecoveryIssueCode = Literal[
    "MISSING_CACHED_ORDER", "INITIALIZED_CACHED_PLACEHOLDER", "CACHED_ORDER_TYPE_MISMATCH",
    "CACHED_ORDER_IDENTITY_MISMATCH", "CACHED_ORDER_NOT_ACTIVE", "NATIVE_POSITION_ID_MISMATCH",
    "MISSING_EXPECTED_POSITION_LINK", "UNOWNED_CACHED_ORDER", "NATIVE_CACHE_READ_FAILED",
    "NATIVE_CACHE_INDEX_MISMATCH", "MISSING_NATIVE_CHECKPOINT", "RECOVERY_CHECKPOINT_MISMATCH",
    "NATIVE_VERSION_MISMATCH", "NATIVE_BINDINGS_UNAVAILABLE", "EXPECTATION_CHECKPOINT_MISMATCH",
    "CONTRADICTORY_OWNED_EXPECTATIONS", "INSPECTION_REVISION_CHANGED",
    "INVALID_INSPECTION_REVISION",
]


@dataclass(frozen=True)
class NativeRecoveryIssue:
    # Only durable owned IDs are emitted. Manual/cache-only IDs and payloads are redacted.
    client_order_id: str | None
    code: RecoveryIssueCode


@dataclass(frozen=True)
class NativeRecoveryCheck:
    status: Literal["CONSISTENT", "BLOCKED"]
    issues: tuple[NativeRecoveryIssue, ...]
    production_supported: Literal[False] = field(default=False, init=False)


class _NativeVersionMismatch(ValueError):
    pass


def _native_types() -> dict[str, type]:
    """Lazy, exact pinned classes; imports cannot create a client, cache, or node."""
    if version("nautilus-trader") != NATIVE_VERSION:
        raise _NativeVersionMismatch("NATIVE_VERSION_MISMATCH")
    result: dict[str, type] = {}
    for path, names in (
        ("model.identifiers", ("ClientOrderId", "InstrumentId", "TraderId", "StrategyId",
                               "AccountId", "VenueOrderId", "PositionId")),
        ("model.enums", ("OrderStatus", "OrderType", "OrderSide")),
        ("model.orders", ("MarketOrder", "LimitOrder")),
    ):
        module = importlib.import_module("nautilus_trader." + path)
        for name in names:
            result[name] = getattr(module, name)
    return result


def _exact(value: object, expected: object) -> bool:
    return type(value) is type(expected) and value == expected


def _inspect_order(order: object, expected: OwnedCacheExpectation, bindings: NativeBindings,
                   types: dict[str, type]) -> set[RecoveryIssueCode]:
    codes: set[RecoveryIssueCode] = set()
    request, owned = expected.request, expected.request.owned
    if type(order) is not types["MarketOrder" if request.order_type == "MARKET" else "LimitOrder"]:
        return {"CACHED_ORDER_TYPE_MISMATCH"}
    # The lazy registry deliberately avoids importing native stubs on portable hosts.
    instrument_type = types["InstrumentId"]
    instrument_id = instrument_type.from_str(owned.instrument_id)  # type: ignore[attr-defined]
    fields: dict[str, object] = {
        "client_order_id": types["ClientOrderId"](owned.client_order_id),
        "instrument_id": instrument_id,
        "trader_id": types["TraderId"](expected.trader_id),
        "strategy_id": types["StrategyId"](expected.strategy_id),
        "account_id": types["AccountId"](expected.native_account_id),
        "venue_order_id": None if expected.venue_order_id is None else
            types["VenueOrderId"](expected.venue_order_id),
        "side": getattr(types["OrderSide"], owned.side),
        "order_type": getattr(types["OrderType"], request.order_type),
        "quantity": bindings.quantity(format(owned.quantity, "f")),
        "is_reduce_only": owned.reduce_only,
        "price": None if request.limit_price is None else
            bindings.price(format(request.limit_price, "f")),
    }
    if any(not _exact(getattr(order, name, None), value) for name, value in fields.items()):
        codes.add("CACHED_ORDER_IDENTITY_MISMATCH")
    # Scalar factories must preserve intent, including precision. They never establish authority.
    if bounded_decimal(str(fields["quantity"])) != owned.quantity:
        codes.add("CACHED_ORDER_IDENTITY_MISMATCH")
    if (request.limit_price is not None
            and bounded_decimal(str(fields["price"])) != request.limit_price):
        codes.add("CACHED_ORDER_IDENTITY_MISMATCH")
    status = getattr(order, "status", None)
    status_name = getattr(status, "name", None)
    if type(status) is types["OrderStatus"] and status_name == "INITIALIZED":
        codes.add("INITIALIZED_CACHED_PLACEHOLDER")
    elif (type(status) is not types["OrderStatus"] or status_name not in {
            "SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED", "TRIGGERED", "PENDING_UPDATE"}
            or getattr(order, "is_closed", None) is not False
            or getattr(order, "is_pending_cancel", None) is not False):
        codes.add("CACHED_ORDER_NOT_ACTIVE")
    return codes


def inspect_native_recovery(*, cache: NativeCacheReader, bindings: NativeBindings,
                            expectations: tuple[OwnedCacheExpectation, ...],
                            application_checkpoint: RecoveryCheckpoint,
                            cache_checkpoint: RecoveryCheckpoint | None,
                            observed_revision_before: int,
                            observed_revision_after: int) -> NativeRecoveryCheck:
    """Collect fixed, redacted mismatches without mutating any native/application state."""
    if (type(application_checkpoint) is not RecoveryCheckpoint
            or (cache_checkpoint is not None and type(cache_checkpoint) is not RecoveryCheckpoint)
            or type(bindings) is not NativeBindings or type(expectations) is not tuple
            or any(type(item) is not OwnedCacheExpectation for item in expectations)):
        raise ValueError("INVALID_RECOVERY_INPUT")
    issues: set[NativeRecoveryIssue] = set()

    def add(code: RecoveryIssueCode, key: str | None = None) -> None:
        issues.add(NativeRecoveryIssue(key, code))

    if not _revision(observed_revision_before) or not _revision(observed_revision_after):
        add("INVALID_INSPECTION_REVISION")
    elif observed_revision_before != observed_revision_after:
        add("INSPECTION_REVISION_CHANGED")
    if cache_checkpoint is None:
        add("MISSING_NATIVE_CHECKPOINT")
    elif cache_checkpoint != application_checkpoint:
        add("RECOVERY_CHECKPOINT_MISMATCH")
    if any(checkpoint is not None and checkpoint.native_version != NATIVE_VERSION
           for checkpoint in (application_checkpoint, cache_checkpoint)):
        add("NATIVE_VERSION_MISMATCH")
    try:
        types = _native_types()
    except _NativeVersionMismatch:
        types = None
        add("NATIVE_VERSION_MISMATCH")
    except Exception:  # noqa: BLE001 - dependency errors must not expose local/provider data.
        types = None
        add("NATIVE_BINDINGS_UNAVAILABLE")

    by_id: dict[str, OwnedCacheExpectation] = {}
    for expected in expectations:
        key = expected.request.owned.client_order_id
        if key in by_id and by_id[key] != expected:
            add("CONTRADICTORY_OWNED_EXPECTATIONS", key)
        by_id[key] = expected
        if (expected.request.scope != application_checkpoint.scope
                or expected.trader_id != application_checkpoint.trader_id
                or expected.strategy_id != application_checkpoint.strategy_id):
            add("EXPECTATION_CHECKPOINT_MISMATCH", key)

    inventory: dict[str, object] | None = None
    try:
        inventory = {}
        for order in cache.orders():
            if types is None:
                continue
            native_id = getattr(order, "client_order_id", None)
            if type(native_id) is not types["ClientOrderId"]:
                add("UNOWNED_CACHED_ORDER")
                continue
            key = str(native_id)
            if key not in by_id:
                add("UNOWNED_CACHED_ORDER")
            elif key in inventory:
                add("NATIVE_CACHE_INDEX_MISMATCH", key)
            inventory[key] = order
    except Exception:  # noqa: BLE001 - cache/accessor text is untrusted and stays redacted.
        inventory = None
        add("NATIVE_CACHE_READ_FAILED")

    for expected in expectations:
        owned = expected.request.owned
        key = owned.client_order_id
        permitted_position = owned.instrument_id + "-" + expected.strategy_id
        if (expected.expected_native_position_id is not None
                and expected.expected_native_position_id != permitted_position):
            add("NATIVE_POSITION_ID_MISMATCH", key)
        if types is None:
            continue
        try:
            native_id = bindings.client_order_id(key)
            if not _exact(native_id, types["ClientOrderId"](key)):
                add("CACHED_ORDER_IDENTITY_MISMATCH", key)
                continue
        except Exception:  # noqa: BLE001 - native conversion errors remain fixed/redacted.
            add("CACHED_ORDER_IDENTITY_MISMATCH", key)
            continue
        try:
            order = cache.order(native_id)
            if order is None:
                add("MISSING_CACHED_ORDER", key)
            else:
                if inventory is not None and inventory.get(key) is not order:
                    add("NATIVE_CACHE_INDEX_MISMATCH", key)
                for code in _inspect_order(order, expected, bindings, types):
                    add(code, key)
        except Exception:  # noqa: BLE001 - inspect remaining orders and links after a failure.
            add("NATIVE_CACHE_READ_FAILED", key)
        try:
            position = cache.position_id(native_id)
            if position is None:
                if expected.expected_native_position_id is not None:
                    add("MISSING_EXPECTED_POSITION_LINK", key)
            elif not _exact(position, types["PositionId"](permitted_position)):
                add("NATIVE_POSITION_ID_MISMATCH", key)
        except Exception:  # noqa: BLE001 - never return raw cache/property errors.
            add("NATIVE_CACHE_READ_FAILED", key)

    ordered = tuple(sorted(issues, key=lambda issue: (issue.client_order_id or "", issue.code)))
    return NativeRecoveryCheck("BLOCKED" if ordered else "CONSISTENT", ordered)
