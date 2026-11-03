"""Synthetic offline declared-universe catalog contracts."""

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo

import pytest

from multi_exchange_trader.market_data.instrument_catalog import (
    CATALOG_SCHEMA_VERSION,
    InstrumentCatalog,
    build_instrument_catalog,
    require_catalog_selection,
)
from multi_exchange_trader.market_data.instrument_definition import (
    INSTRUMENT_ENDPOINT,
    PARSER_REVISION,
    SPOT_SOURCE_RULE_REVISION,
    CatalogRejected,
    CatalogSource,
    parse_bybit_instrument_response,
)
from multi_exchange_trader.trading_product import InstrumentKey

NOW = datetime(2024, 1, 1, 0, 0, 2, tzinfo=UTC)
TTL = timedelta(seconds=10)


def observation(
    product="SPOT",
    symbol="BTCUSDT",
    *,
    at=None,
    source_id="synthetic",
    env="FIXTURE",
    extra=None,
    tick="0.10",
    scale="2",
):
    row = {"symbol": symbol, "baseCoin": "BTC", "quoteCoin": "USDT", "status": "Trading"}
    if product == "SPOT":
        row |= {
            "priceFilter": {"tickSize": tick},
            "lotSizeFilter": {
                "basePrecision": "0.001",
                "quotePrecision": "0.01",
                "minOrderAmt": "5",
                "maxLimitOrderQty": "100",
                "maxMarketOrderQty": "50",
            },
        }
    else:
        row |= {
            "settleCoin": "USDT",
            "contractType": "LinearPerpetual",
            "priceScale": scale,
            "isPreListing": False,
            "priceFilter": {"tickSize": tick, "minPrice": "0.1", "maxPrice": "1000000"},
            "lotSizeFilter": {
                "qtyStep": "0.001",
                "minOrderQty": "0.001",
                "maxOrderQty": "100",
                "maxMktOrderQty": "50",
                "minNotionalValue": "5",
            },
            "leverageFilter": {"minLeverage": "1", "maxLeverage": "100", "leverageStep": "0.01"},
        }
    if extra is not None:
        row["uninterpreted"] = extra
    return parse_bybit_instrument_response(
        {
            "retCode": 0,
            "time": 1704067200000,
            "result": {"category": product.lower(), "list": [row]},
        },
        expected=InstrumentKey("BYBIT", product, symbol),
        source=CatalogSource(source_id, env, INSTRUMENT_ENDPOINT, at or NOW - timedelta(seconds=1)),
    )


def build(*observations, **changes):
    values = {
        "declared_universe": tuple(item.key for item in observations),
        "now": NOW,
        "max_age": TTL,
    }
    return build_instrument_catalog(tuple(observations), **(values | changes))


def select(catalog, **changes):
    values = {
        "allowed": catalog.declared_universe,
        "selected": catalog.declared_universe,
        "expected_product": "SPOT",
        "expected_source_environment": "FIXTURE",
        "expected_revision": catalog.revision,
        "now": NOW,
    }
    return require_catalog_selection(catalog, **(values | changes))


def rejected(code, function, *args, **kwargs):
    with pytest.raises(CatalogRejected) as caught:
        function(*args, **kwargs)
    assert caught.value.code == code
    assert caught.value.args == (code,)
    assert str(caught.value) == code


def forge(value, **changes):
    result = object.__new__(type(value))
    for field in fields(value):
        object.__setattr__(result, field.name, changes.get(field.name, getattr(value, field.name)))
    return result


def test_build_canonicalizes_exact_coverage_and_keeps_same_symbol_products_distinct():
    spot, linear, eth = observation(), observation("LINEAR"), observation(symbol="ETHUSDT")
    catalog = build(eth, spot, linear)
    assert type(catalog) is InstrumentCatalog
    assert CATALOG_SCHEMA_VERSION == catalog.schema_version == 1
    assert catalog.declared_universe == (linear.key, spot.key, eth.key)
    assert catalog.observations == (linear, spot, eth)
    assert catalog.complete_for_declared_universe is True
    assert catalog.expires_at == datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC)
    assert build(linear, eth, spot).revision == catalog.revision
    with pytest.raises(FrozenInstanceError):
        catalog.revision = "changed"


@pytest.mark.parametrize("kind", ["missing", "extra", "duplicate_key", "duplicate_observation"])
def test_coverage_is_exact_and_duplicates_are_not_silently_removed(kind):
    spot, other = observation(), observation(symbol="ETHUSDT")
    rows, keys = (spot,), (spot.key,)
    if kind == "missing":
        keys = (spot.key, other.key)
    elif kind == "extra":
        rows = (spot, other)
    elif kind == "duplicate_key":
        keys *= 2
    else:
        rows *= 2
    rejected(
        "CATALOG_COVERAGE_INVALID",
        build_instrument_catalog,
        rows,
        declared_universe=keys,
        now=NOW,
        max_age=TTL,
    )


def test_empty_and_over_limit_collections_reject_but_exact_hundred_works():
    rejected("CATALOG_COVERAGE_INVALID", build)
    rows = tuple(observation(symbol=f"COIN{i}USDT") for i in range(101))
    assert len(build(*rows[:100]).observations) == 100
    rejected("CATALOG_RESOURCE_LIMIT", build, *rows)


@pytest.mark.parametrize(
    "name,value",
    [
        ("declared_universe", []),
        ("max_age", 10),
        ("max_age", timedelta(0)),
        ("max_age", timedelta(seconds=-1)),
    ],
)
def test_builder_rejects_wrong_container_and_ttl_types(name, value):
    rejected("CATALOG_INPUT_INVALID", build, observation(), **{name: value})


def test_exact_tuple_and_key_classes_precede_untrusted_hooks():
    class TupleSubclass(tuple):
        def __iter__(self):
            raise AssertionError("untrusted tuple hook")

    class KeySubclass(InstrumentKey):
        pass

    item = observation()
    for rows, keys in [
        (TupleSubclass((item,)), (item.key,)),
        ((item,), TupleSubclass((item.key,))),
        ((item,), (KeySubclass("BYBIT", "SPOT", "BTCUSDT"),)),
    ]:
        rejected(
            "CATALOG_INPUT_INVALID",
            build_instrument_catalog,
            rows,
            declared_universe=keys,
            now=NOW,
            max_age=TTL,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_id", "different"),
        ("source_environment", "PUBLIC_MAINNET"),
        ("endpoint", "/different"),
    ],
)
def test_one_source_id_environment_and_path_required_across_all_products(field, value):
    first, second = observation(), observation("LINEAR")
    source = forge(second.source, **{field: value})
    bad = forge(second, source=source)
    code = "CATALOG_OBSERVATION_INVALID" if field == "endpoint" else "CATALOG_SOURCE_MISMATCH"
    rejected(code, build, first, bad)
    assert build(first, observation("LINEAR", at=NOW)).complete_for_declared_universe


def test_revision_exact_payload_and_golden_digest():
    item = observation()
    catalog = build(item)
    key = {"venue": "BYBIT", "product": "SPOT", "symbol": "BTCUSDT"}
    payload = {
        "schema_version": 1,
        "parser_revision": PARSER_REVISION,
        "source_rule_revisions": [SPOT_SOURCE_RULE_REVISION],
        "source_environment": "FIXTURE",
        "endpoint": INSTRUMENT_ENDPOINT,
        "declared_universe": [key],
        "specifications": [{"key": key, "specification_revision": item.specification_revision}],
        "max_age_microseconds": 10000000,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    assert catalog.revision == hashlib.sha256(encoded).hexdigest()
    assert (
        item.specification_revision
        == "54631d58a12047471302db0b85a2607e0d04cd99e56414901eba97887f9ad302"
    )
    assert catalog.revision == "95a2de3cfe762a00180bba17f0a287fa16ac9e776a54dd9ed8f1381d49d87fc7"


def test_revision_excludes_only_collection_identity_time_and_raw_extra():
    first = build(observation())
    changed = observation(at=NOW, source_id="another", extra="new raw field")
    changed = replace(changed, response_time=changed.response_time + timedelta(milliseconds=1))
    assert build(changed).revision == first.revision
    assert build(observation(tick="0.1")).revision != first.revision
    assert build(observation(env="PUBLIC_TESTNET")).revision != first.revision
    assert build(observation(), max_age=TTL + timedelta(microseconds=1)).revision != first.revision
    assert build(observation(symbol="ETHUSDT")).revision != first.revision
    assert build(observation("LINEAR", scale="1")).revision != build(observation("LINEAR")).revision


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("schema_version", True, "CATALOG_INPUT_INVALID"),
        ("schema_version", 2, "CATALOG_INPUT_INVALID"),
        ("revision", "0" * 64, "CATALOG_REVISION_MISMATCH"),
        ("revision", "SECRET_MARKER", "CATALOG_REVISION_MISMATCH"),
        ("expires_at", NOW, "CATALOG_TIME_INVALID"),
        ("declared_universe", (), "CATALOG_COVERAGE_INVALID"),
        ("observations", (), "CATALOG_COVERAGE_INVALID"),
    ],
)
def test_manual_constructor_and_computed_completeness_revalidate_integrity(field, value, code):
    catalog = build(observation())
    rejected(code, replace, catalog, **{field: value})
    corrupt = forge(catalog, **{field: value})
    rejected(code, lambda: corrupt.complete_for_declared_universe)
    rejected(code, select, corrupt)


def test_manual_noncanonical_order_rejects_and_missing_fields_are_fixed_rejections():
    catalog = build(observation(), observation(symbol="ETHUSDT"))
    for field in ("declared_universe", "observations"):
        rejected(
            "CATALOG_COVERAGE_INVALID",
            replace,
            catalog,
            **{field: tuple(reversed(getattr(catalog, field)))},
        )
    for field in fields(catalog):
        corrupt = forge(catalog)
        object.__delattr__(corrupt, field.name)
        rejected(
            "CATALOG_INPUT_INVALID", lambda value=corrupt: value.complete_for_declared_universe
        )
        rejected(
            "CATALOG_INPUT_INVALID",
            require_catalog_selection,
            corrupt,
            allowed=catalog.declared_universe,
            selected=catalog.declared_universe,
            expected_revision=catalog.revision,
            expected_product="SPOT",
            expected_source_environment="FIXTURE",
            now=NOW,
        )


def test_full_catalog_revalidates_unselected_other_product_observations():
    spot, linear = observation(), observation("LINEAR")
    catalog = build(spot, linear)
    corrupt = forge(catalog, observations=(forge(linear, raw_row_sha256="0" * 64), spot))
    rejected(
        "CATALOG_OBSERVATION_INVALID", select, corrupt, allowed=(spot.key,), selected=(spot.key,)
    )
    corrupt = forge(
        catalog,
        observations=(
            forge(linear, source=forge(linear.source, received_at=NOW + timedelta(seconds=1))),
            spot,
        ),
    )
    rejected("CATALOG_TIME_INVALID", select, corrupt, allowed=(spot.key,), selected=(spot.key,))


def test_freshness_uses_earliest_response_or_receipt_and_equality_is_expired():
    catalog = build(observation(at=NOW))
    assert select(catalog, now=catalog.expires_at - timedelta(microseconds=1))
    rejected("CATALOG_EXPIRED", select, catalog, now=catalog.expires_at)
    rejected("CATALOG_EXPIRED", build, observation(), now=catalog.expires_at)
    assert catalog.complete_for_declared_universe is True
    recent_spot = replace(observation(at=NOW), response_time=NOW - timedelta(milliseconds=1))
    older_linear = observation("LINEAR")
    mixed = build(recent_spot, older_linear)
    rejected(
        "CATALOG_EXPIRED",
        select,
        mixed,
        now=mixed.expires_at,
        allowed=(recent_spot.key,),
        selected=(recent_spot.key,),
    )


def test_future_inverted_and_overflow_time_are_rejected():
    item = observation()
    rejected("CATALOG_TIME_INVALID", build, observation(at=NOW + timedelta(microseconds=1)))
    rejected("CATALOG_TIME_INVALID", build, forge(item, response_time=NOW))
    rejected("CATALOG_TIME_INVALID", build, item, max_age=timedelta.max)
    rejected("CATALOG_TIME_INVALID", build, item, now=datetime.min.replace(tzinfo=UTC))


def test_future_receipt_is_time_invalid_even_when_another_product_is_stale():
    stale_linear = observation("LINEAR")
    future_spot = observation(at=NOW + timedelta(seconds=1))
    rejected("CATALOG_TIME_INVALID", build, stale_linear, future_spot, max_age=timedelta(seconds=1))


def test_exact_datetime_timedelta_contract_and_fixed_offsets():
    class DateSubclass(datetime):
        pass

    class DeltaSubclass(timedelta):
        pass

    class HostileTimezone(tzinfo):
        def utcoffset(self, dt):
            raise AssertionError("timezone hook")

    item = observation()
    for bad in [
        NOW.replace(tzinfo=None),
        DateSubclass(2024, 1, 1, tzinfo=UTC),
        NOW.replace(tzinfo=HostileTimezone()),
        object(),
        datetime.max.replace(tzinfo=timezone(timedelta(hours=-1))),
    ]:
        rejected("CATALOG_TIME_INVALID", build, item, now=bad)
        rejected("CATALOG_TIME_INVALID", select, build(item), now=bad)
    rejected("CATALOG_INPUT_INVALID", build, item, max_age=DeltaSubclass(seconds=10))
    shifted = NOW.astimezone(timezone(timedelta(hours=9)))
    assert build(item, now=shifted) == build(item)


def test_selection_returns_exact_selected_order_and_expected_product_is_explicit():
    spot, eth, linear = observation(), observation(symbol="ETHUSDT"), observation("LINEAR")
    catalog = build(spot, eth, linear)
    assert select(catalog, allowed=(spot.key, eth.key), selected=(eth.key, spot.key)) == (eth, spot)
    assert select(
        catalog, allowed=(linear.key,), selected=(linear.key,), expected_product="LINEAR"
    ) == (linear,)
    rejected(
        "CATALOG_SELECTION_INVALID",
        select,
        catalog,
        allowed=(spot.key, linear.key),
        selected=(spot.key,),
    )


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"allowed": ()}, "CATALOG_SELECTION_INVALID"),
        ({"selected": ()}, "CATALOG_SELECTION_INVALID"),
        ({"allowed": []}, "CATALOG_SELECTION_INVALID"),
        ({"selected": []}, "CATALOG_SELECTION_INVALID"),
        ({"expected_product": "spot"}, "CATALOG_SELECTION_INVALID"),
        ({"expected_product": True}, "CATALOG_SELECTION_INVALID"),
        ({"expected_source_environment": "DEMO"}, "CATALOG_SOURCE_MISMATCH"),
        ({"expected_source_environment": True}, "CATALOG_SOURCE_MISMATCH"),
        ({"expected_source_environment": "PUBLIC_TESTNET"}, "CATALOG_SOURCE_MISMATCH"),
        ({"expected_revision": "0" * 64}, "CATALOG_REVISION_MISMATCH"),
        ({"expected_revision": None}, "CATALOG_REVISION_MISMATCH"),
    ],
)
def test_selection_has_no_implicit_product_domain_or_revision_and_requires_exact_tuples(
    changes, code
):
    rejected(code, select, build(observation()), **changes)


def test_selection_rejects_unknown_allowed_duplicate_and_non_subset_without_filtering():
    spot, eth, unknown = (
        observation(),
        observation(symbol="ETHUSDT"),
        observation(symbol="OTHERUSDT"),
    )
    catalog = build(spot, eth)
    for allowed, selected in [
        ((spot.key, unknown.key), (spot.key,)),
        ((spot.key,), (eth.key,)),
        ((spot.key, spot.key), (spot.key,)),
        ((spot.key,), (spot.key, spot.key)),
        ((object(),), (spot.key,)),
    ]:
        rejected("CATALOG_SELECTION_INVALID", select, catalog, allowed=allowed, selected=selected)


def test_matching_old_revision_is_static_integrity_and_not_current_issuer_proof():
    old = build(observation())
    newer = build(observation(tick="0.2"))
    assert newer.revision != old.revision
    assert select(old, expected_revision=old.revision) == old.observations
    rejected("CATALOG_REVISION_MISMATCH", select, old, expected_revision=newer.revision)


def test_fresh_isolated_import_build_selection_have_no_application_io_or_clock():
    script = r"""
import sys, builtins, socket, datetime, os, json, io
def deny(*a, **kw): raise AssertionError("external effect")
builtins.open = deny
io.open = deny
os.open = deny
socket.socket = deny
os.getenv = deny
class NoEnvironment(dict):
    def __getitem__(self, key): return deny()
    def get(self, *args): return deny()
    def __iter__(self): return deny()
    def __contains__(self, key): return deny()
    def items(self): return deny()
    def keys(self): return deny()
    def values(self): return deny()
os.environ = NoEnvironment()
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
    CatalogSource, parse_bybit_instrument_response)
from multi_exchange_trader.market_data.instrument_catalog import (
    build_instrument_catalog, require_catalog_selection)
from multi_exchange_trader.trading_product import InstrumentKey
now = datetime.datetime(2024,1,1,0,0,2,tzinfo=datetime.timezone.utc)
key = InstrumentKey("BYBIT", "SPOT", "BTCUSDT")
observation = parse_bybit_instrument_response(json.loads(sys.argv[1]), expected=key,
    source=CatalogSource("synthetic", "FIXTURE", "/v5/market/instruments-info", now))
catalog = build_instrument_catalog((observation,), declared_universe=(key,), now=now,
    max_age=datetime.timedelta(seconds=10))
assert catalog.complete_for_declared_universe
assert require_catalog_selection(catalog, allowed=(key,), selected=(key,),
    expected_product="SPOT", expected_source_environment="FIXTURE",
    expected_revision=catalog.revision, now=now) == (observation,)
"""
    item = observation()
    envelope = {
        "retCode": 0,
        "time": 1704067200000,
        "result": {"category": "spot", "list": [json.loads(item.raw_row_canonical)]},
    }
    result = subprocess.run(
        [sys.executable, "-c", script, json.dumps(envelope)],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        check=False,
    )
    assert result.returncode == 0, result.stderr
