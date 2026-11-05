"""Actual worker/J/source/model owners with synthetic HTTP/economics, no financial grant."""
from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import test_risk
import test_runtime
from test_bybit_linear_entry_producer_v1 import SourceFixture, book_for, economic_inputs
from test_bybit_linear_source_assembler_v1 import SCOPE
from test_bybit_native import FakeFactory, FakeStrategy
from test_worker import fill, quarantines

from multi_exchange_trader.execution import risk_domain_v2 as d
from multi_exchange_trader.execution.bybit_linear_entry_producer_v1 import (
    BybitLinearPreparedEntryV1,
)
from multi_exchange_trader.execution.bybit_native import NativeBindings, NativeBybitBridge
from multi_exchange_trader.execution.journal import CommandJournal, CommandKind
from multi_exchange_trader.execution.modes import AutomaticMode
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
from multi_exchange_trader.execution.native_events import NativeEventDecoder
from multi_exchange_trader.execution.native_node import NativeChannel, NativeNodeAssembly
from multi_exchange_trader.execution.protection import ProtectionPolicy
from multi_exchange_trader.execution.risk import RiskScope, assess_entry
from multi_exchange_trader.execution.runtime import RuntimeBlocked
from multi_exchange_trader.execution.worker import ExecutionWorker, TrustedPlanRegistry
from multi_exchange_trader.execution.worker_entry_model_v2 import WorkerModelPlanV2
from multi_exchange_trader.market_data.adapters.bybit import BybitAdapter
from multi_exchange_trader.market_data.service import MarketDataService


def new_worker(fixture: Any, book: Any, path: Path, binding: Any) -> tuple[ExecutionWorker, Any]:
    scope = SCOPE.product_scope.account
    strategy = FakeStrategy()
    bridge = NativeBybitBridge(scope=scope, native_account_id="BYBIT-UNIFIED", factory=FakeFactory(),
        strategy=strategy, bindings=NativeBindings(str, str, str, str, str))
    assembly = NativeNodeAssembly(SimpleNamespace(), strategy, object(), bridge, NativeChannel(scope, "BYBIT-UNIFIED"))

    def no_entry(*_args: Any) -> Any:
        raise RuntimeBlocked("TEST_NO_FINANCIAL_INPUT_ISSUER")

    return ExecutionWorker(directory=path, scope=scope, session=fixture.session, binding=binding,
        symbols=("BTCUSDT",), trader_id="MODEL-WORKER", policy_book=book, registry=TrustedPlanRegistry(()),
        entry_risk=no_entry, clock=fixture.clock, max_age=timedelta(seconds=30),
        assembly_factory=lambda _sink: assembly,
        decoder=NativeEventDecoder(event_types={SimpleNamespace: "OrderFilled"})), strategy


@pytest.fixture
async def model_worker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    fixture = SourceFixture(monkeypatch, tmp_path)
    book, publication = book_for(tmp_path)
    binding = await fixture.auth()
    worker, strategy = new_worker(fixture, book, tmp_path, binding)
    await worker.start_passive()
    market_client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _request: pytest.fail("entry model fixture must not read public market data")))
    state = SimpleNamespace(worker=worker, fixture=fixture, book=book, publication=publication,
        strategy=strategy, path=tmp_path, binding=binding,
        market=MarketDataService({"bybit": BybitAdapter(market_client)}), market_client=market_client,
        signal_market_clients=[])
    try:
        yield state
    finally:
        await state.worker.stop()
        for client in state.signal_market_clients:
            await client.aclose()
        await state.market_client.aclose()
        await fixture.close()


def install(state: Any) -> None:
    state.worker.install_entry_model_v2(public_marks=state.fixture.marks,
        catalog_producer=state.fixture.catalog, market=state.market)


async def prepare(state: Any) -> tuple[Any, Any, Any, Any]:
    worker = state.worker
    read = await worker.capture_entry_sources_v2(scope=SCOPE, profile="UTA2_V1",
        nonvip_fee_basis=state.fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
        budget=MonitoringReadBudget())
    inputs, domain = economic_inputs(read, state.book, state.fixture.clock())
    # Explicit fixture-only economic sources do not become account-loss authority.
    domain = replace(domain, source_generation=worker._entry_model_owner_v2._journal_epoch)
    inputs = replace(inputs, risk_domain_sha256=d.risk_domain_sha256_v2(domain))
    handle = worker.prepare_entry_plan_v2(inputs=inputs, domain=domain,
        policy_revision=2, publication_generation=1)
    assert type(handle) is WorkerModelPlanV2
    return handle, read, inputs, domain


async def test_actual_worker_consumes_original_sources_and_model_without_financial_grant(model_worker: Any) -> None:
    state = model_worker
    assert state.worker._journal._v2_meta(required=False) is None  # Passive startup does not bind entry J.
    before = len(state.fixture.requests)
    install(state)
    assert len(state.fixture.requests) == before  # Explicit installation makes no network request.
    handle, read, inputs, _domain = await prepare(state)
    owner = state.worker._entry_model_owner_v2
    with state.worker.entry_model_publication_v2(handle) as original:
        assert type(original) is BybitLinearPreparedEntryV1
        assert original is owner._producer._current.result
        assert original.source_read is read and original.inputs.risk_inputs is inputs
        assert owner._assembler.journal is state.worker.runtime.journal
        assert read.full_reservations.owner_generation == owner._journal_epoch
    assert state.worker.runtime.journal.list_commands() == ()
    assert not state.strategy.commands
    with pytest.raises(RuntimeBlocked, match="NATIVE_WORKER_QUALIFICATION_REQUIRED"):
        state.worker.runtime.start()


@pytest.mark.parametrize("fault", ["source", "read-copy", "generation", "foreign-handle", "approval", "j-epoch"])
async def test_changed_original_owners_cannot_reuse_worker_model_plan(model_worker: Any, fault: str) -> None:
    state = model_worker
    install(state)
    handle, read, _inputs, _domain = await prepare(state)
    owner = state.worker._entry_model_owner_v2
    if fault == "source":
        owner._assembler._current = None
    elif fault == "read-copy":
        owner._read = replace(read)
    elif fault == "generation":
        state.worker._retire_entry_publications_v2()
    elif fault == "foreign-handle":
        handle = object.__new__(WorkerModelPlanV2)
    elif fault == "approval":
        state.publication.revoke(SCOPE.strategy, expected_policy_revision=2, expected_generation=1)
    else:
        old = state.worker._journal._v2_meta(required=True)
        state.worker._journal.bind_owner_epoch_v2("competing-owner", expected_previous_owner_epoch=old[0],
            expected_journal_revision=old[1])
    with pytest.raises(ValueError), state.worker.entry_model_publication_v2(handle):
        pytest.fail("a retired, copied or replaced original must not yield")
    assert not state.strategy.commands and state.worker.runtime.journal.list_commands() == ()


async def test_handle_is_not_constructible_copyable_or_usable_outside_original_section(model_worker: Any) -> None:
    state = model_worker
    install(state)
    handle, _read, _inputs, _domain = await prepare(state)
    with pytest.raises(RuntimeBlocked):
        WorkerModelPlanV2()
    with pytest.raises(RuntimeBlocked):
        copy.copy(handle)
    with pytest.raises(RuntimeBlocked):
        state.worker._entry_model_owner_v2.require(handle)
    with state.worker.entry_model_publication_v2(handle):
        pass


async def test_caught_reentrant_prepare_taints_original_worker_section(model_worker: Any) -> None:
    state = model_worker
    install(state)
    handle, _read, inputs, domain = await prepare(state)
    with (
        pytest.raises(RuntimeBlocked),
        state.worker.entry_model_publication_v2(handle),
        pytest.raises(RuntimeBlocked),
    ):
        state.worker.prepare_entry_plan_v2(inputs=inputs, domain=domain,
            policy_revision=2, publication_generation=1)
    assert state.worker._entry_publication_frame_v2 is None
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


@pytest.mark.parametrize("state_name", ["PENDING", "UNKNOWN"])
async def test_existing_nonterminal_command_prevents_new_entry_epoch(model_worker: Any, state_name: str) -> None:
    state = model_worker
    journal: CommandJournal = state.worker.runtime.journal
    record = journal.register("preexisting", CommandKind.ENTRY, {"test_only": "not-sent"})
    if state_name == "UNKNOWN":
        journal.claim(record.command_key)
        record = journal.mark_unknown(record.command_key, "TEST_ONLY_UNKNOWN")
    before_meta = journal._v2_meta(required=False)
    before_records = journal.list_commands()
    with pytest.raises(RuntimeBlocked, match="RECONCILIATION_REQUIRED"):
        install(state)
    assert state.worker._entry_model_owner_v2 is None
    assert journal._v2_meta(required=False) == before_meta
    assert journal.list_commands() == before_records
    assert not state.strategy.commands


async def test_actual_j_cas_conflict_does_not_install_an_owner(model_worker: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    state = model_worker
    journal: CommandJournal = state.worker.runtime.journal
    original = journal.bind_owner_epoch_v2

    def conflict(epoch: str, *, expected_previous_owner_epoch: str | None, expected_journal_revision: int) -> int:
        original("competing-owner", expected_previous_owner_epoch=expected_previous_owner_epoch,
            expected_journal_revision=expected_journal_revision)
        return original(epoch, expected_previous_owner_epoch=expected_previous_owner_epoch,
            expected_journal_revision=expected_journal_revision)

    monkeypatch.setattr(journal, "bind_owner_epoch_v2", conflict)
    with pytest.raises(ValueError, match="LOCAL_ADMISSION_CONFLICT"):
        install(state)
    assert state.worker._entry_model_owner_v2 is None
    meta = journal._v2_meta(required=True)
    assert meta is not None and meta[0] == "competing-owner"
    assert journal.list_commands() == () and not state.strategy.commands


async def test_new_worker_with_same_local_lease_counter_gets_distinct_j_epoch(model_worker: Any) -> None:
    state = model_worker
    install(state)
    handle, _read, _inputs, _domain = await prepare(state)
    first, first_owner = state.worker, state.worker._entry_model_owner_v2
    first_counter = first._lock.acquisition_id
    first_epoch = first_owner._journal_epoch
    await first.stop()
    worker, strategy = new_worker(state.fixture, state.book, state.path, state.binding)
    state.worker, state.strategy = worker, strategy
    await worker.start_passive()
    assert worker._lock is not None
    assert worker._lock.acquisition_id == first_counter
    install(state)
    assert worker._entry_model_owner_v2 is not None
    assert worker._entry_model_owner_v2._journal_epoch != first_epoch
    with pytest.raises(RuntimeBlocked), worker.entry_model_publication_v2(handle):
        pytest.fail("old worker handle must never join the new owner")
    assert worker.runtime.journal.list_commands() == () and not strategy.commands


async def test_caught_capture_reentry_cannot_republish_the_outer_read(model_worker: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    state = model_worker
    install(state)
    entered, release = asyncio.Event(), asyncio.Event()
    original = state.fixture.send

    async def paused(client: Any, request: Any, **kwargs: Any) -> Any:
        if request.url.path == "/v5/position/list" and not entered.is_set():
            entered.set()
            await release.wait()
        return await original(client, request, **kwargs)

    monkeypatch.setattr(state.fixture, "send", paused)

    async def capture() -> Any:
        return await state.worker.capture_entry_sources_v2(scope=SCOPE, profile="UTA2_V1",
            nonvip_fee_basis=state.fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
            budget=MonitoringReadBudget())

    outer = asyncio.create_task(capture())
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        with pytest.raises(RuntimeBlocked, match="OPERATION_IN_PROGRESS"):
            await capture()
        release.set()
        with pytest.raises(ValueError):
            await outer
        owner = state.worker._entry_model_owner_v2
        assert owner._read is None and owner._current is None and owner._busy is None
        assert state.worker._operations == 0
        assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands
    finally:
        release.set()
        if not outer.done():
            await outer


async def test_actual_native_callback_retires_the_read_before_another_prepare(model_worker: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    state = model_worker
    install(state)
    _handle, _read, inputs, domain = await prepare(state)
    before = state.worker.callback_failures

    def replaced_retire() -> None:
        pytest.fail("cleanup must invoke the original retirement implementation")

    monkeypatch.setattr(state.worker._entry_model_owner_v2, "retire", replaced_retire)
    # Real owned-channel callback path, with unsupported synthetic event; no invented fill/order.
    state.worker._consume_native(state.worker._assembly.channel, object(), 1)
    assert state.worker.callback_failures == before + 1
    assert state.worker._entry_model_owner_v2._read is None
    with pytest.raises(RuntimeBlocked, match="PUBLICATION_REQUIRED"):
        state.worker.prepare_entry_plan_v2(inputs=inputs, domain=domain,
            policy_revision=2, publication_generation=1)
    assert state.worker.runtime.journal.list_commands() == () and not state.strategy.commands


async def test_successful_actual_native_fill_also_discards_the_original_model_read(model_worker: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    state = model_worker
    install(state)
    _handle, _read, _inputs, _domain = await prepare(state)
    now = state.fixture.clock()
    monkeypatch.setattr(test_risk, "NOW", now)
    monkeypatch.setattr(test_runtime, "NOW", now)
    data = test_risk.qualified_inputs()
    scope = RiskScope("12345", "DEMO", "BTCUSDT", "strategy-1")
    for key in ("policy", "market", "instrument", "account", "tiers", "reservations", "liquidation", "leverage_readback"):
        data[key] = replace(data[key], scope=scope)
    request = test_runtime.selection()
    request = replace(request, scope=replace(request.scope, account_id="12345"),
        policy=replace(request.policy, account_id="12345"),
        candidates=tuple(replace(candidate, account_id="12345") for candidate in request.candidates))
    record = state.worker.runtime.queue_entry("native-entry", "position-1", plan_id="plan-1",
        plan=assess_entry(**data), protection_policy=ProtectionPolicy("policy-1", 0,
            Decimal(90), Decimal(120), "MarkPrice"), mode=AutomaticMode.FULL_AUTO, selection_request=request)
    state.worker.runtime.journal.claim(record.command_key)
    event = fill(record.client_order_id)
    event.ts_event = event.ts_init = int(now.timestamp() * 1_000_000_000)
    before = state.worker.callback_failures
    state.worker._consume_native(state.worker._assembly.channel, event, event.ts_event)
    assert state.worker.callback_failures == before  # Genuine decoder/owned durable map succeeded.
    assert len(quarantines(state.worker)) == 1
    assert state.worker.runtime.projection("position-1").owned_quantity == 0  # Authenticated history still required.
    assert state.worker._entry_model_owner_v2._read is None
    assert not state.strategy.commands  # Synthetic native event only, no submitted order.
