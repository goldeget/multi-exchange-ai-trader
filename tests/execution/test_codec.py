import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from multi_exchange_trader.execution.codec import decode, encode
from multi_exchange_trader.execution.protection import (
    FillEvent,
    OwnedOrder,
    ProjectionConflict,
    ProtectionProjection,
    ProtectionScope,
)


def test_complete_conflict_history_roundtrips_without_losing_decimal_or_utc() -> None:
    scope = ProtectionScope("a", "DEMO")
    order = OwnedOrder("id", "p", "BTC", "BUY", "ENTRY", Decimal("1.00"), False, 0, "v1")
    fill = FillEvent(scope, "e", "id", "p", "BTC", "BUY", Decimal("0.20"),
                     datetime(2026, 11, 2, tzinfo=UTC))
    unknown = replace(fill, execution_id="foreign", client_order_id="external")
    state = ProtectionProjection(scope, "p", "BTC", "BUY", (order,), (fill,), ("id",),
                                 (ProjectionConflict("UNOWNED", unknown, order, "external"),))
    restored = decode(encode(state))
    assert restored == state
    assert isinstance(restored, ProtectionProjection)
    assert str(restored.executions[0].quantity) == "0.20"
    assert restored.conflicts[0].event == unknown
    assert restored.cancelled_order_ids == ("id",)


@pytest.mark.parametrize("payload", [
    '{"record":"os.system","fields":{}}',
    '{"decimal":"NaN"}',
    '{"utc":"2026-11-02T00:00:00"}',
    '{"record":"ProtectionScope","fields":{"account_id":"a","environment":"MAINNET"}}',
    '{"record":"ProtectionScope","fields":{"account_id":true,"environment":"DEMO"}}',
])
def test_invalid_restored_state_fails_closed(payload: str) -> None:
    with pytest.raises((ValueError, TypeError)):
        decode(payload)


def test_untrusted_execution_records_roundtrip_with_rebate_and_provenance() -> None:
    from test_execution_provenance import api, candidate, observation

    original = candidate()
    records = (original.fill, original, observation(),
        api().compare_execution_candidate(original, observation()),
        api().FillQuarantineRecord(original, "AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED"))
    for record in records:
        assert decode(encode(record)) == record
        extra = json.loads(encode(record))
        extra["fields"]["authenticated"] = True
        with pytest.raises(ValueError, match="missing or extra record fields"):
            decode(json.dumps(extra))
    restored = decode(encode(records[-1]))
    assert restored.candidate.fill.native_reconciliation is False
    assert str(restored.candidate.fill.commission) == "-0.010"


def test_codec_cannot_restore_authorizing_comparison_or_invalid_metadata() -> None:
    from test_execution_provenance import api, candidate, observation

    comparison = json.loads(encode(api().compare_execution_candidate(candidate(), observation())))
    comparison["fields"]["credit_authorized"] = True
    with pytest.raises(ValueError):
        decode(json.dumps(comparison))
    fill = json.loads(encode(candidate().fill))
    fill["fields"]["native_reconciliation"] = 0
    with pytest.raises(ValueError):
        decode(json.dumps(fill))


def test_durable_credit_records_roundtrip_but_capabilities_and_results_do_not() -> None:
    from test_execution_credit import api, conflict, credit, reference

    for record in (reference(), credit(), conflict()):
        assert decode(encode(record)) == record
        extra = json.loads(encode(record))
        extra["fields"]["credit_authorized"] = True
        with pytest.raises(ValueError, match="missing or extra record fields"):
            decode(json.dumps(extra))
    assert str(decode(encode(credit())).candidate.fill.commission) == "-0.010"
    with pytest.raises(TypeError, match="unsupported execution codec value"):
        encode(api().ExecutionCreditResult("c" * 64, "CREDITED", ()))
    for name in ("AuthenticatedHistoryReceipt", "HistoryReceiptView"):
        with pytest.raises(ValueError, match="unknown record schema"):
            decode(json.dumps({"record": name, "fields": {}}))


def test_legacy_scope_encoding_is_byte_identical() -> None:
    assert encode(ProtectionScope("a", "DEMO")) == (
        '{"fields":{"account_id":"a","environment":"DEMO"},"record":"ProtectionScope"}')
