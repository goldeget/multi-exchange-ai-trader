"""Durable corroboration data cannot act as a live observation capability."""

import importlib
import importlib.util
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from multi_exchange_trader.execution.protection import ProtectionScope


def api() -> Any:
    name = "multi_exchange_trader.execution.execution_credit"
    assert importlib.util.find_spec(name) is not None, "durable credit data module required"
    return importlib.import_module(name)


def reference() -> Any:
    return api().ExecutionObservationReference(ProtectionScope("fixture-account", "DEMO"),
        "binding", "a" * 64, "snapshot", "BTCUSDT", 1, 2,
        datetime(2026, 11, 2, tzinfo=UTC), datetime(2026, 11, 2, tzinfo=UTC))


def credit() -> Any:
    from test_execution_provenance import candidate, observation

    return api().CorroboratedExecutionCredit("c" * 64, candidate(),
        replace(observation(), binding_reference="binding", history_batch_id="snapshot"),
        reference(), "b" * 64, "command")


def conflict() -> Any:
    return api().ExecutionCorroborationConflict("c" * 64, reference().scope, "BTC.BYBIT",
        "exec-1", "venue-1", "client-1", reference(), "b" * 64,
        ("d" * 64,), ("PRICE_MISMATCH",), None)


def test_durable_records_are_frozen_original_data_with_no_authority() -> None:
    for record in (reference(), credit(), conflict(),
                   api().ExecutionCreditResult("c" * 64, "CREDITED", ())):
        assert not hasattr(record, "credit_authorized")
        with pytest.raises(FrozenInstanceError):
            record.candidate_id = "changed"
    assert str(credit().candidate.fill.commission) == "-0.010"
    assert reference().observed_at == datetime(2026, 11, 2, tzinfo=UTC)


@pytest.mark.parametrize("field,value", [
    ("scope", "scope"), ("binding_reference", ""), ("binding_reference", "x" * 257),
    ("observation_key", "bad"), ("snapshot_id", ""), ("symbol", "BTC-USDT"),
    ("start_ms", True), ("end_ms", 0), ("end_ms", 7 * 86400000 + 2),
    ("observed_at", datetime(2026, 11, 2)),
    ("server_observed_at", datetime(2026, 11, 2, tzinfo=timezone(timedelta(hours=1)))),
])
def test_reference_rejects_invalid_fields(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        replace(reference(), **{field: value})


@pytest.mark.parametrize("field,value", [
    ("candidate_id", "bad"), ("candidate", None), ("observation", None),
    ("source", None), ("history_variant_hash", "bad"), ("command_key", ""),
])
def test_credit_rejects_invalid_fields(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        replace(credit(), **{field: value})


def test_credit_rejects_disconnected_source_and_mismatching_economics() -> None:
    original = credit()
    for changed in (replace(original.observation, history_batch_id="other"),
                    replace(original.observation, binding_reference="other"),
                    replace(original.observation, commission=Decimal("0.01"))):
        with pytest.raises(ValueError, match="^EXECUTION_CREDIT_INVALID$"):
            replace(original, observation=changed)


@pytest.mark.parametrize("changes", [
    {"mismatch_codes": ("raw-provider-message",)}, {"mismatch_codes": ()},
    {"mismatch_codes": ("PRICE_MISMATCH", "PRICE_MISMATCH")},
    {"history_conflict_keys": ["d" * 64]}, {"history_conflict_keys": ("bad",)},
    {"history_conflict_keys": ([],)},
    {"prior_credit_event_id": ""}, {"execution_id": ""}, {"source": None},
    {"prior_credit_event_id": "position-local-event"},
    {"scope": ProtectionScope("other", "DEMO")},
])
def test_conflict_rejects_unbounded_or_unknown_evidence(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        replace(conflict(), **changes)


def test_result_is_data_only_and_reason_allowlist_is_closed() -> None:
    assert api().ExecutionCreditResult("c" * 64, "CONFLICT", ("PRICE_MISMATCH",)).status == "CONFLICT"
    for status, codes in (("READY", ()), ("UNRESOLVED", ("provider raw secret",)),
                          ("UNRESOLVED", ["PRICE_MISMATCH"])):
        with pytest.raises(ValueError, match="^EXECUTION_CREDIT_RESULT_INVALID$"):
            api().ExecutionCreditResult("c" * 64, status, codes)


def test_prior_credit_reference_is_the_stable_account_execution_guard() -> None:
    original = conflict()
    prior = api().execution_guard(original.scope, original.instrument_id, original.execution_id)
    assert replace(original, prior_credit_event_id=prior).prior_credit_event_id == prior
    with pytest.raises(ValueError):
        replace(original, prior_credit_event_id="credit:" + "F" * 64)
    with pytest.raises(ValueError):
        replace(original, prior_credit_event_id="credit:" + "f" * 64)


def test_guard_is_structured_scoped_and_excludes_economic_representations() -> None:
    guard = api().execution_guard
    scope = ProtectionScope("a", "DEMO")
    first = guard(scope, "bc", "d")
    assert first.startswith("credit:") and len(first) == 71
    assert first != guard(scope, "b", "cd")
    assert first != guard(ProtectionScope("other", "DEMO"), "bc", "d")
    assert first != guard(ProtectionScope("a", "LIVE"), "bc", "d")
    candidate = credit().candidate
    scale = replace(candidate, fill=replace(candidate.fill, quantity=Decimal("0.00400")),
                    venue_order_id="changed")
    assert guard(candidate.fill.scope, candidate.fill.instrument_id, candidate.fill.execution_id) == guard(
        scale.fill.scope, scale.fill.instrument_id, scale.fill.execution_id)
    with pytest.raises(ValueError):
        guard(scope, "", "d")
