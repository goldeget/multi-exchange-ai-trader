"""Pure declared-universe catalogs; no issuer or execution authority."""

import hashlib
import re
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from typing import NoReturn

from multi_exchange_trader.market_data.instrument_definition import (
    PARSER_REVISION,
    CatalogRejected,
    InstrumentObservation,
    SourceEnvironment,
    _canonical_json_bytes,
    _key_payload,
    _utc_datetime,
    validate_instrument_observation,
)
from multi_exchange_trader.trading_product import InstrumentKey, TradingProduct

CATALOG_SCHEMA_VERSION = 1
_DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)


def _reject(code: str) -> NoReturn:
    raise CatalogRejected(code) from None


@dataclass(frozen=True)
class InstrumentCatalog:
    schema_version: int
    revision: str
    declared_universe: tuple[InstrumentKey, ...]
    observations: tuple[InstrumentObservation, ...]
    max_age: timedelta
    expires_at: datetime

    def __post_init__(self) -> None:
        _validate_catalog(self)
        object.__setattr__(self, "expires_at", _utc_datetime(self.expires_at))

    @property
    def complete_for_declared_universe(self) -> bool:
        """Revalidate static integrity, without proving freshness or provenance."""
        _validate_catalog(self)
        return True


def _keys(
    keys: tuple[InstrumentKey, ...], *, code: str, canonical: bool
) -> tuple[tuple[str, str, str], ...]:
    if type(keys) is not tuple:
        _reject(code)
    if len(keys) > 100:
        _reject("CATALOG_RESOURCE_LIMIT")
    if not keys:
        _reject(
            "CATALOG_SELECTION_INVALID"
            if code == "CATALOG_SELECTION_INVALID"
            else "CATALOG_COVERAGE_INVALID"
        )
    identities = []
    for key in keys:
        try:
            _key_payload(key)
        except CatalogRejected:
            _reject(code)
        identities.append((key.venue, key.product, key.symbol))
    result = tuple(identities)
    if len(set(result)) != len(result) or (canonical and result != tuple(sorted(result))):
        _reject(
            "CATALOG_SELECTION_INVALID"
            if code == "CATALOG_SELECTION_INVALID"
            else "CATALOG_COVERAGE_INVALID"
        )
    return result


def _observations(
    observations: tuple[InstrumentObservation, ...],
    declared_universe: tuple[InstrumentKey, ...],
    *,
    canonical: bool,
) -> None:
    identities = _keys(declared_universe, code="CATALOG_INPUT_INVALID", canonical=canonical)
    if type(observations) is not tuple:
        _reject("CATALOG_INPUT_INVALID")
    if len(observations) > 100:
        _reject("CATALOG_RESOURCE_LIMIT")
    if not observations:
        _reject("CATALOG_COVERAGE_INVALID")
    for observation in observations:
        validate_instrument_observation(observation)
    observed = _keys(
        tuple(item.key for item in observations), code="CATALOG_INPUT_INVALID", canonical=canonical
    )
    if set(observed) != set(identities):
        _reject("CATALOG_COVERAGE_INVALID")
    first = observations[0].source
    for item in observations[1:]:
        if (item.source.source_id, item.source.source_environment, item.source.endpoint) != (
            first.source_id,
            first.source_environment,
            first.endpoint,
        ):
            _reject("CATALOG_SOURCE_MISMATCH")


def _expiry(observations: tuple[InstrumentObservation, ...], max_age: timedelta) -> datetime:
    if type(max_age) is not timedelta or max_age <= timedelta(0):
        _reject("CATALOG_INPUT_INVALID")
    earliest = min(
        time
        for item in observations
        for time in (_utc_datetime(item.response_time), _utc_datetime(item.source.received_at))
    )
    try:
        return earliest + max_age
    except OverflowError:
        _reject("CATALOG_TIME_INVALID")


def _revision(
    declared_universe: tuple[InstrumentKey, ...],
    observations: tuple[InstrumentObservation, ...],
    max_age: timedelta,
) -> str:
    source = observations[0].source
    payload = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "parser_revision": PARSER_REVISION,
        "source_rule_revisions": sorted({item.source_rule_revision for item in observations}),
        "source_environment": source.source_environment,
        "endpoint": source.endpoint,
        "declared_universe": [_key_payload(key) for key in declared_universe],
        "specifications": [
            {"key": _key_payload(item.key), "specification_revision": item.specification_revision}
            for item in observations
        ],
        "max_age_microseconds": (
            (max_age.days * 86400 + max_age.seconds) * 1000000 + max_age.microseconds
        ),
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _validate_catalog(catalog: InstrumentCatalog) -> None:
    if type(catalog) is not InstrumentCatalog:
        _reject("CATALOG_INPUT_INVALID")
    try:
        for item in fields(InstrumentCatalog):
            object.__getattribute__(catalog, item.name)
    except AttributeError:
        _reject("CATALOG_INPUT_INVALID")
    if type(catalog.schema_version) is not int or catalog.schema_version != CATALOG_SCHEMA_VERSION:
        _reject("CATALOG_INPUT_INVALID")
    _observations(catalog.observations, catalog.declared_universe, canonical=True)
    expiry = _expiry(catalog.observations, catalog.max_age)
    if _utc_datetime(catalog.expires_at) != expiry:
        _reject("CATALOG_TIME_INVALID")
    if (
        type(catalog.revision) is not str
        or _DIGEST.fullmatch(catalog.revision) is None
        or catalog.revision
        != _revision(catalog.declared_universe, catalog.observations, catalog.max_age)
    ):
        _reject("CATALOG_REVISION_MISMATCH")


def _freshness(catalog: InstrumentCatalog, now: datetime) -> None:
    current = _utc_datetime(now)
    try:
        cutoff = current - catalog.max_age
    except OverflowError:
        _reject("CATALOG_TIME_INVALID")
    times = tuple(
        (_utc_datetime(item.response_time), _utc_datetime(item.source.received_at))
        for item in catalog.observations
    )
    for response, received in times:
        if response > received or received > current:
            _reject("CATALOG_TIME_INVALID")
    if any(response <= cutoff or received <= cutoff for response, received in times):
        _reject("CATALOG_EXPIRED")
    if current >= _utc_datetime(catalog.expires_at):
        _reject("CATALOG_EXPIRED")


def build_instrument_catalog(
    observations: tuple[InstrumentObservation, ...],
    *,
    declared_universe: tuple[InstrumentKey, ...],
    now: datetime,
    max_age: timedelta,
) -> InstrumentCatalog:
    _utc_datetime(now)
    _observations(observations, declared_universe, canonical=False)
    expiry = _expiry(observations, max_age)
    ordered_keys = tuple(
        sorted(declared_universe, key=lambda key: (key.venue, key.product, key.symbol))
    )
    ordered = tuple(
        sorted(observations, key=lambda item: (item.key.venue, item.key.product, item.key.symbol))
    )
    catalog = InstrumentCatalog(
        CATALOG_SCHEMA_VERSION,
        _revision(ordered_keys, ordered, max_age),
        ordered_keys,
        ordered,
        max_age,
        expiry,
    )
    _freshness(catalog, now)
    return catalog


def require_catalog_selection(
    catalog: InstrumentCatalog,
    *,
    allowed: tuple[InstrumentKey, ...],
    selected: tuple[InstrumentKey, ...],
    expected_product: TradingProduct,
    expected_source_environment: SourceEnvironment,
    expected_revision: str,
    now: datetime,
) -> tuple[InstrumentObservation, ...]:
    _validate_catalog(catalog)
    _freshness(catalog, now)
    if type(expected_product) is not str or expected_product not in ("SPOT", "LINEAR"):
        _reject("CATALOG_SELECTION_INVALID")
    if (
        type(expected_source_environment) is not str
        or expected_source_environment not in ("PUBLIC_MAINNET", "PUBLIC_TESTNET", "FIXTURE")
        or expected_source_environment != catalog.observations[0].source.source_environment
    ):
        _reject("CATALOG_SOURCE_MISMATCH")
    if (
        type(expected_revision) is not str
        or _DIGEST.fullmatch(expected_revision) is None
        or expected_revision != catalog.revision
    ):
        _reject("CATALOG_REVISION_MISMATCH")
    allowed_ids = _keys(allowed, code="CATALOG_SELECTION_INVALID", canonical=False)
    selected_ids = _keys(selected, code="CATALOG_SELECTION_INVALID", canonical=False)
    declared = {(key.venue, key.product, key.symbol) for key in catalog.declared_universe}
    if not set(selected_ids) <= set(allowed_ids) <= declared or any(
        key.product != expected_product for key in allowed + selected
    ):
        _reject("CATALOG_SELECTION_INVALID")
    by_key = {
        (item.key.venue, item.key.product, item.key.symbol): item for item in catalog.observations
    }
    return tuple(by_key[key] for key in selected_ids)
