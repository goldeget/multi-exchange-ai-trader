"""Pure supported-field projection, without source or execution authority.

This adapter deliberately depends on the frozen definition's private canonical,
row decoder, parser, spec validator/payload and revision helpers. Their positional
signatures and known fixture semantics are pinned by focused regression tests;
changes require coordinated review, never a fallback parser or runtime file read.
"""

from dataclasses import dataclass, field, fields
from typing import cast

from multi_exchange_trader.market_data.instrument_definition import (
    LINEAR_SOURCE_RULE_REVISION,
    PARSER_REVISION,
    SPOT_SOURCE_RULE_REVISION,
    CatalogRejected,
    LinearInstrumentSpec,
    SpotInstrumentSpec,
    _canonical_json_bytes,
    _decode_row,
    _key_payload,
    _parse_spec,
    _spec_payload,
    _spec_revision,
    _validate_spec,
)
from multi_exchange_trader.trading_product import InstrumentKey, bybit_native_instrument_id


@dataclass(frozen=True)
class SupportedInstrumentProjection:
    key: InstrumentKey
    native_instrument_id: str
    parser_revision: str
    source_rule_revision: str
    specification_revision: str
    raw_row_canonical: bytes = field(repr=False)
    spec: LinearInstrumentSpec | SpotInstrumentSpec

    def __post_init__(self) -> None:
        validate_supported_instrument_projection(self)


def _derive(
    row: object, expected: InstrumentKey
) -> tuple[bytes, LinearInstrumentSpec | SpotInstrumentSpec, str]:
    """Use one nonrecursive derivation for the builder and DTO validation."""
    _key_payload(expected)
    if type(row) is not dict:
        raise CatalogRejected("CATALOG_INPUT_INVALID")
    exact_row = cast(dict[str, object], row)
    symbol = exact_row.get("symbol")
    if type(symbol) is not str:
        raise CatalogRejected("CATALOG_INPUT_INVALID")
    if symbol != expected.symbol:
        raise CatalogRejected("CATALOG_SCOPE_MISMATCH")
    raw = _canonical_json_bytes(exact_row)
    spec = _parse_spec(exact_row, expected)
    return raw, spec, _spec_revision(expected, spec)


def project_supported_instrument_row(
    row: object, *, expected: InstrumentKey
) -> SupportedInstrumentProjection:
    """Project a bounded exact row; this does not prove origin or freshness."""
    raw, spec, revision = _derive(row, expected)
    rule = (
        LINEAR_SOURCE_RULE_REVISION if expected.product == "LINEAR" else SPOT_SOURCE_RULE_REVISION
    )
    return SupportedInstrumentProjection(
        expected, bybit_native_instrument_id(expected), PARSER_REVISION, rule, revision, raw, spec
    )


def validate_supported_instrument_projection(value: object) -> None:
    """Recheck exact fields and bounded raw/spec integrity without source claims."""
    code = "CATALOG_OBSERVATION_INVALID"
    if type(value) is not SupportedInstrumentProjection:
        raise CatalogRejected(code)
    try:
        # Enumerate the trusted class, never caller-controlled instance metadata.
        parts = {
            item.name: object.__getattribute__(value, item.name)
            for item in fields(SupportedInstrumentProjection)
        }
    except AttributeError:
        parts = {}
    if not parts:
        raise CatalogRejected(code)
    failure: str | None = None
    try:
        key = cast(InstrumentKey, parts["key"])
        _key_payload(key)
        rule = LINEAR_SOURCE_RULE_REVISION if key.product == "LINEAR" else SPOT_SOURCE_RULE_REVISION
        for name in (
            "native_instrument_id",
            "parser_revision",
            "source_rule_revision",
            "specification_revision",
        ):
            if type(parts[name]) is not str:
                raise CatalogRejected(code)
        if (
            parts["native_instrument_id"] != bybit_native_instrument_id(key)
            or parts["parser_revision"] != PARSER_REVISION
            or parts["source_rule_revision"] != rule
        ):
            raise CatalogRejected(code)
        spec = cast(LinearInstrumentSpec | SpotInstrumentSpec, parts["spec"])
        _validate_spec(spec)
        expected_type = LinearInstrumentSpec if key.product == "LINEAR" else SpotInstrumentSpec
        if type(spec) is not expected_type:
            raise CatalogRejected(code)
        raw = parts["raw_row_canonical"]
        if type(raw) is not bytes:
            raise CatalogRejected(code)
        if len(raw) > 65536:
            raise CatalogRejected("CATALOG_RESOURCE_LIMIT")
        row = _decode_row(raw)
        _, derived, revision = _derive(row, key)
        if parts["specification_revision"] != revision or _canonical_json_bytes(
            _spec_payload(spec)
        ) != _canonical_json_bytes(_spec_payload(derived)):
            raise CatalogRejected(code)
    except CatalogRejected as error:
        failure = "CATALOG_RESOURCE_LIMIT" if error.code == "CATALOG_RESOURCE_LIMIT" else code
    if failure is not None:
        raise CatalogRejected(failure)
