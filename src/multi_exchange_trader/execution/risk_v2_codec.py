"""Pure durable Risk V2 history; no current admission authority."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import Any, Literal, NoReturn, cast

from multi_exchange_trader.execution import linear_risk_evidence_v2 as e
from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.modes import StrategyReference
from multi_exchange_trader.execution.product_scope import ProductScope
from multi_exchange_trader.execution.protection import ProtectionPolicy, ProtectionScope
from multi_exchange_trader.execution.risk_v2_math import decimal_fraction_v2, decimal_text_v2
from multi_exchange_trader.trading_product import InstrumentKey


@dataclass(frozen=True)
class DurableEntryIntentV2:
    schema_version: Literal[2]
    command_key: str
    position_id: str
    plan_id: str
    strategy: StrategyReference
    envelope: c.FixedLimitEnvelopeV2
    assessment: e.LinearEntryAssessmentV2
    protection_policy: ProtectionPolicy
    source_record_sha256s: tuple[str, ...]

    def __post_init__(self) -> None:
        _validated_fields(self)


# These contextual recipes are a closed description of this one wire format.
# They do not inspect caller classes or resolve names from the payload.
_RECIPES: dict[str, tuple[type[object], tuple[tuple[str, str], ...]]] = {
    "intent": (
        DurableEntryIntentV2,
        (
            ("schema_version", "version"),
            ("command_key", "id"),
            ("position_id", "id"),
            ("plan_id", "id"),
            ("strategy", "strategy"),
            ("envelope", "envelope"),
            ("assessment", "assessment"),
            ("protection_policy", "protection"),
            ("source_record_sha256s", "hashes"),
        ),
    ),
    "strategy": (StrategyReference, (("strategy_id", "id"), ("revision", "id"))),
    "account": (ProtectionScope, (("account_id", "account_text"), ("environment", "text"))),
    "product": (ProductScope, (("account", "account"), ("product", "text"))),
    "key": (InstrumentKey, (("venue", "text"), ("product", "text"), ("symbol", "text"))),
    "scope": (
        c.RiskScopeV2,
        (("product_scope", "product"), ("instrument", "key"), ("strategy", "strategy")),
    ),
    "pin": (
        c.InstrumentPinV2,
        (
            ("key", "key"),
            ("native_instrument_id", "text"),
            ("catalog_revision", "id"),
            ("specification_revision", "hash"),
            ("parser_revision", "id"),
            ("source_rule_revision", "id"),
            ("source_id", "id"),
            ("source_environment", "text"),
            ("response_time", "time"),
            ("received_at", "time"),
            ("raw_row_sha256", "hash"),
        ),
    ),
    "envelope": (
        c.FixedLimitEnvelopeV2,
        (
            ("schema_version", "version"),
            ("scope", "scope"),
            ("instrument_pin", "pin"),
            ("policy_revision", "id"),
            ("policy_sha256", "hash"),
            ("side", "text"),
            ("base_quantity", "decimal"),
            ("limit_price", "source"),
            ("order_type", "text"),
            ("time_in_force", "text"),
            ("role", "text"),
            ("reduce_only", "false"),
            ("origin_quote_id", "id"),
            ("valid_until", "time"),
        ),
    ),
    "computation": (
        c.RiskComputationRefV2,
        (
            ("calculation_id", "id"),
            ("model_revision", "id"),
            ("target_environment", "text"),
            ("computed_at", "time"),
            ("input_sha256", "hash"),
        ),
    ),
    "observation": (
        c.RiskObservationRefV2,
        (
            ("observation_id", "id"),
            ("source_id", "id"),
            ("source_environment", "text"),
            ("source_rule_revision", "id"),
            ("server_observed_at", "time"),
            ("received_at", "time"),
            ("payload_sha256", "hash"),
        ),
    ),
    "sizing": (
        c.LinearSizedEntryV2,
        (
            ("scope", "scope"),
            ("assessed_at", "time"),
            ("policy_sha256", "hash"),
            ("envelope", "envelope"),
            ("cost_reference", "computation"),
            ("cost_allowance_sha256", "hash"),
            ("stop_price", "source"),
            ("stop_distance_quote_per_base", "decimal"),
            ("unit_cost_quote", "decimal"),
            ("unit_loss_quote", "decimal"),
            ("estimated_loss_quote", "decimal"),
            ("cost_allowance_quote", "decimal"),
            ("funding_allowance_quote", "decimal"),
            ("formula_version", "text"),
        ),
    ),
    "valuation": (
        c.LinearAdmissionValuationV2,
        (
            ("scope", "scope"),
            ("assessed_at", "time"),
            ("account_observation_id", "id"),
            ("reservation_snapshot_id", "id"),
            ("mark_observation_ids", "marks"),
            ("account_gross_quote", "decimal"),
            ("reserved_gross_quote", "decimal"),
            ("proposed_mark_quote", "decimal"),
            ("combined_gross_quote", "decimal"),
            ("valuation_rule", "text"),
        ),
    ),
    "tier": (
        e.LinearTierBoundV2,
        (
            ("instrument", "key"),
            ("tier_observation_id", "id"),
            ("mark_observation_id", "id"),
            ("symbol_gross_upper_quote", "decimal"),
            ("covered_tier_ids", "ids"),
            ("continuous_leverage_lower", "decimal"),
            ("continuous_leverage_upper", "decimal"),
            ("leverage_step", "source"),
            ("leverage_grid_rule", "text"),
            ("permitted_leverage_min", "decimal"),
            ("permitted_leverage_max", "decimal"),
            ("bound_rule", "text"),
        ),
    ),
    "minimum": (
        e.MinimumAmountAssessmentV2,
        (
            ("scope", "scope"),
            ("reference", "computation"),
            ("envelope_sha256", "hash"),
            ("instrument_revision", "hash"),
            ("rule_revision", "id"),
            ("input_observation_ids", "ids"),
            ("evaluation_price", "source"),
            ("minimum_quote_amount", "source"),
            ("evaluated_quote_amount", "decimal"),
            ("valid_until", "time"),
        ),
    ),
    "binding": (
        e.LinearRiskBindingV2,
        (
            ("scope", "scope"),
            ("policy_sha256", "hash"),
            ("envelope_sha256", "hash"),
            ("sizing_sha256", "hash"),
            ("instrument_pin", "pin"),
            ("source_mapping_revision", "id"),
            ("cost_calculation_id", "id"),
            ("cost_allowance_sha256", "hash"),
            ("account_observation_id", "id"),
            ("account_snapshot_sha256", "hash"),
            ("account_coverage_revision", "id"),
            ("reservation_snapshot_id", "id"),
            ("reservation_snapshot_sha256", "hash"),
            ("reservation_owner_generation", "id"),
            ("mark_set_sha256", "hash"),
            ("valuation_sha256", "hash"),
            ("tier_observation_id", "id"),
            ("tier_snapshot_sha256", "hash"),
            ("tier_bound_sha256", "hash"),
            ("risk_domain_sha256", "hash"),
            ("minimum_assessment_sha256", "hash"),
            ("requirements_sha256", "hash"),
        ),
    ),
    "preparation": (
        e.LinearEntryPreparationV2,
        (
            ("sizing", "sizing"),
            ("valuation", "valuation"),
            ("tier_bound", "tier"),
            ("minimum_amount", "minimum"),
            ("binding", "binding"),
            ("capacity_quote", "decimal"),
            ("estimated_loss_fraction_display", "decimal"),
            ("display_ratio_rule", "text"),
        ),
    ),
    "ratio": (e.ExactRiskRatioV2, (("numerator", "decimal"), ("denominator", "decimal"))),
    "positive": (
        e.LiquidationPositiveRootV2,
        (
            ("kind", "text"),
            ("exact_price", "ratio"),
            ("candidate_tier_id", "id"),
            ("domain_upper_quote", "decimal"),
            ("full_table_sha256", "hash"),
            ("raw_manifest_sha256", "hash"),
            ("consumed_tier_ids", "ids"),
            ("rule_revision", "id"),
        ),
    ),
    "no_root": (
        e.LiquidationNoPositiveRootV2,
        (
            ("kind", "text"),
            ("reason", "text"),
            ("domain_upper_quote", "decimal"),
            ("full_table_sha256", "hash"),
            ("raw_manifest_sha256", "hash"),
            ("consumed_tier_ids", "ids"),
            ("rule_revision", "id"),
            ("model_input_sha256", "hash"),
            ("proof_sha256", "hash"),
        ),
    ),
    "margin": (
        e.LinearMarginAssessmentV2,
        (
            ("binding_sha256", "hash"),
            ("reference", "computation"),
            ("model_revision", "id"),
            ("quantity", "decimal"),
            ("leverage", "decimal"),
            ("leverage_grid_rule", "text"),
            ("selection_rule", "text"),
            ("initial_margin_quote", "decimal"),
            ("venue_required_total_reserve_quote", "decimal"),
            ("covered_tier_ids", "ids"),
            ("valid_until", "time"),
            ("reserve_basis", "text"),
            ("reserve_rounding_revision", "text"),
            ("reserve_quantum_usdt", "source"),
            ("model_input_sha256", "hash"),
            ("exact_calculation_sha256", "hash"),
        ),
    ),
    "liquidation": (
        e.LinearLiquidationAssessmentV2,
        (
            ("binding_sha256", "hash"),
            ("margin_assessment_sha256", "hash"),
            ("reference", "computation"),
            ("model_revision", "id"),
            ("quantity", "decimal"),
            ("leverage", "decimal"),
            ("side", "text"),
            ("entry_limit_price", "source"),
            ("margin_mode", "text"),
            ("position_mode", "text"),
            ("covered_tier_ids", "ids"),
            ("outcome", "outcome"),
            ("valid_until", "time"),
            ("model_input_sha256", "hash"),
            ("exact_calculation_sha256", "hash"),
        ),
    ),
    "readback": (
        e.LinearLeverageReadbackV2,
        (
            ("binding_sha256", "hash"),
            ("reference", "observation"),
            ("account_observation_id", "id"),
            ("control_command_key", "id"),
            ("after_control_at", "time"),
            ("side", "text"),
            ("applied_leverage", "source"),
            ("margin_mode", "text"),
            ("position_mode", "text"),
        ),
    ),
    "assessment": (
        e.LinearEntryAssessmentV2,
        (
            ("stage", "text"),
            ("assessed_at", "time"),
            ("preparation", "preparation"),
            ("margin", "?margin"),
            ("liquidation", "?liquidation"),
            ("leverage_readback", "?readback"),
            ("required_reservation_quote", "?decimal"),
            ("blockers", "blockers"),
            ("formula_version", "text"),
            ("liquidation_buffer_result", "?text"),
        ),
    ),
    "protection": (
        ProtectionPolicy,
        (
            ("revision", "id"),
            ("generation", "generation"),
            ("stop_price", "source"),
            ("take_profit_price", "source"),
            ("trigger_source", "id"),
        ),
    ),
}
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:E[+-][0-9]+)?", re.ASCII)
_TIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}\+00:00", re.ASCII
)
_MAX = 65536


def _reject(resource: bool = False) -> NoReturn:
    c._reject_v2("RISK_V2_RESOURCE_LIMIT" if resource else "RISK_V2_INPUT_INVALID")


def _digits(number: int) -> int:
    result = 1
    while number >= 10:
        result += 1
        number //= 10
    return result


def _string_size(value: str) -> int:
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
        if size > _MAX:
            _reject(True)
    return size


def _decimal_bounds(digits: int, exponent: int, source: bool) -> None:
    if digits > _MAX or abs(exponent) > _MAX:
        _reject(True)
    if source and (digits > 256 or abs(exponent) > 1000 or abs(exponent + digits - 1) > 1000):
        _reject(True)


def _decimal_size(value: object, source: bool, wire: bool) -> int:
    if wire:
        if type(value) is not str or _DECIMAL.fullmatch(value) is None:
            _reject()
        mantissa, marker, exp_text = value.partition("E")
        exponent = 0
        if marker:
            if len(exp_text) > 7:
                _reject(True)
            exponent = int(exp_text)
        coefficient = mantissa.lstrip("-").replace(".", "")
        digits = len(coefficient.lstrip("0")) or 1
        if "." in mantissa:
            exponent -= len(mantissa) - mantissa.index(".") - 1
        _decimal_bounds(digits, exponent, source)
        return _string_size(value)
    if type(value) is not Decimal or not value.is_finite():
        _reject()
    parts = value.as_tuple()
    exponent = cast(int, parts.exponent)
    digits = len(parts.digits)
    _decimal_bounds(digits, exponent, source)
    adjusted, point = exponent + digits - 1, digits + exponent
    if exponent <= 0 and adjusted >= -6:
        size = 2 - point + digits if point <= 0 else digits + int(point < digits)
    else:
        size = digits + int(digits > 1) + 2 + _digits(abs(adjusted))
    return size + parts.sign + 2


def _kind(kind: str, value: object, wire: bool) -> str:
    if kind.startswith("?"):
        return "null" if value is None else kind[1:]
    if kind != "outcome":
        return kind
    if wire:
        if type(value) is not dict:
            _reject()
        branch = value.get("kind")
        if type(branch) is not str:
            _reject()
        if branch == "POSITIVE_ROOT":
            return "positive"
        if branch == "NO_POSITIVE_ROOT":
            return "no_root"
    elif type(value) is e.LiquidationPositiveRootV2:
        return "positive"
    elif type(value) is e.LiquidationNoPositiveRootV2:
        return "no_root"
    _reject()


def _preflight(
    value: object,
    kind: str,
    *,
    wire: bool,
    depth: int = 2,
    active: set[int] | None = None,
    budget: list[int] | None = None,
) -> None:
    """Walk every occurrence before semantic helpers or transformed trees exist."""
    if active is None:
        active = set()
    if budget is None:
        # Exact outer framing, including its checksum; the root body follows.
        budget = [183]
    if depth > 32:
        _reject(True)
    kind = _kind(kind, value, wire)
    if kind in _RECIPES:
        expected, recipe = _RECIPES[kind]
        if (wire and type(value) is not dict) or (not wire and type(value) is not expected):
            _reject()
        if wire and set(cast(dict[str, object], value)) != {name for name, _ in recipe}:
            _reject()
        if id(value) in active:
            _reject()
        active.add(id(value))
        budget[0] += 2 + max(0, len(recipe) - 1)
        try:
            for name, child_kind in recipe:
                budget[0] += len(name) + 3
                try:
                    child = value[name] if wire else object.__getattribute__(value, name)  # type: ignore[index]
                except AttributeError:
                    _reject()
                _preflight(
                    child, child_kind, wire=wire, depth=depth + 1, active=active, budget=budget
                )
        finally:
            active.remove(id(value))
    elif kind in ("ids", "marks", "hashes", "blockers"):
        if type(value) is not (list if wire else tuple):
            _reject()
        sequence = cast(list[object] | tuple[object, ...], value)
        minimum, maximum = (0, 3) if kind == "blockers" else (1, 100 if kind == "marks" else 1024)
        if len(sequence) > maximum:
            _reject(True)
        if len(sequence) < minimum:
            _reject()
        budget[0] += 2 + max(0, len(sequence) - 1)
        for item in sequence:
            _preflight(
                item,
                "hash" if kind == "hashes" else "id",
                wire=wire,
                depth=depth + 1,
                active=active,
                budget=budget,
            )
    elif kind in ("decimal", "source"):
        budget[0] += _decimal_size(value, kind == "source", wire)
    elif kind == "time":
        if wire:
            if type(value) is not str or _TIME.fullmatch(value) is None:
                _reject()
        elif (
            type(value) is not datetime
            or type(object.__getattribute__(value, "tzinfo")) is not timezone
        ):
            _reject()
        budget[0] += 34
    elif kind in ("version", "generation"):
        if type(value) is not int or value < 0:
            _reject()
        if kind == "version" and value != 2:
            _reject()
        # Keep the existing exact/nonnegative count owner, then add the new
        # boundary's adopted 63-bit representation ceiling.
        if kind == "generation" and c._count_v2(value).bit_length() > 63:
            _reject(True)
        budget[0] += _digits(value)
    elif kind == "false":
        if type(value) is not bool or value is not False:
            _reject()
        budget[0] += 5
    elif kind == "null":
        budget[0] += 4
    else:
        if type(value) is not str:
            _reject()
        if kind == "hash":
            if _DIGEST.fullmatch(value) is None:
                _reject()
        elif kind == "id":
            if _IDENTIFIER.fullmatch(value) is None:
                _reject()
        elif kind == "account_text":
            if (
                not 1 <= len(value) <= 256
                or value != value.strip()
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
            ):
                _reject()
        elif kind == "text":
            if not 1 <= len(value) <= 96:
                _reject()
        else:
            _reject()
        budget[0] += _string_size(value)
    if budget[0] > _MAX:
        _reject(True)


def _validated_fields(value: object) -> dict[str, object]:
    _preflight(value, "intent", wire=False)
    intent = cast(DurableEntryIntentV2, value)
    # Only the now completely trusted closed graph reaches accepted validators.
    envelope = c._envelope_payload_v2(intent.envelope)
    assessment = e._assessment_payload_v2(intent.assessment)
    policy = intent.protection_policy
    c._count_v2(policy.generation)
    protection: dict[str, object] = {
        "revision": c._identifier_v2(policy.revision),
        "generation": policy.generation,
        "stop_price": decimal_text_v2(
            c._decimal_v2(policy.stop_price, source=True, sign="positive")
        ),
        "take_profit_price": decimal_text_v2(
            c._decimal_v2(policy.take_profit_price, source=True, sign="positive")
        ),
        "trigger_source": c._identifier_v2(policy.trigger_source),
    }
    strategy = {
        "strategy_id": c._identifier_v2(intent.strategy.strategy_id),
        "revision": c._identifier_v2(intent.strategy.revision),
    }
    preparation = intent.assessment.preparation
    if (
        strategy != c._scope_payload_v2(intent.envelope.scope)["strategy"]
        or strategy != c._scope_payload_v2(preparation.binding.scope)["strategy"]
        or envelope != c._envelope_payload_v2(preparation.sizing.envelope)
    ):
        c._reject_v2("RISK_V2_SCOPE_MISMATCH")
    if policy.revision != intent.envelope.policy_revision:
        c._reject_v2("RISK_V2_REVISION_MISMATCH")
    stop, entry, target = (
        decimal_fraction_v2(number)
        for number in (policy.stop_price, intent.envelope.limit_price, policy.take_profit_price)
    )
    if protection["stop_price"] != decimal_text_v2(preparation.sizing.stop_price) or not (
        stop < entry < target if intent.envelope.side == "BUY" else target < entry < stop
    ):
        c._reject_v2("RISK_V2_INVALID_STOP")
    refs = intent.source_record_sha256s
    if any(left >= right for left, right in pairwise(refs)):
        _reject()
    result: dict[str, object] = {
        "schema_version": 2,
        "command_key": c._identifier_v2(intent.command_key),
        "position_id": c._identifier_v2(intent.position_id),
        "plan_id": c._identifier_v2(intent.plan_id),
        "strategy": strategy,
        "envelope": envelope,
        "assessment": assessment,
        "protection_policy": protection,
        "source_record_sha256s": refs,
    }
    return result


def _text(tree: dict[str, object]) -> str:
    c._json_size_v2(tree)
    return c._json_text_v2(tree)


def encode_entry_intent_v2(value: DurableEntryIntentV2) -> str:
    fields = _validated_fields(value)
    frame: dict[str, object] = {
        "schema": "RISK_V2_DURABLE_INTENT",
        "wire_version": 2,
        "record_type": "DurableEntryIntentV2",
        "fields": fields,
    }
    frame["content_sha256"] = hashlib.sha256(_text(frame).encode("ascii")).hexdigest()
    return _text(frame)


def _scan(payload: str) -> None:
    depth, quoted, escaped, number = 0, False, False, 0
    negative = False
    for char in payload:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 32:
                _reject(True)
        elif char in "]}":
            depth -= 1
        if char in "0123456789-eE+.":
            if number == 0:
                negative = char == "-"
            number += 1
            if number > 19 + int(negative):
                _reject(True)
        else:
            number = 0
            negative = False


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            _reject()
        result[name] = value
    return result


def _integer(token: str) -> int:
    if len(token) - int(token.startswith("-")) > 19:
        _reject(True)
    return int(token)


def _no_number(token: str) -> NoReturn:
    _reject()


def _reconstruct(value: Any, kind: str, *, construct: bool) -> Any:
    """Fixed contextual reconstruction after the entire raw graph preflight."""
    kind = _kind(kind, value, True)
    if kind in _RECIPES:
        _, recipe = _RECIPES[kind]
        children = {
            name: _reconstruct(value[name], child_kind, construct=construct)
            for name, child_kind in recipe
        }
        if not construct:
            return children
        # Direct trusted constructors only; no payload-selected class or import.
        if kind == "intent":
            return DurableEntryIntentV2(**children)
        if kind == "strategy":
            return StrategyReference(**children)
        if kind == "account":
            try:
                return ProtectionScope(**children)
            except c.RiskV2Rejected:
                raise
            except ValueError:
                _reject()
        if kind == "product":
            try:
                return ProductScope(**children)
            except ValueError:
                _reject()
        if kind == "key":
            try:
                return InstrumentKey(**children)
            except ValueError:
                _reject()
        if kind == "scope":
            return c.RiskScopeV2(**children)
        if kind == "pin":
            return c.InstrumentPinV2(**children)
        if kind == "envelope":
            return c.FixedLimitEnvelopeV2(**children)
        if kind == "computation":
            return c.RiskComputationRefV2(**children)
        if kind == "observation":
            return c.RiskObservationRefV2(**children)
        if kind == "sizing":
            return c.LinearSizedEntryV2(**children)
        if kind == "valuation":
            return c.LinearAdmissionValuationV2(**children)
        if kind == "tier":
            return e.LinearTierBoundV2(**children)
        if kind == "minimum":
            return e.MinimumAmountAssessmentV2(**children)
        if kind == "binding":
            return e.LinearRiskBindingV2(**children)
        if kind == "preparation":
            return e.LinearEntryPreparationV2(**children)
        if kind == "ratio":
            return e.ExactRiskRatioV2(**children)
        if kind == "positive":
            return e.LiquidationPositiveRootV2(**children)
        if kind == "no_root":
            return e.LiquidationNoPositiveRootV2(**children)
        if kind == "margin":
            return e.LinearMarginAssessmentV2(**children)
        if kind == "liquidation":
            return e.LinearLiquidationAssessmentV2(**children)
        if kind == "readback":
            return e.LinearLeverageReadbackV2(**children)
        if kind == "assessment":
            return e.LinearEntryAssessmentV2(**children)
        if kind == "protection":
            try:
                return ProtectionPolicy(**children)
            except c.RiskV2Rejected:
                raise
            except ValueError:
                _reject()
        _reject()
    if kind in ("ids", "marks", "hashes", "blockers"):
        return tuple(value)
    if not construct:
        return value
    if kind in ("decimal", "source"):
        try:
            decimal = Decimal(value)
        except InvalidOperation:
            _reject()
        if decimal_text_v2(decimal) != value:
            _reject()
        return decimal
    if kind == "time":
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            _reject()
        if c._time_text_v2(moment) != value:
            _reject()
        return moment
    return value


def decode_entry_intent_v2(payload: str) -> DurableEntryIntentV2:
    if type(payload) is not str:
        _reject()
    if len(payload) > _MAX:
        _reject(True)
    try:
        encoded = payload.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        _reject()
    if len(encoded) > _MAX:
        _reject(True)
    if not encoded:
        _reject()
    _scan(payload)
    try:
        frame = json.loads(
            payload,
            object_pairs_hook=_pairs,
            parse_int=_integer,
            parse_float=_no_number,
            parse_constant=_no_number,
        )
    except json.JSONDecodeError:
        _reject()
    if type(frame) is not dict or set(frame) != {
        "schema",
        "wire_version",
        "record_type",
        "fields",
        "content_sha256",
    }:
        _reject()
    if (
        type(frame["schema"]) is not str
        or frame["schema"] != "RISK_V2_DURABLE_INTENT"
        or type(frame["record_type"]) is not str
        or frame["record_type"] != "DurableEntryIntentV2"
        or type(frame["wire_version"]) is not int
        or frame["wire_version"] != 2
    ):
        _reject()
    if (
        type(frame["content_sha256"]) is not str
        or _DIGEST.fullmatch(frame["content_sha256"]) is None
    ):
        _reject()
    _preflight(frame["fields"], "intent", wire=True)
    tree = _reconstruct(frame["fields"], "intent", construct=False)
    unsigned: dict[str, object] = {
        "schema": frame["schema"],
        "wire_version": 2,
        "record_type": frame["record_type"],
        "fields": tree,
    }
    if hashlib.sha256(_text(unsigned).encode("ascii")).hexdigest() != frame["content_sha256"]:
        _reject()
    restored = cast(DurableEntryIntentV2, _reconstruct(frame["fields"], "intent", construct=True))
    if encode_entry_intent_v2(restored) != payload:
        _reject()
    return restored
