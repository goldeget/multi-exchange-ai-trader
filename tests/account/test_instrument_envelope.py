"""Strict source values are data, never issuer authority."""

import hashlib
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from multi_exchange_trader.account import instrument_envelope as module
from multi_exchange_trader.trading_product import InstrumentKey


def test_datetime_offset_overflow_has_fixed_clock_failure() -> None:
    with pytest.raises(module.InstrumentSourceError, match="CLOCK_INVALID"):
        replace(valid_envelope(), request_started_at=datetime.min.replace(
            tzinfo=timezone(timedelta(hours=1))))


def test_source_error_whitelist_is_fixed_and_exact() -> None:
    error = module.InstrumentSourceError("INSTRUMENT_SOURCE_JSON_INVALID")
    assert error.args == (error.code,)
    assert str(error) == error.code
    for value in ("raw secret", 1, None):
        with pytest.raises(ValueError, match="^INVALID_INSTRUMENT_SOURCE_ERROR_CODE$"):
            module.InstrumentSourceError(value)  # type: ignore[arg-type]


def test_strict_decoder_remains_independent_of_http_client_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = valid_envelope().decoded_body_bytes
    monkeypatch.setitem(sys.modules, "multi_exchange_trader.account.bybit_readonly", None)
    body, _, _ = module.decode_instrument_envelope(raw)
    assert body["retCode"] == 0


def valid_envelope() -> module.InstrumentEnvelopeRead:
    raw = b'{"retCode":0,"result":{"category":"linear","list":[]},"time":1700000000000}'
    _, canonical, stamp = module.decode_instrument_envelope(raw)
    origin = module.HttpsOrigin("https", "api.bybit.com", 443)
    return module.InstrumentEnvelopeRead(
        InstrumentKey("BYBIT", "LINEAR", "BTCUSDT"), "GET",
        "/v5/market/instruments-info", (("category", "linear"), ("symbol", "BTCUSDT")),
        origin, origin, 200, datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC),
        datetime(2023, 11, 14, 22, 13, 21, tzinfo=UTC), 1.0, 2.0, stamp, raw,
        hashlib.sha256(raw).hexdigest(), canonical, 0, 0, 1, 1,
    )


def test_envelope_revalidates_body_hash_times_origin_and_exact_fields() -> None:
    envelope = valid_envelope()
    module.validate_instrument_envelope_read(envelope)
    assert "retCode" not in repr(envelope)
    for changes in (
        {"decoded_body_sha256": "0" * 64}, {"physical_end": 4},
        {"response_status": True}, {"received_mono": float("nan")},
        {"canonical_envelope_bytes": b"{}"},
        {"response_origin": module.HttpsOrigin("https", "api-demo.bybit.com", 443)},
    ):
        with pytest.raises(module.InstrumentSourceError):
            replace(envelope, **changes)
    forged = object.__new__(module.InstrumentEnvelopeRead)
    object.__setattr__(forged, "__dataclass_fields__", {})
    with pytest.raises(module.InstrumentSourceError):
        module.validate_instrument_envelope_read(forged)
    with pytest.raises(module.InstrumentSourceError, match="RECEIPT_REQUIRED"):
        module.CurrentCatalogReceipt()


@pytest.mark.parametrize("raw", [
    b'{"retCode":0,"retCode":0}', b'{"result":{"x":1,"x":2}}',
    b'{"x":1.0}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":9223372036854775808}',
    b'{"x":123456789012345678901}', b'{"x":"\\ud800"}', b'"\xff"',
])
def test_strict_decoder_rejects_wire_values(raw: bytes) -> None:
    with pytest.raises(module.InstrumentSourceError):
        module.decode_instrument_envelope(raw)


def test_strict_decoder_applies_frozen_tree_resource_bounds() -> None:
    for raw in (b" " * 65537, b'{"x":"' + b"x" * 129 + b'"}',
                b'{"x":' + b"[" * 9 + b"0" + b"]" * 9 + b"}"):
        with pytest.raises(module.InstrumentSourceError, match="RESOURCE_LIMIT"):
            module.decode_instrument_envelope(raw)


def test_dto_canonical_bytes_limit_is_a_resource_rejection() -> None:
    with pytest.raises(module.InstrumentSourceError, match="RESOURCE_LIMIT"):
        replace(valid_envelope(), canonical_envelope_bytes=b"x" * 65537)


def test_catalog_view_rederives_exact_observation_and_has_no_current_authority() -> None:
    from multi_exchange_trader.market_data.instrument_catalog import build_instrument_catalog
    from multi_exchange_trader.market_data.instrument_definition import (
        CatalogSource,
        parse_bybit_instrument_response,
    )

    row = {
        "symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT", "status": "Trading",
        "priceFilter": {"tickSize": "0.10"},
        "lotSizeFilter": {"basePrecision": "0.001", "quotePrecision": "0.01",
                          "minOrderAmt": "5", "maxLimitOrderQty": "100",
                          "maxMarketOrderQty": "50"},
    }
    body = {"retCode": 0, "result": {"category": "spot", "list": [row]}, "time": 1700000000000}
    raw = json.dumps(body).encode()
    _, canonical, _ = module.decode_instrument_envelope(raw)
    envelope = replace(valid_envelope(), key=InstrumentKey("BYBIT", "SPOT", "BTCUSDT"),
                       params=(("category", "spot"), ("symbol", "BTCUSDT")),
                       decoded_body_bytes=raw, decoded_body_sha256=hashlib.sha256(raw).hexdigest(),
                       canonical_envelope_bytes=canonical)
    source_id = "1" * 32
    source = CatalogSource(source_id, "PUBLIC_MAINNET", envelope.endpoint, envelope.received_at)
    observation = parse_bybit_instrument_response(body, expected=envelope.key, source=source)
    catalog = build_instrument_catalog((observation,), declared_universe=(envelope.key,),
                                       now=envelope.received_at, max_age=timedelta(seconds=10))
    view = module.CatalogReceiptView(catalog, "PUBLIC_MAINNET", source_id, (envelope,),
                                     envelope.received_at, envelope.received_mono)
    module.validate_catalog_receipt_view(view)
    for changes in ({"source_id": "2" * 32}, {"source_environment": "FIXTURE"},
                    {"envelopes": ()}, {"issued_mono": 0.0}):
        with pytest.raises(module.InstrumentSourceError):
            replace(view, **changes)
    handle = module._new_current_catalog_receipt()
    assert type(handle) is module.CurrentCatalogReceipt
    assert repr(handle) == "CurrentCatalogReceipt()"
    assert handle is not module._new_current_catalog_receipt()
