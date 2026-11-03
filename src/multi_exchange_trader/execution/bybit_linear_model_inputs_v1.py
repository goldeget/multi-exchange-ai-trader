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
from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.execution.risk_v2_contracts import (
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
)
from multi_exchange_trader.execution.risk_v2_math import (
    decimal_fraction_v2,
    decimal_text_v2,
    finite_decimal_v2,
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
