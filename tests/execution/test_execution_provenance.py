"""Untrusted observations can agree, but can never authorize credit."""

import importlib
import importlib.util
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from typing import Any

import pytest

from multi_exchange_trader.execution.native_events import NativeFillEvidence
from multi_exchange_trader.execution.protection import ProtectionScope


def api() -> Any:
    name = "multi_exchange_trader.execution.execution_provenance"
    assert importlib.util.find_spec(name) is not None, "candidate provenance module required"
    return importlib.import_module(name)


def candidate() -> Any:
    return api().NativeExecutionCandidate(NativeFillEvidence(
        scope=ProtectionScope("fixture-account", "DEMO"), execution_id=" exec-1 ",
        client_order_id="client-1", position_id="logical-position", instrument_id="BTC.BYBIT",
        side="BUY", quantity=Decimal("0.004"), price=Decimal("50001.5"),
        commission=Decimal("-0.010"), commission_currency="USDT", ts_event_ns=100,
        ts_init_ns=200, received_at_ns=900, source_kind="NATIVE_ORDER_FILLED",
        native_reconciliation=False), " venue-1 ")


def observation() -> Any:
    return api().VenueExecutionObservation(
        scope=ProtectionScope("fixture-account", "DEMO"), binding_reference="forged-binding",
        history_batch_id="forged-batch", execution_id=" exec-1 ", client_order_id="client-1",
        venue_order_id=" venue-1 ", instrument_id="BTC.BYBIT", side="BUY",
        quantity=Decimal("0.004"), price=Decimal("50001.5"), commission=Decimal("-0.010"),
        commission_currency="USDT", ts_event_ns=100)


def test_exact_match_and_forged_references_never_authorize_credit() -> None:
    result = api().compare_execution_candidate(candidate(), observation())
    assert result.state == "MATCH"
    assert result.mismatch_codes == ()
    assert result.credit_authorized is False
    with pytest.raises(FrozenInstanceError):
        result.credit_authorized = True
    with pytest.raises(ValueError):
        replace(result, credit_authorized=True)


@pytest.mark.parametrize("field,value,code", [
    ("scope", ProtectionScope("other", "DEMO"), "SCOPE_MISMATCH"),
    ("scope", ProtectionScope("fixture-account", "LIVE"), "SCOPE_MISMATCH"),
    ("execution_id", "other", "EXECUTION_ID_MISMATCH"),
    ("client_order_id", "other", "CLIENT_ORDER_ID_MISMATCH"),
    ("venue_order_id", "other", "VENUE_ORDER_ID_MISMATCH"),
    ("instrument_id", "other", "INSTRUMENT_ID_MISMATCH"),
    ("side", "SELL", "SIDE_MISMATCH"),
    ("quantity", Decimal("0.005"), "QUANTITY_MISMATCH"),
    ("price", Decimal("50001.6"), "PRICE_MISMATCH"),
    ("commission", Decimal("0.010"), "COMMISSION_MISMATCH"),
    ("commission_currency", "USDC", "COMMISSION_CURRENCY_MISMATCH"),
    ("ts_event_ns", 101, "TS_EVENT_NS_MISMATCH"),
])
def test_each_identity_and_economic_difference_conflicts(
    field: str, value: object, code: str,
) -> None:
    result = api().compare_execution_candidate(candidate(), replace(observation(), **{field: value}))
    assert (result.state, result.mismatch_codes, result.credit_authorized) == (
        "CONFLICT", (code,), False)


def test_receipt_times_do_not_change_execution_comparison_and_rebate_is_preserved() -> None:
    original = candidate()
    replay = replace(original, fill=replace(original.fill, received_at_ns=999, ts_init_ns=888))
    assert api().compare_execution_candidate(replay, observation()).state == "MATCH"
    assert original.fill.execution_id == " exec-1 "
    assert original.venue_order_id == " venue-1 "
    assert str(original.fill.commission) == "-0.010"


@pytest.mark.parametrize("value", [None, "", " ", "x" * 257, 1, False])
def test_missing_or_invalid_venue_id_is_not_inferred(value: object) -> None:
    with pytest.raises(ValueError):
        replace(candidate(), venue_order_id=value)


@pytest.mark.parametrize("field,value", [
    ("execution_id", ""), ("client_order_id", ""), ("position_id", ""),
    ("instrument_id", ""), ("quantity", "1"), ("quantity", Decimal(0)),
    ("price", Decimal("NaN")), ("commission", 0.1), ("commission_currency", "bad!"),
    ("ts_event_ns", True), ("ts_init_ns", -1), ("received_at_ns", "100"),
    ("native_reconciliation", 0), ("native_reconciliation", 1),
    ("native_reconciliation", "False"), ("native_reconciliation", None),
    ("source_kind", "OTHER"), ("scope", "scope"), ("side", "other"),
])
def test_invalid_native_candidate_fields_are_rejected(field: str, value: object) -> None:
    original = candidate()
    with pytest.raises(ValueError):
        replace(original, fill=replace(original.fill, **{field: value}))


@pytest.mark.parametrize("field,value", [
    ("binding_reference", ""), ("history_batch_id", ""), ("execution_id", ""),
    ("venue_order_id", None), ("quantity", "1"), ("price", Decimal(-1)),
    ("commission", Decimal("Infinity")), ("commission_currency", "bad!"),
    ("ts_event_ns", False), ("scope", "scope"), ("side", "other"),
])
def test_invalid_observation_cannot_enter_comparison(field: str, value: object) -> None:
    original = observation()
    with pytest.raises(ValueError):
        replace(original, **{field: value})


def test_fill_report_has_absent_reconciliation_and_remains_only_a_candidate() -> None:
    fill = replace(candidate().fill, source_kind="NATIVE_FILL_REPORT", native_reconciliation=None)
    result = api().compare_execution_candidate(replace(candidate(), fill=fill), observation())
    assert result.credit_authorized is False
    for invalid in (False, True):
        with pytest.raises(ValueError):
            replace(fill, native_reconciliation=invalid)


def test_comparison_rejects_inconsistent_state_and_unknown_codes() -> None:
    result = api().compare_execution_candidate(candidate(), observation())
    for changes in ({"state": "CONFLICT"}, {"mismatch_codes": ("SCOPE_MISMATCH",)},
                    {"state": "CONFLICT", "mismatch_codes": ("RAW_PROVIDER_SECRET",)},
                    {"credit_authorized": 0}):
        with pytest.raises(ValueError):
            replace(result, **changes)
