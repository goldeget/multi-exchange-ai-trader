"""Same-worker join of current signal/plan with an actual-fee restored T4 receipt.

Every Bybit HTTP request in this test is handled by the existing synthetic
restored/source fixtures. The pair snapshot performs no network or order I/O.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import test_bybit_linear_entry_producer_v1 as source_support
import test_worker_strategy_signal_v1 as signal_support
from restored_monitoring_io_support import admit, restored_case

from multi_exchange_trader.account.instrument_envelope import HttpsOrigin
from multi_exchange_trader.execution import risk_domain_v2 as domain_v2
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
from multi_exchange_trader.execution.native_instrument_receipts import (
    NativeInstrumentReceiptViewV1,
)
from multi_exchange_trader.execution.native_monitor_client import MonitoringBybitExecutionClient
from multi_exchange_trader.execution.observation_instrument_v1 import ACTUAL_ACCOUNT_FEE_V1
from multi_exchange_trader.execution.worker_entry_model_v2 import WorkerModelPlanV2
from multi_exchange_trader.trading_product import InstrumentKey


@pytest.mark.asyncio
async def test_same_worker_pair_joins_current_signal_plan_and_actual_fee_t4_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_httpx_send = httpx.AsyncClient.send
    async with restored_case(tmp_path, monkeypatch, pending_command=False) as h:
        # Install the test policy book before MonitoringResume captures owner
        # identity. Replacing this pointer after admission would invalidate the
        # restored monitoring owner.
        model_root = tmp_path / "signal-instrument-pair-model"
        model_root.mkdir(parents=True, exist_ok=True)
        book, publication = source_support.book_for(model_root)
        h.worker.policy_book = book
        h.book = book

        fixture = source_support.SourceFixture(monkeypatch, model_root)

        async def dispatch(
            client: httpx.AsyncClient,
            request: httpx.Request,
            **kwargs: Any,
        ) -> httpx.Response:
            host = request.url.host
            if host == "api-demo.bybit.com":
                return await h.io.send(request, **kwargs)
            if host != "api.bybit.com":
                raise AssertionError("unexpected synthetic HTTP host")
            if type(client._transport) is httpx.AsyncHTTPTransport:
                return await fixture.send(client, request, **kwargs)
            if type(client._transport) is httpx.MockTransport:
                return await original_httpx_send(client, request, **kwargs)
            raise AssertionError("unexpected synthetic HTTP transport")

        # Fixture.__init__ installs its own legacy interceptor. Replace it with
        # this single explicit host/transport router before any fixture request.
        monkeypatch.setattr(httpx.AsyncClient, "send", dispatch)
        state = SimpleNamespace(
            worker=h.worker,
            fixture=fixture,
            book=book,
            publication=publication,
            strategy=h.assembly.strategy,
            path=model_root,
            signal_market_clients=[],
        )
        # Establish all policy and approval state before restored monitoring
        # captures its owner snapshot. The post-admission path below is read only.
        snapshot = book.read()
        draft = snapshot["policy"]
        draft["risk_v2"].update(max_quote_age_seconds="120", max_evidence_age_seconds="300")
        book.save_v2(snapshot["revision"], draft, expected_strategy_generation=1,
            policy_sections_version=1)
        scope = signal_support.approved_fixture(
            state, raw_config=signal_support.config_bytes(signal_ttl_seconds=300)
        )
        h.worker.max_age = timedelta(minutes=5)

        # This runs the real restored worker/assembly path and issues its current
        # actual-fee T4 receipt using only restored_monitoring_io_support.IO.
        await admit(h)
        client = h.assembly.execution_client
        assert type(client) is MonitoringBybitExecutionClient
        assert client._metadata_profile == ACTUAL_ACCOUNT_FEE_V1
        receipt = client._native_instrument_receipt_v1
        assert receipt is not None
        target = client._monitor_target
        assert target is not None
        native_view = h.session.require_native_instrument_receipt_v1(
            receipt,
            binding=h.worker.binding,
            scope=h.worker.scope,
            target=target,
        )
        assert type(native_view) is NativeInstrumentReceiptViewV1
        assert len(native_view.evidence) == 1
        native_evidence, = native_view.evidence
        assert native_evidence.fee is not None and native_evidence.fee.signed is True

        try:
            # The existing helper writes policy snapshots, which is forbidden
            # after MonitoringResume has captured this worker's owner. Repeat
            # its source → signal → plan steps directly with the exact pre-admit
            # policy/publication state established above.
            market, _bars = signal_support.install(state)
            fixture.mode = 3
            read = await h.worker.capture_entry_sources_v2(
                scope=scope,
                profile="DEMO_UTA1_V1",
                nonvip_fee_basis=fixture.basis,
                nonvip_applicability_revision="fixture-fee-v1",
                budget=MonitoringReadBudget(),
            )
            signal_handle = await h.worker.capture_builtin_signal_v1(scope=scope, market=market)
            signal = h.worker.consume_builtin_signal_v1(signal_handle)
            entry_owner = h.worker._entry_model_owner_v2
            entry_slot = entry_owner._assembler._current
            signal_issued = h.worker._strategy_signal_owner_v1._current
            assert entry_owner._read is read and entry_slot is not None
            assert entry_slot.result is read and signal_issued.entry_read is read
            assert signal_issued.entry_read_slot is entry_slot
            assert signal_issued.catalog_receipt is entry_slot.catalog_receipt
            assert fixture.catalog._current is entry_slot.catalog_current
            inputs, domain = source_support.economic_inputs(
                read,
                book,
                fixture.clock(),
                scope=scope,
                side=signal.direction.side,
                entry_price=Decimal(104),
                stop_price=Decimal(100),
                lifetime_seconds=120,
            )
            domain = replace(domain, source_generation=entry_owner._journal_epoch)
            inputs = replace(inputs, risk_domain_sha256=domain_v2.risk_domain_sha256_v2(domain))
            plan_handle = h.worker.prepare_entry_plan_v2(
                inputs=inputs,
                domain=domain,
                policy_revision=signal.policy_revision,
                publication_generation=signal.publication_generation,
            )
            assert type(plan_handle) is WorkerModelPlanV2
            prepared = h.worker._entry_model_owner_v2._current[1]
            source_slot = h.worker._entry_model_owner_v2._assembler._current
            assert source_slot is not None and source_slot.result is prepared.source_read
            assert prepared.source_read is h.worker._entry_model_owner_v2._read
            assert signal.scope.instrument == InstrumentKey("BYBIT", "LINEAR", "BTCUSDT")

            # Confirm the same T4 handle remains current after preparing the
            # signal and plan. Model reads do not substitute a DEMO/native label.
            assert h.session.require_native_instrument_receipt_v1(
                receipt,
                binding=h.worker.binding,
                scope=h.worker.scope,
                target=target,
            ) is native_view

            fixture_requests = tuple(fixture.requests)
            restored_requests = tuple(h.io.sends)
            commands = h.worker.runtime.journal.list_commands()
            submissions = tuple(h.assembly.bridge._submissions)
            native_command_count = h.assembly.node.kernel.exec_engine.command_count

            pair = h.worker.snapshot_builtin_signal_risk_instrument_pair_v1(
                signal_handle, plan_handle
            )

            assert pair.status == "OBSERVED_PAIR_UNMAPPED"
            assert pair.financial_authority is False and pair.trading_enabled is False
            assert pair.signal_id == signal.signal_id
            assert pair.signal_catalog_revision == signal.catalog_revision
            assert pair.plan_catalog_revision == prepared.inputs.risk_inputs.catalog_revision
            assert pair.public_catalog_revision == prepared.source_read.catalog.revision
            assert pair.policy_revision == signal.policy_revision
            assert pair.publication_generation == signal.publication_generation
            assert pair.instrument == signal.scope.instrument
            assert pair.public_source_environment == "PUBLIC_MAINNET"
            public_view = fixture.catalog._risk_current(
                source_slot.catalog_receipt,
                expected_revision=signal.catalog_revision,
            )
            public_observation = next(
                item for item in public_view.catalog.observations
                if item.key == signal.scope.instrument
            )
            assert pair.public_source_id == public_view.source_id
            assert pair.public_endpoint == public_observation.source.endpoint
            assert pair.public_specification_revision == public_observation.specification_revision
            assert pair.public_raw_row_sha256 == public_observation.raw_row_sha256
            assert pair.public_response_time == public_observation.response_time
            assert pair.public_received_at == public_observation.source.received_at
            assert pair.demo_origin == HttpsOrigin("https", "api-demo.bybit.com", 443)
            assert pair.demo_target_fingerprint == native_view.target_fingerprint
            assert pair.demo_native_specification_revision == native_evidence.projection.specification_revision
            assert pair.demo_native_semantics_sha256 == native_evidence.native_semantics_sha256
            assert pair.demo_receipt_issued_at == native_view.issued_at
            assert not hasattr(pair, "receipt") and not hasattr(pair, "fee")

            # The join is a pure currentness snapshot: no extra reads, command
            # mutation, native command, or order submission occurs here.
            assert tuple(fixture.requests) == fixture_requests
            assert tuple(h.io.sends) == restored_requests
            assert h.worker.runtime.journal.list_commands() == commands
            assert h.worker.runtime.journal.list_commands() == ()
            assert tuple(h.assembly.bridge._submissions) == submissions == ()
            assert h.assembly.node.kernel.exec_engine.command_count == native_command_count
            assert all(request.method == "GET" for request in fixture.requests)
            assert {request.url.host for request in fixture.requests} <= {
                "api-demo.bybit.com", "api.bybit.com"
            }
        finally:
            for market_client in state.signal_market_clients:
                await market_client.aclose()
            await fixture.close()
