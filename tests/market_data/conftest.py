"""Complete wire rows for the documented public endpoints; no upstream calls."""

import copy

import httpx
import pytest

from multi_exchange_trader.market_data.adapters.binance import BinanceAdapter
from multi_exchange_trader.market_data.adapters.bybit import BybitAdapter


@pytest.fixture(params=[BinanceAdapter, BybitAdapter], ids=["binance", "bybit"])
def adapter_class(request):
    return request.param


def binance_row(start=1704067200000):
    return [
        start,
        "10.1234567890123456789",
        "12",
        "9",
        "11",
        "2.5",
        start + 59999,
        "26",
        4,
        "1",
        "11",
        "0",
    ]


def bybit_row(start="1704067200000"):
    return [start, "10.1234567890123456789", "12", "9", "11", "2.5", "26"]


def payload(adapter_class, kind="ticker", symbol="BTCUSDT", market="spot"):
    if adapter_class is BinanceAdapter:
        if kind == "candles":
            return [binance_row()]
        item = {"symbol": symbol, "price": "10.1234567890123456789"}
        if market == "perpetual":
            item["time"] = 1704067260000
        return item
    category = "spot" if market == "spot" else "linear"
    rows = (
        [
            {
                "symbol": symbol,
                "lastPrice": "10.1234567890123456789",
                "bid1Price": "10",
                "bid1Size": "2",
                "ask1Price": "11",
                "ask1Size": "3",
                "prevPrice24h": "10",
                "price24hPcnt": "0.01",
                "highPrice24h": "12",
                "lowPrice24h": "9",
                "turnover24h": "26",
                "volume24h": "2.5",
                "usdIndexPrice": "10",
            }
        ]
        if kind == "ticker"
        else [bybit_row()]
    )
    result = {"category": category, "list": rows}
    if kind == "candles":
        result["symbol"] = symbol
    return {"retCode": 0, "retMsg": "OK", "retExtInfo": {}, "time": 1704067260000, "result": result}


@pytest.fixture
def make_client():

    def make(body, status=200, handler=None):
        def respond(request):
            return httpx.Response(status, json=copy.deepcopy(body))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler or respond))
        return client

    return make
