"""A DEMO signal cannot qualify a PLAN without an approved holding horizon."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
import test_worker_entry_model_v2 as model_support
import test_worker_strategy_signal_v1 as signal_support
from test_bybit_linear_entry_producer_v1 import economic_inputs

from multi_exchange_trader.execution import risk_domain_v2 as domain_v2
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected


async def prepare_entry_read(state: Any, *, include_held_eth: bool = False) -> tuple[Any, Any, Any]:
    market, _bars = signal_support.install(state)
    snapshot = state.book.read()
    draft = snapshot["policy"]
    draft["risk_v2"].update(max_quote_age_seconds="120", max_evidence_age_seconds="300")
    state.book.save_v2(snapshot["revision"], draft, expected_strategy_generation=1,
        policy_sections_version=2)
    state.worker.max_age = timedelta(minutes=5)
    scope = signal_support.approved_fixture(
        state, raw_config=signal_support.config_bytes(signal_ttl_seconds=300))
    state.fixture.mode = 3
    state.fixture.include_held_eth = include_held_eth
    read = await state.worker.capture_entry_sources_v2(scope=scope, profile="DEMO_UTA1_V1",
        nonvip_fee_basis=state.fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
        budget=model_support.MonitoringReadBudget())
    return market, scope, read


async def prepare_signal_inputs(
    state: Any,
    *,
    entry_price: Decimal = Decimal(104),
    stop_price: Decimal = Decimal(100),
) -> tuple[Any, Any, Any, Any]:
    market, scope, read = await prepare_entry_read(state)
    signal_handle = await state.worker.capture_builtin_signal_v1(scope=scope, market=market)
    signal = state.worker.consume_builtin_signal_v1(signal_handle)
    entry_owner = state.worker._entry_model_owner_v2
    entry_slot = entry_owner._assembler._current
    signal_issued = state.worker._strategy_signal_owner_v1._current
    assert entry_owner._read is read and entry_slot is not None
    assert entry_slot.result is read and signal_issued.entry_read is read
    assert signal_issued.entry_read_slot is entry_slot
    assert signal_issued.catalog_receipt is entry_slot.catalog_receipt
    assert state.fixture.catalog._current is entry_slot.catalog_current
    assert entry_slot.catalog_current.view.source_environment == "PUBLIC_MAINNET"
    assert {key.symbol for key in read.catalog.declared_universe} == {"BTCUSDT"}
    inputs, domain = economic_inputs(read, state.book, state.fixture.clock(), scope=scope,
        side=signal.direction.side, entry_price=entry_price, stop_price=stop_price,
        lifetime_seconds=120)
    domain = replace(domain, source_generation=state.worker._entry_model_owner_v2._journal_epoch)
    inputs = replace(inputs, risk_domain_sha256=domain_v2.risk_domain_sha256_v2(domain))
    assert entry_owner._read is read and entry_owner._assembler._current is entry_slot
    assert signal_issued.entry_read is read and signal_issued.catalog_receipt is entry_slot.catalog_receipt
    assert entry_owner._current is None and entry_owner._producer._current is None
    return signal_handle, signal, inputs, domain


def assert_horizon_blocked(state: Any, signal: Any, inputs: Any, domain: Any) -> None:
    owner = state.worker._entry_model_owner_v2
    journal = state.worker.runtime.journal
    owner_meta = journal._v2_meta(required=True)
    commands = journal.list_commands()
    native_commands = tuple(state.strategy.commands)
    requests = tuple(state.fixture.requests)

    with pytest.raises(RiskV2Rejected, match="^RISK_V2_HOLDING_HORIZON_REQUIRED$"):
        state.worker.prepare_entry_plan_v2(
            inputs=inputs,
            domain=domain,
            policy_revision=signal.policy_revision,
            publication_generation=signal.publication_generation,
        )

    assert owner._current is None and owner._producer._current is None
    assert owner._risk_assessment_current is None
    assert journal._v2_meta(required=True) == owner_meta
    assert journal.list_commands() == commands == ()
    assert tuple(state.strategy.commands) == native_commands == ()
    assert tuple(state.fixture.requests) == requests
    assert state.worker._entry_publication_frame_v2 is None


@pytest.mark.asyncio
async def test_joint_signal_rejects_expired_prepared_entry_read_without_public_refresh(model_worker: Any) -> None:
    state = model_worker
    market, scope, read = await prepare_entry_read(state)
    previous_requests = tuple(state.fixture.requests)
    object.__setattr__(read.venue, "valid_until", state.fixture.clock() - timedelta(seconds=1))

    with pytest.raises(RiskV2Rejected, match="RISK_V2_EXPIRED"):
        await state.worker.capture_builtin_signal_v1(scope=scope, market=market)

    assert tuple(state.fixture.requests) == previous_requests
    assert state._signal_test_source_v1.calls == 0
    assert state.worker._entry_model_owner_v2._read is None
    signal_owner = state.worker._strategy_signal_owner_v1
    assert signal_owner is None or signal_owner._current is None


@pytest.mark.asyncio
async def test_joint_signal_rejects_replaced_entry_catalog_receipt_before_market_read(model_worker: Any) -> None:
    state = model_worker
    market, scope, read = await prepare_entry_read(state)
    previous_requests = tuple(state.fixture.requests)
    await state.fixture.catalog.refresh(declared_universe=read.catalog.declared_universe,
        max_age=read.catalog.max_age, budget=model_support.MonitoringReadBudget())
    rotated_requests = tuple(state.fixture.requests)
    assert rotated_requests != previous_requests

    with pytest.raises(ValueError, match="BUILTIN_STRATEGY_SOURCE_REQUIRED"):
        await state.worker.capture_builtin_signal_v1(scope=scope, market=market)

    assert tuple(state.fixture.requests) == rotated_requests
    assert state._signal_test_source_v1.calls == 0
    assert state.worker._entry_model_owner_v2._read is None
    signal_owner = state.worker._strategy_signal_owner_v1
    assert signal_owner is None or signal_owner._current is None


@pytest.mark.asyncio
async def test_joint_signal_rejects_entry_catalog_missing_selected_instrument(model_worker: Any) -> None:
    state = model_worker
    market, scope, read = await prepare_entry_read(state)
    previous_requests = tuple(state.fixture.requests)
    object.__setattr__(read.catalog, "declared_universe",
        tuple(key for key in read.catalog.declared_universe if key != scope.instrument))

    with pytest.raises(ValueError, match="BUILTIN_STRATEGY_SOURCE_REQUIRED"):
        await state.worker.capture_builtin_signal_v1(scope=scope, market=market)

    assert tuple(state.fixture.requests) == previous_requests
    assert state._signal_test_source_v1.calls == 0
    assert state.worker._entry_model_owner_v2._read is None
    signal_owner = state.worker._strategy_signal_owner_v1
    assert signal_owner is None or signal_owner._current is None


model_worker = signal_support.model_worker


@pytest.mark.asyncio
async def test_signal_reuses_entry_read_for_dynamic_held_eth_catalog(model_worker: Any) -> None:
    state = model_worker
    market, scope, read = await prepare_entry_read(state, include_held_eth=True)
    entry_owner = state.worker._entry_model_owner_v2
    entry_slot = entry_owner._assembler._current
    assert entry_owner._read is read and entry_slot is not None and entry_slot.result is read
    assert {key.symbol for key in read.catalog.declared_universe} == {"BTCUSDT", "ETHUSDT"}
    assert {row.instrument_pin.key.symbol for row in read.venue.held_positions} == {"ETHUSDT"}
    assert {row.instrument_pin.key.symbol for row in read.venue.held_orders} == {"ETHUSDT"}
    assert read.venue.wallet.total_position_im.value == Decimal(10)
    assert read.venue.wallet.total_order_im.value == Decimal(2)
    assert {observation.key.symbol for observation in read.catalog.observations} == {"BTCUSDT", "ETHUSDT"}
    assert entry_slot.catalog_current.view.source_environment == "PUBLIC_MAINNET"
    assert state.fixture.catalog._current is entry_slot.catalog_current

    private_requests = tuple(state.fixture.requests)
    signal_handle = await state.worker.capture_builtin_signal_v1(scope=scope, market=market)
    signal = state.worker.consume_builtin_signal_v1(signal_handle)
    signal_issued = state.worker._strategy_signal_owner_v1._current
    assert signal.scope == scope
    assert entry_owner._read is read and entry_owner._assembler._current is entry_slot
    assert signal_issued.entry_read is read and signal_issued.entry_read_slot is entry_slot
    assert signal_issued.catalog_receipt is entry_slot.catalog_receipt
    assert state.fixture.catalog._current is entry_slot.catalog_current
    assert tuple(state.fixture.requests) == private_requests


@pytest.mark.asyncio
async def test_current_approved_signal_cannot_create_plan_without_horizon(model_worker: Any) -> None:
    state = model_worker
    _signal_handle, signal, inputs, domain = await prepare_signal_inputs(state)
    assert inputs.proposal.entry.limit_price == Decimal(104)
    assert inputs.proposal.stop_price == Decimal(100)
    assert_horizon_blocked(state, signal, inputs, domain)


@pytest.mark.asyncio
async def test_signal_entry_or_stop_variant_cannot_bypass_missing_horizon(model_worker: Any) -> None:
    state = model_worker
    _signal_handle, signal, inputs, domain = await prepare_signal_inputs(
        state, entry_price=Decimal(50), stop_price=Decimal(40))
    assert_horizon_blocked(state, signal, inputs, domain)


@pytest.mark.asyncio
async def test_incomplete_user_risk_settings_never_receive_default_values(model_worker: Any) -> None:
    state = model_worker
    signal_support.install(state)
    snapshot = state.book.read()
    draft = snapshot["policy"]
    draft["risk_v2"]["per_trade_loss"] = None
    state.book.save_v2(snapshot["revision"], draft, expected_strategy_generation=1,
        policy_sections_version=2)
    scope, signal_handle, _bars = await signal_support.captured(
        state, raw_config=signal_support.config_bytes(signal_ttl_seconds=300))
    assert state.worker.consume_builtin_signal_v1(signal_handle).scope == scope
    requests = tuple(state.fixture.requests)
    journal = state.worker.runtime.journal
    owner_meta = journal._v2_meta(required=True)

    with pytest.raises(ValueError, match="POLICY_V2_ADMISSION_REQUIRED"):
        await state.worker.capture_entry_sources_v2(scope=scope, profile="DEMO_UTA1_V1",
            nonvip_fee_basis=state.fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
            budget=model_support.MonitoringReadBudget())

    assert tuple(state.fixture.requests) == requests
    assert journal._v2_meta(required=True) == owner_meta
    assert journal.list_commands() == ()
    assert not state.strategy.commands


@pytest.mark.asyncio
async def test_retired_signal_cannot_create_plan_without_horizon(model_worker: Any) -> None:
    state = model_worker
    signal_handle, signal, inputs, domain = await prepare_signal_inputs(state)
    state.worker._strategy_signal_owner_v1.retire()
    with pytest.raises(ValueError, match="BUILTIN_STRATEGY_SOURCE_REQUIRED"):
        state.worker.consume_builtin_signal_v1(signal_handle)
    assert_horizon_blocked(state, signal, inputs, domain)


@pytest.mark.asyncio
async def test_caller_low_costs_cannot_create_plan_without_horizon(model_worker: Any) -> None:
    state = model_worker
    _signal_handle, signal, inputs, domain = await prepare_signal_inputs(state)
    low_cost = replace(
        inputs.costs,
        fee_quote_per_base=Decimal("0.00000001"),
        funding_quote_per_base=Decimal("0.00000001"),
    )
    assert_horizon_blocked(state, signal, replace(inputs, costs=low_cost), domain)
