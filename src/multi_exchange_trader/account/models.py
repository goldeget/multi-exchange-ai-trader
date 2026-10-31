"""Korean account view. Numbers are absent unless Bybit actually returned them."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from .config import EnvironmentName
from .freshness import STALE_AFTER_SECONDS, UNUSABLE_AFTER_SECONDS

Connection = Literal["ok", "BLOCKED", "error", "missing_credentials", "config"]


class LeverageInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    종목: Literal["BTCUSDT"]
    최대: str
    최소: str
    간격: str


class CoinBalance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    코인: str
    지갑잔고: str | None
    자산평가: str | None


class WalletView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    계정유형: Literal["통합 계좌"] = "통합 계좌"
    총자산평가: str | None
    총지갑잔고: str | None
    총사용가능: str | None
    코인: list[CoinBalance]


class PositionItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    출처: Literal["external"] = "external"
    관리함: Literal[False] = False
    구분: Literal["외부에서 연 포지션"] = "외부에서 연 포지션"
    종목: str
    방향: str
    수량: str
    레버리지: str | None
    진입가: str | None
    표시가: str | None
    청산가: str | None
    미실현손익: str | None
    마진모드: str
    포지션모드: str


class OrderItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    출처: Literal["external"] = "external"
    관리함: Literal[False] = False
    구분: Literal["외부에서 연 주문"] = "외부에서 연 주문"
    종목: str
    방향: str
    유형: str
    수량: str
    가격: str | None
    상태: str
    줄이기만: str


class AccountView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    연결상태: str
    상태코드: Connection
    환경: str
    환경코드: EnvironmentName | None
    구분: list[str]
    페이퍼: Literal[False] = False
    실거래켜짐: bool
    주문보냄: Literal[False] = False
    메시지: str
    서버시각: str | None
    최대레버리지: LeverageInfo | None
    잔고: WalletView | None
    포지션: list[PositionItem] | None
    미체결주문: list[OrderItem] | None
    오류코드: str | None
    계좌마진모드: str = "확인 불가"
    계좌마진설명: str = "거래소 계정 정보입니다. 포지션의 예전 항목으로 짐작하지 않습니다."
    레버리지안내: str = "종목 안내에 적힌 최대 배수입니다. 지금 포지션 크기에 실제로 허용되는 배수와 같지 않을 수 있습니다."
    순차조회: bool = True
    수집시작시각: str | None = None
    수집완료시각: str | None = None
    거래소응답시각: str | None = None
    계정갱신시각: str | None = None
    수신시각: str | None = None
    캐시사용: bool = False
    캐시나이초: int = 0
    데이터나이초: int = 0
    동시요청공유: bool = False
    지연기준초: int = STALE_AFTER_SECONDS
    확인불가기준초: int = UNUSABLE_AFTER_SECONDS
    신선도표시: str = "현재 확인 불가"
    주문판단에사용: bool = False
