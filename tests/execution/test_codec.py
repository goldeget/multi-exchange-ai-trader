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
