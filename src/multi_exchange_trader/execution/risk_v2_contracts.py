"""Pure Risk V2 value contracts; no execution or source authority."""

import hashlib
import json
import re
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime, timezone
from decimal import Decimal
from fractions import Fraction
from typing import Any, Literal, NoReturn, cast

from multi_exchange_trader.execution.modes import StrategyReference
from multi_exchange_trader.execution.product_scope import ProductScope
from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.execution.risk_v2_math import (
    RiskV2Rejected as RiskV2Rejected,  # noqa: PLC0414 - explicit shared export
)
from multi_exchange_trader.execution.risk_v2_math import (
    decimal_fraction_v2,
    decimal_text_v2,
    finite_decimal_v2,
)
from multi_exchange_trader.market_data.instrument_definition import (
    LINEAR_SOURCE_RULE_REVISION,
    PARSER_REVISION,
    CatalogRejected,
    InstrumentObservation,
    SourceEnvironment,
    validate_instrument_observation,
)
from multi_exchange_trader.trading_product import InstrumentKey, bybit_native_instrument_id

RiskSourceEnvironmentV2 = Literal["PUBLIC_MAINNET", "PUBLIC_TESTNET", "DEMO", "LIVE", "FIXTURE"]


@dataclass(frozen=True)
class RiskScopeV2:
    product_scope: ProductScope
    instrument: InstrumentKey
    strategy: StrategyReference

    def __post_init__(self) -> None:
        _scope_payload_v2(self)


@dataclass(frozen=True)
class InstrumentPinV2:
    key: InstrumentKey
    native_instrument_id: str
    catalog_revision: str
    specification_revision: str
    parser_revision: str
    source_rule_revision: str
    source_id: str
    source_environment: SourceEnvironment
    response_time: datetime
    received_at: datetime
    raw_row_sha256: str

    def __post_init__(self) -> None:
        _pin_payload_v2(self)
        object.__setattr__(self, "response_time", _utc_v2(self.response_time))
        object.__setattr__(self, "received_at", _utc_v2(self.received_at))


@dataclass(frozen=True)
class RiskObservationRefV2:
    observation_id: str
    source_id: str
    source_environment: RiskSourceEnvironmentV2
    source_rule_revision: str
    server_observed_at: datetime
    received_at: datetime
    payload_sha256: str

    def __post_init__(self) -> None:
        _observation_payload_v2(self)
        object.__setattr__(self, "server_observed_at", _utc_v2(self.server_observed_at))
        object.__setattr__(self, "received_at", _utc_v2(self.received_at))


@dataclass(frozen=True)
class RiskComputationRefV2:
    calculation_id: str
    model_revision: str
    target_environment: Literal["DEMO", "LIVE", "FIXTURE"]
    computed_at: datetime
    input_sha256: str

    def __post_init__(self) -> None:
        _computation_payload_v2(self)
        object.__setattr__(self, "computed_at", _utc_v2(self.computed_at))


@dataclass(frozen=True)
class LinearCapitalCapsV2:
    max_order_admission_notional: Decimal
    max_account_admission_notional: Decimal
    valuation_rule: Literal["LINEAR_MARK_GROSS_AT_ADMISSION_V1"]
    turnover_requirement: Literal["NOT_REQUIRED", "HARD_EXECUTION_QUOTE_CAP"]
    max_execution_quote_amount: Decimal | None

    def __post_init__(self) -> None:
        _caps_payload_v2(self)


@dataclass(frozen=True)
class LinearRiskPolicyV2:
    scope: RiskScopeV2
    revision: str
    sizing_rule: Literal["LOSS_FIXED"]
    caps: LinearCapitalCapsV2
    leverage_min: Decimal
    leverage_max: Decimal
    per_trade_loss: Decimal
    max_daily_loss: Decimal
    max_drawdown: Decimal
    max_consecutive_losses: int
    max_positions: int
    max_total_risk: Decimal
    max_total_margin: Decimal
    margin_allocation: Decimal
    margin_mode: Literal["ISOLATED", "CROSS"]
    position_mode: Literal["ONE_WAY"]
    liquidation_buffer_fraction: Decimal
    max_quote_age_seconds: Decimal
    max_evidence_age_seconds: Decimal

    def __post_init__(self) -> None:
        _policy_payload_v2(self)


@dataclass(frozen=True)
class FixedLimitPriceV2:
    side: Literal["BUY", "SELL"]
    limit_price: Decimal
    origin_quote_id: str
    valid_until: datetime

    def __post_init__(self) -> None:
        _price_payload_v2(self)
        object.__setattr__(self, "valid_until", _utc_v2(self.valid_until))


@dataclass(frozen=True)
class LinearEntryProposalV2:
    scope: RiskScopeV2
    side: Literal["LONG", "SHORT"]
    stop_price: Decimal
    entry: FixedLimitPriceV2

    def __post_init__(self) -> None:
        _proposal_payload_v2(self)


@dataclass(frozen=True)
class LinearCostAllowanceV2:
    scope: RiskScopeV2
    reference: RiskComputationRefV2
    policy_sha256: str
    instrument_revision: str
    origin_quote_id: str
    side: Literal["LONG", "SHORT"]
    entry_limit_price: Decimal
    stop_price: Decimal
    fee_reference: RiskObservationRefV2
    fee_currency: Literal["USDT"]
    fee_rule_revision: str
    funding_reference: RiskObservationRefV2
    funding_rate: Decimal
    next_funding_at: datetime
    holding_until: datetime
    funding_rule_revision: str
    stop_gap_model_revision: str
    fee_quote_per_base: Decimal
    funding_quote_per_base: Decimal
    stop_gap_quote_per_base: Decimal
    valid_until: datetime
    cost_rule_revision: str

    def __post_init__(self) -> None:
        _cost_payload_v2(self)
        object.__setattr__(self, "next_funding_at", _utc_v2(self.next_funding_at))
        object.__setattr__(self, "holding_until", _utc_v2(self.holding_until))
        object.__setattr__(self, "valid_until", _utc_v2(self.valid_until))


@dataclass(frozen=True)
class FixedLimitEnvelopeV2:
    schema_version: Literal[2]
    scope: RiskScopeV2
    instrument_pin: InstrumentPinV2
    policy_revision: str
    policy_sha256: str
    side: Literal["BUY", "SELL"]
    base_quantity: Decimal
    limit_price: Decimal
    order_type: Literal["LIMIT"]
    time_in_force: Literal["IOC"]
    role: Literal["ENTRY"]
    reduce_only: Literal[False]
    origin_quote_id: str
    valid_until: datetime

    def __post_init__(self) -> None:
        _envelope_payload_v2(self)
        object.__setattr__(self, "valid_until", _utc_v2(self.valid_until))


@dataclass(frozen=True)
class LinearSizedEntryV2:
    scope: RiskScopeV2
    assessed_at: datetime
    policy_sha256: str
    envelope: FixedLimitEnvelopeV2
    cost_reference: RiskComputationRefV2
    cost_allowance_sha256: str
    stop_price: Decimal
    stop_distance_quote_per_base: Decimal
    unit_cost_quote: Decimal
    unit_loss_quote: Decimal
    estimated_loss_quote: Decimal
    cost_allowance_quote: Decimal
    funding_allowance_quote: Decimal
    formula_version: Literal["LINEAR_FIXED_LIMIT_LOSS_SIZE_V2"]

    def __post_init__(self) -> None:
        _sizing_payload_v2(self)
        object.__setattr__(self, "assessed_at", _utc_v2(self.assessed_at))


@dataclass(frozen=True)
class ExposureQuantityV2:
    component_id: str
    instrument: InstrumentKey
    origin: Literal["ACCOUNT_POSITION", "ACCOUNT_ENTRY_ORDER", "PENDING_ENTRY"]
    side: Literal["BUY", "SELL"]
    base_quantity: Decimal

    def __post_init__(self) -> None:
        _exposure_payload_v2(self)


@dataclass(frozen=True)
class LinearMarkQuoteV2:
    instrument: InstrumentKey
    reference: RiskObservationRefV2
    mark_price: Decimal

    def __post_init__(self) -> None:
        _mark_payload_v2(self)


@dataclass(frozen=True)
class LinearAccountRiskSnapshotV2:
    account: ProtectionScope
    reference: RiskObservationRefV2
    coverage_revision: str
    components: tuple[ExposureQuantityV2, ...]
    loss_accounting: Literal["UTC_REALIZED_NET_FEES_FUNDING"]
    loss_day: date
    equity: Decimal
    available_margin: Decimal
    total_risk: Decimal
    total_margin: Decimal
    position_count: int
    daily_loss: Decimal
    drawdown: Decimal
    consecutive_losses: int
    margin_mode: Literal["ISOLATED", "CROSS"]
    position_mode: Literal["ONE_WAY"]
    external_same_symbol_activity_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _account_snapshot_payload_v2(self)


@dataclass(frozen=True)
class LinearReservationSnapshotV2:
    account: ProtectionScope
    snapshot_id: str
    owner_generation: str
    journal_revision: int
    held_quantity_sha256: str
    observed_at: datetime
    excluded_command_key: str | None
    components: tuple[ExposureQuantityV2, ...]
    risk_amount: Decimal
    margin_amount: Decimal
    position_count: int

    def __post_init__(self) -> None:
        _reservation_snapshot_payload_v2(self)
        object.__setattr__(self, "observed_at", _utc_v2(self.observed_at))


@dataclass(frozen=True)
class LinearAdmissionValuationV2:
    scope: RiskScopeV2
    assessed_at: datetime
    account_observation_id: str
    reservation_snapshot_id: str
    mark_observation_ids: tuple[str, ...]
    account_gross_quote: Decimal
    reserved_gross_quote: Decimal
    proposed_mark_quote: Decimal
    combined_gross_quote: Decimal
    valuation_rule: Literal["LINEAR_MARK_GROSS_AT_ADMISSION_V1"]

    def __post_init__(self) -> None:
        _valuation_payload_v2(self)
        object.__setattr__(self, "assessed_at", _utc_v2(self.assessed_at))


def account_snapshot_sha256_v2(value: LinearAccountRiskSnapshotV2) -> str:
    return _canonical_sha256_v2("LinearAccountRiskSnapshotV2", _account_snapshot_payload_v2(value))


def reservation_snapshot_sha256_v2(value: LinearReservationSnapshotV2) -> str:
    return _canonical_sha256_v2(
        "LinearReservationSnapshotV2", _reservation_snapshot_payload_v2(value)
    )


def mark_set_sha256_v2(value: tuple[LinearMarkQuoteV2, ...]) -> str:
    return _canonical_sha256_v2("LinearMarkQuoteSetV2", {"marks": _marks_payload_v2(value)})


def valuation_sha256_v2(valuation: LinearAdmissionValuationV2) -> str:
    payload = _valuation_payload_v2(valuation)
    del payload["assessed_at"]
    return _canonical_sha256_v2("LinearAdmissionValuationV2", payload)


def pin_instrument_v2(
    observation: InstrumentObservation, *, catalog_revision: str
) -> InstrumentPinV2:
    _identifier_v2(catalog_revision)
    try:
        # The shared validator owns all parsing and source-integrity checks.
        validate_instrument_observation(observation)
    except CatalogRejected:
        _reject_v2("RISK_V2_INPUT_INVALID")
    if observation.key.product != "LINEAR":
        _reject_v2("RISK_V2_UNSUPPORTED_PRODUCT")
    return InstrumentPinV2(
        observation.key,
        observation.native_instrument_id,
        catalog_revision,
        observation.specification_revision,
        observation.parser_revision,
        observation.source_rule_revision,
        observation.source.source_id,
        observation.source.source_environment,
        observation.response_time,
        observation.source.received_at,
        observation.raw_row_sha256,
    )


def risk_policy_sha256_v2(policy: LinearRiskPolicyV2) -> str:
    return _canonical_sha256_v2("LinearRiskPolicyV2", _policy_payload_v2(policy))


def cost_allowance_sha256_v2(costs: LinearCostAllowanceV2) -> str:
    return _canonical_sha256_v2("LinearCostAllowanceV2", _cost_payload_v2(costs))


def envelope_sha256_v2(envelope: FixedLimitEnvelopeV2) -> str:
    return _canonical_sha256_v2("FixedLimitEnvelopeV2", _envelope_payload_v2(envelope))


def sizing_sha256_v2(sizing: LinearSizedEntryV2) -> str:
    payload = _sizing_payload_v2(sizing)
    del payload["assessed_at"]
    return _canonical_sha256_v2("LinearSizedEntryV2", payload)


def _proposal_payload_v2(proposal: LinearEntryProposalV2) -> dict[str, object]:
    values = _fields_v2(proposal, LinearEntryProposalV2)
    scope = _scope_payload_v2(values["scope"])
    side = _literal_v2(values["side"], ("LONG", "SHORT"))
    stop = _decimal_v2(values["stop_price"], source=True, sign="positive")
    entry = _price_payload_v2(values["entry"])
    expected = "BUY" if side == "LONG" else "SELL"
    if entry["side"] != expected:
        _reject_v2("RISK_V2_SCOPE_MISMATCH")
    _distance_v2(expected, cast(FixedLimitPriceV2, values["entry"]).limit_price, stop)
    return {"scope": scope, "side": side, "stop_price": decimal_text_v2(stop), "entry": entry}


_IDENTIFIER_V2 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}", re.ASCII)
_DIGEST_V2 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_MAX_CANONICAL_BYTES_V2 = 65536


def _reject_v2(code: str = "RISK_V2_INPUT_INVALID") -> NoReturn:
    raise RiskV2Rejected(code) from None


def _fields_v2(value: object, expected: type[object]) -> dict[str, Any]:
    if type(value) is not expected:
        _reject_v2()
    try:
        # Field metadata belongs to this fixed trusted class, never the input.
        return {
            field.name: object.__getattribute__(value, field.name)
            for field in fields(cast(type[RiskScopeV2], expected))
        }
    except AttributeError:
        _reject_v2()


def _identifier_v2(value: object) -> str:
    if type(value) is not str or _IDENTIFIER_V2.fullmatch(value) is None:
        _reject_v2()
    return value


def _digest_v2(value: object) -> str:
    if type(value) is not str or _DIGEST_V2.fullmatch(value) is None:
        _reject_v2()
    return value


def _literal_v2(
    value: object, choices: tuple[str, ...], *, code: str = "RISK_V2_INPUT_INVALID"
) -> str:
    if type(value) is not str or value not in choices:
        _reject_v2(code)
    return value


def _utc_v2(value: object) -> datetime:
    if type(value) is not datetime or type(value.tzinfo) is not timezone:
        _reject_v2()
    try:
        return value.astimezone(UTC)
    except (OverflowError, ValueError):
        _reject_v2()


def _time_text_v2(value: object) -> str:
    return _utc_v2(value).isoformat(timespec="microseconds")


def _decimal_v2(
    value: object, *, source: bool, sign: Literal["positive", "nonnegative", "signed"]
) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        _reject_v2()
    parts = value.as_tuple()
    exponent = cast(int, parts.exponent)
    if source and (
        len(parts.digits) > 256
        or abs(exponent) > 1000
        or abs(exponent + len(parts.digits) - 1) > 1000
    ):
        _reject_v2("RISK_V2_RESOURCE_LIMIT")
    number = decimal_fraction_v2(value)
    if (sign == "positive" and number <= 0) or (sign == "nonnegative" and number < 0):
        _reject_v2()
    return value


def _decimal_text_field_v2(
    values: dict[str, Any],
    name: str,
    *,
    source: bool,
    sign: Literal["positive", "nonnegative", "signed"] = "positive",
) -> str:
    return decimal_text_v2(_decimal_v2(values[name], source=source, sign=sign))


def _key_payload_v2(value: object) -> dict[str, str]:
    values = _fields_v2(value, InstrumentKey)
    for name in ("venue", "product", "symbol"):
        if type(values[name]) is not str:
            _reject_v2()
    try:
        bybit_native_instrument_id(cast(InstrumentKey, value))
    except ValueError:
        _reject_v2()
    return {"venue": values["venue"], "product": values["product"], "symbol": values["symbol"]}


def _scope_payload_v2(value: object) -> dict[str, object]:
    values = _fields_v2(value, RiskScopeV2)
    product = _fields_v2(values["product_scope"], ProductScope)
    account = _fields_v2(product["account"], ProtectionScope)
    identifier = account["account_id"]
    if (
        type(identifier) is not str
        or not 1 <= len(identifier) <= 256
        or identifier != identifier.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in identifier)
    ):
        _reject_v2()
    environment = _literal_v2(account["environment"], ("DEMO", "LIVE"))
    product_name = _literal_v2(product["product"], ("SPOT", "LINEAR"))
    key = _key_payload_v2(values["instrument"])
    strategy = _fields_v2(values["strategy"], StrategyReference)
    strategy_payload = {
        name: _identifier_v2(strategy[name]) for name in ("strategy_id", "revision")
    }
    if product_name != key["product"]:
        _reject_v2("RISK_V2_SCOPE_MISMATCH")
    if product_name != "LINEAR":
        _reject_v2("RISK_V2_UNSUPPORTED_PRODUCT")
    return {
        "product_scope": {
            "account": {"account_id": identifier, "environment": environment},
            "product": product_name,
        },
        "instrument": key,
        "strategy": strategy_payload,
    }


def _pin_payload_v2(value: object) -> dict[str, object]:
    values = _fields_v2(value, InstrumentPinV2)
    key = _key_payload_v2(values["key"])
    if key["product"] != "LINEAR":
        _reject_v2("RISK_V2_UNSUPPORTED_PRODUCT")
    native = values["native_instrument_id"]
    if type(native) is not str or native != bybit_native_instrument_id(values["key"]):
        _reject_v2("RISK_V2_SCOPE_MISMATCH")
    payload: dict[str, object] = {"key": key, "native_instrument_id": native}
    for name in ("catalog_revision", "parser_revision", "source_rule_revision", "source_id"):
        payload[name] = _identifier_v2(values[name])
    if (
        values["parser_revision"] != PARSER_REVISION
        or values["source_rule_revision"] != LINEAR_SOURCE_RULE_REVISION
    ):
        _reject_v2("RISK_V2_REVISION_MISMATCH")
    payload["source_environment"] = _literal_v2(
        values["source_environment"], ("PUBLIC_MAINNET", "PUBLIC_TESTNET", "FIXTURE")
    )
    for name in ("specification_revision", "raw_row_sha256"):
        payload[name] = _digest_v2(values[name])
    for name in ("response_time", "received_at"):
        payload[name] = _time_text_v2(values[name])
    if _utc_v2(values["response_time"]) > _utc_v2(values["received_at"]):
        _reject_v2("RISK_V2_CAUSAL_ORDER_INVALID")
    return payload


def _observation_payload_v2(value: object) -> dict[str, object]:
    values = _fields_v2(value, RiskObservationRefV2)
    payload: dict[str, object] = {
        name: _identifier_v2(values[name])
        for name in ("observation_id", "source_id", "source_rule_revision")
    }
    payload["source_environment"] = _literal_v2(
        values["source_environment"],
        ("PUBLIC_MAINNET", "PUBLIC_TESTNET", "DEMO", "LIVE", "FIXTURE"),
    )
    payload["payload_sha256"] = _digest_v2(values["payload_sha256"])
    for name in ("server_observed_at", "received_at"):
        payload[name] = _time_text_v2(values[name])
    if _utc_v2(values["server_observed_at"]) > _utc_v2(values["received_at"]):
        _reject_v2("RISK_V2_CAUSAL_ORDER_INVALID")
    return payload


def _computation_payload_v2(value: object) -> dict[str, object]:
    values = _fields_v2(value, RiskComputationRefV2)
    return {
        "calculation_id": _identifier_v2(values["calculation_id"]),
        "model_revision": _identifier_v2(values["model_revision"]),
        "target_environment": _literal_v2(
            values["target_environment"], ("DEMO", "LIVE", "FIXTURE")
        ),
        "computed_at": _time_text_v2(values["computed_at"]),
        "input_sha256": _digest_v2(values["input_sha256"]),
    }


def _caps_payload_v2(value: object) -> dict[str, object]:
    values = _fields_v2(value, LinearCapitalCapsV2)
    payload: dict[str, object] = {
        name: _decimal_text_field_v2(values, name, source=True)
        for name in ("max_order_admission_notional", "max_account_admission_notional")
    }
    payload["valuation_rule"] = _literal_v2(
        values["valuation_rule"], ("LINEAR_MARK_GROSS_AT_ADMISSION_V1",)
    )
    requirement = _literal_v2(
        values["turnover_requirement"], ("NOT_REQUIRED", "HARD_EXECUTION_QUOTE_CAP")
    )
    payload["turnover_requirement"] = requirement
    if requirement == "NOT_REQUIRED":
        if values["max_execution_quote_amount"] is not None:
            _reject_v2()
        payload["max_execution_quote_amount"] = None
    else:
        payload["max_execution_quote_amount"] = _decimal_text_field_v2(
            values, "max_execution_quote_amount", source=True
        )
    return payload


def _policy_payload_v2(value: object) -> dict[str, object]:
    values = _fields_v2(value, LinearRiskPolicyV2)
    payload: dict[str, object] = {
        "scope": _scope_payload_v2(values["scope"]),
        "caps": _caps_payload_v2(values["caps"]),
        "revision": _identifier_v2(values["revision"]),
        "sizing_rule": _literal_v2(values["sizing_rule"], ("LOSS_FIXED",)),
    }
    for name in (
        "leverage_min",
        "leverage_max",
        "per_trade_loss",
        "max_daily_loss",
        "max_drawdown",
        "max_total_risk",
        "max_total_margin",
        "margin_allocation",
    ):
        payload[name] = _decimal_text_field_v2(values, name, source=True)
    for name in (
        "liquidation_buffer_fraction",
        "max_quote_age_seconds",
        "max_evidence_age_seconds",
    ):
        payload[name] = _decimal_text_field_v2(values, name, source=True, sign="nonnegative")
    for name in ("max_consecutive_losses", "max_positions"):
        number = values[name]
        if type(number) is not int or number <= 0:
            _reject_v2()
        # JSON must never invoke Python's global huge-int text machinery.
        if number.bit_length() > 1_000_000:
            _reject_v2("RISK_V2_RESOURCE_LIMIT")
        payload[name] = number
    payload["margin_mode"] = _literal_v2(
        values["margin_mode"], ("ISOLATED", "CROSS"), code="RISK_V2_UNSUPPORTED_MODE"
    )
    payload["position_mode"] = _literal_v2(
        values["position_mode"], ("ONE_WAY",), code="RISK_V2_UNSUPPORTED_MODE"
    )
    if (
        decimal_fraction_v2(values["leverage_min"]) > decimal_fraction_v2(values["leverage_max"])
        or decimal_fraction_v2(values["liquidation_buffer_fraction"]) >= 1
    ):
        _reject_v2()
    return payload


def _price_payload_v2(value: object) -> dict[str, object]:
    values = _fields_v2(value, FixedLimitPriceV2)
    return {
        "side": _literal_v2(values["side"], ("BUY", "SELL")),
        "limit_price": _decimal_text_field_v2(values, "limit_price", source=True),
        "origin_quote_id": _identifier_v2(values["origin_quote_id"]),
        "valid_until": _time_text_v2(values["valid_until"]),
    }


def _distance_v2(side: str, limit: Decimal, stop: Decimal) -> Fraction:
    entry_fraction, stop_fraction = decimal_fraction_v2(limit), decimal_fraction_v2(stop)
    distance = (
        entry_fraction - stop_fraction
        if side in ("BUY", "LONG")
        else stop_fraction - entry_fraction
    )
    if distance <= 0:
        _reject_v2("RISK_V2_INVALID_STOP")
    return distance


def _target_environment_v2(environment: str, scope: RiskScopeV2) -> None:
    if environment != "FIXTURE" and environment != scope.product_scope.account.environment:
        _reject_v2("RISK_V2_SOURCE_MISMATCH")


def _cost_payload_v2(value: object) -> dict[str, object]:
    values = _fields_v2(value, LinearCostAllowanceV2)
    payload: dict[str, object] = {
        "scope": _scope_payload_v2(values["scope"]),
        "reference": _computation_payload_v2(values["reference"]),
        "fee_reference": _observation_payload_v2(values["fee_reference"]),
        "funding_reference": _observation_payload_v2(values["funding_reference"]),
    }
    for name in ("policy_sha256", "instrument_revision"):
        payload[name] = _digest_v2(values[name])
    for name in (
        "origin_quote_id",
        "fee_rule_revision",
        "funding_rule_revision",
        "stop_gap_model_revision",
        "cost_rule_revision",
    ):
        payload[name] = _identifier_v2(values[name])
    payload["side"] = _literal_v2(values["side"], ("LONG", "SHORT"))
    payload["fee_currency"] = _literal_v2(values["fee_currency"], ("USDT",))
    for name in ("entry_limit_price", "stop_price"):
        payload[name] = _decimal_text_field_v2(values, name, source=True)
    payload["funding_rate"] = _decimal_text_field_v2(
        values, "funding_rate", source=True, sign="signed"
    )
    for name in ("fee_quote_per_base", "funding_quote_per_base", "stop_gap_quote_per_base"):
        payload[name] = _decimal_text_field_v2(values, name, source=False, sign="nonnegative")
    for name in ("next_funding_at", "holding_until", "valid_until"):
        payload[name] = _time_text_v2(values[name])
    reference = cast(RiskComputationRefV2, values["reference"])
    if reference.model_revision != values["cost_rule_revision"]:
        _reject_v2("RISK_V2_REVISION_MISMATCH")
    completed_at = _utc_v2(reference.computed_at)
    for source in (
        cast(RiskObservationRefV2, values["fee_reference"]),
        cast(RiskObservationRefV2, values["funding_reference"]),
    ):
        if _utc_v2(source.received_at) > completed_at:
            _reject_v2("RISK_V2_CAUSAL_ORDER_INVALID")
        if source.source_environment in ("DEMO", "LIVE"):
            _target_environment_v2(source.source_environment, values["scope"])
    fee_environment = cast(RiskObservationRefV2, values["fee_reference"]).source_environment
    if fee_environment not in ("DEMO", "LIVE", "FIXTURE"):
        _reject_v2("RISK_V2_SOURCE_MISMATCH")
    _target_environment_v2(reference.target_environment, values["scope"])
    if any(
        _utc_v2(values[name]) <= completed_at
        for name in ("next_funding_at", "holding_until", "valid_until")
    ):
        _reject_v2("RISK_V2_CAUSAL_ORDER_INVALID")
    _distance_v2(values["side"], values["entry_limit_price"], values["stop_price"])
    return payload


def _envelope_payload_v2(value: object) -> dict[str, object]:
    values = _fields_v2(value, FixedLimitEnvelopeV2)
    payload: dict[str, object] = {
        "scope": _scope_payload_v2(values["scope"]),
        "instrument_pin": _pin_payload_v2(values["instrument_pin"]),
    }
    if type(values["schema_version"]) is not int or values["schema_version"] != 2:
        _reject_v2()
    if type(values["reduce_only"]) is not bool or values["reduce_only"] is not False:
        _reject_v2()
    payload["schema_version"], payload["reduce_only"] = 2, False
    for name, choices in (
        ("side", ("BUY", "SELL")),
        ("order_type", ("LIMIT",)),
        ("time_in_force", ("IOC",)),
        ("role", ("ENTRY",)),
    ):
        payload[name] = _literal_v2(values[name], choices)
    for name in ("policy_revision", "origin_quote_id"):
        payload[name] = _identifier_v2(values[name])
    payload["policy_sha256"] = _digest_v2(values["policy_sha256"])
    payload["base_quantity"] = _decimal_text_field_v2(values, "base_quantity", source=False)
    payload["limit_price"] = _decimal_text_field_v2(values, "limit_price", source=True)
    payload["valid_until"] = _time_text_v2(values["valid_until"])
    scope, pin = cast(RiskScopeV2, values["scope"]), cast(InstrumentPinV2, values["instrument_pin"])
    if _key_payload_v2(scope.instrument) != _key_payload_v2(pin.key):
        _reject_v2("RISK_V2_SCOPE_MISMATCH")
    if _utc_v2(values["valid_until"]) <= _utc_v2(pin.received_at):
        _reject_v2("RISK_V2_CAUSAL_ORDER_INVALID")
    return payload


def _sizing_payload_v2(value: object) -> dict[str, object]:
    values = _fields_v2(value, LinearSizedEntryV2)
    payload: dict[str, object] = {
        "scope": _scope_payload_v2(values["scope"]),
        "envelope": _envelope_payload_v2(values["envelope"]),
        "cost_reference": _computation_payload_v2(values["cost_reference"]),
        "assessed_at": _time_text_v2(values["assessed_at"]),
    }
    for name in ("policy_sha256", "cost_allowance_sha256"):
        payload[name] = _digest_v2(values[name])
    payload["stop_price"] = _decimal_text_field_v2(values, "stop_price", source=True)
    for name in ("stop_distance_quote_per_base", "unit_loss_quote", "estimated_loss_quote"):
        payload[name] = _decimal_text_field_v2(values, name, source=False)
    for name in ("unit_cost_quote", "cost_allowance_quote", "funding_allowance_quote"):
        payload[name] = _decimal_text_field_v2(values, name, source=False, sign="nonnegative")
    payload["formula_version"] = _literal_v2(
        values["formula_version"], ("LINEAR_FIXED_LIMIT_LOSS_SIZE_V2",)
    )
    envelope = cast(FixedLimitEnvelopeV2, values["envelope"])
    if payload["scope"] != _scope_payload_v2(envelope.scope):
        _reject_v2("RISK_V2_SCOPE_MISMATCH")
    if values["policy_sha256"] != envelope.policy_sha256:
        _reject_v2("RISK_V2_REVISION_MISMATCH")
    reference = cast(RiskComputationRefV2, values["cost_reference"])
    _target_environment_v2(reference.target_environment, values["scope"])
    assessed = _utc_v2(values["assessed_at"])
    if assessed < _utc_v2(reference.computed_at) or assessed >= _utc_v2(envelope.valid_until):
        _reject_v2("RISK_V2_CAUSAL_ORDER_INVALID")
    quantity = decimal_fraction_v2(envelope.base_quantity)
    distance = _distance_v2(envelope.side, envelope.limit_price, values["stop_price"])
    unit_cost = decimal_fraction_v2(values["unit_cost_quote"])
    unit_loss = decimal_fraction_v2(values["unit_loss_quote"])
    cost = decimal_fraction_v2(values["cost_allowance_quote"])
    if (
        decimal_fraction_v2(values["stop_distance_quote_per_base"]) != distance
        or unit_loss != distance + unit_cost
        or decimal_fraction_v2(values["estimated_loss_quote"]) != quantity * unit_loss
        or cost != quantity * unit_cost
        or decimal_fraction_v2(values["funding_allowance_quote"]) > cost
    ):
        _reject_v2()
    return payload


def _account_payload_v2(value: object) -> dict[str, str]:
    values = _fields_v2(value, ProtectionScope)
    identifier = values["account_id"]
    if (
        type(identifier) is not str
        or not 1 <= len(identifier) <= 256
        or identifier != identifier.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in identifier)
    ):
        _reject_v2()
    return {
        "account_id": identifier,
        "environment": _literal_v2(values["environment"], ("DEMO", "LIVE")),
    }


def _count_v2(value: object) -> int:
    if type(value) is not int or value < 0:
        _reject_v2()
    if value.bit_length() > 1_000_000:
        _reject_v2("RISK_V2_RESOURCE_LIMIT")
    return value


def _tuple_v2(value: object, *, minimum: int, maximum: int) -> tuple[Any, ...]:
    if type(value) is not tuple:
        _reject_v2()
    if len(value) > maximum:
        _reject_v2("RISK_V2_RESOURCE_LIMIT")
    if len(value) < minimum:
        _reject_v2()
    return value


def _key_order_v2(key: InstrumentKey) -> tuple[str, str, str]:
    # Callers have already fully validated every key before sorting or hashing.
    return key.venue, key.product, key.symbol


def _exposure_payload_v2(value: object) -> dict[str, object]:
    values = _fields_v2(value, ExposureQuantityV2)
    origin = _literal_v2(
        values["origin"], ("ACCOUNT_POSITION", "ACCOUNT_ENTRY_ORDER", "PENDING_ENTRY")
    )
    key = _key_payload_v2(values["instrument"])
    if key["product"] != "LINEAR":
        _reject_v2("RISK_V2_UNSUPPORTED_PRODUCT")
    return {
        "component_id": _identifier_v2(values["component_id"]),
        "instrument": key,
        "origin": origin,
        "side": _literal_v2(values["side"], ("BUY", "SELL")),
        "base_quantity": _decimal_text_field_v2(
            values, "base_quantity", source=origin != "PENDING_ENTRY"
        ),
    }


def _components_payload_v2(value: object) -> tuple[dict[str, object], ...]:
    components = _tuple_v2(value, minimum=0, maximum=1024)
    payload = tuple(_exposure_payload_v2(item) for item in components)
    ids = [item.component_id for item in components]
    if len(set(ids)) != len(ids):
        _reject_v2("RISK_V2_EXPOSURE_OVERLAP")
    order = [(*_key_order_v2(item.instrument), item.component_id) for item in components]
    if order != sorted(order):
        _reject_v2()
    return payload


def _mark_payload_v2(value: object) -> dict[str, object]:
    values = _fields_v2(value, LinearMarkQuoteV2)
    key = _key_payload_v2(values["instrument"])
    reference = _observation_payload_v2(values["reference"])
    if key["product"] != "LINEAR":
        _reject_v2("RISK_V2_UNSUPPORTED_PRODUCT")
    if reference["source_environment"] not in ("PUBLIC_MAINNET", "PUBLIC_TESTNET", "FIXTURE"):
        _reject_v2("RISK_V2_SOURCE_MISMATCH")
    return {
        "instrument": key,
        "reference": reference,
        "mark_price": _decimal_text_field_v2(values, "mark_price", source=True),
    }


def _marks_payload_v2(value: object) -> tuple[dict[str, object], ...]:
    marks = _tuple_v2(value, minimum=1, maximum=100)
    payload = tuple(_mark_payload_v2(item) for item in marks)
    order = [_key_order_v2(item.instrument) for item in marks]
    if order != sorted(order) or len(set(order)) != len(order):
        _reject_v2("RISK_V2_MARK_COVERAGE_REQUIRED")
    return payload


def _account_snapshot_payload_v2(value: object) -> dict[str, object]:
    values = _fields_v2(value, LinearAccountRiskSnapshotV2)
    account = _account_payload_v2(values["account"])
    reference = _observation_payload_v2(values["reference"])
    components = _components_payload_v2(values["components"])
    if reference["source_environment"] not in (account["environment"], "FIXTURE"):
        _reject_v2("RISK_V2_SOURCE_MISMATCH")
    if any(item.origin == "PENDING_ENTRY" for item in values["components"]):
        _reject_v2("RISK_V2_EXPOSURE_OVERLAP")
    if type(values["loss_day"]) is not date:
        _reject_v2()
    external = _tuple_v2(values["external_same_symbol_activity_ids"], minimum=0, maximum=1024)
    for identifier in external:
        _identifier_v2(identifier)
    if tuple(sorted(set(external))) != external:
        _reject_v2()
    payload: dict[str, object] = {
        "account": account,
        "reference": reference,
        "coverage_revision": _identifier_v2(values["coverage_revision"]),
        "components": components,
        "loss_accounting": _literal_v2(
            values["loss_accounting"], ("UTC_REALIZED_NET_FEES_FUNDING",)
        ),
        "loss_day": values["loss_day"].isoformat(),
        "external_same_symbol_activity_ids": external,
        "margin_mode": _literal_v2(
            values["margin_mode"], ("ISOLATED", "CROSS"), code="RISK_V2_UNSUPPORTED_MODE"
        ),
        "position_mode": _literal_v2(
            values["position_mode"], ("ONE_WAY",), code="RISK_V2_UNSUPPORTED_MODE"
        ),
    }
    for name in (
        "equity",
        "available_margin",
        "total_risk",
        "total_margin",
        "daily_loss",
        "drawdown",
    ):
        payload[name] = _decimal_text_field_v2(
            values, name, source=False, sign="positive" if name == "equity" else "nonnegative"
        )
    for name in ("position_count", "consecutive_losses"):
        payload[name] = _count_v2(values[name])
    return payload


def _reservation_snapshot_payload_v2(value: object) -> dict[str, object]:
    values = _fields_v2(value, LinearReservationSnapshotV2)
    payload: dict[str, object] = {
        "account": _account_payload_v2(values["account"]),
        "components": _components_payload_v2(values["components"]),
        "journal_revision": _count_v2(values["journal_revision"]),
        "held_quantity_sha256": _digest_v2(values["held_quantity_sha256"]),
        "observed_at": _time_text_v2(values["observed_at"]),
        "position_count": _count_v2(values["position_count"]),
    }
    if any(item.origin != "PENDING_ENTRY" for item in values["components"]):
        _reject_v2("RISK_V2_EXPOSURE_OVERLAP")
    for name in ("snapshot_id", "owner_generation"):
        payload[name] = _identifier_v2(values[name])
    payload["excluded_command_key"] = (
        None
        if values["excluded_command_key"] is None
        else _identifier_v2(values["excluded_command_key"])
    )
    for name in ("risk_amount", "margin_amount"):
        payload[name] = _decimal_text_field_v2(values, name, source=False, sign="nonnegative")
    return payload


def _valuation_payload_v2(value: object) -> dict[str, object]:
    values = _fields_v2(value, LinearAdmissionValuationV2)
    identifiers = _tuple_v2(values["mark_observation_ids"], minimum=1, maximum=100)
    for identifier in identifiers:
        _identifier_v2(identifier)
    payload: dict[str, object] = {
        "scope": _scope_payload_v2(values["scope"]),
        "assessed_at": _time_text_v2(values["assessed_at"]),
        "mark_observation_ids": identifiers,
        "valuation_rule": _literal_v2(
            values["valuation_rule"], ("LINEAR_MARK_GROSS_AT_ADMISSION_V1",)
        ),
    }
    for name in ("account_observation_id", "reservation_snapshot_id"):
        payload[name] = _identifier_v2(values[name])
    for name in (
        "account_gross_quote",
        "reserved_gross_quote",
        "proposed_mark_quote",
        "combined_gross_quote",
    ):
        payload[name] = _decimal_text_field_v2(
            values,
            name,
            source=False,
            sign="positive" if name == "proposed_mark_quote" else "nonnegative",
        )
    if decimal_fraction_v2(values["combined_gross_quote"]) != sum(
        (
            decimal_fraction_v2(values[name])
            for name in ("account_gross_quote", "reserved_gross_quote", "proposed_mark_quote")
        ),
        Fraction(0),
    ):
        _reject_v2()
    return payload


def _fresh_v2(observed: datetime, *, now: datetime, maximum: Fraction) -> None:
    observed = _utc_v2(observed)
    if observed > now:
        raise RiskV2Rejected("RISK_V2_FUTURE_EVIDENCE")
    age = now - observed
    microseconds = (age.days * 86400 + age.seconds) * 1_000_000 + age.microseconds
    if microseconds > maximum * 1_000_000:
        raise RiskV2Rejected("RISK_V2_STALE_EVIDENCE")


def _mark_values_v2(
    marks: tuple[LinearMarkQuoteV2, ...],
    *,
    required: tuple[InstrumentKey, ...],
    now: datetime,
    max_age_seconds: Decimal,
) -> dict[InstrumentKey, Decimal]:
    _marks_payload_v2(marks)
    _tuple_v2(required, minimum=1, maximum=100)
    for key in required:
        _key_payload_v2(key)
    now = _utc_v2(now)
    maximum = decimal_fraction_v2(_decimal_v2(max_age_seconds, source=True, sign="nonnegative"))
    order = [_key_order_v2(key) for key in required]
    if order != sorted(set(order)):
        _reject_v2()
    if tuple(mark.instrument for mark in marks) != required:
        _reject_v2("RISK_V2_MARK_COVERAGE_REQUIRED")
    for mark in marks:
        _fresh_v2(mark.reference.server_observed_at, now=now, maximum=maximum)
        _fresh_v2(mark.reference.received_at, now=now, maximum=maximum)
    return {mark.instrument: mark.mark_price for mark in marks}


def _gross_components_v2(
    components: tuple[ExposureQuantityV2, ...], *, prices: dict[InstrumentKey, Decimal]
) -> Decimal:
    _components_payload_v2(components)
    if type(prices) is not dict:
        _reject_v2()
    if len(prices) > 100:
        _reject_v2("RISK_V2_RESOURCE_LIMIT")
    for key, price in prices.items():
        payload = _key_payload_v2(key)
        if payload["product"] != "LINEAR":
            _reject_v2("RISK_V2_UNSUPPORTED_PRODUCT")
        _decimal_v2(price, source=True, sign="positive")
    if any(item.instrument not in prices for item in components):
        _reject_v2("RISK_V2_MARK_COVERAGE_REQUIRED")
    return finite_decimal_v2(
        sum(
            (
                decimal_fraction_v2(item.base_quantity)
                * decimal_fraction_v2(prices[item.instrument])
                for item in components
            ),
            Fraction(0),
        )
    )


def _json_size_v2(value: object) -> int:
    """Bound only the closed, already validated scalar tree before encoding."""
    if value is None:
        return 4
    if type(value) is bool:
        return 4 if value else 5
    if type(value) is str:
        size = 2
        for char in value:
            code = ord(char)
            size += (
                2
                if char in '\\"\b\f\n\r\t'
                else 6
                if code < 32 or 127 < code <= 65535
                else 12
                if code > 65535
                else 1
            )
            if size > _MAX_CANONICAL_BYTES_V2:
                _reject_v2("RISK_V2_RESOURCE_LIMIT")
        return size
    if type(value) is int:
        # Exact integer digit counting without Python's process-wide str limit.
        number, size = abs(value), int(value < 0)
        while number:
            number //= 10
            size += 1
            if size > _MAX_CANONICAL_BYTES_V2:
                _reject_v2("RISK_V2_RESOURCE_LIMIT")
        return max(size, 1)
    if type(value) is tuple:
        size = 2
        for index, item in enumerate(value):
            size += int(index > 0) + _json_size_v2(item)
            if size > _MAX_CANONICAL_BYTES_V2:
                _reject_v2("RISK_V2_RESOURCE_LIMIT")
        return size
    if type(value) is dict:
        mapping = cast(dict[str, object], value)
        size = 2
        for index, (name, item) in enumerate(mapping.items()):
            size += int(index > 0) + _json_size_v2(name) + 1 + _json_size_v2(item)
            if size > _MAX_CANONICAL_BYTES_V2:
                _reject_v2("RISK_V2_RESOURCE_LIMIT")
        return size
    # This internal helper has no arbitrary-record or iterable dispatch.
    _reject_v2()


def _canonical_sha256_v2(record_type: str, payload: dict[str, object]) -> str:
    value = {"schema": "RISK_V2_CANONICAL_V1", "record_type": record_type, "fields": payload}
    _json_size_v2(value)
    try:
        text = _json_text_v2(value)
        encoded = text.encode("ascii")
    except (ValueError, OverflowError, UnicodeError):
        _reject_v2("RISK_V2_RESOURCE_LIMIT")
    if len(encoded) > _MAX_CANONICAL_BYTES_V2:
        _reject_v2("RISK_V2_RESOURCE_LIMIT")
    return hashlib.sha256(encoded).hexdigest()


def _json_text_v2(value: object) -> str:
    """Encode only our validated scalar tree; never inspect arbitrary records."""
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is str:
        return json.dumps(value, ensure_ascii=True)
    if type(value) is int:
        number = abs(value)
        digits: list[str] = []
        while number:
            number, digit = divmod(number, 10)
            digits.append(chr(48 + digit))
        return ("-" if value < 0 else "") + ("".join(reversed(digits)) if digits else "0")
    if type(value) is tuple:
        return "[" + ",".join(_json_text_v2(item) for item in value) + "]"
    if type(value) is dict:
        mapping = cast(dict[str, object], value)
        return (
            "{"
            + ",".join(
                _json_text_v2(name) + ":" + _json_text_v2(mapping[name]) for name in sorted(mapping)
            )
            + "}"
        )
    _reject_v2()
