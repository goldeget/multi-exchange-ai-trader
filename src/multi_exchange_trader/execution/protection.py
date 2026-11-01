"""Pure, fail-closed owned execution projection and venue evidence checks.

No venue effect is performed here. A conflict preserves the report for durable
journaling by the caller; it never guesses exposure or silently clamps a fill.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import MAX_EMAX, MIN_EMIN, Decimal, localcontext
from typing import Literal

Side = Literal["BUY", "SELL"]
Role = Literal["ENTRY", "STOP", "TAKE_PROFIT", "EXIT"]
Status = Literal["OPEN_PROTECTED", "OPEN_UNPROTECTED", "ATTENTION", "FLAT_UNVERIFIED"]


def _add(left: Decimal, right: Decimal) -> Decimal:
    """Exact addition independent of the caller's Decimal precision."""
    exponent = min(int(left.as_tuple().exponent), int(right.as_tuple().exponent))
    with localcontext() as context:
        context.prec = max(left.adjusted(), right.adjusted()) - exponent + 3
        context.Emax = MAX_EMAX
        context.Emin = MIN_EMIN
        return left + right


def _sum(values: Iterable[Decimal]) -> Decimal:
    total = Decimal(0)
    for value in values:
        total = _add(total, value)
    return total


def _text(*values: str) -> None:
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("Explicit nonempty identifiers required")


def _decimal(value: Decimal, *, zero: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("Finite Decimal required")
    # Bound input representation before exact arithmetic allocates precision.
    if (len(value.as_tuple().digits) > 256
            or abs(int(value.as_tuple().exponent)) > 1000
            or (value != 0 and abs(value.adjusted()) > 1000)):
        raise ValueError("Decimal representation exceeds technical bounds")
    if value < 0 or (value == 0 and not zero):
        raise ValueError("Positive quantity/price required")


def _time(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Timezone-aware UTC instant required")


def _side(value: str) -> None:
    if value not in ("BUY", "SELL"):
        raise ValueError("Explicit BUY/SELL required")


def _generation(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("Nonnegative integer generation required")


def _fresh(observed: datetime, now: datetime, max_age: timedelta) -> bool:
    return timedelta(0) <= now - observed <= max_age


def _clock(now: datetime, max_age: timedelta) -> None:
    _time(now)
    if not isinstance(max_age, timedelta) or max_age < timedelta(0):
        raise ValueError("Explicit nonnegative evidence age required")


@dataclass(frozen=True)
class ProtectionScope:
    account_id: str
    environment: str

    def __post_init__(self) -> None:
        _text(self.account_id)
        if self.environment not in ("DEMO", "LIVE"):
            raise ValueError("Explicit DEMO/LIVE environment required")


@dataclass(frozen=True)
class OwnedOrder:
    client_order_id: str
    position_id: str
    instrument_id: str
    side: Side
    role: Role
    quantity: Decimal
    reduce_only: bool
    generation: int
    policy_revision: str
    trigger_price: Decimal | None = None
    trigger_source: str | None = None

    def __post_init__(self) -> None:
        _text(self.client_order_id, self.position_id, self.instrument_id, self.policy_revision)
        _side(self.side)
        _decimal(self.quantity)
        _generation(self.generation)
        if self.role not in ("ENTRY", "STOP", "TAKE_PROFIT", "EXIT"):
            raise ValueError("Unknown order role")
        if type(self.reduce_only) is not bool:
            raise ValueError("Explicit reduce-only flag required")
        if self.role in ("STOP", "TAKE_PROFIT"):
            if self.trigger_price is None or self.trigger_source is None:
                raise ValueError("Protection requires explicit trigger price/source")
            _decimal(self.trigger_price)
            _text(self.trigger_source)


@dataclass(frozen=True)
class FillEvent:
    scope: ProtectionScope
    execution_id: str
    client_order_id: str
    position_id: str
    instrument_id: str
    side: Side
    quantity: Decimal
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ProtectionScope):
            raise TypeError("Explicit ProtectionScope required")
        _text(self.execution_id, self.client_order_id, self.position_id, self.instrument_id)
        _side(self.side)
        _decimal(self.quantity)
        _time(self.occurred_at)


@dataclass(frozen=True)
class ProjectionConflict:
    reason: str
    event: FillEvent | None = None
    order: OwnedOrder | None = None
    client_order_id: str | None = None


@dataclass(frozen=True)
class ProtectionProjection:
    scope: ProtectionScope
    position_id: str
    instrument_id: str
    entry_side: Side
    orders: tuple[OwnedOrder, ...] = ()
    executions: tuple[FillEvent, ...] = ()
    cancelled_order_ids: tuple[str, ...] = ()
    conflicts: tuple[ProjectionConflict, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ProtectionScope):
            raise TypeError("Explicit ProtectionScope required")
        _text(self.position_id, self.instrument_id)
        _side(self.entry_side)
        if any(
            type(value) is not tuple
            for value in (self.orders, self.executions, self.cancelled_order_ids, self.conflicts)
        ):
            raise ValueError("Immutable projection tuples required")
        # Restored projections must satisfy the same ownership invariants as
        # reducer-produced projections. Caller-supplied tuples are not authority.
        mapping: dict[str, OwnedOrder] = {}
        for order in self.orders:
            if (
                order.client_order_id in mapping
                or order.position_id != self.position_id
                or order.instrument_id != self.instrument_id
                or (order.role == "ENTRY" and (order.side != self.entry_side or order.reduce_only))
                or (
                    order.role != "ENTRY"
                    and (order.side == self.entry_side or not order.reduce_only)
                )
            ):
                raise ValueError("Invalid immutable owned-order mapping")
            mapping[order.client_order_id] = order
        seen: set[str] = set()
        filled: dict[str, Decimal] = {}
        owned = Decimal(0)
        for event in self.executions:
            fill_order = mapping.get(event.client_order_id)
            if (
                fill_order is None
                or event.execution_id in seen
                or event.scope != self.scope
                or event.position_id != self.position_id
                or event.instrument_id != self.instrument_id
                or event.side != fill_order.side
            ):
                raise ValueError("Invalid owned-execution history")
            seen.add(event.execution_id)
            total = _add(filled.get(event.client_order_id, Decimal(0)), event.quantity)
            if total > fill_order.quantity:
                raise ValueError("Execution history exceeds immutable order intent")
            filled[event.client_order_id] = total
            owned = _add(
                owned,
                event.quantity if fill_order.role == "ENTRY" else event.quantity.copy_negate(),
            )
            if owned < 0:
                raise ValueError("Execution history flips owned exposure")
        if len(set(self.cancelled_order_ids)) != len(self.cancelled_order_ids) or any(
            order_id not in mapping for order_id in self.cancelled_order_ids
        ):
            raise ValueError("Invalid owned cancellation history")

    @property
    def latest_fill_at(self) -> datetime | None:
        return max((event.occurred_at for event in self.executions), default=None)

    @property
    def signed_quantity(self) -> Decimal:
        return _sum(
            (
                event.quantity if event.side == "BUY" else event.quantity.copy_negate()
                for event in self.executions
            ),
        )

    @property
    def owned_quantity(self) -> Decimal:
        return self.signed_quantity.copy_abs()

    @property
    def entries_blocked(self) -> bool:
        """Projection alone cannot prove protection of any open exposure."""
        return bool(self.conflicts) or self.owned_quantity > 0


def _conflict(
    state: ProtectionProjection,
    reason: str,
    *,
    event: FillEvent | None = None,
    order: OwnedOrder | None = None,
    client_order_id: str | None = None,
) -> ProtectionProjection:
    conflict = ProjectionConflict(reason, event, order, client_order_id)
    if conflict in state.conflicts:
        return state
    return replace(state, conflicts=state.conflicts + (conflict,))


def register_order(state: ProtectionProjection, order: OwnedOrder) -> ProtectionProjection:
    for existing in state.orders:
        if existing.client_order_id == order.client_order_id:
            if existing == order:
                return state
            return _conflict(state, "IMMUTABLE_ORDER_MAPPING_CONFLICT", order=order)
    if order.position_id != state.position_id or order.instrument_id != state.instrument_id:
        return _conflict(state, "ORDER_POSITION_SCOPE_MISMATCH", order=order)
    if order.role == "ENTRY":
        valid = order.side == state.entry_side and not order.reduce_only
    else:
        valid = (
            order.side != state.entry_side
            and order.reduce_only
            and order.quantity <= state.owned_quantity
        )
    if not valid:
        return _conflict(state, "UNSAFE_ORDER_INTENT", order=order)
    return replace(state, orders=state.orders + (order,))


def record_order_cancelled(
    state: ProtectionProjection, client_order_id: str
) -> ProtectionProjection:
    """Cancel evidence never erases fills or prevents a real late execution."""
    if not any(order.client_order_id == client_order_id for order in state.orders):
        return _conflict(state, "UNKNOWN_CANCEL_ORDER", client_order_id=client_order_id)
    if client_order_id in state.cancelled_order_ids:
        return state
    return replace(state, cancelled_order_ids=state.cancelled_order_ids + (client_order_id,))


def apply_fill(state: ProtectionProjection, event: FillEvent) -> ProtectionProjection:
    for previous in state.executions:
        if previous.execution_id == event.execution_id:
            if previous == event:
                return state
            return _conflict(state, "EXECUTION_ID_CONFLICT", event=event)
    if (
        event.scope != state.scope
        or event.position_id != state.position_id
        or event.instrument_id != state.instrument_id
    ):
        return _conflict(state, "FILL_SCOPE_MISMATCH", event=event)
    order = next(
        (item for item in state.orders if item.client_order_id == event.client_order_id), None
    )
    if order is None or event.side != order.side:
        return _conflict(state, "UNOWNED_OR_WRONG_SIDE_FILL", event=event)
    prior = _sum(
        (
            item.quantity
            for item in state.executions
            if item.client_order_id == event.client_order_id
        ),
    )
    if _add(prior, event.quantity) > order.quantity:
        return _conflict(state, "FILL_EXCEEDS_ORDER_INTENT", event=event)
    if order.role != "ENTRY" and (not order.reduce_only or event.quantity > state.owned_quantity):
        return _conflict(state, "EXIT_EXCEEDS_OWNED_EXPOSURE", event=event)
    return replace(state, executions=state.executions + (event,))


@dataclass(frozen=True)
class ProtectionPolicy:
    revision: str
    generation: int
    stop_price: Decimal
    take_profit_price: Decimal
    trigger_source: str

    def __post_init__(self) -> None:
        _text(self.revision, self.trigger_source)
        _generation(self.generation)
        _decimal(self.stop_price)
        _decimal(self.take_profit_price)


@dataclass(frozen=True)
class ProtectionEvidence:
    scope: ProtectionScope
    client_order_id: str
    position_id: str
    instrument_id: str
    side: Side
    role: Role
    quantity: Decimal
    reduce_only: bool
    generation: int
    policy_revision: str
    trigger_price: Decimal
    trigger_source: str
    state: str
    observed_at: datetime
    cycle_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ProtectionScope):
            raise TypeError("Explicit ProtectionScope required")
        _text(
            self.client_order_id,
            self.position_id,
            self.instrument_id,
            self.policy_revision,
            self.trigger_source,
            self.state,
            self.cycle_id,
        )
        _side(self.side)
        _decimal(self.quantity, zero=True)
        _decimal(self.trigger_price)
        _generation(self.generation)
        _time(self.observed_at)
        if type(self.reduce_only) is not bool:
            raise ValueError("Explicit reduce-only required")


@dataclass(frozen=True)
class PositionEvidence:
    scope: ProtectionScope
    position_id: str
    instrument_id: str
    side: Side
    quantity: Decimal
    observed_at: datetime
    cycle_id: str
    complete: bool
    has_external_activity: bool

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ProtectionScope):
            raise TypeError("Explicit ProtectionScope required")
        _text(self.position_id, self.instrument_id, self.cycle_id)
        _side(self.side)
        _decimal(self.quantity, zero=True)
        _time(self.observed_at)
        if type(self.complete) is not bool or type(self.has_external_activity) is not bool:
            raise ValueError("Explicit completeness/external activity flags required")


@dataclass(frozen=True)
class ProtectionAssessment:
    status: Status
    owned_quantity: Decimal
    unprotected_quantity: Decimal
    entries_blocked: bool
    reasons: tuple[str, ...]
    proposed_cancellations: tuple[str, ...] = ()


def _matches_order(
    state: ProtectionProjection,
    order: OwnedOrder,
    report: ProtectionEvidence,
    position: PositionEvidence,
    now: datetime,
    max_age: timedelta,
) -> bool:
    return (
        report.scope == state.scope
        and report.position_id == state.position_id
        and report.instrument_id == state.instrument_id
        and report.side == order.side
        and report.role == order.role
        and report.reduce_only is True
        and report.generation == order.generation
        and report.policy_revision == order.policy_revision
        and report.trigger_price == order.trigger_price
        and report.trigger_source == order.trigger_source
        and report.state == "ACTIVE"
        and _fresh(report.observed_at, now, max_age)
        and (state.latest_fill_at is None or report.observed_at >= state.latest_fill_at)
        and report.cycle_id == position.cycle_id
        and report.client_order_id not in state.cancelled_order_ids
    )


def assess_protection(
    state: ProtectionProjection,
    policy: ProtectionPolicy,
    evidence: tuple[ProtectionEvidence, ...],
    position: PositionEvidence | None,
    *,
    now: datetime,
    max_age: timedelta,
) -> ProtectionAssessment:
    """Both SL/TP must exactly cover current ownership under current policy.

    Partial valid reports quantify uncovered exposure but do not permit a
    protected label or cancellation of previous protection generations.
    """
    _clock(now, max_age)
    quantity = state.owned_quantity
    if state.conflicts:
        return ProtectionAssessment("ATTENTION", quantity, quantity, True, ("PROJECTION_CONFLICT",))
    if quantity == 0:
        return ProtectionAssessment(
            "FLAT_UNVERIFIED", quantity, quantity, True, ("INDEPENDENT_FLAT_EVIDENCE_REQUIRED",)
        )
    if position is None:
        return ProtectionAssessment("ATTENTION", quantity, quantity, True, ("MISSING_POSITION",))
    if (
        position.scope != state.scope
        or position.position_id != state.position_id
        or position.instrument_id != state.instrument_id
        or position.side != state.entry_side
        or position.quantity != quantity
        or not position.complete
        or position.has_external_activity
        or not _fresh(position.observed_at, now, max_age)
        or (state.latest_fill_at is not None and position.observed_at < state.latest_fill_at)
    ):
        return ProtectionAssessment("ATTENTION", quantity, quantity, True, ("POSITION_CONFLICT",))
    owned_ids = {order.client_order_id for order in state.orders}
    reports: dict[str, ProtectionEvidence] = {}
    for incoming_report in evidence:
        if incoming_report.client_order_id not in owned_ids:
            continue
        previous = reports.get(incoming_report.client_order_id)
        if previous is not None and previous != incoming_report:
            return ProtectionAssessment(
                "ATTENTION", quantity, quantity, True, ("CONTRADICTORY_PROTECTION_REPORT",)
            )
        reports[incoming_report.client_order_id] = incoming_report
    coverage = {"STOP": Decimal(0), "TAKE_PROFIT": Decimal(0)}
    exact = {"STOP": False, "TAKE_PROFIT": False}
    contradictions: list[str] = []
    for order in state.orders:
        report = reports.get(order.client_order_id)
        # Only immutable registered IDs establish ownership. A current ACTIVE
        # owned report cannot be silently discarded behind a valid sibling.
        if (
            report is not None
            and order.role in coverage
            and report.state == "ACTIVE"
            and _fresh(report.observed_at, now, max_age)
            and (state.latest_fill_at is None or report.observed_at >= state.latest_fill_at)
            and report.cycle_id == position.cycle_id
            and (
                not _matches_order(state, order, report, position, now, max_age)
                or report.quantity > order.quantity
            )
        ):
            contradictions.append("CONTRADICTORY_PROTECTION_REPORT")
        if (
            report is not None
            and order.role in coverage
            and order.generation == policy.generation
            and report.state == "ACTIVE"
            and _fresh(report.observed_at, now, max_age)
            and (
                order.policy_revision != policy.revision
                or order.trigger_source != policy.trigger_source
                or order.trigger_price
                != (policy.stop_price if order.role == "STOP" else policy.take_profit_price)
            )
        ):
            contradictions.append("CURRENT_GENERATION_POLICY_CONFLICT")
        if (
            report is None
            or order.role not in coverage
            or order.generation != policy.generation
            or order.policy_revision != policy.revision
            or order.trigger_source != policy.trigger_source
            or order.trigger_price
            != (policy.stop_price if order.role == "STOP" else policy.take_profit_price)
            or not _matches_order(state, order, report, position, now, max_age)
        ):
            continue
        # Never treat over-sized or intent-exceeding evidence as partial coverage.
        if report.quantity <= quantity and report.quantity <= order.quantity:
            coverage[order.role] = max(coverage[order.role], report.quantity)
        if report.quantity == quantity and order.quantity == quantity:
            exact[order.role] = True
    protected = exact["STOP"] and exact["TAKE_PROFIT"]
    uncovered = _add(quantity, min(coverage.values()).copy_negate())
    if contradictions:
        return ProtectionAssessment(
            "ATTENTION", quantity, uncovered, True, tuple(dict.fromkeys(contradictions))
        )
    if not protected:
        return ProtectionAssessment(
            "OPEN_UNPROTECTED",
            quantity,
            uncovered,
            True,
            ("MISSING_EXACT_CURRENT_STOP_OR_TAKE_PROFIT",),
        )
    cancellations = tuple(
        order.client_order_id
        for order in state.orders
        if order.role in ("STOP", "TAKE_PROFIT")
        and order.generation < policy.generation
        and (old_report := reports.get(order.client_order_id)) is not None
        and _matches_order(state, order, old_report, position, now, max_age)
        and old_report.quantity == order.quantity
    )
    return ProtectionAssessment("OPEN_PROTECTED", quantity, Decimal(0), False, (), cancellations)


@dataclass(frozen=True)
class FlatEvidence:
    scope: ProtectionScope
    position_id: str
    instrument_id: str
    position_quantity: Decimal
    working_order_ids: tuple[str, ...]
    position_observed_at: datetime
    orders_observed_at: datetime
    position_cycle_id: str
    orders_cycle_id: str
    position_complete: bool
    orders_complete: bool

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ProtectionScope):
            raise TypeError("Explicit ProtectionScope required")
        _text(self.position_id, self.instrument_id, self.position_cycle_id, self.orders_cycle_id)
        _decimal(self.position_quantity, zero=True)
        _time(self.position_observed_at)
        _time(self.orders_observed_at)
        if type(self.working_order_ids) is not tuple:
            raise ValueError("Immutable order snapshot required")
        _text(*self.working_order_ids)
        if type(self.position_complete) is not bool or type(self.orders_complete) is not bool:
            raise ValueError("Explicit snapshot completeness required")


def assess_flat(
    state: ProtectionProjection,
    evidence: FlatEvidence | None,
    *,
    now: datetime,
    max_age: timedelta,
) -> Literal["STOPPED_FLAT", "FLAT_UNVERIFIED"]:
    """Require independent complete same-cycle position and all-order snapshots."""
    _clock(now, max_age)
    if state.owned_quantity or state.conflicts or evidence is None:
        return "FLAT_UNVERIFIED"
    if (
        evidence.scope != state.scope
        or evidence.position_id != state.position_id
        or evidence.instrument_id != state.instrument_id
        or evidence.position_quantity != 0
        or not evidence.position_complete
        or not evidence.orders_complete
        or evidence.position_cycle_id != evidence.orders_cycle_id
        or not _fresh(evidence.position_observed_at, now, max_age)
        or not _fresh(evidence.orders_observed_at, now, max_age)
        or (
            state.latest_fill_at is not None
            and min(evidence.position_observed_at, evidence.orders_observed_at)
            < state.latest_fill_at
        )
        or any(order.client_order_id in evidence.working_order_ids for order in state.orders)
    ):
        return "FLAT_UNVERIFIED"
    return "STOPPED_FLAT"
