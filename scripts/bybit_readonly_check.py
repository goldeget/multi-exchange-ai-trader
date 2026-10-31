"""Print one redacted Bybit read-only snapshot.

This script never sends an order, changes leverage, or prints the API key or
secret. Exit 0 means the read succeeded, 2 means Bybit returned BLOCKED, and
any other non-zero code is an error. A leak check prints LEAK_DETECTED and
exits 3 without the snapshot.
"""

import asyncio
import json
import sys

from multi_exchange_trader.account.bybit_readonly import BybitReadOnlyClient
from multi_exchange_trader.account.config import (
    MissingBybitCredentials,
    live_trading_enabled,
    text_contains_credential,
)
from multi_exchange_trader.account.service import build_account_view, publish


async def _run() -> int:
    try:
        client = BybitReadOnlyClient.from_environment()
    except MissingBybitCredentials:
        print(json.dumps({"상태코드": "missing_credentials"}, ensure_ascii=False))
        return 1
    except ValueError:
        print(json.dumps({"상태코드": "config"}, ensure_ascii=False))
        return 1
    try:
        view = publish(
            await build_account_view(client, None, live_trading=live_trading_enabled())
        )
    finally:
        await client.aclose()
    text = json.dumps(view.model_dump(by_alias=True, mode="json"), ensure_ascii=False)
    if text_contains_credential(text):
        print("LEAK_DETECTED")
        return 3
    print(text)
    if view.상태코드 == "BLOCKED":
        return 2
    if view.상태코드 != "ok":
        return 1
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
