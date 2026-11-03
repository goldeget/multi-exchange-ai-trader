"""Pure true-journal PENDING projections; no execution or source authority."""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from multi_exchange_trader.execution import bybit_linear_model_inputs_v1 as a
from multi_exchange_trader.execution import linear_risk_evidence_v2 as e
from multi_exchange_trader.execution import linear_risk_v2 as r
from multi_exchange_trader.execution import reservations_v2 as v
from multi_exchange_trader.execution import risk_domain_v2 as d
from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.journal import CommandJournal, _OwnPendingViewV2
from multi_exchange_trader.execution.risk_v2_math import (
    _compare,
    _fraction,
    decimal_fraction_v2,
    decimal_text_v2,
    finite_decimal_v2,
)
from multi_exchange_trader.market_data.instrument_catalog import InstrumentCatalog

_CONFLICT = "RISK_V2_LOCAL_ADMISSION_CONFLICT"


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
    evaluation: a.BybitLinearModelInputsV1, reservation: v.ReservationV2
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
    profile = c._literal_v2(
        value.profile_revision, ("BYBIT_USDT_LINEAR_ISOLATED_ONE_WAY_SELECTED_FLAT_OWN_PENDING_V1",)
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
    full_venue = a.bybit_venue_state_sha256_v1(value.full_venue)
    evaluation = a.bybit_model_inputs_sha256_v1(value.evaluation)
    point = a.bybit_point_input_sha256_v1(value.point)
    expiry = c._utc_v2(value.valid_until)
    rule = c._literal_v2(value.model_rule_revision, ("BYBIT_LINEAR_OWN_PENDING_MODEL_V1",))
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
        or a.bybit_venue_state_sha256_v1(venue)
        != a.bybit_venue_state_sha256_v1(value.evaluation.venue)
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
    e._inputs_payload_v2(full_inputs)
    full_domain_hash = d.risk_domain_sha256_v2(full_domain)
    a._venue_payload_v1(full_venue)
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
    evaluation = a.bind_bybit_model_inputs_v1(
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
        evaluation,
        point,
        min(evaluation.valid_until, data.intent.envelope.valid_until),
        "BYBIT_LINEAR_OWN_PENDING_MODEL_V1",
    )
