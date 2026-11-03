"""Pure explicit product identity and Bybit string mapping.

The symbol envelope is a local technical bound, not a venue maximum or a
listing, quote-currency, market-type, or execution eligibility check.
"""

import re
from dataclasses import dataclass
from typing import Literal

TradingProduct = Literal["SPOT", "LINEAR"]
_SYMBOL = re.compile(r"[A-Z0-9]{2,48}")


def _validate_key(key: "InstrumentKey") -> None:
    try:
        venue, product, symbol = key.venue, key.product, key.symbol
    except AttributeError:
        raise ValueError("Complete InstrumentKey required") from None
    if type(venue) is not str or venue != "BYBIT":
        raise ValueError("Exact BYBIT venue required")
    bybit_category(product)
    if type(symbol) is not str or _SYMBOL.fullmatch(symbol) is None:
        raise ValueError("Symbol outside local technical envelope")


@dataclass(frozen=True)
class InstrumentKey:
    venue: Literal["BYBIT"]
    product: TradingProduct
    symbol: str

    def __post_init__(self) -> None:
        _validate_key(self)


def bybit_category(product: TradingProduct) -> Literal["spot", "linear"]:
    if type(product) is not str or product not in ("SPOT", "LINEAR"):
        raise ValueError("Exact SPOT/LINEAR product required")
    return "spot" if product == "SPOT" else "linear"


def bybit_native_instrument_id(key: InstrumentKey) -> str:
    if type(key) is not InstrumentKey:
        raise ValueError("Exact InstrumentKey required")
    _validate_key(key)
    return f"{key.symbol}-{key.product}.BYBIT"
