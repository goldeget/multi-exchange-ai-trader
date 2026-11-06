"""Read-only, source-preserving worker/catalog/native receipt pair checks."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import test_worker_signal_risk_plan_v1 as plan_support

from multi_exchange_trader.account.instrument_envelope import HttpsOrigin
from multi_exchange_trader.execution.runtime import RuntimeBlocked
from multi_exchange_trader.execution.worker_signal_instrument_pair_v1 import (
    WorkerSignalRiskInstrumentPairV1,
)
from multi_exchange_trader.trading_product import InstrumentKey

model_worker = plan_support.model_worker


def pair_value(**changes: object) -> WorkerSignalRiskInstrumentPairV1:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "signal_id": "signal-fixture",
        "signal_catalog_revision": "a" * 64,
        "plan_catalog_revision": "a" * 64,
        "public_catalog_revision": "a" * 64,
        "policy_revision": 2,
        "publication_generation": 1,
        "instrument": InstrumentKey("BYBIT", "LINEAR", "BTCUSDT"),
        "public_source_environment": "PUBLIC_MAINNET",
        "public_source_id": "bybit-public-mainnet",
        "public_endpoint": "/v5/market/instruments-info",
        "public_specification_revision": "b" * 64,
        "public_raw_row_sha256": "c" * 64,
        "public_response_time": now,
        "public_received_at": now,
        "demo_origin": HttpsOrigin("https", "api-demo.bybit.com", 443),
        "demo_target_fingerprint": "target-fixture",
        "demo_native_specification_revision": "d" * 64,
        "demo_native_semantics_sha256": "e" * 64,
        "demo_receipt_issued_at": now,
    }
    return WorkerSignalRiskInstrumentPairV1(**(values | changes))


def test_pair_snapshot_keeps_public_and_demo_revisions_separate() -> None:
    value = pair_value()

    assert value.status == "OBSERVED_PAIR_UNMAPPED"
    assert value.public_source_environment == "PUBLIC_MAINNET"
    assert value.demo_origin.host == "api-demo.bybit.com"
    assert value.public_specification_revision != value.demo_native_specification_revision
    assert value.financial_authority is False and value.trading_enabled is False
    assert not hasattr(value, "fee") and not hasattr(value, "receipt")


@pytest.mark.parametrize("changes", [
    {"public_catalog_revision": "f" * 64},
    {"public_source_environment": "PUBLIC_TESTNET"},
    {"instrument": InstrumentKey("BYBIT", "SPOT", "BTCUSDT")},
    {"demo_origin": HttpsOrigin("https", "api.bybit.com", 443)},
])
def test_pair_snapshot_rejects_revision_product_or_source_relabel(changes: dict[str, object]) -> None:
    with pytest.raises(RuntimeBlocked, match="WORKER_SIGNAL_RISK_INSTRUMENT_PAIR_INVALID"):
        pair_value(**changes)


@pytest.mark.asyncio
async def test_worker_pair_requires_current_restored_actual_fee_receipt_without_effects(
    model_worker: Any,
) -> None:
    state = model_worker
    signal_handle, _signal, plan_handle = await plan_support.prepare_signal_plan(state)
    before_requests = tuple(state.fixture.requests)
    before_commands = state.worker.runtime.journal.list_commands()
    before_native = tuple(state.strategy.commands)

    with pytest.raises(RuntimeBlocked, match="WORKER_SIGNAL_RISK_NATIVE_RECEIPT_REQUIRED"):
        state.worker.snapshot_builtin_signal_risk_instrument_pair_v1(signal_handle, plan_handle)

    assert tuple(state.fixture.requests) == before_requests
    assert state.worker.runtime.journal.list_commands() == before_commands == ()
    assert tuple(state.strategy.commands) == before_native == ()


@pytest.mark.asyncio
async def test_final_public_catalog_recheck_rejects_expiry_after_owner_callback(
    model_worker: Any,
) -> None:
    state = model_worker
    _signal_handle, signal, _plan_handle = await plan_support.prepare_signal_plan(state)
    owner = state.worker._entry_model_owner_v2
    prepared = owner._current[1]
    producer = owner._assembler.catalog_producer
    slot = owner._assembler._current
    current = producer.require_current(slot.catalog_receipt,
        expected_revision=signal.catalog_revision)
    producer._wall_clock = lambda: current.catalog.expires_at + timedelta(microseconds=1)

    with pytest.raises(RuntimeBlocked, match="WORKER_SIGNAL_RISK_PUBLIC_CATALOG_REQUIRED"):
        state.worker._require_current_signal_risk_public_catalog_v1(signal, prepared)

    assert producer._current is None
    assert state.worker.runtime.journal.list_commands() == ()
    assert not state.strategy.commands
