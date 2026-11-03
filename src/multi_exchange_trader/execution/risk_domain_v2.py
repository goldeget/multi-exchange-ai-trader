"""Pure supplied held-domain values; no selection or issuer authority."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import cast

from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.linear_risk_evidence_v2 import (
    LinearRiskTierV2,
    LinearTierSnapshotV2,
    _tier_snapshot_payload_v2,
)
from multi_exchange_trader.execution.modes import StrategyReference
from multi_exchange_trader.execution.product_scope import ProductScope
from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.execution.risk_v2_math import (
    _compare,
    _fraction,
    decimal_fraction_v2,
    decimal_text_v2,
)
from multi_exchange_trader.market_data.instrument_catalog import (
    InstrumentCatalog,
    build_instrument_catalog,
)
from multi_exchange_trader.market_data.instrument_definition import (
    CatalogRejected,
    CatalogSource,
    DecimalField,
    InstrumentObservation,
    KnownNotPublished,
    LinearInstrumentSpec,
    SpotInstrumentSpec,
    validate_instrument_observation,
)
from multi_exchange_trader.trading_product import InstrumentKey


@dataclass(frozen=True)
class LinearRiskDomainMemberV2:
    instrument_pin: c.InstrumentPinV2
    mark: c.LinearMarkQuoteV2
    tiers: LinearTierSnapshotV2
    account_component_ids: tuple[str, ...]
    reservation_component_ids: tuple[str, ...]
    account_risk_quote: Decimal
    account_margin_quote: Decimal
    account_position_count: int
    reserved_risk_quote: Decimal
    reserved_margin_quote: Decimal
    reserved_position_count: int
    account_margin_reference: c.RiskObservationRefV2
    account_risk_reference: c.RiskComputationRefV2


@dataclass(frozen=True)
class LinearRiskDomainCoverageV2:
    account_scope: ProtectionScope
    catalog_revision: str
    selected_entry_key: InstrumentKey
    coverage_revision: str
    source_generation: str
    members: tuple[LinearRiskDomainMemberV2, ...]
    account_snapshot_sha256: str
    reservation_snapshot_sha256: str
    mark_set_sha256: str
    source_record_sha256s: tuple[str, ...]


def risk_domain_sha256_v2(value: LinearRiskDomainCoverageV2) -> str:
    return c._canonical_sha256_v2("LinearRiskDomainCoverageV2", _coverage_payload_v2(value))


def _structural_domain_inputs_v2(
    *,
    coverage: object | None = None,
    member: object | None = None,
    inputs: tuple[object, ...] | None = None,
) -> None:
    """Scalar-only first pass over the fixed domain graph, before any links.

    This deliberately does not use equality, ordering, hashes or source parsers.
    The expected classes below are local trusted constants, never input metadata.
    Existing payload validators own the subsequent intrinsic semantic checks.
    """

    def record(value: object, expected: type[object]) -> None:
        v = c._fields_v2(value, expected)
        nested: tuple[tuple[str, type[object]], ...] = ()
        strings: tuple[str, ...] = ()
        identifiers: tuple[str, ...] = ()
        digests: tuple[str, ...] = ()
        times: tuple[str, ...] = ()
        counts: tuple[str, ...] = ()
        amounts: tuple[str, ...] = ()
        source = False
        if expected is LinearRiskDomainCoverageV2:
            nested = (("account_scope", ProtectionScope), ("selected_entry_key", InstrumentKey))
            identifiers = ("coverage_revision", "source_generation")
            digests = (
                "catalog_revision",
                "account_snapshot_sha256",
                "reservation_snapshot_sha256",
                "mark_set_sha256",
            )
            for item in c._tuple_v2(v["members"], minimum=1, maximum=100):
                record(item, LinearRiskDomainMemberV2)
            for digest in c._tuple_v2(v["source_record_sha256s"], minimum=1, maximum=1024):
                c._digest_v2(digest)
        elif expected is LinearRiskDomainMemberV2:
            nested = (
                ("instrument_pin", c.InstrumentPinV2),
                ("mark", c.LinearMarkQuoteV2),
                ("tiers", LinearTierSnapshotV2),
                ("account_margin_reference", c.RiskObservationRefV2),
                ("account_risk_reference", c.RiskComputationRefV2),
            )
            amounts = (
                "account_risk_quote",
                "account_margin_quote",
                "reserved_risk_quote",
                "reserved_margin_quote",
            )
            counts = ("account_position_count", "reserved_position_count")
            for name in ("account_component_ids", "reservation_component_ids"):
                for identifier in c._tuple_v2(v[name], minimum=0, maximum=1024):
                    c._identifier_v2(identifier)
        elif expected is c.LinearRiskPolicyV2:
            nested = (("scope", c.RiskScopeV2), ("caps", c.LinearCapitalCapsV2))
            identifiers = ("revision",)
            strings = ("sizing_rule", "margin_mode", "position_mode")
            counts = ("max_consecutive_losses", "max_positions")
            amounts = (
                "leverage_min",
                "leverage_max",
                "per_trade_loss",
                "max_daily_loss",
                "max_drawdown",
                "max_total_risk",
                "max_total_margin",
                "margin_allocation",
                "liquidation_buffer_fraction",
                "max_quote_age_seconds",
                "max_evidence_age_seconds",
            )
            source = True
        elif expected is c.LinearCapitalCapsV2:
            strings = ("valuation_rule", "turnover_requirement")
            amounts = ("max_order_admission_notional", "max_account_admission_notional")
            source = True
            if v["max_execution_quote_amount"] is not None:
                c._decimal_v2(v["max_execution_quote_amount"], source=True, sign="positive")
        elif expected is c.RiskScopeV2:
            nested = (
                ("product_scope", ProductScope),
                ("instrument", InstrumentKey),
                ("strategy", StrategyReference),
            )
        elif expected is ProductScope:
            nested = (("account", ProtectionScope),)
            strings = ("product",)
        elif expected is StrategyReference:
            identifiers = ("strategy_id", "revision")
        elif expected is ProtectionScope:
            # This helper has no cross-record operation.
            c._account_payload_v2(value)
        elif expected is InstrumentKey:
            strings = ("venue", "product", "symbol")
        elif expected is c.InstrumentPinV2:
            nested = (("key", InstrumentKey),)
            identifiers = (
                "catalog_revision",
                "parser_revision",
                "source_rule_revision",
                "source_id",
            )
            strings = ("native_instrument_id", "source_environment")
            digests = ("specification_revision", "raw_row_sha256")
            times = ("response_time", "received_at")
        elif expected is c.RiskObservationRefV2:
            identifiers = ("observation_id", "source_id", "source_rule_revision")
            strings = ("source_environment",)
            digests = ("payload_sha256",)
            times = ("server_observed_at", "received_at")
        elif expected is c.RiskComputationRefV2:
            identifiers = ("calculation_id", "model_revision")
            strings = ("target_environment",)
            times = ("computed_at",)
            digests = ("input_sha256",)
        elif expected is c.LinearMarkQuoteV2:
            nested = (("instrument", InstrumentKey), ("reference", c.RiskObservationRefV2))
            c._decimal_v2(v["mark_price"], source=True, sign="positive")
        elif expected is LinearTierSnapshotV2:
            nested = (("instrument", InstrumentKey), ("reference", c.RiskObservationRefV2))
            identifiers = ("instrument_revision",)
            for row in c._tuple_v2(v["rows"], minimum=1, maximum=1024):
                record(row, LinearRiskTierV2)
        elif expected is LinearRiskTierV2:
            identifiers = ("tier_id",)
            for name in (
                "lower_exclusive_quote",
                "upper_inclusive_quote",
                "min_leverage",
                "max_leverage",
                "initial_margin_rate",
                "maintenance_margin_rate",
                "maintenance_margin_deduction",
            ):
                c._decimal_v2(
                    v[name],
                    source=True,
                    sign="nonnegative"
                    if name in ("lower_exclusive_quote", "maintenance_margin_deduction")
                    else "positive",
                )
        elif expected in (c.LinearAccountRiskSnapshotV2, c.LinearReservationSnapshotV2):
            nested = (("account", ProtectionScope),)
            counts = ("position_count",)
            for item in c._tuple_v2(v["components"], minimum=0, maximum=1024):
                record(item, c.ExposureQuantityV2)
            if expected is c.LinearAccountRiskSnapshotV2:
                nested += (("reference", c.RiskObservationRefV2),)
                identifiers = ("coverage_revision",)
                strings = ("loss_accounting", "margin_mode", "position_mode")
                counts += ("consecutive_losses",)
                amounts = (
                    "equity",
                    "available_margin",
                    "total_risk",
                    "total_margin",
                    "daily_loss",
                    "drawdown",
                )
                if type(v["loss_day"]) is not date:
                    c._reject_v2()
                for identifier in c._tuple_v2(
                    v["external_same_symbol_activity_ids"], minimum=0, maximum=1024
                ):
                    c._identifier_v2(identifier)
            else:
                identifiers = ("snapshot_id", "owner_generation")
                digests = ("held_quantity_sha256",)
                counts += ("journal_revision",)
                amounts = ("risk_amount", "margin_amount")
                times = ("observed_at",)
                if v["excluded_command_key"] is not None:
                    c._identifier_v2(v["excluded_command_key"])
        elif expected is c.ExposureQuantityV2:
            nested = (("instrument", InstrumentKey),)
            identifiers = ("component_id",)
            origin = c._literal_v2(
                v["origin"], ("ACCOUNT_POSITION", "ACCOUNT_ENTRY_ORDER", "PENDING_ENTRY")
            )
            c._literal_v2(v["side"], ("BUY", "SELL"))
            c._decimal_v2(v["base_quantity"], source=origin != "PENDING_ENTRY", sign="positive")
        elif expected is InstrumentCatalog:
            if type(v["schema_version"]) is not int or v["schema_version"] != 1:
                c._reject_v2()
            digests = ("revision",)
            times = ("expires_at",)
            if type(v["max_age"]) is not timedelta or v["max_age"] <= timedelta(0):
                c._reject_v2()
            for key in c._tuple_v2(v["declared_universe"], minimum=1, maximum=100):
                record(key, InstrumentKey)
            for observation in c._tuple_v2(v["observations"], minimum=1, maximum=100):
                record(observation, InstrumentObservation)
        elif expected is InstrumentObservation:
            nested = (("key", InstrumentKey), ("source", CatalogSource))
            strings = ("native_instrument_id", "parser_revision", "source_rule_revision")
            digests = ("specification_revision", "raw_row_sha256")
            times = ("response_time",)
            if type(v["raw_row_canonical"]) is not bytes:
                c._reject_v2()
            if len(v["raw_row_canonical"]) > 65536:
                c._reject_v2("RISK_V2_RESOURCE_LIMIT")
            if type(v["spec"]) is LinearInstrumentSpec:
                record(v["spec"], LinearInstrumentSpec)
            elif type(v["spec"]) is SpotInstrumentSpec:
                record(v["spec"], SpotInstrumentSpec)
            else:
                c._reject_v2()
        elif expected is CatalogSource:
            strings = ("source_id", "source_environment", "endpoint")
            times = ("received_at",)
        elif expected in (LinearInstrumentSpec, SpotInstrumentSpec):
            strings = ("base_coin", "quote_coin", "status")
            decimal_fields: tuple[str, ...] = (
                "price_tick",
                "min_quote_amount",
                "max_limit_quantity",
                "max_market_quantity",
            )
            nested = (("max_quote_amount", KnownNotPublished),)
            if expected is LinearInstrumentSpec:
                strings += ("settle_coin", "contract_type")
                counts = ("source_price_scale",)
                if type(v["is_pre_listing"]) is not bool:
                    c._reject_v2()
                decimal_fields += (
                    "min_price",
                    "max_price",
                    "quantity_step",
                    "min_quantity",
                    "min_leverage",
                    "max_leverage",
                    "leverage_step",
                )
            else:
                decimal_fields += ("base_precision", "quote_precision")
            for name in decimal_fields:
                record(v[name], DecimalField)
        elif expected is DecimalField:
            strings = ("raw",)
            counts = ("scale",)
            c._decimal_v2(v["value"], source=True, sign="positive")
        elif expected is KnownNotPublished:
            strings = ("field", "source_rule_revision")
        else:
            c._reject_v2()
        for name in strings:
            if type(v[name]) is not str:
                c._reject_v2()
        for name in identifiers:
            c._identifier_v2(v[name])
        for name in digests:
            c._digest_v2(v[name])
        for name in times:
            c._utc_v2(v[name])
        for name in counts:
            c._count_v2(v[name])
        for name in amounts:
            c._decimal_v2(v[name], source=source, sign="nonnegative")
        for name, child in nested:
            record(v[name], child)

    if member is not None:
        record(member, LinearRiskDomainMemberV2)
    elif inputs is None:
        record(coverage, LinearRiskDomainCoverageV2)
    else:
        if len(inputs) != 11:
            c._reject_v2()
        policy, catalog, account, reservations, marks, members, sources, now, rev, cov, gen = inputs
        record(policy, c.LinearRiskPolicyV2)
        record(catalog, InstrumentCatalog)
        record(account, c.LinearAccountRiskSnapshotV2)
        record(reservations, c.LinearReservationSnapshotV2)
        for mark in c._tuple_v2(marks, minimum=1, maximum=100):
            record(mark, c.LinearMarkQuoteV2)
        for domain_member in c._tuple_v2(members, minimum=1, maximum=100):
            record(domain_member, LinearRiskDomainMemberV2)
        for digest in c._tuple_v2(sources, minimum=1, maximum=1024):
            c._digest_v2(digest)
        c._utc_v2(now)
        c._digest_v2(rev)
        c._identifier_v2(cov)
        c._identifier_v2(gen)


def _source_record_sha256s_v2(value: object) -> tuple[str, ...]:
    records = c._tuple_v2(value, minimum=1, maximum=1024)
    result = tuple(c._digest_v2(item) for item in records)
    if result != tuple(sorted(set(result))):
        c._reject_v2()
    return result


def _member_payload_v2(value: object) -> dict[str, object]:
    _structural_domain_inputs_v2(member=value)
    v = c._fields_v2(value, LinearRiskDomainMemberV2)
    payload: dict[str, object] = {
        "instrument_pin": c._pin_payload_v2(v["instrument_pin"]),
        "mark": c._mark_payload_v2(v["mark"]),
        "tiers": _tier_snapshot_payload_v2(v["tiers"]),
        "account_margin_reference": c._observation_payload_v2(v["account_margin_reference"]),
        "account_risk_reference": c._computation_payload_v2(v["account_risk_reference"]),
    }
    for name in ("account_component_ids", "reservation_component_ids"):
        ids = tuple(
            c._identifier_v2(item) for item in c._tuple_v2(v[name], minimum=0, maximum=1024)
        )
        if ids != tuple(sorted(set(ids))):
            c._reject_v2()
        payload[name] = ids
    for name in (
        "account_risk_quote",
        "account_margin_quote",
        "reserved_risk_quote",
        "reserved_margin_quote",
    ):
        payload[name] = decimal_text_v2(c._decimal_v2(v[name], source=False, sign="nonnegative"))
    for name in ("account_position_count", "reserved_position_count"):
        payload[name] = c._count_v2(v[name])
    return payload


def _coverage_payload_v2(value: object) -> dict[str, object]:
    _structural_domain_inputs_v2(coverage=value)
    v = c._fields_v2(value, LinearRiskDomainCoverageV2)
    scope = c._account_payload_v2(v["account_scope"])
    selected = c._key_payload_v2(v["selected_entry_key"])
    if selected["product"] != "LINEAR":
        c._reject_v2("RISK_V2_UNSUPPORTED_PRODUCT")
    members = cast(tuple[LinearRiskDomainMemberV2, ...], v["members"])
    payload: dict[str, object] = {
        "account_scope": scope,
        "selected_entry_key": selected,
        "catalog_revision": c._digest_v2(v["catalog_revision"]),
        "coverage_revision": c._identifier_v2(v["coverage_revision"]),
        "source_generation": c._identifier_v2(v["source_generation"]),
        "members": tuple(_member_payload_v2(member) for member in members),
        "source_record_sha256s": _source_record_sha256s_v2(v["source_record_sha256s"]),
    }
    for name in ("account_snapshot_sha256", "reservation_snapshot_sha256", "mark_set_sha256"):
        payload[name] = c._digest_v2(v[name])
    order = tuple(c._key_order_v2(member.instrument_pin.key) for member in members)
    if order != tuple(sorted(set(order))):
        c._reject_v2()
    if c._key_order_v2(v["selected_entry_key"]) not in order:
        c._reject_v2("RISK_V2_HELD_COVERAGE_REQUIRED")
    account_ids = tuple(
        identifier for member in members for identifier in member.account_component_ids
    )
    reserved_ids = tuple(
        identifier for member in members for identifier in member.reservation_component_ids
    )
    if len(account_ids) > 1024 or len(reserved_ids) > 1024:
        c._reject_v2("RISK_V2_RESOURCE_LIMIT")
    if (
        len(set(account_ids)) != len(account_ids)
        or len(set(reserved_ids)) != len(reserved_ids)
        or set(account_ids).intersection(reserved_ids)
    ):
        c._reject_v2("RISK_V2_EXPOSURE_OVERLAP")
    for member in members:
        pin, mark, tiers = member.instrument_pin, member.mark, member.tiers
        if pin.catalog_revision != v["catalog_revision"]:
            c._reject_v2("RISK_V2_REVISION_MISMATCH")
        if c._key_payload_v2(pin.key) != c._key_payload_v2(mark.instrument) or (
            c._key_payload_v2(pin.key) != c._key_payload_v2(tiers.instrument)
        ):
            c._reject_v2("RISK_V2_SCOPE_MISMATCH")
        if tiers.instrument_revision != pin.specification_revision:
            c._reject_v2("RISK_V2_REVISION_MISMATCH")
        if (
            mark.reference.source_environment != pin.source_environment
            or tiers.reference.source_environment != pin.source_environment
            or member.account_margin_reference.source_environment
            not in (scope["environment"], "FIXTURE")
            or member.account_risk_reference.target_environment
            not in (scope["environment"], "FIXTURE")
        ):
            c._reject_v2("RISK_V2_SOURCE_MISMATCH")
    if c.mark_set_sha256_v2(tuple(member.mark for member in members)) != v["mark_set_sha256"]:
        c._reject_v2("RISK_V2_SOURCE_MISMATCH")
    return payload


def _catalog_risk_observations_v2(
    catalog: InstrumentCatalog,
    *,
    required: tuple[InstrumentKey, ...],
    expected_revision: str,
    now: datetime,
) -> tuple[InstrumentObservation, ...]:
    v = c._fields_v2(catalog, InstrumentCatalog)
    # The caller's first pass checked every scalar before any source parser.
    try:
        for observation in catalog.observations:
            validate_instrument_observation(observation)
        rebuilt = build_instrument_catalog(
            catalog.observations,
            declared_universe=catalog.declared_universe,
            now=now,
            max_age=catalog.max_age,
        )
    except CatalogRejected:
        c._reject_v2()
    declared_order = tuple(c._key_order_v2(key) for key in catalog.declared_universe)
    observation_order = tuple(c._key_order_v2(item.key) for item in catalog.observations)
    if (
        type(v["schema_version"]) is not int
        or v["schema_version"] != 1
        or declared_order != tuple(c._key_order_v2(key) for key in rebuilt.declared_universe)
        or observation_order != tuple(c._key_order_v2(item.key) for item in rebuilt.observations)
        or catalog.revision != rebuilt.revision
        or c._utc_v2(catalog.expires_at) != c._utc_v2(rebuilt.expires_at)
    ):
        c._reject_v2()
    if expected_revision != rebuilt.revision:
        c._reject_v2("RISK_V2_REVISION_MISMATCH")
    by_key = {c._key_order_v2(observation.key): observation for observation in rebuilt.observations}
    if any(c._key_order_v2(key) not in by_key for key in required):
        c._reject_v2("RISK_V2_HELD_COVERAGE_REQUIRED")
    return tuple(by_key[c._key_order_v2(key)] for key in required)


def build_linear_risk_domain_coverage_v2(
    *,
    policy: c.LinearRiskPolicyV2,
    catalog: InstrumentCatalog,
    catalog_revision: str,
    coverage_revision: str,
    source_generation: str,
    account: c.LinearAccountRiskSnapshotV2,
    reservations: c.LinearReservationSnapshotV2,
    marks: tuple[c.LinearMarkQuoteV2, ...],
    members: tuple[LinearRiskDomainMemberV2, ...],
    source_record_sha256s: tuple[str, ...],
    now: datetime,
) -> LinearRiskDomainCoverageV2:
    _structural_domain_inputs_v2(
        inputs=(
            policy,
            catalog,
            account,
            reservations,
            marks,
            members,
            source_record_sha256s,
            now,
            catalog_revision,
            coverage_revision,
            source_generation,
        )
    )
    policy_payload = c._policy_payload_v2(policy)
    account_payload = c._account_snapshot_payload_v2(account)
    reservation_payload = c._reservation_snapshot_payload_v2(reservations)
    c._marks_payload_v2(marks)
    for member in members:
        _member_payload_v2(member)
    now = c._utc_v2(now)
    required = tuple(
        sorted(
            {
                policy.scope.instrument,
                *(item.instrument for item in account.components),
                *(item.instrument for item in reservations.components),
            },
            key=c._key_order_v2,
        )
    )
    c._tuple_v2(required, minimum=1, maximum=100)
    observations = _catalog_risk_observations_v2(
        catalog,
        required=required,
        expected_revision=catalog_revision,
        now=now,
    )
    if (
        c._account_payload_v2(policy.scope.product_scope.account) != account_payload["account"]
        or account_payload["account"] != reservation_payload["account"]
        or account.margin_mode != policy.margin_mode
        or account.position_mode != policy.position_mode
    ):
        c._reject_v2("RISK_V2_SCOPE_MISMATCH")
    # Keep the validated policy payload alive only for its validation, not authority.
    del policy_payload
    value = LinearRiskDomainCoverageV2(
        account.account,
        catalog_revision,
        policy.scope.instrument,
        coverage_revision,
        source_generation,
        members,
        c.account_snapshot_sha256_v2(account),
        c.reservation_snapshot_sha256_v2(reservations),
        c.mark_set_sha256_v2(marks),
        source_record_sha256s,
    )
    member_order = tuple(c._key_order_v2(member.instrument_pin.key) for member in members)
    if member_order != tuple(sorted(set(member_order))):
        c._reject_v2()
    if member_order != tuple(c._key_order_v2(key) for key in required):
        c._reject_v2("RISK_V2_HELD_COVERAGE_REQUIRED")
    prices = c._mark_values_v2(
        marks,
        required=required,
        now=now,
        max_age_seconds=policy.max_quote_age_seconds,
    )
    _coverage_payload_v2(value)
    account_ids = {item.component_id for item in account.components}
    if account_ids.intersection(item.component_id for item in reservations.components):
        c._reject_v2("RISK_V2_EXPOSURE_OVERLAP")
    maximum = decimal_fraction_v2(policy.max_evidence_age_seconds)
    for timestamp in (
        account.reference.server_observed_at,
        account.reference.received_at,
        reservations.observed_at,
    ):
        c._fresh_v2(timestamp, now=now, maximum=maximum)
    for member, observation, mark in zip(members, observations, marks, strict=True):
        pin = c.pin_instrument_v2(observation, catalog_revision=catalog_revision)
        if member.instrument_pin.specification_revision != pin.specification_revision:
            c._reject_v2("RISK_V2_REVISION_MISMATCH")
        if c._pin_payload_v2(member.instrument_pin) != c._pin_payload_v2(pin) or c._mark_payload_v2(
            member.mark
        ) != c._mark_payload_v2(mark):
            c._reject_v2("RISK_V2_SOURCE_MISMATCH")
        account_components = tuple(
            item for item in account.components if item.instrument == pin.key
        )
        reserved_components = tuple(
            item for item in reservations.components if item.instrument == pin.key
        )
        if member.account_component_ids != tuple(
            item.component_id for item in account_components
        ) or member.reservation_component_ids != tuple(
            item.component_id for item in reserved_components
        ):
            c._reject_v2("RISK_V2_HELD_COVERAGE_REQUIRED")
        # Bounds apply to each exact intermediate, and to the final comparison.
        gross = _fraction(
            decimal_fraction_v2(c._gross_components_v2(account_components, prices=prices))
            + decimal_fraction_v2(c._gross_components_v2(reserved_components, prices=prices))
        )
        if _compare(gross, decimal_fraction_v2(member.tiers.rows[-1].upper_inclusive_quote)) > 0:
            c._reject_v2("RISK_V2_RISK_TIER_UNVERIFIED")
        for timestamp in (
            pin.response_time,
            pin.received_at,
            member.tiers.reference.server_observed_at,
            member.tiers.reference.received_at,
            member.account_margin_reference.server_observed_at,
            member.account_margin_reference.received_at,
            member.account_risk_reference.computed_at,
        ):
            c._fresh_v2(timestamp, now=now, maximum=maximum)
    for member_name, header in (
        ("account_risk_quote", account.total_risk),
        ("account_margin_quote", account.total_margin),
        ("reserved_risk_quote", reservations.risk_amount),
        ("reserved_margin_quote", reservations.margin_amount),
    ):
        total = Fraction(0)
        for member in members:
            total = _fraction(
                total + decimal_fraction_v2(cast(Decimal, getattr(member, member_name)))
            )
        if _compare(total, decimal_fraction_v2(header)) != 0:
            c._reject_v2("RISK_V2_HELD_COVERAGE_REQUIRED")
    for member_name, count in (
        ("account_position_count", account.position_count),
        ("reserved_position_count", reservations.position_count),
    ):
        total_count = 0
        for member in members:
            total_count = c._count_v2(total_count + cast(int, getattr(member, member_name)))
        if total_count != count:
            c._reject_v2("RISK_V2_HELD_COVERAGE_REQUIRED")
    # Apply the shared whole-payload bound before returning any coverage value.
    risk_domain_sha256_v2(value)
    return value
