"""Portable, non-authorizing facts about the audited native command path."""

from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest


def describe(**arguments: Any) -> Any:
    from multi_exchange_trader.execution.native_capabilities import describe_native_capability

    return describe_native_capability(**arguments)


@pytest.mark.parametrize("source", ["MarkPrice", "LastPrice", "IndexPrice"])
@pytest.mark.parametrize("kind,forwarding,reason", [
    ("ATTACHED_ENTRY", "SUPPORTED", "ATTACHED_TRIGGER_SOURCE_FORWARDED"),
    ("STANDALONE_STOP", "UNSUPPORTED", "EXPLICIT_TRIGGER_SOURCE_NOT_FORWARDED"),
    ("STANDALONE_TAKE_PROFIT", "UNSUPPORTED", "EXPLICIT_TRIGGER_SOURCE_NOT_FORWARDED"),
])
def test_forwarding_distinguishes_attached_from_explicit_standalone_without_authority(
    source: str, kind: str, forwarding: str, reason: str,
) -> None:
    result = describe(native_version="1.231.0", command_kind=kind, trigger_source=source)
    assert result.parameter_forwarding == forwarding
    assert result.reason_code == reason
    assert result.production_supported is False
    assert (result.native_version, result.source_commit, result.environment, result.product) == (
        "1.231.0", "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317", "DEMO", "LINEAR")
    assert (result.command_kind, result.trigger_source) == (kind, source)


@pytest.mark.parametrize("field,value", [
    ("native_version", "1.230.0"), ("native_version", "1.231.0+unverified"),
    ("native_version", None), ("native_version", ["1.231.0"]),
    ("command_kind", "ENTRY"), ("command_kind", None), ("command_kind", {}),
    ("trigger_source", "MARK_PRICE"), ("trigger_source", ""), ("trigger_source", None),
])
def test_unaudited_inputs_fail_closed_with_fixed_error(field: str, value: object) -> None:
    arguments: dict[str, object] = {
        "native_version": "1.231.0", "command_kind": "ATTACHED_ENTRY",
        "trigger_source": "MarkPrice",
    }
    arguments[field] = value
    with pytest.raises(ValueError, match="^NATIVE_CAPABILITY_INPUT_UNSUPPORTED$"):
        describe(**arguments)


def test_capability_is_immutable_and_cannot_be_replaced_with_execution_authority() -> None:
    result = describe(native_version="1.231.0", command_kind="ATTACHED_ENTRY",
                      trigger_source="MarkPrice")
    with pytest.raises(FrozenInstanceError):
        result.production_supported = True
    with pytest.raises(ValueError):
        replace(result, production_supported=True)
