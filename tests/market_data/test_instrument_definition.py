"""Synthetic, offline instrument-definition contract tests."""

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

import pytest

from multi_exchange_trader.market_data.instrument_definition import (
    INSTRUMENT_ENDPOINT,
    LINEAR_SOURCE_RULE_REVISION,
    PARSER_REVISION,
    SPOT_SOURCE_RULE_REVISION,
    CatalogRejected,
    CatalogSource,
    DecimalField,
    InstrumentObservation,
    KnownNotPublished,
    LinearInstrumentSpec,
    SpotInstrumentSpec,
    _canonical_json_bytes,
    _key_payload,
    _utc_datetime,
    parse_bybit_instrument_response,
    validate_instrument_observation,
)
from multi_exchange_trader.trading_product import InstrumentKey

NOW = datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC)


def source(**changes):
    values = {
        "source_id": "synthetic:one",
        "source_environment": "FIXTURE",
        "endpoint": INSTRUMENT_ENDPOINT,
        "received_at": NOW,
    }
    return CatalogSource(**(values | changes))


def row(product="LINEAR"):
    common = {"symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT", "status": "Trading"}
    if product == "SPOT":
        return common | {
            "priceFilter": {"tickSize": "0.10"},
            "lotSizeFilter": {
                "basePrecision": "0.001",
                "quotePrecision": "0.01",
                "minOrderAmt": "5",
                "maxLimitOrderQty": "100",
                "maxMarketOrderQty": "50",
                "minOrderQty": "999999",
                "maxOrderQty": "999999",
                "maxOrderAmt": "999999",
            },
        }
    return common | {
        "settleCoin": "USDT",
        "contractType": "LinearPerpetual",
        "priceScale": "2",
        "isPreListing": False,
        "preListingInfo": None,
        "priceFilter": {"tickSize": "0.10", "minPrice": "0.1", "maxPrice": "1000000"},
        "lotSizeFilter": {
            "qtyStep": "0.001",
            "minOrderQty": "0.001",
            "maxOrderQty": "100",
            "maxMktOrderQty": "50",
            "minNotionalValue": "5",
        },
        "leverageFilter": {"minLeverage": "1", "maxLeverage": "100", "leverageStep": "0.01"},
    }


def body(product="LINEAR"):
    return {
        "retCode": 0,
        "retMsg": "OK",
        "time": 1704067200000,
        "result": {"category": product.lower(), "list": [row(product)], "nextPageCursor": ""},
    }


def parse(product="LINEAR", value=None, **kwargs):
    return parse_bybit_instrument_response(
        body(product) if value is None else value,
        expected=InstrumentKey("BYBIT", product, "BTCUSDT"),
        source=source(),
        **kwargs,
    )


def rejected(code, function, *args, **kwargs):
    with pytest.raises(CatalogRejected) as caught:
        function(*args, **kwargs)
    assert caught.value.code == code
    assert str(caught.value) == code
    assert caught.value.args == (code,)
    assert "SECRET_MARKER" not in repr(caught.value)


def forge(original, **changes):
    forged = object.__new__(type(original))
    for item in fields(original):
        object.__setattr__(forged, item.name, changes.get(item.name, getattr(original, item.name)))
    return forged


@pytest.mark.parametrize(
    "product,spec_type,rule,native",
    [
        ("LINEAR", LinearInstrumentSpec, LINEAR_SOURCE_RULE_REVISION, "BTCUSDT-LINEAR.BYBIT"),
        ("SPOT", SpotInstrumentSpec, SPOT_SOURCE_RULE_REVISION, "BTCUSDT-SPOT.BYBIT"),
    ],
)
def test_regular_single_symbol_has_product_specific_identity_and_modern_caps(
    product, spec_type, rule, native
):
    value = body(product)
    observation = parse(product, value)
    assert type(observation) is InstrumentObservation
    assert type(observation.spec) is spec_type
    assert observation.native_instrument_id == native
    assert observation.parser_revision == "BYBIT_INSTRUMENT_PARSER_V1"
    assert observation.source_rule_revision == rule
    assert observation.response_time == datetime(2024, 1, 1, tzinfo=UTC)
    assert observation.spec.max_limit_quantity == DecimalField("100", Decimal(100), 0)
    assert observation.spec.max_market_quantity == DecimalField("50", Decimal(50), 0)
    assert observation.spec.max_quote_amount == KnownNotPublished("max_quote_amount", rule)
    assert "raw_row_canonical=" not in repr(observation)
    with pytest.raises(FrozenInstanceError):
        observation.parser_revision = "changed"
    value["result"]["list"][0]["symbol"] = "ETHUSDT"
    validate_instrument_observation(observation)


@pytest.mark.parametrize(
    "raw",
    [
        "0",
        "0.000",
        "-1",
        "+1",
        "01",
        ".1",
        "1.",
        "1e1",
        " 1",
        "NaN",
        "Infinity",
        "١",
        "1" * 65,
        "0." + "1" * 33,
        1,
        True,
        1.0,
        Decimal(1),
    ],
)
def test_decimal_source_spelling_rejects_aliases_nonpositive_and_bounds(raw):
    rejected("CATALOG_FILTER_INVALID", DecimalField, raw, Decimal(1), 0)


@pytest.mark.parametrize(
    "value,scale",
    [(Decimal("1.0"), 0), (Decimal("NaN"), 0), (Decimal(1), True), (1, 0), (Decimal(1), 1)],
)
def test_decimal_raw_value_tuple_and_scale_cannot_disagree(value, scale):
    rejected("CATALOG_FILTER_INVALID", DecimalField, "1", value, scale)


def test_decimal_precision_is_independent_of_context_and_accepts_exact_boundaries():
    raw = "9" * 32 + "." + "1" * 32
    with localcontext() as context:
        context.prec = 2
        decimal = DecimalField(raw, Decimal(raw), 32)
        observation = parse()
    assert decimal.value.as_tuple() == Decimal(raw).as_tuple()
    assert observation.spec.price_tick.raw == "0.10"
    assert observation.spec.price_tick.scale == 2
    assert DecimalField("1" * 64, Decimal("1" * 64), 0).scale == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"source_id": ""},
        {"source_id": "x" * 97},
        {"source_id": "https://SECRET_MARKER"},
        {"source_id": "é"},
        {"source_environment": "DEMO"},
        {"endpoint": "https://SECRET_MARKER"},
        {"received_at": NOW.replace(tzinfo=None)},
    ],
)
def test_source_has_explicit_bounded_local_domain(changes):
    rejected("CATALOG_SOURCE_MISMATCH", source, **changes)


def test_fixed_offset_times_normalize_without_accepting_arbitrary_timezone_hooks():
    shifted = datetime(2024, 1, 1, 9, 0, 1, tzinfo=timezone(timedelta(hours=9)))
    assert source(received_at=shifted).received_at == NOW
    assert source(received_at=shifted).received_at.tzinfo is UTC

    class HostileTimezone(tzinfo):
        def utcoffset(self, dt):
            raise AssertionError("timezone hook called")

    class DateSubclass(datetime):
        pass

    for value in [
        NOW.replace(tzinfo=HostileTimezone()),
        NOW.replace(tzinfo=ZoneInfo("UTC")),
        NOW.replace(tzinfo=None),
        DateSubclass(2024, 1, 1, tzinfo=UTC),
    ]:
        rejected("CATALOG_TIME_INVALID", _utc_datetime, value)


@pytest.mark.parametrize(
    "product,path,value,code",
    [
        ("LINEAR", ("baseCoin",), "", "CATALOG_FILTER_INVALID"),
        ("LINEAR", ("quoteCoin",), "USDC", "CATALOG_INSTRUMENT_UNSUPPORTED"),
        ("LINEAR", ("settleCoin",), "USDC", "CATALOG_INSTRUMENT_UNSUPPORTED"),
        ("LINEAR", ("contractType",), "LinearFutures", "CATALOG_INSTRUMENT_UNSUPPORTED"),
        ("LINEAR", ("status",), "PreLaunch", "CATALOG_INSTRUMENT_UNSUPPORTED"),
        ("LINEAR", ("isPreListing",), True, "CATALOG_INSTRUMENT_UNSUPPORTED"),
        ("LINEAR", ("isPreListing",), 0, "CATALOG_FILTER_INVALID"),
        ("LINEAR", ("preListingInfo",), {}, "CATALOG_INSTRUMENT_UNSUPPORTED"),
        ("LINEAR", ("priceScale",), 2, "CATALOG_FILTER_INVALID"),
        ("LINEAR", ("priceScale",), True, "CATALOG_FILTER_INVALID"),
        ("LINEAR", ("priceScale",), "02", "CATALOG_FILTER_INVALID"),
        ("LINEAR", ("priceScale",), "33", "CATALOG_FILTER_INVALID"),
        ("LINEAR", ("priceFilter", "minPrice"), "1000001", "CATALOG_FILTER_INVALID"),
        ("LINEAR", ("lotSizeFilter", "minOrderQty"), "101", "CATALOG_FILTER_INVALID"),
        ("LINEAR", ("lotSizeFilter", "qtyStep"), "51", "CATALOG_FILTER_INVALID"),
        ("LINEAR", ("leverageFilter", "minLeverage"), "101", "CATALOG_FILTER_INVALID"),
        ("LINEAR", ("leverageFilter", "leverageStep"), "101", "CATALOG_FILTER_INVALID"),
        ("SPOT", ("status",), "Closed", "CATALOG_INSTRUMENT_UNSUPPORTED"),
        ("SPOT", ("lotSizeFilter", "basePrecision"), "51", "CATALOG_FILTER_INVALID"),
    ],
)
def test_known_rule_fields_and_same_unit_ranges_are_validated(product, path, value, code):
    envelope = body(product)
    target = envelope["result"]["list"][0]
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value
    rejected(code, parse, product, envelope)


@pytest.mark.parametrize(
    "product,cap",
    [
        ("LINEAR", "maxOrderQty"),
        ("LINEAR", "maxMktOrderQty"),
        ("SPOT", "maxLimitOrderQty"),
        ("SPOT", "maxMarketOrderQty"),
    ],
)
@pytest.mark.parametrize("bad", [None, "0", "MISSING"])
def test_both_modern_caps_are_mandatory_and_deprecated_spot_values_never_fallback(
    product, cap, bad
):
    envelope = body(product)
    lot = envelope["result"]["list"][0]["lotSizeFilter"]
    if bad == "MISSING":
        del lot[cap]
    else:
        lot[cap] = bad
    rejected("CATALOG_FILTER_INVALID", parse, product, envelope)


def test_no_invented_grid_or_cross_unit_or_order_kind_ordering():
    envelope = body()
    target = envelope["result"]["list"][0]
    target["priceScale"] = "0"
    target["lotSizeFilter"]["maxMktOrderQty"] = "200"
    target["lotSizeFilter"]["minNotionalValue"] = "9999999999999999"
    target["priceFilter"]["tickSize"] = "0.03"
    del target["preListingInfo"]
    assert parse(value=envelope).spec.source_price_scale == 0


@pytest.mark.parametrize(
    "path,value,code",
    [
        (("retCode",), True, "CATALOG_RESPONSE_INVALID"),
        (("retCode",), 1, "CATALOG_PROVIDER_REJECTED"),
        (("time",), 0, "CATALOG_RESPONSE_INVALID"),
        (("time",), True, "CATALOG_RESPONSE_INVALID"),
        (("time",), 253402300800000, "CATALOG_RESPONSE_INVALID"),
        (("result", "category"), "spot", "CATALOG_SCOPE_MISMATCH"),
        (("result", "nextPageCursor"), "SECRET_MARKER", "CATALOG_RESPONSE_INVALID"),
        (("result", "list"), [], "CATALOG_RESPONSE_INVALID"),
        (("result", "list"), [row(), row()], "CATALOG_RESPONSE_INVALID"),
    ],
)
def test_envelope_is_exact_single_symbol_and_not_paginated(path, value, code):
    envelope = body()
    target = envelope
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    rejected(code, parse, "LINEAR", envelope)


def test_symbol_mismatch_missing_identity_and_cursor_absence():
    envelope = body()
    envelope["result"]["list"][0]["symbol"] = "ETHUSDT"
    rejected("CATALOG_SCOPE_MISMATCH", parse, "LINEAR", envelope)
    del envelope["result"]["list"][0]["symbol"]
    rejected("CATALOG_RESPONSE_INVALID", parse, "LINEAR", envelope)
    envelope = body()
    del envelope["result"]["nextPageCursor"]
    assert parse(value=envelope).key.symbol == "BTCUSDT"


@pytest.mark.parametrize("value", [1.0, float("nan"), object(), {1: "x"}, "\ud800"])
def test_canonical_json_rejects_non_json_and_surrogates(value):
    rejected("CATALOG_INPUT_INVALID", _canonical_json_bytes, value)


def test_json_rejects_subclasses_before_any_untrusted_hooks():
    class HostileStr(str):
        def __str__(self):
            raise AssertionError("str hook")

        def __repr__(self):
            raise AssertionError("repr hook")

    class HostileDict(dict):
        def items(self):
            raise AssertionError("dict hook")

    class HostileInt(int):
        def __abs__(self):
            raise AssertionError("int hook")

    for value in [
        HostileStr("SECRET_MARKER"),
        HostileDict(),
        HostileInt(2),
        {HostileStr("SECRET_MARKER"): 1},
    ]:
        rejected("CATALOG_INPUT_INVALID", _canonical_json_bytes, value)


def test_json_bounds_depth_nodes_container_scalar_integer_and_cycle():
    assert _canonical_json_bytes({"z": "é", "a": True}) == b'{"a":true,"z":"\\u00e9"}'
    assert _canonical_json_bytes("x" * 128) == b'"' + b"x" * 128 + b'"'
    assert _canonical_json_bytes(2**63 - 1) == b"9223372036854775807"
    value = 0
    for _ in range(7):
        value = [value]
    assert _canonical_json_bytes(value)
    rejected("CATALOG_RESOURCE_LIMIT", _canonical_json_bytes, [value])
    for bad in ["x" * 129, [0] * 129, {str(i): 0 for i in range(129)}, 2**63, -(2**63)]:
        rejected("CATALOG_RESOURCE_LIMIT", _canonical_json_bytes, bad)
    cycle = []
    cycle.append(cycle)
    rejected("CATALOG_INPUT_INVALID", _canonical_json_bytes, cycle)
    shared = [1]
    assert _canonical_json_bytes([shared, shared]) == b"[[1],[1]]"
    # Root +31*(list +128 leaves)+95 leaves =4095; one extra leaf =4096.
    nodes = [[0] * 128 for _ in range(31)] + [0] * 96
    assert _canonical_json_bytes(nodes)
    rejected("CATALOG_RESOURCE_LIMIT", _canonical_json_bytes, nodes + [0])


def test_encoded_limit_counts_escapes_punctuation_before_serializing():
    # Every 128-character escaped string costs770 bytes; 85 + punctuation =65536.
    value = ["\u0080" * 128] * 85
    assert len(_canonical_json_bytes(value)) == 65536
    rejected("CATALOG_RESOURCE_LIMIT", _canonical_json_bytes, value + [""])


def test_raw_hash_and_semantic_hash_have_distinct_membership():
    first = parse("SPOT")
    envelope = body("SPOT")
    envelope["result"]["list"][0]["uninterpreted"] = "extra"
    extra = parse("SPOT", envelope)
    assert extra.raw_row_sha256 != first.raw_row_sha256
    assert extra.specification_revision == first.specification_revision
    assert first.raw_row_sha256 == hashlib.sha256(first.raw_row_canonical).hexdigest()
    envelope["result"]["list"][0]["priceFilter"]["tickSize"] = "0.1"
    assert parse("SPOT", envelope).specification_revision != first.specification_revision
    changed_source = replace(
        first, source=source(source_id="another", received_at=NOW + timedelta(1))
    )
    assert changed_source.specification_revision == first.specification_revision
    assert replace(first, response_time=first.response_time + timedelta(milliseconds=1))
    payload = {
        "key": {"venue": "BYBIT", "product": "SPOT", "symbol": "BTCUSDT"},
        "native_instrument_id": "BTCUSDT-SPOT.BYBIT",
        "parser_revision": PARSER_REVISION,
        "source_rule_revision": SPOT_SOURCE_RULE_REVISION,
        "spec": {
            "base_coin": "BTC",
            "quote_coin": "USDT",
            "status": "Trading",
            "price_tick": {"raw": "0.10", "value": "0.10", "scale": 2},
            "base_precision": {"raw": "0.001", "value": "0.001", "scale": 3},
            "quote_precision": {"raw": "0.01", "value": "0.01", "scale": 2},
            "min_quote_amount": {"raw": "5", "value": "5", "scale": 0},
            "max_limit_quantity": {"raw": "100", "value": "100", "scale": 0},
            "max_market_quantity": {"raw": "50", "value": "50", "scale": 0},
            "max_quote_amount": {
                "field": "max_quote_amount",
                "source_rule_revision": SPOT_SOURCE_RULE_REVISION,
            },
        },
    }
    expected = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode()
    assert first.specification_revision == hashlib.sha256(expected).hexdigest()


@pytest.mark.parametrize(
    "changes",
    [
        {"parser_revision": "UNKNOWN"},
        {"source_rule_revision": LINEAR_SOURCE_RULE_REVISION},
        {"source_rule_revision": "UNKNOWN"},
        {"specification_revision": "0" * 64},
        {"raw_row_sha256": "0" * 64},
        {"native_instrument_id": "BTCUSDT-LINEAR.BYBIT"},
        {"key": InstrumentKey("BYBIT", "LINEAR", "BTCUSDT")},
        {"raw_row_canonical": b"{}"},
    ],
)
def test_forged_observation_revalidates_every_derived_value(changes):
    rejected(
        "CATALOG_OBSERVATION_INVALID",
        validate_instrument_observation,
        forge(parse("SPOT"), **changes),
    )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":1,"x":2}',
        b' {"x":1}',
        b'{"x":1.0}',
        b'{"x":NaN}',
        b'{"x":' + b"9" * 5000 + b"}",
        b"\xff",
        b"[]",
        b"",
        b"[" * 2000 + b"]" * 2000,
        b" " * 65537,
    ],
)
def test_observation_raw_decode_is_bounded_duplicate_safe_and_canonical(raw):
    rejected(
        "CATALOG_OBSERVATION_INVALID",
        validate_instrument_observation,
        forge(parse(), raw_row_canonical=raw, raw_row_sha256=hashlib.sha256(raw).hexdigest()),
    )


def test_forged_and_missing_nested_objects_cannot_bypass_constructor_validation():
    observation = parse()
    for corrupt in [
        forge(observation.spec.price_tick, value=Decimal(2)),
        object.__new__(DecimalField),
    ]:
        spec = forge(observation.spec, price_tick=corrupt)
        rejected(
            "CATALOG_OBSERVATION_INVALID",
            validate_instrument_observation,
            forge(observation, spec=spec),
        )
    for bad in [
        object.__new__(InstrumentObservation),
        forge(observation, source=object.__new__(CatalogSource)),
        forge(observation, key=object.__new__(InstrumentKey)),
    ]:
        rejected("CATALOG_OBSERVATION_INVALID", validate_instrument_observation, bad)
    rejected(
        "CATALOG_TIME_INVALID",
        replace,
        observation,
        response_time=observation.source.received_at + timedelta(microseconds=1),
    )
    rejected(
        "CATALOG_FILTER_INVALID", KnownNotPublished, "max_quantity", LINEAR_SOURCE_RULE_REVISION
    )
    rejected("CATALOG_FILTER_INVALID", KnownNotPublished, "max_quote_amount", "UNKNOWN")


def test_rejection_constructor_never_echoes_unknown_codes():
    for code in ["SECRET_MARKER", 1, None]:
        with pytest.raises(ValueError, match="^INVALID_CATALOG_REJECTION_CODE$"):
            CatalogRejected(code)


def test_shared_key_helper_revalidates_and_separates_same_symbol_products():
    assert _key_payload(InstrumentKey("BYBIT", "SPOT", "BTCUSDT")) == {
        "venue": "BYBIT",
        "product": "SPOT",
        "symbol": "BTCUSDT",
    }
    rejected("CATALOG_INPUT_INVALID", _key_payload, object.__new__(InstrumentKey))


def test_spec_type_validation_does_not_invoke_unknown_metaclass_equality():
    class HostileMeta(type):
        def __eq__(self, other):
            raise AssertionError("unknown equality hook")

    class Unknown(metaclass=HostileMeta):
        pass

    rejected(
        "CATALOG_OBSERVATION_INVALID",
        validate_instrument_observation,
        forge(parse(), spec=Unknown()),
    )


@pytest.mark.parametrize("product", ["LINEAR", "SPOT"])
def test_every_supported_required_row_field_is_mandatory(product):
    complete = row(product)
    optional = {"preListingInfo", "minOrderQty", "maxOrderQty", "maxOrderAmt"}
    for name, value in complete.items():
        if name == "preListingInfo":
            continue
        envelope = body(product)
        del envelope["result"]["list"][0][name]
        code = "CATALOG_RESPONSE_INVALID" if name == "symbol" else "CATALOG_FILTER_INVALID"
        rejected(code, parse, product, envelope)
        if type(value) is dict:
            for child in value:
                if product == "SPOT" and child in optional:
                    continue
                envelope = body(product)
                del envelope["result"]["list"][0][name][child]
                rejected("CATALOG_FILTER_INVALID", parse, product, envelope)


def test_deprecated_spot_caps_only_change_raw_observation_not_supported_revision():
    first = parse("SPOT")
    envelope = body("SPOT")
    lot = envelope["result"]["list"][0]["lotSizeFilter"]
    for name in ("minOrderQty", "maxOrderQty", "maxOrderAmt"):
        lot[name] = "0"
    second = parse("SPOT", envelope)
    assert second.raw_row_sha256 != first.raw_row_sha256
    assert second.specification_revision == first.specification_revision
    for name in ("minOrderQty", "maxOrderQty", "maxOrderAmt"):
        del lot[name]
    assert parse("SPOT", envelope).specification_revision == first.specification_revision


@pytest.mark.parametrize("scale", ["0", "32"])
def test_wire_price_scale_accepts_both_integer_string_boundaries(scale):
    envelope = body()
    envelope["result"]["list"][0]["priceScale"] = scale
    assert parse(value=envelope).spec.source_price_scale == int(scale)


def test_exact_decimal_string_integer_decimal_and_dto_types_are_required():
    class StringSubclass(str):
        pass

    class IntSubclass(int):
        pass

    class DecimalSubclass(Decimal):
        pass

    rejected("CATALOG_FILTER_INVALID", DecimalField, StringSubclass("1"), Decimal(1), 0)
    rejected("CATALOG_FILTER_INVALID", DecimalField, "1", DecimalSubclass(1), 0)
    rejected("CATALOG_FILTER_INVALID", DecimalField, "1", Decimal(1), IntSubclass(0))
    rejected("CATALOG_SOURCE_MISMATCH", source, source_id=StringSubclass("synthetic"))
    rejected("CATALOG_SOURCE_MISMATCH", source, source_environment=StringSubclass("FIXTURE"))
    rejected("CATALOG_SOURCE_MISMATCH", source, endpoint=StringSubclass(INSTRUMENT_ENDPOINT))

    class KeySubclass(InstrumentKey):
        pass

    rejected("CATALOG_INPUT_INVALID", _key_payload, KeySubclass("BYBIT", "SPOT", "BTCUSDT"))

    class SpecSubclass(SpotInstrumentSpec):
        pass

    original = parse("SPOT")
    values = {item.name: getattr(original.spec, item.name) for item in fields(original.spec)}
    rejected("CATALOG_FILTER_INVALID", SpecSubclass, **values)

    class ObservationSubclass(InstrumentObservation):
        pass

    rejected(
        "CATALOG_OBSERVATION_INVALID",
        validate_instrument_observation,
        object.__new__(ObservationSubclass),
    )


@pytest.mark.parametrize(
    "product,name,value",
    [
        ("LINEAR", "source_price_scale", True),
        ("LINEAR", "source_price_scale", "2"),
        ("LINEAR", "source_price_scale", -1),
        ("LINEAR", "source_price_scale", 33),
        ("LINEAR", "is_pre_listing", 0),
        ("LINEAR", "price_tick", Decimal("0.1")),
        ("SPOT", "base_coin", "btc"),
        ("SPOT", "base_coin", "B" * 33),
        ("SPOT", "quote_coin", None),
        ("SPOT", "max_market_quantity", None),
    ],
)
def test_direct_spec_constructors_reject_malformed_required_types(product, name, value):
    rejected("CATALOG_FILTER_INVALID", replace, parse(product).spec, **{name: value})


def test_product_specific_absence_is_not_a_missing_required_numeric_fallback():
    for product, other_rule in [
        ("SPOT", LINEAR_SOURCE_RULE_REVISION),
        ("LINEAR", SPOT_SOURCE_RULE_REVISION),
    ]:
        rejected(
            "CATALOG_FILTER_INVALID",
            replace,
            parse(product).spec,
            max_quote_amount=KnownNotPublished("max_quote_amount", other_rule),
        )
        rejected("CATALOG_FILTER_INVALID", replace, parse(product).spec, max_quote_amount=None)


def test_input_revalidation_precedes_body_resource_walk():
    rejected(
        "CATALOG_INPUT_INVALID",
        parse_bybit_instrument_response,
        object(),
        expected=object.__new__(InstrumentKey),
        source=source(),
    )
    rejected(
        "CATALOG_SOURCE_MISMATCH",
        parse_bybit_instrument_response,
        object(),
        expected=InstrumentKey("BYBIT", "SPOT", "BTCUSDT"),
        source=object.__new__(CatalogSource),
    )


def test_json_container_subclasses_and_non_string_keys_are_never_traversed():
    class ListSubclass(list):
        def __iter__(self):
            raise AssertionError("unknown iteration")

        def __len__(self):
            raise AssertionError("unknown length")

    rejected("CATALOG_INPUT_INVALID", _canonical_json_bytes, ListSubclass())
    rejected("CATALOG_INPUT_INVALID", _canonical_json_bytes, {False: "SECRET_MARKER"})
    rejected("CATALOG_RESOURCE_LIMIT", _canonical_json_bytes, {"x" * 129: 1})
    assert _canonical_json_bytes({"x" * 128: 1})
    assert _canonical_json_bytes([0] * 128)
    assert _canonical_json_bytes({str(i): 0 for i in range(128)})


def test_resource_prechecks_happen_before_aggregate_serialization_hashing_and_decode(monkeypatch):
    import multi_exchange_trader.market_data.instrument_definition as module

    real_dumps = json.dumps

    def guarded_dump(value, **kwargs):
        if type(value) in (dict, list):
            raise AssertionError("aggregate serialization before resource check")
        return real_dumps(value, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(module.json, "dumps", guarded_dump)
        rejected("CATALOG_RESOURCE_LIMIT", _canonical_json_bytes, ["\u0080" * 128] * 86)
    observation = parse()

    def deny(*args, **kwargs):
        raise AssertionError("hash/decode before resource check")

    with monkeypatch.context() as scoped:
        scoped.setattr(module.hashlib, "sha256", deny)
        envelope = body()
        envelope["unknown"] = ["\u0080" * 128] * 86
        rejected("CATALOG_RESOURCE_LIMIT", parse, "LINEAR", envelope)
        scoped.setattr(module.json, "loads", deny)
        rejected(
            "CATALOG_OBSERVATION_INVALID",
            validate_instrument_observation,
            forge(observation, raw_row_canonical=b" " * 65537),
        )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":9223372036854775808}',
        b'{"x":-9223372036854775808}',
        b'{"x":Infinity}',
        b'{"x":-Infinity}',
        b'{"x":"\\ud800"}',
        b'{"x":' + b'"' + b"a" * 129 + b'"}',
        b'{"x":[' + b",".join([b"0"] * 129) + b"]}",
        b'{"x":true,}',
    ],
)
def test_raw_decoder_integer_string_container_and_constant_limits(raw):
    rejected(
        "CATALOG_OBSERVATION_INVALID",
        validate_instrument_observation,
        forge(parse(), raw_row_canonical=raw, raw_row_sha256=hashlib.sha256(raw).hexdigest()),
    )


def test_raw_bytes_subclasses_and_observation_string_subclasses_do_not_get_hooks():
    class HostileBytes(bytes):
        def __len__(self):
            raise AssertionError("bytes length hook")

    class HostileStr(str):
        def __eq__(self, other):
            raise AssertionError("string equality hook")

    observation = parse()
    rejected(
        "CATALOG_OBSERVATION_INVALID",
        validate_instrument_observation,
        forge(observation, raw_row_canonical=HostileBytes(b"{}")),
    )
    for name in (
        "native_instrument_id",
        "parser_revision",
        "source_rule_revision",
        "specification_revision",
        "raw_row_sha256",
    ):
        rejected(
            "CATALOG_OBSERVATION_INVALID",
            validate_instrument_observation,
            forge(observation, **{name: HostileStr("SECRET_MARKER")}),
        )


def test_timestamp_extremes_and_utc_overflow_have_fixed_errors():
    envelope = body("SPOT")
    for timestamp in [1, 253402300799999]:
        envelope["time"] = timestamp
        observation = parse_bybit_instrument_response(
            envelope,
            expected=InstrumentKey("BYBIT", "SPOT", "BTCUSDT"),
            source=source(received_at=datetime.max.replace(tzinfo=UTC)),
        )
        assert observation.response_time == datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            milliseconds=timestamp
        )
    rejected(
        "CATALOG_TIME_INVALID",
        _utc_datetime,
        datetime.max.replace(tzinfo=timezone(timedelta(hours=-1))),
    )


def test_missing_observation_field_is_rejected_independently_for_each_field():
    observation = parse()
    for item in fields(observation):
        corrupt = forge(observation)
        object.__delattr__(corrupt, item.name)
        rejected("CATALOG_OBSERVATION_INVALID", validate_instrument_observation, corrupt)


@pytest.mark.parametrize("product", ["SPOT", "LINEAR"])
@pytest.mark.parametrize("target_name", ["observation", "spec", "decimal"])
@pytest.mark.parametrize("metadata_kind", ["empty", "hook"])
@pytest.mark.parametrize("missing_field", [False, True])
def test_instance_dataclass_metadata_cannot_control_declared_observation_fields(
    product, target_name, metadata_kind, missing_field
):
    class HostileMetadata:
        def values(self):
            raise AssertionError("METADATA_HOOK_EXECUTED")

    observation = parse(product)
    target, required_field = {
        "observation": (observation, "key"),
        "spec": (observation.spec, "price_tick"),
        "decimal": (observation.spec.price_tick, "raw"),
    }[target_name]
    metadata = {} if metadata_kind == "empty" else HostileMetadata()
    object.__setattr__(target, "__dataclass_fields__", metadata)
    if missing_field:
        object.__delattr__(target, required_field)
        rejected("CATALOG_OBSERVATION_INVALID", validate_instrument_observation, observation)
    else:
        # Extra instance metadata is not a declared field or serialization input.
        # In particular spec metadata must survive both validation and payload hashing.
        validate_instrument_observation(observation)


def test_source_labels_are_local_inputs_and_do_not_change_supported_semantics():
    original = parse("SPOT")
    for environment in ("PUBLIC_MAINNET", "PUBLIC_TESTNET", "FIXTURE"):
        copied = replace(original, source=source(source_environment=environment))
        assert copied.specification_revision == original.specification_revision
        validate_instrument_observation(copied)


def test_json_preserves_unicode_without_normalization_and_rejects_two_node_cycle():
    assert _canonical_json_bytes("é") == b'"\\u00e9"'
    assert _canonical_json_bytes("e\u0301") == b'"e\\u0301"'
    assert _canonical_json_bytes("😀") == b'"\\ud83d\\ude00"'
    left, right = {}, []
    left["next"] = right
    right.append(left)
    rejected("CATALOG_INPUT_INVALID", _canonical_json_bytes, left)


def test_fresh_isolated_import_and_evaluation_have_no_external_effects():
    script = r"""
import sys, builtins, socket, datetime, json, time, os
from decimal import Decimal
def deny(*args, **kwargs): raise AssertionError("external effect")
builtins.open = deny
socket.socket = deny
os.getenv = deny
def no_clock(frame, event, function):
    if event == "c_call" and getattr(function, "__name__", "") in (
        "now", "utcnow", "today", "time", "monotonic", "perf_counter"):
        raise AssertionError("clock read")
sys.setprofile(no_clock)
class Guard:
    def find_spec(self, name, path=None, target=None):
        if name.startswith(("nautilus_trader", "httpx", "requests")):
            raise AssertionError("forbidden import")
sys.meta_path.insert(0, Guard())
from multi_exchange_trader.market_data.instrument_definition import (
    DecimalField, CatalogSource, _canonical_json_bytes, _utc_datetime,
    INSTRUMENT_ENDPOINT, parse_bybit_instrument_response)
from multi_exchange_trader.trading_product import InstrumentKey
DecimalField("0.10", Decimal("0.10"), 2)
source = CatalogSource("synthetic", "FIXTURE", INSTRUMENT_ENDPOINT,
    datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc))
assert _canonical_json_bytes({"x":1}) == b'{"x":1}'
envelope = json.loads(sys.argv[1])
parse_bybit_instrument_response(envelope,
    expected=InstrumentKey("BYBIT", "SPOT", "BTCUSDT"), source=source)
assert not any(n.startswith(("nautilus_trader", "httpx", "requests")) for n in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, json.dumps(body("SPOT"))],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        check=False,
    )
    assert result.returncode == 0, result.stderr
