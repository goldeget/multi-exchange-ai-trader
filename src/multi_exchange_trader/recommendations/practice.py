"""Confirm the Bybit practice account with a read. This does not place an order."""

from __future__ import annotations

from typing import Protocol

from multi_exchange_trader.account.bybit_readonly import (
    BybitBlocked,
    BybitReadError,
    BybitReadOnlyClient,
)
from multi_exchange_trader.account.config import allowed_host


class PracticeUnavailable(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class PracticeCheck(Protocol):
    async def confirm(self) -> str: ...


class BybitPracticeGate:
    """Read the demo wallet. The order helper stays unused here."""

    def __init__(self, reader: BybitReadOnlyClient) -> None:
        if reader.environment != "demo" or reader.base_url != allowed_host("demo"):
            raise PracticeUnavailable("연습 계좌 주소가 아니라서 멈췄습니다. 실계좌 주문은 없습니다.")
        self.reader = reader

    async def confirm(self) -> str:
        try:
            _time_result, server_ms = await self.reader.read("/v5/market/time", [], signed=False)
            if server_ms is None:
                raise BybitReadError("invalid_json")
            self.reader.anchor_server_time(server_ms)
            wallet, _wallet_ms = await self.reader.read(
                "/v5/account/wallet-balance",
                [("accountType", "UNIFIED")],
                signed=True,
            )
        except (BybitReadError, BybitBlocked):
            raise PracticeUnavailable(
                "연습 계좌 잔고를 확인하지 못했습니다. 실계좌 주문은 없습니다. 연습 주문도 넣지 않았습니다."
            ) from None
        rows = wallet.get("list")
        if not isinstance(rows, list) or not rows:
            raise PracticeUnavailable(
                "연습 계좌 잔고를 확인하지 못했습니다. 실계좌 주문은 없습니다. 연습 주문도 넣지 않았습니다."
            )
        return "연습 계좌 잔고를 확인했습니다. 연습 주문은 자동으로 넣지 않았습니다."
