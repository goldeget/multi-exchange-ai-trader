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

from .account_binding import AuthenticatedAccountBinding, BybitDemoAccountSession
from .monitoring_budget import MonitoringReadBudget
from .monitoring_models import MonitoringError
from .native_events import bounded_decimal
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


def convert_instrument(row: dict[str, Any], fee: dict[str, Any], symbol: str, stamp_ms: int) -> Any:
    # Runtime public signatures include info, unlike the installed .pyi.
    p = importlib.import_module("nautilus_trader.core.nautilus_pyo3")

    if (
        row.get("symbol") != symbol
        or fee.get("symbol") != symbol
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
    return p.CryptoPerpetual(
        instrument_id=p.InstrumentId.from_str(symbol + "-LINEAR.BYBIT"),
        raw_symbol=p.Symbol(symbol),
        base_currency=base,
        quote_currency=quote,
        settlement_currency=settlement,
        is_inverse=False,
        price_precision=price_precision,
        size_precision=size_precision,
        price_increment=price_increment,
        size_increment=size_increment,
        ts_event=stamp_ms * 1_000_000,
        ts_init=stamp_ms * 1_000_000,
        multiplier=p.Quantity.from_str("1"),
        lot_size=size_increment,
        max_quantity=p.Quantity.from_str(str(max_quantity)),
        min_quantity=p.Quantity.from_str(str(min_quantity)),
        max_notional=None,
        min_notional=_money(_number(sizes.get("minNotionalValue"), positive=True), quote),
        max_price=p.Price.from_str(str(max_price)),
        min_price=p.Price.from_str(str(min_price)),
        margin_init=Decimal("0.1"),
        margin_maint=Decimal("0.1"),
        maker_fee=_number(fee.get("makerFeeRate")),
        taker_fee=_number(fee.get("takerFeeRate")),
        tick_scheme=None,
        info=None,
    )


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
    ) -> None:
        session.require_observation_binding(binding, scope=scope)
        self._session, self._binding, self._scope = session, binding, scope
        self._symbols, self._fault = symbols, fault
        self._instruments: dict[str, Any] = {}
        self._cancelled = False

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

    async def _get(
        self, path: str, params: list[tuple[str, str]], *, signed: bool
    ) -> tuple[dict[str, Any], int]:
        try:
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
