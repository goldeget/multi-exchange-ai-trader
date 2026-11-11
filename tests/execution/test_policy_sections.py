"""Synthetic static policy fixtures; none of these pins confer authority."""

import copy
from dataclasses import asdict

import pytest

from multi_exchange_trader.execution.modes import StrategyReference
from multi_exchange_trader.execution.runtime_policy import KEYS, validate_v2
from multi_exchange_trader.execution.strategy_catalog import (
    StrategyDescriptor,
    build_strategy_catalog,
)
from multi_exchange_trader.trading_product import InstrumentKey


def policy_fixture(mode="FULL_AUTO"):
    keys = [InstrumentKey("BYBIT", "LINEAR", symbol) for symbol in ("BTCUSDT", "ETHUSDT")]
    entries = tuple(StrategyDescriptor(
        StrategyReference(name, "r1"), "a" * 64, "b" * 64, 1, "engine1", (key,),
        "approval1", "source1", "c" * 64, ("DEMO",), "APPROVED")
        for name, key in zip(("alpha", "beta"), keys))
    catalog = build_strategy_catalog(entries)
    # asdict retains tuple values, whereas the wire contract requires JSON arrays.
    import json
    catalog_wire = json.loads(json.dumps(asdict(catalog)))
    instruments = [asdict(key) for key in keys]
    refs = [asdict(entry.reference) for entry in entries]
    return {"legacy_v1": dict.fromkeys(KEYS), "scope": {
        "section_version": 1, "account": {"account_id": "fixture-account", "environment": "DEMO"},
        "mode": mode, "allowed_products": ["LINEAR"], "selected_product": "LINEAR",
        "permission_profile_revision": "BYBIT_SPOT_LINEAR_NO_FUNDS_V1",
        "allowed_instruments": instruments, "selected_instruments": copy.deepcopy(instruments),
    }, "strategy": {"section_version": 1, "catalog": catalog_wire, "allowed": refs, "selected": copy.deepcopy(refs)},
        "risk_v2": {"section_version": 2, "product": "LINEAR", "sizing_rule": "LOSS_FIXED",
                    "caps": {"max_order_admission_notional": "100.00",
                             "max_account_admission_notional": "200", "valuation_rule": "LINEAR_MARK_GROSS_AT_ADMISSION_V1",
                             "turnover_requirement": "NOT_REQUIRED", "max_execution_quote_amount": None},
                    **{key: "1.00" for key in ("leverage_min", "leverage_max", "per_trade_loss",
                       "max_daily_loss", "max_drawdown", "max_total_risk", "max_total_margin", "margin_allocation")},
                    "max_consecutive_losses": 2, "max_positions": 2, "margin_mode": "CROSS",
                    "position_mode": "ONE_WAY", "liquidation_buffer_fraction": "0.010",
                    "max_quote_age_seconds": "0", "max_evidence_age_seconds": "0",
                    "max_stop_gap_quote_per_base": "1.00"}}


def test_typed_heterogeneous_policy_preserves_all_pins_and_lexical_order():
    policy = policy_fixture()
    result = validate_v2(policy)
    assert result == policy
    assert result is not policy
    result["strategy"]["allowed"].reverse()
    assert result != policy


@pytest.mark.parametrize("section", ["scope", "strategy"])
@pytest.mark.parametrize("version", [True, 0, 2, None, "1"])
def test_future_and_non_exact_versions_fail_closed(section, version):
    policy = policy_fixture()
    policy[section]["section_version"] = version
    with pytest.raises(ValueError):
        validate_v2(policy)


@pytest.mark.parametrize("version", [True, 0, 3, None, "2"])
def test_risk_v2_rejects_non_current_versions(version):
    policy = policy_fixture()
    policy["risk_v2"]["section_version"] = version
    with pytest.raises(ValueError):
        validate_v2(policy)


def test_legacy_risk_section_reads_as_missing_stop_gap_without_a_numeric_default():
    from multi_exchange_trader.execution.policy_sections import section_missing_fields

    policy = policy_fixture()
    policy["risk_v2"].pop("max_stop_gap_quote_per_base")
    policy["risk_v2"]["section_version"] = 1
    result = validate_v2(policy)
    assert result["risk_v2"]["section_version"] == 2
    assert result["risk_v2"]["max_stop_gap_quote_per_base"] is None
    assert "risk_v2.max_stop_gap_quote_per_base" in section_missing_fields(result)


@pytest.mark.parametrize("version", [True, 1.0])
def test_legacy_risk_section_version_requires_exact_integer(version):
    policy = policy_fixture()
    policy["risk_v2"].pop("max_stop_gap_quote_per_base")
    policy["risk_v2"]["section_version"] = version
    with pytest.raises(ValueError):
        validate_v2(policy)


@pytest.mark.parametrize("decimal", ["01", "1e2", "-1", "0", 1.0, True, "1.123456789", "10000000000000000"])
def test_financial_operator_envelope_is_exact(decimal):
    policy = policy_fixture()
    policy["risk_v2"]["per_trade_loss"] = decimal
    with pytest.raises(ValueError):
        validate_v2(policy)


@pytest.mark.parametrize("fault", ["digest", "unknown", "revoked", "environment", "extra", "duplicate", "latest", "stale_product"])
def test_whole_global_input_is_validated_before_pair_derivation(fault):
    policy = policy_fixture()
    strategy = policy["strategy"]
    # Beta remains globally allowed while unused by FULL_AUTO's effective universe.
    policy["scope"]["selected_instruments"] = policy["scope"]["selected_instruments"][:1]
    strategy["selected"] = strategy["selected"][:1]
    if fault == "digest":
        strategy["catalog"]["entries"][1]["config_sha256"] = "d" * 64
    elif fault == "unknown":
        strategy["allowed"].append({"strategy_id": "unknown", "revision": "r1"})
    elif fault in {"revoked", "environment"}:
        entry = strategy["catalog"]["entries"][1]
        entry["approval_status" if fault == "revoked" else "approval_environments"] = "REVOKED" if fault == "revoked" else ["LIVE"]
        # Rehashing is not enough to make globally invalid allowed metadata admissible.
        from multi_exchange_trader.execution.policy_sections import catalog_from_wire
        catalog = catalog_from_wire(strategy["catalog"], check_revision=False)
        strategy["catalog"]["revision"] = catalog.revision
    elif fault == "extra":
        policy["scope"]["invented"] = None
    elif fault == "duplicate":
        strategy["allowed"].append(copy.deepcopy(strategy["allowed"][0]))
    elif fault == "latest":
        strategy["allowed"][0]["revision"] = "Latest"
    else:
        policy["scope"]["selected_product"] = "SPOT"
    with pytest.raises(ValueError):
        validate_v2(policy)


def test_partial_orphan_and_spot_are_savable_without_invented_values():
    policy = policy_fixture()
    orphan = {"venue": "BYBIT", "product": "LINEAR", "symbol": "SOLUSDT"}
    policy["scope"]["allowed_instruments"].append(orphan)
    policy["scope"]["selected_instruments"].append(orphan)
    assert validate_v2(policy) == policy


@pytest.mark.parametrize("path,value", [
    (("scope", "account", "account_id"), " private-value "),
    (("scope", "account", "environment"), "FIXTURE"),
    (("scope", "allowed_products"), ["LINEAR", "LINEAR"]),
    (("scope", "allowed_instruments"), ()),
    (("risk_v2", "position_mode"), "HEDGE"),
    (("risk_v2", "max_positions"), True),
    (("risk_v2", "max_consecutive_losses"), 1000001),
    (("risk_v2", "liquidation_buffer_fraction"), "1.000"),
    (("risk_v2", "max_stop_gap_quote_per_base"), "0"),
    (("risk_v2", "caps", "max_execution_quote_amount"), "100"),
    (("strategy", "catalog", "entries", 0, "config_schema_version"), True),
    (("strategy", "catalog", "entries", 1, "approval_source_sha256"), "A" * 64),
    (("strategy", "catalog", "entries", 1, "approval_environments"), []),
    (("strategy", "catalog", "entries", 1, "permitted_instruments"), []),
    (("strategy", "catalog", "entries", 1, "reference", "revision"), "LATEST"),
])
def test_closed_scalar_container_and_descriptor_failures_are_finite(path, value):
    policy = policy_fixture()
    target = policy
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError) as failure:
        validate_v2(policy)
    assert str(failure.value) == "POLICY_SECTIONS_INVALID"


def test_nested_duplicate_and_canonical_size_limits():
    import json

    from multi_exchange_trader.execution.runtime_policy import parse_json
    raw = json.dumps(policy_fixture()).replace('"section_version": 1', '"section_version": 1,"section_version": 1', 1)
    with pytest.raises(ValueError, match="duplicate policy field"):
        parse_json(raw)
    policy = policy_fixture()
    # Individually valid entries remain subject to the total canonical 64KiB bound.
    original = policy["strategy"]["catalog"]["entries"][0]
    entries = []
    for index in range(100):
        entry = copy.deepcopy(original)
        entry["reference"]["strategy_id"] = f"strategy{index:03}" + "x" * 80
        entries.append(entry)
    policy["strategy"]["catalog"]["entries"] = entries
    from multi_exchange_trader.execution.policy_sections import catalog_from_wire
    policy["strategy"]["catalog"]["revision"] = catalog_from_wire(policy["strategy"]["catalog"], check_revision=False).revision
    policy["strategy"]["allowed"] = policy["strategy"]["selected"] = []
    assert len(json.dumps(policy).encode()) > 65536
    with pytest.raises(ValueError):
        validate_v2(policy)


def test_spot_selection_remains_explicit_with_null_unsupported_risk():
    policy = policy_fixture()
    policy["scope"].update(selected_product="SPOT", allowed_products=["SPOT"],
                           allowed_instruments=None, selected_instruments=None)
    policy["strategy"] = None
    policy["risk_v2"] = None
    assert validate_v2(policy) == policy


def test_intrinsically_valid_nonallowed_historical_revoked_entry_remains_pinned():
    from multi_exchange_trader.execution.policy_sections import (
        catalog_from_wire,
        section_missing_fields,
    )
    policy = policy_fixture()
    policy["scope"]["selected_instruments"] = policy["scope"]["selected_instruments"][:1]
    policy["strategy"]["allowed"] = policy["strategy"]["allowed"][:1]
    policy["strategy"]["selected"] = policy["strategy"]["selected"][:1]
    catalog = policy["strategy"]["catalog"]
    catalog["entries"][1]["approval_status"] = "REVOKED"
    catalog["revision"] = catalog_from_wire(catalog, check_revision=False).revision
    assert validate_v2(policy) == policy
    assert section_missing_fields(policy) == []
    catalog["entries"][1]["config_schema_version"] = False
    with pytest.raises(ValueError, match="POLICY_SECTIONS_INVALID"):
        validate_v2(policy)
