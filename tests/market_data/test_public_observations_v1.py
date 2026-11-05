"""Adversarial public observation bindings; all source times and data are synthetic."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from multi_exchange_trader.market_data.bybit_public_contracts import TICKER_URL
from multi_exchange_trader.market_data.instrument_catalog import InstrumentCatalog, build_instrument_catalog
from multi_exchange_trader.market_data.instrument_definition import (
    INSTRUMENT_ENDPOINT,
    CatalogSource,
    parse_bybit_instrument_response,
)
from multi_exchange_trader.market_data.public_observations_v1 import (
    BybitTickerQuoteReadV1,
    PublicObservationRejected,
    _source_freshness_v1,
    bind_bybit_quote_snapshot_v1,
    make_source_ref_v1,
)
from multi_exchange_trader.trading_product import InstrumentKey


NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


def public_quote_read(*, received: datetime = NOW, server: datetime = NOW,
                      bid: Decimal = Decimal("100"), ask: Decimal = Decimal("101")) -> BybitTickerQuoteReadV1:
    query = (("category", "linear"), ("symbol", "BTCUSDT"))
    source = make_source_ref_v1(source_generation="quote-fixture-v1", endpoint=TICKER_URL,
        query=query, request_started_at=received - timedelta(milliseconds=1), received_at=received,
        server_observed_at=server, raw_body_sha256="a" * 64)
    return BybitTickerQuoteReadV1("BTCUSDT", bid, Decimal("2"), ask, Decimal("3"), source)


def plain_linear_catalog() -> InstrumentCatalog:
    key = InstrumentKey("BYBIT", "LINEAR", "BTCUSDT")
    stamp = NOW
    observation = parse_bybit_instrument_response(
        {"retCode": 0, "time": int(NOW.timestamp() * 1000), "result": {"category": "linear", "list": [{
            "symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT", "settleCoin": "USDT",
            "contractType": "LinearPerpetual", "status": "Trading", "priceScale": "2",
            "isPreListing": False, "priceFilter": {"tickSize": "0.1", "minPrice": "0.1", "maxPrice": "1000000"},
            "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "100",
                "maxMktOrderQty": "50", "minNotionalValue": "5"},
            "leverageFilter": {"minLeverage": "1", "maxLeverage": "100", "leverageStep": "0.01"},
        }]}}, expected=key, source=CatalogSource("plain-catalog-fixture", "PUBLIC_MAINNET",
            INSTRUMENT_ENDPOINT, stamp))
    return build_instrument_catalog((observation,), declared_universe=(key,), now=NOW,
        max_age=timedelta(seconds=10))


def test_plain_catalog_and_forged_receipt_cannot_bind_public_quote() -> None:
    catalog = plain_linear_catalog()
    read = public_quote_read()
    with pytest.raises(PublicObservationRejected, match="public_catalog_receipt_required"):
        bind_bybit_quote_snapshot_v1(read, catalog, object(), catalog.revision,
            now=NOW, max_age=timedelta(seconds=5))


@pytest.mark.parametrize("received,server,now,reason", [
    (NOW - timedelta(seconds=20), NOW - timedelta(seconds=20), NOW, "public_source_stale"),
    (NOW + timedelta(seconds=1), NOW + timedelta(seconds=1), NOW, "public_source_future"),
])
def test_quote_evidence_fails_closed_when_stale_or_future(received: datetime, server: datetime,
                                                          now: datetime, reason: str) -> None:
    read = public_quote_read(received=received, server=server)
    with pytest.raises(PublicObservationRejected) as caught:
        _source_freshness_v1(read.source, now=now, max_age=timedelta(seconds=5))
    assert caught.value.reason == reason


def test_quote_crossing_and_wrong_product_route_are_rejected() -> None:
    with pytest.raises(PublicObservationRejected, match="public_quote_crossed"):
        public_quote_read(bid=Decimal("102"), ask=Decimal("101"))

    source = make_source_ref_v1(source_generation="quote-fixture-v1", endpoint=TICKER_URL,
        query=(("category", "spot"), ("symbol", "BTCUSDT")),
        request_started_at=NOW - timedelta(milliseconds=1), received_at=NOW, server_observed_at=NOW,
        raw_body_sha256="b" * 64)
    with pytest.raises(PublicObservationRejected, match="public_quote_source_mismatch"):
        BybitTickerQuoteReadV1("BTCUSDT", Decimal("100"), Decimal("1"), Decimal("101"), Decimal("1"), source)
