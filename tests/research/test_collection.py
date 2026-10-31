"""Offline MockTransport public GET proof; no exchange contacts in CI."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from multi_exchange_trader.market_data.public_client import PublicMarketClient

START = 1767225600000
END = START + 3600000 * 2


def rows():
    return [
        [
            START + i * 3600000,
            "60000.00",
            "60100.00",
            "59900.00",
            "60010.00",
            "1.00",
            START + (i + 1) * 3600000 - 1,
            "60000",
            1,
            "0",
            "0",
            "0",
        ]
        for i in range(2)
    ]


async def test_collector_fixed_host_get_no_body_exact_period_and_raw_checksums():
    from multi_exchange_trader.research.collection import collect_spot

    sent = []

    def handler(q):
        sent.append(q)
        assert q.method == "GET" and q.content == b""
        assert str(q.url.copy_with(query=None)) == "https://data-api.binance.vision/api/v3/klines"
        assert dict(q.url.params) == {
            "symbol": "BTCUSDT",
            "interval": "1h",
            "startTime": str(START),
            "endTime": str(END - 1),
            "limit": "1000",
        }
        return httpx.Response(200, json=rows())

    async with PublicMarketClient(transport=httpx.MockTransport(handler)) as client:
        result = await collect_spot(client, "BTCUSDT", START, END)
    assert len(sent) == 1 and result["synthetic"] is False and result["market"] == "spot"
    assert json.loads(result["raw_body"]) == rows()
    assert datetime.fromisoformat(result["collected_at"]) <= datetime.now(UTC)
    from multi_exchange_trader.research.data import validate_dataset

    assert validate_dataset(result) == result


@pytest.mark.parametrize("status", [403, 451, 302])
async def test_denial_or_redirect_stops_batch_no_second_transmission(status):
    from multi_exchange_trader.research.collection import collect_batch

    sent = []

    def handler(q):
        sent.append(q)
        return httpx.Response(
            status,
            headers={"location": "https://fapi.binance.com/never"},
            text="sensitive untrusted response",
        )

    async with PublicMarketClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="collection stopped") as err:
            await collect_batch(client, START, END)
    assert len(sent) == 1 and "sensitive" not in str(err.value)


async def test_invalid_period_or_symbol_never_sends():
    from multi_exchange_trader.research.collection import collect_spot

    def forbidden(q):
        raise AssertionError("network before validation")

    async with PublicMarketClient(transport=httpx.MockTransport(forbidden)) as client:
        for symbol, start, end in [
            ("BTC-PAPER", START, END),
            ("BTCUSDT", START + 1, END),
            ("BTCUSDT", START, START + 1001 * 3600000),
        ]:
            with pytest.raises(ValueError):
                await collect_spot(client, symbol, start, end)
