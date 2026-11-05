"""Fifteen actual restored-monitor contracts; only isolated Linux qualifies them.

Every worker uses production construction, original stream parsing, engines,
portfolio, SQLite quarantine and shutdown. The two synthetic seams are HTTP send
and generic WebSocket dial. Ordinary collection/Windows skips prove no lifecycle.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib
import inspect
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import httpx
import pytest
from fixtures.runtime_policy_v1 import commit_policy_change
from restored_monitoring_io_support import (
    KEY,
    SCOPE,
    SECRET,
    admit,
    assert_liabilities,
    cleanup_contract,
    deliver,
    eventually,
    make_worker,
    milliseconds,
    restored_case,
)
from test_bybit_native_linux import isolated_native_runtime, module  # noqa: F401

from multi_exchange_trader.account.bybit_readonly import BybitReadError
from multi_exchange_trader.execution.account_binding import BybitDemoAccountSession
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
from multi_exchange_trader.execution.monitoring_models import MonitoringError
from multi_exchange_trader.execution.monitoring_receipts import AuthenticatedMonitoringReceipt
from multi_exchange_trader.execution.native_restart_store import decode_document
from multi_exchange_trader.execution.native_instrument_receipts import (
    NativeUnknownFeeInstrumentReceiptV1,
    NativeUnknownFeeInstrumentReceiptViewV1,
    native_instrument_semantics_sha256,
)
from multi_exchange_trader.execution.observation_instrument_v1 import (
    SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
    observation_metadata_marker_v1,
)
from multi_exchange_trader.execution.runtime import RuntimeBlocked
from multi_exchange_trader.market_data.instrument_definition import LinearInstrumentSpec


async def test_actual_restored_lifecycle_and_one_shot_admission(tmp_path, monkeypatch):
    async with restored_case(tmp_path, monkeypatch, filled=True) as h:
        cache = h.assembly.node.cache
        order = cache.order(h.order_id)
        position = cache.position_for_order(h.order_id)
        instrument = cache.instrument(order.instrument_id)
        before = order.to_dict(), position.to_dict(), instrument.to_dict(instrument)
        client = h.assembly.execution_client
        adapter = client._http_client
        metadata_failure_paths = []
        original_fault = adapter._fault
        from multi_exchange_trader.execution import native_instrument_receipts

        original_deny = native_instrument_receipts._deny

        def capture_active_failure(stage):
            active = sys.exception()
            if active is None:
                return
            safe_frames = tuple(
                (
                    frame.f_code.co_filename.replace("\\", "/").rsplit("/", 1)[-1],
                    line_number,
                    frame.f_code.co_name,
                )
                for frame, line_number in traceback.walk_tb(active.__traceback__)
            )[-8:]
            exception_type = f"{type(active).__module__}.{type(active).__qualname__}"
            metadata_failure_paths.append((stage, exception_type, safe_frames))

        def capture_safe_failure_path(code):
            if code == "MONITORING_METADATA_INVALID":
                capture_active_failure("metadata-boundary")
            elif code == "MONITORING_HTTP_FAILED":
                capture_active_failure("native-http-boundary")
            original_fault(code)

        def capture_native_mismatch(code):
            if code == "MONITORING_INSTRUMENT_NATIVE_MISMATCH":
                active = sys.exception()
                if active is not None and not isinstance(active, MonitoringError):
                    capture_active_failure("native-projection-validation")
            original_deny(code)

        monkeypatch.setattr(adapter, "_fault", capture_safe_failure_path)
        monkeypatch.setattr(native_instrument_receipts, "_deny", capture_native_mismatch)
        try:
            status = await admit(h)
        except Exception as error:
            detail = metadata_failure_paths or [(type(error).__name__, ())]
            pytest.fail(f"restored native admission failed; safe source path={detail!r}", pytrace=False)
        client = h.assembly.execution_client
        receipt = client._native_instrument_receipt_v1
        slot = client._native_instrument_capture_v1
        assert receipt is not None
        assert slot is not None
        view = h.session.require_native_instrument_receipt_v1(
            receipt, binding=h.worker.binding, scope=SCOPE, target=slot.target
        )
        assert view.symbols == ("BTCUSDT",)
        assert len(view.evidence) == 1
        evidence = view.evidence[0]
        assert evidence.envelope.key.symbol == "BTCUSDT"
        assert evidence.envelope.sent_origin.host == "api-demo.bybit.com"
        assert evidence.envelope.response_origin == evidence.envelope.sent_origin
        assert evidence.envelope.response_status == 200
        assert evidence.envelope.decoded_body_sha256 == hashlib.sha256(
            evidence.envelope.decoded_body_bytes
        ).hexdigest()
        spec = evidence.projection.spec
        assert type(spec) is LinearInstrumentSpec
        assert spec.max_limit_quantity.raw == "100.000"
        assert spec.max_market_quantity.raw == "80.000"
        assert spec.source_price_scale == 2
        assert spec.max_leverage.raw == "75"
        assert instrument.max_quantity.as_decimal() == Decimal("100.000")
        assert instrument.margin_init == instrument.margin_maint == Decimal("0.1")
        assert evidence.fee.path == "/v5/account/fee-rate"
        assert evidence.fee.query == (("category", "linear"), ("symbol", "BTCUSDT"))
        assert evidence.fee.signed is True
        assert evidence.fee.origin == evidence.envelope.sent_origin
        assert evidence.fee.response_status == 200
        assert not hasattr(evidence.fee, "raw_body")
        _fee_generation, fee_slot = h.session._linear_private_state_v1("FEE")
        assert fee_slot is not None
        assert fee_slot.capture.raw_body_sha256 == evidence.fee.body_sha256
        assert fee_slot.capture.request_started_at == evidence.fee.request_started_at
        assert fee_slot.capture.received_at == evidence.fee.received_at
        assert type(h.assembly.node) is module("live.node").TradingNode
        assert type(position) is module("model.position").Position
        assert h.assembly.strategy.cache is cache
        assert status.reconciled_at and status.stream_stamp.received == status.stream_stamp.delivered
        assert order.is_closed and position.quantity.as_decimal() == Decimal(1)
        assert (order.to_dict(), position.to_dict(), instrument.to_dict(instrument)) == before
        assert [event.to_dict(event) for event in order.events] == h.original_events
        assert cache.position_id(h.order_id) == order.position_id == position.id
        assert cache.positions_open() == [position] and cache.orders_closed() == [order]
        assert_liabilities(h)
        account = cache.account(h.assembly.execution_client.account_id)
        assert account.base_currency is None
        assert account.last_event.ts_event >= h.io.ledger.initialized_ns
        assert account.last_event is h.assembly.execution_client._monitor_account_event
        balance = account.last_event.balances[0]
        assert (balance.total.as_decimal(), balance.free.as_decimal()) == (Decimal(98), Decimal(95))
        assert h.session._monitoring_receipts == {}
        with pytest.raises((ValueError, RuntimeError)):
            await h.worker.resume_restored_monitoring()
        with pytest.raises(FrozenInstanceError):
            status.trading_enabled = True


async def test_unknown_fee_restored_uses_public_demo_envelope_and_observation_receipt(
    tmp_path, monkeypatch
):
    async with restored_case(
        tmp_path,
        monkeypatch,
        metadata_profile=SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
    ) as h:
        sends_before = len(h.io.sends)
        status = await admit(h)
        client = h.assembly.execution_client
        slot = client._native_instrument_capture_v1
        receipt = client._native_instrument_receipt_v1
        assert slot is not None and receipt is not None
        assert slot.metadata_profile == SDK1231_OBSERVATION_UNKNOWN_FEES_V1
        assert type(receipt) is NativeUnknownFeeInstrumentReceiptV1
        view = h.session.require_native_unknown_fee_instrument_receipt_v1(
            receipt,
            binding=h.worker.binding,
            scope=SCOPE,
            target=slot.target,
        )
        assert type(view) is NativeUnknownFeeInstrumentReceiptViewV1
        assert view.metadata_profile == SDK1231_OBSERVATION_UNKNOWN_FEES_V1
        assert view.symbols == ("BTCUSDT",) and len(view.evidence) == 1
        evidence = view.evidence[0]
        assert evidence.metadata_profile == SDK1231_OBSERVATION_UNKNOWN_FEES_V1
        assert not hasattr(evidence, "fee")
        assert evidence.envelope.key.symbol == "BTCUSDT"
        assert evidence.envelope.sent_origin.host == "api-demo.bybit.com"
        assert evidence.envelope.response_origin == evidence.envelope.sent_origin
        assert evidence.envelope.response_status == 200
        assert evidence.envelope.decoded_body_sha256 == hashlib.sha256(
            evidence.envelope.decoded_body_bytes
        ).hexdigest()
        from nautilus_trader.model.identifiers import InstrumentId

        instrument_id = InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT")
        instrument = h.assembly.node.cache.instrument(instrument_id)
        spec = evidence.projection.spec
        assert type(spec) is LinearInstrumentSpec
        assert spec.max_limit_quantity.raw == "100.000"
        assert spec.max_market_quantity.raw == "80.000"
        assert spec.source_price_scale == 2
        assert spec.max_leverage.raw == "75"
        assert instrument.max_quantity.as_decimal() == Decimal("100.000")
        assert instrument.price_increment.as_decimal() == Decimal("0.10")
        assert (
            native_instrument_semantics_sha256(instrument)
            == evidence.native_semantics_sha256
        )
        assert instrument.info == observation_metadata_marker_v1()
        assert instrument.maker_fee == instrument.taker_fee == Decimal("0.001")
        assert slot.fees == {} and slot.evidence == {}
        assert set(slot.unknown_fee_evidence) == {"BTCUSDT"}
        assert slot.last_logical - slot.logical_start == 2  # one instrument + one wallet GET
        assert slot.wallet_reads == 1
        account = h.assembly.node.cache.account(client.account_id)
        assert account.last_event is client._monitor_account_event
        assert not any(path == "/v5/account/fee-rate" for path, _ in h.io.sends)
        assert not any((
            status.trading_enabled,
            status.credit_authorized,
            status.production_supported,
            status.venue_event_complete,
        ))
        assert client._native_instrument_group_closed_v1.is_set()
        assert slot.phase == "ISSUED" and not slot.startup_open

        with pytest.raises(MonitoringError):
            h.session.require_native_unknown_fee_instrument_receipt_v1(
                receipt,
                binding=object(),
                scope=SCOPE,
                target=slot.target,
            )
        assert h.session._native_instrument_receipts_v1._slot is None
        with pytest.raises(MonitoringError):
            h.session.require_native_unknown_fee_instrument_receipt_v1(
                receipt,
                binding=h.worker.binding,
                scope=SCOPE,
                target=slot.target,
            )
        relevant = [
            path for path, _ in h.io.sends[sends_before:]
            if path in (
                "/v5/market/instruments-info",
                "/v5/account/fee-rate",
                "/v5/account/wallet-balance",
            )
        ]
        assert relevant.count("/v5/market/instruments-info") == 1
        assert "/v5/account/fee-rate" not in relevant
        assert relevant.count("/v5/account/wallet-balance") == 1


async def test_unknown_fee_provider_or_cache_marker_corruption_blocks_wallet_and_stream(
    tmp_path, monkeypatch
):
    from nautilus_trader.model.identifiers import InstrumentId

    for corruption in ("provider", "cache"):
        with monkeypatch.context() as case_patch:
            async with restored_case(
                tmp_path / corruption,
                case_patch,
                metadata_profile=SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
            ) as h:
                client_type = importlib.import_module(
                    "multi_exchange_trader.execution.native_monitor_client"
                ).MonitoringBybitExecutionClient
                base = module("adapters.bybit.execution").BybitExecutionClient
                send_start = len(h.io.sends)
                mutated = {"value": False}
                if corruption == "provider":
                    original_cache = client_type._cache_instruments

                    async def mutate_provider(client):
                        if client._metadata_profile == SDK1231_OBSERVATION_UNKNOWN_FEES_V1:
                            instrument = next(
                                iter(client.bybit_instrument_provider.get_all().values())
                            )
                            instrument.info["financial_use"] = "ALLOWED"
                            mutated["value"] = True
                        await original_cache(client)

                    case_patch.setattr(client_type, "_cache_instruments", mutate_provider)
                else:
                    original_cache = base._cache_instruments

                    async def cache_then_mutate(client):
                        await original_cache(client)
                        if client._metadata_profile == SDK1231_OBSERVATION_UNKNOWN_FEES_V1:
                            cached = client._cache.instrument(
                                InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT")
                            )
                            cached.info["financial_use"] = "ALLOWED"
                            mutated["value"] = True

                    case_patch.setattr(base, "_cache_instruments", cache_then_mutate)

                with pytest.raises((MonitoringError, ValueError, RuntimeError)):
                    async with asyncio.timeout(30):
                        await h.worker.resume_restored_monitoring()
                assert mutated["value"]
                observed_paths = [path for path, _params in h.io.sends[send_start:]]
                assert observed_paths.count("/v5/market/instruments-info") == 1
                assert "/v5/account/fee-rate" not in observed_paths
                assert "/v5/account/wallet-balance" not in observed_paths
                assert (
                    h.assembly.execution_client.monitoring_fault
                    == "MONITORING_NATIVE_CONNECT_FAILED"
                )
                assert h.assembly.execution_client._native_instrument_receipt_v1 is None
                assert h.session._native_instrument_receipts_v1._slot is None
                assert not h.io.wires


async def test_issued_t4_receipt_rejects_extra_native_get_before_send(tmp_path, monkeypatch):
    async with restored_case(tmp_path, monkeypatch) as h:
        await admit(h)
        client = h.assembly.execution_client
        before = len(h.io.sends)
        with pytest.raises(MonitoringError):
            await client._http_client._get(
                "/v5/account/fee-rate",
                [("category", "linear"), ("symbol", "BTCUSDT")],
                signed=True,
            )
        assert len(h.io.sends) == before
        assert client._native_instrument_receipt_v1 is None
        assert client.monitoring_fault


async def test_readonly_binding_without_capital_or_trading_permission(tmp_path, monkeypatch):
    async with restored_case(tmp_path / "readonly", monkeypatch) as h:
        policy = h.book.read()
        changed = commit_policy_change(h.book, {**policy["policy"], "capital_usdt": None})
        assert changed["revision"] == policy["revision"] + 1
        assert h.book.read()["policy"]["capital_usdt"] is None
        identity = h.session.require_observation_binding(h.worker.binding, scope=SCOPE)
        assert identity.read_only and not identity.has_permission("Wallet", "Capital")
        with pytest.raises(ValueError):
            h.session.require_execution_binding(h.worker.binding, scope=SCOPE)
        instrument = h.assembly.node.cache.instrument(h.assembly.node.cache.order(h.order_id).instrument_id)
        assert instrument.maker_fee == Decimal("0.0002") and instrument.taker_fee == Decimal("0.00055")
        assert instrument.margin_init == instrument.margin_maint == Decimal("0.1")
        assert instrument.size_increment.as_decimal() == Decimal("0.001")
        await admit(h)
        assert h.assembly.node.cache.instrument(instrument.id) is instrument
        assert_liabilities(h)
    async with restored_case(tmp_path / "drift", monkeypatch) as h:
        h.io.fee["makerFeeRate"] = "0.0003"
        with pytest.raises((ValueError, RuntimeError)):
            await h.worker.resume_restored_monitoring()
        assert not h.io.wires
        assert h.assembly.execution_client.monitoring_fault
    injected = BybitDemoAccountSession(api_key=KEY, api_secret=SECRET,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request,
            json={"retCode": 0, "time": milliseconds(), "result": {}})))
    try:
        binding = await injected.authenticate(expected_uid="12345", expected_parent_uid="0", expected_is_master=False)
        with pytest.raises(ValueError):
            injected.require_observation_binding(binding, scope=SCOPE)
    finally:
        await injected.aclose()


async def test_forged_stale_phase_and_budget_receipts_cannot_admit(tmp_path, monkeypatch):
    async with restored_case(tmp_path / "issuer", monkeypatch) as h:
        await admit(h)
        target, binding = h.worker._monitoring.capture(), h.worker.binding
        receipt = await h.session.read_monitoring_receipt(binding, target=target, phase="POST")
        foreign = await make_worker(tmp_path / "foreign", h.io, h)
        try:
            for forged in (object(), object.__new__(AuthenticatedMonitoringReceipt)):
                with pytest.raises(ValueError):
                    h.session.require_monitoring_receipt(forged, binding=binding, target=target, phase="POST")
            with pytest.raises((ValueError, TypeError)):
                copy.copy(receipt)
            for owner, current_binding, current_target, phase in (
                (h.session, binding, target, "PRE"), (foreign.session, foreign.worker.binding, target, "POST"),
                (h.session, binding, replace(target), "POST"),
                (h.session, object.__new__(type(binding)), target, "POST"),
            ):
                with pytest.raises(ValueError):
                    owner.require_monitoring_receipt(receipt, binding=current_binding, target=current_target, phase=phase)
            h.session.consume_monitoring_receipt(receipt, binding=binding, target=target, phase="POST")
            with pytest.raises(ValueError):
                h.session.consume_monitoring_receipt(receipt, binding=binding, target=target, phase="POST")
            receipts = [await h.session.read_monitoring_receipt(binding, target=target, phase="POST") for _ in range(15)]
            capacity_gate = h.io.gate()

            async def pending_response(path, params, response):
                if path == "/v5/position/list":
                    await capacity_gate.wait()

            h.io.on_http = pending_response
            pending = asyncio.create_task(h.session.read_monitoring_receipt(binding, target=target, phase="POST"))
            try:
                await asyncio.wait_for(capacity_gate.entered.wait(), 5)
                count = len(h.io.sends)
                with pytest.raises(ValueError, match="CAPACITY"):
                    await h.session.read_monitoring_receipt(binding, target=target, phase="POST")
                assert len(h.io.sends) == count
                capacity_gate.release.set()
                receipts.append(await pending)
            finally:
                capacity_gate.release.set()
                h.io.on_http = None
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            for issued in receipts:
                h.session.discard_monitoring_receipt(issued)
            client, budget = h.session.read_client(binding, scope=SCOPE), MonitoringReadBudget()
            count = len(h.io.sends)
            with h.session._monitoring_operation(budget):
                for _ in range(200):
                    await client.read("/v5/market/time", [], signed=False)
                with pytest.raises(ValueError, match="PHYSICAL"):
                    await client.read("/v5/market/time", [], signed=False)
            assert len(h.io.sends) - count == budget.physical_attempts == 200
            retry_budget = MonitoringReadBudget()

            async def unavailable(path, params, response):
                return 503, {}

            h.io.on_http = unavailable
            count = len(h.io.sends)
            with h.session._monitoring_operation(retry_budget), pytest.raises(ValueError):
                await client.read("/v5/market/time", [], signed=False)
            assert len(h.io.sends) - count == retry_budget.physical_attempts == 3
            h.io.on_http = None
            logical = MonitoringReadBudget()
            count = len(h.io.sends)
            with h.session._monitoring_operation(logical), logical.receipt():
                for _ in range(32):
                    await client.read("/v5/market/time", [], signed=False)
                with pytest.raises(ValueError, match="LOGICAL"):
                    await client.read("/v5/market/time", [], signed=False)
            assert len(h.io.sends) - count == 32
            # Real timeout; do not shorten the production budget or patch clocks.
            held = h.io.gate()

            async def timeout_response(path, params, response):
                await held.wait()

            h.io.on_http = timeout_response
            deadline_budget = MonitoringReadBudget()
            count = len(h.io.sends)
            with h.session._monitoring_operation(deadline_budget), deadline_budget.receipt(), pytest.raises(BybitReadError, match="^timeout$"):
                await client.read("/v5/market/time", [], signed=False)
            held.release.set()
            h.io.on_http = None
            assert len(h.io.sends) - count == 1
        finally:
            await foreign.session.aclose()
    async with restored_case(tmp_path / "stale-start", monkeypatch, binding_age=2) as h:
        gate = h.io.gate()

        async def stale(path, params, response):
            if path == "/v5/position/list":
                response["time"] = milliseconds()
                await gate.wait()

        h.io.on_http = stale
        attempt = asyncio.create_task(h.worker.resume_restored_monitoring())
        await gate.entered.wait()
        await asyncio.sleep(2.05)
        gate.release.set()
        with pytest.raises((ValueError, RuntimeError)):
            await attempt
        assert not h.io.wires and not h.session._monitoring_receipts


async def test_all_financial_and_reconciliation_fences(tmp_path, monkeypatch):
    async with restored_case(tmp_path, monkeypatch) as h:
        await admit(h)
        client, cache = h.assembly.execution_client, h.assembly.node.cache
        engine = h.assembly.node.kernel.exec_engine
        assert engine.get_reconciliation_task() is None
        assert engine.get_own_books_audit_task() is None
        assert not set(h.assembly.node.kernel.clock.timer_names) & {
            "ExecEngine_SNAPSHOT_POSITIONS", "ExecEngine_PURGE_CLOSED_ORDERS",
            "ExecEngine_PURGE_CLOSED_POSITIONS", "ExecEngine_PURGE_ACCOUNT_EVENTS",
        }
        assert engine.get_cmd_queue_task() is not None and not engine.get_cmd_queue_task().done()
        assert engine.get_evt_queue_task() is not None and not engine.get_evt_queue_task().done()
        await client._repay_task
        assert client._repay_task.done() and client._repay_queues == {} and client._repay_enqueuers == {}
        before = [event.to_dict(event) for event in cache.order(h.order_id).events]
        sends, commands = len(h.io.sends), h.assembly.node.kernel.exec_engine.command_count
        names = (
            "submit_order", "submit_order_list", "modify_order", "cancel_order", "cancel_all_orders",
            "batch_cancel_orders", "query_account", "query_order", "_submit_order", "_submit_order_list",
            "_submit_order_list_http", "_submit_order_list_ws", "_modify_order", "_cancel_order",
            "_cancel_all_orders", "_batch_cancel_orders", "_query_account", "_query_order",
            "generate_order_status_report", "generate_order_status_reports", "generate_fill_reports",
            "generate_position_status_reports", "generate_mass_status", "set_leverage", "set_position_mode",
            "_handle_borrow_action", "_handle_repay_action", "_repay_spot_borrow_if_needed",
            "_send_fill_report", "_send_order_status_report", "_send_position_status_report",
            "_send_mass_status_report", "generate_order_submitted", "generate_order_filled",
            "generate_order_accepted", "generate_order_canceled", "generate_order_rejected",
            "arbitrary_future_write",
        )
        first = None
        for name in names:
            with pytest.raises(ValueError, match="MONITORING_"):
                result = getattr(client, name)(object())
                if inspect.isawaitable(result):
                    await result
            first = first or client.monitoring_fault
            assert client.monitoring_fault == first
        for transport in (client._http_client, client._ws_private_client, client._ws_trade_client):
            for name in ("submit_order", "cancel_order", "borrow", "repay", "request_order_status", "connect"):
                with pytest.raises(ValueError):
                    result = getattr(transport, name)(object())
                    if inspect.isawaitable(result):
                        await result
        assert [event.to_dict(event) for event in cache.order(h.order_id).events] == before
        assert h.assembly.node.kernel.exec_engine.command_count == commands
        assert len(h.io.sends) == sends and client._repay_queues == {}
        await client._repay_task
        assert h.worker.monitoring_status.phase == "ATTENTION"
        assert_liabilities(h)


async def test_ack_pong_modes_and_transport_epoch_are_required(tmp_path, monkeypatch):
    for defect in ("negative", "duplicate", "wrong-connection", "failed-topics", "missing-auth", "public-pong", "missing-pong"):
        async with restored_case(tmp_path / defect, monkeypatch) as h:
            if defect not in ("missing-auth", "missing-pong"):
                h.cleanup_contract = cleanup_contract(h, "gap")

            async def change(wire, packet, ack, defect=defect):
                if defect == "missing-auth" and packet["op"] == "auth":
                    ack.clear()
                if packet["op"] == "subscribe":
                    if defect == "negative":
                        ack["success"] = False
                    elif defect == "duplicate":
                        wire.raw(ack)
                    elif defect == "wrong-connection":
                        ack["conn_id"] = "foreign"
                    elif defect == "failed-topics":
                        ack["failTopics"] = ["execution.linear"]
                if packet["op"] == "ping":
                    if defect == "public-pong":
                        ack.update(op="ping", success=True, ret_msg="pong")
                    elif defect == "missing-pong":
                        ack.clear()

            h.io.on_send = change
            with pytest.raises((ValueError, RuntimeError)):
                await h.worker.resume_restored_monitoring()
            assert h.worker.monitoring_status.phase != "MONITORING"
            assert set(h.io.wires[0].sent) <= {"auth", "subscribe", "ping"}
    async with restored_case(tmp_path / "sole-pending", monkeypatch) as h:
        async def absent_id(wire, packet, ack):
            if packet["op"] != "ping":
                ack.pop("req_id")
        h.io.on_send = absent_id
        await admit(h)
        assert h.io.wires[0].sent[:3] == ["auth", "subscribe", "ping"]
    for mode in ("inactive", "reconnecting", "disconnecting", "closed", "reconnected"):
        async with restored_case(tmp_path / mode, monkeypatch) as h:
            await admit(h)
            wire = h.io.wires[-1]
            if mode == "reconnected":
                wire.callbacks["post_reconnection"]()
            else:
                wire.mode = mode
            assert h.worker.monitoring_status.phase == "ATTENTION"
            stamp = h.assembly.monitoring_stamp()
            wire.emit("position.linear", [h.io.ledger.position])
            await asyncio.sleep(0)
            assert h.assembly.monitoring_stamp() == stamp
            assert len(h.io.wires) == 1


async def test_original_owned_execution_reaches_native_and_app_quarantine(tmp_path, monkeypatch):
    async with restored_case(tmp_path, monkeypatch) as h:
        await admit(h)
        row = await h.io.ledger.execution("live-original")
        order = await deliver(h, [row])
        fills = [event for event in order.events if type(event) is module("model.events.order").OrderFilled]
        assert len(fills) == 1 and str(fills[0].trade_id) == row["execId"]
        assert fills[0].ts_event == int(row["execTime"]) * 1_000_000
        assert fills[0].last_qty.as_decimal() == Decimal(row["execQty"])
        assert fills[0].commission.as_decimal() == Decimal(row["execFee"])
        position = h.assembly.node.cache.position_for_order(h.order_id)
        assert position.quantity.as_decimal() == Decimal(1)
        assert position.events == fills
        assert_liabilities(h, candidates=1)
        state = h.worker.monitoring_status
        assert state.phase == "MONITORING" and not state.reconciliation_current
        assert state.current_revision != h.worker._monitoring.checkpoint.application.revision


async def test_pending_applied_duplicates_and_original_field_guards(tmp_path, monkeypatch):
    async with restored_case(tmp_path / "batch", monkeypatch) as h:
        await admit(h)
        first = await h.io.ledger.execution("batch-first", "0.400")
        second = await h.io.ledger.execution("batch-second", "0.600")
        # Production waits for native + app ACK per row. Duplicates in this
        # whole batch are evaluated after the earlier original is applied.
        # Queue another raw duplicate at the observable first-native/app gap;
        # this proves pending original + queued INPUT, not pending-ledger dispatch.
        windows = []
        bus = h.assembly.node.kernel.msgbus
        topic = f"events.order.{h.assembly.strategy.id}"

        def observe_pending_original(event):
            if type(event) is not module("model.events.order").OrderFilled:
                return
            reports = h.assembly.execution_client._monitor_reports
            pending = tuple(sorted(key[1] for key in reports._ledger._pending))
            applied = tuple(sorted(key[1] for key in reports._ledger._applied))
            expected = reports._expected.get(id(event))
            unacknowledged = expected is not None and not expected["future"].done()
            before = h.assembly.monitoring_stamp()
            if str(event.trade_id) == "batch-first":
                h.io.wires[-1].emit("execution.linear", [copy.deepcopy(first)])
            after = h.assembly.monitoring_stamp()
            windows.append((str(event.trade_id), pending, applied, unacknowledged,
                len(h.worker.runtime.monitoring_state().candidates),
                h.assembly.node.cache.order(h.order_id).filled_qty.as_decimal(),
                after.received - before.received, after.received > after.delivered))

        bus.subscribe(topic=topic, handler=observe_pending_original)
        try:
            order = await deliver(h, [first, copy.deepcopy(first), second, copy.deepcopy(second)])
        finally:
            bus.unsubscribe(topic=topic, handler=observe_pending_original)
        assert windows == [
            ("batch-first", ("batch-first",), (), True, 0, Decimal("0.4"), 1, True),
            ("batch-second", ("batch-second",), ("batch-first",), True, 1, Decimal(1), 0, True),
        ]
        stamp = h.assembly.monitoring_stamp()
        assert stamp.received == stamp.delivered
        assert not h.assembly.execution_client._monitor_reports._ledger._pending
        assert order.filled_qty.as_decimal() == Decimal(1)
        assert [str(event.trade_id) for event in order.events if hasattr(event, "trade_id")] == ["batch-first", "batch-second"]
        await deliver(h, [first, second])
        assert_liabilities(h, candidates=2)
        assert len(h.assembly.node.cache.position_for_order(h.order_id).events) == 2
        h.cleanup_contract = cleanup_contract(h, "gap")
        h.io.wires[-1].emit("execution.linear", [{**first, "execPrice": "101.00"}])
        await eventually(lambda: h.assembly.execution_client.monitoring_fault is not None)
        assert_liabilities(h, candidates=2)
    for field, value in (("symbol", "ETHUSDT"), ("side", "Sell"), ("orderLinkId", "foreign"),
                        ("orderId", "foreign"), ("execQty", "0.0001"), ("execFee", None),
                        ("feeCurrency", "BTC"), ("execType", "BustTrade"), ("execQty", "2"),
                        ("orderType", "Limit"), ("execTime", "1")):
        async with restored_case(tmp_path / f"{field}-{value}", monkeypatch) as h:
            h.cleanup_contract = cleanup_contract(h, "gap")
            await admit(h)
            row = await h.io.ledger.execution("invalid", apply=False)
            h.io.wires[-1].emit("execution.linear", [{**row, field: value}])
            await eventually(lambda: h.assembly.execution_client.monitoring_fault is not None)
            assert_liabilities(h)
            with pytest.raises((ValueError, RuntimeError)):
                await h.worker.stop()
            stamp = h.assembly.monitoring_stamp()
            assert stamp.received > stamp.delivered
            assert not h.worker._channel_closed and h.worker._lock.acquired
            assert h.worker.monitoring_status.resources_retained
            assert decode_document((h.worker.directory / "native-restart.json").read_bytes())["state"] == "active"
            assert h.assembly.node.cache.order(h.order_id).filled_qty.as_decimal() == 0
    for raw in (b'{"topic":"execution.linear","data":NaN}', b"{" * 1_048_577):
        async with restored_case(tmp_path / str(len(raw)), monkeypatch) as h:
            h.cleanup_contract = cleanup_contract(h, "gap")
            await admit(h)
            h.io.wires[-1].callbacks["handler"](raw)
            await eventually(lambda: h.assembly.execution_client.monitoring_fault is not None)
            assert_liabilities(h)


async def test_partial_terminal_ordering_never_infers_execution(tmp_path, monkeypatch):
    async with restored_case(tmp_path / "terminal-ahead", monkeypatch) as h:
        await admit(h)
        first = await h.io.ledger.execution("ahead-first", "0.400")
        second = await h.io.ledger.execution("ahead-second", "0.600")
        wire = h.io.wires[-1]
        wire.emit("order.linear", [{**copy.deepcopy(h.io.ledger.order), "category": "linear"}])
        # Whole raw batch; the real dispatcher, not this test, serializes ACKs.
        wire.emit("execution.linear", [first, first, second])
        await h.assembly.monitoring_barrier()
        assert h.assembly.node.cache.order(h.order_id).is_closed
        assert_liabilities(h, candidates=2)
        assert len(h.assembly.node.cache.position_for_order(h.order_id).events) == 2
    async with restored_case(tmp_path / "partial", monkeypatch) as h:
        await admit(h)
        partial = await h.io.ledger.execution("partial", "0.400")
        order = await deliver(h, [partial])
        assert order.status.name == "PARTIALLY_FILLED"
        assert_liabilities(h, candidates=1)
        terminal = {**h.io.ledger.order, "category": "linear", "orderStatus": "Filled", "cumExecQty": "1", "leavesQty": "0"}
        h.cleanup_contract = cleanup_contract(h, "gap")
        h.io.wires[-1].emit("order.linear", [terminal])
        await asyncio.sleep(5.1)
        await eventually(lambda: h.assembly.execution_client.monitoring_fault is not None)
        assert order.filled_qty.as_decimal() == Decimal("0.4")
        assert_liabilities(h, candidates=1)
    for change in ({"qty": "2"}, {"orderStatus": "Untriggered"}, {"orderStatus": "Triggered"},
                   {"orderStatus": "Deactivated"}, {"orderLinkId": "external"}, {"triggerPrice": "90"}):
        async with restored_case(tmp_path / str(len(str(change))) / next(iter(change)) / str(next(iter(change.values()))), monkeypatch) as h:
            h.cleanup_contract = cleanup_contract(h, "gap")
            await admit(h)
            h.io.wires[-1].emit("order.linear", [{**h.io.ledger.order, "category": "linear", **change}])
            await eventually(lambda: h.assembly.execution_client.monitoring_fault is not None)
            assert_liabilities(h)


async def test_pre_post_mutation_and_final_delivery_barrier(tmp_path, monkeypatch):
    async with restored_case(tmp_path / "pre-conflict", monkeypatch) as h:
        async def pre_conflict(path, params, response):
            if path == "/v5/order/realtime":
                response["result"]["list"][0]["qty"] = "2"
        h.io.on_http = pre_conflict
        with pytest.raises((ValueError, RuntimeError)):
            await h.worker.resume_restored_monitoring()
        assert not h.io.wires and h.worker._node_task is None
    failures = (
        ("status403", "/v5/position/list"), ("status429", "/v5/position/list"),
        ("status451", "/v5/position/list"), ("zeroquota", "/v5/position/list"),
        ("retCode", "/v5/position/list"), ("region", "/v5/position/list"),
        ("body", "/v5/position/list"), ("list", "/v5/position/list"),
        ("cursor", "/v5/position/list"), ("position", "/v5/position/list"),
        ("qty", "/v5/order/realtime"), ("price", "/v5/order/realtime"),
        ("orderId", "/v5/order/realtime"), ("orderLinkId", "/v5/order/realtime"),
        ("timeInForce", "/v5/order/realtime"), ("execution", "/v5/execution/list"),
    )
    for defect, endpoint, delta in ((defect, endpoint, delta) for defect, endpoint in failures for delta in ("pong", "fill")):
        async with restored_case(tmp_path / defect / delta, monkeypatch) as h:
            observed = []
            order = h.assembly.node.cache.order(h.order_id)

            async def poison(path, params, response, defect=defect, endpoint=endpoint, observed=observed, delta=delta):
                if not h.io.wires or path != endpoint or observed:
                    return None
                before = h.assembly.monitoring_stamp()
                if delta == "pong":
                    await h.assembly.execution_client._monitor_stream._ping()
                    await h.assembly.monitoring_barrier()
                else:
                    await deliver(h, [await h.io.ledger.execution("terminal-response-original")])
                    assert_liabilities(h, candidates=1)
                assert h.assembly.monitoring_stamp().received > before.received
                observed.append(len(h.io.sends))
                result = response["result"]
                if defect.startswith("status"):
                    return int(defect[-3:]), {}
                if defect == "zeroquota":
                    return 200, {"X-Bapi-Limit-Status": "0"}
                if defect in ("retCode", "region"):
                    response["retCode"] = 10006 if defect == "retCode" else 10024
                elif defect == "body":
                    response["result"] = []
                elif defect == "list":
                    result["list"] = {}
                elif defect == "cursor":
                    result["nextPageCursor"] = "bad cursor"
                elif defect == "position":
                    result["list"][0]["positionIdx"] = 1
                elif defect == "execution":
                    result["list"] = [{"symbol": "BTCUSDT", "execQty": "NaN"}]
                else:
                    values = {"qty": "2", "price": "101", "orderId": "foreign",
                        "orderLinkId": "foreign", "timeInForce": "FOK"}
                    result["list"][0][defect] = values[defect]
                return None

            h.io.on_http = poison
            with pytest.raises((ValueError, RuntimeError)):
                await h.worker.resume_restored_monitoring()
            assert len(observed) == 1 and len(h.io.sends) == observed[0]
            assert h.worker.monitoring_status.phase != "MONITORING"
            assert not h.session._monitoring_receipts
            if delta == "fill":
                assert order.filled_qty.as_decimal() == 1
                assert str(order.last_event.trade_id) == "terminal-response-original"
    for mutation in ("pong", "fill", "three-posts"):
        async with restored_case(tmp_path / mutation, monkeypatch) as h:
            hits = []
            windows = []
            bus = h.assembly.node.kernel.msgbus
            topic = f"events.order.{h.assembly.strategy.id}"

            def observe_native_before_app(event, windows=windows):
                if type(event) is module("model.events.order").OrderFilled:
                    windows.append((h.worker._monitoring.state.phase,
                        len(h.worker.runtime.monitoring_state().candidates),
                        h.assembly.node.cache.order(h.order_id).filled_qty.as_decimal()))

            bus.subscribe(topic=topic, handler=observe_native_before_app)

            async def evolve(path, params, response, mutation=mutation, hits=hits):
                if path != "/v5/position/list" or not h.io.wires or (hits and mutation != "three-posts"):
                    return
                hits.append(len(h.io.sends))
                if mutation == "fill":
                    first = await h.io.ledger.execution("retarget-first", "0.400")
                    second = await h.io.ledger.execution("retarget-second", "0.600")
                    h.io.wires[-1].emit("execution.linear", [first, first, second])
                    # Release HTTP before either native queue or app callback settles.
                else:
                    await h.assembly.execution_client._monitor_stream._ping()
                    await h.assembly.monitoring_barrier()

            h.io.on_http = evolve
            try:
                if mutation == "three-posts":
                    with pytest.raises((ValueError, RuntimeError)):
                        await h.worker.resume_restored_monitoring()
                    assert len(hits) == 3
                else:
                    await admit(h)
                    assert len(hits) == 1
                    assert_liabilities(h, candidates=2 if mutation == "fill" else 0)
                    stamp = h.assembly.monitoring_stamp()
                    assert stamp.received == stamp.delivered
                    if mutation == "fill":
                        # Each genuine row has its own native-before-app window;
                        # serialization deliberately prevents multiple native rows
                        # advancing ahead of the first application's ACK.
                        assert windows == [("CONNECTING", 0, Decimal("0.4")), ("CONNECTING", 1, Decimal(1))]
            finally:
                bus.unsubscribe(topic=topic, handler=observe_native_before_app)


async def test_cancel_stop_reauthentication_and_lease_fences(tmp_path, monkeypatch):
    for phase in ("PRE", "CONNECT", "POST"):
        for action in ("cancel", "stop", "reauth", "close", "lease"):
            async with restored_case(tmp_path / phase / action, monkeypatch) as h:
                if action == "lease":
                    h.cleanup_contract = cleanup_contract(h, "lease")
                gate = h.io.gate()

                async def hold(path, params, response, phase=phase, gate=gate):
                    if (phase == "PRE" and path == "/v5/position/list" and not h.io.wires
                            or phase == "CONNECT" and path == "/v5/account/wallet-balance"
                            or phase == "POST" and path == "/v5/position/list" and h.io.wires):
                        await gate.wait()

                h.io.on_http = hold
                task = asyncio.create_task(h.worker.resume_restored_monitoring())
                await asyncio.wait_for(gate.entered.wait(), 12)
                lock, acquisition = h.worker._lock, h.worker._lock.acquisition_id
                if action == "cancel":
                    task.cancel()
                elif action == "stop":
                    with pytest.raises(RuntimeBlocked, match="^AWAIT_INFLIGHT_OPERATION_BEFORE_STOP$"):
                        await h.worker.stop()
                elif action == "reauth":
                    await h.session.authenticate(expected_uid="12345", expected_parent_uid="0", expected_is_master=False)
                elif action == "close":
                    await h.session.aclose()
                else:
                    lock.release()
                    lock.acquire()
                    assert lock.acquisition_id != acquisition
                gate.release.set()
                with pytest.raises((ValueError, RuntimeError, asyncio.CancelledError)):
                    await task
                assert h.worker.monitoring_status.phase != "MONITORING"
                assert not h.session._monitoring_receipts
                if action == "lease":
                    assert lock.acquired and not h.worker._channel_closed
                    assert h.worker.monitoring_status.resources_retained
    for attribute in ("_store", "_runtime", "_assembly", "_monitoring", "lease"):
        async with restored_case(tmp_path / "last-continuation" / attribute, monkeypatch) as h:
            await admit(h)
            worker = h.worker
            original = worker._lock.acquisition_id if attribute == "lease" else getattr(worker, attribute)
            if attribute == "lease":
                h.cleanup_contract = cleanup_contract(h, "lease")
            replaced = asyncio.Event()
            holder = []

            async def replace_at_final_yield(attribute=attribute, original=original, worker=worker,
                                            replaced=replaced, holder=holder):
                # Observe the genuine coroutine's final sleep continuation. This
                # schedules an owner mutation; it does not replace any await/guard.
                while not replaced.is_set():
                    await asyncio.sleep(0)
                    if not holder:
                        continue
                    stopping = holder[0]
                    coroutine = stopping.get_coro()
                    while coroutine is not None:
                        code = getattr(coroutine, "cr_code", None)
                        child = getattr(coroutine, "cr_await", None)
                        child_code = getattr(child, "cr_code", None)
                        if (code is not None and code.co_name == "_finish_stop"
                                and child_code is not None and child_code.co_name == "sleep"):
                            assert worker._node_task.done() and worker._node_stop_task.done()
                            h.assembly.require_quiesced()
                            h.assembly.require_monitoring_tasks_quiesced()
                            assert not worker._channel_closed
                            if attribute == "lease":
                                assert worker._lock.acquisition_id == original
                                worker._lock.release()
                                worker._lock.acquire()
                            else:
                                assert getattr(worker, attribute) is original
                                setattr(worker, attribute, object())
                            replaced.set()
                            return
                        coroutine = child
                    assert not stopping.done(), "final cleanup continuation was not observed"

            watcher = asyncio.create_task(replace_at_final_yield())
            stopping = asyncio.create_task(worker.stop())
            holder.append(stopping)
            try:
                with pytest.raises((MonitoringError, RuntimeBlocked), match="^MONITORING_CLEANUP_OWNER_CHANGED$") as failure:
                    await stopping
                assert type(failure.value) in (MonitoringError, RuntimeBlocked)
                await asyncio.wait_for(watcher, 12)
                assert replaced.is_set()
                if attribute == "lease":
                    assert worker._lock.acquisition_id != original
                else:
                    assert getattr(worker, attribute) is not original
                assert not worker._channel_closed and worker._lock.acquired and not worker._stopped
                controller = original if attribute == "_monitoring" else worker._monitoring
                assert controller.state.resources_retained
                assert decode_document((worker.directory / "native-restart.json").read_bytes())["state"] == "active"
            finally:
                if attribute != "lease":
                    setattr(worker, attribute, original)
                if not watcher.done():
                    watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            if attribute != "lease":
                await worker.stop()


async def test_real_shutdown_quiesces_without_closing_shared_loop(tmp_path, monkeypatch):
    loop, release, entered = asyncio.get_running_loop(), threading.Event(), threading.Event()
    prior = loop._default_executor
    pool = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(pool)
    blocker = queued = None
    try:
        assert await asyncio.to_thread(lambda: 7) == 7

        def blocking():
            entered.set()
            assert release.wait(60)
            return "caller-complete"

        blocker = asyncio.create_task(asyncio.to_thread(blocking))
        await eventually(entered.is_set)
        queued = asyncio.create_task(asyncio.to_thread(lambda: "queued-complete"))
        await asyncio.sleep(0)
        async with restored_case(tmp_path / "shared", monkeypatch) as h:
            from multi_exchange_trader.execution.native_node import build_node_config

            configs = []
            h.session.configure_native_monitoring(h.worker.binding, scope=SCOPE, install=configs.append)
            direct = build_node_config(configs[0], symbols=("BTCUSDT",), trader_id="WORKER-001")
            monitor = build_node_config(configs[0], symbols=("BTCUSDT",), trader_id="WORKER-001", monitor_only=True)
            assert direct.timeout_post_stop == 10 and monitor.timeout_post_stop == 0.05
            assert loop._default_executor is pool
            assert h.assembly.node.kernel.executor is not pool
            await admit(h)
            client, channel, lease = h.assembly.execution_client, h.assembly.order_subscription, h.worker._lock
            row = await h.io.ledger.execution("stop-original")
            await deliver(h, [row])
            await h.worker.stop()
            assert h.worker._node_task.done() and h.worker._node_stop_task.done()
            assert client._repay_task.done() and not client.is_connected
            assert not client._monitor_stream._tasks and h.io.wires[-1].is_closed()
            assert h.assembly.node.kernel.exec_engine.cmd_qsize() == 0
            assert h.assembly.node.kernel.exec_engine.evt_qsize() == 0
            assert h.worker._channel_closed and not lease.acquired
            assert h.worker.monitoring_status.phase == "STOPPED"
            assert channel is h.assembly.order_subscription
            assert decode_document((h.worker.directory / "native-restart.json").read_bytes())["state"] == "sealed"
            assert loop._default_executor is pool and not queued.done()
        release.set()
        assert await blocker == "caller-complete" and await queued == "queued-complete"
        assert await asyncio.to_thread(lambda: "after-stop") == "after-stop"
        assert await asyncio.create_task(asyncio.sleep(0, result="loop-alive")) == "loop-alive"
    finally:
        release.set()
        for task in (blocker, queued):
            if task is not None:
                await task
        loop._default_executor = prior
        pool.shutdown(wait=True)
    # Actual construction also preserves an absent default executor.
    prior = loop._default_executor
    loop._default_executor = None
    try:
        async with restored_case(tmp_path / "absent", monkeypatch) as h:
            assert loop._default_executor is None
            await admit(h)
            await h.worker.stop()
            assert loop._default_executor is None
            assert await asyncio.to_thread(lambda: 9) == 9
    finally:
        created = loop._default_executor
        loop._default_executor = prior
        if created is not None:
            created.shutdown(wait=True)
    async with restored_case(tmp_path / "unproven", monkeypatch) as h:
        await admit(h)
        gate = h.io.gate()
        h.io.wires[-1].disconnect_gate = gate
        with pytest.raises(TimeoutError):
            await h.worker.stop()
        assert not h.worker._channel_closed and h.worker._lock.acquired
        assert not h.worker._stopped and h.worker.monitoring_status.resources_retained
        assert h.worker.runtime.store.get_position("position-1") is not None
        assert decode_document((h.worker.directory / "native-restart.json").read_bytes())["state"] == "active"
        original_stop = h.worker._node_stop_task
        gate.release.set()
        await asyncio.wait_for(asyncio.shield(original_stop), 12)
        await h.worker.stop()
        assert h.worker._node_stop_task is original_stop


async def test_live_fill_stales_proof_fault_requires_new_admission_and_seals(tmp_path, monkeypatch, record_property):
    async with restored_case(tmp_path / "expired", monkeypatch, binding_age=3) as h:
        initial = await admit(h)
        worker, assembly = h.worker, h.assembly
        controller, budget = worker._monitoring, worker._monitoring.budget
        binding, source = worker.binding, h.session._attested
        old_client, old_wire = assembly.execution_client, h.io.wires[-1]
        run_task, stream = worker._node_task, old_client._monitor_stream
        deadline, started = budget.deadline, budget.deadline - 60.0
        stamp = assembly.monitoring_stamp()
        epoch, connection = stamp.epoch, stamp.conn_id
        proof = initial.reconciled_at, initial.reconciliation_fingerprint
        reads, pings = len(h.io.sends), old_wire.sent.count("ping")
        await asyncio.sleep(3.05)
        with pytest.raises(ValueError):
            h.session.require_binding(binding, scope=SCOPE)
        assert h.session._attested is source
        assert worker.monitoring_status.phase == "MONITORING"
        assert not worker.monitoring_status.fresh_read_authority
        first = await h.io.ledger.execution("expired-binding-original", "0.400")
        order = await deliver(h, [first])
        assert_liabilities(h, candidates=1)
        after_first = worker.monitoring_status
        assert after_first.phase == "MONITORING" and not after_first.reconciliation_current
        assert after_first.current_revision != initial.current_revision
        assert order.filled_qty.as_decimal() == Decimal("0.400")

        # Cross the original admission budget using the real monotonic clock and
        # the same live stream. Status/readiness checks consume real pong state.
        async with asyncio.timeout(65):
            while time.monotonic() <= deadline + 0.25:
                status = worker.monitoring_status
                assert status.phase == "MONITORING" and not status.fresh_read_authority
                assert not any((status.trading_enabled, status.credit_authorized,
                    status.production_supported, status.venue_event_complete))
                assert (status.reconciled_at, status.reconciliation_fingerprint) == proof
                assert worker._monitoring is controller and controller.budget is budget
                assert budget.deadline == deadline and worker.binding is binding
                assert h.session._binding is binding and h.session._attested is source
                assert worker._assembly is assembly and assembly.execution_client is old_client
                assert old_client._monitor_stream is stream
                assert worker._node_task is run_task and not run_task.done()
                assert h.io.wires == [old_wire] and len(h.io.sends) == reads
                live_stamp = assembly.monitoring_stamp()
                assert (live_stamp.epoch, live_stamp.conn_id) == (epoch, connection)
                assert assembly.node.is_running() and old_client.is_connected and assembly.strategy.is_running
                assembly.require_monitoring_ready()
                await asyncio.sleep(0.25)
        elapsed = time.monotonic() - started
        record_property("original_budget_elapsed_seconds", f"{elapsed:.3f}")
        assert time.monotonic() > deadline + 0.25 and elapsed > 60.0
        assert old_wire.sent.count("ping") - pings >= 2
        assembly.require_monitoring_ready()
        await assembly.monitoring_barrier()
        stamp = assembly.monitoring_stamp()
        assert stamp.received == stamp.delivered and (stamp.epoch, stamp.conn_id) == (epoch, connection)
        assert stamp.received >= after_first.stream_stamp.received + 2
        assert worker.monitoring_status.phase == "MONITORING" and h.session._binding is binding
        second = await h.io.ledger.execution("past-deadline-original", "0.600")
        order = await deliver(h, [second])
        fills = [event for event in order.events if type(event) is module("model.events.order").OrderFilled]
        assert [str(fill.trade_id) for fill in fills] == [first["execId"], second["execId"]]
        for fill, row in zip(fills, (first, second), strict=True):
            assert fill.ts_event == int(row["execTime"]) * 1_000_000
            assert fill.last_qty.as_decimal() == Decimal(row["execQty"])
            assert fill.last_px.as_decimal() == Decimal(row["execPrice"])
            assert fill.commission.as_decimal() == Decimal(row["execFee"])
        position = assembly.node.cache.position_for_order(h.order_id)
        assert order.filled_qty.as_decimal() == position.quantity.as_decimal() == Decimal(1)
        assert position.events == fills
        assert_liabilities(h, candidates=2)
        candidates = worker.runtime.monitoring_state().candidates
        assert {candidate.fill.execution_id for candidate in candidates} == {first["execId"], second["execId"]}
        status = worker.monitoring_status
        assert status.phase == "MONITORING" and not status.reconciliation_current and not status.fresh_read_authority
        assert status.current_revision != after_first.current_revision
        assert (status.reconciled_at, status.reconciliation_fingerprint) == proof
        assert not any((status.trading_enabled, status.credit_authorized,
            status.production_supported, status.venue_event_complete))
        assert not worker.runtime._active and len(h.io.sends) == reads and h.session._attested is source
        stamp = assembly.monitoring_stamp()
        assert stamp.received == stamp.delivered and (stamp.epoch, stamp.conn_id) == (epoch, connection)
        events = [event.to_dict(event) for event in order.events]
        row = second
        old_wire.callbacks["post_reconnection"]()
        assert h.worker.monitoring_status.phase == "ATTENTION"
        with pytest.raises((ValueError, RuntimeError)):
            await h.worker.resume_restored_monitoring()
        await h.worker.stop()
        assert decode_document((h.worker.directory / "native-restart.json").read_bytes())["state"] == "sealed"
        next_owner = await make_worker(h.worker.directory, h.io, h)
        h.owners.append(next_owner)
        from multi_exchange_trader.execution.worker_cli import serve_monitor

        stop, refreshes = asyncio.Event(), []

        async def refresh():
            refreshes.append(True)

        before = len(h.io.sends)
        supervisor = asyncio.create_task(serve_monitor(next_owner.worker, stop=stop,
            refresh=refresh, connect=True, interval=0.01))
        try:
            await eventually(lambda: next_owner.worker.monitoring_status.phase == "MONITORING" or supervisor.done())
            if supervisor.done():
                supervisor.result()
                pytest.fail("connected supervisor ended before admission")
            next_owner.assembly = next_owner.worker._assembly
            next_owner.order_id, next_owner.reservations = h.order_id, h.reservations
            status = next_owner.worker.monitoring_status
            assert status.phase == "MONITORING" and status.reconciliation_current
            assert next_owner.assembly.execution_client is not old_client and h.io.wires[-1] is not old_wire
            assert len(h.io.sends) > before and len(h.io.wires) == 2
            assert [event.to_dict(event) for event in next_owner.assembly.node.cache.order(h.order_id).events] == events
            assert_liabilities(next_owner, candidates=2)
            stamp, reads = next_owner.assembly.monitoring_stamp(), len(h.io.sends)
            old_wire.emit("execution.linear", [row])
            await asyncio.sleep(0.05)
            assert next_owner.assembly.monitoring_stamp() == stamp
            assert not refreshes and len(h.io.sends) == reads and not supervisor.done()
            h.io.wires[-1].callbacks["post_reconnection"]()
            with pytest.raises(ValueError, match="NATIVE_MONITORING_RETIRED"):
                await supervisor
            assert next_owner.worker._stopped and not next_owner.worker._lock.acquired
        finally:
            stop.set()
            if not supervisor.done():
                await supervisor
    for revocation in ("reauth", "close"):
        async with restored_case(tmp_path / revocation, monkeypatch) as h:
            await admit(h)
            if revocation == "reauth":
                await h.session.authenticate(expected_uid="12345", expected_parent_uid="0", expected_is_master=False)
            else:
                await h.session.aclose()
            assert h.worker.monitoring_status.phase == "ATTENTION"
            before = h.assembly.monitoring_stamp()
            row = await h.io.ledger.execution("retired-original", apply=False)
            h.io.wires[-1].emit("execution.linear", [row])
            await asyncio.sleep(0)
            assert h.assembly.monitoring_stamp() == before
            assert_liabilities(h)
