"""An original accepted DEMO signal can qualify only a read-only current PLAN."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
import test_worker_entry_model_v2 as model_support
import test_worker_strategy_signal_v1 as signal_support
from test_bybit_linear_entry_producer_v1 import economic_inputs

from multi_exchange_trader.execution import bybit_demo_uta1_profile_v1 as demo
from multi_exchange_trader.execution import risk_domain_v2 as domain_v2
from multi_exchange_trader.execution.runtime import RuntimeBlocked
from multi_exchange_trader.execution.worker_entry_model_v2 import WorkerModelPlanV2
from multi_exchange_trader.execution.worker_signal_risk_plan_v1 import WorkerSignalRiskPlanV1


async def prepare_signal_plan(
    state: Any,
    *,
    entry_price: Decimal = Decimal(104),
    stop_price: Decimal = Decimal(100),
) -> tuple[Any, Any, Any]:
    model_support.install(state)
    snapshot = state.book.read()
    draft = snapshot["policy"]
    draft["risk_v2"].update(max_quote_age_seconds="120", max_evidence_age_seconds="300")
    state.book.save_v2(snapshot["revision"], draft, expected_strategy_generation=1,
        policy_sections_version=1)
    state.worker.max_age = timedelta(minutes=5)
    scope, signal_handle, _bars = await signal_support.captured(
        state, raw_config=signal_support.config_bytes(signal_ttl_seconds=300))
    signal = state.worker.consume_builtin_signal_v1(signal_handle)
    state.fixture.mode = 3
    read = await state.worker.capture_entry_sources_v2(scope=scope, profile="DEMO_UTA1_V1",
        nonvip_fee_basis=state.fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
        budget=model_support.MonitoringReadBudget())
    inputs, domain = economic_inputs(read, state.book, state.fixture.clock(), scope=scope,
        side=signal.direction.side, entry_price=entry_price, stop_price=stop_price,
        lifetime_seconds=120)
    domain = replace(domain, source_generation=state.worker._entry_model_owner_v2._journal_epoch)
    inputs = replace(inputs, risk_domain_sha256=domain_v2.risk_domain_sha256_v2(domain))
    plan_handle = state.worker.prepare_entry_plan_v2(inputs=inputs, domain=domain,
        policy_revision=signal.policy_revision, publication_generation=signal.publication_generation)
    assert type(plan_handle) is WorkerModelPlanV2
    return signal_handle, signal, plan_handle


model_worker = signal_support.model_worker


@pytest.mark.asyncio
async def test_current_approved_signal_binds_to_current_demo_plan_without_side_effects(model_worker: Any) -> None:
    state = model_worker
    signal_handle, signal, plan_handle = await prepare_signal_plan(state)
    journal = state.worker.runtime.journal
    owner_meta = journal._v2_meta(required=True)
    commands = journal.list_commands()
    native_commands = tuple(state.strategy.commands)
    requests = tuple(state.fixture.requests)

    result = state.worker.consume_builtin_signal_risk_plan_v1(signal_handle, plan_handle)

    assert type(result) is WorkerSignalRiskPlanV1
    assert result.signal is signal
    assert type(result.prepared_entry.source_read.venue) is demo.BybitDemoUta1VenueStateV1
    assert result.prepared_entry.inputs.risk_inputs.policy.scope == signal.scope
    assert result.prepared_entry.inputs.risk_inputs.policy.revision == str(signal.policy_revision)
    assert result.prepared_entry.policy_revision == signal.policy_revision
    assert result.prepared_entry.publication_generation == signal.publication_generation
    assert result.prepared_entry.inputs.risk_inputs.proposal.side == signal.direction.side
    assert result.prepared_entry.inputs.risk_inputs.proposal.entry.limit_price == Decimal(104)
    assert result.prepared_entry.inputs.risk_inputs.proposal.stop_price == Decimal(100)
    assert result.financial_authority is False and result.trading_enabled is False
    assert result.valid_until <= signal.valid_until
    assert result.valid_until <= result.prepared_entry.valid_until
    assert journal._v2_meta(required=True) == owner_meta
    assert journal.list_commands() == commands == ()
    assert tuple(state.strategy.commands) == native_commands == ()
    assert tuple(state.fixture.requests) == requests


@pytest.mark.asyncio
async def test_signal_entry_or_stop_mismatch_retires_plan_and_never_stages(model_worker: Any) -> None:
    state = model_worker
    signal_handle, _signal, plan_handle = await prepare_signal_plan(
        state, entry_price=Decimal(50), stop_price=Decimal(40))
    journal = state.worker.runtime.journal
    owner_meta = journal._v2_meta(required=True)
    commands = journal.list_commands()
    requests = tuple(state.fixture.requests)

    with pytest.raises(RuntimeBlocked, match="WORKER_SIGNAL_RISK_PROPOSAL_MISMATCH"):
        state.worker.consume_builtin_signal_risk_plan_v1(signal_handle, plan_handle)

    with (pytest.raises(RuntimeBlocked, match="WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED"),
          state.worker.entry_model_publication_v2(plan_handle)):
        pytest.fail("a signal-mismatched plan must be retired")
    assert journal._v2_meta(required=True) == owner_meta
    assert journal.list_commands() == commands == ()
    assert not state.strategy.commands
    assert tuple(state.fixture.requests) == requests


@pytest.mark.asyncio
async def test_incomplete_user_risk_settings_never_receive_default_values(model_worker: Any) -> None:
    state = model_worker
    model_support.install(state)
    snapshot = state.book.read()
    draft = snapshot["policy"]
    draft["risk_v2"]["per_trade_loss"] = None
    state.book.save_v2(snapshot["revision"], draft, expected_strategy_generation=1,
        policy_sections_version=1)
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
async def test_retired_signal_rejects_existing_plan_and_retires_model_handle(model_worker: Any) -> None:
    state = model_worker
    signal_handle, _signal, plan_handle = await prepare_signal_plan(state)
    state.worker._strategy_signal_owner_v1.retire()
    journal = state.worker.runtime.journal
    owner_meta = journal._v2_meta(required=True)
    requests = tuple(state.fixture.requests)

    with pytest.raises(ValueError, match="BUILTIN_STRATEGY_SOURCE_REQUIRED"):
        state.worker.consume_builtin_signal_risk_plan_v1(signal_handle, plan_handle)
    with (pytest.raises(RuntimeBlocked, match="WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED"),
          state.worker.entry_model_publication_v2(plan_handle)):
        pytest.fail("a retired signal must retire its associated model plan")
    assert journal._v2_meta(required=True) == owner_meta
    assert journal.list_commands() == ()
    assert not state.strategy.commands
    assert tuple(state.fixture.requests) == requests


@pytest.mark.asyncio
async def test_tampered_current_model_inputs_reject_joint_plan_without_side_effects(model_worker: Any) -> None:
    state = model_worker
    signal_handle, _signal, plan_handle = await prepare_signal_plan(state)
    prepared = state.worker._entry_model_owner_v2._current[1]
    object.__setattr__(prepared.inputs.risk_inputs.proposal.entry, "limit_price", Decimal(105))
    journal = state.worker.runtime.journal
    owner_meta = journal._v2_meta(required=True)
    requests = tuple(state.fixture.requests)

    with pytest.raises(RuntimeBlocked, match="WORKER_SIGNAL_RISK_PROPOSAL_MISMATCH"):
        state.worker.consume_builtin_signal_risk_plan_v1(signal_handle, plan_handle)
    with (pytest.raises(RuntimeBlocked, match="WORKER_ENTRY_MODEL_PUBLICATION_REQUIRED"),
          state.worker.entry_model_publication_v2(plan_handle)):
        pytest.fail("tampered model input must retire its plan")
    assert journal._v2_meta(required=True) == owner_meta
    assert journal.list_commands() == ()
    assert not state.strategy.commands
    assert tuple(state.fixture.requests) == requests
