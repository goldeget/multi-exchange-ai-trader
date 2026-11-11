"""Same-worker horizon block preserves the actual-fee restored T4 receipt.

Every Bybit HTTP request in this test is handled by the existing synthetic
restored/source fixtures. The horizon block must not cause extra network or order I/O.
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

from multi_exchange_trader.execution import risk_domain_v2 as domain_v2
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
from multi_exchange_trader.execution.native_instrument_receipts import (
    NativeInstrumentReceiptViewV1,
)
from multi_exchange_trader.execution.native_monitor_client import MonitoringBybitExecutionClient
from multi_exchange_trader.execution.observation_instrument_v1 import ACTUAL_ACCOUNT_FEE_V1
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected
from multi_exchange_trader.trading_product import InstrumentKey


@pytest.mark.asyncio
async def test_same_worker_horizon_block_preserves_actual_fee_t4_receipt(
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
        # Keep the synthetic account snapshot coherent with economic_inputs:
        # 1000 USDT equity and free margin, with no held margin or liabilities.
        h.io.wallet["coin"][0].update({
            "walletBalance": "1000",
            "equity": "1000",
            "totalPositionIM": "0",
            "totalOrderIM": "0",
            "locked": "0",
            "bonus": "0",
            "borrowAmount": "0",
            "spotBorrow": "0",
            "accruedInterest": "0",
            "unrealisedPnl": "0",
            "totalPositionMM": "0",
        })

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
            h.io.account_mode = 3
            source_journal = h.worker.runtime.journal.list_commands()
            source_submissions = tuple(h.assembly.bridge._submissions)
            source_native_commands = h.assembly.node.kernel.exec_engine.command_count
            read = await h.worker.capture_entry_sources_v2(
                scope=scope,
                profile="DEMO_UTA1_V1",
                nonvip_fee_basis=fixture.basis,
                nonvip_applicability_revision="fixture-fee-v1",
                budget=MonitoringReadBudget(),
            )
            assert h.worker.runtime.journal.list_commands() == source_journal == ()
            assert tuple(h.assembly.bridge._submissions) == source_submissions == ()
            assert h.assembly.node.kernel.exec_engine.command_count == source_native_commands
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
            entry_owner = h.worker._entry_model_owner_v2
            source_slot = entry_owner._assembler._current
            assert source_slot is not None and source_slot.result is read
            assert read is entry_owner._read
            assert signal.scope.instrument == InstrumentKey("BYBIT", "LINEAR", "BTCUSDT")

            fixture_requests = tuple(fixture.requests)
            restored_requests = tuple(h.io.sends)
            commands = h.worker.runtime.journal.list_commands()
            submissions = tuple(h.assembly.bridge._submissions)
            native_command_count = h.assembly.node.kernel.exec_engine.command_count
            with pytest.raises(RiskV2Rejected, match="^RISK_V2_HOLDING_HORIZON_REQUIRED$"):
                h.worker.prepare_entry_plan_v2(
                    inputs=inputs,
                    domain=domain,
                    policy_revision=signal.policy_revision,
                    publication_generation=signal.publication_generation,
                )
            assert entry_owner._current is None and entry_owner._producer._current is None
            assert entry_owner._risk_assessment_current is None
            assert entry_owner._read is read and entry_owner._assembler._current is source_slot
            assert h.worker._entry_publication_frame_v2 is None

            # Confirm the same T4 handle remains current after the blocked plan
            # attempt. Model reads do not substitute a DEMO/native label.
            current_view = h.session.require_native_instrument_receipt_v1(
                receipt,
                binding=h.worker.binding,
                scope=h.worker.scope,
                target=target,
            )
            assert current_view is native_view

            capture = client._native_instrument_capture_v1
            registry = h.session._native_instrument_receipts_v1
            registry_slot = registry._slot
            identity_checks = (
                ("current_view_is_native_view", current_view is native_view),
                ("registry_slot_is_capture", registry_slot is capture),
                ("registry_slot_view_is_native_view",
                    registry_slot is not None and registry_slot.view is native_view),
                ("registry_slot_handle_is_receipt",
                    registry_slot is not None and registry_slot.handle is receipt),
                ("registry_slot_target_is_target",
                    registry_slot is not None and registry_slot.target is target),
                ("capture_handle_is_receipt", capture is not None and capture.handle is receipt),
                ("capture_view_is_native_view", capture is not None and capture.view is native_view),
                ("capture_target_is_target", capture is not None and capture.target is target),
                ("capture_owner_is_session", capture is not None and capture.owner is h.session),
                ("capture_assembly_is_worker_assembly",
                    capture is not None and capture.assembly is h.assembly),
                ("capture_channel_is_assembly_channel",
                    capture is not None and capture.channel is h.assembly.channel),
                ("capture_client_is_assembly_client",
                    capture is not None and capture.client is client),
                ("capture_adapter_is_client_adapter",
                    capture is not None and capture.adapter is client._http_client),
                ("capture_provider_is_client_provider",
                    capture is not None and capture.provider is client.bybit_instrument_provider),
                ("capture_cache_is_node_cache",
                    capture is not None and capture.cache is h.assembly.node.cache),
                ("client_capture_is_capture", client._native_instrument_capture_v1 is capture),
                ("client_receipt_is_receipt", client._native_instrument_receipt_v1 is receipt),
                ("client_target_is_target", client._monitor_target is target),
                ("assembly_client_is_client", h.assembly.execution_client is client),
            )
            failed_identity_links = tuple(label for label, passed in identity_checks if not passed)
            assert not failed_identity_links, failed_identity_links

            # The horizon gate publishes no plan and performs no additional
            # reads, command mutation, native command, or order submission.
            assert tuple(fixture.requests) == fixture_requests
            assert tuple(h.io.sends) == restored_requests
            assert h.worker.runtime.journal.list_commands() == commands == ()
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
