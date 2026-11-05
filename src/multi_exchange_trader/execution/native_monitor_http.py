"""Bounded session GETs converted through pinned public native constructors.

The 0.1 instrument margins and unit multiplier are SDK 1.231.0 representation
constants, never venue margin/leverage evidence. No Rust HTTP client is created.
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Callable
from datetime import timedelta
from decimal import Context, Decimal, Inexact, Rounded, localcontext
from typing import Any, NoReturn

from ..account.instrument_envelope import (
    decode_instrument_envelope,
    validate_instrument_envelope_read,
)
from ..market_data.instrument_source_projection import (
    project_supported_instrument_row,
    validate_supported_instrument_projection,
)
from ..trading_product import InstrumentKey
from .account_binding import AuthenticatedAccountBinding, BybitDemoAccountSession
from .monitoring_budget import MonitoringReadBudget
from .monitoring_models import MonitoringError
from .native_events import bounded_decimal
from .native_instrument_receipts import (
    NativeFeeResponseV1,
    NativeInstrumentEvidenceV1,
    NativeUnknownFeeInstrumentEvidenceV1,
    _PendingNativeInstrumentCaptureV1,
    require_native_matches_projection,
    require_native_observation_matches_projection,
)
from .observation_instrument_v1 import (
    ACTUAL_ACCOUNT_FEE_V1,
    SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
    convert_observation_instrument_v1,
    validate_metadata_profile_v1,
)
from .protection import ProtectionScope


def _number(value: object, *, positive: bool = False) -> Decimal:
    if type(value) is not str or not value:
        raise MonitoringError("MONITORING_METADATA_AMOUNT_REQUIRED")
    return bounded_decimal(value, positive=positive)


def _currency(code: object) -> Any:
    from nautilus_trader.core import nautilus_pyo3 as p

    Currency = importlib.import_module("nautilus_trader.model.objects").Currency

    if type(code) is not str or not code or len(code) > 32:
        raise MonitoringError("MONITORING_KNOWN_CURRENCY_REQUIRED")
    native = Currency.from_str(code, strict=True)
    if native is None:
        raise MonitoringError("MONITORING_KNOWN_CURRENCY_REQUIRED")
    currency = p.Currency.from_str(code)
    if any(
        str(getattr(currency, key)) != str(getattr(native, key))
        for key in ("code", "precision", "iso4217", "name")
    ):
        raise MonitoringError("MONITORING_CURRENCY_SEMANTICS")
    if currency.currency_type.name != native.currency_type.name:
        raise MonitoringError("MONITORING_CURRENCY_SEMANTICS")
    return currency


def _money(value: Decimal, currency: Any) -> Any:
    p = importlib.import_module("nautilus_trader.core.nautilus_pyo3")

    result = p.Money.from_decimal(value, currency)
    if result.as_decimal() != value:
        raise MonitoringError("MONITORING_MONEY_PRECISION")
    return result


def _instrument_parameters_v1(row: dict[str, Any], symbol: str, stamp_ms: int) -> dict[str, Any]:
    # Runtime public signatures include info, unlike the installed .pyi.
    p = importlib.import_module("nautilus_trader.core.nautilus_pyo3")

    if (
        row.get("symbol") != symbol
        or row.get("contractType") != "LinearPerpetual"
        or row.get("status") != "Trading"
        or row.get("quoteCoin") != row.get("settleCoin")
        or type(stamp_ms) is not int
        or stamp_ms <= 0
    ):
        raise MonitoringError("MONITORING_INSTRUMENT_SCOPE")
    base, quote, settlement = (
        _currency(row.get(key)) for key in ("baseCoin", "quoteCoin", "settleCoin")
    )
    prices, sizes = row.get("priceFilter"), row.get("lotSizeFilter")
    if type(prices) is not dict or type(sizes) is not dict:
        raise MonitoringError("MONITORING_INSTRUMENT_FILTERS")
    tick = _number(prices.get("tickSize"), positive=True)
    step = _number(sizes.get("qtyStep"), positive=True)
    price_precision = max(0, -int(tick.as_tuple().exponent))
    size_precision = max(0, -int(step.as_tuple().exponent))
    price_increment, size_increment = (
        p.Price.from_str(prices["tickSize"]),
        p.Quantity.from_str(sizes["qtyStep"]),
    )
    if (
        price_increment.as_decimal() != tick
        or size_increment.as_decimal() != step
        or price_increment.precision != price_precision
        or size_increment.precision != size_precision
    ):
        raise MonitoringError("MONITORING_INCREMENT_PRECISION")
    max_quantity = _number(sizes.get("maxOrderQty"), positive=True)
    min_quantity = _number(sizes.get("minOrderQty"), positive=True)
    min_price = _number(prices.get("minPrice"), positive=True)
    max_price = _number(prices.get("maxPrice"), positive=True)
    if min_quantity > max_quantity or min_price > max_price:
        raise MonitoringError("MONITORING_INSTRUMENT_LIMITS")
    return {
        "instrument_id": p.InstrumentId.from_str(symbol + "-LINEAR.BYBIT"),
        "raw_symbol": p.Symbol(symbol),
        "base_currency": base,
        "quote_currency": quote,
        "settlement_currency": settlement,
        "is_inverse": False,
        "price_precision": price_precision,
        "size_precision": size_precision,
        "price_increment": price_increment,
        "size_increment": size_increment,
        "ts_event": stamp_ms * 1_000_000,
        "ts_init": stamp_ms * 1_000_000,
        "multiplier": p.Quantity.from_str("1"),
        "lot_size": size_increment,
        "max_quantity": p.Quantity.from_str(str(max_quantity)),
        "min_quantity": p.Quantity.from_str(str(min_quantity)),
        "max_notional": None,
        "min_notional": _money(_number(sizes.get("minNotionalValue"), positive=True), quote),
        "max_price": p.Price.from_str(str(max_price)),
        "min_price": p.Price.from_str(str(min_price)),
        "margin_init": Decimal("0.1"),
        "margin_maint": Decimal("0.1"),
        "tick_scheme": None,
    }


def convert_instrument(row: dict[str, Any], fee: dict[str, Any], symbol: str, stamp_ms: int) -> Any:
    if fee.get("symbol") != symbol:
        raise MonitoringError("MONITORING_INSTRUMENT_SCOPE")
    p = importlib.import_module("nautilus_trader.core.nautilus_pyo3")
    return p.CryptoPerpetual(**_instrument_parameters_v1(row, symbol, stamp_ms),
        maker_fee=_number(fee.get("makerFeeRate")),
        taker_fee=_number(fee.get("takerFeeRate")), info=None)


def convert_account(row: dict[str, Any], account_id: Any, stamp_ms: int) -> Any:
    p = importlib.import_module("nautilus_trader.core.nautilus_pyo3")

    coins = row.get("coin")
    if (
        row.get("accountType") != "UNIFIED"
        or type(coins) is not list
        or not coins
        or len(coins) > 200
        or type(account_id) is not p.AccountId
        or type(stamp_ms) is not int
        or stamp_ms <= 0
    ):
        raise MonitoringError("MONITORING_WALLET_SHAPE")
    balances, margins, seen = [], [], set()
    for coin in coins:
        if type(coin) is not dict or coin.get("coin") in seen:
            raise MonitoringError("MONITORING_WALLET_CURRENCY_DUPLICATE")
        currency = _currency(coin.get("coin"))
        seen.add(coin["coin"])
        # Validate each original amount before arithmetic can erase unsupported
        # digits. Use the verified currency scale and a sufficient exact context.
        wallet, borrowed, locked, position_im, order_im, maintenance = values = tuple(
            _money(_number(coin.get(key)), currency).as_decimal()
            for key in (
                "walletBalance",
                "spotBorrow",
                "locked",
                "totalPositionIM",
                "totalOrderIM",
                "totalPositionMM",
            )
        )
        precision = max(1, *(value.adjusted() + 1 for value in values)) + currency.precision + 2
        with localcontext(Context(prec=precision, traps=[Inexact, Rounded])):
            total, initial, free = (
                wallet - borrowed,
                position_im + order_im,
                wallet - borrowed - locked,
            )
        if min(total, borrowed, locked, position_im, order_im, maintenance) < 0 or locked > total:
            raise MonitoringError("MONITORING_WALLET_ECONOMICS")
        balances.append(
            p.AccountBalance(
                _money(total, currency), _money(locked, currency), _money(free, currency)
            )
        )
        if initial != 0 or maintenance != 0:
            margins.append(
                p.MarginBalance(_money(initial, currency), _money(maintenance, currency), None)
            )
    return p.AccountState(
        account_id=account_id,
        account_type=p.AccountType.MARGIN,
        balances=balances,
        margins=margins,
        is_reported=True,
        event_id=p.UUID4(),
        ts_event=stamp_ms * 1_000_000,
        ts_init=stamp_ms * 1_000_000,
        base_currency=None,
    )


class MonitoringHttpAdapter:
    """Default-deny adapter; every physical attempt uses the exact session budget."""

    def __init__(
        self,
        *,
        session: BybitDemoAccountSession,
        binding: AuthenticatedAccountBinding,
        scope: ProtectionScope,
        symbols: tuple[str, ...],
        fault: Callable[[str], None],
        metadata_profile: str = ACTUAL_ACCOUNT_FEE_V1,
    ) -> None:
        metadata_profile = validate_metadata_profile_v1(metadata_profile)
        session.require_observation_binding(binding, scope=scope)
        self._session, self._binding, self._scope = session, binding, scope
        self._symbols, self._fault = symbols, fault
        self._instruments: dict[str, Any] = {}
        self._cancelled = False
        self._native_capture_slot: _PendingNativeInstrumentCaptureV1 | None = None
        self._metadata_profile = metadata_profile

    def __repr__(self) -> str:
        return "MonitoringHttpAdapter(GET_only=True)"

    def _deny(self) -> NoReturn:
        self._fault("MONITORING_HTTP_DENIED")
        raise MonitoringError("MONITORING_HTTP_DENIED")

    def __getattr__(self, name: str) -> Any:
        self._deny()

    @property
    def api_key_masked(self) -> str:
        return "[monitoring-session]"

    def set_use_spot_position_reports(self, value: bool) -> None:
        if value is not False:
            self._deny()

    def cache_instrument(self, instrument: Any) -> None:
        from nautilus_trader.core import nautilus_pyo3 as p

        if type(instrument) is not p.CryptoPerpetual or str(instrument.id) not in {
            s + "-LINEAR.BYBIT" for s in self._symbols
        }:
            self._deny()
        self._instruments[str(instrument.id)] = instrument

    def cancel_all_requests(self) -> None:
        self._cancelled = True
        self._session._revoke_native_instrument_capture_v1(self._native_capture_slot)

    async def _get(
        self, path: str, params: list[tuple[str, str]], *, signed: bool
    ) -> tuple[dict[str, Any], int]:
        slot = self._native_capture_slot
        try:
            group_before = None
            physical_before = None
            if slot is not None:
                registry = self._session._native_instrument_receipts_v1
                registry.require(slot, phases=("CACHED",))
                group_before = slot.budget.require_active_group(slot.group)
                physical_before = slot.budget.physical_attempts
                if path != "/v5/account/wallet-balance" or slot.wallet_reads != 0:
                    registry.revoke(slot)
                    self._deny()
                if (
                    group_before.logical_gets != slot.last_logical
                    or physical_before != slot.last_physical
                ):
                    registry.revoke(slot)
                    self._deny()
                # Reserve the one wallet read before I/O so a retry or duplicate
                # call cannot create another GET under the same T4 receipt.
                slot.wallet_reads = 1
            if path not in (
                "/v5/account/fee-rate",
                "/v5/market/instruments-info",
                "/v5/account/wallet-balance",
            ):
                self._deny()
            if path == "/v5/account/wallet-balance":
                if params != [("accountType", "UNIFIED")] or signed is not True:
                    self._deny()
            elif params not in [
                [("category", "linear"), ("symbol", symbol)] for symbol in self._symbols
            ] or signed is not (path == "/v5/account/fee-rate"):
                self._deny()
            if self._cancelled:
                self._deny()
            client = self._session.read_client(self._binding, scope=self._scope)
            budget = client._operation.get()
            if type(budget) is not MonitoringReadBudget:
                self._deny()
            result, stamp = await client.read(path, params, signed=signed, deadline=budget.deadline)
            self._session.require_observation_binding(self._binding, scope=self._scope)
            if slot is not None:
                assert group_before is not None and physical_before is not None
                registry.require(slot, phases=("CACHED",))
                group_after = slot.budget.require_active_group(slot.group)
                physical_after = slot.budget.physical_attempts
                if (
                    group_after.logical_gets != group_before.logical_gets + 1
                    or not 1 <= physical_after - physical_before <= 3
                ):
                    registry.revoke(slot)
                    self._deny()
                slot.last_logical = group_after.logical_gets
                slot.last_physical = physical_after
            if self._cancelled or type(stamp) is not int or stamp <= 0:
                self._deny()
            from .history import EPOCH, utc

            if (
                not timedelta(0)
                <= utc(self._session._clock()) - (EPOCH + timedelta(milliseconds=stamp))
                <= min(self._session._max_age, timedelta(seconds=30))
            ):
                self._deny()
            return result, stamp
        except asyncio.CancelledError:
            self._fault("MONITORING_HTTP_FAILED")
            raise
        except Exception:  # noqa: BLE001 - transport diagnostics must not escape.
            self._fault("MONITORING_HTTP_FAILED")
        # Outside the handler: do not retain the original exception context.
        raise MonitoringError("MONITORING_HTTP_FAILED") from None

    async def request_instruments(
        self, product_type: Any, symbol: str | None, base_coin: str | None = None
    ) -> list[Any]:
        from nautilus_trader.core import nautilus_pyo3 as p

        if (
            product_type != p.BybitProductType.LINEAR
            or symbol not in self._symbols
            or base_coin is not None
        ):
            self._deny()
        assert symbol is not None
        params = [("category", "linear"), ("symbol", symbol)]
        validate_metadata_profile_v1(self._metadata_profile)
        if self._native_capture_slot is not None:
            if self._metadata_profile == ACTUAL_ACCOUNT_FEE_V1:
                return await self._request_bound_instrument_v1(
                    symbol, self._native_capture_slot
                )
            if self._metadata_profile == SDK1231_OBSERVATION_UNKNOWN_FEES_V1:
                return await self._request_bound_unknown_fee_instrument_v1(
                    symbol, self._native_capture_slot
                )
            else:
                self._session._revoke_native_instrument_capture_v1(self._native_capture_slot)
                self._deny()
        if self._metadata_profile == SDK1231_OBSERVATION_UNKNOWN_FEES_V1:
            return await self._request_observation_instruments_v1(symbol, params)
        fees, _ = await self._get("/v5/account/fee-rate", params, signed=True)
        data, stamp = await self._get("/v5/market/instruments-info", params, signed=False)
        rows, fee_rows = data.get("list"), fees.get("list")
        if (
            data.get("category") != "linear"
            or fees.get("category", "linear") != "linear"
            or data.get("nextPageCursor") != ""
            or type(rows) is not list
            or len(rows) != 1
            or type(fee_rows) is not list
            or len(fee_rows) != 1
            or type(rows[0]) is not dict
            or type(fee_rows[0]) is not dict
        ):
            self._deny()
        try:
            return [convert_instrument(rows[0], fee_rows[0], symbol, stamp)]
        except asyncio.CancelledError:
            self._fault("MONITORING_METADATA_INVALID")
            raise
        except Exception:  # noqa: BLE001 - SDK errors may contain original payloads.
            self._fault("MONITORING_METADATA_INVALID")
        raise MonitoringError("MONITORING_METADATA_INVALID") from None

    async def _request_bound_instrument_v1(
        self, symbol: str, slot: _PendingNativeInstrumentCaptureV1,
    ) -> list[Any]:
        registry = self._session._native_instrument_receipts_v1
        try:
            registry.begin_symbol(slot, symbol)
            fee: NativeFeeResponseV1 = await self._session._capture_native_fee_response_v1(
                slot, symbol=symbol
            )
            registry.record_fee(slot, fee)
            registry.require(slot, phases=("INITIALIZING",))
            client = self._session.read_client(self._binding, scope=self._scope)
            if client._operation.get() is not slot.budget:
                self._deny()
            envelope = await client.read_instrument_envelope(
                InstrumentKey("BYBIT", "LINEAR", symbol),
                budget=slot.budget,
                group=slot.group,
            )
            registry.require(slot, phases=("INITIALIZING",))
            validate_instrument_envelope_read(envelope)
            body, _canonical, response_time = decode_instrument_envelope(
                envelope.decoded_body_bytes
            )
            registry.require(slot, phases=("INITIALIZING",))
            result = body.get("result")
            if (
                body.get("retCode") != 0
                or type(body.get("retCode")) is not int
                or type(result) is not dict
            ):
                self._deny()
            rows = result.get("list")
            stamp_ms = body.get("time")
            if (
                result.get("category") != "linear"
                or result.get("nextPageCursor") != ""
                or type(rows) is not list
                or len(rows) != 1
                or type(rows[0]) is not dict
                or type(stamp_ms) is not int
                or stamp_ms <= 0
                or response_time != envelope.response_time
            ):
                self._deny()
            key = InstrumentKey("BYBIT", "LINEAR", symbol)
            projection = project_supported_instrument_row(rows[0], expected=key)
            validate_supported_instrument_projection(projection)
            registry.require(slot, phases=("INITIALIZING",))
            fee_row = {
                "symbol": symbol,
                "makerFeeRate": fee.maker_fee_rate,
                "takerFeeRate": fee.taker_fee_rate,
            }
            instrument = convert_instrument(rows[0], fee_row, symbol, stamp_ms)
            registry.require(slot, phases=("INITIALIZING",))
            native_digest = require_native_matches_projection(projection, instrument, fee)
            evidence = NativeInstrumentEvidenceV1(
                key, envelope, projection, fee, native_digest
            )
            registry.record_instrument(slot, evidence)
            return [instrument]
        except asyncio.CancelledError:
            registry.revoke(slot)
            self._fault("MONITORING_METADATA_INVALID")
            raise
        except Exception:  # noqa: BLE001 - original source and SDK details stay private.
            registry.revoke(slot)
            self._fault("MONITORING_METADATA_INVALID")
        raise MonitoringError("MONITORING_METADATA_INVALID") from None

    async def _request_bound_unknown_fee_instrument_v1(
        self, symbol: str, slot: _PendingNativeInstrumentCaptureV1,
    ) -> list[Any]:
        registry = self._session._native_instrument_receipts_v1
        try:
            registry.begin_symbol(slot, symbol)
            key = InstrumentKey("BYBIT", "LINEAR", symbol)
            client = self._session.read_client(self._binding, scope=self._scope)
            if (
                client._operation.get() is not slot.budget
                or self._metadata_profile != SDK1231_OBSERVATION_UNKNOWN_FEES_V1
            ):
                self._deny()
            # This is the same non-injected, owner-bound DEMO client used by the
            # accepted wallet read. The endpoint is public (unsigned); the exact
            # source envelope consumes the current group and has no fee response.
            envelope = await client.read_instrument_envelope(
                key, budget=slot.budget, group=slot.group
            )
            registry.require(slot, phases=("INITIALIZING",))
            validate_instrument_envelope_read(envelope)
            if (
                envelope.key != key
                or envelope.sent_origin.scheme != "https"
                or envelope.sent_origin.host != "api-demo.bybit.com"
                or envelope.sent_origin.port != 443
                or envelope.response_origin != envelope.sent_origin
                or envelope.response_status != 200
            ):
                self._deny()
            body, _canonical, response_time = decode_instrument_envelope(
                envelope.decoded_body_bytes
            )
            registry.require(slot, phases=("INITIALIZING",))
            result = body.get("result")
            if (
                body.get("retCode") != 0
                or type(body.get("retCode")) is not int
                or type(result) is not dict
            ):
                self._deny()
            rows = result.get("list")
            stamp_ms = body.get("time")
            if (
                result.get("category") != "linear"
                or result.get("nextPageCursor") != ""
                or type(rows) is not list
                or len(rows) != 1
                or type(rows[0]) is not dict
                or type(stamp_ms) is not int
                or stamp_ms <= 0
                or response_time != envelope.response_time
            ):
                self._deny()
            projection = project_supported_instrument_row(rows[0], expected=key)
            validate_supported_instrument_projection(projection)
            registry.require(slot, phases=("INITIALIZING",))
            instrument = convert_observation_instrument_v1(rows[0], symbol, stamp_ms)
            registry.require(slot, phases=("INITIALIZING",))
            native_digest = require_native_observation_matches_projection(
                projection, instrument
            )
            evidence = NativeUnknownFeeInstrumentEvidenceV1(
                key,
                envelope,
                projection,
                SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
                native_digest,
            )
            registry.record_unknown_fee_instrument(slot, evidence)
            return [instrument]
        except asyncio.CancelledError:
            registry.revoke(slot)
            self._fault("MONITORING_METADATA_INVALID")
            raise
        except Exception:  # noqa: BLE001 - source and SDK details remain private.
            registry.revoke(slot)
            self._fault("MONITORING_METADATA_INVALID")
        raise MonitoringError("MONITORING_METADATA_INVALID") from None

    async def _request_observation_instruments_v1(
        self, symbol: str, params: list[tuple[str, str]],
    ) -> list[Any]:
        data, stamp = await self._get("/v5/market/instruments-info", params, signed=False)
        rows = data.get("list")
        if (data.get("category") != "linear" or data.get("nextPageCursor") != ""
                or type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict):
            self._deny()
        try:
            return [convert_observation_instrument_v1(rows[0], symbol, stamp)]
        except asyncio.CancelledError:
            self._fault("MONITORING_METADATA_INVALID")
            raise
        except Exception:  # noqa: BLE001 - native errors may contain original payloads.
            self._fault("MONITORING_METADATA_INVALID")
        raise MonitoringError("MONITORING_METADATA_INVALID") from None

    async def request_account_state(self, *, account_type: Any, account_id: Any) -> Any:
        from nautilus_trader.core import nautilus_pyo3 as p

        if account_type != p.BybitAccountType.UNIFIED or str(account_id) != "BYBIT-UNIFIED":
            self._deny()
        data, stamp = await self._get(
            "/v5/account/wallet-balance", [("accountType", "UNIFIED")], signed=True
        )
        rows = data.get("list")
        if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
            self._deny()
        try:
            needed = {str(i.settlement_currency) for i in self._instruments.values()}
            coins = rows[0].get("coin")
            if (
                not needed
                or type(coins) is not list
                or not needed <= {c.get("coin") for c in coins if type(c) is dict}
            ):
                self._deny()
            return convert_account(rows[0], account_id, stamp)
        except asyncio.CancelledError:
            self._fault("MONITORING_WALLET_INVALID")
            raise
        except Exception:  # noqa: BLE001 - SDK errors may contain original payloads.
            self._fault("MONITORING_WALLET_INVALID")
        raise MonitoringError("MONITORING_WALLET_INVALID") from None
