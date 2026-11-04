"""Source-bound first-flat coverage for DEMO USDT-settled LINEAR only.

Every original whole-scope raw page and the entire original journal must be
empty. Zeroes describe that bounded exposure scope; they do not describe USDC,
inverse, spot, debt, daily loss, loss streak or financial/ledger qualification.
Historical terminal commands intentionally remain unsupported in this slice.
Values are constructible data; only their original owner can require currentness.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from ..account.bybit_readonly import BybitReadOnlyClient
from . import bybit_linear_model_inputs_v1 as a
from . import risk_v2_contracts as c
from .account_binding import AuthenticatedAccountBinding, BybitDemoAccountSession
from .bybit_linear_source_assembler_v1 import BybitLinearSourceAssemblerV1, BybitLinearVenueReadV1
from .bybit_position_order_projections_v1 import _page
from .journal import CommandJournal
from .monitoring_budget import MonitoringReadBudget
from .protection import ProtectionScope
from .risk_v2_math import RiskV2Rejected

type _Sources = tuple[a.BybitModelSourceRefV1, ...]


@dataclass(frozen=True)
class BybitLinearFlatCoverageV1:
    scope: ProtectionScope
    positions_sources: _Sources
    orders_sources: _Sources
    reservations: c.LinearReservationSnapshotV2
    observed_at: datetime
    valid_until: datetime
    coverage_revision: Literal["BYBIT_DEMO_USDT_LINEAR_FIRST_FLAT_V1"] = field(
        default="BYBIT_DEMO_USDT_LINEAR_FIRST_FLAT_V1", init=False)
    total_risk: Decimal = field(default=Decimal(0), init=False)
    total_margin: Decimal = field(default=Decimal(0), init=False)
    position_count: Literal[0] = field(default=0, init=False)


def bybit_linear_flat_coverage_sha256_v1(value: BybitLinearFlatCoverageV1) -> str:
    """Data identity only; this hash cannot prove an original Session or empty journal."""
    c._fields_v2(value, BybitLinearFlatCoverageV1)
    c._literal_v2(value.coverage_revision, ("BYBIT_DEMO_USDT_LINEAR_FIRST_FLAT_V1",))
    c._tuple_v2(value.positions_sources, minimum=1, maximum=64)
    c._tuple_v2(value.orders_sources, minimum=1, maximum=64)
    if (type(value.total_risk) is not Decimal or type(value.total_margin) is not Decimal
            or value.total_risk != 0 or value.total_margin != 0
            or type(value.position_count) is not int or value.position_count != 0):
        raise RiskV2Rejected("RISK_V2_HELD_COVERAGE_REQUIRED")
    return c._canonical_sha256_v2("BybitLinearFlatCoverageV1", {
        "scope": c._account_payload_v2(value.scope), "coverage_revision": value.coverage_revision,
        "positions_sources": tuple(a.bybit_source_ref_sha256_v1(s) for s in value.positions_sources),
        "orders_sources": tuple(a.bybit_source_ref_sha256_v1(s) for s in value.orders_sources),
        "reservations": c.reservation_snapshot_sha256_v2(value.reservations),
        "observed_at": c._time_text_v2(value.observed_at),
        "valid_until": c._time_text_v2(value.valid_until),
        "total_risk": "0", "total_margin": "0", "position_count": 0,
    })


@dataclass(frozen=True)
class _Issued:
    result: BybitLinearFlatCoverageV1
    binding: AuthenticatedAccountBinding
    scope: c.RiskScopeV2
    owner_generation: str
    budget: MonitoringReadBudget
    max_age: timedelta
    positions_slot: object
    orders_slot: object
    witness: str
    assembler: BybitLinearSourceAssemblerV1 | None
    read: BybitLinearVenueReadV1 | None


class BybitLinearFlatCoverageOwnerV1:
    """Issue only from complete original page collections and a wholly empty true J."""

    def __init__(self, *, session: BybitDemoAccountSession, journal: CommandJournal,
                 clock: Callable[[], datetime], owner_guard: Callable[[], None]) -> None:
        if (type(session) is not BybitDemoAccountSession or type(journal) is not CommandJournal
                or not callable(clock) or not callable(owner_guard)):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        self.session, self.journal = session, journal
        self.clock, self.owner_guard = clock, owner_guard
        self._session, self._journal = session, journal
        self._clock, self._owner_guard = clock, owner_guard
        self._current: _Issued | None = None
        self._busy: object | None = None
        self._epoch = object()

    @contextmanager
    def _operation(self) -> Iterator[object]:
        if self._busy is not None:
            self._current = None
            self._epoch = object()
            raise RiskV2Rejected("RISK_V2_LOCAL_ADMISSION_CONFLICT")
        token, epoch = object(), self._epoch
        self._busy = token
        try:
            yield token
            if self._busy is not token or self._epoch is not epoch:
                raise RiskV2Rejected("RISK_V2_LOCAL_ADMISSION_CONFLICT")
        except BaseException:
            self._current = None
            raise
        finally:
            if self._busy is token:
                self._busy = None

    def _guard(self, token: object, binding: AuthenticatedAccountBinding,
               scope: c.RiskScopeV2) -> None:
        if (self.session is not self._session or self.journal is not self._journal
                or self.clock is not self._clock or self.owner_guard is not self._owner_guard):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        self.owner_guard()
        self.session.require_observation_binding(binding, scope=scope.product_scope.account)
        if (self._busy is not token or self.session is not self._session
                or self.journal is not self._journal or self.clock is not self._clock
                or self.owner_guard is not self._owner_guard):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")

    def _empty_journal(self, scope: c.RiskScopeV2, owner_generation: str,
                       now: datetime) -> c.LinearReservationSnapshotV2:
        # Deliberately includes confirmed historical commands: this is first-flat,
        # not a reconciliation shortcut for terminal/owned historical quantities.
        if self.journal.list_commands():
            raise RiskV2Rejected("RISK_V2_HELD_COVERAGE_REQUIRED")
        snapshot = self.journal.reservation_snapshot_v2(scope, now=now,
            owner_generation=owner_generation, exclude=None)
        if (snapshot.components or snapshot.risk_amount != 0 or snapshot.margin_amount != 0
                or snapshot.position_count != 0 or snapshot.excluded_command_key is not None):
            raise RiskV2Rejected("RISK_V2_HELD_COVERAGE_REQUIRED")
        return snapshot

    def _empty_pages(self, positions: _Sources, orders: _Sources,
                     budget: MonitoringReadBudget) -> None:
        for sources, slot, is_orders in (
            (positions, self.session._linear_positions_current, False),
            (orders, self.session._linear_orders_current, True),
        ):
            if slot is None or len(sources) != len(slot.pages):
                raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
            for source, page in zip(sources, slot.pages, strict=True):
                if source is not page.source or page.capture.budget is not budget:
                    raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
                rows, _cursor = _page(source, page.capture.raw_body, orders=is_orders)
                if rows:
                    raise RiskV2Rejected("RISK_V2_HELD_COVERAGE_REQUIRED")

    async def capture_v1(self, binding: AuthenticatedAccountBinding, *, scope: c.RiskScopeV2,
                         owner_generation: str, budget: MonitoringReadBudget, max_age: timedelta,
                         assembler: BybitLinearSourceAssemblerV1 | None = None,
                         read: BybitLinearVenueReadV1 | None = None) -> BybitLinearFlatCoverageV1:
        with self._operation() as token:
            self._current = None
            c._scope_payload_v2(scope)
            c._identifier_v2(owner_generation)
            if (scope.product_scope.account.environment != "DEMO" or scope.product_scope.product != "LINEAR"
                    or scope.instrument.venue != "BYBIT" or type(budget) is not MonitoringReadBudget
                    or type(max_age) is not timedelta or not timedelta(0) < max_age <= timedelta(minutes=5)
                    or (assembler is None) != (read is None)):
                raise RiskV2Rejected("RISK_V2_SCOPE_MISMATCH")
            self._guard(token, binding, scope)
            self._empty_journal(scope, owner_generation, c._utc_v2(self.clock()))
            if assembler is None:
                def guard() -> None:
                    active = self._busy
                    if active is None:
                        raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
                    self._guard(active, binding, scope)

                with budget.reuse_or_open_receipt():
                    positions = await self.session._capture_linear_positions_v1(binding,
                        scope=scope.product_scope.account, budget=budget, owner_guard=guard)
                    orders = await self.session._capture_linear_orders_v1(binding,
                        scope=scope.product_scope.account, budget=budget, owner_guard=guard)
            else:
                if (type(assembler) is not BybitLinearSourceAssemblerV1 or assembler.session is not self.session
                        or assembler.journal is not self.journal or read is None):
                    raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
                assembler.require_current_v1(read)
                original = assembler._current
                if (original is None or original.binding is not binding
                        or c._scope_payload_v2(original.scope) != c._scope_payload_v2(scope)):
                    raise RiskV2Rejected("RISK_V2_SCOPE_MISMATCH")
                by_kind = dict(original.sources)
                positions, orders = by_kind["POSITIONS"], by_kind["ORDERS"]
            # Original family validators establish raw/hash/query/cursor completeness;
            # all original rows, including later pages, must physically be absent.
            now = c._utc_v2(self.clock())
            reservations = self._empty_journal(scope, owner_generation, now)
            sources = (*positions, *orders)
            identity = self.session.require_observation_binding(binding, scope=scope.product_scope.account)
            expiry = min(*(s.request_started_at + max_age for s in sources),
                identity.observed_at + self.session._max_age,
                identity.server_observed_at + self.session._max_age)
            if read is not None:
                expiry = min(expiry, read.venue.valid_until)
            result = BybitLinearFlatCoverageV1(scope.product_scope.account, positions, orders,
                reservations, now, expiry)
            self._current = _Issued(result, binding, scope, owner_generation, budget, max_age,
                self.session._linear_positions_current, self.session._linear_orders_current,
                bybit_linear_flat_coverage_sha256_v1(result), assembler, read)
            self._require(result, token)
            return result

    def _require(self, result: BybitLinearFlatCoverageV1, token: object) -> None:
        issued = self._current
        if issued is None or result is not issued.result or self._busy is not token:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        if bybit_linear_flat_coverage_sha256_v1(result) != issued.witness:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        session, journal, epoch = self.session, self.journal, self._epoch
        if type(session) is not BybitDemoAccountSession or type(session._client) is not BybitReadOnlyClient:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        client, binding, pair = session._client, session._binding, session._credential_pair
        if type(pair) is not tuple or len(pair) != 2 or any(type(value) is not str for value in pair):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        http_client, http_state = client._client, client._client._state
        clock, guard = self.clock, self.owner_guard
        now = c._utc_v2(clock())
        issued.budget.check()
        self._guard(token, issued.binding, issued.scope)
        if issued.assembler is None:
            session._require_linear_positions_v1(result.positions_sources,
                binding=issued.binding, scope=result.scope)
            session._require_linear_orders_v1(result.orders_sources,
                binding=issued.binding, scope=result.scope)
        else:
            if issued.read is None:
                raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
            issued.assembler.require_current_v1(issued.read)
        self._empty_pages(result.positions_sources, result.orders_sources, issued.budget)
        if now >= result.valid_until or any(not timedelta(0) <= now - source.request_started_at <= issued.max_age
                for source in (*result.positions_sources, *result.orders_sources)):
            raise RiskV2Rejected("RISK_V2_EXPIRED")
        current = self._empty_journal(issued.scope, issued.owner_generation, now)
        if current.snapshot_id != result.reservations.snapshot_id:
            raise RiskV2Rejected("RISK_V2_LOCAL_ADMISSION_CONFLICT")
        # No callbacks follow the original sources and final true-J check.
        if (self._current is not issued or self._busy is not token or self._epoch is not epoch
                or self.session is not session or self.journal is not journal
                or self.clock is not clock or self.owner_guard is not guard
                or session._closed is not False or session._binding is not binding
                or binding is not issued.binding or session._client is not client
                or session._credential_client is not client or session._credential_pair is not pair
                or client._client is not http_client or http_client._state is not http_state
                or session._linear_positions_current is not issued.positions_slot
                or session._linear_orders_current is not issued.orders_slot
                or journal._own_pending_closed_v2 is not False
                or type(client.environment) is not str or client.environment != "demo"
                or type(client.base_url) is not str or client.base_url != "https://api-demo.bybit.com"
                or type(session._api_key) is not str or type(session._api_secret) is not str
                or type(client._api_key) is not str or type(client._api_secret) is not str
                or (session._api_key, session._api_secret) != pair
                or (client._api_key, client._api_secret) != pair):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")

    def require_current_v1(self, result: BybitLinearFlatCoverageV1) -> BybitLinearFlatCoverageV1:
        with self._operation() as token:
            self._require(result, token)
            return result
