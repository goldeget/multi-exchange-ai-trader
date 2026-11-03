"""Synthetic metadata only: no strategy seed, issuer, runtime, or venue approval."""

import ast
import builtins
import datetime
import hashlib
import importlib.util
import io
import json
import os
import socket
import sys
import time
from dataclasses import MISSING, FrozenInstanceError, fields, replace
from pathlib import Path

import pytest

from multi_exchange_trader.execution import strategy_catalog as subject
from multi_exchange_trader.execution.modes import StrategyReference
from multi_exchange_trader.execution.strategy_catalog import (
    StrategyCatalog,
    StrategyCatalogRejected,
    StrategyCatalogSelection,
    StrategyDescriptor,
    build_strategy_catalog,
    require_strategy_references,
)
from multi_exchange_trader.trading_product import InstrumentKey

SPOT = InstrumentKey("BYBIT", "SPOT", "BTCUSDT")
LINEAR = InstrumentKey("BYBIT", "LINEAR", "BTCUSDT")
INVALID = "STRATEGY_CATALOG_INVALID_INPUT"
MISMATCH = "STRATEGY_CATALOG_REVISION_MISMATCH"
CODES = (
    INVALID, "DUPLICATE_STRATEGY_REFERENCE", "CONFLICTING_STRATEGY_REFERENCE", MISMATCH,
    "STRATEGY_CATALOG_EMPTY", "STRATEGY_ALLOWLIST_EMPTY", "STRATEGY_SELECTION_OUTSIDE_ALLOWLIST",
    "UNKNOWN_STRATEGY_REFERENCE", "STRATEGY_NOT_APPROVED", "STRATEGY_ENVIRONMENT_MISMATCH",
    "STRATEGY_INSTRUMENT_MISMATCH",
)


def descriptor(**changes):
    values = {
        "reference": StrategyReference("fixture-trend", "v1"),
        "implementation_sha256": "a" * 64, "config_sha256": "b" * 64, "config_schema_version": 1,
        "engine_contract_version": "fixture-engine-v1", "permitted_instruments": (SPOT,),
        "approval_reference": "fixture-approval-v1", "approval_source_reference": "fixture-source-v1",
        "approval_source_sha256": "c" * 64, "approval_environments": ("DEMO",),
        "approval_status": "APPROVED",
    }
    return StrategyDescriptor(**(values | changes))


def forged(cls, **values):
    value = object.__new__(cls)
    for name, item in values.items():
        object.__setattr__(value, name, item)
    return value


def changed(value, **changes):
    return forged(type(value), **({field.name: getattr(value, field.name) for field in fields(value)}
                                 | changes))


def rejected(code, call):
    with pytest.raises(StrategyCatalogRejected) as caught:
        call()
    assert caught.value.code == code
    assert str(caught.value) == code
    assert caught.value.__suppress_context__ or caught.value.__context__ is None


def oracle(entries):
    payloads = []
    for entry in entries:
        payloads.append({
            "reference": {"strategy_id": entry.reference.strategy_id,
                          "revision": entry.reference.revision},
            "implementation_sha256": entry.implementation_sha256,
            "config_sha256": entry.config_sha256,
            "config_schema_version": entry.config_schema_version,
            "engine_contract_version": entry.engine_contract_version,
            "permitted_instruments": [
                {"venue": key.venue, "product": key.product, "symbol": key.symbol}
                for key in sorted(entry.permitted_instruments,
                                  key=lambda key: (key.venue, key.product, key.symbol))
            ],
            "approval_reference": entry.approval_reference,
            "approval_source_reference": entry.approval_source_reference,
            "approval_source_sha256": entry.approval_source_sha256,
            "approval_environments": sorted(entry.approval_environments),
            "approval_status": entry.approval_status,
        })
    payloads.sort(key=lambda value: (value["reference"]["strategy_id"],
                                     value["reference"]["revision"]))
    data = json.dumps({"schema_version": 1, "entries": payloads}, sort_keys=True,
                      ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


class Unsafe:
    def __hash__(self):
        raise AssertionError("unsafe hash")

    def __eq__(self, other):
        raise AssertionError("unsafe equality")

    def __lt__(self, other):
        raise AssertionError("unsafe ordering")

    def __str__(self):
        raise AssertionError("unsafe string")


class UnsafeMeta(type):
    def __hash__(cls):
        raise AssertionError("unsafe metaclass hash")

    def __eq__(cls, other):
        raise AssertionError("unsafe metaclass equality")


class UnsafeType(metaclass=UnsafeMeta):
    pass


def test_record_contract_uses_existing_types_and_required_fields():
    assert subject.StrategyReference is StrategyReference
    assert subject.InstrumentKey is InstrumentKey
    names = {
        StrategyDescriptor: (
            "reference", "implementation_sha256", "config_sha256", "config_schema_version",
            "engine_contract_version", "permitted_instruments", "approval_reference",
            "approval_source_reference", "approval_source_sha256", "approval_environments",
            "approval_status",
        ),
        StrategyCatalog: ("schema_version", "revision", "entries"),
        StrategyCatalogSelection: ("catalog_revision", "instrument", "environment", "allowed",
                                   "selected"),
    }
    for cls, expected in names.items():
        assert tuple(field.name for field in fields(cls)) == expected
        assert all(field.default is MISSING and field.default_factory is MISSING
                   for field in fields(cls))
        with pytest.raises(TypeError):
            cls()
    entry = descriptor()
    with pytest.raises(FrozenInstanceError):
        entry.config_schema_version = 2
    rejected(INVALID, lambda: descriptor(config_schema_version=True))
    rejected(INVALID, lambda: StrategyCatalog(True, oracle(()), ()))


def test_build_empty_catalog_has_exact_canonical_revision():
    expected = "sha256:1038daeddcbbd28c7ee6feb49e2ddbb6ea1587d4e9f8b3282ef5ed1813165f23"
    assert "sha256:" + hashlib.sha256(b'{"entries":[],"schema_version":1}').hexdigest() == expected
    assert build_strategy_catalog(()) == StrategyCatalog(1, expected, ())


def test_build_preserves_entries_but_hashes_set_order_canonically():
    first = descriptor(permitted_instruments=(SPOT, LINEAR), approval_environments=("DEMO", "LIVE"))
    second = descriptor(reference=StrategyReference("fixture-trend", "v2"))
    entries = (second, first)
    catalog = build_strategy_catalog(entries)
    assert catalog.entries is entries
    assert catalog.entries[0] is second and catalog.entries[1] is first
    assert catalog.revision == oracle(entries)
    permuted = replace(first, permitted_instruments=(LINEAR, SPOT),
                       approval_environments=("LIVE", "DEMO"))
    assert build_strategy_catalog((permuted, second)).revision == catalog.revision


@pytest.mark.parametrize("change", [
    {"reference": StrategyReference("fixture-other", "v1")},
    {"reference": StrategyReference("fixture-trend", "v2")},
    {"implementation_sha256": "d" * 64}, {"config_sha256": "d" * 64},
    {"config_schema_version": 2}, {"engine_contract_version": "fixture-engine-v2"},
    {"permitted_instruments": (LINEAR,)},
    {"permitted_instruments": (InstrumentKey("BYBIT", "SPOT", "ETHUSDT"),)},
    {"permitted_instruments": (SPOT, LINEAR)},
    {"approval_reference": "fixture-approval-v2"},
    {"approval_source_reference": "fixture-source-v2"}, {"approval_source_sha256": "d" * 64},
    {"approval_environments": ("LIVE",)}, {"approval_status": "REVOKED"},
])
def test_each_semantic_field_and_removal_changes_revision(change):
    original = build_strategy_catalog((descriptor(),))
    assert build_strategy_catalog((descriptor(**change),)).revision != original.revision
    assert build_strategy_catalog(()).revision != original.revision


def test_duplicate_and_conflicting_exact_references():
    entry = descriptor(permitted_instruments=(SPOT, LINEAR), approval_environments=("DEMO", "LIVE"))
    rejected("DUPLICATE_STRATEGY_REFERENCE", lambda: build_strategy_catalog((entry, entry)))
    duplicate = replace(entry, permitted_instruments=(LINEAR, SPOT),
                        approval_environments=("LIVE", "DEMO"))
    rejected("DUPLICATE_STRATEGY_REFERENCE", lambda: build_strategy_catalog((entry, duplicate)))
    rejected("CONFLICTING_STRATEGY_REFERENCE", lambda: build_strategy_catalog(
        (entry, replace(entry, config_sha256="d" * 64))))
    assert len(build_strategy_catalog((entry, descriptor(
        reference=StrategyReference("fixture-trend", "v2")))).entries) == 2


@pytest.mark.parametrize("change", [
    {"reference": {}}, {"reference": forged(StrategyReference)},
    *[{"reference": StrategyReference(value, "v1")} for value in
      ("", "a" * 97, "é", " x", "x?", Unsafe(), UnsafeType())],
    *[{"reference": StrategyReference("fixture-trend", value)} for value in
      ("latest", "LATEST", "LaTeSt", "", "a" * 97)],
    *[{field: value} for field in ("implementation_sha256", "config_sha256",
                                   "approval_source_sha256")
      for value in ("A" * 64, "a" * 63, "a" * 65, "z" * 64, Unsafe())],
    *[{"config_schema_version": value} for value in (True, 0, -1, 2147483648, 1.0, Unsafe())],
    *[{field: value} for field in ("engine_contract_version", "approval_reference",
                                   "approval_source_reference")
      for value in ("", "a" * 97, "é", Unsafe())],
    *[{"permitted_instruments": value} for value in
      ((), [], (SPOT, SPOT), (Unsafe(),), (forged(InstrumentKey),),
       (changed(SPOT, venue=Unsafe()),), (changed(SPOT, product="spot"),),
       (changed(SPOT, symbol="btc"),))],
    *[{"approval_environments": value} for value in
      ((), [], ("DEMO", "DEMO"), ("DEMO", "LIVE", "DEMO"), ("demo",), (Unsafe(),))],
    *[{"approval_status": value} for value in ("approved", "", Unsafe(), UnsafeType())],
])
def test_strict_record_field_and_bound_rejections(change):
    rejected(INVALID, lambda: descriptor(**change))
    invalid = changed(descriptor(), **change)
    rejected(INVALID, lambda: build_strategy_catalog((invalid,)))
    rejected(INVALID, lambda: StrategyCatalog(1, "sha256:" + "0" * 64, (invalid,)))


@pytest.mark.parametrize("value", [[], {}, (Unsafe(),), (forged(StrategyDescriptor),)])
def test_build_rejects_wrong_entries_and_missing_attributes(value):
    rejected(INVALID, lambda: build_strategy_catalog(value))


@pytest.mark.parametrize("schema,revision", [(True, "sha256:" + "0" * 64),
    (2, "sha256:" + "0" * 64), (1, "SHA256:" + "a" * 64), (1, Unsafe())])
def test_catalog_constructor_checks_schema_and_revision(schema, revision):
    rejected(INVALID, lambda: StrategyCatalog(schema, revision, ()))


def test_catalog_constructor_checks_digest_and_descriptor_preflight():
    rejected(MISMATCH, lambda: StrategyCatalog(1, "sha256:" + "0" * 64, ()))
    # A later oversized domain must be rejected before traversing the first entry's unsafe fields.
    first = changed(descriptor(), reference=Unsafe())
    second = changed(descriptor(), permitted_instruments=(Unsafe(),) * 101)
    rejected(INVALID, lambda: build_strategy_catalog((first, second)))


def test_collection_resource_limits():
    domain = tuple(InstrumentKey("BYBIT", "SPOT", f"X{index:03}") for index in range(100))
    entry = descriptor(permitted_instruments=domain, approval_environments=("DEMO", "LIVE"),
                       config_schema_version=2147483647, engine_contract_version="a" * 96)
    entries = tuple(replace(entry, reference=StrategyReference(f"fixture-{i}", "v1"))
                    for i in range(100))
    assert len(build_strategy_catalog(entries).entries) == 100
    rejected(INVALID, lambda: descriptor(permitted_instruments=domain + (Unsafe(),)))
    rejected(INVALID, lambda: build_strategy_catalog(entries + (Unsafe(),)))
    rejected(INVALID, lambda: StrategyCatalog(1, "sha256:" + "0" * 64, entries + (Unsafe(),)))


@pytest.mark.parametrize("code", CODES)
def test_rejection_code_constructor_is_closed(code):
    error = StrategyCatalogRejected(code)
    assert error.code == code and str(error) == code


@pytest.mark.parametrize("value", ["custom-unsafe-secret", 1, None, Unsafe(), UnsafeType()])
def test_rejection_code_constructor_rejects_unknown_safely(value):
    with pytest.raises(ValueError, match="^INVALID_STRATEGY_CATALOG_REJECTION_CODE$"):
        StrategyCatalogRejected(value)


def test_rejection_code_constructor_rejects_str_subclass():
    class Text(str):
        def __hash__(self):
            raise AssertionError("unsafe subclass hash")

    with pytest.raises(ValueError, match="^INVALID_STRATEGY_CATALOG_REJECTION_CODE$"):
        StrategyCatalogRejected(Text(INVALID))


def require(catalog, **changes):
    arguments = {"expected_catalog_revision": catalog.revision,
                 "allowed": (StrategyReference("fixture-trend", "v1"),), "selected": (),
                 "instrument": SPOT, "environment": "DEMO"}
    return require_strategy_references(catalog, **(arguments | changes))


def selection(allowed_entries, selected_entries=(), **changes):
    arguments = {"catalog_revision": "sha256:" + "0" * 64, "instrument": SPOT, "environment": "DEMO",
                 "allowed": allowed_entries, "selected": selected_entries}
    return StrategyCatalogSelection(**(arguments | changes))


def test_require_preserves_allowed_selected_order_and_empty_subset():
    one, two = descriptor(), descriptor(reference=StrategyReference("fixture-trend", "v2"))
    catalog = build_strategy_catalog((one, two))
    result = require(catalog, allowed=(two.reference, one.reference),
                     selected=(one.reference, two.reference))
    assert result.catalog_revision == catalog.revision
    assert result.instrument is SPOT and result.environment == "DEMO"
    assert result.allowed == (two, one) and result.selected == (one, two)
    assert result.allowed[0] is two and result.selected[0] is one
    assert require(catalog).selected == ()
    with pytest.raises(FrozenInstanceError):
        result.catalog_revision = "other"
    with pytest.raises(FrozenInstanceError):
        catalog.revision = "other"


@pytest.mark.parametrize("changes,code", [
    ({"schema_version": True}, INVALID), ({"schema_version": 2}, INVALID),
    ({"revision": "sha256:" + "0" * 64}, MISMATCH), ({"revision": "bad"}, INVALID),
    ({"entries": []}, INVALID), ({"entries": (changed(descriptor(), config_sha256=Unsafe()),)},
                             INVALID),
    ({"entries": (descriptor(), changed(descriptor(), approval_source_reference=Unsafe()))},
     INVALID),
])
def test_require_revalidates_entire_catalog_and_digest(changes, code):
    catalog = build_strategy_catalog((descriptor(),))
    rejected(code, lambda: require(changed(catalog, **changes)))


def test_require_rejects_stale_expected_revision():
    old = build_strategy_catalog((descriptor(),))
    new = build_strategy_catalog((descriptor(approval_source_reference="fixture-source-v2"),))
    rejected(MISMATCH, lambda: require(new, expected_catalog_revision=old.revision))


def test_require_empty_and_subset_precedence():
    empty = build_strategy_catalog(())
    rejected("STRATEGY_CATALOG_EMPTY", lambda: require(empty, allowed=(), selected=()))
    catalog = build_strategy_catalog((descriptor(),))
    rejected("STRATEGY_ALLOWLIST_EMPTY", lambda: require(catalog, allowed=()))
    unknown = StrategyReference("fixture-unknown", "v1")
    rejected("STRATEGY_SELECTION_OUTSIDE_ALLOWLIST", lambda: require(catalog,
        allowed=(unknown,), selected=(descriptor().reference,)))
    rejected("STRATEGY_SELECTION_OUTSIDE_ALLOWLIST", lambda: require(catalog,
        selected=(StrategyReference("fixture-trend", "v2"),)))


@pytest.mark.parametrize("offender,code", [
    (None, "UNKNOWN_STRATEGY_REFERENCE"),
    ({"approval_status": "DEPRECATED"}, "STRATEGY_NOT_APPROVED"),
    ({"approval_status": "REVOKED"}, "STRATEGY_NOT_APPROVED"),
    ({"permitted_instruments": (LINEAR,)}, "STRATEGY_INSTRUMENT_MISMATCH"),
    ({"approval_environments": ("LIVE",)}, "STRATEGY_ENVIRONMENT_MISMATCH"),
])
def test_unused_invalid_allowed_rejects_whole_selection(offender, code):
    good = descriptor()
    bad_reference = StrategyReference("fixture-other", "v1")
    entries = (good,) if offender is None else (good, descriptor(reference=bad_reference, **offender))
    rejected(code, lambda: require(build_strategy_catalog(entries),
        allowed=(good.reference, bad_reference), selected=(good.reference,)))


def test_require_same_symbol_product_and_environment_separation():
    for granted, denied in ((SPOT, LINEAR), (LINEAR, SPOT)):
        catalog = build_strategy_catalog((descriptor(permitted_instruments=(granted,)),))
        assert require(catalog, instrument=granted).instrument is granted
        rejected("STRATEGY_INSTRUMENT_MISMATCH",
                 lambda catalog=catalog, denied=denied: require(catalog, instrument=denied))
    demo = build_strategy_catalog((descriptor(),))
    rejected("STRATEGY_ENVIRONMENT_MISMATCH", lambda: require(demo, environment="LIVE"))
    both = build_strategy_catalog((descriptor(permitted_instruments=(SPOT, LINEAR),
                                              approval_environments=("DEMO", "LIVE")),))
    for key in (SPOT, LINEAR):
        for environment in ("DEMO", "LIVE"):
            assert require(both, instrument=key, environment=environment).instrument is key


def test_require_failure_order_is_deterministic():
    catalog = build_strategy_catalog((descriptor(),))
    rejected(INVALID, lambda: require(changed(catalog, schema_version=True),
        expected_catalog_revision="bad", instrument=Unsafe(), environment=Unsafe()))
    rejected(MISMATCH, lambda: require(catalog, expected_catalog_revision="sha256:" + "0" * 64,
        instrument=Unsafe(), allowed=()))
    rejected(INVALID, lambda: require(build_strategy_catalog(()), instrument=Unsafe(), allowed=()))
    rejected(INVALID, lambda: require(catalog, allowed=(), selected=(Unsafe(),)))
    rejected(INVALID, lambda: require(catalog, allowed=(descriptor().reference,) * 2, selected=()))
    rejected(INVALID, lambda: require(catalog, selected=(descriptor().reference,) * 2))
    # Entire reference validation precedes subset/resolution; exact unknown rev is unknown.
    rejected(INVALID, lambda: require(catalog, allowed=(StrategyReference("unknown", "v1"),
                                                       forged(StrategyReference))))
    rejected("UNKNOWN_STRATEGY_REFERENCE", lambda: require(catalog,
        allowed=(StrategyReference("fixture-trend", "v2"),)))
    wrong_environment = descriptor(approval_environments=("LIVE",), permitted_instruments=(LINEAR,))
    revoked = descriptor(reference=StrategyReference("fixture-other", "v1"),
                         approval_status="REVOKED")
    mixed = build_strategy_catalog((wrong_environment, revoked))
    for entries, expected in (((wrong_environment, revoked), "STRATEGY_ENVIRONMENT_MISMATCH"),
                              ((revoked, wrong_environment), "STRATEGY_NOT_APPROVED")):
        rejected(expected, lambda entries=entries: require(
            mixed, allowed=tuple(e.reference for e in entries)))
        rejected(expected, lambda entries=entries: selection(entries, catalog_revision=mixed.revision))
    # Existence is checked per entry, not as a separate global pre-resolution pass.
    rejected("STRATEGY_ENVIRONMENT_MISMATCH", lambda: require(mixed,
        allowed=(wrong_environment.reference, StrategyReference("unknown", "v1"))))
    rejected("UNKNOWN_STRATEGY_REFERENCE", lambda: require(mixed,
        allowed=(StrategyReference("unknown", "v1"), wrong_environment.reference)))


def test_old_catalog_and_old_revision_are_only_static_consistency():
    entry = descriptor()
    old = build_strategy_catalog((entry,))
    assert require(old, expected_catalog_revision=old.revision).allowed == (entry,)
    for status in ("REVOKED", "DEPRECATED"):
        current = build_strategy_catalog((replace(entry, approval_status=status),))
        rejected("STRATEGY_NOT_APPROVED", lambda current=current: require(current))
        rejected(MISMATCH, lambda current=current: require(
            current, expected_catalog_revision=old.revision))
    removed = build_strategy_catalog((descriptor(reference=StrategyReference("fixture-other", "v1")),))
    rejected("UNKNOWN_STRATEGY_REFERENCE", lambda: require(removed))
    # No currently authoritative generation is available to this static pure boundary.
    assert require(old, expected_catalog_revision=old.revision).allowed == (entry,)


def test_constructible_metadata_result_has_no_authority():
    entry = descriptor()
    direct = StrategyCatalog(1, oracle((entry,)), (entry,))
    result = require(direct)
    assert tuple(vars(result)) == ("catalog_revision", "instrument", "environment", "allowed",
                                   "selected")
    assert result.allowed == (entry,)
    # Direct selection construction proves neither catalog membership nor source authority.
    assert selection((entry,), catalog_revision="sha256:" + "0" * 64).allowed == (entry,)


def test_selection_constructor_intrinsic_subset_and_scope():
    entry = descriptor()
    rejected("STRATEGY_ALLOWLIST_EMPTY", lambda: selection(()))
    rejected(INVALID, lambda: selection((entry, entry)))
    rejected(INVALID, lambda: selection((entry,), (entry, entry)))
    rejected("STRATEGY_SELECTION_OUTSIDE_ALLOWLIST", lambda: selection((entry,),
        (replace(entry, config_sha256="d" * 64),)))
    rejected("STRATEGY_SELECTION_OUTSIDE_ALLOWLIST", lambda: selection((entry,),
        (descriptor(reference=StrategyReference("fixture-trend", "v2")),)))
    both = descriptor(permitted_instruments=(SPOT, LINEAR), approval_environments=("DEMO", "LIVE"))
    reordered = replace(both, permitted_instruments=(LINEAR, SPOT),
                        approval_environments=("LIVE", "DEMO"))
    assert selection((both,), (reordered,)).selected[0] is reordered
    for changes, code in (({"approval_status": "REVOKED"}, "STRATEGY_NOT_APPROVED"),
                          ({"approval_environments": ("LIVE",)}, "STRATEGY_ENVIRONMENT_MISMATCH"),
                          ({"permitted_instruments": (LINEAR,)}, "STRATEGY_INSTRUMENT_MISMATCH")):
        rejected(code, lambda changes=changes: selection((descriptor(**changes),)))
    # Full payload subset check precedes status; all intrinsic values precede subset check.
    revoked = descriptor(approval_status="REVOKED")
    rejected("STRATEGY_SELECTION_OUTSIDE_ALLOWLIST", lambda: selection((revoked,), (entry,)))
    rejected(INVALID, lambda: selection((revoked,),
        (changed(descriptor(), approval_reference=Unsafe()),)))


@pytest.mark.parametrize("arguments", [
    {"catalog_revision": "bad"}, {"instrument": Unsafe()}, {"environment": Unsafe()},
    {"allowed": []}, {"selected": []}, {"allowed": (forged(StrategyDescriptor),)},
    {"selected": (forged(StrategyDescriptor),)},
])
def test_selection_constructor_rejects_forged_fields(arguments):
    rejected(INVALID, lambda: selection((descriptor(),), **arguments))


def test_selection_and_require_tuple_caps():
    entries = tuple(descriptor(reference=StrategyReference(f"fixture-{i}", "v1"))
                    for i in range(100))
    refs = tuple(e.reference for e in entries)
    catalog = build_strategy_catalog(entries)
    assert len(require(catalog, allowed=refs, selected=refs).selected) == 100
    assert len(selection(entries, entries).selected) == 100
    for field in ("allowed", "selected"):
        rejected(INVALID, lambda field=field: require(catalog, **{field: refs + (Unsafe(),)}))
        rejected(INVALID, lambda field=field: selection(entries, **{field: entries + (Unsafe(),)}))


@pytest.mark.parametrize("value", [Unsafe(), UnsafeType(), [], {},
    forged(StrategyReference), StrategyReference("fixture-trend", Unsafe())])
def test_forged_nested_reference_fields_reject_before_unsafe_hooks(value):
    catalog = build_strategy_catalog((descriptor(),))
    for field in ("allowed", "selected"):
        rejected(INVALID, lambda field=field: require(catalog, **{field: (value,)}))


def test_forged_nested_fields_reject_before_unsafe_hooks():
    catalog = build_strategy_catalog((descriptor(),))
    for cls in (StrategyCatalog, InstrumentKey):
        value = forged(cls)
        if cls is StrategyCatalog:
            rejected(INVALID, lambda value=value: require_strategy_references(value,
                expected_catalog_revision=catalog.revision, allowed=(), selected=(),
                instrument=SPOT, environment="DEMO"))
        else:
            rejected(INVALID, lambda value=value: require(catalog, instrument=value))
    for name in (field.name for field in fields(StrategyDescriptor)):
        values = dict(vars(descriptor()))
        del values[name]
        value = forged(StrategyDescriptor, **values)
        rejected(INVALID, lambda value=value: build_strategy_catalog((value,)))
        rejected(INVALID, lambda value=value: require(changed(catalog, entries=(value,))))
        rejected(INVALID, lambda value=value: selection((value,), catalog_revision=catalog.revision))
    for field in ("venue", "product", "symbol"):
        rejected(INVALID, lambda field=field: require(
            catalog, instrument=changed(SPOT, **{field: Unsafe()})))


def test_exact_types_reject_scalar_record_and_tuple_subclasses():
    class Text(str):
        pass

    class Items(tuple):
        pass

    class Descriptor(StrategyDescriptor):
        pass

    entry = descriptor()
    rejected(INVALID, lambda: descriptor(reference=StrategyReference(Text("fixture-trend"), "v1")))
    rejected(INVALID, lambda: descriptor(permitted_instruments=Items((SPOT,))))
    rejected(INVALID, lambda: descriptor(approval_environments=(Text("DEMO"),)))
    rejected(INVALID, lambda: build_strategy_catalog(Items((entry,))))
    rejected(INVALID, lambda: build_strategy_catalog((forged(Descriptor, **vars(entry)),)))
    catalog = build_strategy_catalog((entry,))
    rejected(INVALID, lambda: require(catalog, allowed=Items((entry.reference,))))
    rejected(INVALID, lambda: require(catalog, expected_catalog_revision=Text(catalog.revision)))


def test_import_and_calls_are_pure(monkeypatch):
    path = Path(subject.__file__)
    source = path.read_text(encoding="utf-8")
    imports = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            imports.append(("." * node.level) + node.module)
    assert set(imports) <= {"hashlib", "json", "re", "dataclasses", "typing", ".modes",
                            "multi_exchange_trader.trading_product"}
    name = "multi_exchange_trader.execution._strategy_catalog_purity_fixture"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)

    def forbidden(*args, **kwargs):
        raise AssertionError("forbidden external operation")

    class NoClock(datetime.datetime):
        now = classmethod(forbidden)
        utcnow = classmethod(forbidden)
        today = classmethod(forbidden)

    # The ordinary loader may load Python source/bytecode; application file access may not.
    with monkeypatch.context() as guard:
        guard.setitem(sys.modules, name, module)
        for owner, attr in ((builtins, "open"), (io, "open"), (Path, "open"),
                            (Path, "read_text"), (Path, "read_bytes"), (os, "getenv"),
                            (socket, "socket"), (socket, "create_connection"),
                            (time, "time"), (time, "monotonic"), (time, "perf_counter")):
            guard.setattr(owner, attr, forbidden)
        guard.setattr(datetime, "datetime", NoClock)
        spec.loader.exec_module(module)
        entry = module.StrategyDescriptor(**vars(descriptor()))
        catalog = module.build_strategy_catalog((entry,))
        result = module.require_strategy_references(catalog,
            expected_catalog_revision=catalog.revision, allowed=(entry.reference,), selected=(),
            instrument=SPOT, environment="DEMO")
    assert result.allowed == (entry,) and result.selected == ()
