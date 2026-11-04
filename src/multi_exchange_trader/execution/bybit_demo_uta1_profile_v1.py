"""Versioned DEMO UTA1 isolated data; no account change or trading authority.

The actual status stays 3. Original Session consumers own source authorization;
the retained UTA2 V1 contract does not accept these separate values or hashes.
Economic point/search and true-journal subtraction kernels are shared unchanged.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from ..account.risk_limit_envelope import _count_v1
from ..market_data.instrument_catalog import InstrumentCatalog
from . import bybit_linear_model_inputs_v1 as a
from . import bybit_linear_model_v1 as m
from . import bybit_linear_own_pending_v1 as o
from . import linear_risk_evidence_v2 as e
from . import reservations_v2 as v
from . import risk_domain_v2 as d
from . import risk_v2_contracts as c
from .journal import CommandJournal, _OwnPendingViewV2

MODEL_REVISION = "BYBIT_DEMO_UTA1_LINEAR_ISOLATED_POINT_MODEL_V1"
OWN_MODEL_REVISION = "BYBIT_DEMO_UTA1_LINEAR_OWN_PENDING_MODEL_V1"
OWN_PROFILE_REVISION = "BYBIT_DEMO_UTA1_USDT_ISOLATED_SELECTED_FLAT_OWN_PENDING_V1"


@dataclass(frozen=True)
class BybitDemoUta1IsolatedModeV1:
    source: a.BybitModelSourceRefV1
    unified_margin_status: Literal[3]
    margin_mode: Literal["ISOLATED_MARGIN"]
    updated_time_ms: int

    def __post_init__(self) -> None:
        _mode_payload(self)


def _mode_payload(value: BybitDemoUta1IsolatedModeV1) -> dict[str, object]:
    c._fields_v2(value, BybitDemoUta1IsolatedModeV1)
    a._source_v1(value.source)
    c._literal_v2(value.margin_mode, ("ISOLATED_MARGIN",))
    if (
        type(value.unified_margin_status) is not int
        or value.unified_margin_status != 3
        or value.margin_mode != "ISOLATED_MARGIN"
        or value.source.account_scope is None
        or value.source.account_scope.environment != "DEMO"
        or value.source.source_environment not in ("DEMO", "FIXTURE")
        or value.source.path != "/v5/account/info"
        or value.source.query != ()
    ):
        c._reject_v2("RISK_V2_UNSUPPORTED_MODE")
    _count_v1(value.updated_time_ms, 1, 253402300799999)
    return {
        "source": a.bybit_source_ref_sha256_v1(value.source),
        "unified_margin_status": 3,
        "margin_mode": "ISOLATED_MARGIN",
        "updated_time_ms": value.updated_time_ms,
    }


def bybit_demo_uta1_mode_sha256_v1(value: BybitDemoUta1IsolatedModeV1) -> str:
    return c._canonical_sha256_v2("BybitDemoUta1IsolatedModeV1", _mode_payload(value))


@dataclass(frozen=True)
class BybitDemoUta1VenueStateV1:
    account_mode: BybitDemoUta1IsolatedModeV1
    flat_leverage: a.BybitFlatLeverageV1
    fee: a.BybitLinearFeeInputsV1
    wallet: a.BybitUsdtWalletV1
    held_positions: tuple[a.BybitHeldPositionV1, ...]
    held_orders: tuple[a.BybitHeldOrderV1, ...]
    tier_tables: tuple[a.BybitLinearTierTableV1, ...]
    selected_profile: a.BybitSelectedEntryProfileV1
    source_records: tuple[a.BybitModelSourceRefV1, ...]
    valid_until: datetime

    def __post_init__(self) -> None:
        _venue_payload(self)


def _venue_payload(value: BybitDemoUta1VenueStateV1) -> dict[str, object]:
    c._fields_v2(value, BybitDemoUta1VenueStateV1)
    return a._venue_economic_payload_v1(
        value, mode_hash=bybit_demo_uta1_mode_sha256_v1(value.account_mode)
    )


def bybit_demo_uta1_venue_sha256_v1(value: BybitDemoUta1VenueStateV1) -> str:
    return c._canonical_sha256_v2("BybitDemoUta1VenueStateV1", _venue_payload(value))


@dataclass(frozen=True)
class BybitDemoUta1ModelInputsV1:
    risk_inputs: e.LinearRiskInputsV2
    preparation: e.LinearEntryPreparationV2
    domain: d.LinearRiskDomainCoverageV2
    venue: BybitDemoUta1VenueStateV1
    point: a.BybitLinearPointInputV1
    catalog: InstrumentCatalog
    valid_until: datetime
    model_rule_revision: Literal["BYBIT_DEMO_UTA1_LINEAR_ISOLATED_POINT_MODEL_V1"]

    def __post_init__(self) -> None:
        _model_payload(self)


def _model_payload(value: BybitDemoUta1ModelInputsV1) -> dict[str, object]:
    c._fields_v2(value, BybitDemoUta1ModelInputsV1)
    if value.risk_inputs.policy.scope.product_scope.account.environment != "DEMO":
        c._reject_v2("RISK_V2_UNSUPPORTED_MODE")
    return a._model_economic_payload_v1(
        value,
        venue_hash=bybit_demo_uta1_venue_sha256_v1(value.venue),
        model_rule_revision=MODEL_REVISION,
    )


def bybit_demo_uta1_model_inputs_sha256_v1(value: BybitDemoUta1ModelInputsV1) -> str:
    return c._canonical_sha256_v2("BybitDemoUta1ModelInputsV1", _model_payload(value))


def bind_bybit_demo_uta1_model_inputs_v1(
    inputs: e.LinearRiskInputsV2,
    *,
    preparation: e.LinearEntryPreparationV2,
    domain: d.LinearRiskDomainCoverageV2,
    venue: BybitDemoUta1VenueStateV1,
    catalog: InstrumentCatalog,
    selected_mark: a.BybitDecimalFieldV1,
    now: datetime,
) -> BybitDemoUta1ModelInputsV1:
    e._inputs_payload_v2(inputs)
    e._preparation_payload_v2(preparation)
    d.risk_domain_sha256_v2(domain)
    _venue_payload(venue)
    a._catalog_input_sha256_v1(catalog)
    a._decimal_field_v1(selected_mark)
    now = c._utc_v2(now)
    if inputs.policy.scope.product_scope.account.environment != "DEMO":
        c._reject_v2("RISK_V2_UNSUPPORTED_MODE")
    actual, expiry, point = a._bind_bybit_model_economics_v1(
        inputs,
        preparation=preparation,
        domain=domain,
        venue=venue,
        catalog=catalog,
        selected_mark=selected_mark,
        now=now,
    )
    return BybitDemoUta1ModelInputsV1(
        inputs,
        actual,
        domain,
        venue,
        point,
        catalog,
        expiry,
        "BYBIT_DEMO_UTA1_LINEAR_ISOLATED_POINT_MODEL_V1",
    )


def calculate_bound_bybit_demo_uta1_model_v1(
    inputs: BybitDemoUta1ModelInputsV1,
    *,
    now: datetime,
) -> m.BybitBoundModelCalculationV1 | m.BybitModelRejectedV1:
    original = bybit_demo_uta1_model_inputs_sha256_v1(inputs)
    now = c._utc_v2(now)
    rebound = bind_bybit_demo_uta1_model_inputs_v1(
        inputs.risk_inputs,
        preparation=inputs.preparation,
        domain=inputs.domain,
        venue=inputs.venue,
        catalog=inputs.catalog,
        selected_mark=inputs.point.selected_mark,
        now=now,
    )
    model_hash = bybit_demo_uta1_model_inputs_sha256_v1(rebound)
    if model_hash != original:
        c._reject_v2("RISK_V2_SOURCE_MISMATCH")
    return m._calculate_bound_point_v1(rebound, model_hash=model_hash, now=now)


@dataclass(frozen=True)
class BybitDemoUta1OwnPendingModelInputsV1:
    profile_revision: Literal["BYBIT_DEMO_UTA1_USDT_ISOLATED_SELECTED_FLAT_OWN_PENDING_V1"]
    projection_revision: Literal["EXACT_JOURNAL_OWN_PENDING_SUBTRACTION_V1"]
    command_key: str
    client_order_id: str
    position_id: str
    command_identity_sha256: str
    archived_intent_sha256: str
    original_reservation: v.ReservationV2
    full_risk_inputs: e.LinearRiskInputsV2
    full_domain: d.LinearRiskDomainCoverageV2
    full_venue: BybitDemoUta1VenueStateV1
    evaluation: BybitDemoUta1ModelInputsV1
    point: a.BybitLinearPointInputV1
    valid_until: datetime
    model_rule_revision: Literal["BYBIT_DEMO_UTA1_LINEAR_OWN_PENDING_MODEL_V1"]

    def __post_init__(self) -> None:
        _own_payload(self)


def _own_payload(value: BybitDemoUta1OwnPendingModelInputsV1) -> dict[str, object]:
    c._fields_v2(value, BybitDemoUta1OwnPendingModelInputsV1)
    return o._own_pending_payload_kernel_v1(
        value,
        profile_revision=OWN_PROFILE_REVISION,
        model_rule_revision=OWN_MODEL_REVISION,
        venue_hash=bybit_demo_uta1_venue_sha256_v1,
        model_hash=bybit_demo_uta1_model_inputs_sha256_v1,
    )


def bybit_demo_uta1_own_pending_inputs_sha256_v1(
    value: BybitDemoUta1OwnPendingModelInputsV1,
) -> str:
    return c._canonical_sha256_v2("BybitDemoUta1OwnPendingModelInputsV1", _own_payload(value))


def bind_bybit_demo_uta1_own_pending_inputs_v1(
    full_inputs: e.LinearRiskInputsV2,
    *,
    full_domain: d.LinearRiskDomainCoverageV2,
    full_venue: BybitDemoUta1VenueStateV1,
    catalog: InstrumentCatalog,
    selected_mark: a.BybitDecimalFieldV1,
    journal: CommandJournal,
    view: _OwnPendingViewV2,
    command_key: str,
    expected_command_identity_sha256: str,
    now: datetime,
) -> BybitDemoUta1OwnPendingModelInputsV1:
    _venue_payload(full_venue)
    data, evaluation, point = o._bind_bybit_own_pending_kernel_v1(
        full_inputs,
        full_domain=full_domain,
        full_venue=full_venue,
        catalog=catalog,
        selected_mark=selected_mark,
        journal=journal,
        view=view,
        command_key=command_key,
        expected_command_identity_sha256=expected_command_identity_sha256,
        now=now,
        bind_model=bind_bybit_demo_uta1_model_inputs_v1,
    )
    return BybitDemoUta1OwnPendingModelInputsV1(
        "BYBIT_DEMO_UTA1_USDT_ISOLATED_SELECTED_FLAT_OWN_PENDING_V1",
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
        cast(BybitDemoUta1ModelInputsV1, evaluation),
        point,
        min(evaluation.valid_until, data.intent.envelope.valid_until),
        "BYBIT_DEMO_UTA1_LINEAR_OWN_PENDING_MODEL_V1",
    )


def calculate_bybit_demo_uta1_own_pending_v1(
    inputs: BybitDemoUta1OwnPendingModelInputsV1,
    *,
    journal: CommandJournal,
    view: _OwnPendingViewV2,
    now: datetime,
) -> o.BybitOwnPendingCalculationV1 | m.BybitModelRejectedV1:
    original = _own_payload(inputs)
    now = c._utc_v2(now)
    rebound = bind_bybit_demo_uta1_own_pending_inputs_v1(
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
    if _own_payload(rebound) != original or a._point_payload_v1(
        rebound.point
    ) != a._point_payload_v1(inputs.point):
        c._reject_v2("RISK_V2_SOURCE_MISMATCH")
    return o._finish_own_pending_calculation_v1(
        rebound,
        journal=journal,
        view=view,
        now=now,
        model_hash=bybit_demo_uta1_own_pending_inputs_sha256_v1(rebound),
    )


def replay_bybit_demo_uta1_own_pending_v1(
    calculation: o.BybitOwnPendingCalculationV1 | m.BybitModelRejectedV1,
    *,
    inputs: BybitDemoUta1OwnPendingModelInputsV1,
    journal: CommandJournal,
    view: _OwnPendingViewV2,
    now: datetime,
) -> o.BybitOwnPendingCalculationV1:
    if type(calculation) is m.BybitModelRejectedV1:
        m._rejected(calculation)
        c._reject_v2(calculation.public_code)
    calculation = cast(o.BybitOwnPendingCalculationV1, calculation)
    supplied = o._own_pending_calculation_payload_v1(calculation)
    if (
        o.bybit_own_pending_calculation_sha256_v1(calculation)
        != calculation.exact_calculation_sha256
    ):
        c._reject_v2("RISK_V2_SOURCE_MISMATCH")
    actual = calculate_bybit_demo_uta1_own_pending_v1(inputs, journal=journal, view=view, now=now)
    if type(actual) is m.BybitModelRejectedV1:
        c._reject_v2(actual.public_code)
    actual = cast(o.BybitOwnPendingCalculationV1, actual)
    if (
        o._own_pending_calculation_payload_v1(actual) != supplied
        or actual.exact_calculation_sha256 != calculation.exact_calculation_sha256
    ):
        c._reject_v2("RISK_V2_SOURCE_MISMATCH")
    return actual
