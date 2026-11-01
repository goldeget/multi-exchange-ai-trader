"""Read the Bybit practice account, then place one smallest order and close it."""

from __future__ import annotations

from typing import Protocol

from multi_exchange_trader.account.bybit_demo_order import (
    BybitDemoOrderClient,
    DemoCloseFailed,
    DemoOrderRefused,
)
from multi_exchange_trader.account.bybit_readonly import (
    BybitBlocked,
    BybitReadError,
    BybitReadOnlyClient,
)
from multi_exchange_trader.account.config import allowed_host

OPEN_AND_CLOSE = (
    "연습 계좌에 고정 매수·감축 요청을 접수했습니다. 실제 체결과 포지션 종료는 확인하지 않았습니다."
)
CLOSE_WARNING = (
    "연습 주문을 넣었지만 바로 닫지 못했습니다. 다시 넣지 않았습니다. 실계좌 주문은 없습니다."
)
UNCERTAIN_WARNING = (
    "연습 주문의 처리 결과를 확인할 수 없습니다. 같은 요청을 다시 보내지 않았습니다. "
    "연습 계좌의 주문·포지션을 확인하세요. 실계좌 주문은 없습니다."
)


class PracticeUnavailable(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class PracticeCloseFailed(PracticeUnavailable):
    """One practice order is open because the single close did not succeed."""


class PracticeUncertain(PracticeUnavailable):
    """A persisted attempt without a definitive receipt must never be repeated."""


class PracticeCheck(Protocol):
    async def round_trip(self) -> str: ...


class BybitPracticeGate:
    """Read the demo wallet, then open and close the smallest demo order once."""

    def __init__(self, reader: BybitReadOnlyClient, orders: BybitDemoOrderClient) -> None:
        demo = allowed_host("demo")
        if reader.environment != "demo" or reader.base_url != demo or orders.base_url != demo:
            raise PracticeUnavailable("연습 계좌 주소가 아니라서 멈췄습니다. 실계좌 주문은 없습니다.")
        self.reader = reader
        self.orders = orders

    async def round_trip(self) -> str:
        await self._wallet()
        try:
            await self.orders.round_trip()
        except DemoCloseFailed:
            raise PracticeCloseFailed(CLOSE_WARNING) from None
        except DemoOrderRefused:
            raise PracticeUnavailable(
                "연습 주문을 넣지 못했습니다. 실계좌 주문은 없습니다."
            ) from None
        return OPEN_AND_CLOSE

    async def _wallet(self) -> None:
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
