"""Decimal-safe, immutable public market observations."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    model_validator,
)

MarketType = Literal["spot", "perpetual"]


def _decimal_input(value: object) -> object:
    if isinstance(value, (float, bool)):
        raise ValueError("Use decimal strings, integers or Decimal values")  # noqa: TRY004 - Pydantic validators require ValueError
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An aware timestamp is required")
    return value.astimezone(UTC)


Price = Annotated[Decimal, BeforeValidator(_decimal_input), Field(ge=0, allow_inf_nan=False)]
UtcTimestamp = Annotated[datetime, AfterValidator(_utc)]


class Ticker(BaseModel):
    model_config = ConfigDict(frozen=True)

    exchange: str
    symbol: str
    market_type: MarketType
    last_price: Price
    observed_at: UtcTimestamp | None
    fetched_at: UtcTimestamp
    source: str


class Candle(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: UtcTimestamp
    open: Price
    high: Price
    low: Price
    close: Price
    volume: Price
    is_closed: StrictBool

    @model_validator(mode="after")
    def valid_ohlc(self) -> Self:
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("Impossible OHLC relationship")
        return self
