"""Original signed HTTPX owners with a new fixture journal; no venue calls."""

from __future__ import annotations

import importlib
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_bybit_linear_source_assembler_v1 import Fixture, Stream
from test_history import transaction
from test_private_account_sources_bundle_v1 import COIN, KEY, SECRET

from multi_exchange_trader.account.bybit_readonly import sign_v5
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
from multi_exchange_trader.execution.protection import ProtectionScope

SCOPE = ProtectionScope("12345", "DEMO")


class CashFixture(Fixture):
    def __init__(self, monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
        self.offset = timedelta(0)
        super().__init__(monkeypatch, path)
        self.cash = "85"
        self.second_cash: str | None = None
        self.wallet_reads = 0
        self.history_reads = 0
        self.late_rows: list[dict[str, Any]] | None = None
        midnight = self.clock().replace(hour=0, minute=0, second=0, microsecond=0)
        self.start_ms = int(midnight.timestamp() * 1000)
        self.rows = [
            transaction(
                transactionTime=str(self.start_ms + 1),
                cashFlow="5",
                fee="0",
                change="5",
                cashBalance="85",
            )
        ]

    @staticmethod
    def clock() -> datetime:
        return datetime.now(UTC)

    async def send(
        self, client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        path = request.url.path
        if path not in {
            "/v5/account/wallet-balance",
            "/v5/execution/list",
            "/v5/order/history",
            "/v5/account/transaction-log",
        }:
            return await super().send(client, request, **kwargs)
        assert request.method == "GET" and kwargs.get("stream") is True
        assert request.headers["X-BAPI-SIGN"] == sign_v5(
            SECRET,
            KEY,
            request.headers["X-BAPI-TIMESTAMP"],
            request.headers["X-BAPI-RECV-WINDOW"],
            request.url.query.decode(),
        )
        self.requests.append(request)
        if path.endswith("wallet-balance"):
            self.wallet_reads += 1
            cash = (
                self.second_cash if self.wallet_reads % 2 == 0 and self.second_cash else self.cash
            )
            result: dict[str, Any] = {
                "list": [
                    {
                        "accountType": "UNIFIED",
                        "coin": [
                            {
                                **COIN,
                                "walletBalance": cash,
                                "equity": cash,
                                "totalPositionIM": "0",
                                "totalOrderIM": "0",
                            }
                        ],
                    }
                ]
            }
        else:
            assert request.url.params["startTime"] == str(self.start_ms)
            assert int(request.url.params["endTime"]) <= int(self.clock().timestamp() * 1000)
            rows: list[dict[str, Any]] = []
            if path.endswith("transaction-log"):
                assert set(request.url.params) == {"accountType", "startTime", "endTime", "limit"}
                self.history_reads += 1
                rows = (
                    self.late_rows
                    if self.late_rows is not None and self.history_reads % 2 == 0
                    else self.rows
                )
            result = {"category": "linear", "list": rows, "nextPageCursor": ""}
        body = json.dumps(
            {"retCode": 0, "result": result, "time": int(self.clock().timestamp() * 1000)}
        ).encode()
        return httpx.Response(200, stream=Stream(body), request=request)


def observer(fixture: CashFixture) -> Any:
    name = "multi_exchange_trader.execution.derived_daily_cash_v1"
    assert importlib.util.find_spec(name) is not None, "Derived daily cash observer is absent"
    module = importlib.import_module(name)
    return module.DerivedDailyCashObserverV1(assembler=fixture.assembler)


async def collect(owner: Any, binding: Any) -> Any:
    return await owner.capture_v1(
        binding,
        scope=SCOPE,
        symbol="BTCUSDT",
        budget=MonitoringReadBudget(),
        max_age=timedelta(seconds=30),
    )


async def test_original_wallet_refresh_and_history_custody_produce_only_derived_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = CashFixture(monkeypatch, tmp_path)
    try:
        binding = await fixture.auth()
        owner = observer(fixture)
        result = await collect(owner, binding)
        assert owner.require_current_v1(result) is result
        assert result.method == "DERIVED_UTC_OPENING_CASH_FROM_TX_CHAIN_V1"
        assert result.derived_opening_cash == Decimal(80)
        assert result.ending_cash == Decimal(85) and result.day_net == Decimal(5)
        assert result.daily_observed_loss == 0
        assert not result.direct_midnight_observation and not result.venue_event_complete
        assert (
            not result.ledger_qualified
            and not result.financial_authority
            and not result.live_permission
        )
        assert len(result.wallet_source_hashes) == len(result.history_content_hashes) == 2
        assert result.history_content_hashes[0] == result.history_content_hashes[1]
        assert result.history_snapshot_ids[0] != result.history_snapshot_ids[1]
        first, last = result.wallet_sources
        assert first is not last
        wallet_slot = fixture.session._linear_private_state_v1("WALLET")[1]
        assert wallet_slot is not None and wallet_slot.source is last
        assert fixture.wallet_reads == fixture.history_reads == 2
        assert not any(
            r.url.path.startswith("/v5/market/") or r.url.path.endswith("fee-rate")
            for r in fixture.requests
            if r.url.path != "/v5/market/time"
        )
        # A real but omitted -20 before this observed +5 cannot be detected by
        # matching terminal wallet balances. Never label the derived 80 as 100.
        actual_opening_with_hidden_prefix = Decimal(100)
        assert result.derived_opening_cash != actual_opening_with_hidden_prefix
        assert "MISSING_PREFIX_CAN_REMAIN_UNDETECTED" in result.quality_flags
    finally:
        await fixture.close()


async def test_first_wallet_lifetime_uses_original_monotonic_even_when_wall_clock_stands_still(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = CashFixture(monkeypatch, tmp_path)
    try:
        monotonic = [0.0]
        fixture.session._monotonic = lambda: monotonic[0]
        frozen = fixture.clock()
        fixture.assembler.clock = lambda: frozen
        owner = observer(fixture)
        result = await owner.capture_v1(
            await fixture.auth(),
            scope=SCOPE,
            symbol="BTCUSDT",
            budget=MonitoringReadBudget(),
            max_age=timedelta(seconds=5),
        )
        monotonic[0] = 6.0
        with pytest.raises(ValueError, match="DERIVED_CASH_EXPIRED"):
            owner.require_current_v1(result)
        assert owner._current is None
    finally:
        await fixture.close()


async def test_replacing_original_monotonic_owner_retires_result_without_renewing_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = CashFixture(monkeypatch, tmp_path)
    try:
        fixture.session._monotonic = lambda: 0.0
        owner = observer(fixture)
        result = await collect(owner, await fixture.auth())
        fixture.session._monotonic = lambda: 0.0
        with pytest.raises(ValueError, match="ORIGINAL_OWNER_RETIRED"):
            owner.require_current_v1(result)
        assert owner._current is None
    finally:
        await fixture.close()


async def test_last_forwarding_owner_callback_cannot_retain_a_discarded_original_history_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = CashFixture(monkeypatch, tmp_path)
    calls, final_call, armed = [0], [0], [False]
    owners: list[Any] = []
    original_guard = fixture.assembler.owner_guard

    def forwarding_guard() -> None:
        original_guard()
        calls[0] += 1
        if armed[0] and calls[0] == final_call[0]:
            fixture.session.discard_history_receipt(owners[0]._receipts[0])

    fixture.assembler.owner_guard = forwarding_guard
    try:
        owner = observer(fixture)
        owners.append(owner)
        result = await collect(owner, await fixture.auth())
        calls[0] = 0
        assert owner.require_current_v1(result) is result
        final_call[0] = calls[0]
        assert final_call[0] > 0
        calls[0], armed[0] = 0, True
        with pytest.raises(ValueError):
            owner.require_current_v1(result)
        assert owner._current is None and not fixture.session._history_receipts
    finally:
        await fixture.close()


async def test_day_net_separates_transfers_and_retains_fee_funding_and_rebate_signs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = CashFixture(monkeypatch, tmp_path)
    try:
        fixture.cash = "117.1"
        base = {"transactionTime": str(fixture.start_ms), "tradeId": "", "orderId": ""}
        fixture.rows = [
            transaction(
                **base,
                id="in",
                symbol="",
                category="",
                type="TRANSFER_IN",
                cashFlow="20",
                fee="0",
                change="20",
                cashBalance="120",
            ),
            transaction(
                **{**base, "transactionTime": str(fixture.start_ms + 1)},
                id="trade",
                cashFlow="-2",
                fee="0.1",
                change="-2.1",
                cashBalance="117.9",
            ),
            transaction(
                **{**base, "transactionTime": str(fixture.start_ms + 2)},
                id="funding",
                type="SETTLEMENT",
                cashFlow="0",
                funding="-1",
                fee="0",
                change="-1",
                cashBalance="116.9",
            ),
            transaction(
                **{**base, "transactionTime": str(fixture.start_ms + 3)},
                id="rebate",
                cashFlow="0",
                fee="-0.2",
                change="0.2",
                cashBalance="117.1",
            ),
        ]
        owner = observer(fixture)
        result = await collect(owner, await fixture.auth())
        assert result.derived_opening_cash == 100 and result.net_transfers == 20
        assert result.day_net == Decimal("-2.9") and result.daily_observed_loss == Decimal("2.9")
        assert result.trading_fees_paid == Decimal("-0.1") and result.funding_received == -1
    finally:
        await fixture.close()


async def test_empty_observed_window_uses_wallet_value_with_explicit_missing_events_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = CashFixture(monkeypatch, tmp_path)
    try:
        fixture.rows = []
        owner = observer(fixture)
        result = await collect(owner, await fixture.auth())
        assert result.derived_opening_cash == 85 and result.transaction_count == 0
        assert result.day_net == result.daily_observed_loss == 0
        assert "EMPTY_OBSERVED_WINDOW_DOES_NOT_PROVE_NO_ACTIVITY" in result.quality_flags
        assert not result.direct_midnight_observation and not result.ledger_qualified
    finally:
        await fixture.close()


async def test_observed_loss_keeps_full_source_decimal_precision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = CashFixture(monkeypatch, tmp_path)
    try:
        fixture.cash = "1"
        loss = "12345678901234567890123456789.123456789012345678"
        fixture.rows = [
            transaction(
                transactionTime=str(fixture.start_ms),
                cashFlow="-" + loss,
                fee="0",
                change="-" + loss,
                cashBalance="1",
            )
        ]
        owner = observer(fixture)
        result = await collect(owner, await fixture.auth())
        assert result.daily_observed_loss == Decimal(loss)
    finally:
        await fixture.close()


@pytest.mark.parametrize("failure", ["wallet", "chain", "same-time", "unsupported"])
async def test_inconsistent_or_unsupported_original_observations_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    fixture = CashFixture(monkeypatch, tmp_path)
    try:
        if failure == "wallet":
            fixture.second_cash = "86"
        elif failure == "chain":
            fixture.rows.append(
                transaction(
                    id="gap",
                    transactionTime=str(fixture.start_ms + 2),
                    cashFlow="1",
                    fee="0",
                    change="1",
                    cashBalance="85",
                )
            )
        elif failure == "same-time":
            fixture.rows.append({**fixture.rows[0], "id": "ambiguous"})
        else:
            fixture.rows[0]["currency"] = "USDC"
        owner = observer(fixture)
        with pytest.raises(ValueError):
            await collect(owner, await fixture.auth())
        assert owner._current is None
    finally:
        await fixture.close()


async def test_late_fixed_window_prefix_retires_previously_issued_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = CashFixture(monkeypatch, tmp_path)
    try:
        owner = observer(fixture)
        binding = await fixture.auth()
        previous = await collect(owner, binding)
        fixture.late_rows = [
            transaction(
                id="late",
                transactionTime=str(fixture.start_ms),
                cashFlow="-20",
                fee="0",
                change="-20",
                cashBalance="80",
            ),
            *fixture.rows,
        ]
        with pytest.raises(ValueError, match="HISTORY_CHANGED"):
            await collect(owner, binding)
        with pytest.raises(ValueError):
            owner.require_current_v1(previous)
        assert not fixture.session._history_receipts
    finally:
        await fixture.close()


@pytest.mark.parametrize("failure", ["owner", "expiry", "reauth", "wallet-refresh", "close"])
async def test_result_requires_original_final_wallet_receipts_and_owner_lifetime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    fixture = CashFixture(monkeypatch, tmp_path)
    try:
        owner = observer(fixture)
        binding = await fixture.auth()
        result = await collect(owner, binding)
        if failure == "owner":
            fixture.break_owner = True
        elif failure == "expiry":
            fixture.session._clock = lambda: datetime.now(UTC) + timedelta(seconds=31)
        elif failure == "reauth":
            await fixture.auth()
        elif failure == "wallet-refresh":
            await fixture.session._capture_linear_wallet_v1(
                binding, scope=SCOPE, budget=MonitoringReadBudget(), owner_guard=fixture.guard
            )
        else:
            await fixture.session.aclose()
        with pytest.raises(ValueError):
            owner.require_current_v1(result)
        assert owner._current is None
    finally:
        await fixture.close()
