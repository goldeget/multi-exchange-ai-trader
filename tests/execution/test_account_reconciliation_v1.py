"""Original Session/HTTPX reads and fixture SQLite; no live financial authority."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from test_bybit_entry_market_projections_v1 import FLAT
from test_bybit_native import FakeFactory, FakeStrategy
from test_private_account_sources_bundle_v1 import SCOPE, authenticate, intercepted_session
from test_worker import harness

from multi_exchange_trader.execution.bybit_native import NativeBindings, NativeBybitBridge
from multi_exchange_trader.execution.native_node import NativeChannel, NativeNodeAssembly
from multi_exchange_trader.execution.native_restart_store import (
    NativeRestartError,
    NativeRestartIdentity,
    NativeRestartStore,
)
from multi_exchange_trader.execution.runtime import RuntimeBlocked
from multi_exchange_trader.execution.worker import ExecutionWorker, TrustedPlanRegistry


async def account_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    fixture = harness(tmp_path)
    await fixture.session.aclose()
    session, requests = intercepted_session(monkeypatch)
    wall = [session._clock()]
    session._clock = lambda: max(wall[0], datetime.now(UTC))
    data: dict[str, Any] = {"cash": "100", "transactions": [], "positions": [], "orders": []}
    original = httpx.AsyncClient.send

    async def send(
        client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        path = request.url.path
        if path in (
            "/v5/position/list",
            "/v5/order/realtime",
            "/v5/execution/list",
            "/v5/order/history",
            "/v5/account/transaction-log",
        ):
            requests.append(request)
            rows = (
                data["transactions"]
                if path.endswith("transaction-log")
                else [{**FLAT}]
                if "symbol" in request.url.params and path.endswith("position/list")
                else data["positions"]
                if path.endswith("position/list")
                else data["orders"]
                if path.endswith("realtime")
                else []
            )
            return httpx.Response(
                200,
                json={
                    "retCode": 0,
                    "time": int(wall[0].timestamp() * 1000),
                    "result": {"category": "linear", "list": rows, "nextPageCursor": ""},
                },
                request=request,
            )
        response = await original(client, request, **kwargs)
        if path.endswith("wallet-balance"):
            body = json.loads(response.content)
            body["time"] = int(wall[0].timestamp() * 1000)
            body["result"]["list"][0]["coin"][0].update(
                walletBalance=data["cash"], equity=data["cash"]
            )
            return httpx.Response(200, json=body, request=request)
        return response

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    binding = await authenticate(session)
    strategy = FakeStrategy()
    bridge = NativeBybitBridge(
        scope=SCOPE,
        native_account_id="BYBIT-UNIFIED",
        factory=FakeFactory(),
        strategy=strategy,
        bindings=NativeBindings(str, str, str, str, str),
    )
    assembly = NativeNodeAssembly(
        SimpleNamespace(), strategy, object(), bridge, NativeChannel(SCOPE, "BYBIT-UNIFIED")
    )
    worker = ExecutionWorker(
        directory=tmp_path,
        scope=SCOPE,
        session=session,
        binding=binding,
        symbols=("BTCUSDT",),
        trader_id="WORKER-001",
        policy_book=fixture.book,
        registry=TrustedPlanRegistry(()),
        entry_risk=lambda *_: (_ for _ in ()).throw(RuntimeBlocked("NO_ENTRY")),
        clock=lambda: max(wall[0], datetime.now(UTC)),
        max_age=timedelta(seconds=30),
        assembly_factory=lambda _: assembly,
    )
    return SimpleNamespace(worker=worker, session=session, requests=requests, wall=wall, data=data)


async def finish(h: Any) -> None:
    try:
        if h.worker._runtime is not None and not h.worker._stopped:
            await h.worker.stop()
    finally:
        await h.session.aclose()


async def test_start_checkpoint_is_explicit_durable_and_never_a_midnight_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await account_worker(tmp_path, monkeypatch)
    try:
        begin = getattr(h.worker, "begin_account_observation_v1", None)
        assert callable(begin), "Explicit account observation entry point is absent"
        with pytest.raises(RuntimeBlocked):
            await begin("BTCUSDT")
        await h.worker.start_passive()
        assert h.worker.runtime.journal.get_projection("account-observation-v1") is None
        checkpoint = await begin("BTCUSDT")
        assert checkpoint.cash_balance == Decimal(100)
        assert checkpoint.started_at == checkpoint.wallet_observed_at
        assert len(checkpoint.source_hashes) == 7
        assert not checkpoint.ledger_qualified and not checkpoint.utc_day_coverage
        persisted = h.worker.runtime.journal.get_projection("account-observation-v1")
        assert persisted is not None and persisted.revision == 1
        assert (
            json.loads(persisted.payload_json)["checkpoint"]["checkpoint_id"]
            == checkpoint.checkpoint_id
        )
        with pytest.raises(RuntimeBlocked, match="ALREADY_STARTED"):
            await begin("BTCUSDT")
        assert h.worker.runtime.journal.get_projection("account-observation-v1").revision == 1
    finally:
        await finish(h)


async def test_cash_history_reconciliation_preserves_unknown_risk_and_original_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await account_worker(tmp_path, monkeypatch)
    try:
        begin = getattr(h.worker, "begin_account_observation_v1", None)
        assert callable(begin), "Explicit account observation entry point is absent"
        await h.worker.start_passive()
        baseline = await begin("BTCUSDT")
        await asyncio.sleep(0.02)
        h.wall[0] = datetime.now(UTC)
        event_ms = int(h.wall[0].timestamp() * 1000) - 1
        h.data.update(
            cash="102",
            transactions=[
                {
                    "id": "funding-1",
                    "tradeId": "",
                    "orderId": "",
                    "symbol": "BTCUSDT",
                    "category": "linear",
                    "currency": "USDT",
                    "type": "SETTLEMENT",
                    "transSubType": "",
                    "transactionTime": str(event_ms),
                    "cashFlow": "0",
                    "funding": "2",
                    "fee": "0",
                    "change": "2",
                    "cashBalance": "102",
                }
            ],
        )
        report = await h.worker.collect_account_reconciliation_v1()
        assert report.checkpoint_id == baseline.checkpoint_id
        assert report.cash_change == report.history_change == Decimal(2)
        assert report.cash_difference == 0 and report.cash_chain_status == "MATCHED_OBSERVED_VALUES"
        assert (
            report.daily_loss
            is report.drawdown
            is report.consecutive_losses
            is report.total_risk
            is None
        )
        assert not report.ledger_qualified and not report.utc_day_coverage
        assert "UTC_MIDNIGHT_BASELINE_REQUIRED" in report.blockers
        assert "VENUE_EVENT_COMPLETENESS_UNPROVEN" in report.blockers
        assert report.history_observation_keys
        projection = h.worker.runtime.journal.get_projection("account-observation-v1")
        assert projection is not None and projection.revision == 2
        assert (
            json.loads(projection.payload_json)["checkpoint"]["checkpoint_id"]
            == baseline.checkpoint_id
        )
    finally:
        await finish(h)


@pytest.mark.parametrize("failure", ["cash", "uncovered-symbol", "external-order"])
async def test_reconciliation_reports_material_mismatch_without_invented_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    h = await account_worker(tmp_path, monkeypatch)
    try:
        await h.worker.start_passive()
        await h.worker.begin_account_observation_v1("BTCUSDT")
        await asyncio.sleep(0.02)
        h.wall[0] = datetime.now(UTC)
        if failure == "cash":
            h.data["cash"] = "101"
            expected = "CASH_CHAIN_UNRECONCILED"
        elif failure == "uncovered-symbol":
            h.data["transactions"] = [
                {
                    "id": "funding-eth",
                    "tradeId": "",
                    "orderId": "",
                    "symbol": "ETHUSDT",
                    "category": "linear",
                    "currency": "USDT",
                    "type": "SETTLEMENT",
                    "transSubType": "",
                    "transactionTime": str(int(h.wall[0].timestamp() * 1000) - 1),
                    "cashFlow": "0",
                    "funding": "2",
                    "fee": "0",
                    "change": "2",
                    "cashBalance": "102",
                }
            ]
            h.data["cash"] = "102"
            expected = "HISTORY_SYMBOL_COVERAGE_INCOMPLETE"
        else:
            h.data["orders"] = [
                {
                    "symbol": "BTCUSDT",
                    "orderId": "external-1",
                    "orderLinkId": "",
                    "positionIdx": 0,
                    "side": "Buy",
                    "reduceOnly": False,
                }
            ]
            expected = "EXTERNAL_ORDER_OWNERSHIP_UNRECONCILED"
        report = await h.worker.collect_account_reconciliation_v1()
        assert expected in report.blockers and not report.ledger_qualified
        assert report.daily_loss is report.total_risk is None
    finally:
        await finish(h)


async def test_normal_worker_stop_seals_original_store_and_restart_keeps_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fake native boundary; original worker stop, journal and sealed store are real."""
    h = await account_worker(tmp_path, monkeypatch)
    restarted: ExecutionWorker | None = None
    try:
        await h.worker.start_passive()
        store = NativeRestartStore(tmp_path)
        identity = NativeRestartIdentity(
            SCOPE, "BYBIT-UNIFIED", "WORKER-001", "OwnedExecution-001", ("BTCUSDT-LINEAR.BYBIT",)
        )
        empty: dict[str, Any] = {
            "schema": 1,
            "encoding": "msgpack-base64",
            "instruments": [],
            "orders": [],
            "positions": [],
        }
        store.mark_active(identity, None)
        checkpoint = await h.worker.begin_account_observation_v1("BTCUSDT")
        with pytest.raises(NativeRestartError):
            store.load(identity)  # Existing active/crash gate is preserved.

        def seal(resolve: Any) -> None:
            assert resolve(()) == ()
            store.seal(identity, empty)

        h.worker._restart = SimpleNamespace(seal=seal)
        await h.worker.stop()
        generation = store.load(identity)
        assert generation is not None
        binding = await authenticate(h.session)
        strategy = FakeStrategy()
        bridge = NativeBybitBridge(
            scope=SCOPE,
            native_account_id="BYBIT-UNIFIED",
            factory=FakeFactory(),
            strategy=strategy,
            bindings=NativeBindings(str, str, str, str, str),
        )
        assembly = NativeNodeAssembly(
            SimpleNamespace(), strategy, object(), bridge, NativeChannel(SCOPE, "BYBIT-UNIFIED")
        )
        restarted = ExecutionWorker(
            directory=tmp_path,
            scope=SCOPE,
            session=h.session,
            binding=binding,
            symbols=("BTCUSDT",),
            trader_id="WORKER-001",
            policy_book=h.worker.policy_book,
            registry=TrustedPlanRegistry(()),
            entry_risk=h.worker.entry_risk,
            clock=h.worker.clock,
            max_age=timedelta(seconds=30),
            assembly_factory=lambda _: assembly,
        )
        await restarted.start_passive()
        saved = restarted.runtime.journal.get_projection("account-observation-v1")
        assert (
            saved is not None
            and json.loads(saved.payload_json)["checkpoint"]["checkpoint_id"]
            == checkpoint.checkpoint_id
        )
        with pytest.raises(RuntimeBlocked, match="ALREADY_STARTED"):
            await restarted.begin_account_observation_v1("BTCUSDT")
        report = await restarted.collect_account_reconciliation_v1()
        assert report.checkpoint_id == checkpoint.checkpoint_id and not report.ledger_qualified
    finally:
        if restarted is not None and not restarted._stopped:
            await restarted.stop()
        await finish(h)


async def test_collect_requires_original_checkpoint_and_detects_saved_payload_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await account_worker(tmp_path, monkeypatch)
    try:
        await h.worker.start_passive()
        with pytest.raises(RuntimeBlocked, match="NOT_STARTED"):
            await h.worker.collect_account_reconciliation_v1()
        await h.worker.begin_account_observation_v1("BTCUSDT")
        journal = h.worker.runtime.journal
        saved = journal.get_projection("account-observation-v1")
        body = json.loads(saved.payload_json)
        body["checkpoint"]["cash_balance"] = "0"
        journal.apply_event(
            "fixture-corruption", {"fixture": True}, "account-observation-v1", 1, body
        )
        before = len(h.requests)
        with pytest.raises(RuntimeBlocked, match="CHECKPOINT_INVALID"):
            await h.worker.collect_account_reconciliation_v1()
        assert len(h.requests) == before
    finally:
        await finish(h)


async def test_stop_during_actual_read_retires_attempt_before_checkpoint_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await account_worker(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = httpx.AsyncClient.send

    async def send(
        client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        if request.url.path.endswith("wallet-balance"):
            entered.set()
            await release.wait()
        return await original(client, request, **kwargs)

    task: asyncio.Task[Any] | None = None
    try:
        await h.worker.start_passive()
        monkeypatch.setattr(httpx.AsyncClient, "send", send)
        task = asyncio.create_task(h.worker.begin_account_observation_v1("BTCUSDT"))
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(RuntimeBlocked, match="INFLIGHT"):
            await h.worker.stop()
        release.set()
        with pytest.raises((RuntimeBlocked, ValueError)):
            await task
        assert h.worker.runtime.journal.get_projection("account-observation-v1") is None
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await finish(h)
