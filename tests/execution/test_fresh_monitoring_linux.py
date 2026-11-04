"""Eight genuine first-monitor cases; only the isolated pinned Linux job qualifies."""

from __future__ import annotations

import copy
import hashlib
import hmac
import importlib
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any

import httpx
import pytest
from test_bybit_native_linux import isolated_native_runtime, module  # noqa: F401

from multi_exchange_trader.execution.account_binding import BybitDemoAccountSession
from multi_exchange_trader.execution.fresh_monitoring_v1 import (
    FreshMonitoringBootstrapV1,
    FreshMonitoringReceiptV1,
    FreshMonitoringTargetV1,
    require_fresh_target_v1,
)
from multi_exchange_trader.execution.monitoring_models import MonitoringError
from multi_exchange_trader.execution.native_restart_store import decode_document
from multi_exchange_trader.execution.observation_instrument_v1 import (
    ACTUAL_ACCOUNT_FEE_V1,
    SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
)
from multi_exchange_trader.execution.worker import ExecutionWorker


@asynccontextmanager
async def fresh_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    metadata_profile: str = ACTUAL_ACCOUNT_FEE_V1,
    previous: Any = None,
    initial_empty: bool = True,
) -> AsyncIterator[Any]:
    support = importlib.import_module("restored_monitoring_io_support")
    io: Any = support.IO(monkeypatch)
    original = io.send
    io.whole_positions, io.whole_orders = [], []

    async def send(request: httpx.Request, **kwargs: Any) -> httpx.Response:
        params, path = dict(request.url.params), request.url.path
        if path in ("/v5/position/list", "/v5/order/realtime") and "settleCoin" in params:
            assert request.method == "GET" and request.url.host == "api-demo.bybit.com"
            assert kwargs.get("stream") is True and params["category"] == "linear"
            assert params["settleCoin"] == "USDT" and "symbol" not in params
            assert set(params) == (
                {"category", "settleCoin", "openOnly"}
                if path.endswith("realtime")
                else {"category", "settleCoin"}
            )
            headers = request.headers
            assert headers["X-BAPI-API-KEY"] == support.KEY
            message = headers["X-BAPI-TIMESTAMP"] + support.KEY + headers["X-BAPI-RECV-WINDOW"]
            message += request.url.query.decode()
            assert hmac.compare_digest(
                headers["X-BAPI-SIGN"],
                hmac.new(support.SECRET.encode(), message.encode(), hashlib.sha256).hexdigest(),
            )
            io.sends.append((path, params))
            response = {
                "retCode": 0,
                "time": support.milliseconds(),
                "result": {
                    "category": "linear",
                    "nextPageCursor": "",
                    "list": copy.deepcopy(
                        io.whole_orders if path.endswith("realtime") else io.whole_positions
                    ),
                },
            }
            if io.on_http is not None:
                await io.on_http(path, params, response)
            return httpx.Response(200, json=response, request=request)
        response = await original(request, **kwargs)
        assert type(response) is httpx.Response
        return response

    io.send = send
    h: Any = await support.make_worker(tmp_path, io, previous=previous)
    old = h.worker
    h.worker = ExecutionWorker(
        session=old.session, binding=old.binding, scope=old.scope, symbols=old.symbols,
        trader_id=old.trader_id, directory=old.directory, policy_book=old.policy_book,
        registry=old.registry, entry_risk=old.entry_risk, clock=old.clock, max_age=old.max_age,
        lock_directory=old.lock_directory, metadata_profile=metadata_profile,
    )
    h.io = io
    try:
        await h.worker.start_passive()
        h.assembly = h.worker._assembly
        status = h.worker.native_restart_status
        assert status.status == ("FRESH_EMPTY_CACHE" if previous is None else "RESTORED_LOCAL_CACHE")
        assert (status.generation_id is None) == (previous is None)
        if initial_empty:
            assert not h.assembly.node.cache.instruments() and not h.assembly.node.cache.accounts()
            assert not h.assembly.node.cache.orders() and not h.assembly.node.cache.positions()
        yield h
    finally:
        for gate in io.gates:
            gate.release.set()
        try:
            if not h.worker._stopped:
                await h.worker.stop()
        finally:
            await h.session.aclose()
        assert all(wire.is_closed() for wire in io.wires)


async def connect(h: Any) -> Any:
    restart = h.worker.native_restart_status
    await h.worker.connect_monitoring()
    status = h.worker.monitoring_status
    assert status.phase == "MONITORING" and status.reconciliation_current
    assert not any(
        (
            status.trading_enabled,
            status.credit_authorized,
            status.production_supported,
            status.venue_event_complete,
        )
    )
    assert h.worker.native_restart_status is restart
    return h.worker._monitoring


async def test_actual_fresh_bootstrap_installs_provider_metadata_and_streams_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with fresh_case(
        tmp_path, monkeypatch, metadata_profile=SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
    ) as h:
        controller = await connect(h)
        assert controller.origin == "FRESH_NATIVE_MONITOR_BOOTSTRAP_V1"
        assert controller.source_generation_id is None
        assert not any(path == "/v5/account/fee-rate" for path, _ in h.io.sends)
        client, cache = h.assembly.execution_client, h.assembly.node.cache
        assert type(h.assembly.node) is module("live.node").TradingNode
        loaded = client.bybit_instrument_provider.get_all()
        assert {str(key) for key in loaded} == {"BTCUSDT-LINEAR.BYBIT"}
        instrument = cache.instruments()[0]
        assert type(instrument) is module("model.instruments").CryptoPerpetual
        assert cache.instrument(instrument.id) is loaded[instrument.id]
        account = cache.account(client.account_id)
        assert account.last_event is client._monitor_account_event
        assert controller.state.stream_stamp.received == controller.state.stream_stamp.delivered
        assert h.io.wires[-1].sent[:2] == ["auth", "subscribe"]
        with pytest.raises((ValueError, RuntimeError)):
            await client.prepare_fresh_metadata_v1(controller.witness)
        assert cache.instrument(instrument.id) is instrument
        assert not cache.orders() and not cache.positions()
        await h.worker.stop()
        async with fresh_case(
            tmp_path, monkeypatch, metadata_profile=SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
            previous=h, initial_empty=False,
        ) as restored:
            assert restored.worker._lock is not controller.lock
            assert restored.worker._lock.acquired and not controller.lock.acquired
            assert restored.worker.native_restart_status.generation_id is not None
            await connect(restored)
            assert not any(path == "/v5/account/fee-rate" for path, _ in restored.io.sends)
        with pytest.raises((ValueError, RuntimeError)):
            async with fresh_case(
                tmp_path, monkeypatch, previous=restored, initial_empty=False,
            ):
                pytest.fail("Observation metadata must not restore under the actual-fee profile")


async def test_actual_fresh_origin_rejects_copied_foreign_and_restored_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with fresh_case(tmp_path, monkeypatch) as h:
        controller = await connect(h)
        target = controller.capture()
        assert type(target) is FreshMonitoringTargetV1
        with pytest.raises(MonitoringError):
            copy.copy(controller.witness)
        with pytest.raises(MonitoringError):
            require_fresh_target_v1(copy.copy(target), h.session, "POST")
        with pytest.raises(MonitoringError):
            require_fresh_target_v1(target, object(), "POST")
        with pytest.raises((ValueError, RuntimeError)):
            await h.worker.resume_restored_monitoring()
        receipt = object.__new__(FreshMonitoringReceiptV1)
        with pytest.raises(MonitoringError):
            controller.require_receipt(receipt, target, "POST")


async def test_actual_fresh_bootstrap_rejects_nonempty_application_or_native_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with fresh_case(tmp_path, monkeypatch) as h:
        h.worker.runtime.journal.bind_owner_epoch_v2(
            "unexpected-owner", expected_previous_owner_epoch=None, expected_journal_revision=0
        )
        with pytest.raises((ValueError, RuntimeError)):
            await h.worker.connect_monitoring()
        assert not h.io.wires and h.worker.runtime.journal.scope.account_id == "12345"
    async with fresh_case(tmp_path / "cache", monkeypatch) as h:
        from multi_exchange_trader.execution.native_monitor_http import convert_instrument

        support = importlib.import_module("restored_monitoring_io_support")
        instrument = module("model.instruments").instruments_from_pyo3(
            [convert_instrument(h.io.instrument, h.io.fee, "BTCUSDT", support.milliseconds())]
        )[0]
        h.assembly.node.cache.add_instrument(instrument)
        with pytest.raises((ValueError, RuntimeError)):
            await h.worker.connect_monitoring()
        assert not h.io.wires and h.assembly.node.cache.instrument(instrument.id) is instrument
    async with fresh_case(tmp_path / "empty-sealed", monkeypatch) as empty:
        await empty.worker.stop()
        async with fresh_case(
            tmp_path / "empty-sealed", monkeypatch, previous=empty,
        ) as changed:
            changed.worker.runtime.journal.bind_owner_epoch_v2(
                "unexpected-restored-owner", expected_previous_owner_epoch=None,
                expected_journal_revision=0,
            )
            with pytest.raises((ValueError, RuntimeError)):
                await changed.worker.connect_monitoring()
            assert not changed.io.wires and not changed.assembly.node.cache.instruments()


async def test_actual_fresh_bootstrap_preserves_pre_post_and_whole_account_flatness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for when in ("PRE", "POST"):
        async with fresh_case(tmp_path / when, monkeypatch) as h:
            if when == "PRE":
                h.io.whole_positions = [
                    {"symbol": "ETHUSDT", "positionIdx": 0, "side": "Buy", "size": "1"}
                ]
            else:

                async def on_send(wire: Any, packet: Any, ack: Any) -> None:
                    if packet["op"] == "subscribe":
                        h.io.whole_orders = [{"orderId": "external-unowned"}]

                h.io.on_send = on_send
            with pytest.raises((ValueError, RuntimeError)):
                await h.worker.connect_monitoring()
            assert not h.assembly.node.cache.orders() and not h.assembly.node.cache.positions()
            if when == "PRE":
                assert not h.io.wires


async def test_actual_fresh_bootstrap_rejects_metadata_drift_and_stale_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with fresh_case(tmp_path / "stale", monkeypatch) as h:
        support = importlib.import_module("restored_monitoring_io_support")

        async def stale(path: str, params: Any, response: Any) -> None:
            if path == "/v5/market/instruments-info":
                response["time"] = support.milliseconds() - 60000

        h.io.on_http = stale
        with pytest.raises((ValueError, RuntimeError)):
            await h.worker.connect_monitoring()
        assert not h.io.wires
    async with fresh_case(tmp_path / "drift", monkeypatch) as h:
        controller = await connect(h)
        from multi_exchange_trader.execution.native_monitor_http import convert_instrument

        support = importlib.import_module("restored_monitoring_io_support")
        changed = module("model.instruments").instruments_from_pyo3(
            [
                convert_instrument(
                    h.io.instrument,
                    {**h.io.fee, "makerFeeRate": "0.0003"},
                    "BTCUSDT",
                    support.milliseconds(),
                )
            ]
        )[0]
        h.assembly.node.cache.add_instrument(changed)
        assert h.worker.monitoring_status.phase == "ATTENTION"
        with pytest.raises((ValueError, RuntimeError)):
            controller.capture()


async def test_actual_fresh_bootstrap_stop_reauthentication_and_owner_fences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with fresh_case(tmp_path / "stop", monkeypatch) as h:
        controller = await connect(h)
        lock = h.worker._lock
        await h.worker.stop()
        assert h.worker._stopped and not lock.acquired
        assert controller.state.phase == "STOPPED"
        with pytest.raises((ValueError, RuntimeError)):
            controller.capture()
    async with fresh_case(tmp_path / "reauth", monkeypatch) as h:
        await connect(h)
        await h.session.authenticate(
            expected_uid="12345", expected_parent_uid="0", expected_is_master=False
        )
        assert h.worker.monitoring_status.phase == "ATTENTION"
        assert not h.worker.monitoring_status.fresh_read_authority
    # A first pre-metadata network failure seals a genuinely empty native/app frame.
    # Its real restored generation may retry observation without being called fresh.
    failed_path = tmp_path / "first-network-failure"
    async with fresh_case(
        failed_path, monkeypatch, metadata_profile=SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
    ) as failed:
        async def unavailable(path: str, _params: Any, _response: Any) -> None:
            if path == "/v5/market/instruments-info":
                raise httpx.ConnectError("fixture first public request failed")

        failed.io.on_http = unavailable
        with pytest.raises((ValueError, RuntimeError, httpx.HTTPError)):
            await failed.worker.connect_monitoring()
        assert failed.worker._stopped
        assert not failed.assembly.node.cache.instruments()
        sealed = decode_document((failed.worker.directory / "native-restart.json").read_bytes())
        assert sealed["state"] == "sealed"
        async with fresh_case(
            failed_path, monkeypatch, metadata_profile=SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
            previous=failed,
        ) as retry:
            old_generation = retry.worker.native_restart_status.generation_id
            controller = await connect(retry)
            assert controller.origin == "EMPTY_SEALED_OBSERVATION_BOOTSTRAP_V1"
            assert controller.source_generation_id == old_generation
            assert old_generation is not None
            assert controller.lock is not failed.worker._monitoring.lock
            assert controller.lock.acquired and not failed.worker._monitoring.lock.acquired
            assert not any(path == "/v5/account/fee-rate" for path, _ in retry.io.sends)

    original_init = BybitDemoAccountSession.__init__
    armed: dict[str, Any] = {"kind": None, "fired": 0, "client": None}
    snapshot_code = FreshMonitoringBootstrapV1.snapshot.__code__
    capture_code = FreshMonitoringBootstrapV1.capture.__code__
    receipt_code = FreshMonitoringBootstrapV1.require_receipt.__code__

    def forwarding_clock() -> datetime:
        value = datetime.now(UTC)
        frame: FrameType | None = sys._getframe(1)
        direct_receipt = frame is not None and frame.f_code is receipt_code
        snapshot = capture = False
        while frame is not None:
            snapshot |= frame.f_code is snapshot_code
            capture |= frame.f_code is capture_code
            frame = frame.f_back
        if ((armed["kind"] == "snapshot" and snapshot and not capture)
                or (armed["kind"] == "receipt" and direct_receipt)):
            armed["kind"] = None
            armed["fired"] += 1
            armed["client"].base_url = "https://api.bybit.com"
        return value

    def init(owner: BybitDemoAccountSession, *args: Any, **kwargs: Any) -> None:
        kwargs["clock"] = forwarding_clock
        original_init(owner, *args, **kwargs)

    monkeypatch.setattr(BybitDemoAccountSession, "__init__", init)
    for kind in ("snapshot", "receipt"):
        async with fresh_case(
            tmp_path / ("final-clock-" + kind), monkeypatch,
            metadata_profile=SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
        ) as h:
            controller = await connect(h)
            normal = controller.snapshot()
            assert normal.phase == "MONITORING" and normal.fresh_read_authority
            client = h.session.read_client(h.worker.binding, scope=h.worker.scope)
            prior_url = client.base_url
            armed.update(client=client, fired=0)
            try:
                if kind == "snapshot":
                    armed["kind"] = kind
                    after = controller.snapshot()
                    assert after.phase == "ATTENTION" and not after.fresh_read_authority
                else:
                    target = controller.capture()
                    with h.session._monitoring_operation(controller.budget):
                        receipt = await controller.read_receipt(target, "POST")
                        controller.require_receipt(receipt, target, "POST")
                        armed["kind"] = kind
                        with pytest.raises(MonitoringError):
                            controller.require_receipt(receipt, target, "POST")
                        client.base_url = prior_url
                        with pytest.raises(MonitoringError):
                            controller.require_receipt(receipt, target, "POST")
                assert armed["fired"] == 1 and armed["kind"] is None
                assert not controller.state.trading_enabled and not controller.state.credit_authorized
            finally:
                armed["kind"] = None
                client.base_url = prior_url


async def test_actual_fresh_bootstrap_drain_failure_retains_original_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with fresh_case(tmp_path, monkeypatch) as h:
        await connect(h)
        stores, lock = (h.worker._journal, h.worker._store, h.worker._history_store), h.worker._lock
        gate = h.io.gate()
        h.io.wires[-1].disconnect_gate = gate
        with pytest.raises((ValueError, RuntimeError, TimeoutError)):
            await h.worker.stop()
        assert h.worker.monitoring_status.resources_retained and lock.acquired
        assert (h.worker._journal, h.worker._store, h.worker._history_store) == stores
        assert (
            decode_document((h.worker.directory / "native-restart.json").read_bytes())["state"]
            == "active"
        )
        gate.release.set()
        await h.worker.stop()
        assert h.worker._stopped


async def test_actual_fresh_bootstrap_keeps_all_financial_writers_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with fresh_case(
        tmp_path, monkeypatch, metadata_profile=SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
    ) as h:
        await connect(h)
        client = h.assembly.execution_client
        for name in (
            "submit_order",
            "submit_order_list",
            "modify_order",
            "cancel_order",
            "cancel_all_orders",
            "batch_cancel_orders",
        ):
            with pytest.raises(MonitoringError):
                getattr(client, name)(None)
        for name in (
            "_submit_order",
            "_modify_order",
            "_cancel_order",
            "set_leverage",
            "set_position_mode",
            "generate_mass_status",
        ):
            with pytest.raises(MonitoringError):
                await getattr(client, name)(None)
        assert not h.assembly.node.cache.orders() and not h.assembly.node.cache.positions()
        assert h.worker.runtime.monitoring_state().owned == ()
        assert h.book.read()["desired_state"] == "STOPPED"
