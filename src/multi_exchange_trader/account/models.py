"""Korean account view. Numbers are absent unless Bybit actually returned them."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from .config import EnvironmentName

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

    계정유형: Literal["통합(UNIFIED)"] = "통합(UNIFIED)"
    총자산평가: str | None
    총지갑잔고: str | None
    총사용가능: str | None
    코인: list[CoinBalance]


class PositionItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    출처: Literal["external"] = "external"
    관리함: Literal[False] = False
    구분: Literal["외부 · 관리 안 함"] = "외부 · 관리 안 함"
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
    구분: Literal["외부 · 관리 안 함"] = "외부 · 관리 안 함"
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
