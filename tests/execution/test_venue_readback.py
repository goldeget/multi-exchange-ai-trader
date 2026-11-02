from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

from multi_exchange_trader.account.bybit_readonly import BybitReadOnlyClient
from multi_exchange_trader.execution.protection import ProtectionScope

NOW = datetime(2026, 11, 2, 1, tzinfo=UTC)
MS = int(NOW.timestamp() * 1000)


def position() -> dict[str, Any]:
    return {"symbol": "BTCUSDT", "positionIdx": 0, "side": "Buy", "size": "0.4",
                "leverage": "2", "stopLoss": "90", "takeProfit": "120", "liqPrice": "80"}


async def read(handler: Any) -> Any:
    from multi_exchange_trader.execution.venue_readback import BybitVenueReadback

    client = BybitReadOnlyClient(api_key="fake-key", api_secret="fake-secret",
        base_url="https://api-demo.bybit.com", environment="demo",
        transport=httpx.MockTransport(handler))
    client.retry_waits = ()
    client.anchor_server_time(MS)
    try:
        adapter = BybitVenueReadback(client=client, scope=ProtectionScope("account-1", "DEMO"),
            clock=lambda: NOW, max_evidence_age=timedelta(seconds=10))
        return await adapter.snapshot("BTCUSDT")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_real_get_client_preserves_raw_position_but_does_not_certify_identity() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        paths.append(request.url.path)
        rows = [position()] if request.url.path == "/v5/position/list" else []
        return httpx.Response(200, json={"retCode": 0, "time": MS,
            "result": {"category": "linear", "list": rows, "nextPageCursor": ""}})

    result = await read(handler)
    assert paths == ["/v5/position/list", "/v5/order/realtime"]
    assert result.complete
    assert result.positions[0].quantity == Decimal("0.4")
    assert result.positions[0].leverage == Decimal(2)
    assert result.positions[0].stop_loss == Decimal(90)
    assert not result.identity_verified
    assert not result.flat_verified


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "cursor", "stale", "malformed", "wrong_symbol"])
async def test_failed_or_partial_read_never_becomes_empty_complete_snapshot(failure: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("secret-body", request=request)
        row = position()
        if failure == "malformed":
            row["size"] = "NaN"
        if failure == "wrong_symbol":
            row["symbol"] = "ETHUSDT"
        return httpx.Response(200, json={"retCode": 0,
            "time": MS - 11000 if failure == "stale" else MS,
            "result": {"category": "linear", "list": [row],
                       "nextPageCursor": "again" if failure == "cursor" else ""}})

    result = await read(handler)
    assert not result.complete
    assert result.blockers
    assert not result.flat_verified
    assert "secret-body" not in repr(result)


@pytest.mark.asyncio
async def test_no_rows_does_not_prove_flat_or_release_unknown_order() -> None:
    result = await read(lambda _: httpx.Response(200, json={"retCode": 0, "time": MS,
        "result": {"category": "linear", "list": [], "nextPageCursor": ""}}))
    assert result.complete
    assert not result.flat_verified
    assert not result.identity_verified
