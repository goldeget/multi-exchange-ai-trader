from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import Any

import httpx
import pytest

from multi_exchange_trader.execution.account_binding import BybitDemoAccountSession
from multi_exchange_trader.execution.account_ledger import DailyCashBaseline, daily_realized_loss
from multi_exchange_trader.execution.history import BybitHistoryReader, HistoryError
from multi_exchange_trader.execution.protection import ProtectionScope

NOW = datetime(2026, 11, 2, 1, tzinfo=UTC)
MS = int(NOW.timestamp() * 1000)
START = MS - 3600000
SCOPE = ProtectionScope("12345", "DEMO")
KEY = "fake-history-key"


def execution(**changes: Any) -> dict[str, Any]:
    return {"execId": "e1", "orderId": "o1", "orderLinkId": "client1", "symbol": "BTCUSDT",
        "side": "Buy", "execType": "Trade", "execTime": str(START + 1000), "execQty": "1",
        "execPrice": "100", "execValue": "100", "execFee": "0.1", "feeCurrency": "USDT",
        "feeRate": "0.001", "closedSize": "0"} | changes


def order(**changes: Any) -> dict[str, Any]:
    return {"orderId": "o1", "orderLinkId": "client1", "symbol": "BTCUSDT", "side": "Buy",
        "positionIdx": 0, "reduceOnly": False, "orderStatus": "Filled", "qty": "1",
        "cumExecQty": "1", "leavesQty": "0", "createdTime": str(START + 500),
        "updatedTime": str(START + 1000)} | changes


def transaction(**changes: Any) -> dict[str, Any]:
    return {"id": "t1", "tradeId": "e1", "orderId": "o1", "symbol": "BTCUSDT",
        "category": "linear", "currency": "USDT", "type": "TRADE", "transSubType": "",
        "transactionTime": str(START + 1000), "cashFlow": "-2", "funding": "0", "fee": "0.1",
        "change": "-2.1", "cashBalance": "997.9"} | changes


def pages(*, executions: list[Any] | None = None, orders: list[Any] | None = None,
          transactions: list[Any] | None = None) -> dict[str, Any]:
    return {
        "/v5/execution/list": {"category": "linear", "list": executions or [], "nextPageCursor": ""},
        "/v5/order/history": {"category": "linear", "list": orders or [], "nextPageCursor": ""},
        "/v5/account/transaction-log": {"list": transactions or [], "nextPageCursor": ""},
        "/v5/market/instruments-info": {"category": "linear", "nextPageCursor": "", "list": [{
            "symbol": "BTCUSDT", "status": "Trading", "settleCoin": "USDT",
            "leverageFilter": {"minLeverage": "1", "maxLeverage": "100", "leverageStep": "0.5"}}]},
        "/v5/market/risk-limit": {"category": "linear", "nextPageCursor": "", "list": [{
            "id": 1, "symbol": "BTCUSDT", "riskLimitValue": "100000", "maxLeverage": "100",
            "maintenanceMargin": 0.005, "initialMargin": 0.01, "isLowestRisk": 1,
            "mmDeduction": "0"}, {"id": 2, "symbol": "BTCUSDT", "riskLimitValue": "200000",
            "maxLeverage": "50", "maintenanceMargin": 0.01, "initialMargin": 0.02,
            "isLowestRisk": 0, "mmDeduction": "500"}]},
    }


async def reader(data: dict[str, Any], *, server_delta: int = 0,
                 max_pages: int = 20) -> tuple[Any, Any, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET" and request.url.host == "api-demo.bybit.com"
        path = request.url.path
        if path == "/v5/market/time":
            result: Any = {}
        elif path == "/v5/user/query-api":
            result = {"apiKey": KEY, "secret": "", "userID": 12345, "parentUid": "56789",
                "isMaster": False, "readOnly": 1, "permissions": {"Wallet": []}}
        else:
            result = data[path]
            if callable(result):
                result = result(request)
        return httpx.Response(200, json={"retCode": 0, "result": result,
            "time": MS if path in {"/v5/market/time", "/v5/user/query-api"} else MS + server_delta})

    session = BybitDemoAccountSession(api_key=KEY, api_secret="fake-history-secret",
        clock=lambda: NOW, monotonic=lambda: 1.0, transport=httpx.MockTransport(handler))
    binding = await session.authenticate(expected_uid="12345", expected_parent_uid="56789",
                                         expected_is_master=False)
    return session, BybitHistoryReader(session, binding, scope=SCOPE, clock=lambda: NOW,
                                     max_pages=max_pages), seen


async def snapshot(data: dict[str, Any]) -> Any:
    session, adapter, _ = await reader(data)
    try:
        return await adapter.read_history("BTCUSDT", start_ms=START, end_ms=MS)
    finally:
        await session.aclose()


def baseline(**changes: Any) -> DailyCashBaseline:
    return replace(DailyCashBaseline(SCOPE, NOW.replace(hour=0), Decimal(1000), "wallet-midnight"),
                   **changes)


async def test_empty_authenticated_history_never_proves_flat_or_qualifies_account() -> None:
    session, adapter, seen = await reader(pages())
    try:
        evidence = await adapter.read_history("BTCUSDT", start_ms=START, end_ms=MS)
    finally:
        await session.aclose()
    assert evidence.page_complete and evidence.transport_injected
    assert not evidence.flat_verified and not evidence.venue_event_complete
    transactions_request = next(r for r in seen if r.url.path.endswith("transaction-log"))
    assert set(transactions_request.url.params) == {"accountType", "startTime", "endTime", "limit"}
    loss = daily_realized_loss(evidence, baseline(), now=NOW)
    assert loss.daily_loss == 0 and not loss.ledger_qualified
    assert loss.qualification_blockers


@pytest.mark.parametrize("fee,change,balance,expected_loss", [
    ("0.1", "-0.1", "999.9", "0.1"),
    ("-0.1", "0.1", "1000.1", "0"),
])
async def test_documented_trade_blank_funding_preserves_fee_and_rebate(
    fee: str, change: str, balance: str, expected_loss: str,
) -> None:
    # The official transaction-log TRADE example uses funding="" and cashFlow="0".
    # IDs, time and balances below are synthetic; fees retain their documented sign.
    tx = transaction(funding="", cashFlow="0", fee=fee, change=change, cashBalance=balance)
    evidence = await snapshot(pages(executions=[execution(execFee=fee)], transactions=[tx]))
    loss = daily_realized_loss(evidence, baseline(), now=NOW)
    assert evidence.transactions[0].funding == 0
    assert loss.trading_fees_paid == Decimal(fee)
    assert loss.net_realized == Decimal(change)
    assert loss.daily_loss == Decimal(expected_loss)
    assert not evidence.venue_event_complete and not loss.ledger_qualified
    assert loss.baseline_source_id == "wallet-midnight"


@pytest.mark.parametrize("key,value", [
    ("cashFlow", ""), ("fee", ""), ("change", ""), ("cashBalance", ""),
    ("funding", None), ("funding", "NaN"), ("funding", 0),
    ("fee", None), ("fee", "NaN"), ("fee", 0.1),
])
async def test_trade_blank_funding_does_not_relax_required_money_fields(
    key: str, value: Any,
) -> None:
    tx = transaction(funding="", cashFlow="0", change="-0.1", cashBalance="999.9")
    tx[key] = value
    with pytest.raises(HistoryError, match="DECIMAL_REQUIRED"):
        await snapshot(pages(transactions=[tx]))


@pytest.mark.parametrize("kind", ["SETTLEMENT", "ADL", "LIQUIDATION", "TRANSFER_IN", "TRANSFER_OUT"])
async def test_blank_funding_remains_unsupported_outside_trade(kind: str) -> None:
    with pytest.raises(HistoryError, match="DECIMAL_REQUIRED"):
        await snapshot(pages(transactions=[transaction(type=kind, funding="")]))


@pytest.mark.parametrize("fee", ["0.1", "0", "-0.1"])
async def test_delivery_execution_blocks_history_before_zero_accounting(fee: str) -> None:
    # No order/transaction counterpart is available for this observed closing event.
    with pytest.raises(HistoryError, match="DELIVERY_ACCOUNTING_UNSUPPORTED"):
        await snapshot(pages(executions=[execution(execType="Delivery", closedSize="1", execFee=fee)]))


@pytest.mark.parametrize("kind", ["Delivery", "UNKNOWN"])
@pytest.mark.parametrize("has_counterpart", [False, True])
async def test_ledger_rejects_unsupported_observed_executions(
    kind: str, has_counterpart: bool,
) -> None:
    evidence = await snapshot(pages(executions=[execution()], transactions=[transaction()]))
    execution_record = replace(evidence.executions[0], execution_type=kind, closed_quantity=Decimal(1))
    evidence = replace(evidence, executions=(execution_record,),
                       transactions=evidence.transactions if has_counterpart else ())
    with pytest.raises(HistoryError, match="EXECUTION_TYPE_UNSUPPORTED"):
        daily_realized_loss(evidence, baseline(), now=NOW)


@pytest.mark.parametrize("path,key,value", [
    ("/v5/execution/list", "symbol", "ETHUSDT"),
    ("/v5/execution/list", "execTime", str(START - 1)),
    ("/v5/execution/list", "execQty", "NaN"),
    ("/v5/execution/list", "execFee", 0.1),
    ("/v5/execution/list", "feeCurrency", "BTC"),
    ("/v5/execution/list", "closedSize", "2"),
    ("/v5/execution/list", "execType", "MovePosition"),
    ("/v5/order/history", "reduceOnly", "false"),
    ("/v5/order/history", "cumExecQty", "2"),
    ("/v5/order/history", "orderLinkId", "conflicting-client"),
    ("/v5/account/transaction-log", "currency", "USDC"),
    ("/v5/account/transaction-log", "type", "INTEREST"),
    ("/v5/account/transaction-log", "type", "UNKNOWN"),
    ("/v5/account/transaction-log", "change", "2.1"),
    ("/v5/account/transaction-log", "funding", "1"),
    ("/v5/account/transaction-log", "transSubType", "movePosition"),
])
async def test_malformed_or_mixed_scope_history_blocks(path: str, key: str, value: Any) -> None:
    data = pages(executions=[execution()], orders=[order()], transactions=[transaction()])
    data[path]["list"][0][key] = value
    with pytest.raises(HistoryError):
        await snapshot(data)


@pytest.mark.parametrize("path", ["/v5/execution/list", "/v5/order/history", "/v5/account/transaction-log"])
async def test_duplicate_and_conflicting_ids_block(path: str) -> None:
    data = pages(executions=[execution()], orders=[order()], transactions=[transaction()])
    data[path]["list"] *= 2
    with pytest.raises(HistoryError, match="DUPLICATE"):
        await snapshot(data)


async def test_pagination_is_bounded_and_cycles_are_rejected() -> None:
    data = pages()
    data["/v5/execution/list"] = {"category": "linear", "list": [execution()], "nextPageCursor": "a"}
    session, adapter, seen = await reader(data)
    try:
        with pytest.raises(HistoryError, match="CURSOR"):
            await adapter.read_history("BTCUSDT", start_ms=START, end_ms=MS)
        assert len([r for r in seen if r.url.path.endswith("execution/list")]) == 2
    finally:
        await session.aclose()
    session, adapter, seen = await reader(data, max_pages=1)
    try:
        with pytest.raises(HistoryError, match="TRUNCATED"):
            await adapter.read_history("BTCUSDT", start_ms=START, end_ms=MS)
    finally:
        await session.aclose()


async def test_stale_server_empty_cursor_and_expired_binding_block() -> None:
    session, adapter, _ = await reader(pages(), server_delta=-31000)
    try:
        with pytest.raises(HistoryError, match="STALE"):
            await adapter.read_history("BTCUSDT", start_ms=START, end_ms=MS)
    finally:
        await session.aclose()
    with pytest.raises(HistoryError, match="READ_FAILED"):
        await adapter.read_history("BTCUSDT", start_ms=START, end_ms=MS)
    data = pages()
    data["/v5/execution/list"]["nextPageCursor"] = "never-ending-empty"
    with pytest.raises(HistoryError, match="CURSOR"):
        await snapshot(data)


async def test_daily_loss_includes_external_activity_funding_fees_excludes_transfers() -> None:
    txs = [transaction(), transaction(id="t2", tradeId="external", orderId="external-order",
        symbol="ETHUSDT", transactionTime=str(START + 2000), cashFlow="3", fee="-0.01",
        change="3.01", cashBalance="1000.91"),
        transaction(id="t3", tradeId="", orderId="", type="SETTLEMENT", cashFlow="0",
            fee="0", funding="-5", change="-5", cashBalance="995.91",
            transactionTime=str(START + 3000)),
        transaction(id="t4", tradeId="", orderId="", symbol="", category="", type="TRANSFER_IN",
            cashFlow="500", fee="0", change="500", cashBalance="1495.91",
            transactionTime=str(START + 4000))]
    evidence = await snapshot(pages(executions=[execution()], orders=[order()], transactions=txs))
    with localcontext() as context:
        context.prec = 2
        loss = daily_realized_loss(evidence, baseline(), now=NOW)
    assert loss.net_realized == Decimal("-4.09")
    assert loss.daily_loss == Decimal("4.09") and loss.funding_received == -5
    assert loss.trading_fees_paid == Decimal("0.09") and loss.net_transfers == 500
    assert loss.ending_cash_balance == Decimal("1495.91") and not loss.ledger_qualified


async def test_received_funding_and_maker_rebate_keep_their_signs() -> None:
    evidence = await snapshot(pages(transactions=[transaction(type="SETTLEMENT", tradeId="",
        orderId="", cashFlow="0", funding="2", fee="0", change="2", cashBalance="1002")]))
    loss = daily_realized_loss(evidence, baseline(), now=NOW)
    assert loss.net_realized == 2 and loss.daily_loss == 0


async def test_missing_midnight_baseline_window_cash_chain_and_execution_fee_block() -> None:
    evidence = await snapshot(pages(executions=[execution()], transactions=[transaction()]))
    with pytest.raises(HistoryError, match="BASELINE"):
        daily_realized_loss(evidence, baseline(at=NOW), now=NOW)
    with pytest.raises(HistoryError, match="WINDOW"):
        daily_realized_loss(replace(evidence, window=replace(evidence.window, start_ms=START + 1)),
                            baseline(), now=NOW)
    with pytest.raises(HistoryError, match="CASH_CHAIN"):
        daily_realized_loss(evidence, baseline(cash_balance=Decimal(999)), now=NOW)
    with pytest.raises(HistoryError, match="COST_UNRECONCILED"):
        daily_realized_loss(replace(evidence, transactions=()), baseline(), now=NOW)
    mismatched = replace(evidence.transactions[0], fee=Decimal("0.2"))
    with pytest.raises(HistoryError, match="COST_UNRECONCILED"):
        daily_realized_loss(replace(evidence, transactions=(mismatched,)), baseline(), now=NOW)


@pytest.mark.parametrize("changes", [
    {"fee": "1", "change": "499"}, {"funding": "1", "change": "501"},
    {"symbol": "BTCUSDT"}, {"cashFlow": "-500", "change": "-500"},
])
async def test_ambiguous_transfer_is_not_excluded(changes: dict[str, Any]) -> None:
    transfer = transaction(type="TRANSFER_IN", symbol="", category="", cashFlow="500", fee="0",
                           change="500", cashBalance="1500") | changes
    with pytest.raises(HistoryError):
        await snapshot(pages(transactions=[transfer]))


async def test_tiers_use_current_instrument_min_max_and_fractional_step() -> None:
    session, adapter, _ = await reader(pages())
    try:
        evidence = await adapter.read_risk_limits("BTCUSDT")
    finally:
        await session.aclose()
    assert evidence.leverage_step == Decimal("0.5")
    assert evidence.tiers[1].min_notional == evidence.tiers[0].max_notional
    assert evidence.tiers[1].max_leverage == 50 and not evidence.qualified


@pytest.mark.parametrize("change", ["empty", "duplicate", "symbol", "lowest", "leverage", "status"])
async def test_invalid_risk_tiers_block(change: str) -> None:
    data = pages()
    tiers = data["/v5/market/risk-limit"]["list"]
    if change == "empty":
        tiers.clear()
    elif change == "duplicate":
        tiers[1]["id"] = 1
    elif change == "symbol":
        tiers[1]["symbol"] = "ETHUSDT"
    elif change == "lowest":
        tiers[1]["isLowestRisk"] = 1
    elif change == "leverage":
        tiers[1]["maxLeverage"] = "200"
    else:
        data["/v5/market/instruments-info"]["list"][0]["status"] = "PreLaunch"
    session, adapter, _ = await reader(data)
    try:
        with pytest.raises(HistoryError):
            await adapter.read_risk_limits("BTCUSDT")
    finally:
        await session.aclose()

async def test_full_precision_money_is_not_rounded_by_global_context() -> None:
    # 30 integer and 18 fractional digits are the supported source bound.
    fee = "0.123456789012345678"
    cash_flow = "-123456789012345678901234567890"
    change = "-123456789012345678901234567890.123456789012345678"
    tx = transaction(cashFlow=cash_flow, fee=fee, change=change, cashBalance=change)
    with localcontext() as context:
        context.prec = 2
        evidence = await snapshot(pages(transactions=[tx]))
        loss = daily_realized_loss(evidence, baseline(cash_balance=Decimal(0)), now=NOW)
    assert loss.daily_loss == Decimal("123456789012345678901234567890.123456789012345678")
    assert loss.trading_fees_paid == Decimal(fee)


async def test_successful_two_pages_keep_exact_ids_and_all_requests_bounded() -> None:
    data = pages()

    def execution_page(request: httpx.Request) -> dict[str, Any]:
        assert request.url.params["limit"] == "100"
        assert request.url.params["startTime"] == str(START)
        assert request.url.params["endTime"] == str(MS)
        if "cursor" not in request.url.params:
            return {"category": "linear", "list": [execution()], "nextPageCursor": "next"}
        assert request.url.params["cursor"] == "next"
        return {"category": "linear", "list": [execution(execId="e2", orderId="o2")],
                "nextPageCursor": ""}

    data["/v5/execution/list"] = execution_page
    evidence = await snapshot(data)
    assert tuple(row.execution_id for row in evidence.executions) == ("e1", "e2")


async def test_broken_bounds_and_scope_cannot_make_history_calls() -> None:
    session, adapter, seen = await reader(pages())
    try:
        for start, end in [(START, MS + 1), (START, START - 1), (START - 8 * 86400000, MS),
                           (True, MS)]:
            with pytest.raises(HistoryError):
                await adapter.read_history("BTCUSDT", start_ms=start, end_ms=end)
        with pytest.raises(ValueError, match="SCOPE_MISMATCH"):
            BybitHistoryReader(session, adapter._binding, scope=ProtectionScope("999", "DEMO"))
        assert len(seen) == 2  # Only authentication GETs.
    finally:
        await session.aclose()


async def test_conflicting_execution_totals_and_cash_time_ties_block() -> None:
    with pytest.raises(HistoryError, match="TOTAL_CONFLICT"):
        await snapshot(pages(executions=[execution(), execution(execId="e2")], orders=[order()]))
    evidence = await snapshot(pages(transactions=[transaction(cashFlow="0", fee="0", change="0",
        cashBalance="1000"), transaction(id="t2", tradeId="e2", cashFlow="0", fee="0",
                                         change="0", cashBalance="1000")]))
    with pytest.raises(HistoryError, match="AMBIGUOUS"):
        daily_realized_loss(evidence, baseline(), now=NOW)

async def test_funding_execution_fee_has_opposite_transaction_sign() -> None:
    settlement = transaction(type="SETTLEMENT", tradeId="", orderId="", cashFlow="0", funding="-2",
                             fee="0", change="-2", cashBalance="998")
    funding_execution = execution(execType="Funding", execFee="2", orderId="", orderLinkId="")
    evidence = await snapshot(pages(executions=[funding_execution], transactions=[settlement]))
    assert daily_realized_loss(evidence, baseline(), now=NOW).daily_loss == 2
    wrong_fee = replace(evidence.executions[0], fee=Decimal(-2))
    with pytest.raises(HistoryError, match="FUNDING_UNRECONCILED"):
        daily_realized_loss(replace(evidence, executions=(wrong_fee,)), baseline(), now=NOW)
