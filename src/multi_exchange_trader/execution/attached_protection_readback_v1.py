"""Authenticated attached TP/SL observations; no trading or financial authority.

Children retain their actual venue/client IDs. No synthetic OwnedOrder, cache
registration, codec entry, cancellation or protection modification is created.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, NoReturn, Self, SupportsIndex
from uuid import uuid4

from ..account.bybit_readonly import (
    _account_info_capture_data_v1,
    _AccountInfoCaptureV1,
    _parse_json,
)
from .account_binding import (
    AuthenticatedAccountBinding,
    BybitDemoAccountSession,
    _linear_source_data_v1,
    _LinearAccountInfoSlot,
    _LinearHeldPageV1,
)
from .bybit_linear_model_inputs_v1 import BybitModelSourceRefV1
from .history import _symbol
from .monitoring_budget import MonitoringReadBudget
from .native_events import bounded_decimal
from .protection import ProtectionScope, Side
from .venue_readback import VenueSnapshot, _order, _position

type _IssuedPagesV1 = tuple[tuple[_LinearHeldPageV1, BybitModelSourceRefV1,
    _AccountInfoCaptureV1, tuple[object, ...], tuple[object, ...], bytes], ...]
type _IssuedSourcesV1 = tuple[tuple[BybitModelSourceRefV1, ...], tuple[BybitModelSourceRefV1, ...],
    _LinearAccountInfoSlot, _LinearAccountInfoSlot, tuple[_LinearHeldPageV1, ...],
    tuple[_LinearHeldPageV1, ...], _IssuedPagesV1]


class AttachedProtectionErrorV1(ValueError):
    def __init__(self) -> None:
        super().__init__("AUTHENTICATED_ATTACHED_PROTECTION_REQUIRED")


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is not UTC:
        raise AttachedProtectionErrorV1()
    return value


def _text(value: object, *, empty: bool = False) -> str:
    if (type(value) is not str or len(value) > 256 or not empty and not value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise AttachedProtectionErrorV1()
    return value


@dataclass(frozen=True)
class AttachedProtectionTargetV1:
    scope: ProtectionScope
    position_id: str
    instrument_id: str
    parent_client_order_id: str
    parent_venue_order_id: str
    parent_quantity: Decimal
    owned_quantity: Decimal
    entry_side: Side
    policy_revision: str
    policy_generation: int
    stop_price: Decimal
    take_profit_price: Decimal
    trigger_source: str
    latest_fill_at: datetime
    command_payload_sha256: str
    projection_sha256: str

    def __post_init__(self) -> None:
        if type(self.scope) is not ProtectionScope or self.scope.environment != "DEMO":
            raise AttachedProtectionErrorV1()
        for value in (self.position_id, self.instrument_id, self.parent_client_order_id,
                self.parent_venue_order_id, self.policy_revision, self.trigger_source):
            _text(value)
        if (self.entry_side not in ("BUY", "SELL") or type(self.entry_side) is not str
                or type(self.policy_generation) is not int or self.policy_generation < 0
                or self.trigger_source not in ("MarkPrice", "LastPrice", "IndexPrice")
                or not self.instrument_id.endswith("-LINEAR.BYBIT")):
            raise AttachedProtectionErrorV1()
        for value in (self.parent_quantity, self.owned_quantity, self.stop_price, self.take_profit_price):
            if type(value) is not Decimal:
                raise AttachedProtectionErrorV1()
            bounded_decimal(value)
        if self.owned_quantity > self.parent_quantity:
            raise AttachedProtectionErrorV1()
        for value in (self.command_payload_sha256, self.projection_sha256):
            if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise AttachedProtectionErrorV1()
        _utc(self.latest_fill_at)


@dataclass(frozen=True)
class AttachedProtectionChildV1:
    venue_order_id: str
    client_order_id: str
    role: Literal["STOP", "TAKE_PROFIT"]


@dataclass(frozen=True)
class AttachedProtectionViewV1:
    target: AttachedProtectionTargetV1
    snapshot: VenueSnapshot
    children: tuple[AttachedProtectionChildV1, ...]
    protection_coverage: Literal["COMPLETE", "INCOMPLETE"]


class AuthenticatedAttachedProtectionReceiptV1:
    __slots__ = ()

    def __new__(cls) -> Self:
        raise AttachedProtectionErrorV1()

    def __copy__(self) -> NoReturn:
        raise AttachedProtectionErrorV1()

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        raise AttachedProtectionErrorV1()

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise AttachedProtectionErrorV1()


def _rows(pages: tuple[tuple[BybitModelSourceRefV1, bytes], ...]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for source, raw in pages:
        envelope = _parse_json(raw)
        if (type(envelope) is not dict or type(envelope.get("retCode")) is not int
                or envelope["retCode"] != 0 or type(envelope.get("time")) is not int
                or envelope["time"] != source.envelope_time_ms):
            raise AttachedProtectionErrorV1()
        result = envelope.get("result")
        if type(result) is not dict or result.get("category") != "linear":
            raise AttachedProtectionErrorV1()
        values = result.get("list")
        if type(values) is not list or len(values) > 200:
            raise AttachedProtectionErrorV1()
        for row in values:
            if type(row) is not dict or any(type(k) is not str for k in row):
                raise AttachedProtectionErrorV1()
            _symbol(_text(row.get("symbol")))
            rows.append(row)
    return rows


def _project(target: AttachedProtectionTargetV1, *,
             positions: tuple[tuple[BybitModelSourceRefV1, bytes], ...],
             orders: tuple[tuple[BybitModelSourceRefV1, bytes], ...],
             cycle_id: str, observed_at: datetime) -> AttachedProtectionViewV1:
    target.__post_init__()
    symbol = target.instrument_id.removesuffix("-LINEAR.BYBIT")
    selected_positions = tuple(_position(row, symbol) for row in _rows(positions) if row.get("symbol") == symbol)
    if len(selected_positions) != 1:
        raise AttachedProtectionErrorV1()
    position = selected_positions[0]
    if (position.position_idx != 0 or position.side != target.entry_side
            or position.quantity != target.owned_quantity):
        raise AttachedProtectionErrorV1()
    all_orders = [(row, source.envelope_time_ms) for source, raw in orders
        for row in _rows(((source, raw),))]
    venue_ids = [_text(row.get("orderId")) for row, _stamp in all_orders]
    if len(set(venue_ids)) != len(venue_ids):
        raise AttachedProtectionErrorV1()
    children: list[AttachedProtectionChildV1] = []
    selected_orders = []
    for row, page_stamp in all_orders:
        if row.get("symbol") != symbol:
            if row.get("parentOrderLinkId") == target.parent_client_order_id:
                raise AttachedProtectionErrorV1()
            continue
        order = _order(row, symbol)
        selected_orders.append(order)
        if order.client_order_id == target.parent_client_order_id:
            # A still-resting parent is visible but never treated as reduce-only cleanup.
            if (order.venue_order_id != target.parent_venue_order_id or order.position_idx != 0
                    or order.side != target.entry_side or order.reduce_only
                    or order.quantity != target.parent_quantity):
                raise AttachedProtectionErrorV1()
            continue
        role: Literal["STOP", "TAKE_PROFIT"]
        if order.stop_order_type == "StopLoss":
            role, trigger = "STOP", target.stop_price
        elif order.stop_order_type == "TakeProfit":
            role, trigger = "TAKE_PROFIT", target.take_profit_price
        else:
            raise AttachedProtectionErrorV1()
        expected_direction = (2 if role == "STOP" else 1) if target.entry_side == "BUY" else (1 if role == "STOP" else 2)
        created, updated = row.get("createdTime"), row.get("updatedTime")
        if (type(created) is not str or not created.isascii() or not created.isdigit()
                or not 1 <= len(created) <= 15 or type(updated) is not str or updated != created):
            raise AttachedProtectionErrorV1()
        if (type(page_stamp) is not int or not 0 < int(created) < 253402300800000
                or int(created) > page_stamp):
            raise AttachedProtectionErrorV1()
        created_at = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(created))
        if (row.get("parentOrderLinkId") != target.parent_client_order_id
                or order.venue_order_id == target.parent_venue_order_id
                or order.client_order_id == target.parent_client_order_id
                or order.position_idx != 0 or order.side == target.entry_side
                or order.quantity != target.owned_quantity or order.leaves_quantity != target.owned_quantity
                or order.cumulative_filled != 0 or order.reduce_only is not True
                or row.get("closeOnTrigger") is not True or order.status != "Untriggered"
                or row.get("orderType") != "Market" or row.get("tpslMode") != "Full"
                or row.get("createType") != "CreateBy" + order.stop_order_type
                or type(row.get("triggerDirection")) is not int or row["triggerDirection"] != expected_direction
                or order.trigger_price != trigger or order.trigger_source != target.trigger_source
                or not target.latest_fill_at <= created_at <= observed_at):
            raise AttachedProtectionErrorV1()
        children.append(AttachedProtectionChildV1(order.venue_order_id, order.client_order_id, role))
    if len(children) > 2 or len({c.role for c in children}) != len(children):
        raise AttachedProtectionErrorV1()
    times = [source.envelope_time_ms for source, _raw in (*positions, *orders)]
    if not times or any(type(stamp) is not int for stamp in times):
        raise AttachedProtectionErrorV1()
    server_at = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=min(t for t in times if t is not None))
    snapshot = VenueSnapshot(target.scope, symbol, cycle_id, observed_at, server_at, True,
        selected_positions, tuple(selected_orders), ())
    # Position-level TP/SL fields describe coverage, never child identity or ownership.
    coverage: Literal["COMPLETE", "INCOMPLETE"] = ("COMPLETE" if len(children) == 2
        and position.stop_loss == target.stop_price and position.take_profit == target.take_profit_price
        else "INCOMPLETE")
    return AttachedProtectionViewV1(target, snapshot, tuple(children), coverage)


def _target_data(target: AttachedProtectionTargetV1) -> tuple[object, ...]:
    if type(target) is not AttachedProtectionTargetV1:
        raise AttachedProtectionErrorV1()
    target.__post_init__()
    return (target.scope.account_id, target.scope.environment, target.position_id, target.instrument_id,
        target.parent_client_order_id, target.parent_venue_order_id, target.parent_quantity,
        target.owned_quantity, target.entry_side, target.policy_revision, target.policy_generation,
        target.stop_price, target.take_profit_price, target.trigger_source, target.latest_fill_at,
        target.command_payload_sha256, target.projection_sha256)


def _view_data(view: AttachedProtectionViewV1) -> tuple[object, ...]:
    from dataclasses import fields

    from .venue_readback import VenueOrder, VenuePosition

    if (type(view) is not AttachedProtectionViewV1 or type(view.snapshot) is not VenueSnapshot
            or type(view.children) is not tuple
            or type(view.protection_coverage) is not str
            or view.protection_coverage not in ("COMPLETE", "INCOMPLETE")):
        raise AttachedProtectionErrorV1()
    snapshot = view.snapshot
    if (type(snapshot.positions) is not tuple or type(snapshot.orders) is not tuple
            or type(snapshot.scope) is not ProtectionScope or type(snapshot.complete) is not bool
            or snapshot.identity_verified is not False or snapshot.flat_verified is not False
            or type(snapshot.blockers) is not tuple or snapshot.blockers):
        raise AttachedProtectionErrorV1()
    _utc(snapshot.observed_at)
    if snapshot.server_observed_at is None:
        raise AttachedProtectionErrorV1()
    _utc(snapshot.server_observed_at)
    rows: list[tuple[object, ...]] = []
    for row in (*snapshot.positions, *snapshot.orders):
        if not isinstance(row, (VenuePosition, VenueOrder)) or type(row) not in (VenuePosition, VenueOrder):
            raise AttachedProtectionErrorV1()
        values = tuple(getattr(row, field.name) for field in fields(row))
        if any(type(value) not in (str, int, bool, Decimal, type(None)) for value in values):
            raise AttachedProtectionErrorV1()
        rows.append(tuple((type(value).__name__, value) for value in values))
    children = []
    for child in view.children:
        if type(child) is not AttachedProtectionChildV1:
            raise AttachedProtectionErrorV1()
        children.append((_text(child.venue_order_id), _text(child.client_order_id, empty=True), _text(child.role)))
    return (_target_data(view.target), _text(snapshot.scope.account_id), _text(snapshot.scope.environment),
        _text(snapshot.symbol), _text(snapshot.cycle_id), snapshot.observed_at, snapshot.server_observed_at,
        snapshot.complete, tuple(rows), tuple(children), view.protection_coverage)


class AttachedProtectionReadbackV1:
    """One original Session publication, revoked by source refresh or owner change."""

    def __init__(self, session: BybitDemoAccountSession, binding: AuthenticatedAccountBinding,
                 target: AttachedProtectionTargetV1, budget: MonitoringReadBudget,
                 owner_guard: Callable[[], None], max_evidence_age: timedelta) -> None:
        if (type(session) is not BybitDemoAccountSession or type(target) is not AttachedProtectionTargetV1
                or type(budget) is not MonitoringReadBudget or not callable(owner_guard)
                or type(max_evidence_age) is not timedelta or max_evidence_age <= timedelta(0)):
            raise AttachedProtectionErrorV1()
        target.__post_init__()
        self.session, self.binding, self.target = session, binding, target
        self.budget, self.owner_guard = budget, owner_guard
        self.started_at, self.started_mono = _utc(session._clock()), session._mono()
        self.ttl = min(session._max_age, max_evidence_age, timedelta(seconds=30))
        self.position_sources: tuple[BybitModelSourceRefV1, ...] = ()
        self.order_sources: tuple[BybitModelSourceRefV1, ...] = ()
        self.position_slot: _LinearAccountInfoSlot | None = None
        self.order_slot: _LinearAccountInfoSlot | None = None
        self.receipt: AuthenticatedAttachedProtectionReceiptV1 | None = None
        self.view: AttachedProtectionViewV1 | None = None
        self.cycle_id, self.observed_at = uuid4().hex, self.started_at
        self._original = self
        self._original_links_v1 = (session, binding, target, budget, owner_guard)
        self._target_preimage_v1 = _target_data(target)
        self._issued_v1: tuple[AuthenticatedAttachedProtectionReceiptV1, AttachedProtectionViewV1] | None = None
        self._checked_clock_v1 = self.started_at, self.started_mono
        self._limits_v1 = self.started_at, self.started_mono, self.ttl, self.cycle_id
        self._issued_view_preimage_v1: tuple[object, ...] | None = None
        self._issued_sources_v1: _IssuedSourcesV1 | None = None

    def guard(self) -> None:
        links = self._original_links_v1
        if (self._original is not self or self.session is not links[0] or self.binding is not links[1]
                or self.target is not links[2] or self.budget is not links[3] or self.owner_guard is not links[4]):
            raise AttachedProtectionErrorV1()
        self.owner_guard()
        self.session.require_observation_binding(self.binding, scope=self.target.scope)
        now, mono = _utc(self.session._clock()), self.session._mono()
        if (self._original is not self or self.session._attached_protection_current_v1 is not self
                or not timedelta(0) <= now - self.started_at <= self.ttl
                or not 0 <= mono - self.started_mono <= self.ttl.total_seconds()):
            raise AttachedProtectionErrorV1()
        self._checked_clock_v1 = now, mono

    def _pages(self, endpoint: Literal["POSITIONS", "ORDERS"]
               ) -> tuple[tuple[BybitModelSourceRefV1, bytes], ...]:
        sources = self.position_sources if endpoint == "POSITIONS" else self.order_sources
        slot = self.session._linear_positions_current if endpoint == "POSITIONS" else self.session._linear_orders_current
        expected = self.position_slot if endpoint == "POSITIONS" else self.order_slot
        if type(slot) is not _LinearAccountInfoSlot or slot is not expected or slot.retired or slot.pages is None:
            raise AttachedProtectionErrorV1()
        self.session._linear_account_info_links_v1(slot)
        if len(slot.pages) != len(sources):
            raise AttachedProtectionErrorV1()
        result = []
        for source, page in zip(sources, slot.pages, strict=True):
            if (source is not page.source or _linear_source_data_v1(source, BybitModelSourceRefV1) != page.source_preimage
                    or _account_info_capture_data_v1(page.capture) != page.capture_preimage):
                raise AttachedProtectionErrorV1()
            result.append((source, page.capture.raw_body))
        return tuple(result)

    async def read(self) -> AuthenticatedAttachedProtectionReceiptV1:
        self.guard()
        self.position_sources = await self.session._capture_linear_positions_v1(self.binding,
            scope=self.target.scope, budget=self.budget, owner_guard=self.guard)
        self.position_slot = self.session._linear_positions_current
        self.guard()
        self.order_sources = await self.session._capture_linear_orders_v1(self.binding,
            scope=self.target.scope, budget=self.budget, owner_guard=self.guard)
        self.order_slot = self.session._linear_orders_current
        self.observed_at = _utc(self.session._clock())
        self.guard()
        if self.position_slot is None or self.order_slot is None:
            raise AttachedProtectionErrorV1()
        self._issued_sources_v1 = (self.position_sources, self.order_sources,
            self.position_slot, self.order_slot, self.position_slot.pages, self.order_slot.pages,
            tuple((page, page.source, page.capture, page.source_preimage, page.capture_preimage, page.capture.raw_body)
                for slot in (self.position_slot, self.order_slot) for page in slot.pages))
        self.view = _project(self.target, positions=self._pages("POSITIONS"), orders=self._pages("ORDERS"),
            cycle_id=self.cycle_id, observed_at=self.observed_at)
        self.receipt = object.__new__(AuthenticatedAttachedProtectionReceiptV1)
        self._issued_v1 = self.receipt, self.view
        self._issued_view_preimage_v1 = _view_data(self.view)
        self.require(self.receipt, self.target)
        return self.receipt

    def require(self, receipt: AuthenticatedAttachedProtectionReceiptV1,
                target: AttachedProtectionTargetV1) -> AttachedProtectionViewV1:
        self.guard()
        if (type(receipt) is not AuthenticatedAttachedProtectionReceiptV1 or receipt is not self.receipt
                or type(target) is not AttachedProtectionTargetV1 or target != self.target
                or type(self.view) is not AttachedProtectionViewV1):
            raise AttachedProtectionErrorV1()
        self.session._require_linear_positions_v1(self.position_sources, binding=self.binding, scope=target.scope)
        self.session._require_linear_orders_v1(self.order_sources, binding=self.binding, scope=target.scope)
        self.guard()
        positions, orders = self._pages("POSITIONS"), self._pages("ORDERS")
        now = _utc(self.session._clock())
        for source, _raw in (*positions, *orders):
            if source.envelope_time_ms is None:
                raise AttachedProtectionErrorV1()
            stamp = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=source.envelope_time_ms)
            if (not self.target.latest_fill_at <= stamp <= now
                    or any(not timedelta(0) <= now - value <= self.ttl for value in
                        (source.request_started_at, source.received_at, stamp))):
                raise AttachedProtectionErrorV1()
        self.budget.check()
        self.guard()
        return self.require_closed(receipt, target, now=self._checked_clock_v1[0])

    def _require_issued_sources_v1(self) -> None:
        issued = self._issued_sources_v1
        if (type(issued) is not tuple or len(issued) != 7
                or self.position_sources is not issued[0] or self.order_sources is not issued[1]
                or self.position_slot is not issued[2] or self.order_slot is not issued[3]
                or self.position_slot is None or self.order_slot is None
                or self.session._linear_positions_current is not self.position_slot
                or self.session._linear_orders_current is not self.order_slot
                or self.position_slot.pages is not issued[4] or self.order_slot.pages is not issued[5]):
            raise AttachedProtectionErrorV1()
        for page, source, capture, source_preimage, capture_preimage, raw in issued[6]:
            if (page.source is not source or page.capture is not capture
                    or page.source_preimage is not source_preimage or page.capture_preimage is not capture_preimage
                    or capture.raw_body is not raw
                    or _linear_source_data_v1(source, BybitModelSourceRefV1) != source_preimage
                    or _account_info_capture_data_v1(capture) != capture_preimage):
                raise AttachedProtectionErrorV1()

    def require_closed(self, receipt: AuthenticatedAttachedProtectionReceiptV1,
                       target: AttachedProtectionTargetV1, *, now: datetime) -> AttachedProtectionViewV1:
        """Callback-free recheck after the consuming runtime sampled its own clock."""
        now = _utc(now)
        self._require_issued_sources_v1()
        positions, orders = self._pages("POSITIONS"), self._pages("ORDERS")
        # No clock, owner or budget callback follows this closed final suffix.
        final_now, final_mono = self._checked_clock_v1
        links, issued = self._original_links_v1, self._issued_v1
        if (self._original is not self or self.session is not links[0] or self.binding is not links[1]
                or self.target is not links[2] or self.budget is not links[3] or self.owner_guard is not links[4]
                or self.session._attached_protection_current_v1 is not self
                or self.budget._terminal_failure is not None
                or (self.started_at, self.started_mono, self.ttl, self.cycle_id) != self._limits_v1
                or type(self.view) is not AttachedProtectionViewV1
                or type(issued) is not tuple or len(issued) != 2 or issued[0] is not receipt or issued[0] is not self.receipt
                or issued[1] is not self.view or _target_data(self.target) != self._target_preimage_v1
                or type(target) is not AttachedProtectionTargetV1 or _target_data(target) != self._target_preimage_v1
                or not timedelta(0) <= now - self.started_at <= self.ttl
                or not timedelta(0) <= final_now - self.started_at <= self.ttl
                or not 0 <= final_mono - self.started_mono <= self.ttl.total_seconds()):
            raise AttachedProtectionErrorV1()
        if positions != self._pages("POSITIONS") or orders != self._pages("ORDERS"):
            raise AttachedProtectionErrorV1()
        for source, _raw in (*positions, *orders):
            if source.envelope_time_ms is None:
                raise AttachedProtectionErrorV1()
            stamp = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=source.envelope_time_ms)
            if (not self.target.latest_fill_at <= stamp <= final_now or stamp > now
                    or any(not timedelta(0) <= sampled - value <= self.ttl
                        for sampled in (final_now, now)
                        for value in (source.request_started_at, source.received_at, stamp, self.observed_at))):
                raise AttachedProtectionErrorV1()
        projected = _project(self.target, positions=positions, orders=orders,
            cycle_id=self.cycle_id, observed_at=self.observed_at)
        if (_view_data(self.view) != self._issued_view_preimage_v1
                or _view_data(projected) != self._issued_view_preimage_v1):
            raise AttachedProtectionErrorV1()
        return self.view
