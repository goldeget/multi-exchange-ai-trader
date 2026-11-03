"""Synthetic source-neutral projection contract; no provider or native operations."""

import importlib
import inspect
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import MISSING, FrozenInstanceError, fields, replace
from decimal import Decimal, localcontext
from typing import Any, cast

import pytest

from multi_exchange_trader.market_data.instrument_definition import (
    CatalogRejected,
    LinearInstrumentSpec,
    SpotInstrumentSpec,
    _canonical_json_bytes,
    _decode_row,
    _parse_spec,
    _spec_payload,
    _spec_revision,
)
from multi_exchange_trader.trading_product import InstrumentKey, TradingProduct


def row(product: TradingProduct = "LINEAR") -> dict[str, object]:
    common: dict[str, object] = {
        "symbol": "BTCUSDT",
        "baseCoin": "BTC",
        "quoteCoin": "USDT",
        "status": "Trading",
    }
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


def projection_module() -> Any:
    # Import in the test body so absent behavior is an explicit test failure, not collection error.
    return importlib.import_module("multi_exchange_trader.market_data.instrument_source_projection")


def test_regular_row_projects_native_identity_and_preserves_modern_caps() -> None:
    module = projection_module()
    actual = module.project_supported_instrument_row(
        row(), expected=InstrumentKey("BYBIT", "LINEAR", "BTCUSDT")
    )
    assert actual.native_instrument_id == "BTCUSDT-LINEAR.BYBIT"
    assert actual.spec.max_limit_quantity.raw == "100"
    assert actual.spec.max_market_quantity.raw == "50"
    assert actual.spec.price_tick.raw == "0.10"
    assert actual.spec.price_tick.scale == 2


def project(product: TradingProduct = "LINEAR", value: object = None) -> Any:
    return projection_module().project_supported_instrument_row(
        row(product) if value is None else value,
        expected=InstrumentKey("BYBIT", product, "BTCUSDT"),
    )


def rejected(code: str, function: Callable[..., object], *args: Any, **kwargs: Any) -> None:
    with pytest.raises(CatalogRejected) as caught:
        function(*args, **kwargs)
    assert caught.value.code == code
    assert caught.value.args == (code,)
    assert str(caught.value) == code
    assert "SECRET_MARKER" not in repr(caught.value)


def forge(original: Any, **changes: object) -> Any:
    value = object.__new__(type(original))
    for item in fields(projection_module().SupportedInstrumentProjection):
        object.__setattr__(value, item.name, changes.get(item.name, getattr(original, item.name)))
    return value


@pytest.mark.parametrize("product", ["LINEAR", "SPOT"])
def test_exact_immutable_seven_fields_have_no_source_clock_or_authority(
    product: TradingProduct,
) -> None:
    module = projection_module()
    value = row(product)
    actual = project(product, value)
    assert type(actual) is module.SupportedInstrumentProjection
    assert tuple(item.name for item in fields(module.SupportedInstrumentProjection)) == (
        "key",
        "native_instrument_id",
        "parser_revision",
        "source_rule_revision",
        "specification_revision",
        "raw_row_canonical",
        "spec",
    )
    assert all(item.default is MISSING for item in fields(module.SupportedInstrumentProjection))
    assert actual.key == InstrumentKey("BYBIT", product, "BTCUSDT")
    assert type(actual.spec) is (
        LinearInstrumentSpec if product == "LINEAR" else SpotInstrumentSpec
    )
    assert actual.native_instrument_id == f"BTCUSDT-{product}.BYBIT"
    assert actual.parser_revision == "BYBIT_INSTRUMENT_PARSER_V1"
    assert actual.source_rule_revision == (
        "BYBIT_LINEAR_USDT_PERPETUAL_V1" if product == "LINEAR" else "BYBIT_SPOT_USDT_REGULAR_V1"
    )
    assert actual.spec.max_limit_quantity.raw == "100"
    assert actual.spec.max_market_quantity.raw == "50"
    assert actual.spec.max_quote_amount.field == "max_quote_amount"
    assert module.validate_supported_instrument_projection(actual) is None
    assert module.validate_supported_instrument_projection(replace(actual)) is None
    assert "raw_row_canonical=" not in repr(actual)
    with pytest.raises(FrozenInstanceError):
        actual.parser_revision = "changed"
    value["symbol"] = "ETHUSDT"
    cast(dict[str, object], value["priceFilter"])["tickSize"] = "1"
    assert module.validate_supported_instrument_projection(actual) is None


def test_frozen_helper_positional_signatures_and_supported_payload_are_pinned() -> None:
    assert tuple(inspect.signature(_parse_spec).parameters) == ("row", "key")
    assert tuple(inspect.signature(_spec_revision).parameters) == ("key", "spec")
    assert tuple(inspect.signature(_decode_row).parameters) == ("raw",)
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        for helper in (_parse_spec, _spec_revision)
        for parameter in inspect.signature(helper).parameters.values()
    )
    linear = project()
    spot = project("SPOT")
    payload = _spec_payload(linear.spec)
    assert payload["quantity_step"] == {"raw": "0.001", "value": "0.001", "scale": 3}
    assert payload["price_tick"] == {"raw": "0.10", "value": "0.10", "scale": 2}
    assert payload["source_price_scale"] == 2
    assert _spec_payload(spot.spec)["base_precision"] == {
        "raw": "0.001",
        "value": "0.001",
        "scale": 3,
    }
    # Characterization pins from frozen helpers, not the projection under test.
    assert linear.specification_revision == (
        "09a60851212020d003f3ba5dd7201977a712128663fd9ca692789e2116b26ecb"
    )
    assert spot.specification_revision == (
        "54631d58a12047471302db0b85a2607e0d04cd99e56414901eba97887f9ad302"
    )


def test_supported_decimal_scale_changes_revision_but_unknown_raw_fields_do_not() -> None:
    original = project()
    changed = row()
    cast(dict[str, object], changed["priceFilter"])["tickSize"] = "0.1"
    changed_projection = project(value=changed)
    assert changed_projection.spec.price_tick.value == Decimal("0.1")
    assert changed_projection.spec.price_tick.scale == 1
    assert changed_projection.specification_revision != original.specification_revision
    unknown = project(value=row() | {"unknown": "SECRET_MARKER"})
    assert unknown.raw_row_canonical != original.raw_row_canonical
    assert unknown.specification_revision == original.specification_revision
    assert "SECRET_MARKER" not in repr(unknown)
    with localcontext() as context:
        context.prec = 2
        assert project().specification_revision == original.specification_revision


def test_builder_exact_key_row_and_symbol_order_precedes_bounds_or_parsing() -> None:
    module = projection_module()

    class HostileDict(dict[str, object]):
        def get(self, key: str, default: object = None) -> object:
            raise AssertionError("caller hook invoked")

    class Text(str):
        pass

    class Key(InstrumentKey):
        pass

    bad_key = object.__new__(InstrumentKey)
    object.__setattr__(bad_key, "venue", "BYBIT")
    object.__setattr__(bad_key, "product", "INVERSE")
    object.__setattr__(bad_key, "symbol", "BTCUSDT")
    for key in (None, Key("BYBIT", "LINEAR", "BTCUSDT"), bad_key):
        rejected(
            "CATALOG_INPUT_INVALID", module.project_supported_instrument_row, row(), expected=key
        )
    value: object
    for value in (
        None,
        [],
        HostileDict(row()),
        row() | {"symbol": Text("BTCUSDT")},
        {"priceFilter": "bad"},
        row() | {"symbol": 1},
    ):
        rejected(
            "CATALOG_INPUT_INVALID",
            module.project_supported_instrument_row,
            value,
            expected=InstrumentKey("BYBIT", "LINEAR", "BTCUSDT"),
        )
    rejected(
        "CATALOG_SCOPE_MISMATCH",
        module.project_supported_instrument_row,
        {"symbol": "ETHUSDT", "oversized": "x" * 129},
        expected=InstrumentKey("BYBIT", "LINEAR", "BTCUSDT"),
    )


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"priceFilter": {}}, "CATALOG_FILTER_INVALID"),
        ({"lotSizeFilter": {"qtyStep": "1"}}, "CATALOG_FILTER_INVALID"),
        ({"isPreListing": True}, "CATALOG_INSTRUMENT_UNSUPPORTED"),
        ({"preListingInfo": {}}, "CATALOG_INSTRUMENT_UNSUPPORTED"),
        ({"status": "Closed"}, "CATALOG_INSTRUMENT_UNSUPPORTED"),
        ({"quoteCoin": "USDC"}, "CATALOG_INSTRUMENT_UNSUPPORTED"),
        ({"settleCoin": "USDC"}, "CATALOG_INSTRUMENT_UNSUPPORTED"),
        ({"priceScale": "33"}, "CATALOG_FILTER_INVALID"),
        ({"priceScale": True}, "CATALOG_FILTER_INVALID"),
        ({"unknown": 1.0}, "CATALOG_INPUT_INVALID"),
        ({"unknown": "\ud800"}, "CATALOG_INPUT_INVALID"),
        ({"unknown": "x" * 129}, "CATALOG_RESOURCE_LIMIT"),
        ({"unknown": [0] * 129}, "CATALOG_RESOURCE_LIMIT"),
        ({"unknown": 2**63}, "CATALOG_RESOURCE_LIMIT"),
        ({"unknown": [[[[[[[0]]]]]]]}, "CATALOG_RESOURCE_LIMIT"),
    ],
)
def test_builder_retains_frozen_helper_rejections(changes: dict[str, object], code: str) -> None:
    rejected(code, project, value=row() | changes)


def test_entire_unknown_row_is_bounded_before_invalid_supported_fields() -> None:
    invalid = row() | {"priceFilter": {}}
    invalid["unknown"] = ["x" * 128 for _ in range(128)]
    invalid["unknown2"] = ["x" * 128 for _ in range(128)]
    invalid["unknown3"] = ["x" * 128 for _ in range(128)]
    invalid["unknown4"] = ["x" * 128 for _ in range(128)]
    rejected("CATALOG_RESOURCE_LIMIT", project, value=invalid)


@pytest.mark.parametrize(
    "name,value",
    [
        ("key", None),
        ("native_instrument_id", "BTCUSDT-SPOT.BYBIT"),
        ("parser_revision", "SECRET_MARKER"),
        ("source_rule_revision", "SECRET_MARKER"),
        ("specification_revision", "0" * 64),
        ("specification_revision", 1),
        ("spec", None),
        ("raw_row_canonical", bytearray(b"{}")),
        ("raw_row_canonical", b""),
        ("raw_row_canonical", b"\xff"),
        ("raw_row_canonical", b"{} "),
        ("raw_row_canonical", b'{"symbol":"BTCUSDT","symbol":"BTCUSDT"}'),
        ("raw_row_canonical", b'{"number":1.2}'),
        ("raw_row_canonical", b'{"number":NaN}'),
        ("raw_row_canonical", b'{"number":9223372036854775808}'),
    ],
)
def test_constructor_and_public_validator_reject_inconsistent_dto_fields(
    name: str, value: object
) -> None:
    original = project()
    module = projection_module()
    rejected("CATALOG_OBSERVATION_INVALID", replace, original, **{name: value})
    rejected(
        "CATALOG_OBSERVATION_INVALID",
        module.validate_supported_instrument_projection,
        forge(original, **{name: value}),
    )


def test_forged_specs_raw_rows_cross_product_and_missing_fields_are_revalidated() -> None:
    module = projection_module()
    original = project()
    for alternate in (
        {"spec": project("SPOT").spec},
        {"raw_row_canonical": _canonical_json_bytes(row() | {"symbol": "ETHUSDT"})},
        {"raw_row_canonical": _canonical_json_bytes(row() | {"isPreListing": True})},
    ):
        rejected("CATALOG_OBSERVATION_INVALID", replace, original, **alternate)
    invalid_spec = object.__new__(LinearInstrumentSpec)
    for item in fields(LinearInstrumentSpec):
        object.__setattr__(invalid_spec, item.name, getattr(original.spec, item.name))
    object.__setattr__(invalid_spec, "price_tick", None)
    rejected(
        "CATALOG_OBSERVATION_INVALID",
        module.validate_supported_instrument_projection,
        forge(original, spec=invalid_spec),
    )
    for item in fields(module.SupportedInstrumentProjection):
        missing = forge(original)
        object.__delattr__(missing, item.name)
        object.__setattr__(missing, "__dataclass_fields__", {})
        rejected(
            "CATALOG_OBSERVATION_INVALID", module.validate_supported_instrument_projection, missing
        )
    subclass = type("Subclass", (module.SupportedInstrumentProjection,), {})
    for value in (None, object(), object.__new__(subclass)):
        rejected(
            "CATALOG_OBSERVATION_INVALID", module.validate_supported_instrument_projection, value
        )


def test_dto_revalidation_preserves_actual_resource_failures() -> None:
    module = projection_module()
    original = project()
    for raw in (b" " * 65537, _canonical_json_bytes(row())[:-1] + b',"z":"' + b"x" * 129 + b'"}'):
        rejected("CATALOG_RESOURCE_LIMIT", replace, original, raw_row_canonical=raw)
        rejected(
            "CATALOG_RESOURCE_LIMIT",
            module.validate_supported_instrument_projection,
            forge(original, raw_row_canonical=raw),
        )


def test_fresh_import_and_projection_have_no_runtime_io_environment_clock_or_native_access() -> (
    None
):
    script = r"""
import builtins, datetime, importlib.abc, inspect, io, json, os, pathlib, socket, sys, time
from dataclasses import dataclass, field, fields
from decimal import Decimal
from typing import Literal, NoReturn, cast

def denied(*args, **kwargs):
    raise AssertionError("forbidden runtime effect")

class GuardedDatetime(datetime.datetime):
    now = classmethod(denied)
    utcnow = classmethod(denied)

class NoSensitiveImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("nautilus_trader", "httpx", "multi_exchange_trader.account",
                                "multi_exchange_trader.execution", "multi_exchange_trader.settings")):
            raise AssertionError("forbidden import " + fullname)

def audit(event, args):
    if event == "open":
        frame = sys._getframe(1)
        while frame is not None:
            if frame.f_code.co_name == "get_data" and "_bootstrap_external" in frame.f_code.co_filename:
                return
            frame = frame.f_back
        denied()
    if event.startswith(("socket.", "subprocess.", "os.system", "os.spawn")):
        denied()

datetime.datetime = GuardedDatetime
for name in ("time", "time_ns", "monotonic", "monotonic_ns", "perf_counter", "perf_counter_ns"):
    setattr(time, name, denied)
os._Environ.__getitem__ = denied
os._Environ.get = denied
sys.meta_path.insert(0, NoSensitiveImports())
sys.addaudithook(audit)
from multi_exchange_trader.trading_product import InstrumentKey
from multi_exchange_trader.market_data.instrument_source_projection import (
    project_supported_instrument_row, validate_supported_instrument_projection
)
value = project_supported_instrument_row(
    {"symbol":"BTCUSDT","baseCoin":"BTC","quoteCoin":"USDT","status":"Trading",
     "priceFilter":{"tickSize":"0.10"},
     "lotSizeFilter":{"basePrecision":"0.001","quotePrecision":"0.01","minOrderAmt":"5",
                      "maxLimitOrderQty":"100","maxMarketOrderQty":"50"}},
    expected=InstrumentKey("BYBIT","SPOT","BTCUSDT"))
validate_supported_instrument_projection(value)
assert value.native_instrument_id == "BTCUSDT-SPOT.BYBIT"
assert "nautilus_trader" not in sys.modules
print("PURE_IMPORT_AND_PROJECTION_OK")
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "PURE_IMPORT_AND_PROJECTION_OK"
