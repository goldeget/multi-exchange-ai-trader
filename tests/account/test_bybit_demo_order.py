"""Fake Bybit demo client. No network, no leverage, no leftover position."""

import json

import httpx
import pytest

from multi_exchange_trader.account.bybit_demo_order import (
    DEMO_HOST,
    BybitDemoOrderClient,
    DemoCloseFailed,
    DemoOrderRefused,
)
from multi_exchange_trader.account.config import MissingBybitCredentials, require_demo_credentials

KEY = "demo-order-key"
SECRET = "demo-order-secret"


def _time() -> httpx.Response:
    return httpx.Response(
        200,
        json={"retCode": 0, "retMsg": "OK", "result": {"timeSecond": "1700000000"}, "time": 1700000000000},
    )


def _order_ok() -> httpx.Response:
    return httpx.Response(
        200,
        json={"retCode": 0, "retMsg": "OK", "result": {"orderId": "1"}, "time": 1700000000000},
    )


@pytest.mark.asyncio
async def test_round_trip_stays_on_demo_and_closes_reduce_only() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.host == "api-demo.bybit.com"
        assert "set-leverage" not in request.url.path
        if request.method == "GET":
            assert request.url.path == "/v5/market/time"
            return _time()
        assert request.method == "POST"
        assert request.url.path == "/v5/order/create"
        body = json.loads(request.content)
        assert "leverage" not in body
        assert body["qty"] == "0.001"
        assert body["symbol"] == "BTCUSDT"
        return _order_ok()

    client = BybitDemoOrderClient(
        api_key=KEY, api_secret=SECRET, transport=httpx.MockTransport(handler)
    )
    try:
        await client.round_trip()
    finally:
        await client.aclose()
    bodies = [json.loads(item.content) for item in seen if item.method == "POST"]
    assert [item["side"] for item in bodies] == ["Buy", "Sell"]
    assert bodies[0]["reduceOnly"] is False
    assert bodies[1]["reduceOnly"] is True
    assert DEMO_HOST == "https://api-demo.bybit.com"
    assert all(item.url.host != "api.bybit.com" for item in seen)


@pytest.mark.asyncio
async def test_close_failure_sends_one_reduce_only_close() -> None:
    posts: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api-demo.bybit.com"
        if request.method == "GET":
            return _time()
        posts.append(json.loads(request.content))
        if len(posts) == 1:
            return _order_ok()
        return httpx.Response(200, json={"retCode": 110017, "retMsg": "no", "result": {}, "time": 1})

    client = BybitDemoOrderClient(
        api_key=KEY, api_secret=SECRET, transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(DemoCloseFailed):
            await client.round_trip()
    finally:
        await client.aclose()
    assert len(posts) == 2
    assert posts[0]["side"] == "Buy" and posts[0]["reduceOnly"] is False
    assert posts[1]["side"] == "Sell" and posts[1]["reduceOnly"] is True
    assert all("leverage" not in body for body in posts)


@pytest.mark.asyncio
async def test_order_helper_rejects_leverage_without_sending() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _order_ok()

    client = BybitDemoOrderClient(
        api_key=KEY, api_secret=SECRET, transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(DemoOrderRefused):
            await client.submit({**client.open_body(), "leverage": "10"})
    finally:
        await client.aclose()
    assert seen == []


def test_demo_key_must_not_be_the_live_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BYBIT_ENV_FILE", "off")
    monkeypatch.setenv("BYBIT_DEMO", "true")
    monkeypatch.setenv("BYBIT_API_KEY", KEY)
    monkeypatch.setenv("BYBIT_API_SECRET", SECRET)
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", KEY)
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "different-demo-secret")
    with pytest.raises(MissingBybitCredentials) as exc:
        require_demo_credentials()
    assert KEY not in str(exc.value)
    assert SECRET not in str(exc.value)
