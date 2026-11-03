"""Closed source values, complete captured tables and canonical source identities.

Values preserve supplied evidence; they cannot qualify its issuer, freshness,
venue completeness, or model eligibility. No I/O or source authority lives here.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from itertools import pairwise
from math import gcd
from typing import Literal, cast
from urllib.parse import urlsplit

from multi_exchange_trader.account.instrument_envelope import HttpsOrigin
from multi_exchange_trader.account.risk_limit_envelope import (
    BybitRiskLimitEnvelopeReadV1,
    JsonNumberTokenV1,
    _count_v1,
    _integer_token_v1,
    _number_spelling_v1,
    _origin_v1,
    _query_v1,
    _reject_v1,
    _text_v1,
    _validated_risk_body_v1,
)
from multi_exchange_trader.execution import linear_risk_evidence_v2 as e
from multi_exchange_trader.execution import linear_risk_v2 as r
from multi_exchange_trader.execution import risk_domain_v2 as d
from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.execution.risk_v2_contracts import (
    FixedLimitEnvelopeV2,
    InstrumentPinV2,
    RiskObservationRefV2,
    RiskSourceEnvironmentV2,
    _canonical_sha256_v2,
    _decimal_v2,
    _digest_v2,
    _fields_v2,
    _identifier_v2,
    _key_payload_v2,
    _literal_v2,
    _observation_payload_v2,
    _pin_payload_v2,
    _time_text_v2,
    _utc_v2,
    envelope_sha256_v2,
)
from multi_exchange_trader.execution.risk_v2_math import (
    _compare,
    _fraction,
    decimal_fraction_v2,
    decimal_text_v2,
    finite_decimal_v2,
)
from multi_exchange_trader.market_data.instrument_catalog import InstrumentCatalog
from multi_exchange_trader.market_data.instrument_definition import (
    CatalogRejected,
    InstrumentObservation,
    LinearInstrumentSpec,
    validate_instrument_observation,
)
from multi_exchange_trader.trading_product import InstrumentKey

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
SOURCE_RULE_REVISION = "BYBIT_LINEAR_RISK_LIMIT_TABLE_V1"


class _ValueV1:
    def __post_init__(self) -> None:
        _validate_value_v1(self)
        if type(self) is BybitModelSourceRefV1:
            for name in ("request_started_at", "received_at"):
                object.__setattr__(self, name, _utc_v2(object.__getattribute__(self, name)))
        elif type(self) is BybitFeeDocumentRefV1:
            object.__setattr__(self, "captured_at", _utc_v2(self.captured_at))


@dataclass(frozen=True)
class BybitModelSourceRefV1(_ValueV1):
    observation_id: str
    source_id: str
    source_generation: str
    account_scope: ProtectionScope | None
    source_environment: RiskSourceEnvironmentV2
    origin: HttpsOrigin
    path: str
    query: tuple[tuple[str, str], ...]
    request_started_at: datetime
    received_at: datetime
    envelope_time_ms: int | None
    raw_body_sha256: str
    page_ordinal: int
    accepted_attempt: int


@dataclass(frozen=True)
class BybitModelFieldRefV1(_ValueV1):
    source: BybitModelSourceRefV1
    json_pointer: str
    wire_kind: Literal["STRING", "NUMBER", "INTEGER", "BOOLEAN"]
    wire_spelling: str


@dataclass(frozen=True)
class BybitDecimalFieldV1(_ValueV1):
    value: Decimal
    reference: BybitModelFieldRefV1


@dataclass(frozen=True)
class BybitFeeDocumentRefV1(_ValueV1):
    document_url: str
    document_updated_at_text: str
    captured_at: datetime
    raw_document_sha256: str
    exact_locator: str
    displayed_rate_spelling: str
    displayed_unit: Literal["PERCENT", "FRACTION"]
    account_region_profile_sha256: str
    applicability_revision: str


@dataclass(frozen=True)
class BybitNonVipFeeBasisV1(_ValueV1):
    value: Decimal
    reference: BybitModelFieldRefV1 | BybitFeeDocumentRefV1


@dataclass(frozen=True)
class BybitUtaModeV1(_ValueV1):
    source: BybitModelSourceRefV1
    unified_margin_status: Literal[5, 6]
    margin_mode: Literal["ISOLATED_MARGIN"]
    updated_time_ms: int


@dataclass(frozen=True)
class BybitTradeBehaviourV1(_ValueV1):
    source: BybitModelSourceRefV1
    lpa_perp: bool
    futures_mnt_fee_deduction: bool
    delta_enable: bool | None


@dataclass(frozen=True)
class BybitFlatLeverageV1(_ValueV1):
    source: BybitModelSourceRefV1
    symbol: str
    position_idx: Literal[0]
    leverage: BybitDecimalFieldV1
    read_path: Literal["/v5/position/symbol-info", "/v5/position/list"]


@dataclass(frozen=True)
class BybitLinearFeeInputsV1(_ValueV1):
    account_taker_rate: BybitDecimalFieldV1
    account_maker_rate: BybitDecimalFieldV1
    closing_order_reserve_nonvip_rate: BybitNonVipFeeBasisV1
    fee_currency: Literal["USDT"]
    behaviour: BybitTradeBehaviourV1
    nonvip_applicability_revision: str


@dataclass(frozen=True)
class BybitUsdtWalletV1(_ValueV1):
    source: BybitModelSourceRefV1
    wallet_balance: BybitDecimalFieldV1
    equity: BybitDecimalFieldV1
    total_position_im: BybitDecimalFieldV1
    total_order_im: BybitDecimalFieldV1
    locked: BybitDecimalFieldV1
    bonus: BybitDecimalFieldV1
    borrow_amount: BybitDecimalFieldV1
    spot_borrow: BybitDecimalFieldV1
    accrued_interest: BybitDecimalFieldV1
    unrealised_pnl: BybitDecimalFieldV1


@dataclass(frozen=True)
class BybitHeldPositionV1(_ValueV1):
    source: BybitModelSourceRefV1
    instrument_pin: InstrumentPinV2
    position_idx: int
    side: str
    size: BybitDecimalFieldV1
    avg_price: BybitDecimalFieldV1
    mark_price: BybitDecimalFieldV1
    leverage: BybitDecimalFieldV1
    position_im: BybitDecimalFieldV1
    position_im_by_mp: BybitDecimalFieldV1
    position_mm: BybitDecimalFieldV1
    position_mm_by_mp: BybitDecimalFieldV1
    liq_price: BybitDecimalFieldV1 | None
    auto_add_margin: int
    risk_id: int
    risk_limit_value: BybitDecimalFieldV1
    position_status: str
    is_reduce_only: bool
    seq: int
    updated_time_ms: int


@dataclass(frozen=True)
class BybitHeldOrderV1(_ValueV1):
    source: BybitModelSourceRefV1
    order_id: str
    order_link_id: str
    instrument_pin: InstrumentPinV2
    position_idx: int
    side: str
    order_type: str
    price: BybitDecimalFieldV1
    quantity: BybitDecimalFieldV1
    leaves_quantity: BybitDecimalFieldV1
    status: str
    reduce_only: bool
    close_on_trigger: bool
    trigger_price: BybitDecimalFieldV1 | None
    updated_time_ms: int


@dataclass(frozen=True)
class BybitSelectedEntryProfileV1(_ValueV1):
    revision: Literal["BYBIT_USDT_LINEAR_ISOLATED_ONE_WAY_NEW_ENTRY_V1"]
    symbol: str
    position_idx: Literal[0]
    selected_position_quantity: BybitDecimalFieldV1
    selected_open_order_ids: tuple[str, ...]
    selected_conditional_order_ids: tuple[str, ...]
    local_unresolved_entry_ids: tuple[str, ...]
    auto_add_margin: BybitModelFieldRefV1
    prospective_extra_margin_usdt: Decimal
    extra_margin_basis: Literal["NEW_SELECTED_FLAT_NO_ADD_V1"]
    source_records: tuple[BybitModelSourceRefV1, ...]


@dataclass(frozen=True)
class BybitRawTierRowV1(_ValueV1):
    risk_id: int
    symbol: str
    upper_inclusive_usdt: BybitDecimalFieldV1
    initial_margin_rate: BybitDecimalFieldV1
    maintenance_margin_rate: BybitDecimalFieldV1
    max_leverage: BybitDecimalFieldV1
    mm_deduction_usdt: BybitDecimalFieldV1
    is_lowest_risk: int


@dataclass(frozen=True)
class BybitRiskLimitPageV1:
    envelope: BybitRiskLimitEnvelopeReadV1
    source: BybitModelSourceRefV1
    rows: tuple[BybitRawTierRowV1, ...]
    next_cursor: str

    def __post_init__(self) -> None:
        # Rebuild from bytes once at every public trust boundary. Page fields
        # cannot substitute for the captured row/pointer/source association.
        _validate_page_v1(self)


@dataclass(frozen=True)
class BybitLinearTierRowV1(_ValueV1):
    risk_id: int
    lower_exclusive_usdt: Fraction
    upper_inclusive_usdt: BybitDecimalFieldV1
    initial_margin_rate: BybitDecimalFieldV1
    maintenance_margin_rate: BybitDecimalFieldV1
    max_leverage: BybitDecimalFieldV1
    mm_deduction_usdt: BybitDecimalFieldV1
    is_lowest_risk: int


def _source_v1(value: BybitModelSourceRefV1) -> None:
    _fields_v2(value, BybitModelSourceRefV1)
    for item in (value.observation_id, value.source_id, value.source_generation):
        _identifier_v2(item)
    _literal_v2(
        value.source_environment, ("PUBLIC_MAINNET", "PUBLIC_TESTNET", "DEMO", "LIVE", "FIXTURE")
    )
    _origin_v1(value.origin)
    path = _text_v1(value.path, 2048, empty=False)
    if re.fullmatch(r"/v5/[A-Za-z0-9/_-]+", path, re.ASCII) is None:
        _reject_v1()
    public = path.startswith("/v5/market/")
    if public:
        if value.account_scope is not None or value.source_environment not in (
            "PUBLIC_MAINNET",
            "PUBLIC_TESTNET",
            "FIXTURE",
        ):
            _reject_v1("RISK_V2_SCOPE_MISMATCH")
    else:
        scope = _fields_v2(value.account_scope, ProtectionScope)
        account = _text_v1(scope["account_id"], 256, empty=False)
        if account != account.strip() or any(ord(c) < 32 or ord(c) == 127 for c in account):
            _reject_v1()
        environment = _literal_v2(scope["environment"], ("DEMO", "LIVE"))
        if value.source_environment not in (environment, "FIXTURE"):
            _reject_v1("RISK_V2_SCOPE_MISMATCH")
    expected = {
        "PUBLIC_MAINNET": "api.bybit.com",
        "PUBLIC_TESTNET": "api-testnet.bybit.com",
        "DEMO": "api-demo.bybit.com",
        "LIVE": "api.bybit.com",
    }
    if (
        value.source_environment != "FIXTURE"
        and value.origin.host != expected[value.source_environment]
    ):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    _query_v1(value.query)
    start, received = _utc_v2(value.request_started_at), _utc_v2(value.received_at)
    if start > received:
        _reject_v1("RISK_V2_CAUSAL_ORDER_INVALID")
    if value.envelope_time_ms is not None:
        stamp = _count_v1(value.envelope_time_ms, 1, 253402300799999)
        if _EPOCH + timedelta(milliseconds=stamp) > received:
            _reject_v1("RISK_V2_CAUSAL_ORDER_INVALID")
    _digest_v2(value.raw_body_sha256)
    _count_v1(value.page_ordinal, 1, 64)
    _count_v1(value.accepted_attempt, 1, 2**31 - 1)


def _field_v1(value: BybitModelFieldRefV1) -> None:
    _fields_v2(value, BybitModelFieldRefV1)
    _source_v1(value.source)
    pointer = _text_v1(value.json_pointer, 2048)
    if (pointer and not pointer.startswith("/")) or re.search(r"~(?![01])", pointer):
        _reject_v1()
    kind = _literal_v2(value.wire_kind, ("STRING", "NUMBER", "INTEGER", "BOOLEAN"))
    spelling = _text_v1(value.wire_spelling, 1024)
    if not spelling.isascii():
        _reject_v1()
    if kind == "NUMBER":
        _number_spelling_v1(spelling)
    elif kind == "INTEGER":
        _integer_token_v1(JsonNumberTokenV1(spelling), -(2**63), 2**63 - 1)
    elif kind == "BOOLEAN" and spelling not in ("true", "false"):
        _reject_v1()


def _decimal_field_v1(value: BybitDecimalFieldV1) -> Fraction:
    _fields_v2(value, BybitDecimalFieldV1)
    _field_v1(value.reference)
    if value.reference.wire_kind not in ("STRING", "NUMBER", "INTEGER"):
        _reject_v1()
    spelling = _number_spelling_v1(value.reference.wire_spelling)
    _decimal_v2(value.value, source=True, sign="signed")
    # Decimal construction from validated text is exact and context independent.
    if value.value.as_tuple() != Decimal(spelling).as_tuple():
        _reject_v1()
    return decimal_fraction_v2(value.value)


def _document_v1(value: BybitFeeDocumentRefV1) -> None:
    _fields_v2(value, BybitFeeDocumentRefV1)
    url = _text_v1(value.document_url, 2048, empty=False)
    if not url.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        _reject_v1()
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        _reject_v1()
    if (
        parts.scheme != "https"
        or parts.hostname not in ("bybit.com", "www.bybit.com", "bybit-exchange.github.io")
        or parts.username is not None
        or parts.password is not None
        or "#" in url
        or parts.netloc.endswith(":")
        or port not in (None, 443)
    ):
        _reject_v1()
    _text_v1(value.document_updated_at_text, 512)
    _text_v1(value.exact_locator, 2048, empty=False)
    _utc_v2(value.captured_at)
    _digest_v2(value.raw_document_sha256)
    _digest_v2(value.account_region_profile_sha256)
    _identifier_v2(value.applicability_revision)
    _number_spelling_v1(value.displayed_rate_spelling)
    _literal_v2(value.displayed_unit, ("PERCENT", "FRACTION"))


def _nonvip_v1(value: BybitNonVipFeeBasisV1) -> None:
    _fields_v2(value, BybitNonVipFeeBasisV1)
    _decimal_v2(value.value, source=False, sign="nonnegative")
    if type(value.reference) is BybitModelFieldRefV1:
        _field_v1(value.reference)
        if value.reference.wire_kind not in ("STRING", "NUMBER", "INTEGER"):
            _reject_v1()
        spelling = _number_spelling_v1(value.reference.wire_spelling)
        expected = decimal_fraction_v2(Decimal(spelling))
    elif type(value.reference) is BybitFeeDocumentRefV1:
        _document_v1(value.reference)
        expected = decimal_fraction_v2(Decimal(value.reference.displayed_rate_spelling))
        if value.reference.displayed_unit == "PERCENT":
            expected /= 100
    else:
        _reject_v1()
    if decimal_fraction_v2(value.value) != expected:
        _reject_v1()


def _boolean_v1(value: object) -> None:
    if type(value) is not bool:
        _reject_v1()


def _symbol_v1(value: object) -> None:
    if type(value) is not str or re.fullmatch(r"[A-Z0-9]{2,48}", value, re.ASCII) is None:
        _reject_v1()
    _key_payload_v2(InstrumentKey("BYBIT", "LINEAR", value))


def _tuple_v1(value: object, maximum: int, *, minimum: int = 0) -> tuple[object, ...]:
    if type(value) is not tuple:
        _reject_v1()
    if len(value) > maximum:
        _reject_v1("RISK_V2_RESOURCE_LIMIT")
    if len(value) < minimum:
        _reject_v1()
    return cast(tuple[object, ...], value)


def _row_v1(value: BybitRawTierRowV1 | BybitLinearTierRowV1) -> None:
    if type(value) is BybitRawTierRowV1:
        _fields_v2(value, BybitRawTierRowV1)
        _symbol_v1(value.symbol)
    elif type(value) is BybitLinearTierRowV1:
        _fields_v2(value, BybitLinearTierRowV1)
        lower = value.lower_exclusive_usdt
        if type(lower) is not Fraction:
            _reject_v1()
        try:
            numerator, denominator = lower.numerator, lower.denominator
        except AttributeError:
            _reject_v1()
        if type(numerator) is not int or type(denominator) is not int or denominator <= 0:
            _reject_v1()
        if numerator.bit_length() > 1_000_000 or denominator.bit_length() > 1_000_000:
            _reject_v1("RISK_V2_RESOURCE_LIMIT")
        if gcd(numerator, denominator) != 1:
            _reject_v1()
        # Reuse exact conversion validation before comparison/number hooks.
        finite_decimal_v2(lower)
        if lower < 0:
            _reject_v1()
    else:
        _reject_v1()
    _count_v1(value.risk_id, 1, 2**63 - 1)
    _count_v1(value.is_lowest_risk, 0, 1)
    for field in (
        value.upper_inclusive_usdt,
        value.initial_margin_rate,
        value.maintenance_margin_rate,
        value.max_leverage,
        value.mm_deduction_usdt,
    ):
        _decimal_field_v1(field)
    for field in (value.initial_margin_rate, value.maintenance_margin_rate):
        if field.reference.wire_kind != "NUMBER":
            _reject_v1()
    for field in (value.upper_inclusive_usdt, value.max_leverage, value.mm_deduction_usdt):
        if field.reference.wire_kind != "STRING":
            _reject_v1()


def _validate_value_v1(value: _ValueV1) -> None:
    """Finite explicit DTO dispatch; no arbitrary class/serializer callbacks."""
    kind = type(value)
    if kind is BybitModelSourceRefV1:
        _source_v1(cast(BybitModelSourceRefV1, value))
        return
    if kind is BybitModelFieldRefV1:
        _field_v1(cast(BybitModelFieldRefV1, value))
        return
    if kind is BybitDecimalFieldV1:
        _decimal_field_v1(cast(BybitDecimalFieldV1, value))
        return
    if kind is BybitFeeDocumentRefV1:
        _document_v1(cast(BybitFeeDocumentRefV1, value))
        return
    if kind is BybitNonVipFeeBasisV1:
        _nonvip_v1(cast(BybitNonVipFeeBasisV1, value))
        return
    if kind in (BybitRawTierRowV1, BybitLinearTierRowV1):
        _row_v1(cast(BybitRawTierRowV1 | BybitLinearTierRowV1, value))
        return
    # All classes below have a supplied source or source-bearing children.
    if kind is BybitUtaModeV1:
        mode = cast(BybitUtaModeV1, value)
        _fields_v2(mode, BybitUtaModeV1)
        _source_v1(mode.source)
        _count_v1(mode.unified_margin_status, 5, 6)
        _literal_v2(mode.margin_mode, ("ISOLATED_MARGIN",))
        _count_v1(mode.updated_time_ms, 1, 253402300799999)
    elif kind is BybitTradeBehaviourV1:
        behaviour = cast(BybitTradeBehaviourV1, value)
        _fields_v2(behaviour, BybitTradeBehaviourV1)
        _source_v1(behaviour.source)
        _boolean_v1(behaviour.lpa_perp)
        _boolean_v1(behaviour.futures_mnt_fee_deduction)
        if behaviour.delta_enable is not None:
            _boolean_v1(behaviour.delta_enable)
    elif kind is BybitFlatLeverageV1:
        flat = cast(BybitFlatLeverageV1, value)
        _fields_v2(flat, BybitFlatLeverageV1)
        _source_v1(flat.source)
        _symbol_v1(flat.symbol)
        _count_v1(flat.position_idx, 0, 0)
        if _decimal_field_v1(flat.leverage) <= 0:
            _reject_v1()
        _literal_v2(flat.read_path, ("/v5/position/symbol-info", "/v5/position/list"))
    elif kind is BybitLinearFeeInputsV1:
        fee = cast(BybitLinearFeeInputsV1, value)
        _fields_v2(fee, BybitLinearFeeInputsV1)
        for field in (fee.account_taker_rate, fee.account_maker_rate):
            _decimal_field_v1(field)
        _nonvip_v1(fee.closing_order_reserve_nonvip_rate)
        _literal_v2(fee.fee_currency, ("USDT",))
        _fields_v2(fee.behaviour, BybitTradeBehaviourV1)
        _validate_value_v1(fee.behaviour)
        _identifier_v2(fee.nonvip_applicability_revision)
    elif kind is BybitUsdtWalletV1:
        wallet = cast(BybitUsdtWalletV1, value)
        data = _fields_v2(wallet, BybitUsdtWalletV1)
        _source_v1(wallet.source)
        for name in (
            "wallet_balance",
            "equity",
            "total_position_im",
            "total_order_im",
            "locked",
            "bonus",
            "borrow_amount",
            "spot_borrow",
            "accrued_interest",
            "unrealised_pnl",
        ):
            amount = _decimal_field_v1(data[name])
            if name not in ("wallet_balance", "equity", "unrealised_pnl") and amount < 0:
                _reject_v1()
    elif kind is BybitHeldPositionV1:
        position = cast(BybitHeldPositionV1, value)
        data = _fields_v2(position, BybitHeldPositionV1)
        _source_v1(position.source)
        _pin_payload_v2(position.instrument_pin)
        _count_v1(position.position_idx, 0, 2**31 - 1)
        for name in ("side", "position_status"):
            _text_v1(data[name], 1024, empty=False)
        for name in (
            "size",
            "avg_price",
            "mark_price",
            "leverage",
            "position_im",
            "position_im_by_mp",
            "position_mm",
            "position_mm_by_mp",
            "risk_limit_value",
        ):
            amount = _decimal_field_v1(data[name])
            if amount < 0 or (
                name in ("size", "avg_price", "mark_price", "leverage") and amount == 0
            ):
                _reject_v1()
        if position.liq_price is not None:
            _decimal_field_v1(position.liq_price)
        _count_v1(position.auto_add_margin, 0, 1)
        _count_v1(position.risk_id, 0, 2**63 - 1)
        _count_v1(position.seq, -(2**63), 2**63 - 1)
        _count_v1(position.updated_time_ms, 1, 253402300799999)
        _boolean_v1(position.is_reduce_only)
    elif kind is BybitHeldOrderV1:
        order = cast(BybitHeldOrderV1, value)
        data = _fields_v2(order, BybitHeldOrderV1)
        _source_v1(order.source)
        _pin_payload_v2(order.instrument_pin)
        _identifier_v2(order.order_id)
        if _text_v1(order.order_link_id, 96):
            _identifier_v2(order.order_link_id)
        _count_v1(order.position_idx, 0, 2**31 - 1)
        for name in ("side", "order_type", "status"):
            _text_v1(data[name], 1024, empty=False)
        for field in (order.price, order.quantity, order.leaves_quantity):
            if _decimal_field_v1(field) < 0:
                _reject_v1()
        if order.trigger_price is not None:
            _decimal_field_v1(order.trigger_price)
        _boolean_v1(order.reduce_only)
        _boolean_v1(order.close_on_trigger)
        _count_v1(order.updated_time_ms, 1, 253402300799999)
    elif kind is BybitSelectedEntryProfileV1:
        profile = cast(BybitSelectedEntryProfileV1, value)
        _fields_v2(profile, BybitSelectedEntryProfileV1)
        _literal_v2(profile.revision, ("BYBIT_USDT_LINEAR_ISOLATED_ONE_WAY_NEW_ENTRY_V1",))
        _symbol_v1(profile.symbol)
        _count_v1(profile.position_idx, 0, 0)
        if _decimal_field_v1(profile.selected_position_quantity) < 0:
            _reject_v1()
        for identifiers in (
            profile.selected_open_order_ids,
            profile.selected_conditional_order_ids,
            profile.local_unresolved_entry_ids,
        ):
            for identifier in _tuple_v1(identifiers, 1024):
                _identifier_v2(identifier)
        _field_v1(profile.auto_add_margin)
        _decimal_v2(profile.prospective_extra_margin_usdt, source=False, sign="nonnegative")
        _literal_v2(profile.extra_margin_basis, ("NEW_SELECTED_FLAT_NO_ADD_V1",))
        for source in _tuple_v1(profile.source_records, 1024, minimum=1):
            _source_v1(cast(BybitModelSourceRefV1, source))
    else:
        _reject_v1()


def _decode_page_parts_v1(
    value: BybitRiskLimitEnvelopeReadV1, page_ordinal: int
) -> tuple[BybitModelSourceRefV1, tuple[BybitRawTierRowV1, ...], str]:
    _count_v1(page_ordinal, 1, 64)
    body = _validated_risk_body_v1(value)
    if (page_ordinal == 1) != (len(value.params) == 2):
        _reject_v1()
    source = BybitModelSourceRefV1(
        value.observation_id,
        value.source_id,
        value.source_generation,
        None,
        value.source_environment,
        value.response_origin,
        value.endpoint,
        value.params,
        value.request_started_at,
        value.received_at,
        _integer_token_v1(body["time"], 1, 253402300799999),
        value.decoded_body_sha256,
        page_ordinal,
        value.accepted_attempt,
    )
    result = cast(dict[str, object], body["result"])
    raw_rows = cast(list[object], result["list"])
    if len(raw_rows) > 1024:
        _reject_v1("RISK_V2_RESOURCE_LIMIT")
    rows: list[BybitRawTierRowV1] = []
    for index, raw_row in enumerate(raw_rows):
        if type(raw_row) is not dict or raw_row.keys() != {
            "id",
            "symbol",
            "riskLimitValue",
            "initialMargin",
            "maintenanceMargin",
            "maxLeverage",
            "mmDeduction",
            "isLowestRisk",
        }:
            _reject_v1()
        row = cast(dict[str, object], raw_row)
        if type(row["symbol"]) is not str or row["symbol"] != value.key.symbol:
            _reject_v1("RISK_V2_INPUT_INVALID")

        rows.append(
            BybitRawTierRowV1(
                _integer_token_v1(row["id"], 1, 2**63 - 1),
                value.key.symbol,
                _raw_field_v1(row, source, index, "riskLimitValue", "STRING"),
                _raw_field_v1(row, source, index, "initialMargin", "NUMBER"),
                _raw_field_v1(row, source, index, "maintenanceMargin", "NUMBER"),
                _raw_field_v1(row, source, index, "maxLeverage", "STRING"),
                _raw_field_v1(row, source, index, "mmDeduction", "STRING"),
                _integer_token_v1(row["isLowestRisk"], 0, 1),
            )
        )
    return source, tuple(rows), cast(str, result["nextPageCursor"])


def _raw_field_v1(
    row: dict[str, object],
    source: BybitModelSourceRefV1,
    index: int,
    name: str,
    wire_kind: Literal["STRING", "NUMBER"],
) -> BybitDecimalFieldV1:
    raw_value = row[name]
    if wire_kind == "STRING":
        spelling = _number_spelling_v1(raw_value)
    else:
        spelling = _fields_v2(raw_value, JsonNumberTokenV1)["spelling"]
        _number_spelling_v1(spelling)
    reference = BybitModelFieldRefV1(source, f"/result/list/{index}/{name}", wire_kind, spelling)
    return BybitDecimalFieldV1(Decimal(spelling), reference)


def _validate_page_v1(value: BybitRiskLimitPageV1) -> None:
    _fields_v2(value, BybitRiskLimitPageV1)
    _source_v1(value.source)
    _text_v1(value.next_cursor, 1024)
    for row in _tuple_v1(value.rows, 1024):
        _fields_v2(row, BybitRawTierRowV1)
        _row_v1(cast(BybitRawTierRowV1, row))
    source, rows, cursor = _decode_page_parts_v1(value.envelope, value.source.page_ordinal)
    if value.source != source or value.rows != rows or value.next_cursor != cursor:
        _reject_v1("RISK_V2_SOURCE_MISMATCH")


def decode_bybit_risk_limit_page_v1(
    value: BybitRiskLimitEnvelopeReadV1, *, page_ordinal: int
) -> BybitRiskLimitPageV1:
    """Decode supplied bytes, retaining original row order and field pointers."""
    source, rows, cursor = _decode_page_parts_v1(value, page_ordinal)
    return BybitRiskLimitPageV1(value, source, rows, cursor)


def normalize_bybit_tier_rows_v1(
    pages: tuple[BybitRiskLimitPageV1, ...],
) -> tuple[BybitLinearTierRowV1, ...]:
    """Validate the complete captured chain, then every exact interval once."""
    _tuple_v1(pages, 64, minimum=1)
    total_bytes = 0
    total_rows = 0
    # Preflight full counts before parsing/rebuilding or allocating row aggregates.
    for page in pages:
        _fields_v2(page, BybitRiskLimitPageV1)
        envelope = _fields_v2(page.envelope, BybitRiskLimitEnvelopeReadV1)
        raw = envelope["decoded_body_bytes"]
        if type(raw) is not bytes:
            _reject_v1()
        total_bytes += len(raw)
        total_rows += len(_tuple_v1(page.rows, 1024))
        if total_bytes > 1048576 or total_rows > 1024:
            _reject_v1("RISK_V2_RESOURCE_LIMIT")
    if total_rows == 0:
        _reject_v1()
    cursors: set[str] = set()
    observations: set[str] = set()
    raw_rows: list[BybitRawTierRowV1] = []
    previous_cursor = ""
    first = pages[0]
    for ordinal, page in enumerate(pages, 1):
        _validate_page_v1(page)
        if page.source.page_ordinal != ordinal:
            _reject_v1()
        if ordinal > 1 and page.envelope.params[-1] != ("cursor", previous_cursor):
            _reject_v1("RISK_V2_SOURCE_MISMATCH")
        if (
            page.envelope.key != first.envelope.key
            or page.envelope.sent_origin != first.envelope.sent_origin
            or page.source.origin != first.source.origin
            or page.source.source_id != first.source.source_id
            or page.source.source_generation != first.source.source_generation
            or page.source.source_environment != first.source.source_environment
            or page.source.observation_id in observations
        ):
            _reject_v1("RISK_V2_SOURCE_MISMATCH")
        observations.add(page.source.observation_id)
        if (ordinal == len(pages)) != (page.next_cursor == ""):
            _reject_v1()
        if page.next_cursor:
            if page.next_cursor in cursors:
                _reject_v1()
            cursors.add(page.next_cursor)
        previous_cursor = page.next_cursor
        raw_rows.extend(page.rows)
    raw_rows.sort(key=lambda row: decimal_fraction_v2(row.upper_inclusive_usdt.value))
    ids: set[int] = set()
    lower, previous_deduction, previous_rate = Fraction(0), Fraction(0), Fraction(0)
    normalized: list[BybitLinearTierRowV1] = []
    for index, row in enumerate(raw_rows):
        upper, initial, maintenance, leverage, deduction = (
            decimal_fraction_v2(field.value)
            for field in (
                row.upper_inclusive_usdt,
                row.initial_margin_rate,
                row.maintenance_margin_rate,
                row.max_leverage,
                row.mm_deduction_usdt,
            )
        )
        # Each source operand is <=256 coefficient digits, exponent1000. Even
        # unreduced products/sums here stay <16000 bits, far below work1m bits;
        # validating sources above therefore guards these allocations beforehand.
        if (
            row.risk_id in ids
            or upper <= lower
            or leverage <= 0
            or not 0 < maintenance < 1
            or not maintenance <= initial <= 1
            or deduction < 0
            or row.is_lowest_risk != int(index == 0)
            or (index == 0 and deduction != 0)
            or (
                index > 0
                and deduction != previous_deduction + lower * (maintenance - previous_rate)
            )
        ):
            _reject_v1("RISK_V2_RISK_TIER_UNVERIFIED")
        ids.add(row.risk_id)
        normalized.append(
            BybitLinearTierRowV1(
                row.risk_id,
                lower,
                row.upper_inclusive_usdt,
                row.initial_margin_rate,
                row.maintenance_margin_rate,
                row.max_leverage,
                row.mm_deduction_usdt,
                row.is_lowest_risk,
            )
        )
        lower, previous_deduction, previous_rate = upper, deduction, maintenance
    return tuple(normalized)


@dataclass(frozen=True)
class BybitLinearTierTableV1:
    """Complete supplied source graph; raw-manifest preimage lives in Capture."""

    instrument_pin: InstrumentPinV2
    reference: RiskObservationRefV2
    pages: tuple[BybitModelSourceRefV1, ...]
    rows: tuple[BybitLinearTierRowV1, ...]
    raw_manifest_sha256: str
    full_table_sha256: str
    rate_encoding_revision: Literal["BYBIT_LINEAR_RISK_NUMBER_FRACTION_V1"]
    deduction_rule_revision: Literal["EXACT_CONTINUOUS_MM_DEDUCTION_V1"]

    def __post_init__(self) -> None:
        _table_v1(self)


@dataclass(frozen=True)
class BybitRiskLimitCaptureV1:
    """A replayable byte association, never an actual issuer receipt."""

    table: BybitLinearTierTableV1
    raw_pages: tuple[BybitRiskLimitEnvelopeReadV1, ...]

    def __post_init__(self) -> None:
        _capture_v1(self)


def _integer_text_v1(value: int) -> str:
    # finite_decimal enforces derived digit/work bounds without global int-str
    # settings. Its exponent is zero for an integer; use coefficient digits.
    parts = finite_decimal_v2(Fraction(value)).as_tuple()
    return ("-" if parts.sign else "") + "".join(chr(48 + d) for d in parts.digits)


def _rational_payload_v1(value: Fraction) -> dict[str, object]:
    if type(value) is not Fraction:
        _reject_v1()
    try:
        n, d = value.numerator, value.denominator
    except AttributeError:
        _reject_v1()
    if type(n) is not int or type(d) is not int or d <= 0:
        _reject_v1()
    if n.bit_length() > 1_000_000 or d.bit_length() > 1_000_000:
        _reject_v1("RISK_V2_RESOURCE_LIMIT")
    if gcd(n, d) != 1:
        _reject_v1()
    return {"numerator": _integer_text_v1(n), "denominator": _integer_text_v1(d)}


def _source_payload_v1(value: BybitModelSourceRefV1) -> dict[str, object]:
    _source_v1(value)
    return {
        "observation_id": value.observation_id,
        "source_id": value.source_id,
        "source_generation": value.source_generation,
        "account_scope": None
        if value.account_scope is None
        else {
            "account_id": value.account_scope.account_id,
            "environment": value.account_scope.environment,
        },
        "source_environment": value.source_environment,
        "origin": {
            "scheme": value.origin.scheme,
            "host": value.origin.host,
            "port": value.origin.port,
        },
        "path": value.path,
        "query": value.query,
        "request_started_at": _time_text_v2(value.request_started_at),
        "received_at": _time_text_v2(value.received_at),
        "envelope_time_ms": value.envelope_time_ms,
        "raw_body_sha256": value.raw_body_sha256,
        "page_ordinal": value.page_ordinal,
        "accepted_attempt": value.accepted_attempt,
    }


def _raw_capture_parts_v1(
    pages: tuple[BybitRiskLimitEnvelopeReadV1, ...],
) -> tuple[tuple[BybitRiskLimitPageV1, ...], tuple[BybitLinearTierRowV1, ...]]:
    _tuple_v1(pages, 64, minimum=1)
    total = 0
    for envelope in pages:
        raw = _fields_v2(envelope, BybitRiskLimitEnvelopeReadV1)["decoded_body_bytes"]
        if type(raw) is not bytes:
            _reject_v1()
        total += len(raw)
        if total > 1048576:
            _reject_v1("RISK_V2_RESOURCE_LIMIT")
    decoded = tuple(
        decode_bybit_risk_limit_page_v1(page, page_ordinal=i) for i, page in enumerate(pages, 1)
    )
    return decoded, normalize_bybit_tier_rows_v1(decoded)


def _raw_manifest_payload_v1(pages: tuple[BybitRiskLimitPageV1, ...]) -> dict[str, object]:
    # Called only after complete A0 replay/normalization of this exact sequence.
    payloads: list[dict[str, object]] = []
    for ordinal, page in enumerate(pages, 1):
        e = page.envelope
        payloads.append(
            {
                "key": _key_payload_v2(e.key),
                "method": e.method,
                "endpoint": e.endpoint,
                "params": e.params,
                "sent_origin": {
                    "scheme": e.sent_origin.scheme,
                    "host": e.sent_origin.host,
                    "port": e.sent_origin.port,
                },
                "response_origin": {
                    "scheme": e.response_origin.scheme,
                    "host": e.response_origin.host,
                    "port": e.response_origin.port,
                },
                "response_status": e.response_status,
                "request_started_at": _time_text_v2(e.request_started_at),
                "received_at": _time_text_v2(e.received_at),
                "started_mono_hex": e.started_mono.hex(),
                "received_mono_hex": e.received_mono.hex(),
                "response_time": _time_text_v2(e.response_time),
                "decoded_body_sha256": e.decoded_body_sha256,
                "decoded_body_length": len(e.decoded_body_bytes),
                "accepted_attempt": e.accepted_attempt,
                "physical_start": e.physical_start,
                "physical_end": e.physical_end,
                "logical_ordinal": e.logical_ordinal,
                "observation_id": e.observation_id,
                "source_id": e.source_id,
                "source_generation": e.source_generation,
                "source_environment": e.source_environment,
                "page_ordinal": ordinal,
                "cursor_in": "" if ordinal == 1 else e.params[-1][1],
                "cursor_out": page.next_cursor,
            }
        )
    return {"pages": tuple(payloads)}


def _semantic_table_payload_v1(
    pin: InstrumentPinV2,
    rows: tuple[BybitLinearTierRowV1, ...],
) -> dict[str, object]:
    # Dedicated self-excluded recipe. Inputs were fully validated by either A0
    # replay/parser or the Table shape/association validator before this call.
    return {
        "instrument": {
            "key": _key_payload_v2(pin.key),
            "native_instrument_id": pin.native_instrument_id,
            "specification_revision": pin.specification_revision,
        },
        "rate_encoding_revision": "BYBIT_LINEAR_RISK_NUMBER_FRACTION_V1",
        "deduction_rule_revision": "EXACT_CONTINUOUS_MM_DEDUCTION_V1",
        "rows": tuple(
            {
                "tier_id": _integer_text_v1(row.risk_id),
                "lower_exclusive_usdt": _rational_payload_v1(row.lower_exclusive_usdt),
                "upper_inclusive_usdt": _rational_payload_v1(
                    decimal_fraction_v2(row.upper_inclusive_usdt.value)
                ),
                "initial_margin_rate": _rational_payload_v1(
                    decimal_fraction_v2(row.initial_margin_rate.value)
                ),
                "maintenance_margin_rate": _rational_payload_v1(
                    decimal_fraction_v2(row.maintenance_margin_rate.value)
                ),
                "max_leverage": _rational_payload_v1(decimal_fraction_v2(row.max_leverage.value)),
                "mm_deduction_usdt": _rational_payload_v1(
                    decimal_fraction_v2(row.mm_deduction_usdt.value)
                ),
                "is_lowest_risk": row.is_lowest_risk,
            }
            for row in rows
        ),
    }


def _tier_source_payload_v1(
    pin: InstrumentPinV2, raw_hash: str, full_hash: str
) -> dict[str, object]:
    return {
        "instrument_pin": _pin_payload_v2(pin),
        "source_rule_revision": SOURCE_RULE_REVISION,
        "raw_manifest_sha256": raw_hash,
        "full_table_sha256": full_hash,
    }


def _table_shape_v1(value: BybitLinearTierTableV1) -> None:
    _fields_v2(value, BybitLinearTierTableV1)
    _pin_payload_v2(value.instrument_pin)
    _observation_payload_v2(value.reference)
    _digest_v2(value.raw_manifest_sha256)
    _digest_v2(value.full_table_sha256)
    _literal_v2(value.rate_encoding_revision, ("BYBIT_LINEAR_RISK_NUMBER_FRACTION_V1",))
    _literal_v2(value.deduction_rule_revision, ("EXACT_CONTINUOUS_MM_DEDUCTION_V1",))
    _tuple_v1(value.pages, 64, minimum=1)
    _tuple_v1(value.rows, 1024, minimum=1)
    # Validate every child before any cross-record equality or key comparison.
    for source in value.pages:
        bybit_source_ref_sha256_v1(source)
    for row in value.rows:
        bybit_tier_row_sha256_v1(row)
    first = value.pages[0]
    symbol = value.instrument_pin.key.symbol
    if first.source_environment != value.instrument_pin.source_environment:
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    observations: set[str] = set()
    cursors: set[str] = set()
    for i, source in enumerate(value.pages, 1):
        if source.page_ordinal != i or source.observation_id in observations:
            _reject_v1("RISK_V2_SOURCE_MISMATCH")
        observations.add(source.observation_id)
        if (
            source.path != "/v5/market/risk-limit"
            or source.account_scope is not None
            or source.source_id != first.source_id
            or source.source_generation != first.source_generation
            or source.source_environment != first.source_environment
            or source.origin != first.origin
            or source.envelope_time_ms is None
            or source.query[:2] != (("category", "linear"), ("symbol", symbol))
            or len(source.query) != (2 if i == 1 else 3)
        ):
            _reject_v1("RISK_V2_SOURCE_MISMATCH")
        if i > 1:
            if (
                source.query[2][0] != "cursor"
                or not source.query[2][1]
                or source.query[2][1] in cursors
            ):
                _reject_v1("RISK_V2_SOURCE_MISMATCH")
            cursors.add(source.query[2][1])
    ref = value.reference
    server = max(
        _EPOCH + timedelta(milliseconds=cast(int, p.envelope_time_ms)) for p in value.pages
    )
    if (
        ref.source_id != first.source_id
        or ref.source_environment != first.source_environment
        or ref.source_rule_revision != SOURCE_RULE_REVISION
        or _utc_v2(ref.server_observed_at) != server
        or _utc_v2(ref.received_at) != max(_utc_v2(p.received_at) for p in value.pages)
    ):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    indices: dict[int, set[int]] = {i: set() for i in range(1, len(value.pages) + 1)}
    ids: set[int] = set()
    lower, previous_d, previous_m = Fraction(0), Fraction(0), Fraction(0)
    for index, row in enumerate(value.rows):
        fields = (
            row.upper_inclusive_usdt,
            row.initial_margin_rate,
            row.maintenance_margin_rate,
            row.max_leverage,
            row.mm_deduction_usdt,
        )
        source = fields[0].reference.source
        if source not in value.pages:
            _reject_v1("RISK_V2_SOURCE_MISMATCH")
        pointer = fields[0].reference.json_pointer
        match = re.fullmatch(r"/result/list/(0|[1-9][0-9]{0,3})/riskLimitValue", pointer, re.ASCII)
        if match is None:
            _reject_v1("RISK_V2_SOURCE_MISMATCH")
        row_index = int(match.group(1))
        _count_v1(row_index, 0, 1023)
        if row_index in indices[source.page_ordinal]:
            _reject_v1("RISK_V2_SOURCE_MISMATCH")
        indices[source.page_ordinal].add(row_index)
        for field, name in zip(
            fields,
            ("riskLimitValue", "initialMargin", "maintenanceMargin", "maxLeverage", "mmDeduction"),
            strict=True,
        ):
            if (
                field.reference.source != source
                or field.reference.json_pointer != f"/result/list/{row_index}/{name}"
            ):
                _reject_v1("RISK_V2_SOURCE_MISMATCH")
        upper, initial, maintenance, leverage, deduction = (
            decimal_fraction_v2(f.value) for f in fields
        )
        # A0 bounds every source operand before this <=16000-bit arithmetic.
        if (
            row.lower_exclusive_usdt != lower
            or row.risk_id in ids
            or upper <= lower
            or leverage <= 0
            or not 0 < maintenance < 1
            or not maintenance <= initial <= 1
            or deduction < 0
            or row.is_lowest_risk != int(index == 0)
            or (index == 0 and deduction != 0)
            or (index > 0 and deduction != previous_d + lower * (maintenance - previous_m))
        ):
            _reject_v1("RISK_V2_RISK_TIER_UNVERIFIED")
        ids.add(row.risk_id)
        lower, previous_d, previous_m = upper, deduction, maintenance
    for seen in indices.values():
        if seen != set(range(len(seen))):
            _reject_v1("RISK_V2_SOURCE_MISMATCH")


def _table_v1(value: BybitLinearTierTableV1) -> None:
    _table_shape_v1(value)
    semantic = _canonical_sha256_v2(
        "BybitLinearTierTableSemanticV1",
        _semantic_table_payload_v1(value.instrument_pin, value.rows),
    )
    source_payload = _canonical_sha256_v2(
        "BybitLinearTierSourcePayloadV1",
        _tier_source_payload_v1(value.instrument_pin, value.raw_manifest_sha256, semantic),
    )
    if value.full_table_sha256 != semantic or value.reference.payload_sha256 != source_payload:
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    # raw_manifest_sha256 cannot be authenticated here: Table has no raw pages.


def _capture_v1(value: BybitRiskLimitCaptureV1) -> None:
    _fields_v2(value, BybitRiskLimitCaptureV1)
    _table_v1(value.table)
    decoded, rows = _raw_capture_parts_v1(value.raw_pages)
    raw_hash = _canonical_sha256_v2(
        "BybitRiskLimitRawManifestV1", _raw_manifest_payload_v1(decoded)
    )
    if (
        raw_hash != value.table.raw_manifest_sha256
        or tuple(p.source for p in decoded) != value.table.pages
        or rows != value.table.rows
    ):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    # Pin/ref/rules/semantic/source payload were checked by _table_v1 above;
    # source and row equality now associate every retained pointer with bytes.


def parse_bybit_risk_limit_table_v1(
    pages: tuple[BybitRiskLimitEnvelopeReadV1, ...],
    *,
    instrument_pin: InstrumentPinV2,
    observation_id: str,
) -> BybitRiskLimitCaptureV1:
    """Finalize a complete supplied capture without I/O, fake hashes or clocks."""
    _pin_payload_v2(instrument_pin)
    _identifier_v2(observation_id)
    decoded, rows = _raw_capture_parts_v1(pages)
    if (
        instrument_pin.key != pages[0].key
        or instrument_pin.source_environment != pages[0].source_environment
    ):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    raw_hash = _canonical_sha256_v2(
        "BybitRiskLimitRawManifestV1", _raw_manifest_payload_v1(decoded)
    )
    full_hash = _canonical_sha256_v2(
        "BybitLinearTierTableSemanticV1", _semantic_table_payload_v1(instrument_pin, rows)
    )
    source_hash = _canonical_sha256_v2(
        "BybitLinearTierSourcePayloadV1",
        _tier_source_payload_v1(instrument_pin, raw_hash, full_hash),
    )
    reference = RiskObservationRefV2(
        observation_id,
        pages[0].source_id,
        pages[0].source_environment,
        SOURCE_RULE_REVISION,
        max(p.response_time for p in pages),
        max(p.received_at for p in pages),
        source_hash,
    )
    table = BybitLinearTierTableV1(
        instrument_pin,
        reference,
        tuple(p.source for p in decoded),
        rows,
        raw_hash,
        full_hash,
        "BYBIT_LINEAR_RISK_NUMBER_FRACTION_V1",
        "EXACT_CONTINUOUS_MM_DEDUCTION_V1",
    )
    return BybitRiskLimitCaptureV1(table, pages)


def bybit_instrument_pin_sha256_v1(value: InstrumentPinV2) -> str:
    return _canonical_sha256_v2("BybitInstrumentPinV1", _pin_payload_v2(value))


def bybit_source_ref_sha256_v1(value: BybitModelSourceRefV1) -> str:
    return _canonical_sha256_v2("BybitModelSourceRefV1", _source_payload_v1(value))


def bybit_field_ref_sha256_v1(value: BybitModelFieldRefV1) -> str:
    _field_v1(value)
    return _canonical_sha256_v2(
        "BybitModelFieldRefV1",
        {
            "source": bybit_source_ref_sha256_v1(value.source),
            "json_pointer": value.json_pointer,
            "wire_kind": value.wire_kind,
            "wire_spelling": value.wire_spelling,
        },
    )


def bybit_decimal_field_sha256_v1(value: BybitDecimalFieldV1) -> str:
    _decimal_field_v1(value)
    return _canonical_sha256_v2(
        "BybitDecimalFieldV1",
        {
            "value": decimal_text_v2(value.value),
            "reference": bybit_field_ref_sha256_v1(value.reference),
        },
    )


def bybit_nonvip_fee_sha256_v1(value: BybitNonVipFeeBasisV1) -> str:
    _nonvip_v1(value)
    if type(value.reference) is BybitModelFieldRefV1:
        kind, reference = "JSON_FIELD", bybit_field_ref_sha256_v1(value.reference)
    else:
        kind, reference = (
            "FEE_DOCUMENT",
            bybit_fee_document_ref_sha256_v1(cast(BybitFeeDocumentRefV1, value.reference)),
        )
    return _canonical_sha256_v2(
        "BybitNonVipFeeBasisV1",
        {"value": decimal_text_v2(value.value), "reference_kind": kind, "reference": reference},
    )


def bybit_fee_document_ref_sha256_v1(value: BybitFeeDocumentRefV1) -> str:
    _document_v1(value)
    return _canonical_sha256_v2(
        "BybitFeeDocumentRefV1",
        {
            "document_url": value.document_url,
            "document_updated_at_text": value.document_updated_at_text,
            "captured_at": _time_text_v2(value.captured_at),
            "raw_document_sha256": value.raw_document_sha256,
            "exact_locator": value.exact_locator,
            "displayed_rate_spelling": value.displayed_rate_spelling,
            "displayed_unit": value.displayed_unit,
            "account_region_profile_sha256": value.account_region_profile_sha256,
            "applicability_revision": value.applicability_revision,
        },
    )


def bybit_uta_mode_sha256_v1(value: BybitUtaModeV1) -> str:
    _fields_v2(value, BybitUtaModeV1)
    _validate_value_v1(value)
    return _canonical_sha256_v2(
        "BybitUtaModeV1",
        {
            "source": bybit_source_ref_sha256_v1(value.source),
            "unified_margin_status": value.unified_margin_status,
            "margin_mode": value.margin_mode,
            "updated_time_ms": value.updated_time_ms,
        },
    )


def bybit_trade_behaviour_sha256_v1(value: BybitTradeBehaviourV1) -> str:
    _fields_v2(value, BybitTradeBehaviourV1)
    _validate_value_v1(value)
    return _canonical_sha256_v2(
        "BybitTradeBehaviourV1",
        {
            "source": bybit_source_ref_sha256_v1(value.source),
            "lpa_perp": value.lpa_perp,
            "futures_mnt_fee_deduction": value.futures_mnt_fee_deduction,
            "delta_enable": value.delta_enable,
        },
    )


def bybit_flat_leverage_sha256_v1(value: BybitFlatLeverageV1) -> str:
    _fields_v2(value, BybitFlatLeverageV1)
    _validate_value_v1(value)
    return _canonical_sha256_v2(
        "BybitFlatLeverageV1",
        {
            "source": bybit_source_ref_sha256_v1(value.source),
            "symbol": value.symbol,
            "position_idx": value.position_idx,
            "leverage": bybit_decimal_field_sha256_v1(value.leverage),
            "read_path": value.read_path,
        },
    )


def bybit_fee_inputs_sha256_v1(value: BybitLinearFeeInputsV1) -> str:
    _fields_v2(value, BybitLinearFeeInputsV1)
    _validate_value_v1(value)
    return _canonical_sha256_v2(
        "BybitLinearFeeInputsV1",
        {
            "account_taker_rate": bybit_decimal_field_sha256_v1(value.account_taker_rate),
            "account_maker_rate": bybit_decimal_field_sha256_v1(value.account_maker_rate),
            "closing_order_reserve_nonvip_rate": bybit_nonvip_fee_sha256_v1(
                value.closing_order_reserve_nonvip_rate
            ),
            "fee_currency": value.fee_currency,
            "behaviour": bybit_trade_behaviour_sha256_v1(value.behaviour),
            "nonvip_applicability_revision": value.nonvip_applicability_revision,
        },
    )


def bybit_wallet_sha256_v1(value: BybitUsdtWalletV1) -> str:
    _fields_v2(value, BybitUsdtWalletV1)
    _validate_value_v1(value)
    return _canonical_sha256_v2(
        "BybitUsdtWalletV1",
        {
            "source": bybit_source_ref_sha256_v1(value.source),
            "wallet_balance": bybit_decimal_field_sha256_v1(value.wallet_balance),
            "equity": bybit_decimal_field_sha256_v1(value.equity),
            "total_position_im": bybit_decimal_field_sha256_v1(value.total_position_im),
            "total_order_im": bybit_decimal_field_sha256_v1(value.total_order_im),
            "locked": bybit_decimal_field_sha256_v1(value.locked),
            "bonus": bybit_decimal_field_sha256_v1(value.bonus),
            "borrow_amount": bybit_decimal_field_sha256_v1(value.borrow_amount),
            "spot_borrow": bybit_decimal_field_sha256_v1(value.spot_borrow),
            "accrued_interest": bybit_decimal_field_sha256_v1(value.accrued_interest),
            "unrealised_pnl": bybit_decimal_field_sha256_v1(value.unrealised_pnl),
        },
    )


def bybit_held_position_sha256_v1(value: BybitHeldPositionV1) -> str:
    _fields_v2(value, BybitHeldPositionV1)
    _validate_value_v1(value)
    return _canonical_sha256_v2(
        "BybitHeldPositionV1",
        {
            "source": bybit_source_ref_sha256_v1(value.source),
            "instrument_pin": bybit_instrument_pin_sha256_v1(value.instrument_pin),
            "position_idx": value.position_idx,
            "side": value.side,
            "auto_add_margin": value.auto_add_margin,
            "risk_id": value.risk_id,
            "position_status": value.position_status,
            "is_reduce_only": value.is_reduce_only,
            "seq": value.seq,
            "updated_time_ms": value.updated_time_ms,
            "size": bybit_decimal_field_sha256_v1(value.size),
            "avg_price": bybit_decimal_field_sha256_v1(value.avg_price),
            "mark_price": bybit_decimal_field_sha256_v1(value.mark_price),
            "leverage": bybit_decimal_field_sha256_v1(value.leverage),
            "position_im": bybit_decimal_field_sha256_v1(value.position_im),
            "position_im_by_mp": bybit_decimal_field_sha256_v1(value.position_im_by_mp),
            "position_mm": bybit_decimal_field_sha256_v1(value.position_mm),
            "position_mm_by_mp": bybit_decimal_field_sha256_v1(value.position_mm_by_mp),
            "risk_limit_value": bybit_decimal_field_sha256_v1(value.risk_limit_value),
            "liq_price": None
            if value.liq_price is None
            else bybit_decimal_field_sha256_v1(value.liq_price),
        },
    )


def bybit_held_order_sha256_v1(value: BybitHeldOrderV1) -> str:
    _fields_v2(value, BybitHeldOrderV1)
    _validate_value_v1(value)
    return _canonical_sha256_v2(
        "BybitHeldOrderV1",
        {
            "source": bybit_source_ref_sha256_v1(value.source),
            "instrument_pin": bybit_instrument_pin_sha256_v1(value.instrument_pin),
            "order_id": value.order_id,
            "order_link_id": value.order_link_id,
            "position_idx": value.position_idx,
            "side": value.side,
            "order_type": value.order_type,
            "status": value.status,
            "reduce_only": value.reduce_only,
            "close_on_trigger": value.close_on_trigger,
            "updated_time_ms": value.updated_time_ms,
            "price": bybit_decimal_field_sha256_v1(value.price),
            "quantity": bybit_decimal_field_sha256_v1(value.quantity),
            "leaves_quantity": bybit_decimal_field_sha256_v1(value.leaves_quantity),
            "trigger_price": None
            if value.trigger_price is None
            else bybit_decimal_field_sha256_v1(value.trigger_price),
        },
    )


def bybit_tier_row_sha256_v1(value: BybitLinearTierRowV1) -> str:
    _fields_v2(value, BybitLinearTierRowV1)
    _row_v1(value)
    return _canonical_sha256_v2(
        "BybitLinearTierRowV1",
        {
            "risk_id": value.risk_id,
            "lower_exclusive_usdt": _rational_payload_v1(value.lower_exclusive_usdt),
            "is_lowest_risk": value.is_lowest_risk,
            "upper_inclusive_usdt": bybit_decimal_field_sha256_v1(value.upper_inclusive_usdt),
            "initial_margin_rate": bybit_decimal_field_sha256_v1(value.initial_margin_rate),
            "maintenance_margin_rate": bybit_decimal_field_sha256_v1(value.maintenance_margin_rate),
            "max_leverage": bybit_decimal_field_sha256_v1(value.max_leverage),
            "mm_deduction_usdt": bybit_decimal_field_sha256_v1(value.mm_deduction_usdt),
        },
    )


def bybit_raw_manifest_sha256_v1(pages: tuple[BybitRiskLimitEnvelopeReadV1, ...]) -> str:
    decoded, _ = _raw_capture_parts_v1(pages)
    return _canonical_sha256_v2("BybitRiskLimitRawManifestV1", _raw_manifest_payload_v1(decoded))


def bybit_full_table_sha256_v1(table: BybitLinearTierTableV1) -> str:
    _table_v1(table)
    return _canonical_sha256_v2(
        "BybitLinearTierTableSemanticV1",
        _semantic_table_payload_v1(table.instrument_pin, table.rows),
    )


def bybit_tier_source_payload_sha256_v1(table: BybitLinearTierTableV1) -> str:
    _table_v1(table)
    return _canonical_sha256_v2(
        "BybitLinearTierSourcePayloadV1",
        _tier_source_payload_v1(
            table.instrument_pin, table.raw_manifest_sha256, table.full_table_sha256
        ),
    )


def bybit_tier_table_sha256_v1(value: BybitLinearTierTableV1) -> str:
    _table_v1(value)
    return _canonical_sha256_v2(
        "BybitLinearTierTableV1",
        {
            "instrument_pin": bybit_instrument_pin_sha256_v1(value.instrument_pin),
            "reference": _observation_payload_v2(value.reference),
            "pages": tuple(bybit_source_ref_sha256_v1(p) for p in value.pages),
            "rows": tuple(bybit_tier_row_sha256_v1(r) for r in value.rows),
            "raw_manifest_sha256": value.raw_manifest_sha256,
            "full_table_sha256": value.full_table_sha256,
            "rate_encoding_revision": value.rate_encoding_revision,
            "deduction_rule_revision": value.deduction_rule_revision,
        },
    )


def bybit_risk_limit_capture_sha256_v1(value: BybitRiskLimitCaptureV1) -> str:
    _capture_v1(value)
    return _canonical_sha256_v2(
        "BybitRiskLimitCaptureV1",
        {
            "table": bybit_tier_table_sha256_v1(value.table),
            "raw_pages": bybit_raw_manifest_sha256_v1(value.raw_pages),
        },
    )


def bybit_selected_profile_sha256_v1(value: BybitSelectedEntryProfileV1) -> str:
    _fields_v2(value, BybitSelectedEntryProfileV1)
    _validate_value_v1(value)
    for ids in (
        value.selected_open_order_ids,
        value.selected_conditional_order_ids,
        value.local_unresolved_entry_ids,
    ):
        if any(left >= right for left, right in pairwise(ids)):
            _reject_v1()
    sources = value.source_records
    if any(left.observation_id >= right.observation_id for left, right in pairwise(sources)):
        _reject_v1()
    return _canonical_sha256_v2(
        "BybitSelectedEntryProfileV1",
        {
            "revision": value.revision,
            "symbol": value.symbol,
            "position_idx": value.position_idx,
            "selected_position_quantity": bybit_decimal_field_sha256_v1(
                value.selected_position_quantity
            ),
            "selected_open_order_ids": value.selected_open_order_ids,
            "selected_conditional_order_ids": value.selected_conditional_order_ids,
            "local_unresolved_entry_ids": value.local_unresolved_entry_ids,
            "auto_add_margin": bybit_field_ref_sha256_v1(value.auto_add_margin),
            "prospective_extra_margin_usdt": decimal_text_v2(value.prospective_extra_margin_usdt),
            "extra_margin_basis": value.extra_margin_basis,
            "source_records": tuple(bybit_source_ref_sha256_v1(source) for source in sources),
        },
    )


@dataclass(frozen=True)
class BybitLinearPointInputV1:
    """Supplied local bounds, not original preparation or whole-account authority."""

    envelope: FixedLimitEnvelopeV2
    stop_price: Decimal
    selected_mark: BybitDecimalFieldV1
    selected_mark_reference: RiskObservationRefV2
    fee: BybitLinearFeeInputsV1
    table: BybitLinearTierTableV1
    profile: BybitSelectedEntryProfileV1
    planned_cost_allowance_quote: Decimal
    capacity_quote: Decimal
    liquidation_buffer_fraction: Decimal
    admission_upper_quote: Decimal
    admission_covered_tier_ids: tuple[str, ...]
    continuous_leverage_lower: Decimal
    continuous_leverage_upper: Decimal
    leverage_step: Decimal
    max_evidence_age_seconds: Decimal
    max_quote_age_seconds: Decimal
    valid_until: datetime
    model_rule_revision: Literal["BYBIT_LINEAR_ISOLATED_POINT_MODEL_V1"]

    def __post_init__(self) -> None:
        _point_payload_v1(self)
        object.__setattr__(self, "valid_until", _utc_v2(self.valid_until))


def _point_payload_v1(value: BybitLinearPointInputV1) -> dict[str, object]:
    _fields_v2(value, BybitLinearPointInputV1)
    # Validate all fixed children before equality, source links or numeric work.
    envelope = envelope_sha256_v2(value.envelope)
    mark = bybit_decimal_field_sha256_v1(value.selected_mark)
    reference = _observation_payload_v2(value.selected_mark_reference)
    fee = bybit_fee_inputs_sha256_v1(value.fee)
    table = bybit_tier_table_sha256_v1(value.table)
    profile = bybit_selected_profile_sha256_v1(value.profile)
    _decimal_v2(value.stop_price, source=True, sign="positive")
    if _decimal_field_v1(value.selected_mark) <= 0:
        _reject_v1()
    for amount in (
        value.planned_cost_allowance_quote,
        value.capacity_quote,
    ):
        _decimal_v2(amount, source=False, sign="nonnegative")
    for amount in (
        value.liquidation_buffer_fraction,
        value.max_evidence_age_seconds,
        value.max_quote_age_seconds,
    ):
        _decimal_v2(amount, source=True, sign="nonnegative")
    for amount in (
        value.admission_upper_quote,
        value.continuous_leverage_lower,
        value.continuous_leverage_upper,
        value.leverage_step,
    ):
        _decimal_v2(amount, source=False, sign="positive")
    if (
        decimal_fraction_v2(value.liquidation_buffer_fraction) >= 1
        or decimal_fraction_v2(value.continuous_leverage_lower) < 1
        or decimal_fraction_v2(value.continuous_leverage_upper)
        < decimal_fraction_v2(value.continuous_leverage_lower)
    ):
        _reject_v1()
    for identifier in _tuple_v1(value.admission_covered_tier_ids, 1024, minimum=1):
        _identifier_v2(identifier)
    if len(set(value.admission_covered_tier_ids)) != len(value.admission_covered_tier_ids):
        _reject_v1()
    _utc_v2(value.valid_until)
    _literal_v2(value.model_rule_revision, ("BYBIT_LINEAR_ISOLATED_POINT_MODEL_V1",))
    return {
        "envelope": envelope,
        "stop_price": decimal_text_v2(value.stop_price),
        "selected_mark": mark,
        "selected_mark_reference": reference,
        "fee": fee,
        "table": table,
        "profile": profile,
        "planned_cost_allowance_quote": decimal_text_v2(value.planned_cost_allowance_quote),
        "capacity_quote": decimal_text_v2(value.capacity_quote),
        "liquidation_buffer_fraction": decimal_text_v2(value.liquidation_buffer_fraction),
        "admission_upper_quote": decimal_text_v2(value.admission_upper_quote),
        "admission_covered_tier_ids": value.admission_covered_tier_ids,
        "continuous_leverage_lower": decimal_text_v2(value.continuous_leverage_lower),
        "continuous_leverage_upper": decimal_text_v2(value.continuous_leverage_upper),
        "leverage_step": decimal_text_v2(value.leverage_step),
        "max_evidence_age_seconds": decimal_text_v2(value.max_evidence_age_seconds),
        "max_quote_age_seconds": decimal_text_v2(value.max_quote_age_seconds),
        "valid_until": _time_text_v2(value.valid_until),
        "model_rule_revision": value.model_rule_revision,
    }


def bybit_point_input_sha256_v1(value: BybitLinearPointInputV1) -> str:
    return _canonical_sha256_v2("BybitLinearPointInputV1", _point_payload_v1(value))


@dataclass(frozen=True)
class BybitVenueStateV1:
    account_mode: BybitUtaModeV1
    flat_leverage: BybitFlatLeverageV1
    fee: BybitLinearFeeInputsV1
    wallet: BybitUsdtWalletV1
    held_positions: tuple[BybitHeldPositionV1, ...]
    held_orders: tuple[BybitHeldOrderV1, ...]
    tier_tables: tuple[BybitLinearTierTableV1, ...]
    selected_profile: BybitSelectedEntryProfileV1
    source_records: tuple[BybitModelSourceRefV1, ...]
    valid_until: datetime

    def __post_init__(self) -> None:
        _venue_payload_v1(self)


def _venue_payload_v1(value: BybitVenueStateV1) -> dict[str, object]:
    _fields_v2(value, BybitVenueStateV1)
    payload: dict[str, object] = {
        "account_mode": bybit_uta_mode_sha256_v1(value.account_mode),
        "flat_leverage": bybit_flat_leverage_sha256_v1(value.flat_leverage),
        "fee": bybit_fee_inputs_sha256_v1(value.fee),
        "wallet": bybit_wallet_sha256_v1(value.wallet),
        "selected_profile": bybit_selected_profile_sha256_v1(value.selected_profile),
        "valid_until": _time_text_v2(value.valid_until),
    }
    _tuple_v1(value.held_positions, 1024)
    _tuple_v1(value.held_orders, 1024)
    _tuple_v1(value.tier_tables, 100, minimum=1)
    _tuple_v1(value.source_records, 1024, minimum=1)
    payload["held_positions"] = tuple(
        bybit_held_position_sha256_v1(p) for p in value.held_positions
    )
    payload["held_orders"] = tuple(bybit_held_order_sha256_v1(o) for o in value.held_orders)
    payload["tier_tables"] = tuple(bybit_tier_table_sha256_v1(t) for t in value.tier_tables)
    payload["source_records"] = tuple(bybit_source_ref_sha256_v1(s) for s in value.source_records)
    position_order = tuple(
        (*c._key_order_v2(p.instrument_pin.key), p.position_idx) for p in value.held_positions
    )
    order_order = tuple(
        (*c._key_order_v2(o.instrument_pin.key), o.order_id) for o in value.held_orders
    )
    table_order = tuple(c._key_order_v2(t.instrument_pin.key) for t in value.tier_tables)
    source_order = tuple(s.observation_id for s in value.source_records)
    if (
        position_order != tuple(sorted(set(position_order)))
        or order_order != tuple(sorted(set(order_order)))
        or table_order != tuple(sorted(set(table_order)))
        or source_order != tuple(sorted(set(source_order)))
    ):
        _reject_v1()
    return payload


def bybit_venue_state_sha256_v1(value: BybitVenueStateV1) -> str:
    return _canonical_sha256_v2("BybitVenueStateV1", _venue_payload_v1(value))


def _catalog_input_sha256_v1(catalog: InstrumentCatalog) -> str:
    """Commit all validated A02 preimages, including non-risk products."""
    v = _fields_v2(catalog, InstrumentCatalog)
    if type(v["schema_version"]) is not int or v["schema_version"] != 1:
        _reject_v1()
    _digest_v2(catalog.revision)
    _utc_v2(catalog.expires_at)
    if type(catalog.max_age) is not timedelta or catalog.max_age <= timedelta(0):
        _reject_v1()
    _tuple_v1(catalog.declared_universe, 100, minimum=1)
    _tuple_v1(catalog.observations, 100, minimum=1)
    keys = tuple(_key_payload_v2(key) for key in catalog.declared_universe)
    try:
        # A02 alone owns row parsing, raw hash/spec revision and product rules.
        for observation in catalog.observations:
            validate_instrument_observation(observation)
        complete = catalog.complete_for_declared_universe
        if complete is not True:
            _reject_v1()
    except CatalogRejected:
        _reject_v1()
    observations = tuple(
        {
            "key": _key_payload_v2(o.key),
            "native_instrument_id": o.native_instrument_id,
            "parser_revision": o.parser_revision,
            "source_rule_revision": o.source_rule_revision,
            "specification_revision": o.specification_revision,
            "source": {
                "source_id": o.source.source_id,
                "source_environment": o.source.source_environment,
                "endpoint": o.source.endpoint,
                "received_at": _time_text_v2(o.source.received_at),
            },
            "response_time": _time_text_v2(o.response_time),
            "raw_row_sha256": o.raw_row_sha256,
        }
        for o in catalog.observations
    )
    return _canonical_sha256_v2(
        "BybitInstrumentCatalogInputsV1",
        {
            "schema_version": catalog.schema_version,
            "revision": catalog.revision,
            "declared_universe": keys,
            "max_age_microseconds": (catalog.max_age.days * 86400 + catalog.max_age.seconds)
            * 1000000
            + catalog.max_age.microseconds,
            "expires_at": _time_text_v2(catalog.expires_at),
            "observations": observations,
        },
    )


def project_bybit_tier_snapshot_v1(
    table: BybitLinearTierTableV1, *, instrument: InstrumentObservation
) -> e.LinearTierSnapshotV2:
    _table_v1(table)
    pin = c.pin_instrument_v2(instrument, catalog_revision=table.instrument_pin.catalog_revision)
    if _pin_payload_v2(pin) != _pin_payload_v2(table.instrument_pin):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    spec = instrument.spec
    if type(spec) is not LinearInstrumentSpec:
        _reject_v1("RISK_V2_UNSUPPORTED_PRODUCT")
    return e.LinearTierSnapshotV2(
        pin.key,
        table.reference,
        pin.specification_revision,
        tuple(
            e.LinearRiskTierV2(
                _integer_text_v1(row.risk_id),
                Decimal(0) if index == 0 else table.rows[index - 1].upper_inclusive_usdt.value,
                row.upper_inclusive_usdt.value,
                spec.min_leverage.value,
                row.max_leverage.value,
                row.initial_margin_rate.value,
                row.maintenance_margin_rate.value,
                row.mm_deduction_usdt.value,
            )
            for index, row in enumerate(table.rows)
        ),
    )


@dataclass(frozen=True)
class BybitLinearModelInputsV1:
    risk_inputs: e.LinearRiskInputsV2
    preparation: e.LinearEntryPreparationV2
    domain: d.LinearRiskDomainCoverageV2
    venue: BybitVenueStateV1
    point: BybitLinearPointInputV1
    catalog: InstrumentCatalog
    valid_until: datetime
    model_rule_revision: Literal["BYBIT_LINEAR_ISOLATED_POINT_MODEL_V1"]

    def __post_init__(self) -> None:
        _model_payload_v1(self)


def _economic_preparation_v1(value: e.LinearEntryPreparationV2) -> dict[str, object]:
    payload = e._preparation_payload_v2(value)
    # These are the only inherited audit exclusions.
    sizing = cast(dict[str, object], payload["sizing"])
    valuation = cast(dict[str, object], payload["valuation"])
    del sizing["assessed_at"]
    del valuation["assessed_at"]
    return payload


def _model_expiry_v1(
    inputs: e.LinearRiskInputsV2,
    preparation: e.LinearEntryPreparationV2,
    venue: BybitVenueStateV1,
    catalog: InstrumentCatalog,
) -> datetime:
    return min(
        _utc_v2(expiry)
        for expiry in (
            preparation.sizing.envelope.valid_until,
            inputs.proposal.entry.valid_until,
            inputs.costs.valid_until,
            inputs.costs.next_funding_at,
            inputs.costs.holding_until,
            inputs.minimum_amount.valid_until,
            venue.valid_until,
            catalog.expires_at,
        )
    )


def _project_point_v1(
    inputs: e.LinearRiskInputsV2,
    preparation: e.LinearEntryPreparationV2,
    venue: BybitVenueStateV1,
    selected_mark: BybitDecimalFieldV1,
    expiry: datetime,
) -> BybitLinearPointInputV1:
    marks = tuple(
        mark for mark in inputs.marks if mark.instrument == inputs.policy.scope.instrument
    )
    tables = tuple(
        table
        for table in venue.tier_tables
        if table.instrument_pin.key == inputs.policy.scope.instrument
    )
    if len(marks) != 1:
        _reject_v1("RISK_V2_MARK_COVERAGE_REQUIRED")
    if len(tables) != 1:
        _reject_v1("RISK_V2_HELD_COVERAGE_REQUIRED")
    mark = marks[0]
    bound = preparation.tier_bound
    return BybitLinearPointInputV1(
        preparation.sizing.envelope,
        preparation.sizing.stop_price,
        selected_mark,
        mark.reference,
        venue.fee,
        tables[0],
        venue.selected_profile,
        preparation.sizing.cost_allowance_quote,
        preparation.capacity_quote,
        inputs.policy.liquidation_buffer_fraction,
        bound.symbol_gross_upper_quote,
        bound.covered_tier_ids,
        bound.continuous_leverage_lower,
        bound.continuous_leverage_upper,
        bound.leverage_step,
        inputs.policy.max_evidence_age_seconds,
        inputs.policy.max_quote_age_seconds,
        expiry,
        "BYBIT_LINEAR_ISOLATED_POINT_MODEL_V1",
    )


def _model_payload_v1(value: BybitLinearModelInputsV1) -> dict[str, object]:
    _fields_v2(value, BybitLinearModelInputsV1)
    e._inputs_payload_v2(value.risk_inputs)
    e._preparation_payload_v2(value.preparation)
    domain_hash = d.risk_domain_sha256_v2(value.domain)
    venue_hash = bybit_venue_state_sha256_v1(value.venue)
    point_hash = bybit_point_input_sha256_v1(value.point)
    catalog_hash = _catalog_input_sha256_v1(value.catalog)
    expiry = _utc_v2(value.valid_until)
    _literal_v2(value.model_rule_revision, ("BYBIT_LINEAR_ISOLATED_POINT_MODEL_V1",))
    if (
        domain_hash != value.risk_inputs.risk_domain_sha256
        or domain_hash != value.preparation.binding.risk_domain_sha256
    ):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    if expiry != _model_expiry_v1(value.risk_inputs, value.preparation, value.venue, value.catalog):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    expected = _project_point_v1(
        value.risk_inputs, value.preparation, value.venue, value.point.selected_mark, expiry
    )
    if _point_payload_v1(expected) != _point_payload_v1(value.point):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    return {
        "binding_sha256": e.binding_sha256_v2(value.preparation.binding),
        "risk_domain_sha256": domain_hash,
        "requirements_sha256": e.requirements_sha256_v2(value.risk_inputs.requirements),
        "venue_state_sha256": venue_hash,
        "point_input_sha256": point_hash,
        "catalog_input_sha256": catalog_hash,
        "valid_until": _time_text_v2(expiry),
        "model_rule_revision": value.model_rule_revision,
    }


def bybit_model_inputs_sha256_v1(value: BybitLinearModelInputsV1) -> str:
    return _canonical_sha256_v2("BybitLinearModelInputsV1", _model_payload_v1(value))


def _venue_sources_v1(venue: BybitVenueStateV1) -> tuple[BybitModelSourceRefV1, ...]:
    sources = (
        venue.account_mode.source,
        venue.flat_leverage.source,
        venue.fee.behaviour.source,
        venue.fee.account_taker_rate.reference.source,
        venue.fee.account_maker_rate.reference.source,
        venue.wallet.source,
        *venue.selected_profile.source_records,
        *(page for table in venue.tier_tables for page in table.pages),
        *(p.source for p in venue.held_positions),
        *(o.source for o in venue.held_orders),
    )
    fee_ref = venue.fee.closing_order_reserve_nonvip_rate.reference
    if type(fee_ref) is BybitModelFieldRefV1:
        sources += (fee_ref.source,)
    return sources


def _venue_links_v1(
    inputs: e.LinearRiskInputsV2,
    domain: d.LinearRiskDomainCoverageV2,
    venue: BybitVenueStateV1,
    catalog: InstrumentCatalog,
    selected_mark: BybitDecimalFieldV1,
    *,
    now: datetime,
) -> None:
    profile, fee = venue.selected_profile, venue.fee
    if (
        profile.symbol != inputs.policy.scope.instrument.symbol
        or _decimal_field_v1(profile.selected_position_quantity) != 0
        or profile.selected_open_order_ids
        or profile.selected_conditional_order_ids
        or profile.local_unresolved_entry_ids
        or profile.auto_add_margin.wire_kind != "INTEGER"
        or profile.auto_add_margin.wire_spelling != "0"
        or decimal_fraction_v2(profile.prospective_extra_margin_usdt) != 0
        or fee.behaviour.lpa_perp is not True
        or fee.behaviour.futures_mnt_fee_deduction is not False
        or fee.behaviour.delta_enable is not False
    ):
        _reject_v1("RISK_V2_UNSUPPORTED_MODE")
    if (
        profile.selected_position_quantity.reference.source not in profile.source_records
        or profile.auto_add_margin.source not in profile.source_records
    ):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    for rate in (
        fee.account_taker_rate.value,
        fee.account_maker_rate.value,
        fee.closing_order_reserve_nonvip_rate.value,
    ):
        if not 0 <= decimal_fraction_v2(rate) < 1:
            _reject_v1("RISK_V2_MARGIN_UNVERIFIED")
    nonvip = fee.closing_order_reserve_nonvip_rate.reference
    if type(nonvip) is BybitFeeDocumentRefV1:
        if nonvip.applicability_revision != fee.nonvip_applicability_revision:
            _reject_v1("RISK_V2_REVISION_MISMATCH")
        c._fresh_v2(
            nonvip.captured_at,
            now=now,
            maximum=decimal_fraction_v2(inputs.policy.max_evidence_age_seconds),
        )
    required = tuple(member.instrument_pin.key for member in domain.members)
    if tuple(t.instrument_pin.key for t in venue.tier_tables) != required:
        _reject_v1("RISK_V2_HELD_COVERAGE_REQUIRED")
    observations = {o.key: o for o in catalog.observations}
    for member, table in zip(domain.members, venue.tier_tables, strict=True):
        if _pin_payload_v2(table.instrument_pin) != _pin_payload_v2(member.instrument_pin):
            _reject_v1("RISK_V2_SOURCE_MISMATCH")
        projected = project_bybit_tier_snapshot_v1(
            table, instrument=observations[member.instrument_pin.key]
        )
        if e._tier_snapshot_payload_v2(projected) != e._tier_snapshot_payload_v2(member.tiers):
            _reject_v1("RISK_V2_SOURCE_MISMATCH")
    selected = inputs.policy.scope.instrument
    table = next(t for t in venue.tier_tables if t.instrument_pin.key == selected)
    if e.tier_snapshot_sha256_v2(
        project_bybit_tier_snapshot_v1(table, instrument=inputs.instrument)
    ) != e.tier_snapshot_sha256_v2(inputs.tiers):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    mark = next(m for m in inputs.marks if m.instrument == selected)
    source = selected_mark.reference.source
    if _decimal_field_v1(selected_mark) != decimal_fraction_v2(mark.mark_price) or (
        source.account_scope is not None
        or dict(source.query).get("category") != "linear"
        or dict(source.query).get("symbol") != selected.symbol
        or source.observation_id != mark.reference.observation_id
        or source.source_id != mark.reference.source_id
        or source.source_environment != mark.reference.source_environment
        or _utc_v2(source.received_at) != _utc_v2(mark.reference.received_at)
        or source.envelope_time_ms is None
        or _EPOCH + timedelta(milliseconds=source.envelope_time_ms)
        != _utc_v2(mark.reference.server_observed_at)
    ):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    all_sources = (*_venue_sources_v1(venue), source)
    records = {s.observation_id: s for s in venue.source_records}
    generations: dict[str, str] = {}
    for s in (*venue.source_records, *all_sources):
        if s.observation_id not in records or bybit_source_ref_sha256_v1(
            records[s.observation_id]
        ) != bybit_source_ref_sha256_v1(s):
            _reject_v1("RISK_V2_SOURCE_MISMATCH")
        if generations.setdefault(s.source_id, s.source_generation) != s.source_generation:
            _reject_v1("RISK_V2_SOURCE_MISMATCH")
        if s.account_scope is not None and s.account_scope != inputs.account.account:
            _reject_v1("RISK_V2_SCOPE_MISMATCH")
        if (table.instrument_pin.source_environment == "FIXTURE") != (
            s.source_environment == "FIXTURE"
        ):
            _reject_v1("RISK_V2_SOURCE_MISMATCH")
        maximum = decimal_fraction_v2(
            inputs.policy.max_quote_age_seconds
            if s.observation_id == source.observation_id
            else inputs.policy.max_evidence_age_seconds
        )
        for stamp in (s.request_started_at, s.received_at):
            c._fresh_v2(stamp, now=now, maximum=maximum)
        if s.envelope_time_ms is not None:
            c._fresh_v2(
                _EPOCH + timedelta(milliseconds=s.envelope_time_ms), now=now, maximum=maximum
            )
    for s in (
        *profile.source_records,
        fee.account_taker_rate.reference.source,
        fee.account_maker_rate.reference.source,
        venue.flat_leverage.source,
    ):
        query = dict(s.query)
        if ("symbol" in query and query["symbol"] != selected.symbol) or (
            "category" in query and query["category"] != "linear"
        ):
            _reject_v1("RISK_V2_SOURCE_MISMATCH")
    scope = inputs.account.account
    for s in (
        venue.account_mode.source,
        venue.flat_leverage.source,
        venue.wallet.source,
        *venue.selected_profile.source_records,
        venue.fee.behaviour.source,
        venue.fee.account_taker_rate.reference.source,
        venue.fee.account_maker_rate.reference.source,
        *(p.source for p in venue.held_positions),
        *(o.source for o in venue.held_orders),
    ):
        if s.account_scope != scope:
            _reject_v1("RISK_V2_SCOPE_MISMATCH")
    if type(nonvip) is BybitModelFieldRefV1 and nonvip.source.account_scope != scope:
        _reject_v1("RISK_V2_SCOPE_MISMATCH")
    if (
        venue.flat_leverage.symbol != selected.symbol
        or venue.flat_leverage.source.path != venue.flat_leverage.read_path
    ):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    if venue.flat_leverage.leverage.reference.source != venue.flat_leverage.source:
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    for name in (
        "wallet_balance",
        "equity",
        "total_position_im",
        "total_order_im",
        "locked",
        "bonus",
        "borrow_amount",
        "spot_borrow",
        "accrued_interest",
        "unrealised_pnl",
    ):
        if (
            cast(BybitDecimalFieldV1, getattr(venue.wallet, name)).reference.source
            != venue.wallet.source
        ):
            _reject_v1("RISK_V2_SOURCE_MISMATCH")
    if any(
        _decimal_field_v1(f) != 0
        for f in (
            venue.wallet.borrow_amount,
            venue.wallet.spot_borrow,
            venue.wallet.accrued_interest,
        )
    ):
        _reject_v1("RISK_V2_UNSUPPORTED_MODE")
    free = _decimal_field_v1(venue.wallet.wallet_balance)
    for amount in (
        venue.wallet.total_position_im,
        venue.wallet.total_order_im,
        venue.wallet.locked,
        venue.wallet.bonus,
    ):
        free = _fraction(free - _decimal_field_v1(amount))
    if _compare(free, decimal_fraction_v2(inputs.account.available_margin)) != 0:
        _reject_v1("RISK_V2_HELD_COVERAGE_REQUIRED")
    for p in venue.held_positions:
        if (
            p.side not in ("Buy", "Sell")
            or p.position_idx != 0
            or p.position_status != "Normal"
            or p.auto_add_margin != 0
        ):
            _reject_v1("RISK_V2_UNSUPPORTED_MODE")
        if p.instrument_pin.key not in required:
            _reject_v1("RISK_V2_HELD_COVERAGE_REQUIRED")
        if _pin_payload_v2(p.instrument_pin) != _pin_payload_v2(
            c.pin_instrument_v2(
                observations[p.instrument_pin.key], catalog_revision=catalog.revision
            )
        ):
            _reject_v1("RISK_V2_SOURCE_MISMATCH")
        for name in (
            "size",
            "avg_price",
            "mark_price",
            "leverage",
            "position_im",
            "position_im_by_mp",
            "position_mm",
            "position_mm_by_mp",
            "risk_limit_value",
            "liq_price",
        ):
            field = cast(BybitDecimalFieldV1 | None, getattr(p, name))
            if field is not None and field.reference.source != p.source:
                _reject_v1("RISK_V2_SOURCE_MISMATCH")
    for o in venue.held_orders:
        if (
            o.side not in ("Buy", "Sell")
            or o.order_type not in ("Limit", "Market")
            or o.status not in ("New", "PartiallyFilled", "Untriggered")
            or o.position_idx != 0
        ):
            _reject_v1("RISK_V2_UNSUPPORTED_MODE")
        if o.instrument_pin.key not in observations:
            _reject_v1("RISK_V2_HELD_COVERAGE_REQUIRED")
        if _pin_payload_v2(o.instrument_pin) != _pin_payload_v2(
            c.pin_instrument_v2(
                observations[o.instrument_pin.key], catalog_revision=catalog.revision
            )
        ):
            _reject_v1("RISK_V2_SOURCE_MISMATCH")
        for field in (o.price, o.quantity, o.leaves_quantity, o.trigger_price):
            if field is not None and field.reference.source != o.source:
                _reject_v1("RISK_V2_SOURCE_MISMATCH")
    for key in required:
        for side, raw_side in (("BUY", "Buy"), ("SELL", "Sell")):
            generic = Fraction(0)
            raw = Fraction(0)
            for component in inputs.account.components:
                if (
                    component.instrument == key
                    and component.origin == "ACCOUNT_POSITION"
                    and component.side == side
                ):
                    generic = _fraction(generic + decimal_fraction_v2(component.base_quantity))
            for p in venue.held_positions:
                if p.instrument_pin.key == key and p.side == raw_side:
                    raw = _fraction(raw + _decimal_field_v1(p.size))
            if _compare(generic, raw) != 0:
                _reject_v1("RISK_V2_HELD_COVERAGE_REQUIRED")
    if (
        any(p.instrument_pin.key == selected for p in venue.held_positions)
        or any(o.instrument_pin.key == selected for o in venue.held_orders)
        or any(
            component.instrument == selected
            for component in (*inputs.account.components, *inputs.reservations.components)
        )
    ):
        _reject_v1("RISK_V2_UNSUPPORTED_MODE")


def bind_bybit_model_inputs_v1(
    inputs: e.LinearRiskInputsV2,
    *,
    preparation: e.LinearEntryPreparationV2,
    domain: d.LinearRiskDomainCoverageV2,
    venue: BybitVenueStateV1,
    catalog: InstrumentCatalog,
    selected_mark: BybitDecimalFieldV1,
    now: datetime,
) -> BybitLinearModelInputsV1:
    e._inputs_payload_v2(inputs)
    e._preparation_payload_v2(preparation)
    d.risk_domain_sha256_v2(domain)
    _venue_payload_v1(venue)
    _catalog_input_sha256_v1(catalog)
    _decimal_field_v1(selected_mark)
    now = _utc_v2(now)
    actual = r.prepare_linear_entry_v2(inputs, now=now)
    if _economic_preparation_v1(actual) != _economic_preparation_v1(preparation):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    rebuilt = d.build_linear_risk_domain_coverage_v2(
        policy=inputs.policy,
        catalog=catalog,
        catalog_revision=inputs.catalog_revision,
        coverage_revision=domain.coverage_revision,
        source_generation=domain.source_generation,
        account=inputs.account,
        reservations=inputs.reservations,
        marks=inputs.marks,
        members=domain.members,
        source_record_sha256s=domain.source_record_sha256s,
        now=now,
    )
    if d.risk_domain_sha256_v2(rebuilt) != inputs.risk_domain_sha256 or d.risk_domain_sha256_v2(
        rebuilt
    ) != d.risk_domain_sha256_v2(domain):
        _reject_v1("RISK_V2_SOURCE_MISMATCH")
    if inputs.requirements.cost_assessment_basis != "POINT_ESTIMATE_WITH_RECONCILIATION_V1":
        _reject_v1("RISK_V2_MARGIN_UNVERIFIED")
    _venue_links_v1(inputs, domain, venue, catalog, selected_mark, now=now)
    expiry = _model_expiry_v1(inputs, actual, venue, catalog)
    if now >= expiry:
        _reject_v1("RISK_V2_EXPIRED")
    point = _project_point_v1(inputs, actual, venue, selected_mark, expiry)
    return BybitLinearModelInputsV1(
        inputs,
        actual,
        domain,
        venue,
        point,
        catalog,
        expiry,
        "BYBIT_LINEAR_ISOLATED_POINT_MODEL_V1",
    )
