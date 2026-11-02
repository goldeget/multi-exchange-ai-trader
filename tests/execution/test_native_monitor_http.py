"""Real pyo3 conversion and session physical-send-budget boundaries."""
# ruff: noqa: F811 -- shared pytest session fixture

import importlib
from decimal import Decimal

import httpx
import pytest
from test_monitoring_receipts import SCOPE, session  # noqa: F401


def module():
    spec = importlib.util.find_spec("multi_exchange_trader.execution.native_monitor_http")
    assert spec is not None, "bounded native HTTP conversion is required"
    return importlib.import_module(spec.name)


def instrument_row():
    return {
        "symbol": "BTCUSDT",
        "contractType": "LinearPerpetual",
        "status": "Trading",
        "baseCoin": "BTC",
        "quoteCoin": "USDT",
        "settleCoin": "USDT",
        "priceFilter": {"tickSize": "0.10", "minPrice": "0.10", "maxPrice": "999999.90"},
        "lotSizeFilter": {
            "qtyStep": "0.001",
            "minOrderQty": "0.001",
            "maxOrderQty": "100.000",
            "minNotionalValue": "5",
        },
    }


def fee_row():
    return {"symbol": "BTCUSDT", "makerFeeRate": "0.0002", "takerFeeRate": "0.00055"}


def wallet_row():
    return {
        "accountType": "UNIFIED",
        "coin": [
            {
                "coin": "USDT",
                "walletBalance": "100",
                "spotBorrow": "2",
                "locked": "3",
                "totalPositionIM": "4",
                "totalOrderIM": "5",
                "totalPositionMM": "6",
            }
        ],
    }


def test_real_instrument_conversion_has_exact_full_economics():
    mod = module()
    from nautilus_trader.core import nautilus_pyo3 as p
    from nautilus_trader.model.instruments import instruments_from_pyo3

    native = mod.convert_instrument(instrument_row(), fee_row(), "BTCUSDT", 1000)
    assert type(native) is p.CryptoPerpetual
    converted = instruments_from_pyo3([native])[0]
    raw = converted.to_dict(converted)
    assert raw["id"] == "BTCUSDT-LINEAR.BYBIT"
    assert raw["price_precision"] == 2 and raw["size_precision"] == 3
    assert raw["price_increment"] == "0.10" and raw["size_increment"] == "0.001"
    assert raw["margin_init"] == raw["margin_maint"] == "0.1"
    assert raw["maker_fee"] == "0.0002" and raw["taker_fee"] == "0.00055"
    assert converted.min_notional.as_decimal() == Decimal(5)
    assert converted.max_quantity.as_decimal() == Decimal(100)
    assert converted.lot_size.as_decimal() == Decimal("0.001")
    assert converted.ts_event == converted.ts_init == 1000000000
    assert raw == {
        "type": "CryptoPerpetual",
        "id": "BTCUSDT-LINEAR.BYBIT",
        "raw_symbol": "BTCUSDT",
        "base_currency": "BTC",
        "quote_currency": "USDT",
        "settlement_currency": "USDT",
        "is_inverse": False,
        "price_precision": 2,
        "price_increment": "0.10",
        "size_precision": 3,
        "size_increment": "0.001",
        "multiplier": "1",
        "lot_size": "0.001",
        "max_quantity": "100.000",
        "min_quantity": "0.001",
        "max_notional": None,
        "min_notional": "5.00000000 USDT",
        "max_price": "999999.90",
        "min_price": "0.10",
        "margin_init": "0.1",
        "margin_maint": "0.1",
        "maker_fee": "0.0002",
        "taker_fee": "0.00055",
        "ts_event": 1000000000,
        "ts_init": 1000000000,
        "tick_scheme_name": None,
        "info": {},
    }


@pytest.mark.parametrize(
    "change", ["missing-fee", "missing-currency", "money-rounding", "wrong-symbol"]
)
def test_instrument_missing_or_unrepresentable_economics_never_fallback(change):
    mod = module()
    row, fee = instrument_row(), fee_row()
    if change == "missing-fee":
        fee.pop("makerFeeRate")
    elif change == "missing-currency":
        row["settleCoin"] = "UNREGISTERED"
    elif change == "money-rounding":
        row["lotSizeFilter"]["minNotionalValue"] = "5.000000001"
    else:
        fee["symbol"] = "ETHUSDT"
    with pytest.raises(ValueError):
        mod.convert_instrument(row, fee, "BTCUSDT", 1000)


def test_real_wallet_conversion_preserves_borrow_lock_and_margin():
    mod = module()
    from nautilus_trader.core import nautilus_pyo3 as p
    from nautilus_trader.model.events import AccountState

    state = mod.convert_account(wallet_row(), p.AccountId("BYBIT-UNIFIED"), 1000)
    assert type(state) is p.AccountState
    converted = AccountState.from_dict(state.to_dict())
    balance, margin = converted.balances[0], converted.margins[0]
    assert (balance.total.as_decimal(), balance.locked.as_decimal(), balance.free.as_decimal()) == (
        Decimal(98),
        Decimal(3),
        Decimal(95),
    )
    assert (margin.initial.as_decimal(), margin.maintenance.as_decimal()) == (
        Decimal(9),
        Decimal(6),
    )
    from nautilus_trader.cache.cache import Cache
    from nautilus_trader.common.component import LiveClock, MessageBus
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.model.identifiers import TraderId
    from nautilus_trader.portfolio.portfolio import Portfolio

    cache, clock = Cache(), LiveClock()
    bus = MessageBus(trader_id=TraderId("TRADER-001"), clock=clock)
    portfolio = Portfolio(msgbus=bus, cache=cache, clock=clock)
    # The actual Bybit lifecycle extracts only balances/margins from this
    # intermediate. ExecutionClient then generates with its base_currency=None.
    generated = AccountState(
        account_id=converted.account_id,
        account_type=converted.account_type,
        base_currency=None,
        balances=converted.balances,
        margins=converted.margins,
        reported=True,
        info={},
        event_id=UUID4(),
        ts_event=1000000000,
        ts_init=1000000000,
    )
    portfolio.update_account(generated)
    account = cache.account(generated.account_id)
    assert account.last_event is generated and account.base_currency is None
    assert account.balance_total(converted.balances[0].currency).as_decimal() == Decimal(98)
    assert account.balance(balance.currency) == balance
    assert account.margin_for_currency(margin.currency) == margin


@pytest.mark.parametrize(
    "field,value",
    [
        ("spotBorrow", None),
        ("coin", "UNKNOWN"),
        ("locked", "101"),
        ("walletBalance", "1.000000001"),
        ("totalPositionIM", ""),
    ],
)
def test_wallet_missing_currency_or_economics_never_fabricate_balance(field, value):
    mod = module()
    from nautilus_trader.core import nautilus_pyo3 as p

    row = wallet_row()
    row["coin"][0][field] = value
    with pytest.raises(ValueError):
        mod.convert_account(row, p.AccountId("BYBIT-UNIFIED"), 1000)


async def test_real_session_native_reads_share_physical_budget_and_reject_unknown(
    session, monkeypatch
):
    mod = module()
    from nautilus_trader.core import nautilus_pyo3 as p

    from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget

    owner, binding, wall, _, requests = session
    faults = []
    adapter = mod.MonitoringHttpAdapter(
        session=owner, binding=binding, scope=SCOPE, symbols=("BTCUSDT",), fault=faults.append
    )

    async def send(client, request, **kwargs):
        requests.append(request)
        rows = [fee_row()] if request.url.path.endswith("fee-rate") else [instrument_row()]
        return httpx.Response(
            200,
            request=request,
            json={
                "retCode": 0,
                "time": int(wall[0].timestamp() * 1000),
                "result": {"list": rows, "category": "linear", "nextPageCursor": ""},
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    budget = MonitoringReadBudget(guard=lambda: None)
    with owner._monitoring_operation(budget):
        result = await adapter.request_instruments(p.BybitProductType.LINEAR, "BTCUSDT", None)
    assert type(result[0]) is p.CryptoPerpetual
    assert budget.physical_attempts == 2
    before = len(requests)
    with pytest.raises(ValueError):
        adapter.place_order(symbol="BTCUSDT")
    assert faults and len(requests) == before


@pytest.mark.parametrize(
    "path,params,signed",
    [
        ("/v5/account/fee-rate", [("category", "linear"), ("symbol", "ETHUSDT")], True),
        ("/v5/account/wallet-balance", [("accountType", "CONTRACT")], True),
        ("/v5/market/instruments-info", [("category", "linear"), ("symbol", "BTCUSDT")], True),
    ],
)
async def test_final_http_boundary_checks_exact_scope_before_send(session, path, params, signed):
    mod = module()
    from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget

    owner, binding, _, _, requests = session
    adapter = mod.MonitoringHttpAdapter(
        session=owner, binding=binding, scope=SCOPE, symbols=("BTCUSDT",), fault=lambda reason: None
    )
    before = len(requests)
    with (
        owner._monitoring_operation(MonitoringReadBudget(guard=lambda: None)),
        pytest.raises(ValueError),
    ):
        await adapter._get(path, params, signed=signed)
    assert len(requests) == before
