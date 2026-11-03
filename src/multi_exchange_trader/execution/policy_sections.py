"""Closed static draft codec. Metadata consistency never grants execution authority."""

import hashlib
import json
import re
from decimal import Decimal
from typing import Any, cast

from multi_exchange_trader.trading_product import InstrumentKey

from .modes import StrategyReference
from .product_scope import ProductScope
from .protection import ProtectionScope
from .strategy_catalog import StrategyCatalog, StrategyDescriptor, build_strategy_catalog

# BEGIN PORTABLE POLICY SECTIONS -- identical stdlib-only counterpart in state_snapshot.
SECTION_LIMIT = 64 * 1024
SCOPE_FIELDS = {"section_version", "account", "mode", "allowed_products", "selected_product",
                "permission_profile_revision", "allowed_instruments", "selected_instruments"}
STRATEGY_FIELDS = {"section_version", "catalog", "allowed", "selected"}
RISK_DECIMALS = {"leverage_min", "leverage_max", "per_trade_loss", "max_daily_loss", "max_drawdown",
                 "max_total_risk", "max_total_margin", "margin_allocation", "liquidation_buffer_fraction",
                 "max_quote_age_seconds", "max_evidence_age_seconds"}
RISK_ZERO = {"liquidation_buffer_fraction", "max_quote_age_seconds", "max_evidence_age_seconds"}
RISK_COUNTS = {"max_consecutive_losses", "max_positions"}
RISK_CHOICES = {"sizing_rule": {"LOSS_FIXED"}, "margin_mode": {"ISOLATED", "CROSS"},
                "position_mode": {"ONE_WAY"}}
CAP_FIELDS = {"max_order_admission_notional", "max_account_admission_notional", "valuation_rule",
              "turnover_requirement", "max_execution_quote_amount"}
DESCRIPTOR_FIELDS = {"reference", "implementation_sha256", "config_sha256", "config_schema_version",
                     "engine_contract_version", "permitted_instruments", "approval_reference",
                     "approval_source_reference", "approval_source_sha256", "approval_environments", "approval_status"}


def _invalid() -> None:
    raise ValueError("POLICY_SECTIONS_INVALID")


def _object(value: Any, keys: set[str]) -> None:
    if type(value) is not dict or set(value) != keys:
        _invalid()


def _choice(value: Any, choices: set[str]) -> None:
    if type(value) is not str or value not in choices:
        _invalid()


def _identifier(value: Any) -> None:
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}", value) is None:
        _invalid()


def _array(value: Any, maximum: int = 100) -> None:
    if type(value) is not list or len(value) > maximum:
        _invalid()
    tokens = [json.dumps(item, sort_keys=True, ensure_ascii=True, allow_nan=False) for item in value]
    if len(set(tokens)) != len(tokens):
        _invalid()


def _key_wire(value: Any) -> tuple[str, str, str]:
    _object(value, {"venue", "product", "symbol"})
    _choice(value["venue"], {"BYBIT"})
    _choice(value["product"], {"SPOT", "LINEAR"})
    if type(value["symbol"]) is not str or re.fullmatch(r"[A-Z0-9]{2,48}", value["symbol"]) is None:
        _invalid()
    return value["venue"], value["product"], value["symbol"]


def _reference_wire(value: Any) -> tuple[str, str]:
    _object(value, {"strategy_id", "revision"})
    _identifier(value["strategy_id"])
    _identifier(value["revision"])
    if value["revision"].casefold() == "latest":
        _invalid()
    return value["strategy_id"], value["revision"]


def _decimal_wire(value: Any, *, zero: bool = False) -> None:
    if (type(value) is not str or re.fullmatch(r"(?:0|[1-9][0-9]{0,15})(?:\.[0-9]{1,8})?", value) is None
            or Decimal(value) < 0 or not zero and Decimal(value) == 0):
        _invalid()


def _catalog_wire(value: Any) -> dict[tuple[str, str], Any]:
    _object(value, {"schema_version", "revision", "entries"})
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        _invalid()
    if type(value["revision"]) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value["revision"]) is None:
        _invalid()
    _array(value["entries"])
    lookup: dict[tuple[str, str], Any] = {}
    canonical = []
    for entry in value["entries"]:
        _object(entry, DESCRIPTOR_FIELDS)
        reference = _reference_wire(entry["reference"])
        if reference in lookup:
            _invalid()
        lookup[reference] = entry
        for name in ("implementation_sha256", "config_sha256", "approval_source_sha256"):
            if type(entry[name]) is not str or re.fullmatch(r"[0-9a-f]{64}", entry[name]) is None:
                _invalid()
        if type(entry["config_schema_version"]) is not int or not 1 <= entry["config_schema_version"] <= 2147483647:
            _invalid()
        for name in ("engine_contract_version", "approval_reference", "approval_source_reference"):
            _identifier(entry[name])
        _array(entry["permitted_instruments"])
        for key in entry["permitted_instruments"]:
            _key_wire(key)
        _array(entry["approval_environments"], 2)
        for environment in entry["approval_environments"]:
            _choice(environment, {"DEMO", "LIVE"})
        _choice(entry["approval_status"], {"APPROVED", "DEPRECATED", "REVOKED"})
        if not entry["permitted_instruments"] or not entry["approval_environments"]:
            _invalid()
        canonical.append({**entry, "permitted_instruments": sorted(entry["permitted_instruments"], key=_key_wire),
                          "approval_environments": sorted(entry["approval_environments"])})
    payload = {"schema_version": 1, "entries": sorted(canonical, key=lambda entry: _reference_wire(entry["reference"]))}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True,
                                      separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    if value["revision"] != "sha256:" + digest:
        _invalid()
    return lookup


def _sections_wire(scope: Any, strategy: Any, risk_v2: Any) -> dict[str, Any]:
    """Local stdlib wire checks, including whole-catalog/global consistency."""
    for value, fields in ((scope, SCOPE_FIELDS), (strategy, STRATEGY_FIELDS),
                          (risk_v2, {"section_version", "product", "caps", *RISK_DECIMALS, *RISK_COUNTS, *RISK_CHOICES})):
        if value is not None:
            _object(value, fields)
            if type(value["section_version"]) is not int or value["section_version"] != 1:
                _invalid()
    if scope is not None:
        account = scope["account"]
        if account is not None:
            _object(account, {"account_id", "environment"})
            identifier = account["account_id"]
            if (type(identifier) is not str or not 1 <= len(identifier) <= 256 or identifier != identifier.strip()
                    or any(ord(char) < 32 or ord(char) == 127 for char in identifier)):
                _invalid()
            _choice(account["environment"], {"DEMO", "LIVE"})
        for name, choices in (("mode", {"FULL_AUTO", "ALL_IN_ONE"}), ("selected_product", {"SPOT", "LINEAR"}),
                              ("permission_profile_revision", {"BYBIT_SPOT_LINEAR_NO_FUNDS_V1"})):
            if scope[name] is not None:
                _choice(scope[name], choices)
        if scope["allowed_products"] is not None:
            _array(scope["allowed_products"], 2)
            for product in scope["allowed_products"]:
                _choice(product, {"SPOT", "LINEAR"})
        if scope["selected_product"] is not None and scope["selected_product"] not in (scope["allowed_products"] or []):
            _invalid()
        for name in ("allowed_instruments", "selected_instruments"):
            if scope[name] is not None:
                _array(scope[name])
                for key in scope[name]:
                    _key_wire(key)
                    if scope["selected_product"] is not None and key["product"] != scope["selected_product"]:
                        _invalid()
        if scope["selected_instruments"] and any(key not in (scope["allowed_instruments"] or []) for key in scope["selected_instruments"]):
            _invalid()
        # Mixed-product universes are invalid even before a product is selected.
        products = {key["product"] for name in ("allowed_instruments", "selected_instruments") for key in (scope[name] or [])}
        if len(products) > 1:
            _invalid()
        if scope["selected_product"] == "SPOT" and risk_v2 is not None:
            _invalid()
    if strategy is not None:
        lookup = _catalog_wire(strategy["catalog"]) if strategy["catalog"] is not None else {}
        for name in ("allowed", "selected"):
            if strategy[name] is not None:
                _array(strategy[name])
                for reference in strategy[name]:
                    _reference_wire(reference)
        for reference in strategy["allowed"] or []:
            token = _reference_wire(reference)
            if token not in lookup or lookup[token]["approval_status"] != "APPROVED":
                _invalid()
            if scope is not None and scope["account"] is not None and scope["account"]["environment"] not in lookup[token]["approval_environments"]:
                _invalid()
        if strategy["selected"] and any(reference not in (strategy["allowed"] or []) for reference in strategy["selected"]):
            _invalid()
    if risk_v2 is not None:
        _choice(risk_v2["product"], {"LINEAR"})
        for name in RISK_DECIMALS:
            if risk_v2[name] is not None:
                _decimal_wire(risk_v2[name], zero=name in RISK_ZERO)
        for name in RISK_COUNTS:
            if risk_v2[name] is not None and (type(risk_v2[name]) is not int or not 1 <= risk_v2[name] <= 1_000_000):
                _invalid()
        for name, choices in RISK_CHOICES.items():
            if risk_v2[name] is not None:
                _choice(risk_v2[name], choices)
        if risk_v2["leverage_min"] is not None and risk_v2["leverage_max"] is not None and Decimal(risk_v2["leverage_min"]) > Decimal(risk_v2["leverage_max"]):
            _invalid()
        if risk_v2["liquidation_buffer_fraction"] is not None and Decimal(risk_v2["liquidation_buffer_fraction"]) >= 1:
            _invalid()
        caps = risk_v2["caps"]
        if caps is not None:
            _object(caps, CAP_FIELDS)
            for name in ("max_order_admission_notional", "max_account_admission_notional", "max_execution_quote_amount"):
                if caps[name] is not None:
                    _decimal_wire(caps[name])
            for name, choices in (("valuation_rule", {"LINEAR_MARK_GROSS_AT_ADMISSION_V1"}),
                                  ("turnover_requirement", {"NOT_REQUIRED", "HARD_EXECUTION_QUOTE_CAP"})):
                if caps[name] is not None:
                    _choice(caps[name], choices)
            if caps["turnover_requirement"] == "NOT_REQUIRED" and caps["max_execution_quote_amount"] is not None:
                _invalid()
    result = {"scope": scope, "strategy": strategy, "risk_v2": risk_v2}
    encoded = json.dumps(result, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > SECTION_LIMIT:
        _invalid()
    return cast(dict[str, Any], json.loads(encoded))
# END PORTABLE POLICY SECTIONS


def catalog_from_wire(value: dict[str, Any], *, check_revision: bool = True) -> StrategyCatalog:
    """Rebuild every public descriptor and the semantic catalog; no approval claim."""
    entries = []
    for raw in value["entries"]:
        entry = dict(raw)
        entry["reference"] = StrategyReference(**raw["reference"])
        entry["permitted_instruments"] = tuple(InstrumentKey(**key) for key in raw["permitted_instruments"])
        entry["approval_environments"] = tuple(raw["approval_environments"])
        entries.append(StrategyDescriptor(**entry))
    catalog = build_strategy_catalog(tuple(entries))
    if check_revision and value["revision"] != catalog.revision:
        _invalid()
    return catalog


def validate_sections_v2(scope: object, strategy: object, risk_v2: object) -> dict[str, Any]:
    try:
        result = _sections_wire(scope, strategy, risk_v2)
        scope_wire, strategy_wire = result["scope"], result["strategy"]
        if scope_wire is not None:
            if scope_wire["account"] is not None and scope_wire["selected_product"] is not None:
                ProductScope(ProtectionScope(**scope_wire["account"]), scope_wire["selected_product"])
            for name in ("allowed_instruments", "selected_instruments"):
                for key in scope_wire[name] or []:
                    InstrumentKey(**key)
        if strategy_wire is not None and strategy_wire["catalog"] is not None:
            catalog_from_wire(strategy_wire["catalog"])
        return result
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise ValueError("POLICY_SECTIONS_INVALID") from None


def eligible_references(scope: dict[str, Any], strategy: dict[str, Any], key: InstrumentKey) -> tuple[tuple[StrategyReference, ...], tuple[StrategyReference, ...]]:
    catalog = catalog_from_wire(strategy["catalog"])
    lookup = {entry.reference: entry for entry in catalog.entries}
    allowed = tuple(StrategyReference(**raw) for raw in strategy["allowed"] or [])
    selected = tuple(StrategyReference(**raw) for raw in strategy["selected"] or [])
    eligible = tuple(reference for reference in allowed if key in lookup[reference].permitted_instruments)
    return eligible, tuple(reference for reference in selected if reference in eligible)


def section_missing_fields(policy: dict[str, Any]) -> list[str]:
    missing: set[str] = set()
    scope, strategy, risk = (policy[name] for name in ("scope", "strategy", "risk_v2"))
    mode = scope["mode"] if scope is not None else None
    for name, section in (("scope", scope), ("strategy", strategy), ("risk_v2", risk)):
        if section is None:
            missing.add(name)
            continue
        for key, value in section.items():
            if key in {"section_version", "product"} or key == "selected" and mode == "ALL_IN_ONE" or key == "selected_instruments" and mode == "ALL_IN_ONE":
                continue
            if value is None or value == []:
                missing.add(f"{name}.{key}")
        if name == "risk_v2" and section["caps"] is not None:
            caps = section["caps"]
            for key, value in caps.items():
                if key == "max_execution_quote_amount" and caps["turnover_requirement"] == "NOT_REQUIRED":
                    continue
                if value is None:
                    missing.add(f"risk_v2.caps.{key}")
    if (scope is not None and strategy is not None and scope["account"] is not None
            and mode in {"FULL_AUTO", "ALL_IN_ONE"} and strategy["catalog"] is not None
            and strategy["allowed"] and (mode == "ALL_IN_ONE" or strategy["selected"])):
        universe = scope["selected_instruments" if mode == "FULL_AUTO" else "allowed_instruments"]
        if universe:
            for raw in universe:
                allowed, selected = eligible_references(scope, strategy, InstrumentKey(**raw))
                if not (selected if mode == "FULL_AUTO" else allowed):
                    missing.add("strategy.eligible_pairs")
    return sorted(missing)
