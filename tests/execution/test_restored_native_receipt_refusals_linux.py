"""Fail-closed contracts for the restored native instrument receipt startup.

These cases use the genuine restored worker and native lifecycle with only the
existing synthetic HTTP and WebSocket seams. No venue order path is enabled.
"""
from __future__ import annotations

import asyncio
import importlib

import httpx
import pytest
from restored_monitoring_io_support import admit, milliseconds, restored_case
from test_bybit_native_linux import isolated_native_runtime, module  # noqa: F401

from multi_exchange_trader.execution.monitoring_budget import (
    MonitoringBudgetError,
    MonitoringReadBudget,
)
from multi_exchange_trader.execution.monitoring_models import MonitoringError


def _client(h):
    return h.assembly.execution_client


async def _expect_resume_refusal(h) -> None:
    with pytest.raises((MonitoringError, ValueError, RuntimeError)):
        async with asyncio.timeout(30):
            await h.worker.resume_restored_monitoring()


def _assert_refused_without_receipt_or_stream(h, *, send_start: int, wallet_reads: int) -> None:
    client = _client(h)
    registry = h.session._native_instrument_receipts_v1
    paths = [path for path, _params in h.io.sends[send_start:]]
    assert paths.count("/v5/account/wallet-balance") == wallet_reads
    assert client._native_instrument_receipt_v1 is None
    assert registry._slot is None
    assert not h.io.wires


def _install_arm_probe(h, monkeypatch, before_arm):
    original = h.session._arm_native_instrument_capture_v1
    observed = {}

    def probe(**parts):
        budget = parts["budget"]
        assert type(budget) is MonitoringReadBudget
        before_arm(parts)
        observed["sends"] = len(h.io.sends)
        observed["symbols"] = parts["target"].symbols
        return original(**parts)

    monkeypatch.setattr(h.session, "_arm_native_instrument_capture_v1", probe)
    return observed


async def test_t4_sixteen_symbol_preflight_rejects_before_any_t4_get(tmp_path, monkeypatch):
    async with restored_case(tmp_path, monkeypatch) as h:
        def make_sixteen(parts):
            target = parts["target"]
            symbols = (target.symbols[0],) + tuple(f"DENY{i:02d}USDT" for i in range(1, 16))
            object.__setattr__(target, "symbols", symbols)
            parts["adapter"]._symbols = symbols

        observed = _install_arm_probe(h, monkeypatch, make_sixteen)
        send_start = len(h.io.sends)
        await _expect_resume_refusal(h)

        assert observed["symbols"] and len(observed["symbols"]) == 16
        assert len(h.io.sends) == observed["sends"]
        assert _client(h).monitoring_fault == "MONITORING_INSTRUMENT_RECEIPT_ARM_FAILED"
        _assert_refused_without_receipt_or_stream(
            h, send_start=send_start, wallet_reads=0
        )


async def test_t4_already_consumed_group_capacity_rejects_without_sends(tmp_path, monkeypatch):
    async with restored_case(tmp_path, monkeypatch) as h:
        def consume_group_capacity(parts):
            # The native startup needs fee + instrument + wallet GET slots.
            # Preconsume 30 logical slots in the active group: only two remain.
            for _ in range(30):
                parts["budget"].begin_read()

        observed = _install_arm_probe(h, monkeypatch, consume_group_capacity)
        send_start = len(h.io.sends)
        await _expect_resume_refusal(h)

        assert len(observed["symbols"]) == 1
        assert len(h.io.sends) == observed["sends"]
        assert _client(h).monitoring_fault == "MONITORING_INSTRUMENT_RECEIPT_ARM_FAILED"
        _assert_refused_without_receipt_or_stream(
            h, send_start=send_start, wallet_reads=0
        )


@pytest.mark.parametrize("corruption", ["response_origin", "fee", "source", "spec"])
async def test_t4_bad_origin_fee_source_or_spec_fails_closed(
    tmp_path, monkeypatch, corruption
):
    async with restored_case(tmp_path, monkeypatch) as h:
        send_start = len(h.io.sends)
        corruption_seen = {"value": False}
        if corruption == "response_origin":
            original_send = httpx.AsyncClient.send

            async def wrong_origin(client, request, **kwargs):
                response = await original_send(client, request, **kwargs)
                if request.url.path == "/v5/market/instruments-info":
                    corruption_seen["value"] = True
                    wrong_request = httpx.Request(
                        request.method,
                        "https://api.bybit.com" + request.url.raw_path.decode("ascii"),
                    )
                    return httpx.Response(
                        response.status_code,
                        headers=response.headers,
                        content=response.content,
                        request=wrong_request,
                    )
                return response

            monkeypatch.setattr(httpx.AsyncClient, "send", wrong_origin)
        else:
            async def corrupt(path, _params, response):
                if corruption == "fee" and path == "/v5/account/fee-rate":
                    response["result"]["list"][0]["makerFeeRate"] = "0.0003"
                    corruption_seen["value"] = True
                elif corruption == "source" and path == "/v5/market/instruments-info":
                    response["result"]["category"] = "spot"
                    corruption_seen["value"] = True
                elif corruption == "spec" and path == "/v5/market/instruments-info":
                    response["result"]["list"][0]["maxOrderQty"] = "99.000"
                    corruption_seen["value"] = True

            h.io.on_http = corrupt

        await _expect_resume_refusal(h)

        assert corruption_seen["value"]
        observed_paths = [path for path, _params in h.io.sends[send_start:]]
        assert "/v5/account/fee-rate" in observed_paths
        assert "/v5/market/instruments-info" in observed_paths
        expected_fault = (
            "MONITORING_INSTRUMENT_DRIFT"
            if corruption in ("fee", "spec")
            else "MONITORING_METADATA_INVALID"
        )
        assert _client(h).monitoring_fault == expected_fault

        _assert_refused_without_receipt_or_stream(
            h, send_start=send_start, wallet_reads=0
        )


async def test_post_super_native_cache_drift_blocks_wallet_ack_and_stream(
    tmp_path, monkeypatch
):
    async with restored_case(tmp_path, monkeypatch) as h:
        base = module("adapters.bybit.execution").BybitExecutionClient
        original_cache_instruments = base._cache_instruments
        send_start = len(h.io.sends)
        drift = {"base_returned": False, "applied": False}

        async def cache_then_drift(client):
            await original_cache_instruments(client)
            drift["base_returned"] = True
            if client is not h.assembly.execution_client:
                return
            from multi_exchange_trader.execution.native_monitor_http import convert_instrument

            native = convert_instrument(
                h.io.instrument,
                {**h.io.fee, "makerFeeRate": "0.0003"},
                "BTCUSDT",
                milliseconds(),
            )
            changed = module("model.instruments").instruments_from_pyo3([native])[0]
            client._cache.add_instrument(changed)
            drift["applied"] = True

        monkeypatch.setattr(base, "_cache_instruments", cache_then_drift)
        await _expect_resume_refusal(h)

        assert drift == {"base_returned": True, "applied": True}
        observed_paths = [path for path, _params in h.io.sends[send_start:]]
        assert "/v5/account/fee-rate" in observed_paths
        assert "/v5/market/instruments-info" in observed_paths
        assert _client(h).monitoring_fault == "MONITORING_INSTRUMENT_CACHE_DRIFT"

        _assert_refused_without_receipt_or_stream(
            h, send_start=send_start, wallet_reads=0
        )


@pytest.mark.parametrize("ack_state", ["missing", "mismatched"])
async def test_wallet_ack_missing_or_mismatched_never_issues_receipt_or_stream(
    tmp_path, monkeypatch, ack_state
):
    async with restored_case(tmp_path, monkeypatch) as h:
        client_type = importlib.import_module(
            "multi_exchange_trader.execution.native_monitor_client"
        ).MonitoringBybitExecutionClient
        original_update = client_type._update_account_state
        send_start = len(h.io.sends)
        ack_tampered = {"value": False}

        async def tampered_update(client):
            await original_update(client)
            client._monitor_account_event = None if ack_state == "missing" else object()
            ack_tampered["value"] = True

        monkeypatch.setattr(client_type, "_update_account_state", tampered_update)
        await _expect_resume_refusal(h)

        assert ack_tampered["value"]
        observed_paths = [path for path, _params in h.io.sends[send_start:]]
        assert "/v5/account/fee-rate" in observed_paths
        assert "/v5/market/instruments-info" in observed_paths
        assert observed_paths.count("/v5/account/wallet-balance") == 1
        assert _client(h).monitoring_fault == "MONITORING_NATIVE_CONNECT_FAILED"

        _assert_refused_without_receipt_or_stream(
            h, send_start=send_start, wallet_reads=1
        )


async def test_cancellation_after_arm_revokes_pending_slot_before_wallet_or_stream(
    tmp_path, monkeypatch
):
    async with restored_case(tmp_path, monkeypatch) as h:
        client_type = importlib.import_module(
            "multi_exchange_trader.execution.native_monitor_client"
        ).MonitoringBybitExecutionClient
        original_connect = client_type._connect
        entered_fee_read = asyncio.Event()
        connect_tasks = []
        fee_hold_entered = {"value": False}
        send_start = len(h.io.sends)

        async def tracked_connect(client):
            connect_tasks.append(asyncio.current_task())
            await original_connect(client)

        async def hold_fee(path, _params, _response):
            if path == "/v5/account/fee-rate":
                fee_hold_entered["value"] = True
                entered_fee_read.set()
                await asyncio.Future()

        monkeypatch.setattr(client_type, "_connect", tracked_connect)
        h.io.on_http = hold_fee
        resume = asyncio.create_task(h.worker.resume_restored_monitoring())
        async with asyncio.timeout(12):
            await entered_fee_read.wait()
        assert connect_tasks and connect_tasks[0] is not None
        assert _client(h)._native_instrument_capture_v1 is not None
        connect_tasks[0].cancel()
        with pytest.raises((MonitoringError, ValueError, RuntimeError)):
            async with asyncio.timeout(30):
                await resume

        assert fee_hold_entered["value"]
        observed_paths = [path for path, _params in h.io.sends[send_start:]]
        assert "/v5/account/fee-rate" in observed_paths
        assert _client(h).monitoring_fault == "MONITORING_HTTP_FAILED"

        _assert_refused_without_receipt_or_stream(
            h, send_start=send_start, wallet_reads=0
        )


async def test_get_budget_group_is_inactive_before_private_websocket_dial(tmp_path, monkeypatch):
    async with restored_case(tmp_path, monkeypatch, filled=True) as h:
        stream_module = importlib.import_module(
            "multi_exchange_trader.execution.private_monitor_stream"
        )
        original_dial = stream_module._dial
        observed = {"called": False}

        async def require_group_closed_before_dial(**callbacks):
            client = _client(h)
            slot = client._native_instrument_capture_v1
            assert slot is not None and not slot.startup_open
            assert client._native_instrument_receipt_v1 is not None
            with pytest.raises(MonitoringBudgetError):
                slot.budget.require_active_group(slot.group)
            observed["called"] = True
            return await original_dial(**callbacks)

        monkeypatch.setattr(stream_module, "_dial", require_group_closed_before_dial)
        async with asyncio.timeout(30):
            await admit(h)
        assert observed["called"]
