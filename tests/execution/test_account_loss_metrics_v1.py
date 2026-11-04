"""Intercepted original HTTPX owners; observations are never live qualification."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_bybit_linear_source_assembler_v1 import SCOPE, Fixture, Stream
from test_history import execution, order, transaction
from test_private_account_sources_bundle_v1 import KEY, SECRET

from multi_exchange_trader.account.bybit_readonly import BybitReadError
from multi_exchange_trader.execution.account_binding import (
    AccountBindingError,
    AuthenticatedAccountBinding,
    BybitDemoAccountSession,
)
from multi_exchange_trader.execution.bybit_linear_model_inputs_v1 import BybitFeeDocumentRefV1
from multi_exchange_trader.execution.bybit_linear_source_assembler_v1 import (
    BybitLinearSourceAssemblerV1,
    BybitLinearVenueReadV1,
)
from multi_exchange_trader.execution.history_receipts import AuthenticatedHistoryReceipt
from multi_exchange_trader.execution.journal import (
    CommandJournal,
    CommandKind,
    ExecutionScope,
    RevisionConflict,
)
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget


class MetricsFixture(Fixture):
    def __init__(self, monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
        self.wall = datetime.now(UTC)
        self.cash = self.equity = "1000"
        self.transactions: list[dict[str, Any]] = []
        self.executions: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        super().__init__(monkeypatch, path)
        self.refresh_basis()

    def refresh_basis(self) -> None:
        reference = self.basis.reference
        assert type(reference) is BybitFeeDocumentRefV1
        self.basis = replace(self.basis, reference=replace(reference, captured_at=self.clock()))

    async def send(
        self, client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        path, params = request.url.path, request.url.params
        original = (
            await super().send(client, request, **kwargs)
            if path
            not in {"/v5/execution/list", "/v5/order/history", "/v5/account/transaction-log"}
            else None
        )
        if original is not None:
            raw = b"".join([chunk async for chunk in original.aiter_bytes()])
            body = json.loads(raw)
            if path == "/v5/account/wallet-balance":
                coin = body["result"]["list"][0]["coin"][0]
                coin.update(
                    walletBalance=self.cash,
                    equity=self.equity,
                    totalPositionIM="0",
                    totalOrderIM="0",
                )
            elif (
                path == "/v5/order/realtime"
                or path == "/v5/position/list"
                and "symbol" not in params
            ):
                body["result"]["list"] = []
            return httpx.Response(200, stream=Stream(json.dumps(body).encode()), request=request)
        self.requests.append(request)
        records = {
            "/v5/execution/list": self.executions,
            "/v5/order/history": self.orders,
            "/v5/account/transaction-log": self.transactions,
        }[path]
        body = {
            "retCode": 0,
            "time": int(self.clock().timestamp() * 1000),
            "result": {"category": "linear", "nextPageCursor": "", "list": records},
        }
        return httpx.Response(200, stream=Stream(json.dumps(body).encode()), request=request)

    async def suffix(self, binding: Any, metrics: Any) -> tuple[Any, AuthenticatedHistoryReceipt]:
        read = await self.capture(binding)
        receipt = await self.session.read_history_receipt(
            binding,
            scope=SCOPE.product_scope.account,
            symbol="BTCUSDT",
            start_ms=metrics.watermark_ms + 1,
            end_ms=read.venue.wallet.source.envelope_time_ms,
        )
        return read, receipt

    def trade(
        self,
        *,
        identity: str,
        position: str,
        side: str,
        closing: bool,
        balance: str,
        cash_flow: str,
        fee: str = "0.1",
        quantity: str = "1",
    ) -> None:
        kind = CommandKind.EXIT if closing else CommandKind.ENTRY
        command = self.journal.register("command-" + identity, kind, {"position_id": position})
        stamp = int(self.wall.timestamp() * 1000)
        self.executions.append(
            execution(
                execId=identity,
                orderId="order-" + identity,
                orderLinkId=command.client_order_id,
                side=side,
                execTime=str(stamp),
                execQty=quantity,
                closedSize=quantity if closing else "0",
                execFee=fee,
            )
        )
        self.orders.append(
            order(
                orderId="order-" + identity,
                orderLinkId=command.client_order_id,
                side=side,
                reduceOnly=closing,
                qty=quantity,
                cumExecQty=quantity,
                createdTime=str(stamp),
                updatedTime=str(stamp),
            )
        )
        self.transactions.append(
            transaction(
                id="tx-" + identity,
                tradeId=identity,
                orderId="order-" + identity,
                transactionTime=str(stamp),
                cashFlow=cash_flow,
                fee=fee,
                change=str(Decimal(cash_flow) - Decimal(fee)),
                cashBalance=balance,
            )
        )
        # Original journal lifecycle for simulated fully filled fixture orders.
        # This is local test metadata, never an actual venue terminal attestation.
        self.journal.claim(command.command_key)
        self.journal.acknowledge(command.command_key, "intercepted fixture fill")
        self.journal.release_reservation(
            command.command_key,
            confirmed_terminal=True,
            terminal_evidence="intercepted fixture full order",
        )


async def test_daily_wallet_model_metrics_and_profile_share_original_current_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from multi_exchange_trader.execution.account_loss_metrics_v1 import AccountLossMetricsOwnerV1
    from multi_exchange_trader.execution.derived_daily_cash_v1 import DerivedDailyCashObserverV1

    fixture = MetricsFixture(monkeypatch, tmp_path)
    try:
        binding = await fixture.auth()
        daily_owner = DerivedDailyCashObserverV1(assembler=fixture.assembler)
        daily = await daily_owner.capture_v1(binding, scope=SCOPE.product_scope.account,
            symbol="BTCUSDT", budget=MonitoringReadBudget())
        read = await fixture.capture(binding, current_wallet_source=daily.wallet_sources[1])
        metrics_owner = AccountLossMetricsOwnerV1(fixture.assembler)
        metrics = metrics_owner.activate_v1(read, activation_id="explicit-fixture-epoch", expected_revision=0)
        profile_source = await fixture.session._capture_linear_api_profile_v1(binding,
            scope=SCOPE.product_scope.account, budget=MonitoringReadBudget(), owner_guard=fixture.guard)
        profile = fixture.session._project_linear_api_profile_v1(profile_source,
            binding=binding, scope=SCOPE.product_scope.account)

        assert fixture.assembler.require_current_v1(read) is read
        assert daily_owner.require_current_v1(daily) is daily
        assert metrics_owner.require_current_v1(metrics) is metrics
        assert read.venue.wallet.source is daily.wallet_sources[1]
        assert metrics.initial_equity == Decimal(1000)
        assert daily.ending_cash == Decimal(1000)
        assert profile.uid == "12345"
        assert not profile.financial_authority and not daily.financial_authority
        assert not metrics.ledger_qualified
    finally:
        await fixture.close()


async def test_explicit_original_equity_epoch_is_persistent_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from multi_exchange_trader.execution.account_loss_metrics_v1 import AccountLossMetricsOwnerV1

    f = MetricsFixture(monkeypatch, tmp_path)
    try:
        binding = await f.auth()
        read = await f.capture(binding)
        owner = AccountLossMetricsOwnerV1(f.assembler)
        first = owner.activate_v1(read, activation_id="operator-action-1", expected_revision=0)
        assert first.initial_equity == Decimal(1000)
        assert first.observed_drawdown == 0
        assert first.observed_consecutive_losses == 0
        assert first.consecutive_losses is None and first.daily_loss is None
        assert first.ledger_qualified is False
        assert owner.require_current_v1(first) is first
        assert AccountLossMetricsOwnerV1(f.assembler).read_v1() == first
    finally:
        await f.close()


async def test_current_metrics_reject_original_history_expiry_before_longer_assembler_ttl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from multi_exchange_trader.execution.account_loss_metrics_v1 import AccountLossMetricsOwnerV1

    f = MetricsFixture(monkeypatch, tmp_path)
    await f.session.aclose()
    real_clock, offset = f.clock, [0]

    def clock() -> datetime:
        return real_clock() + timedelta(seconds=offset[0])

    f.session = BybitDemoAccountSession(
        api_key=KEY,
        api_secret=SECRET,
        clock=clock,
        monotonic=lambda: time.monotonic() + offset[0],
        max_age=timedelta(seconds=120),
    )
    f.assembler = BybitLinearSourceAssemblerV1(
        session=f.session,
        public_marks=f.marks,
        catalog_producer=f.catalog,
        journal=f.journal,
        clock=clock,
        owner_guard=f.guard,
    )

    async def capture(binding: AuthenticatedAccountBinding) -> BybitLinearVenueReadV1:
        return await f.assembler.capture_v1(
            binding,
            scope=SCOPE,
            owner_generation="OWNER",
            nonvip_fee_basis=f.basis,
            nonvip_applicability_revision="fixture-fee-v1",
            budget=MonitoringReadBudget(),
            max_age=timedelta(seconds=120),
            max_quote_age=timedelta(seconds=120),
        )

    try:
        binding = await f.auth()
        owner = AccountLossMetricsOwnerV1(f.assembler)
        first = owner.activate_v1(
            await capture(binding), activation_id="ttl-regression", expected_revision=0
        )
        await asyncio.sleep(0.003)
        read = await capture(binding)
        end_ms = read.venue.wallet.source.envelope_time_ms
        assert end_ms is not None
        receipt = await f.session.read_history_receipt(
            binding,
            scope=SCOPE.product_scope.account,
            symbol="BTCUSDT",
            start_ms=first.watermark_ms + 1,
            end_ms=end_ms,
        )
        value = owner.observe_v1(
            read, history_receipts=(receipt,), expected_revision=first.revision
        )
        offset[0] = 31
        assert f.assembler.require_current_v1(read) is read
        with pytest.raises(AccountBindingError, match="^AUTHENTICATED_HISTORY_RECEIPT_REQUIRED$"):
            owner.require_current_v1(value)
        assert owner._current is None
        assert owner.read_v1() == value
        assert value.ledger_qualified is False
    finally:
        await f.close()


async def test_transfer_neutral_high_water_drawdown_restart_and_no_implicit_day_reset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from multi_exchange_trader.execution.account_loss_metrics_v1 import AccountLossMetricsOwnerV1

    f = MetricsFixture(monkeypatch, tmp_path)
    try:
        binding = await f.auth()
        owner = AccountLossMetricsOwnerV1(f.assembler)
        initial = owner.activate_v1(
            await f.capture(binding), activation_id="explicit", expected_revision=0
        )
        await asyncio.sleep(0.002)
        f.wall = f.clock()
        stamp = str(int(f.wall.timestamp() * 1000))
        f.transactions = [
            transaction(
                id="deposit",
                tradeId="",
                orderId="",
                type="TRANSFER_IN",
                symbol="",
                category="",
                transactionTime=stamp,
                cashFlow="500",
                funding="0",
                fee="0",
                change="500",
                cashBalance="1500",
            )
        ]
        f.cash, f.equity = "1500", "1480"
        read, receipt = await f.suffix(binding, initial)
        value = owner.observe_v1(
            read, history_receipts=(receipt,), expected_revision=initial.revision
        )
        assert (value.performance_equity, value.observed_high_water, value.observed_drawdown) == (
            Decimal(980),
            Decimal(1000),
            Decimal(20),
        )
        assert value.observed_max_drawdown == 20 and value.daily_loss is None
        assert (
            value.consecutive_losses is None
            and "VENUE_EVENT_COMPLETENESS_UNPROVEN" in value.blockers
        )
        replay = owner.observe_v1(
            read, history_receipts=(receipt,), expected_revision=initial.revision
        )
        assert replay == value and replay.revision == value.revision
        # A new genuine journal connection restores the epoch. No new DB/schema.
        f.journal.close()
        f.journal = CommandJournal(tmp_path / "journal.sqlite", ExecutionScope("12345", "DEMO"))
        f.assembler.journal = f.journal
        owner = AccountLossMetricsOwnerV1(f.assembler)
        assert owner.read_v1() == value
        f.transactions = []
        f.equity = "1510"
        read, receipt = await f.suffix(binding, value)
        restored = value
        value = owner.observe_v1(
            read, history_receipts=(receipt,), expected_revision=value.revision
        )
        # Pure arithmetic only: ageing an already observed epoch is no reset
        # rule. This copied seed is never accepted by the public owner API.
        history = f.session.require_history_receipt(
            receipt, binding=binding, scope=SCOPE.product_scope.account
        ).snapshot
        aged = replace(restored, activation_at=restored.activation_at - timedelta(days=8))
        calculation = owner._reduce(aged, read, (history,), "data-only")
        assert calculation.activation_at == aged.activation_at
        assert calculation.observed_high_water == value.observed_high_water
        assert value.activation_id == initial.activation_id
        assert value.observed_high_water == 1010
        assert value.observed_max_drawdown == 20 and value.observed_drawdown == 0
        assert value.daily_loss is None
    finally:
        await f.close()


async def test_closed_position_episode_fees_funding_partial_close_and_zero_reset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from multi_exchange_trader.execution.account_loss_metrics_v1 import AccountLossMetricsOwnerV1

    f = MetricsFixture(monkeypatch, tmp_path)
    try:
        binding = await f.auth()
        owner = AccountLossMetricsOwnerV1(f.assembler)
        start = owner.activate_v1(
            await f.capture(binding), activation_id="explicit", expected_revision=0
        )
        await asyncio.sleep(0.002)
        f.wall = f.clock()
        f.trade(
            identity="entry",
            position="p1",
            side="Buy",
            closing=False,
            balance="999.9",
            cash_flow="0",
        )
        await asyncio.sleep(0.002)
        f.wall = f.clock()
        funding_ms = str(int(f.wall.timestamp() * 1000))
        f.executions.append(
            execution(
                execId="funding",
                orderId="",
                orderLinkId="",
                execType="Funding",
                execTime=funding_ms,
                execQty="0",
                execPrice="0",
                execValue="0",
                execFee="1",
                closedSize="0",
            )
        )
        f.transactions.append(
            transaction(
                id="funding-tx",
                tradeId="",
                orderId="",
                type="SETTLEMENT",
                transactionTime=funding_ms,
                cashFlow="0",
                fee="0",
                funding="-1",
                change="-1",
                cashBalance="998.9",
            )
        )
        await asyncio.sleep(0.002)
        f.wall = f.clock()
        f.trade(
            identity="partial",
            position="p1",
            side="Sell",
            closing=True,
            quantity="0.5",
            balance="996.85",
            cash_flow="-2",
            fee="0.05",
        )
        await asyncio.sleep(0.002)
        f.wall = f.clock()
        f.trade(
            identity="close",
            position="p1",
            side="Sell",
            closing=True,
            quantity="0.5",
            balance="994.8",
            cash_flow="-2",
            fee="0.05",
        )
        f.cash = f.equity = "994.8"
        read, receipt = await f.suffix(binding, start)
        loss = owner.observe_v1(read, history_receipts=(receipt,), expected_revision=1)
        assert loss.observed_closed_episodes == loss.observed_consecutive_losses == 1
        assert loss.observed_drawdown == Decimal("5.2")
        assert loss.open_episodes == () and loss.consecutive_losses is None
        f.executions, f.orders, f.transactions = [], [], []
        await asyncio.sleep(0.002)
        f.wall = f.clock()
        f.trade(
            identity="entry2",
            position="p2",
            side="Buy",
            closing=False,
            balance="994.7",
            cash_flow="0",
        )
        await asyncio.sleep(0.002)
        f.wall = f.clock()
        f.trade(
            identity="close2",
            position="p2",
            side="Sell",
            closing=True,
            balance="994.8",
            cash_flow="0.2",
        )
        read, receipt = await f.suffix(binding, loss)
        zero = owner.observe_v1(read, history_receipts=(receipt,), expected_revision=2)
        assert zero.observed_closed_episodes == 2 and zero.observed_consecutive_losses == 0
        assert zero.observed_max_drawdown == Decimal("5.2")
        assert owner.require_current_v1(zero) is zero
    finally:
        await f.close()


@pytest.mark.parametrize("failure", ["copy", "reservation", "owner", "epoch-reset"])
async def test_activation_refuses_nonoriginal_unreserved_or_retired_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    from multi_exchange_trader.execution.account_loss_metrics_v1 import AccountLossMetricsOwnerV1

    f = MetricsFixture(monkeypatch, tmp_path)
    try:
        binding = await f.auth()
        read = await f.capture(binding)
        owner = AccountLossMetricsOwnerV1(f.assembler)
        if failure == "copy":
            read = replace(read)
        elif failure == "reservation":
            f.journal.register("pending", CommandKind.EXIT, {"position_id": "old"})
        elif failure == "owner":
            f.break_owner = True
        else:
            owner.activate_v1(read, activation_id="first", expected_revision=0)
        with pytest.raises((ValueError, BybitReadError)):
            owner.activate_v1(read, activation_id="second", expected_revision=0)
        if failure != "epoch-reset":
            assert owner.read_v1() is None
    finally:
        await f.close()


@pytest.mark.parametrize(
    "failure", ["gap", "cash", "foreign-handle", "revision", "cost", "attribution", "simultaneous"]
)
async def test_suffix_unknown_and_conflicting_evidence_never_advances_epoch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    from multi_exchange_trader.execution.account_loss_metrics_v1 import AccountLossMetricsOwnerV1

    f = MetricsFixture(monkeypatch, tmp_path)
    try:
        binding = await f.auth()
        owner = AccountLossMetricsOwnerV1(f.assembler)
        initial = owner.activate_v1(
            await f.capture(binding), activation_id="explicit", expected_revision=0
        )
        await asyncio.sleep(0.002)
        f.wall = f.clock()
        if failure in {"cost", "attribution", "simultaneous"}:
            f.trade(
                identity="entry",
                position="p1",
                side="Buy",
                closing=False,
                balance="999.9",
                cash_flow="0",
            )
            f.cash = f.equity = "999.9"
            if failure == "cost":
                f.transactions[0].update(fee="0.2", change="-0.2", cashBalance="999.8")
                f.cash = f.equity = "999.8"
            elif failure == "attribution":
                f.executions[0]["orderLinkId"] = f.orders[0]["orderLinkId"] = "external"
            else:
                # Two indistinguishable zero-change balance links cannot establish order.
                stamp = str(int(f.wall.timestamp() * 1000))
                f.executions, f.orders = [], []
                f.transactions = [
                    transaction(
                        id=name,
                        tradeId=name,
                        transactionTime=stamp,
                        cashFlow="0",
                        fee="0",
                        change="0",
                        cashBalance="1000",
                    )
                    for name in ("a", "b")
                ]
                f.cash = f.equity = "1000"
        elif failure == "cash":
            f.cash = "999"
        read = await f.capture(binding)
        receipt = await f.session.read_history_receipt(
            binding,
            scope=SCOPE.product_scope.account,
            symbol="BTCUSDT",
            start_ms=initial.watermark_ms + (2 if failure == "gap" else 1),
            end_ms=read.venue.wallet.source.envelope_time_ms,
        )
        if failure == "foreign-handle":
            receipt = object.__new__(AuthenticatedHistoryReceipt)
        with pytest.raises((ValueError, RevisionConflict)):
            owner.observe_v1(
                read,
                history_receipts=(receipt,),
                expected_revision=0 if failure == "revision" else 1,
            )
        assert owner.read_v1() == initial
    finally:
        await f.close()
