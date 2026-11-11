"""One original read cycle into existing model data, never financial authority.

The caller owns the Session, public mark/catalog producers, journal and worker
lease guard. A retained read must be required again before the existing binder;
day/account risk, costs, minimum assessment and approved PLAN remain separate.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from ..account.bybit_public_mark_source_v1 import BybitPublicMarkSessionV1
from ..account.bybit_readonly import _PrivateAccountEndpointV1
from ..account.risk_limit_envelope import _number_spelling_v1
from ..account.instrument_envelope import CurrentCatalogReceipt
from ..market_data.instrument_catalog import InstrumentCatalog
from ..market_data.instrument_definition import InstrumentObservation, LinearInstrumentSpec
from ..market_data.instrument_producer import BybitInstrumentCatalogProducer
from ..trading_product import InstrumentKey
from . import bybit_demo_uta1_profile_v1 as demo
from . import bybit_linear_model_inputs_v1 as a
from . import risk_v2_contracts as c
from .account_binding import AuthenticatedAccountBinding, BybitDemoAccountSession
from .bybit_position_order_projections_v1 import _page
from .journal import CommandJournal, _OwnPendingViewV2
from .monitoring_budget import MonitoringBudgetError, MonitoringReadBudget
from .risk_v2_math import RiskV2Rejected, decimal_fraction_v2, decimal_text_v2, finite_decimal_v2


@dataclass(frozen=True)
class BybitLinearVenueReadV1:
    """Model data plus an original owner's retained current-read identity."""

    venue: a.BybitVenueStateV1 | demo.BybitDemoUta1VenueStateV1
    marks: tuple[c.LinearMarkQuoteV2, ...]
    selected_mark: a.BybitDecimalFieldV1
    catalog: InstrumentCatalog
    full_reservations: c.LinearReservationSnapshotV2
    evaluation_reservations: c.LinearReservationSnapshotV2


@dataclass
class _Read:
    result: BybitLinearVenueReadV1
    binding: AuthenticatedAccountBinding
    scope: c.RiskScopeV2
    owner_generation: str
    sources: tuple[tuple[_PrivateAccountEndpointV1, tuple[a.BybitModelSourceRefV1, ...]], ...]
    private_slots: tuple[object, ...]
    mark_sources: tuple[a.BybitModelSourceRefV1, ...]
    mark_slots: tuple[object, ...]
    catalog_receipt: CurrentCatalogReceipt
    catalog_current: object
    captures: tuple[a.BybitRiskLimitCaptureV1, ...]
    witness: tuple[str, str, tuple[str, ...]]
    max_age: timedelta
    max_quote_age: timedelta
    own_pending_view: _OwnPendingViewV2 | None
    command_key: str | None
    command_identity: str | None
    profile: Literal["UTA2_V1", "DEMO_UTA1_V1"]
    issued_cost: c.LinearCostAllowanceV2 | None = None
    issued_cost_sha256: str | None = None


def _require_cost_window_open_v2(cost: c.LinearCostAllowanceV2, now: datetime) -> None:
    """Reject an allowance whose bounded time window closed during final checks."""
    now = c._utc_v2(now)
    if now >= cost.valid_until or now >= cost.holding_until or now >= cost.next_funding_at:
        raise RiskV2Rejected("RISK_V2_STALE_EVIDENCE")


class BybitLinearSourceAssemblerV1:
    """Compose original owners; construction and read data grant no order rights."""

    _COST_RULE_V2 = "BYBIT_LINEAR_COST_ALLOWANCE_V1"
    _FEE_RULE_V2 = "BYBIT_ACCOUNT_TAKER_ENTRY_EXIT_V1"
    _FUNDING_RULE_V2 = "BYBIT_NEXT_EVENT_RATE_BAND_V1"
    _STOP_GAP_RULE_V2 = c.STOP_GAP_MODEL_REVISION_V2

    def __init__(self, *, session: BybitDemoAccountSession,
                 public_marks: BybitPublicMarkSessionV1,
                 catalog_producer: BybitInstrumentCatalogProducer, journal: CommandJournal,
                 clock: Callable[[], datetime], owner_guard: Callable[[], None]) -> None:
        if (type(session) is not BybitDemoAccountSession or type(public_marks) is not BybitPublicMarkSessionV1
                or type(catalog_producer) is not BybitInstrumentCatalogProducer
                or type(journal) is not CommandJournal or not callable(clock) or not callable(owner_guard)):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        self.session, self.public_marks, self.catalog_producer, self.journal = session, public_marks, catalog_producer, journal
        self.clock, self.owner_guard = clock, owner_guard
        self._current: _Read | None = None
        self._busy: object | None = None
        self._epoch = object()

    def _guard(self, token: object, binding: AuthenticatedAccountBinding, scope: c.RiskScopeV2) -> None:
        if self._busy is not token:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        self.owner_guard()
        self.session.require_observation_binding(binding, scope=scope.product_scope.account)
        if self._busy is not token:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")

    def _reservations(self, scope: c.RiskScopeV2, owner_generation: str, now: datetime,
                      view: _OwnPendingViewV2 | None, command: str | None,
                      identity: str | None) -> tuple[c.LinearReservationSnapshotV2, c.LinearReservationSnapshotV2]:
        if view is not None:
            if command is None or identity is None:
                raise RiskV2Rejected("RISK_V2_LOCAL_ADMISSION_CONFLICT")
            data = self.journal._require_own_pending_view_v2(view, scope=scope,
                command_key=command, expected_command_identity_sha256=identity, now=now)
            if data.full.owner_generation != owner_generation:
                raise RiskV2Rejected("RISK_V2_LOCAL_ADMISSION_CONFLICT")
            return data.full, data.evaluation
        if command is not None or identity is not None:
            raise RiskV2Rejected("RISK_V2_LOCAL_ADMISSION_CONFLICT")
        full = self.journal.reservation_snapshot_v2(scope, now=now,
            owner_generation=owner_generation, exclude=None)
        return full, full

    def _held_keys(self, sources: tuple[a.BybitModelSourceRefV1, ...], *, orders: bool,
                   binding: AuthenticatedAccountBinding, scope: c.RiskScopeV2) -> set[InstrumentKey]:
        endpoint: _PrivateAccountEndpointV1 = "ORDERS" if orders else "POSITIONS"
        self.session._require_linear_held_v1(endpoint, sources, binding=binding, scope=scope.product_scope.account)
        slot = self.session._linear_private_state_v1(endpoint)[1]
        assert slot is not None
        keys: set[InstrumentKey] = set()
        for page in slot.pages:
            rows, _cursor = _page(page.source, page.capture.raw_body, orders=orders)
            for row in rows:
                symbol = row.get("symbol")
                a._symbol_v1(symbol)
                assert type(symbol) is str
                # LINEAR is the actual fixed endpoint category. Perpetual/USDT
                # eligibility comes only from the original catalog below.
                keys.add(InstrumentKey("BYBIT", "LINEAR", symbol))
        return keys

    async def capture_v1(
        self, binding: AuthenticatedAccountBinding, *, scope: c.RiskScopeV2,
        owner_generation: str, nonvip_fee_basis: a.BybitNonVipFeeBasisV1,
        nonvip_applicability_revision: str, budget: MonitoringReadBudget,
        max_age: timedelta, max_quote_age: timedelta,
        current_wallet_source: a.BybitModelSourceRefV1 | None = None,
        own_pending_view: _OwnPendingViewV2 | None = None,
        own_pending_command_key: str | None = None, own_pending_identity_sha256: str | None = None,
    ) -> BybitLinearVenueReadV1:
        return await self._capture_profile_v1(binding, scope=scope, owner_generation=owner_generation,
            nonvip_fee_basis=nonvip_fee_basis, nonvip_applicability_revision=nonvip_applicability_revision,
            budget=budget, max_age=max_age, max_quote_age=max_quote_age,
            current_wallet_source=current_wallet_source, own_pending_view=own_pending_view,
            own_pending_command_key=own_pending_command_key, own_pending_identity_sha256=own_pending_identity_sha256,
            profile="UTA2_V1")

    async def capture_demo_uta1_v1(
        self, binding: AuthenticatedAccountBinding, *, scope: c.RiskScopeV2,
        owner_generation: str, nonvip_fee_basis: a.BybitNonVipFeeBasisV1,
        nonvip_applicability_revision: str, budget: MonitoringReadBudget,
        max_age: timedelta, max_quote_age: timedelta,
        current_wallet_source: a.BybitModelSourceRefV1 | None = None,
        own_pending_view: _OwnPendingViewV2 | None = None,
        own_pending_command_key: str | None = None, own_pending_identity_sha256: str | None = None,
    ) -> BybitLinearVenueReadV1:
        return await self._capture_profile_v1(binding, scope=scope, owner_generation=owner_generation,
            nonvip_fee_basis=nonvip_fee_basis, nonvip_applicability_revision=nonvip_applicability_revision,
            budget=budget, max_age=max_age, max_quote_age=max_quote_age,
            current_wallet_source=current_wallet_source, own_pending_view=own_pending_view,
            own_pending_command_key=own_pending_command_key, own_pending_identity_sha256=own_pending_identity_sha256,
            profile="DEMO_UTA1_V1")

    async def _capture_profile_v1(
        self, binding: AuthenticatedAccountBinding, *, scope: c.RiskScopeV2,
        owner_generation: str, nonvip_fee_basis: a.BybitNonVipFeeBasisV1,
        nonvip_applicability_revision: str, budget: MonitoringReadBudget,
        max_age: timedelta, max_quote_age: timedelta,
        profile: Literal["UTA2_V1", "DEMO_UTA1_V1"],
        current_wallet_source: a.BybitModelSourceRefV1 | None = None,
        own_pending_view: _OwnPendingViewV2 | None = None,
        own_pending_command_key: str | None = None, own_pending_identity_sha256: str | None = None,
    ) -> BybitLinearVenueReadV1:
        if self._busy is not None:
            self._current, self._busy = None, None
            self._epoch = object()
            raise RiskV2Rejected("RISK_V2_LOCAL_ADMISSION_CONFLICT")
        self._current = None
        self._epoch = object()
        epoch = self._epoch
        token = object()
        self._busy = token
        try:
            c._literal_v2(profile, ("UTA2_V1", "DEMO_UTA1_V1"))
            c._scope_payload_v2(scope)
            a.bybit_nonvip_fee_sha256_v1(nonvip_fee_basis)
            c._identifier_v2(nonvip_applicability_revision)
            if (scope.instrument.venue != "BYBIT" or scope.instrument.product != "LINEAR"
                    or type(budget) is not MonitoringReadBudget
                    or type(max_age) is not timedelta or not timedelta(0) < max_age <= timedelta(minutes=5)
                    or type(max_quote_age) is not timedelta or not timedelta(0) < max_quote_age <= max_age):
                raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
            if (type(nonvip_fee_basis.reference) is a.BybitFeeDocumentRefV1
                    and nonvip_fee_basis.reference.applicability_revision != nonvip_applicability_revision):
                raise RiskV2Rejected("RISK_V2_REVISION_MISMATCH")
            account, symbol = scope.product_scope.account, scope.instrument.symbol

            def guard() -> None:
                active = self._busy
                if active is None or self._epoch is not epoch:
                    raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
                self._guard(active, binding, scope)
                if self._epoch is not epoch:
                    raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")

            guard()
            full, evaluation = self._reservations(scope, owner_generation, self.clock(),
                own_pending_view, own_pending_command_key, own_pending_identity_sha256)
            info = await self.session._capture_linear_account_info_v1(binding, scope=account, budget=budget, owner_guard=guard)
            if current_wallet_source is None:
                wallet = await self.session._capture_linear_wallet_v1(binding, scope=account, budget=budget, owner_guard=guard)
            else:
                self.session._require_linear_wallet_v1(current_wallet_source, binding=binding, scope=account)
                now = c._utc_v2(self.clock())
                for stamp in (current_wallet_source.request_started_at, current_wallet_source.received_at,
                        datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=current_wallet_source.envelope_time_ms)
                            if current_wallet_source.envelope_time_ms is not None else current_wallet_source.received_at):
                    if not timedelta(0) <= now - stamp <= max_age:
                        raise RiskV2Rejected("RISK_V2_EXPIRED")
                guard()
                wallet = current_wallet_source
            fees = await self.session._capture_linear_fee_rate_v1(binding, symbol=symbol, scope=account, budget=budget, owner_guard=guard)
            settings = await self.session._capture_linear_settings_v1(binding, scope=account, budget=budget, owner_guard=guard)
            flat = await self.session._capture_linear_flat_leverage_v1(binding, symbol=symbol, scope=account, budget=budget, owner_guard=guard)
            positions = await self.session._capture_linear_positions_v1(binding, scope=account, budget=budget, owner_guard=guard)
            orders = await self.session._capture_linear_orders_v1(binding, scope=account, budget=budget, owner_guard=guard)
            keys = {scope.instrument, *(p.instrument for p in full.components)}
            keys.update(self._held_keys(positions, orders=False, binding=binding, scope=scope))
            keys.update(self._held_keys(orders, orders=True, binding=binding, scope=scope))
            universe = tuple(sorted(keys, key=c._key_order_v2))
            receipt = await self.catalog_producer.refresh(declared_universe=universe, max_age=max_age, budget=budget)
            # Trusted in-process composition obtains the newly issued revision
            # from its original owner, then uses the genuine production verifier.
            current = self.catalog_producer._current
            assert current is not None
            view = self.catalog_producer.require_current(receipt, expected_revision=current.view.catalog.revision)
            catalog = view.catalog
            for observation in catalog.observations:
                spec = observation.spec
                if (type(spec) is not LinearInstrumentSpec or spec.contract_type != "LinearPerpetual"
                        or spec.quote_coin != "USDT" or spec.settle_coin != "USDT"):
                    raise RiskV2Rejected("RISK_V2_UNSUPPORTED_PRODUCT")
            pins = tuple(c.pin_instrument_v2(o, catalog_revision=catalog.revision) for o in catalog.observations)
            held_positions = self.session._project_linear_positions_v1(positions, instruments=pins, binding=binding, scope=account)
            held_orders = self.session._project_linear_orders_v1(orders, instruments=pins, binding=binding, scope=account)
            captures = tuple([await self.catalog_producer.capture_linear_risk_limit_v1(
                observation, catalog_receipt=receipt, expected_catalog_revision=catalog.revision,
                budget=budget, max_age=max_age, max_pages=64, owner_guard=guard)
                for observation in catalog.observations])
            account_mode: a.BybitUtaModeV1 | demo.BybitDemoUta1IsolatedModeV1
            if profile == "DEMO_UTA1_V1":
                account_mode = self.session._project_linear_demo_uta1_isolated_mode_v1(info, binding=binding, scope=account)
            else:
                account_mode = self.session._project_linear_account_mode_v1(info, binding=binding, scope=account)
            wallet_data = self.session._project_linear_wallet_v1(wallet, binding=binding, scope=account)
            taker, maker = self.session._project_linear_fee_rates_v1(fees, symbol=symbol, binding=binding, scope=account)
            behaviour = self.session._project_linear_trade_behaviour_v1(settings, binding=binding, scope=account)
            flat_data, size, auto = self.session._project_linear_flat_leverage_v1(flat, symbol=symbol, binding=binding, scope=account)
            local = tuple(sorted(p.component_id for p in evaluation.components if p.instrument == scope.instrument))
            if (local or any(p.instrument_pin.key == scope.instrument for p in held_positions)
                    or any(o.instrument_pin.key == scope.instrument for o in held_orders)
                    or auto.wire_spelling != "0" or behaviour.lpa_perp is not True
                    or behaviour.futures_mnt_fee_deduction is not False or behaviour.delta_enable is not False):
                raise RiskV2Rejected("RISK_V2_UNSUPPORTED_MODE")
            profile_sources = tuple(sorted((flat, *positions, *orders), key=lambda s: s.observation_id))
            selected_profile = a.BybitSelectedEntryProfileV1("BYBIT_USDT_LINEAR_ISOLATED_ONE_WAY_NEW_ENTRY_V1",
                symbol, 0, size, (), (), local, auto, Decimal(0), "NEW_SELECTED_FLAT_NO_ADD_V1", profile_sources)
            fee = a.BybitLinearFeeInputsV1(taker, maker, nonvip_fee_basis, "USDT", behaviour, nonvip_applicability_revision)
            # Quotes are deliberately last, after all slower private/catalog/tier work.
            mark_sources = tuple([await self.public_marks.capture_mark_v1(
                key.symbol, budget=budget, owner_guard=guard) for key in universe])
            mark_fields = tuple(self.public_marks.project_mark_v1(s, symbol=key.symbol)
                for key, s in zip(universe, mark_sources, strict=True))
            mark_rows: list[c.LinearMarkQuoteV2] = []
            for key, source, field in zip(universe, mark_sources, mark_fields, strict=True):
                assert source.envelope_time_ms is not None
                mark_rows.append(c.LinearMarkQuoteV2(key, c.RiskObservationRefV2(source.observation_id,
                    source.source_id, source.source_environment, "BYBIT_PUBLIC_MARK_SOURCE_V1",
                    datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=source.envelope_time_ms),
                    source.received_at, a.bybit_decimal_field_sha256_v1(field)), field.value))
            marks = tuple(mark_rows)
            selected_mark = next(field for key, field in zip(universe, mark_fields, strict=True) if key == scope.instrument)
            sources: tuple[tuple[_PrivateAccountEndpointV1, tuple[a.BybitModelSourceRefV1, ...]], ...] = (
                ("INFO", (info,)), ("WALLET", (wallet,)), ("FEE", (fees,)), ("SETTINGS", (settings,)),
                ("FLAT", (flat,)), ("POSITIONS", positions), ("ORDERS", orders))
            extra = ((nonvip_fee_basis.reference.source,)
                if type(nonvip_fee_basis.reference) is a.BybitModelFieldRefV1 else ())
            by_id: dict[str, a.BybitModelSourceRefV1] = {}
            for source in (*[s for _kind, group in sources for s in group],
                    *[s for capture in captures for s in capture.table.pages], *mark_sources, *extra):
                prior = by_id.setdefault(source.observation_id, source)
                if a.bybit_source_ref_sha256_v1(prior) != a.bybit_source_ref_sha256_v1(source):
                    raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
            records = tuple(sorted(by_id.values(), key=lambda s: s.observation_id))
            now = c._utc_v2(self.clock())
            expiry = min(catalog.expires_at, *(s.request_started_at + (max_quote_age if s in mark_sources else max_age) for s in records))
            venue_fields = (flat_data, fee, wallet_data,
                tuple(sorted(held_positions, key=lambda p: (*c._key_order_v2(p.instrument_pin.key), p.position_idx))),
                tuple(sorted(held_orders, key=lambda o: (*c._key_order_v2(o.instrument_pin.key), o.order_id))),
                tuple(capture.table for capture in captures), selected_profile, records, expiry)
            venue: a.BybitVenueStateV1 | demo.BybitDemoUta1VenueStateV1
            if profile == "DEMO_UTA1_V1":
                assert type(account_mode) is demo.BybitDemoUta1IsolatedModeV1
                venue = demo.BybitDemoUta1VenueStateV1(account_mode, *venue_fields)
            else:
                assert type(account_mode) is a.BybitUtaModeV1
                venue = a.BybitVenueStateV1(account_mode, *venue_fields)
            result = BybitLinearVenueReadV1(venue, marks, selected_mark, catalog, full, evaluation)
            guard()
            self._current = _Read(result, binding, scope, owner_generation, sources,
                tuple(self.session._linear_private_state_v1(kind)[1] for kind, _group in sources),
                mark_sources, tuple(self.public_marks._slots[key.symbol] for key in universe), receipt,
                current, captures, self._witness(result, captures), max_age, max_quote_age,
                own_pending_view, own_pending_command_key, own_pending_identity_sha256, profile)
            self._busy = None
            self.require_current_v1(result)
            if now >= expiry:
                raise RiskV2Rejected("RISK_V2_EXPIRED")
            return result
        except MonitoringBudgetError:
            self._current = None
            raise RiskV2Rejected("RISK_V2_SOURCE_READ_BUDGET_EXHAUSTED") from None
        except Exception:
            self._current = None
            if type(budget) is MonitoringReadBudget and budget._terminal_failure is not None:
                raise RiskV2Rejected("RISK_V2_SOURCE_READ_FAILED" if budget._terminal_failure == "MONITORING_READ_FAILED"
                    else "RISK_V2_SOURCE_READ_BUDGET_EXHAUSTED") from None
            raise
        except BaseException:
            self._current = None
            raise
        finally:
            if self._busy is token:
                self._busy = None

    @staticmethod
    def _witness(result: BybitLinearVenueReadV1, captures: tuple[a.BybitRiskLimitCaptureV1, ...]) -> tuple[str, str, tuple[str, ...]]:
        if type(result.venue) is demo.BybitDemoUta1VenueStateV1:
            venue_hash = demo.bybit_demo_uta1_venue_sha256_v1(result.venue)
        elif type(result.venue) is a.BybitVenueStateV1:
            venue_hash = a.bybit_venue_state_sha256_v1(result.venue)
        else:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        return (venue_hash, c.mark_set_sha256_v2(result.marks),
            tuple(a.bybit_risk_limit_capture_sha256_v1(capture) for capture in captures))

    def require_current_v1(self, read: BybitLinearVenueReadV1) -> BybitLinearVenueReadV1:
        slot = self._current
        if self._busy is not None or slot is None or read is not slot.result:
            if self._busy is not None:
                self._busy, self._current = None, None
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        profile = slot.profile
        token = object()
        self._busy = token
        try:
            c._literal_v2(profile, ("UTA2_V1", "DEMO_UTA1_V1"))
            if (profile == "UTA2_V1" and type(read.venue) is not a.BybitVenueStateV1
                    or profile == "DEMO_UTA1_V1" and type(read.venue) is not demo.BybitDemoUta1VenueStateV1):
                raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
            now = c._utc_v2(self.clock())
            if now >= read.venue.valid_until or self._witness(read, slot.captures) != slot.witness:
                raise RiskV2Rejected("RISK_V2_EXPIRED")
            full, evaluation = self._reservations(slot.scope, slot.owner_generation, now,
                slot.own_pending_view, slot.command_key, slot.command_identity)
            if (full.snapshot_id != read.full_reservations.snapshot_id
                    or evaluation.snapshot_id != read.evaluation_reservations.snapshot_id):
                raise RiskV2Rejected("RISK_V2_LOCAL_ADMISSION_CONFLICT")
            self.catalog_producer.require_current(slot.catalog_receipt, expected_revision=read.catalog.revision)
            for key, source in zip((m.instrument for m in read.marks), slot.mark_sources, strict=True):
                self.public_marks.require_mark_v1(source, symbol=key.symbol)
            for kind, sources in slot.sources:
                if kind in ("POSITIONS", "ORDERS"):
                    self.session._require_linear_held_v1(kind, sources, binding=slot.binding, scope=slot.scope.product_scope.account)
                else:
                    self.session._require_linear_private_source_v1(kind, sources[0],
                        symbol=slot.scope.instrument.symbol if kind in ("FLAT", "FEE") else None,
                        binding=slot.binding, scope=slot.scope.product_scope.account)
            now = c._utc_v2(self.clock())
            for source in read.venue.source_records:
                maximum = slot.max_quote_age if any(source is mark for mark in slot.mark_sources) else slot.max_age
                for stamp in (source.request_started_at, source.received_at,
                        datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=source.envelope_time_ms)
                            if source.envelope_time_ms is not None else source.received_at):
                    if not timedelta(0) <= now - stamp <= maximum:
                        raise RiskV2Rejected("RISK_V2_EXPIRED")
            document = read.venue.fee.closing_order_reserve_nonvip_rate.reference
            if type(document) is a.BybitFeeDocumentRefV1 and not timedelta(0) <= now - document.captured_at <= slot.max_age:
                raise RiskV2Rejected("RISK_V2_EXPIRED")
            if now >= read.venue.valid_until:
                raise RiskV2Rejected("RISK_V2_EXPIRED")
            self._guard(token, slot.binding, slot.scope)
            # Finite original-owner links after the last genuine binding callback.
            if (self._current is not slot or slot.profile != profile
                    or self.catalog_producer._current is not slot.catalog_current
                    or any(self.session._linear_private_state_v1(kind)[1] is not original
                        for (kind, _sources), original in zip(slot.sources, slot.private_slots, strict=True))
                    or any(self.public_marks._slots.get(mark.instrument.symbol) is not original
                        for mark, original in zip(read.marks, slot.mark_slots, strict=True))):
                raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
            return read
        except BaseException:
            self._current = None
            raise
        finally:
            if self._busy is token:
                self._busy = None

    def require_current_last_trade_quote_v2(
        self, read: BybitLinearVenueReadV1, quote: e.LinearLastTradeQuoteV2,
    ) -> e.LinearLastTradeQuoteV2:
        """Require a lastTrade quote projected from this read's retained ticker bytes."""
        self.require_current_v1(read)
        slot = self._current
        if slot is None or slot.result is not read:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        if type(quote) is not e.LinearLastTradeQuoteV2 or quote.instrument != slot.scope.instrument:
            raise RiskV2Rejected("RISK_V2_SCOPE_MISMATCH")
        sources = tuple(
            source for source in slot.mark_sources
            if source.observation_id == quote.reference.observation_id
            and source.query == (("category", "linear"), ("symbol", slot.scope.instrument.symbol))
        )
        if len(sources) != 1:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        source = sources[0]
        projected = self.public_marks.project_last_trade_price_v1(
            source, symbol=slot.scope.instrument.symbol
        )
        server_time = (
            datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=source.envelope_time_ms)
            if source.envelope_time_ms is not None else None
        )
        reference = quote.reference
        if (server_time is None
                or quote.last_price != projected.value
                or reference.source_id != source.source_id
                or reference.source_environment != source.source_environment
                or reference.rule_revision != "BYBIT_LINEAR_LAST_TRADE_PRICE_V1"
                or reference.server_observed_at != server_time
                or reference.received_at != source.received_at
                or reference.payload_sha256 != a.bybit_decimal_field_sha256_v1(projected)):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        self.require_current_v1(read)
        current = self._current
        if current is not slot or current.result is not read:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        return quote

    def issue_current_last_trade_quote_v2(
        self, read: BybitLinearVenueReadV1,
    ) -> e.LinearLastTradeQuoteV2:
        """Issue the selected instrument's lastPrice from retained current ticker bytes."""
        self.require_current_v1(read)
        slot = self._current
        if slot is None or slot.result is not read:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        sources = tuple(
            source for source in slot.mark_sources
            if source.query == (("category", "linear"), ("symbol", slot.scope.instrument.symbol))
        )
        if len(sources) != 1:
            raise RiskV2Rejected("RISK_V2_MARK_COVERAGE_REQUIRED")
        source = sources[0]
        projected = self.public_marks.project_last_trade_price_v1(
            source, symbol=slot.scope.instrument.symbol
        )
        if source.envelope_time_ms is None:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        reference = c.RiskObservationRefV2(
            source.observation_id,
            source.source_id,
            source.source_environment,
            "BYBIT_LINEAR_LAST_TRADE_PRICE_V1",
            datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=source.envelope_time_ms),
            source.received_at,
            a.bybit_decimal_field_sha256_v1(projected),
        )
        quote = e.LinearLastTradeQuoteV2(slot.scope.instrument, reference, projected.value)
        self.require_current_last_trade_quote_v2(read, quote)
        return quote

    def issue_cost_allowance_v2(
        self,
        read: BybitLinearVenueReadV1,
        *,
        policy: c.LinearRiskPolicyV2,
        proposal: c.LinearEntryProposalV2,
        holding_until: datetime,
    ) -> c.LinearCostAllowanceV2:
        """Issue a source-bound next-funding estimate using explicit stop-gap risk budget.

        This covers exactly the next funding event; it does not guarantee that a
        position closes or refreshes before that event. Longer horizons need an
        approved strategy horizon plus enforced exit/refresh owner and are rejected.
        The configured stop-gap amount is a user-declared risk budget, not an
        exchange-guaranteed execution-price bound; a missing value fails closed.
        """
        self.require_current_v1(read)
        slot = self._current
        if slot is None or slot.result is not read:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        policy_payload = c._policy_payload_v2(policy)
        proposal_payload = c._proposal_payload_v2(proposal)
        if policy.max_stop_gap_quote_per_base is None:
            raise RiskV2Rejected("RISK_V2_SLIPPAGE_BOUND_UNAVAILABLE")
        if (policy_payload["scope"] != c._scope_payload_v2(slot.scope)
                or proposal_payload["scope"] != policy_payload["scope"]):
            raise RiskV2Rejected("RISK_V2_SCOPE_MISMATCH")
        caps = policy.caps
        if (decimal_fraction_v2(caps.max_order_admission_notional) <= 0
                or decimal_fraction_v2(caps.max_account_admission_notional) <= 0
                or (caps.turnover_requirement == "HARD_EXECUTION_QUOTE_CAP"
                    and (caps.max_execution_quote_amount is None
                         or decimal_fraction_v2(caps.max_execution_quote_amount) <= 0))):
            raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
        if (slot.scope.instrument.venue != "BYBIT" or slot.scope.instrument.product != "LINEAR"
                or proposal.side not in ("LONG", "SHORT")
                or proposal.entry.side != ("BUY" if proposal.side == "LONG" else "SELL")):
            raise RiskV2Rejected("RISK_V2_UNSUPPORTED_PRODUCT")

        now = c._utc_v2(self.clock())
        holding_until = c._utc_v2(holding_until)
        stop_gap = decimal_fraction_v2(policy.max_stop_gap_quote_per_base)
        if stop_gap <= 0 or holding_until <= now:
            raise RiskV2Rejected("RISK_V2_INPUT_INVALID")
        catalog_rows = tuple(
            observation for observation in read.catalog.observations
            if observation.key == slot.scope.instrument
        )
        if len(catalog_rows) != 1 or type(catalog_rows[0]) is not InstrumentObservation:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        instrument = catalog_rows[0]
        pin = c.pin_instrument_v2(instrument, catalog_revision=read.catalog.revision)
        spec = instrument.spec
        if (type(spec) is not LinearInstrumentSpec or spec.contract_type != "LinearPerpetual"
                or spec.status != "Trading" or spec.quote_coin != "USDT" or spec.settle_coin != "USDT"
                or instrument.source.source_environment not in ("PUBLIC_MAINNET", "PUBLIC_TESTNET")):
            raise RiskV2Rejected("RISK_V2_UNSUPPORTED_PRODUCT")
        selected_marks = tuple(mark for mark in read.marks if mark.instrument == slot.scope.instrument)
        if len(selected_marks) != 1:
            raise RiskV2Rejected("RISK_V2_MARK_COVERAGE_REQUIRED")
        mark = selected_marks[0]
        last_trade_quote = self.issue_current_last_trade_quote_v2(read)
        if (proposal.entry.origin_quote_id != last_trade_quote.reference.observation_id
                or read.selected_mark.reference.source.observation_id != mark.reference.observation_id):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        limit_price = decimal_fraction_v2(proposal.entry.limit_price)
        stop_price_value = decimal_fraction_v2(proposal.stop_price)
        price_tick = decimal_fraction_v2(spec.price_tick.value)
        minimum_price = decimal_fraction_v2(spec.min_price.value)
        maximum_price = decimal_fraction_v2(spec.max_price.value)
        if (limit_price / price_tick).denominator != 1 or (stop_price_value / price_tick).denominator != 1:
            raise RiskV2Rejected("RISK_V2_INVALID_PRICE_TICK")
        if not (minimum_price <= limit_price <= maximum_price
                and minimum_price <= stop_price_value <= maximum_price):
            raise RiskV2Rejected("RISK_V2_INVALID_PRICE_TICK")
        if ((proposal.side == "LONG" and stop_price_value >= limit_price)
                or (proposal.side == "SHORT" and stop_price_value <= limit_price)):
            raise RiskV2Rejected("RISK_V2_INVALID_STOP")
        mark_price = decimal_fraction_v2(read.selected_mark.value)
        if ((proposal.side == "LONG" and limit_price > mark_price)
                or (proposal.side == "SHORT" and limit_price < mark_price)):
            raise RiskV2Rejected("RISK_V2_LIMIT_NOT_MARK_FAVORABLE")
        if proposal.side == "SHORT":
            raise RiskV2Rejected("RISK_V2_SELL_EXECUTION_UPPER_UNAVAILABLE")
        funding_sources = tuple(
            source for source in slot.mark_sources
            if source.query == (("category", "linear"), ("symbol", slot.scope.instrument.symbol))
        )
        if (len(funding_sources) != 1 or funding_sources[0].observation_id != mark.reference.observation_id
                or read.selected_mark.reference.source is not funding_sources[0]
                or funding_sources[0].source_environment != instrument.source.source_environment):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        try:
            funding = self.public_marks.project_funding_v1(
                funding_sources[0], symbol=slot.scope.instrument.symbol
            )
        except Exception:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH") from None
        if (funding.source is not funding_sources[0]
                or funding.next_funding_at != holding_until
                or funding.next_funding_at <= now):
            raise RiskV2Rejected("RISK_V2_EXPIRED")

        # The raw catalog row is the original versioned instrument source. Missing
        # funding bounds never become a zero or guessed rate.
        raw_row = instrument.raw_row_canonical
        if (type(raw_row) is not bytes
                or hashlib.sha256(raw_row).hexdigest() != instrument.raw_row_sha256):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        try:
            row = json.loads(raw_row)
            if type(row) is not dict or row.get("symbol") != slot.scope.instrument.symbol:
                raise ValueError
            lower_rate = Decimal(_number_spelling_v1(row.get("lowerFundingRate")))
            upper_rate = Decimal(_number_spelling_v1(row.get("upperFundingRate")))
        except Exception:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH") from None
        observed_rate = funding.funding_rate.value
        lower_fraction, upper_fraction = decimal_fraction_v2(lower_rate), decimal_fraction_v2(upper_rate)
        observed_fraction = decimal_fraction_v2(observed_rate)
        if (lower_fraction >= upper_fraction or lower_fraction > observed_fraction
                or observed_fraction > upper_fraction or max(abs(lower_fraction), abs(upper_fraction)) <= 0):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")

        fee = read.venue.fee
        if type(fee) is not a.BybitLinearFeeInputsV1:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        fee_source = fee.account_taker_rate.reference.source
        fee_sources = tuple(source for kind, sources in slot.sources if kind == "FEE" for source in sources)
        if (len(fee_sources) != 1 or fee_sources[0] is not fee_source
                or fee_source.source_environment != slot.scope.product_scope.account.environment
                or fee_source.account_scope != slot.scope.product_scope.account):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        self.session._require_linear_private_source_v1(
            "FEE", fee_source, symbol=slot.scope.instrument.symbol,
            binding=slot.binding, scope=slot.scope.product_scope.account,
        )
        taker_rate = decimal_fraction_v2(fee.account_taker_rate.value)
        if taker_rate <= 0:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")

        max_evidence = decimal_fraction_v2(policy.max_evidence_age_seconds)
        max_quote = decimal_fraction_v2(policy.max_quote_age_seconds)
        fee_server_time = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            milliseconds=fee_source.envelope_time_ms
        ) if fee_source.envelope_time_ms is not None else None
        funding_server_time = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            milliseconds=funding.source.envelope_time_ms
        ) if funding.source.envelope_time_ms is not None else None
        if fee_server_time is None or funding_server_time is None:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        evidence_stamps = (
            instrument.response_time,
            instrument.source.received_at,
            fee_source.request_started_at,
            fee_source.received_at,
            fee_server_time,
        )
        quote_stamps = (
            funding.source.request_started_at,
            funding.source.received_at,
            funding_server_time,
        )
        evidence_expiries: list[datetime] = []
        for stamp in evidence_stamps:
            c._fresh_v2(stamp, now=now, maximum=max_evidence)
            try:
                evidence_expiries.append(stamp + timedelta(microseconds=int(max_evidence * 1_000_000)))
            except OverflowError:
                raise RiskV2Rejected("RISK_V2_INPUT_INVALID") from None
        quote_expiries: list[datetime] = []
        for stamp in quote_stamps:
            c._fresh_v2(stamp, now=now, maximum=max_quote)
            try:
                quote_expiries.append(stamp + timedelta(microseconds=int(max_quote * 1_000_000)))
            except OverflowError:
                raise RiskV2Rejected("RISK_V2_INPUT_INVALID") from None

        fee_digest = a.bybit_decimal_field_sha256_v1(fee.account_taker_rate)
        fee_payload_sha = c._canonical_sha256_v2("BybitAccountTakerFeeObservationV1", {
            "source": a.bybit_source_ref_sha256_v1(fee_source),
            "symbol": slot.scope.instrument.symbol,
            "taker_rate": fee_digest,
            "fee_currency": fee.fee_currency,
            "fee_rule_revision": self._FEE_RULE_V2,
        })
        fee_reference = c.RiskObservationRefV2(
            fee_source.observation_id, fee_source.source_id, fee_source.source_environment,
            self._FEE_RULE_V2, fee_server_time, fee_source.received_at, fee_payload_sha,
        )
        funding_payload_sha = c._canonical_sha256_v2("BybitLinearFundingObservationV1", {
            "source": a.bybit_source_ref_sha256_v1(funding.source),
            "catalog_revision": read.catalog.revision,
            "instrument_pin": c._pin_payload_v2(pin),
            "funding_rate": a.bybit_decimal_field_sha256_v1(funding.funding_rate),
            "next_funding_time": a.bybit_field_ref_sha256_v1(funding.next_funding_time),
            "lower_funding_rate": decimal_text_v2(lower_rate),
            "upper_funding_rate": decimal_text_v2(upper_rate),
            "funding_rule_revision": self._FUNDING_RULE_V2,
        })
        funding_reference = c.RiskObservationRefV2(
            funding.source.observation_id, funding.source.source_id, funding.source.source_environment,
            self._FUNDING_RULE_V2, funding_server_time, funding.source.received_at, funding_payload_sha,
        )
        entry_price = decimal_fraction_v2(proposal.entry.limit_price)
        stop_price = decimal_fraction_v2(proposal.stop_price)
        # This owner currently issues fee allowances for LONG only. SHORT fills
        # may execute above their limit floor, with no enforceable fee ceiling.
        fee_per_base = finite_decimal_v2(taker_rate * (entry_price + stop_price))
        funding_per_base = finite_decimal_v2(
            max(abs(lower_fraction), abs(upper_fraction)) * decimal_fraction_v2(spec.max_price.value)
        )
        stop_gap_decimal = finite_decimal_v2(stop_gap)
        if fee_per_base <= 0 or funding_per_base <= 0:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        valid_until = min(
            read.venue.valid_until,
            proposal.entry.valid_until,
            holding_until,
            *evidence_expiries,
            *quote_expiries,
        )
        if valid_until <= now:
            raise RiskV2Rejected("RISK_V2_EXPIRED")
        witness = self._witness(read, slot.captures)
        input_sha = c._canonical_sha256_v2("BybitLinearCostIssuerInputV1", {
            "scope": c._scope_payload_v2(slot.scope),
            "policy_sha256": c.risk_policy_sha256_v2(policy),
            "proposal": proposal_payload,
            "catalog_revision": read.catalog.revision,
            "instrument_pin": c._pin_payload_v2(pin),
            "venue_witness": (witness[0], witness[1], witness[2]),
            "fee_reference": fee_payload_sha,
            "funding_reference": funding_payload_sha,
            "holding_until": c._time_text_v2(holding_until),
            "stop_gap_quote_per_base": decimal_text_v2(stop_gap_decimal),
            "stop_gap_model_revision": self._STOP_GAP_RULE_V2,
            "cost_rule_revision": self._COST_RULE_V2,
        })
        computation = c.RiskComputationRefV2(
            "BYBIT_COST_" + input_sha[:32], self._COST_RULE_V2,
            slot.scope.product_scope.account.environment, now, input_sha,
        )
        cost = c.LinearCostAllowanceV2(
            slot.scope, computation, c.risk_policy_sha256_v2(policy), instrument.specification_revision,
            proposal.entry.origin_quote_id, proposal.side, proposal.entry.limit_price, proposal.stop_price,
            fee_reference, "USDT", self._FEE_RULE_V2, funding_reference, observed_rate,
            funding.next_funding_at, holding_until, self._FUNDING_RULE_V2, self._STOP_GAP_RULE_V2,
            fee_per_base, funding_per_base, stop_gap_decimal, valid_until, self._COST_RULE_V2,
        )
        self.public_marks.project_funding_v1(funding.source, symbol=slot.scope.instrument.symbol)
        self.session._require_linear_private_source_v1(
            "FEE", fee_source, symbol=slot.scope.instrument.symbol,
            binding=slot.binding, scope=slot.scope.product_scope.account,
        )
        self.require_current_v1(read)
        _require_cost_window_open_v2(cost, self.clock())
        if self._current is not slot:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        slot.issued_cost = cost
        slot.issued_cost_sha256 = c.cost_allowance_sha256_v2(cost)
        return cost

    def require_cost_allowance_v2(
        self,
        read: BybitLinearVenueReadV1,
        cost: c.LinearCostAllowanceV2,
        *,
        policy: c.LinearRiskPolicyV2,
    ) -> c.LinearCostAllowanceV2:
        """Require the exact issued cost object while every original owner is current."""
        slot = self._current
        if slot is None or slot.result is not read or slot.issued_cost is not cost:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        policy_hash = c.risk_policy_sha256_v2(policy)
        if cost.policy_sha256 != policy_hash or c._scope_payload_v2(policy.scope) != c._scope_payload_v2(slot.scope):
            raise RiskV2Rejected("RISK_V2_REVISION_MISMATCH")
        self.require_current_v1(read)
        slot = self._current
        if slot is None or slot.result is not read or slot.issued_cost is not cost:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        cost_hash = c.cost_allowance_sha256_v2(cost)
        if slot.issued_cost_sha256 is None or cost_hash != slot.issued_cost_sha256:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        sources = tuple(
            source for source in slot.mark_sources
            if source.observation_id == cost.funding_reference.observation_id
        )
        if len(sources) != 1:
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        funding = self.public_marks.project_funding_v1(
            sources[0], symbol=slot.scope.instrument.symbol
        )
        if (funding.funding_rate.value != cost.funding_rate
                or funding.next_funding_at != cost.next_funding_at
                or funding.source.received_at != cost.funding_reference.received_at):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        now = c._utc_v2(self.clock())
        if now >= cost.valid_until or now >= cost.holding_until or now >= cost.next_funding_at:
            raise RiskV2Rejected("RISK_V2_EXPIRED")
        self.require_current_v1(read)
        _require_cost_window_open_v2(cost, self.clock())
        current = self._current
        if (current is None or current is not slot or current.result is not read or current.issued_cost is not cost
                or current.issued_cost_sha256 != cost_hash):
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        return cost
