"""One original read cycle into existing model data, never financial authority.

The caller owns the Session, public mark/catalog producers, journal and worker
lease guard. A retained read must be required again before the existing binder;
day/account risk, costs, minimum assessment and approved PLAN remain separate.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ..account.bybit_public_mark_source_v1 import BybitPublicMarkSessionV1
from ..account.bybit_readonly import _PrivateAccountEndpointV1
from ..account.instrument_envelope import CurrentCatalogReceipt
from ..market_data.instrument_catalog import InstrumentCatalog
from ..market_data.instrument_definition import LinearInstrumentSpec
from ..market_data.instrument_producer import BybitInstrumentCatalogProducer
from ..trading_product import InstrumentKey
from . import bybit_linear_model_inputs_v1 as a
from . import risk_v2_contracts as c
from .account_binding import AuthenticatedAccountBinding, BybitDemoAccountSession
from .bybit_position_order_projections_v1 import _page
from .journal import CommandJournal, _OwnPendingViewV2
from .monitoring_budget import MonitoringBudgetError, MonitoringReadBudget
from .risk_v2_math import RiskV2Rejected


@dataclass(frozen=True)
class BybitLinearVenueReadV1:
    """Model data plus an original owner's retained current-read identity."""

    venue: a.BybitVenueStateV1
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


class BybitLinearSourceAssemblerV1:
    """Compose original owners; construction and read data grant no order rights."""

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
            wallet = await self.session._capture_linear_wallet_v1(binding, scope=account, budget=budget, owner_guard=guard)
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
            profile = a.BybitSelectedEntryProfileV1("BYBIT_USDT_LINEAR_ISOLATED_ONE_WAY_NEW_ENTRY_V1",
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
            venue = a.BybitVenueStateV1(account_mode, flat_data, fee, wallet_data,
                tuple(sorted(held_positions, key=lambda p: (*c._key_order_v2(p.instrument_pin.key), p.position_idx))),
                tuple(sorted(held_orders, key=lambda o: (*c._key_order_v2(o.instrument_pin.key), o.order_id))),
                tuple(capture.table for capture in captures), profile, records, expiry)
            result = BybitLinearVenueReadV1(venue, marks, selected_mark, catalog, full, evaluation)
            guard()
            self._current = _Read(result, binding, scope, owner_generation, sources,
                tuple(self.session._linear_private_state_v1(kind)[1] for kind, _group in sources),
                mark_sources, tuple(self.public_marks._slots[key.symbol] for key in universe), receipt,
                current, captures, self._witness(result, captures), max_age, max_quote_age,
                own_pending_view, own_pending_command_key, own_pending_identity_sha256)
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
        return (a.bybit_venue_state_sha256_v1(result.venue), c.mark_set_sha256_v2(result.marks),
            tuple(a.bybit_risk_limit_capture_sha256_v1(capture) for capture in captures))

    def require_current_v1(self, read: BybitLinearVenueReadV1) -> BybitLinearVenueReadV1:
        slot = self._current
        if self._busy is not None or slot is None or read is not slot.result:
            if self._busy is not None:
                self._busy, self._current = None, None
            raise RiskV2Rejected("RISK_V2_SOURCE_MISMATCH")
        token = object()
        self._busy = token
        try:
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
            if (self._current is not slot or self.catalog_producer._current is not slot.catalog_current
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
