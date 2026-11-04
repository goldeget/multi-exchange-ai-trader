"""Empty venue exposure plus the one original local PENDING reservation.

This is the post-registration counterpart of first-flat coverage. Venue zeroes
describe only complete USDT-linear position/order pages. The full actual hold
and own-command-excluded evaluation remain separate original journal values.
Neither this value nor its owner qualifies fees, daily loss or account finance.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from ..account.bybit_readonly import BybitReadOnlyClient, _account_info_capture_data_v1
from . import bybit_linear_model_inputs_v1 as a
from . import reservations_v2 as v
from . import risk_v2_contracts as c
from .account_binding import (
    AuthenticatedAccountBinding,
    BybitDemoAccountSession,
    _linear_capture_refs_v1,
    _linear_source_data_v1,
)
from .bybit_linear_source_assembler_v1 import BybitLinearSourceAssemblerV1, BybitLinearVenueReadV1
from .bybit_position_order_projections_v1 import _page
from .journal import CommandJournal, _OwnPendingDataV2, _OwnPendingViewV2
from .monitoring_budget import MonitoringReadBudget
from .protection import ProtectionScope
from .risk_v2_math import RiskV2Rejected

type _Sources = tuple[a.BybitModelSourceRefV1, ...]


@dataclass(frozen=True)
class BybitLinearPendingCoverageV1:
    scope: ProtectionScope
    command_key: str
    command_identity_sha256: str
    positions_sources: _Sources
    orders_sources: _Sources
    reservation: v.ReservationV2
    reservations: c.LinearReservationSnapshotV2
    evaluation_reservations: c.LinearReservationSnapshotV2
    observed_at: datetime
    valid_until: datetime
    coverage_revision: Literal["BYBIT_DEMO_USDT_LINEAR_FLAT_OWN_PENDING_V1"] = field(
        default="BYBIT_DEMO_USDT_LINEAR_FLAT_OWN_PENDING_V1", init=False)
    # Venue exposure only; never add these zeroes over the separate full hold.
    total_risk: Decimal = field(default=Decimal(0), init=False)
    total_margin: Decimal = field(default=Decimal(0), init=False)
    position_count: Literal[0] = field(default=0, init=False)


def bybit_linear_pending_coverage_sha256_v1(value: BybitLinearPendingCoverageV1) -> str:
    c._fields_v2(value, BybitLinearPendingCoverageV1)
    c._literal_v2(value.coverage_revision, ("BYBIT_DEMO_USDT_LINEAR_FLAT_OWN_PENDING_V1",))
    c._tuple_v2(value.positions_sources, minimum=1, maximum=64)
    c._tuple_v2(value.orders_sources, minimum=1, maximum=64)
    if (type(value.total_risk) is not Decimal or value.total_risk != 0
            or type(value.total_margin) is not Decimal or value.total_margin != 0
            or type(value.position_count) is not int or value.position_count != 0):
        raise RiskV2Rejected("RISK_V2_HELD_COVERAGE_REQUIRED")
    return c._canonical_sha256_v2("BybitLinearPendingCoverageV1", {
        "scope": c._account_payload_v2(value.scope),
        "command_key": c._identifier_v2(value.command_key),
        "command_identity_sha256": c._digest_v2(value.command_identity_sha256),
        "positions_sources": tuple(a.bybit_source_ref_sha256_v1(s) for s in value.positions_sources),
        "orders_sources": tuple(a.bybit_source_ref_sha256_v1(s) for s in value.orders_sources),
        "reservation": v._reservation_payload(value.reservation),
        "reservations": c.reservation_snapshot_sha256_v2(value.reservations),
        "evaluation_reservations": c.reservation_snapshot_sha256_v2(value.evaluation_reservations),
        "observed_at": c._time_text_v2(value.observed_at),
        "valid_until": c._time_text_v2(value.valid_until),
        "coverage_revision": value.coverage_revision,
        "total_risk": "0", "total_margin": "0", "position_count": 0,
    })


@dataclass(frozen=True)
class _Issued:
    result: BybitLinearPendingCoverageV1
    binding: AuthenticatedAccountBinding
    scope: c.RiskScopeV2
    owner_generation: str
    budget: MonitoringReadBudget
    max_age: timedelta
    view: _OwnPendingViewV2
    data: _OwnPendingDataV2
    positions_slot: object
    orders_slot: object
    witness: str
    assembler: BybitLinearSourceAssemblerV1 | None
    read: BybitLinearVenueReadV1 | None


class BybitLinearPendingCoverageOwnerV1:
    """Retain original whole venue pages and exactly one true-J own PENDING pair."""

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

    def _pending(self, view: _OwnPendingViewV2, scope: c.RiskScopeV2, *, command_key: str,
                 identity: str, generation: str, now: datetime) -> _OwnPendingDataV2:
        data = self.journal._require_own_pending_view_v2(view, scope=scope,
            command_key=command_key, expected_command_identity_sha256=identity, now=now)
        records = self.journal.list_commands()
        if (len(records) != 1 or records[0] != data.record
                or data.full.owner_generation != generation
                or len(data.full.components) != 1 or data.full.position_count != 1
                or data.full.excluded_command_key is not None
                or data.full.risk_amount != data.reservation.risk_amount
                or data.full.margin_amount != data.reservation.margin_amount
                or data.evaluation.excluded_command_key != command_key
                or data.evaluation.components or data.evaluation.risk_amount != 0
                or data.evaluation.margin_amount != 0 or data.evaluation.position_count != 0):
            raise RiskV2Rejected("RISK_V2_HELD_COVERAGE_REQUIRED")
        return data

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
                         view: _OwnPendingViewV2, command_key: str,
                         expected_command_identity_sha256: str,
                         assembler: BybitLinearSourceAssemblerV1 | None = None,
                         read: BybitLinearVenueReadV1 | None = None) -> BybitLinearPendingCoverageV1:
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
            data = self._pending(view, scope, command_key=command_key,
                identity=expected_command_identity_sha256, generation=owner_generation,
                now=c._utc_v2(self.clock()))

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
                if (type(assembler) is not BybitLinearSourceAssemblerV1 or read is None
                        or assembler.session is not self.session or assembler.journal is not self.journal):
                    raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
                assembler.require_current_v1(read)
                original = assembler._current
                if (original is None or original.binding is not binding
                        or c._scope_payload_v2(original.scope) != c._scope_payload_v2(scope)
                        or read.full_reservations is not data.full
                        or read.evaluation_reservations is not data.evaluation):
                    raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
                sources = dict(original.sources)
                positions, orders = sources["POSITIONS"], sources["ORDERS"]
            now = c._utc_v2(self.clock())
            identity = self.session.require_observation_binding(binding, scope=scope.product_scope.account)
            expiry = min(*(s.request_started_at + max_age for s in (*positions, *orders)),
                identity.observed_at + self.session._max_age,
                identity.server_observed_at + self.session._max_age, data.intent.envelope.valid_until)
            if read is not None:
                expiry = min(expiry, read.venue.valid_until)
            result = BybitLinearPendingCoverageV1(scope.product_scope.account, command_key,
                expected_command_identity_sha256, positions, orders, data.reservation,
                data.full, data.evaluation, now, expiry)
            self._current = _Issued(result, binding, scope, owner_generation, budget, max_age,
                view, data, self.session._linear_positions_current, self.session._linear_orders_current,
                bybit_linear_pending_coverage_sha256_v1(result), assembler, read)
            self._require(result, token)
            return result

    def _require(self, result: BybitLinearPendingCoverageV1, token: object) -> None:
        issued = self._current
        if issued is None or result is not issued.result or self._busy is not token:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        if bybit_linear_pending_coverage_sha256_v1(result) != issued.witness:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        session, journal, epoch = self.session, self.journal, self._epoch
        if type(session) is not BybitDemoAccountSession or type(session._client) is not BybitReadOnlyClient:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        client, binding, pair = session._client, session._binding, session._credential_pair
        identity = session._identity
        if type(pair) is not tuple or len(pair) != 2 or any(type(value) is not str for value in pair):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        http_client, http_state = client._client, client._client._state
        clock, guard = self.clock, self.owner_guard
        assembler = issued.assembler
        assembler_slot = None if assembler is None else assembler._current
        assembler_refs = None if assembler is None else (
            assembler.clock, assembler.owner_guard, assembler.public_marks,
            assembler.catalog_producer, assembler._epoch)
        page_witnesses = []
        for slot in (session._linear_positions_current, session._linear_orders_current):
            if slot is None:
                raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
            for page in slot.pages:
                page_witnesses.append((slot, slot.pages, page, page.source, page.capture,
                    page.source_preimage, page.capture_preimage, _linear_capture_refs_v1(page.capture)))
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
        # Original guards/source validators can advance time. Sample the original
        # clock only after those callbacks, then perform closed data/J replays.
        now = c._utc_v2(clock())
        if bybit_linear_pending_coverage_sha256_v1(result) != issued.witness:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        for slot, pages, page, source, capture, source_data, capture_data, refs in page_witnesses:
            if (slot.pages is not pages or page.source is not source or page.capture is not capture
                    or page.source_preimage is not source_data or page.capture_preimage is not capture_data
                    or _linear_source_data_v1(source, a.BybitModelSourceRefV1) != source_data
                    or _account_info_capture_data_v1(capture) != capture_data
                    or any(current is not old for current, old in zip(
                        _linear_capture_refs_v1(capture), refs, strict=True))):
                raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        if assembler is not None and (
                    assembler_slot is None or assembler_refs is None or issued.read is None
                    or assembler._current is not assembler_slot
                    or assembler.session is not session or assembler.journal is not journal
                    or assembler.clock is not assembler_refs[0] or assembler.owner_guard is not assembler_refs[1]
                    or assembler.public_marks is not assembler_refs[2]
                    or assembler.catalog_producer is not assembler_refs[3] or assembler._epoch is not assembler_refs[4]
                    or assembler_slot.result is not issued.read
                    or assembler._witness(issued.read, assembler_slot.captures) != assembler_slot.witness
                    or assembler.catalog_producer._current is not assembler_slot.catalog_current
                    or any(session._linear_private_state_v1(kind)[1] is not original
                        for (kind, _sources), original in zip(
                            assembler_slot.sources, assembler_slot.private_slots, strict=True))
                    or any(assembler.public_marks._slots.get(mark.instrument.symbol) is not original
                        for mark, original in zip(issued.read.marks, assembler_slot.mark_slots, strict=True))):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        self._empty_pages(result.positions_sources, result.orders_sources, issued.budget)
        if now >= result.valid_until or any(not timedelta(0) <= now - source.request_started_at <= issued.max_age
                for source in (*result.positions_sources, *result.orders_sources)):
            raise RiskV2Rejected("RISK_V2_EXPIRED")
        data = self._pending(issued.view, issued.scope, command_key=result.command_key,
            identity=result.command_identity_sha256, generation=issued.owner_generation, now=now)
        # No guard/clock/source-owner callback follows the final true-J replay.
        if (data is not issued.data or result.reservation is not data.reservation
                or result.reservations is not data.full or result.evaluation_reservations is not data.evaluation
                or self._current is not issued or self._busy is not token or self._epoch is not epoch
                or self.session is not session or self.journal is not journal
                or self.clock is not clock or self.owner_guard is not guard
                or session._closed is not False or session._binding is not binding
                or session._identity is not identity
                or binding is not issued.binding or session._client is not client
                or session._credential_client is not client or session._credential_pair is not pair
                or client._client is not http_client or http_client._state is not http_state
                or session._linear_positions_current is not issued.positions_slot
                or session._linear_orders_current is not issued.orders_slot
                or issued.budget._terminal_failure is not None
                or journal._own_pending_closed_v2 is not False
                or type(client.environment) is not str or client.environment != "demo"
                or type(client.base_url) is not str or client.base_url != "https://api-demo.bybit.com"
                or type(session._api_key) is not str or type(session._api_secret) is not str
                or type(client._api_key) is not str or type(client._api_secret) is not str
                or (session._api_key, session._api_secret) != pair
                or (client._api_key, client._api_secret) != pair):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")

    def require_current_v1(self, result: BybitLinearPendingCoverageV1) -> BybitLinearPendingCoverageV1:
        with self._operation() as token:
            self._require(result, token)
            return result
