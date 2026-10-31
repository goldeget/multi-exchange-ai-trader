"""Explicit bounded collection of the already approved public spot kline endpoint."""

import hashlib
from datetime import UTC, datetime
from typing import Any

import httpx

from ..market_data.adapters.base import MarketDataUnavailable
from .data import HOUR, SCHEMA, SPOT_URL, rows_to_bars, seal, validate_dataset


def period(symbol: str, start: int, end: int) -> None:
    if (
        symbol not in ("BTCUSDT", "ETHUSDT")
        or type(start) is not int
        or type(end) is not int
        or start < 0
        or start % HOUR
        or end % HOUR
        or not 1 <= (end - start) // HOUR <= 1000
        or end > int(datetime.now(UTC).timestamp() * 1000)
    ):
        raise ValueError("bounded closed spot period required")


async def collect_spot(
    client: httpx.AsyncClient, symbol: str, start: int, end: int
) -> dict[str, Any]:
    period(symbol, start, end)
    params: dict[str, str | int] = {
        "symbol": symbol,
        "interval": "1h",
        "startTime": start,
        "endTime": end - 1,
        "limit": 1000,
    }
    try:
        response = await client.get(SPOT_URL, params=params, timeout=15, follow_redirects=False)
    except (httpx.HTTPError, MarketDataUnavailable):
        raise ValueError("collection stopped: public transport unavailable/denied") from None
    if response.status_code != 200:
        raise ValueError(f"collection stopped: HTTP {response.status_code}; no retry/redirect")
    if len(response.content) > 1_000_000:
        raise ValueError("collection stopped: response size")
    try:
        rows = response.json()
        value = seal(
            {
                "schema": SCHEMA,
                "market": "spot",
                "symbol": symbol,
                "synthetic": False,
                "interval_ms": HOUR,
                "start_ms": start,
                "end_ms": end,
                "collected_at": datetime.now(UTC).isoformat(),
                "source": {
                    "url": SPOT_URL,
                    "raw_sha256": hashlib.sha256(response.content).hexdigest(),
                    "http_status": 200,
                    "request": params,
                },
                "raw_body": response.text,
                "bars": rows_to_bars(rows),
            }
        )
        return validate_dataset(value)
    except (ValueError, TypeError, KeyError):
        raise ValueError("collection stopped: spot quality/provenance failure") from None


async def collect_batch(client: httpx.AsyncClient, start: int, end: int) -> list[dict[str, Any]]:
    results = []
    for symbol in ("BTCUSDT", "ETHUSDT"):
        results.append(await collect_spot(client, symbol, start, end))
    return results
