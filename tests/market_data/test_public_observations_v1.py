"""Adversarial public observation bindings; all source times and data are synthetic."""
import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from multi_exchange_trader.market_data.adapters.base import MarketDataUnavailable
from multi_exchange_trader.market_data.adapters.bybit import BybitAdapter
from multi_exchange_trader.market_data.bybit_public_contracts import TICKER_URL
from multi_exchange_trader.market_data.instrument_catalog import (
    InstrumentCatalog,
    build_instrument_catalog,
)
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
    require_bybit_public_read_receipt_v1,
)
from multi_exchange_trader.market_data.service import MarketDataService
from multi_exchange_trader.trading_product import InstrumentKey

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


def public_quote_read(*, received: datetime = NOW, server: datetime = NOW,
                      bid: Decimal = Decimal(100), ask: Decimal = Decimal(101)) -> BybitTickerQuoteReadV1:
    query = (("category", "linear"), ("symbol", "BTCUSDT"))
    source = make_source_ref_v1(source_generation="quote-fixture-v1", endpoint=TICKER_URL,
        query=query, request_started_at=received - timedelta(milliseconds=1), received_at=received,
        server_observed_at=server, raw_body_sha256="a" * 64)
    return BybitTickerQuoteReadV1("BTCUSDT", bid, Decimal(2), ask, Decimal(3), source)


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


def test_direct_quote_dto_cannot_bind_even_with_a_plain_catalog() -> None:
    catalog = plain_linear_catalog()
    read = public_quote_read()
    with pytest.raises(PublicObservationRejected, match="public_read_receipt_required"):
        bind_bybit_quote_snapshot_v1(read, catalog, object(), catalog.revision,
            adapter=object(), now=NOW, max_age=timedelta(seconds=5))


async def test_direct_dto_and_fake_mapping_adapter_cannot_claim_bybit_source() -> None:
    read = public_quote_read()
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: pytest.fail("no synthetic route should be used"))) as async_client:
        adapter = BybitAdapter(async_client, clock=lambda: NOW)
        with pytest.raises(PublicObservationRejected, match="public_read_receipt_required"):
            require_bybit_public_read_receipt_v1(adapter, read, kind="quote")

        class MappingAdapter:
            async def get_quote_read_v1(self, _symbol: str) -> BybitTickerQuoteReadV1:
                return read

        service = MarketDataService({"bybit": MappingAdapter()})
        with pytest.raises(MarketDataUnavailable, match="pinned Bybit public adapter"):
            service._require_bybit_public_owner_v1()
        with pytest.raises(TypeError):
            service._adapters["bybit"] = adapter
        with pytest.raises(AttributeError):
            service._bybit_public_owner_v1 = adapter


async def test_exact_bybit_public_route_issues_receipt_for_raw_response() -> None:
    calls = []
    raw_hashes = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/v5/market/tickers"
        assert dict(request.url.params) == {"symbol": "BTCUSDT", "category": "linear"}
        raw = {"retCode": 0, "time": int(NOW.timestamp() * 1000), "result": {
            "category": "linear", "list": [{"symbol": "BTCUSDT", "bid1Price": "100",
                "bid1Size": "2", "ask1Price": "101", "ask1Size": "3"}]}}
        response = httpx.Response(200, json=raw, request=request)
        raw_hashes.append(hashlib.sha256(response.content).hexdigest())
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = BybitAdapter(client, clock=lambda: NOW)
        read = await adapter.get_quote_read_v1("BTCUSDT")
        require_bybit_public_read_receipt_v1(adapter, read, kind="quote")
        assert read.source.endpoint == TICKER_URL
        assert read.source.raw_body_sha256 == raw_hashes[0]
    assert len(calls) == len(raw_hashes) == 1


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
        public_quote_read(bid=Decimal(102), ask=Decimal(101))

    source = make_source_ref_v1(source_generation="quote-fixture-v1", endpoint=TICKER_URL,
        query=(("category", "spot"), ("symbol", "BTCUSDT")),
        request_started_at=NOW - timedelta(milliseconds=1), received_at=NOW, server_observed_at=NOW,
        raw_body_sha256="b" * 64)
    with pytest.raises(PublicObservationRejected, match="public_quote_source_mismatch"):
        BybitTickerQuoteReadV1("BTCUSDT", Decimal(100), Decimal(1), Decimal(101), Decimal(1), source)
