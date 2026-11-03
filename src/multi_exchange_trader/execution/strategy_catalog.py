"""Pure, caller-constructible exact strategy metadata; never execution authority."""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal, cast

from multi_exchange_trader.trading_product import InstrumentKey

from .modes import StrategyReference

ApprovalStatus = Literal["APPROVED", "DEPRECATED", "REVOKED"]
ApprovalEnvironment = Literal["DEMO", "LIVE"]

_INVALID = "STRATEGY_CATALOG_INVALID_INPUT"
_REVISION_MISMATCH = "STRATEGY_CATALOG_REVISION_MISMATCH"
_CODES = frozenset((
    _INVALID, "DUPLICATE_STRATEGY_REFERENCE", "CONFLICTING_STRATEGY_REFERENCE",
    _REVISION_MISMATCH, "STRATEGY_CATALOG_EMPTY", "STRATEGY_ALLOWLIST_EMPTY",
    "STRATEGY_SELECTION_OUTSIDE_ALLOWLIST", "UNKNOWN_STRATEGY_REFERENCE",
    "STRATEGY_NOT_APPROVED", "STRATEGY_ENVIRONMENT_MISMATCH", "STRATEGY_INSTRUMENT_MISMATCH",
))
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}")
_HASH = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True)
class StrategyDescriptor:
    reference: StrategyReference
    implementation_sha256: str
    config_sha256: str
    config_schema_version: int
    engine_contract_version: str
    permitted_instruments: tuple[InstrumentKey, ...]
    approval_reference: str
    approval_source_reference: str
    approval_source_sha256: str
    approval_environments: tuple[ApprovalEnvironment, ...]
    approval_status: ApprovalStatus

    def __post_init__(self) -> None:
        _validate_descriptor(self)


@dataclass(frozen=True)
class StrategyCatalog:
    schema_version: int
    revision: str
    entries: tuple[StrategyDescriptor, ...]

    def __post_init__(self) -> None:
        _validate_catalog(self)


@dataclass(frozen=True)
class StrategyCatalogSelection:
    catalog_revision: str
    instrument: InstrumentKey
    environment: ApprovalEnvironment
    allowed: tuple[StrategyDescriptor, ...]
    selected: tuple[StrategyDescriptor, ...]

    def __post_init__(self) -> None:
        _validate_selection(self)


class StrategyCatalogRejected(ValueError):
    code: str

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _CODES:
            raise ValueError("INVALID_STRATEGY_CATALOG_REJECTION_CODE") from None
        self.code = code
        super().__init__(code)


def _field(value: object, name: str) -> object:
    try:
        return cast(object, object.__getattribute__(value, name))
    except AttributeError:
        raise StrategyCatalogRejected(_INVALID) from None


def _identifier(value: object) -> None:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise StrategyCatalogRejected(_INVALID)


def _hash(value: object) -> None:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise StrategyCatalogRejected(_INVALID)


def _revision(value: object) -> None:
    if type(value) is not str or _REVISION.fullmatch(value) is None:
        raise StrategyCatalogRejected(_INVALID)


def _tuple(value: object, maximum: int = 100) -> None:
    if type(value) is not tuple or len(value) > maximum:
        raise StrategyCatalogRejected(_INVALID)


def _reference(value: object) -> tuple[str, str]:
    if type(value) is not StrategyReference:
        raise StrategyCatalogRejected(_INVALID)
    strategy_id = _field(value, "strategy_id")
    revision = _field(value, "revision")
    _identifier(strategy_id)
    _identifier(revision)
    if cast(str, revision).casefold() == "latest":
        raise StrategyCatalogRejected(_INVALID)
    return cast(str, strategy_id), cast(str, revision)


def _instrument(value: object) -> tuple[str, str, str]:
    if type(value) is not InstrumentKey:
        raise StrategyCatalogRejected(_INVALID)
    key = value
    # Delegate the shared symbol/product envelope; do not duplicate its rules.
    try:
        InstrumentKey(venue=key.venue, product=key.product, symbol=key.symbol)
    except (ValueError, AttributeError):
        raise StrategyCatalogRejected(_INVALID) from None
    return key.venue, key.product, key.symbol


def _environment(value: object) -> None:
    if type(value) is not str or value not in ("DEMO", "LIVE"):
        raise StrategyCatalogRejected(_INVALID)


def _validate_descriptor(value: object) -> None:
    if type(value) is not StrategyDescriptor:
        raise StrategyCatalogRejected(_INVALID)
    _reference(_field(value, "reference"))
    _hash(_field(value, "implementation_sha256"))
    _hash(_field(value, "config_sha256"))
    schema = _field(value, "config_schema_version")
    if type(schema) is not int or not 1 <= schema <= 2147483647:
        raise StrategyCatalogRejected(_INVALID)
    _identifier(_field(value, "engine_contract_version"))
    instruments = _field(value, "permitted_instruments")
    _tuple(instruments)
    keys = [_instrument(key) for key in cast(tuple[object, ...], instruments)]
    if len(set(keys)) != len(keys) or not keys:
        raise StrategyCatalogRejected(_INVALID)
    _identifier(_field(value, "approval_reference"))
    _identifier(_field(value, "approval_source_reference"))
    _hash(_field(value, "approval_source_sha256"))
    environments = _field(value, "approval_environments")
    _tuple(environments, 2)
    for environment in cast(tuple[object, ...], environments):
        _environment(environment)
    valid_environments = cast(tuple[str, ...], environments)
    if len(set(valid_environments)) != len(valid_environments) or not valid_environments:
        raise StrategyCatalogRejected(_INVALID)
    status = _field(value, "approval_status")
    if type(status) is not str or status not in ("APPROVED", "DEPRECATED", "REVOKED"):
        raise StrategyCatalogRejected(_INVALID)


def _descriptor_payload(entry: StrategyDescriptor) -> dict[str, object]:
    """Only called after intrinsic validation of every participating descriptor."""
    return {
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
    }


def _digest(entries: tuple[StrategyDescriptor, ...]) -> str:
    payload = {
        "schema_version": 1,
        "entries": [_descriptor_payload(entry) for entry in sorted(
            entries, key=lambda entry: (entry.reference.strategy_id, entry.reference.revision))],
    }
    data = json.dumps(payload, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _entries(value: object) -> tuple[StrategyDescriptor, ...]:
    _tuple(value)
    entries = cast(tuple[StrategyDescriptor, ...], value)
    # Check all collection shapes before any nested key traversal or canonicalization.
    for entry in entries:
        if type(entry) is not StrategyDescriptor:
            raise StrategyCatalogRejected(_INVALID)
        _tuple(_field(entry, "permitted_instruments"))
    for entry in entries:
        _validate_descriptor(entry)
    seen: dict[tuple[str, str], dict[str, object]] = {}
    for entry in entries:
        reference = (entry.reference.strategy_id, entry.reference.revision)
        payload = _descriptor_payload(entry)
        if reference in seen:
            code = ("DUPLICATE_STRATEGY_REFERENCE" if seen[reference] == payload
                    else "CONFLICTING_STRATEGY_REFERENCE")
            raise StrategyCatalogRejected(code)
        seen[reference] = payload
    return entries


def _validate_catalog(value: object) -> None:
    if type(value) is not StrategyCatalog:
        raise StrategyCatalogRejected(_INVALID)
    schema = _field(value, "schema_version")
    if type(schema) is not int or schema != 1:
        raise StrategyCatalogRejected(_INVALID)
    revision = _field(value, "revision")
    _revision(revision)
    entries = _entries(_field(value, "entries"))
    if revision != _digest(entries):
        raise StrategyCatalogRejected(_REVISION_MISMATCH)


def build_strategy_catalog(entries: tuple[StrategyDescriptor, ...]) -> StrategyCatalog:
    """Validate all supplied metadata, preserving its original objects and ordering."""
    validated = _entries(entries)
    return StrategyCatalog(1, _digest(validated), validated)


def _unique_references(references: tuple[tuple[str, str], ...]) -> None:
    if len(set(references)) != len(references):
        raise StrategyCatalogRejected(_INVALID)


def _require_scope(
    entry: StrategyDescriptor, instrument: tuple[str, str, str], environment: str,
) -> None:
    if entry.approval_status != "APPROVED":
        raise StrategyCatalogRejected("STRATEGY_NOT_APPROVED")
    if environment not in entry.approval_environments:
        raise StrategyCatalogRejected("STRATEGY_ENVIRONMENT_MISMATCH")
    if instrument not in tuple((key.venue, key.product, key.symbol)
                               for key in entry.permitted_instruments):
        raise StrategyCatalogRejected("STRATEGY_INSTRUMENT_MISMATCH")


def _validate_selection(value: object) -> None:
    if type(value) is not StrategyCatalogSelection:
        raise StrategyCatalogRejected(_INVALID)
    _revision(_field(value, "catalog_revision"))
    instrument = _instrument(_field(value, "instrument"))
    environment = _field(value, "environment")
    _environment(environment)
    allowed_value = _field(value, "allowed")
    selected_value = _field(value, "selected")
    _tuple(allowed_value)
    _tuple(selected_value)
    allowed = cast(tuple[StrategyDescriptor, ...], allowed_value)
    selected = cast(tuple[StrategyDescriptor, ...], selected_value)
    for entry in allowed:
        _validate_descriptor(entry)
    for entry in selected:
        _validate_descriptor(entry)
    allowed_keys = tuple((entry.reference.strategy_id, entry.reference.revision) for entry in allowed)
    selected_keys = tuple((entry.reference.strategy_id, entry.reference.revision) for entry in selected)
    _unique_references(allowed_keys)
    _unique_references(selected_keys)
    if not allowed:
        raise StrategyCatalogRejected("STRATEGY_ALLOWLIST_EMPTY")
    allowed_payloads = dict(zip(allowed_keys, (_descriptor_payload(entry) for entry in allowed)))
    for reference, entry in zip(selected_keys, selected):
        if reference not in allowed_payloads or allowed_payloads[reference] != _descriptor_payload(entry):
            raise StrategyCatalogRejected("STRATEGY_SELECTION_OUTSIDE_ALLOWLIST")
    for entry in allowed:
        _require_scope(entry, instrument, cast(str, environment))


def require_strategy_references(
    catalog: StrategyCatalog, *, expected_catalog_revision: str,
    allowed: tuple[StrategyReference, ...], selected: tuple[StrategyReference, ...],
    instrument: InstrumentKey, environment: ApprovalEnvironment,
) -> StrategyCatalogSelection:
    """Check exact static consistency; old catalog plus its old revision can still pass.

    No source authenticity, authoritative current generation, artifact verification,
    revocation after return, or execution admission is established by this value result.
    """
    _validate_catalog(catalog)
    _revision(expected_catalog_revision)
    if expected_catalog_revision != catalog.revision:
        raise StrategyCatalogRejected(_REVISION_MISMATCH)
    instrument_key = _instrument(instrument)
    _environment(environment)
    if not catalog.entries:
        raise StrategyCatalogRejected("STRATEGY_CATALOG_EMPTY")
    _tuple(allowed)
    _tuple(selected)
    allowed_keys = tuple(_reference(reference) for reference in allowed)
    selected_keys = tuple(_reference(reference) for reference in selected)
    _unique_references(allowed_keys)
    _unique_references(selected_keys)
    if not allowed_keys:
        raise StrategyCatalogRejected("STRATEGY_ALLOWLIST_EMPTY")
    allowed_set = set(allowed_keys)
    if any(reference not in allowed_set for reference in selected_keys):
        raise StrategyCatalogRejected("STRATEGY_SELECTION_OUTSIDE_ALLOWLIST")
    lookup = {(entry.reference.strategy_id, entry.reference.revision): entry
              for entry in catalog.entries}
    resolved: list[StrategyDescriptor] = []
    for reference in allowed_keys:
        if reference not in lookup:
            raise StrategyCatalogRejected("UNKNOWN_STRATEGY_REFERENCE")
        entry = lookup[reference]
        _require_scope(entry, instrument_key, environment)
        resolved.append(entry)
    return StrategyCatalogSelection(catalog.revision, instrument, environment, tuple(resolved),
                                    tuple(lookup[reference] for reference in selected_keys))
