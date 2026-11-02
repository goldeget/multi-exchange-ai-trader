import importlib
from decimal import Decimal

import httpx
import pytest

from multi_exchange_trader.account.bybit_readonly import BybitReadOnlyClient


async def test_physical_attempt_budget_stops_before_fourth_send(monkeypatch):
    assert importlib.util.find_spec("multi_exchange_trader.execution.monitoring_budget")
    from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget

    sent = []

    async def send(client, request, **kwargs):
        sent.append(request)
        return httpx.Response(503, content=b"", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    client = BybitReadOnlyClient(
        api_key="synthetic",
        api_secret="synthetic-secret",
        base_url="https://api-demo.bybit.com",
        environment="demo",
    )
    client.retry_waits = (0, 0, 0, 0)
    budget = MonitoringReadBudget()
    try:
        with client._monitoring_operation(budget), pytest.raises(ValueError, match="ATTEMPTS"):
            await client.read("/v5/market/time", [], signed=False)
        assert len(sent) == 3
        assert budget.physical_attempts == 3
    finally:
        await client.aclose()


async def test_rate_limit_is_not_retried_under_monitoring_budget(monkeypatch):
    assert importlib.util.find_spec("multi_exchange_trader.execution.monitoring_budget")
    from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget

    sent = []

    async def send(client, request, **kwargs):
        sent.append(request)
        return httpx.Response(429, content=b"", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    client = BybitReadOnlyClient(
        api_key="synthetic",
        api_secret="synthetic-secret",
        base_url="https://api-demo.bybit.com",
        environment="demo",
    )
    try:
        with (
            client._monitoring_operation(MonitoringReadBudget()),
            pytest.raises(ValueError, match="QUOTA"),
        ):
            await client.read("/v5/market/time", [], signed=False)
        assert len(sent) == 1
    finally:
        await client.aclose()


@pytest.fixture
async def reader_core(monkeypatch):
    from test_monitoring_receipts import MS, NOW
    from test_monitoring_reconciliation import sample

    from multi_exchange_trader.execution.account_binding import BybitDemoAccountSession

    requests, overrides = [], {}

    def row():
        return {
            "orderId": "venue-1",
            "orderLinkId": "client-1",
            "symbol": "BTCUSDT",
            "side": "Buy",
            "positionIdx": 0,
            "reduceOnly": False,
            "orderStatus": "New",
            "qty": "2",
            "cumExecQty": "0",
            "leavesQty": "2",
            "createdTime": str(MS - 1000),
            "updatedTime": str(MS - 1000),
            "orderType": "Limit",
            "timeInForce": "GTC",
            "price": "100",
            "stopOrderType": "",
            "triggerPrice": "",
            "triggerDirection": 0,
            "closeOnTrigger": False,
            "takeProfit": "",
            "stopLoss": "",
            "createType": "CreateByUser",
        }

    async def send(client, request, **kwargs):
        path = request.url.path
        requests.append(request)
        if path in overrides:
            result = overrides[path](request)
        elif path == "/v5/user/query-api":
            result = {
                "apiKey": "synthetic-key",
                "secret": "",
                "userID": 12345,
                "isMaster": False,
                "parentUid": "0",
                "readOnly": 1,
                "permissions": {"Wallet": []},
            }
        elif path == "/v5/position/list":
            result = {
                "category": "linear",
                "list": [{"symbol": "BTCUSDT", "positionIdx": 0, "side": "", "size": "0"}],
                "nextPageCursor": "",
            }
        else:
            result = {
                "category": "linear",
                "list": [row()] if path == "/v5/order/realtime" else [],
                "nextPageCursor": "",
            }
        return httpx.Response(
            200, json={"retCode": 0, "time": MS, "result": result}, request=request
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    owner = BybitDemoAccountSession(
        api_key="synthetic-key", api_secret="synthetic-secret", clock=lambda: NOW
    )
    binding = await owner.authenticate(
        expected_uid="12345", expected_parent_uid="0", expected_is_master=False
    )
    target, _ = sample()
    yield owner, binding, target, requests, overrides, row
    await owner.aclose()


async def read(core):
    assert importlib.util.find_spec("multi_exchange_trader.execution.monitoring_readback")
    from multi_exchange_trader.execution.monitoring_readback import BybitMonitoringReader

    owner, binding, target, *_ = core
    return await BybitMonitoringReader(owner, binding, target=target, guard=lambda: None).read()


async def test_positive_order_creation_drives_unfiltered_execution_sweep(reader_core):
    from test_monitoring_receipts import MS

    from multi_exchange_trader.execution.monitoring_reconciliation import compare_restored_state

    result = await read(reader_core)
    assert compare_restored_state(reader_core[2], result).status.value == "MATCHED"
    request = next(r for r in reader_core[3] if r.url.path == "/v5/execution/list")
    assert request.url.params["startTime"] == str(MS - 1000)
    assert request.url.params["endTime"] == str(MS)
    assert "orderId" not in request.url.params and "orderLinkId" not in request.url.params


@pytest.mark.parametrize(
    "failure",
    [
        "cursor-cap",
        "repeat",
        "empty",
        "wrong-category",
        "wrong-symbol",
        "duplicate-conflict",
        "missing-cursor",
    ],
)
async def test_invalid_or_incomplete_pages_block(reader_core, failure):
    calls = []

    def page(request):
        calls.append(request)
        row = reader_core[5]()
        if failure == "wrong-symbol":
            row["symbol"] = "ETHUSDT"
        rows = [] if failure == "empty" else [row]
        if failure == "duplicate-conflict":
            rows.append({**row, "qty": "3"})
        result = {
            "category": "spot" if failure == "wrong-category" else "linear",
            "list": rows,
            "nextPageCursor": "same"
            if failure in ("repeat", "empty")
            else str(len(calls))
            if failure == "cursor-cap"
            else "",
        }
        if failure == "missing-cursor":
            result.pop("nextPageCursor")
        return result

    reader_core[4]["/v5/order/realtime"] = page
    with pytest.raises(ValueError):
        await read(reader_core)
    assert len(calls) <= 5


async def test_two_page_order_sweep_completes_and_preserves_broad_filters(reader_core):
    from dataclasses import replace

    from multi_exchange_trader.execution.monitoring_reconciliation import compare_restored_state

    core = list(reader_core)
    native = core[2].native
    core[2] = replace(
        core[2],
        native=replace(
            native,
            orders=(
                *native.orders,
                replace(native.orders[0], client_order_id="client-2", venue_order_id="venue-2"),
            ),
        ),
    )

    def page(request):
        row = core[5]()
        if "cursor" in request.url.params:
            row.update(orderId="venue-2", orderLinkId="client-2")
        assert request.url.params["openOnly"] == "0"
        assert "orderFilter" not in request.url.params
        return {
            "category": "linear",
            "list": [row],
            "nextPageCursor": "" if "cursor" in request.url.params else "next",
        }

    core[4]["/v5/order/realtime"] = page
    assert compare_restored_state(core[2], await read(core)).status.value == "MATCHED"


@pytest.mark.parametrize(
    "failure",
    [
        "old-created",
        "old-zero-terminal",
        "unknown-economic",
        "unowned-execution",
        "out-of-window",
        "later-failure",
        "wrong-shape",
    ],
)
async def test_retention_economic_and_later_page_failure_block(reader_core, failure):
    from test_monitoring_receipts import MS

    def order_page(request):
        row = reader_core[5]()
        if failure == "old-created":
            row["createdTime"] = str(MS - 7 * 86400000 - 1)
        if failure == "old-zero-terminal":
            row.update(orderStatus="Cancelled", createdTime=str(MS - 86400000 - 1))
        if failure == "wrong-shape":
            row["qty"] = "3"
        if failure == "later-failure":
            if "cursor" in request.url.params:
                raise httpx.NetworkError("synthetic")
            return {"category": "linear", "list": [row], "nextPageCursor": "next"}
        return {"category": "linear", "list": [row], "nextPageCursor": ""}

    def fills(request):
        row = {
            "execId": "fill-1",
            "orderId": "venue-1",
            "orderLinkId": "client-1",
            "symbol": "BTCUSDT",
            "side": "Buy",
            "execType": "Trade",
            "execTime": str(MS - 500),
            "execQty": "2",
            "execPrice": "100",
            "execValue": "200",
            "execFee": "0.1",
            "feeCurrency": "USDT",
            "feeRate": "0.0005",
            "closedSize": "0",
            "isMaker": True,
            "seq": 1,
        }
        if failure == "unknown-economic":
            row["execType"] = "Funding"
        elif failure == "unowned-execution":
            row["orderId"] = "foreign"
        elif failure == "out-of-window":
            row["execTime"] = str(MS + 1)
        return {"category": "linear", "list": [row], "nextPageCursor": ""}

    reader_core[4]["/v5/order/realtime"] = order_page
    if failure in ("unknown-economic", "unowned-execution", "out-of-window"):
        reader_core[4]["/v5/execution/list"] = fills
    with pytest.raises((ValueError, RuntimeError)):
        await read(reader_core)


def test_budget_caps_200_physical_attempts_and_32_logical_gets():
    from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget

    budget = MonitoringReadBudget()
    for _ in range(200):
        budget.before_attempt(0)
    with pytest.raises(ValueError, match="ATTEMPTS"):
        budget.before_attempt(0)
    bounded = MonitoringReadBudget()
    with bounded.receipt():
        for _ in range(32):
            bounded.begin_read()
        with pytest.raises(ValueError, match="LOGICAL"):
            bounded.begin_read()


def test_budget_deadline_and_monotonic_rollback(monkeypatch):
    import multi_exchange_trader.execution.monitoring_budget as module

    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    budget = module.MonitoringReadBudget()
    with budget.receipt():
        clock[0] = 120.0
        with pytest.raises(ValueError, match="DEADLINE"):
            budget.check()
    clock[0] = 160.0
    with pytest.raises(ValueError, match="DEADLINE"):
        budget.check()
    clock[0] = 200.0
    budget = module.MonitoringReadBudget()
    clock[0] = 199.0
    with pytest.raises(ValueError, match="CLOCK"):
        budget.check()


@pytest.mark.parametrize("bad", [False, True])
async def test_full_original_filled_readback_and_exact_execution_value(reader_core, bad):
    from dataclasses import replace

    from test_monitoring_receipts import MS
    from test_monitoring_reconciliation import filled_sample

    from multi_exchange_trader.execution.monitoring_models import MonitoringPosition
    from multi_exchange_trader.execution.monitoring_reconciliation import compare_restored_state

    core = list(reader_core)
    target, _ = filled_sample()
    core[2] = replace(
        target,
        native=replace(
            target.native,
            executions=(replace(target.native.executions[0], ts_event_ns=(MS - 500) * 1000000),),
        ),
    )
    order = core[5]()
    order.update(orderStatus="Filled", cumExecQty="2", leavesQty="0", updatedTime=str(MS - 500))
    core[4]["/v5/order/realtime"] = lambda request: {
        "category": "linear",
        "list": [],
        "nextPageCursor": "",
    }
    core[4]["/v5/order/history"] = lambda request: {
        "category": "linear",
        "list": [order],
        "nextPageCursor": "",
    }
    core[4]["/v5/position/list"] = lambda request: {
        "category": "linear",
        "list": [{"symbol": "BTCUSDT", "positionIdx": 0, "side": "Buy", "size": "2"}],
        "nextPageCursor": "",
    }
    execution = {
        "execId": "fill-1",
        "orderId": "venue-1",
        "orderLinkId": "client-1",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "execType": "Trade",
        "execTime": str(MS - 500),
        "execQty": "2",
        "execPrice": "100",
        "execValue": "201" if bad else "200",
        "execFee": "0.1",
        "feeCurrency": "USDT",
        "feeRate": "0.0005",
        "closedSize": "0",
        "isMaker": True,
        "seq": 1,
        "orderType": "Limit",
        "orderQty": "2",
        "orderPrice": "100",
        "leavesQty": "0",
        "stopOrderType": "",
    }
    core[4]["/v5/execution/list"] = lambda request: {
        "category": "linear",
        "list": [execution],
        "nextPageCursor": "",
    }
    if bad:
        with pytest.raises(ValueError):
            await read(core)
    else:
        result = await read(core)
        assert result.positions == (MonitoringPosition("BTCUSDT", Decimal(2)),)
        assert (
            compare_restored_state(core[2], result).status.value
            == "MATCHED_NATIVE_WITH_UNCREDITED_FILLS"
        )

@pytest.mark.parametrize('failure', ['physical', 'logical', 'quota', 'deadline'])
def test_terminal_budget_fault_survives_target_change_and_new_receipt(failure, monkeypatch):
    import multi_exchange_trader.execution.monitoring_budget as module

    now = [100.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    budget = module.MonitoringReadBudget()
    with pytest.raises(ValueError):
        if failure == 'physical':
            budget.before_attempt(3)
        elif failure == 'logical':
            with budget.receipt():
                for _ in range(33):
                    budget.begin_read()
        elif failure == 'quota':
            budget.response(429, {})
        else:
            now[0] = 161.0
            budget.check()
    now[0] = 100.0
    with pytest.raises(ValueError):
        budget.before_attempt(0)
    assert budget.physical_attempts == 0

@pytest.mark.parametrize('field,value', [('quantity', Decimal(3)), ('price', Decimal(101)),
                                       ('venue_order_id', 'foreign'), ('time_in_force', 'IOC')])
def test_positive_owned_order_immutable_economics_are_validated_per_response(field, value):
    from dataclasses import replace

    from test_monitoring_reconciliation import sample

    import multi_exchange_trader.execution.monitoring_readback as module
    target, observation = sample()
    assert hasattr(module, '_validate_positive_order'), 'owned row shape must validate before retarget'
    module._validate_positive_order(target.native.orders[0], observation.orders[0])
    with pytest.raises(ValueError):
        module._validate_positive_order(target.native.orders[0], replace(observation.orders[0], **{field: value}))
