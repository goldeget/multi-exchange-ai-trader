"""Pure true-journal PENDING projections; no execution or source authority."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction
from typing import Any, ClassVar, Literal, Protocol, cast

from multi_exchange_trader.execution import bybit_linear_model_inputs_v1 as a
from multi_exchange_trader.execution import bybit_linear_model_v1 as m
from multi_exchange_trader.execution import linear_risk_evidence_v2 as e
from multi_exchange_trader.execution import linear_risk_v2 as r
from multi_exchange_trader.execution import reservations_v2 as v
from multi_exchange_trader.execution import risk_domain_v2 as d
from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.journal import (
    CommandJournal,
    _OwnPendingDataV2,
    _OwnPendingViewV2,
)
from multi_exchange_trader.execution.risk_v2_math import (
    _compare,
    _fraction,
    decimal_fraction_v2,
    decimal_text_v2,
    finite_decimal_v2,
)
from multi_exchange_trader.market_data.instrument_catalog import InstrumentCatalog

_CONFLICT = "RISK_V2_LOCAL_ADMISSION_CONFLICT"


class _OwnPendingModelDataV1(Protocol):
    __dataclass_fields__: ClassVar[dict[str, Any]]

    @property
    def profile_revision(self) -> str: ...

    @property
    def projection_revision(self) -> str: ...

    @property
    def command_key(self) -> str: ...

    @property
    def client_order_id(self) -> str: ...

    @property
    def position_id(self) -> str: ...

    @property
    def command_identity_sha256(self) -> str: ...

    @property
    def archived_intent_sha256(self) -> str: ...

    @property
    def original_reservation(self) -> v.ReservationV2: ...

    @property
    def full_risk_inputs(self) -> e.LinearRiskInputsV2: ...

    @property
    def full_domain(self) -> d.LinearRiskDomainCoverageV2: ...

    @property
    def full_venue(self) -> a._BybitVenueDataV1: ...

    @property
    def evaluation(self) -> a._BybitModelDataV1: ...

    @property
    def point(self) -> a.BybitLinearPointInputV1: ...

    @property
    def valid_until(self) -> datetime: ...

    @property
    def model_rule_revision(self) -> str: ...


@dataclass(frozen=True)
class BybitOwnPendingModelInputsV1:
    profile_revision: Literal["BYBIT_USDT_LINEAR_ISOLATED_ONE_WAY_SELECTED_FLAT_OWN_PENDING_V1"]
    projection_revision: Literal["EXACT_JOURNAL_OWN_PENDING_SUBTRACTION_V1"]
    command_key: str
    client_order_id: str
    position_id: str
    command_identity_sha256: str
    archived_intent_sha256: str
    original_reservation: v.ReservationV2
    full_risk_inputs: e.LinearRiskInputsV2
    full_domain: d.LinearRiskDomainCoverageV2
    full_venue: a.BybitVenueStateV1
    evaluation: a.BybitLinearModelInputsV1
    point: a.BybitLinearPointInputV1
    valid_until: datetime
    model_rule_revision: Literal["BYBIT_LINEAR_OWN_PENDING_MODEL_V1"]

    def __post_init__(self) -> None:
        _own_pending_payload_v1(self)


def _capped_point_v1(
    evaluation: a._BybitModelDataV1, reservation: v.ReservationV2
) -> a.BybitLinearPointInputV1:
    available = decimal_fraction_v2(evaluation.preparation.capacity_quote)
    held = decimal_fraction_v2(reservation.margin_amount)
    capacity = available if _compare(available, held) <= 0 else held
    return replace(evaluation.point, capacity_quote=finite_decimal_v2(capacity))


def _evaluation_members_v1(
    full: d.LinearRiskDomainCoverageV2, reservation: v.ReservationV2, client: str
) -> tuple[d.LinearRiskDomainMemberV2, ...]:
    result: list[d.LinearRiskDomainMemberV2] = []
    found = 0
    for member in full.members:
        if member.instrument_pin.key != reservation.instrument:
            result.append(member)
            continue
        found += 1
        if (
            member.account_component_ids
            or member.reservation_component_ids != (client,)
            or member.reserved_position_count != 1
            or _compare(
                decimal_fraction_v2(member.reserved_risk_quote),
                decimal_fraction_v2(reservation.risk_amount),
            )
            != 0
            or _compare(
                decimal_fraction_v2(member.reserved_margin_quote),
                decimal_fraction_v2(reservation.margin_amount),
            )
            != 0
        ):
            c._reject_v2(_CONFLICT)
        result.append(
            replace(
                member,
                reservation_component_ids=(),
                reserved_risk_quote=finite_decimal_v2(
                    _fraction(
                        decimal_fraction_v2(member.reserved_risk_quote)
                        - decimal_fraction_v2(reservation.risk_amount)
                    )
                ),
                reserved_margin_quote=finite_decimal_v2(
                    _fraction(
                        decimal_fraction_v2(member.reserved_margin_quote)
                        - decimal_fraction_v2(reservation.margin_amount)
                    )
                ),
                reserved_position_count=c._count_v2(member.reserved_position_count - 1),
            )
        )
    if found != 1:
        c._reject_v2("RISK_V2_HELD_COVERAGE_REQUIRED")
    return tuple(result)


def _build_domain_v1(
    inputs: e.LinearRiskInputsV2,
    full: d.LinearRiskDomainCoverageV2,
    catalog: InstrumentCatalog,
    members: tuple[d.LinearRiskDomainMemberV2, ...],
    *,
    now: datetime,
) -> d.LinearRiskDomainCoverageV2:
    return d.build_linear_risk_domain_coverage_v2(
        policy=inputs.policy,
        catalog=catalog,
        catalog_revision=inputs.catalog_revision,
        coverage_revision=full.coverage_revision,
        source_generation=full.source_generation,
        account=inputs.account,
        reservations=inputs.reservations,
        marks=inputs.marks,
        members=members,
        source_record_sha256s=full.source_record_sha256s,
        now=now,
    )


def _own_pending_payload_v1(value: BybitOwnPendingModelInputsV1) -> dict[str, object]:
    c._fields_v2(value, BybitOwnPendingModelInputsV1)
    return _own_pending_payload_kernel_v1(value,
        profile_revision="BYBIT_USDT_LINEAR_ISOLATED_ONE_WAY_SELECTED_FLAT_OWN_PENDING_V1",
        model_rule_revision="BYBIT_LINEAR_OWN_PENDING_MODEL_V1",
        venue_hash=a.bybit_venue_state_sha256_v1, model_hash=a.bybit_model_inputs_sha256_v1)


def _own_pending_payload_kernel_v1(value: _OwnPendingModelDataV1, *, profile_revision: str, model_rule_revision: str, venue_hash: Callable[..., str], model_hash: Callable[..., str]) -> dict[str, object]:
    profile = c._literal_v2(
        value.profile_revision, (profile_revision,)
    )
    projection = c._literal_v2(
        value.projection_revision, ("EXACT_JOURNAL_OWN_PENDING_SUBTRACTION_V1",)
    )
    identifiers = {
        name: c._identifier_v2(getattr(value, name))
        for name in ("command_key", "client_order_id", "position_id")
    }
    identity = c._digest_v2(value.command_identity_sha256)
    archive = c._digest_v2(value.archived_intent_sha256)
    reservation = v._reservation_payload(value.original_reservation)
    full_inputs = e._inputs_payload_v2(value.full_risk_inputs)
    full_domain = d.risk_domain_sha256_v2(value.full_domain)
    full_venue = venue_hash(value.full_venue)
    evaluation = model_hash(value.evaluation)
    point = a.bybit_point_input_sha256_v1(value.point)
    expiry = c._utc_v2(value.valid_until)
    rule = c._literal_v2(value.model_rule_revision, (model_rule_revision,))
    if (
        value.full_risk_inputs.reservations.excluded_command_key is not None
        or value.evaluation.risk_inputs.reservations.excluded_command_key != value.command_key
        or full_domain != value.full_risk_inputs.risk_domain_sha256
        or value.full_venue.selected_profile.local_unresolved_entry_ids != (value.client_order_id,)
        or value.evaluation.venue.selected_profile.local_unresolved_entry_ids
        or value.original_reservation.envelope_sha256 != c.envelope_sha256_v2(value.point.envelope)
        or expiry != min(value.evaluation.valid_until, value.point.envelope.valid_until)
    ):
        c._reject_v2(_CONFLICT)
    projected = replace(
        value.full_risk_inputs,
        reservations=value.evaluation.risk_inputs.reservations,
        risk_domain_sha256=value.evaluation.risk_inputs.risk_domain_sha256,
    )
    venue = replace(
        value.full_venue,
        selected_profile=replace(value.full_venue.selected_profile, local_unresolved_entry_ids=()),
    )
    if (
        e._inputs_payload_v2(projected) != e._inputs_payload_v2(value.evaluation.risk_inputs)
        or venue_hash(venue)
        != venue_hash(value.evaluation.venue)
        or a._point_payload_v1(_capped_point_v1(value.evaluation, value.original_reservation))
        != a._point_payload_v1(value.point)
    ):
        c._reject_v2(_CONFLICT)
    return {
        "profile_revision": profile,
        "projection_revision": projection,
        **identifiers,
        "command_identity_sha256": identity,
        "archived_intent_sha256": archive,
        "original_reservation": reservation,
        "full_risk_inputs": full_inputs,
        "full_domain_sha256": full_domain,
        "full_venue_sha256": full_venue,
        "evaluation_model_sha256": evaluation,
        "point_input_sha256": point,
        "valid_until": c._time_text_v2(expiry),
        "model_rule_revision": rule,
    }


def bybit_own_pending_model_inputs_sha256_v1(value: BybitOwnPendingModelInputsV1) -> str:
    return c._canonical_sha256_v2("BybitOwnPendingModelInputsV1", _own_pending_payload_v1(value))


def bind_bybit_own_pending_inputs_v1(
    full_inputs: e.LinearRiskInputsV2,
    *,
    full_domain: d.LinearRiskDomainCoverageV2,
    full_venue: a.BybitVenueStateV1,
    catalog: InstrumentCatalog,
    selected_mark: a.BybitDecimalFieldV1,
    journal: CommandJournal,
    view: _OwnPendingViewV2,
    command_key: str,
    expected_command_identity_sha256: str,
    now: datetime,
) -> BybitOwnPendingModelInputsV1:
    a._venue_payload_v1(full_venue)
    data, evaluation, point = _bind_bybit_own_pending_kernel_v1(full_inputs, full_domain=full_domain,
        full_venue=full_venue, catalog=catalog, selected_mark=selected_mark, journal=journal, view=view,
        command_key=command_key, expected_command_identity_sha256=expected_command_identity_sha256,
        now=now, bind_model=a.bind_bybit_model_inputs_v1)
    return BybitOwnPendingModelInputsV1(
        "BYBIT_USDT_LINEAR_ISOLATED_ONE_WAY_SELECTED_FLAT_OWN_PENDING_V1",
        "EXACT_JOURNAL_OWN_PENDING_SUBTRACTION_V1",
        command_key,
        data.record.client_order_id,
        data.intent.position_id,
        data.command_identity_sha256,
        data.record.payload_hash,
        data.reservation,
        full_inputs,
        full_domain,
        full_venue,
        cast(a.BybitLinearModelInputsV1, evaluation),
        point,
        min(evaluation.valid_until, data.intent.envelope.valid_until),
        "BYBIT_LINEAR_OWN_PENDING_MODEL_V1",
    )


def _bind_bybit_own_pending_kernel_v1(
    full_inputs: e.LinearRiskInputsV2,
    *,
    full_domain: d.LinearRiskDomainCoverageV2,
    full_venue: a._BybitVenueDataV1,
    catalog: InstrumentCatalog,
    selected_mark: a.BybitDecimalFieldV1,
    journal: CommandJournal,
    view: _OwnPendingViewV2,
    command_key: str,
    expected_command_identity_sha256: str,
    now: datetime,
    bind_model: Callable[..., a._BybitModelDataV1],
) -> tuple[_OwnPendingDataV2, a._BybitModelDataV1, a.BybitLinearPointInputV1]:
    e._inputs_payload_v2(full_inputs)
    full_domain_hash = d.risk_domain_sha256_v2(full_domain)
    a._catalog_input_sha256_v1(catalog)
    a._decimal_field_v1(selected_mark)
    c._identifier_v2(command_key)
    c._digest_v2(expected_command_identity_sha256)
    now = c._utc_v2(now)
    if type(journal) is not CommandJournal:
        c._reject_v2(_CONFLICT)
    scope = full_inputs.policy.scope
    data = journal._require_own_pending_view_v2(
        view,
        scope=scope,
        command_key=command_key,
        expected_command_identity_sha256=expected_command_identity_sha256,
        now=now,
    )
    if (
        c._reservation_snapshot_payload_v2(full_inputs.reservations)
        != c._reservation_snapshot_payload_v2(data.full)
        or full_inputs.reservations.excluded_command_key is not None
        or full_inputs.policy.revision != data.intent.envelope.policy_revision
        or c.risk_policy_sha256_v2(full_inputs.policy) != data.intent.envelope.policy_sha256
        or full_venue.selected_profile.local_unresolved_entry_ids != (data.record.client_order_id,)
    ):
        c._reject_v2(_CONFLICT)
    selected = tuple(
        component for component in data.full.components if component.instrument == scope.instrument
    )
    if len(selected) != 1 or c._exposure_payload_v2(selected[0]) != c._exposure_payload_v2(
        c.ExposureQuantityV2(
            data.record.client_order_id,
            scope.instrument,
            "PENDING_ENTRY",
            data.intent.envelope.side,
            data.reservation.base_quantity,
        )
    ):
        c._reject_v2(_CONFLICT)
    rebuilt = _build_domain_v1(full_inputs, full_domain, catalog, full_domain.members, now=now)
    if (
        d.risk_domain_sha256_v2(rebuilt) != full_inputs.risk_domain_sha256
        or d.risk_domain_sha256_v2(rebuilt) != full_domain_hash
    ):
        c._reject_v2("RISK_V2_SOURCE_MISMATCH")
    members = _evaluation_members_v1(full_domain, data.reservation, data.record.client_order_id)
    evaluation_inputs = replace(full_inputs, reservations=data.evaluation)
    evaluation_domain = _build_domain_v1(evaluation_inputs, full_domain, catalog, members, now=now)
    evaluation_inputs = replace(
        evaluation_inputs, risk_domain_sha256=d.risk_domain_sha256_v2(evaluation_domain)
    )
    venue = replace(
        full_venue,
        selected_profile=replace(full_venue.selected_profile, local_unresolved_entry_ids=()),
    )
    preparation = r.prepare_linear_entry_v2(evaluation_inputs, now=now)
    evaluation = bind_model(
        evaluation_inputs,
        preparation=preparation,
        domain=evaluation_domain,
        venue=venue,
        catalog=catalog,
        selected_mark=selected_mark,
        now=now,
    )
    if (
        c._envelope_payload_v2(preparation.sizing.envelope)
        != c._envelope_payload_v2(data.intent.envelope)
        or decimal_text_v2(preparation.sizing.stop_price)
        != decimal_text_v2(data.intent.protection_policy.stop_price)
        or _compare(
            decimal_fraction_v2(preparation.sizing.estimated_loss_quote),
            decimal_fraction_v2(data.reservation.risk_amount),
        )
        > 0
    ):
        c._reject_v2(_CONFLICT)
    admission = v.ReservationAdmissionV2(
        data.full.account,
        data.full.owner_generation,
        data.full.journal_revision,
        data.full.held_quantity_sha256,
        data.command_identity_sha256,
        full_inputs.policy,
        full_inputs.account,
        full_inputs.marks,
        now,
    )
    v._check_capacity(data.intent, data.reservation, admission, data.evaluation)
    point = _capped_point_v1(evaluation, data.reservation)
    journal._require_own_pending_view_v2(
        view,
        scope=scope,
        command_key=command_key,
        expected_command_identity_sha256=expected_command_identity_sha256,
        now=now,
    )
    return data, evaluation, point


@dataclass(frozen=True)
class BybitOwnPendingCalculationV1:
    model_input_sha256: str
    point_input_sha256: str
    search: m.BybitLeverageSearchV1
    valid_until: datetime
    exact_calculation_sha256: str

    def __post_init__(self) -> None:
        if self.exact_calculation_sha256 != bybit_own_pending_calculation_sha256_v1(self):
            c._reject_v2("RISK_V2_SOURCE_MISMATCH")


def _own_pending_calculation_payload_v1(value: BybitOwnPendingCalculationV1) -> dict[str, object]:
    c._fields_v2(value, BybitOwnPendingCalculationV1)
    model = c._digest_v2(value.model_input_sha256)
    point = c._digest_v2(value.point_input_sha256)
    search = m.bybit_leverage_search_sha256_v1(value.search)
    expiry = c._time_text_v2(value.valid_until)
    c._digest_v2(value.exact_calculation_sha256)
    if value.search.point_input_sha256 != point:
        c._reject_v2("RISK_V2_SOURCE_MISMATCH")
    return {
        "model_input_sha256": model,
        "point_input_sha256": point,
        "search": search,
        "valid_until": expiry,
    }


def bybit_own_pending_calculation_sha256_v1(value: BybitOwnPendingCalculationV1) -> str:
    return c._canonical_sha256_v2(
        "BybitOwnPendingCalculationV1", _own_pending_calculation_payload_v1(value)
    )


def calculate_bybit_own_pending_v1(
    inputs: BybitOwnPendingModelInputsV1,
    *,
    journal: CommandJournal,
    view: _OwnPendingViewV2,
    now: datetime,
) -> BybitOwnPendingCalculationV1 | m.BybitModelRejectedV1:
    original = _own_pending_payload_v1(inputs)
    now = c._utc_v2(now)
    rebound = bind_bybit_own_pending_inputs_v1(
        inputs.full_risk_inputs,
        full_domain=inputs.full_domain,
        full_venue=inputs.full_venue,
        catalog=inputs.evaluation.catalog,
        selected_mark=inputs.evaluation.point.selected_mark,
        journal=journal,
        view=view,
        command_key=inputs.command_key,
        expected_command_identity_sha256=inputs.command_identity_sha256,
        now=now,
    )
    if _own_pending_payload_v1(rebound) != original or a._point_payload_v1(
        rebound.point
    ) != a._point_payload_v1(inputs.point):
        c._reject_v2("RISK_V2_SOURCE_MISMATCH")
    return _finish_own_pending_calculation_v1(rebound, journal=journal, view=view, now=now,
        model_hash=bybit_own_pending_model_inputs_sha256_v1(rebound))


def _finish_own_pending_calculation_v1(
    rebound: _OwnPendingModelDataV1, *, journal: CommandJournal, view: _OwnPendingViewV2,
    now: datetime, model_hash: str,
) -> BybitOwnPendingCalculationV1 | m.BybitModelRejectedV1:
    search = m.select_smallest_bybit_leverage_v1(rebound.point, now=now)
    journal._require_own_pending_view_v2(
        view,
        scope=rebound.full_risk_inputs.policy.scope,
        command_key=rebound.command_key,
        expected_command_identity_sha256=rebound.command_identity_sha256,
        now=now,
    )
    if type(search) is m.BybitModelRejectedV1:
        return search
    search = cast(m.BybitLeverageSearchV1, search)
    point_hash = a.bybit_point_input_sha256_v1(rebound.point)
    exact = c._canonical_sha256_v2(
        "BybitOwnPendingCalculationV1",
        {
            "model_input_sha256": model_hash,
            "point_input_sha256": point_hash,
            "search": m.bybit_leverage_search_sha256_v1(search),
            "valid_until": c._time_text_v2(rebound.valid_until),
        },
    )
    return BybitOwnPendingCalculationV1(model_hash, point_hash, search, rebound.valid_until, exact)


def _replay_own_pending_v1(
    calculation: BybitOwnPendingCalculationV1 | m.BybitModelRejectedV1,
    inputs: BybitOwnPendingModelInputsV1,
    *,
    journal: CommandJournal,
    view: _OwnPendingViewV2,
    now: datetime,
) -> BybitOwnPendingCalculationV1:
    if type(calculation) is m.BybitModelRejectedV1:
        rejected = calculation
        m._rejected(rejected)
        c._reject_v2(rejected.public_code)
    calculation = cast(BybitOwnPendingCalculationV1, calculation)
    supplied = _own_pending_calculation_payload_v1(calculation)
    if bybit_own_pending_calculation_sha256_v1(calculation) != calculation.exact_calculation_sha256:
        c._reject_v2("RISK_V2_SOURCE_MISMATCH")
    replayed = calculate_bybit_own_pending_v1(inputs, journal=journal, view=view, now=now)
    if type(replayed) is m.BybitModelRejectedV1:
        c._reject_v2(replayed.public_code)
    actual = cast(BybitOwnPendingCalculationV1, replayed)
    if (
        _own_pending_calculation_payload_v1(actual) != supplied
        or m._search_payload(actual.search) != m._search_payload(calculation.search)
        or actual.exact_calculation_sha256 != calculation.exact_calculation_sha256
    ):
        c._reject_v2("RISK_V2_SOURCE_MISMATCH")
    return actual


def _current_own_pending_v1(
    inputs: BybitOwnPendingModelInputsV1,
    journal: CommandJournal,
    view: _OwnPendingViewV2,
    *,
    now: datetime,
) -> _OwnPendingDataV2:
    return journal._require_own_pending_view_v2(
        view,
        scope=inputs.full_risk_inputs.policy.scope,
        command_key=inputs.command_key,
        expected_command_identity_sha256=inputs.command_identity_sha256,
        now=now,
    )


def build_bybit_own_pending_margin_v1(
    calculation: BybitOwnPendingCalculationV1,
    *,
    inputs: BybitOwnPendingModelInputsV1,
    journal: CommandJournal,
    view: _OwnPendingViewV2,
    reference: c.RiskComputationRefV2,
    now: datetime,
) -> e.LinearMarginAssessmentV2:
    c._computation_payload_v2(reference)
    actual = _replay_own_pending_v1(calculation, inputs, journal=journal, view=view, now=now)
    requirements = inputs.evaluation.risk_inputs.requirements
    revision = "BYBIT_LINEAR_OWN_PENDING_MARGIN_V1"
    if requirements.margin_model_revision != revision:
        c._reject_v2("RISK_V2_MARGIN_UNVERIFIED")
    data = _current_own_pending_v1(inputs, journal, view, now=now)
    if not data.intent.assessment.assessed_at <= data.full.observed_at <= reference.computed_at:
        c._reject_v2("RISK_V2_CAUSAL_ORDER_INVALID")
    binding = e.binding_sha256_v2(inputs.evaluation.preparation.binding)
    m._completion(
        reference,
        inputs=inputs.evaluation,
        expected_input=binding,
        model_revision=revision,
        public_code="RISK_V2_MARGIN_UNVERIFIED",
        now=now,
    )
    selected = actual.search.selected
    result = e.LinearMarginAssessmentV2(
        binding,
        reference,
        revision,
        inputs.point.envelope.base_quantity,
        selected.leverage,
        requirements.leverage_grid_rule,
        actual.search.selection_rule,
        selected.reserve.initial_margin_quote,
        selected.reserve.venue_required_total_reserve_quote,
        inputs.point.admission_covered_tier_ids,
        actual.valid_until,
        requirements.margin_reserve_basis,
        requirements.reserve_rounding_revision,
        requirements.reserve_quantum_usdt,
        actual.model_input_sha256,
        actual.exact_calculation_sha256,
    )
    e._margin_payload_v2(result)
    _current_own_pending_v1(inputs, journal, view, now=now)
    return result


def build_bybit_own_pending_liquidation_v1(
    calculation: BybitOwnPendingCalculationV1,
    *,
    inputs: BybitOwnPendingModelInputsV1,
    journal: CommandJournal,
    view: _OwnPendingViewV2,
    margin: e.LinearMarginAssessmentV2,
    reference: c.RiskComputationRefV2,
    now: datetime,
) -> e.LinearLiquidationAssessmentV2:
    e._margin_payload_v2(margin)
    c._computation_payload_v2(reference)
    actual = _replay_own_pending_v1(calculation, inputs, journal=journal, view=view, now=now)
    expected_margin = build_bybit_own_pending_margin_v1(
        calculation, inputs=inputs, journal=journal, view=view, reference=margin.reference, now=now
    )
    if e._margin_payload_v2(margin) != e._margin_payload_v2(expected_margin):
        c._reject_v2("RISK_V2_MARGIN_UNVERIFIED")
    requirements = inputs.evaluation.risk_inputs.requirements
    revision = "BYBIT_LINEAR_OWN_PENDING_LIQUIDATION_V1"
    if requirements.liquidation_model_revision != revision:
        c._reject_v2("RISK_V2_LIQUIDATION_UNVERIFIED")
    data = _current_own_pending_v1(inputs, journal, view, now=now)
    if not data.intent.assessment.assessed_at <= data.full.observed_at <= reference.computed_at:
        c._reject_v2("RISK_V2_CAUSAL_ORDER_INVALID")
    margin_hash = e.margin_assessment_sha256_v2(margin)
    m._completion(
        reference,
        inputs=inputs.evaluation,
        expected_input=margin_hash,
        model_revision=revision,
        public_code="RISK_V2_LIQUIDATION_UNVERIFIED",
        now=now,
        after=margin.reference.computed_at,
    )
    selected = actual.search.selected
    lp = selected.liquidation
    outcome: e.LiquidationPositiveRootV2 | e.LiquidationNoPositiveRootV2
    if type(lp.outcome) is m.BybitPositiveRootPointV1:
        price = lp.outcome.exact_price
        outcome = e.LiquidationPositiveRootV2(
            "POSITIVE_ROOT",
            e.ExactRiskRatioV2(
                finite_decimal_v2(Fraction(price.numerator)),
                finite_decimal_v2(Fraction(price.denominator)),
            ),
            lp.outcome.candidate_tier_id,
            lp.domain_upper_quote,
            lp.full_table_sha256,
            lp.raw_manifest_sha256,
            lp.consumed_tier_ids,
            lp.rule_revision,
        )
    else:
        outcome = e.LiquidationNoPositiveRootV2(
            "NO_POSITIVE_ROOT",
            "NON_POSITIVE_LONG_INTERCEPT",
            lp.domain_upper_quote,
            lp.full_table_sha256,
            lp.raw_manifest_sha256,
            lp.consumed_tier_ids,
            lp.rule_revision,
            actual.model_input_sha256,
            lp.proof_sha256,
        )
    result = e.LinearLiquidationAssessmentV2(
        e.binding_sha256_v2(inputs.evaluation.preparation.binding),
        margin_hash,
        reference,
        revision,
        inputs.point.envelope.base_quantity,
        selected.leverage,
        inputs.evaluation.risk_inputs.proposal.side,
        inputs.point.envelope.limit_price,
        inputs.evaluation.risk_inputs.policy.margin_mode,
        inputs.evaluation.risk_inputs.policy.position_mode,
        inputs.point.admission_covered_tier_ids,
        outcome,
        actual.valid_until,
        actual.model_input_sha256,
        actual.exact_calculation_sha256,
    )
    e._liquidation_payload_v2(result)
    _current_own_pending_v1(inputs, journal, view, now=now)
    return result


def assess_bybit_own_pending_submit_v1(
    inputs: BybitOwnPendingModelInputsV1,
    *,
    calculation: BybitOwnPendingCalculationV1,
    journal: CommandJournal,
    view: _OwnPendingViewV2,
    margin: e.LinearMarginAssessmentV2,
    liquidation: e.LinearLiquidationAssessmentV2,
    leverage_readback: e.LinearLeverageReadbackV2,
    now: datetime,
) -> e.LinearEntryAssessmentV2:
    _own_pending_payload_v1(inputs)
    e._margin_payload_v2(margin)
    e._liquidation_payload_v2(liquidation)
    e._readback_payload_v2(leverage_readback)
    _replay_own_pending_v1(calculation, inputs, journal=journal, view=view, now=now)
    expected_margin = build_bybit_own_pending_margin_v1(
        calculation, inputs=inputs, journal=journal, view=view, reference=margin.reference, now=now
    )
    if e._margin_payload_v2(margin) != e._margin_payload_v2(expected_margin):
        c._reject_v2("RISK_V2_MARGIN_UNVERIFIED")
    expected_liquidation = build_bybit_own_pending_liquidation_v1(
        calculation,
        inputs=inputs,
        journal=journal,
        view=view,
        margin=margin,
        reference=liquidation.reference,
        now=now,
    )
    if e._liquidation_payload_v2(liquidation) != e._liquidation_payload_v2(expected_liquidation):
        c._reject_v2("RISK_V2_LIQUIDATION_UNVERIFIED")
    assessment = r.assess_entry_v2(
        inputs.evaluation.risk_inputs,
        now=now,
        stage="SUBMIT",
        margin=margin,
        liquidation=liquidation,
        leverage_readback=leverage_readback,
    )
    data = _current_own_pending_v1(inputs, journal, view, now=now)
    sizing = assessment.preparation.sizing
    required = assessment.required_reservation_quote
    if (
        c._envelope_payload_v2(sizing.envelope) != c._envelope_payload_v2(data.intent.envelope)
        or decimal_text_v2(sizing.stop_price)
        != decimal_text_v2(data.intent.protection_policy.stop_price)
        or _compare(
            decimal_fraction_v2(sizing.estimated_loss_quote),
            decimal_fraction_v2(data.reservation.risk_amount),
        )
        > 0
        or required is None
        or _compare(
            decimal_fraction_v2(required), decimal_fraction_v2(data.reservation.margin_amount)
        )
        > 0
    ):
        c._reject_v2(_CONFLICT)
    admission = v.ReservationAdmissionV2(
        data.full.account,
        data.full.owner_generation,
        data.full.journal_revision,
        data.full.held_quantity_sha256,
        data.command_identity_sha256,
        inputs.full_risk_inputs.policy,
        inputs.full_risk_inputs.account,
        inputs.full_risk_inputs.marks,
        now,
    )
    v._check_capacity(data.intent, data.reservation, admission, data.evaluation)
    _current_own_pending_v1(inputs, journal, view, now=now)
    return assessment
