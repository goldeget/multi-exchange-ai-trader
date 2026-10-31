"""Manual approved spot collector; CI never invokes this entry point with network."""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from multi_exchange_trader.market_data.public_client import PublicMarketClient, parse_blocked_hosts
from multi_exchange_trader.research.collection import collect_batch
from multi_exchange_trader.research.data import save_dataset


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="UTC inclusive ISO time")
    parser.add_argument("--end", required=True, help="UTC exclusive ISO time")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start, end = (
        int(datetime.fromisoformat(v).astimezone(UTC).timestamp() * 1000)
        for v in (args.start, args.end)
    )
    blocked = parse_blocked_hosts(os.environ.get("MARKET_DATA_BLOCKED_HOSTS", ""))
    if not {"fapi.binance.com", "api.bybit.com"} <= blocked:
        raise ValueError("keep both restricted hosts preblocked")
    async with PublicMarketClient(blocked_hosts=blocked) as client:
        data = await collect_batch(client, start, end)
    ids = [save_dataset(args.output, d) for d in data]
    print(
        json.dumps(
            {
                "dataset_ids": ids,
                "scope": "Actual public SPOT observations only; 30day sample is functionality research, not profitability/perpetual/live validation.",
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
