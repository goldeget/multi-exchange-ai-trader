"""Strict product values must never infer a trading product or load private code."""

from dataclasses import FrozenInstanceError
from enum import StrEnum

import pytest

from multi_exchange_trader.trading_product import (
    InstrumentKey,
    bybit_category,
    bybit_native_instrument_id,
)


class Text(str):
    pass


class ProductEnum(StrEnum):
    SPOT = "SPOT"


@pytest.mark.parametrize(
    ("product", "category", "native_id"),
    [("SPOT", "spot", "BTCUSDT-SPOT.BYBIT"), ("LINEAR", "linear", "BTCUSDT-LINEAR.BYBIT")],
)
def test_same_symbol_has_explicit_distinct_category_and_native_id(product, category, native_id):
    key = InstrumentKey("BYBIT", product, "BTCUSDT")
    assert bybit_category(product) == category
    assert bybit_native_instrument_id(key) == native_id


@pytest.mark.parametrize("symbol", ["AB", "A" * 48, "BTCUSDC", "BTCUSD", "10"])
def test_local_symbol_envelope_accepts_without_quote_or_listing_inference(symbol):
    assert InstrumentKey("BYBIT", "LINEAR", symbol).symbol == symbol


@pytest.mark.parametrize(
    "symbol",
    ["", "A", "A" * 49, " BTCUSDT", "BTCUSDT ", "BTCUSDT\n", "BTC-USDT",
     "BTC_USDT", "BTCUSDT-LINEAR.BYBIT", "ＢTCUSDT", "btcusdt", "BTC/USDT",
     None, True, 123, Text("BTCUSDT"), ProductEnum.SPOT],
)
def test_symbol_rejects_non_exact_or_outside_local_envelope(symbol):
    with pytest.raises(ValueError):
        InstrumentKey("BYBIT", "SPOT", symbol)


@pytest.mark.parametrize(
    "product", [None, True, 1, "spot", "linear", " SPOT", "SPOT ", "INVERSE",
                "SPOT\n", "ＳPOT", Text("SPOT"), ProductEnum.SPOT],
)
def test_product_is_exact_and_never_defaulted_or_normalized(product):
    with pytest.raises(ValueError):
        InstrumentKey("BYBIT", product, "BTCUSDT")
    with pytest.raises(ValueError):
        bybit_category(product)


@pytest.mark.parametrize("venue", [None, True, "bybit", "BYBIT ", "OTHER", Text("BYBIT")])
def test_venue_is_exact_bybit(venue):
    with pytest.raises(ValueError):
        InstrumentKey(venue, "SPOT", "BTCUSDT")


def test_key_fields_are_mandatory_frozen_hashable_and_product_distinct():
    with pytest.raises(TypeError):
        InstrumentKey("BYBIT", "SPOT")
    spot = InstrumentKey("BYBIT", "SPOT", "BTCUSDT")
    linear = InstrumentKey("BYBIT", "LINEAR", "BTCUSDT")
    assert len({spot, linear, InstrumentKey("BYBIT", "SPOT", "BTCUSDT")}) == 2
    with pytest.raises(FrozenInstanceError):
        spot.product = "LINEAR"


@pytest.mark.parametrize(
    ("field", "bad"), [("venue", "OTHER"), ("product", "spot"), ("symbol", "BTCUSDT\n")],
)
def test_native_mapping_revalidates_forged_frozen_fields(field, bad):
    key = InstrumentKey("BYBIT", "SPOT", "BTCUSDT")
    object.__setattr__(key, field, bad)
    with pytest.raises(ValueError):
        bybit_native_instrument_id(key)


def test_native_mapping_requires_exact_key_without_coercion():
    class ChildKey(InstrumentKey):
        pass

    for key in (None, "BTCUSDT", True, ChildKey("BYBIT", "SPOT", "BTCUSDT")):
        with pytest.raises(ValueError):
            bybit_native_instrument_id(key)


def test_native_mapping_rejects_missing_forged_fields_with_controlled_error():
    for missing in ("venue", "product", "symbol"):
        key = InstrumentKey("BYBIT", "SPOT", "BTCUSDT")
        object.__delattr__(key, missing)
        with pytest.raises(ValueError):
            bybit_native_instrument_id(key)
    with pytest.raises(ValueError):
        bybit_native_instrument_id(object.__new__(InstrumentKey))


def test_native_mapping_checks_type_before_accessing_untrusted_attributes():
    class Hostile:
        def __getattribute__(self, name):
            raise AssertionError("untrusted attribute hook must not run")

    with pytest.raises(ValueError):
        bybit_native_instrument_id(Hostile())
