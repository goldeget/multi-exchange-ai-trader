"""SPOT-PAPER-v2 on Nautilus CurrencyPair/CASH; the engine owns every fill and balance."""

from decimal import Decimal
from importlib.metadata import version
from typing import Any

from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine  # type: ignore[import-not-found]
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import LoggingConfig, StrategyConfig
from nautilus_trader.model.currencies import BTC, ETH, USDT
from nautilus_trader.model.data import QuoteTick  # type: ignore[import-not-found]
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import (  # type: ignore[import-not-found]
    ClientOrderId,
    InstrumentId,
    Symbol,
    Venue,
)
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money, Price, Quantity  # type: ignore[import-not-found]
from nautilus_trader.trading.strategy import Strategy  # type: ignore[import-not-found]

from .contract import FEE, SEED, SPECS, UNIT, money
from .spot_contract import VERSION, validate_spot_event

VENUE = Venue("PAPER")
BASE_NS = 1767225600000000000


def instruments() -> dict[str, Any]:
    return {symbol: CurrencyPair(
        instrument_id=InstrumentId.from_str(f"{symbol.split('-')[0]}-USDT-SPOT.PAPER"),
        raw_symbol=Symbol(symbol), base_currency=BTC if symbol == "BTC-PAPER" else ETH,
        quote_currency=USDT, price_precision=1 if symbol == "BTC-PAPER" else 2,
        size_precision=3 if symbol == "BTC-PAPER" else 2,
        price_increment=Price.from_str(spec["tick"]), size_increment=Quantity.from_str(spec["lot"]),
        min_quantity=Quantity.from_str(spec["lot"]), max_quantity=Quantity.from_str(spec["max_qty"]),
        maker_fee=FEE, taker_fee=FEE, ts_event=0, ts_init=0,
    ) for symbol, spec in SPECS.items()}


class SpotStrategy(Strategy):  # type: ignore[misc]
    def __init__(self, events: list[dict[str, Any]], specs: dict[str, Any]) -> None:
        super().__init__(StrategyConfig(strategy_id="SPOT-001"))
        self.events, self.specs = events, specs
        self.symbols = {str(inst.id): symbol for symbol, inst in specs.items()}
        self.marks = {symbol: Decimal(spec["seed"]) for symbol, spec in SPECS.items()}
        self.fills: list[dict[str, Any]] = []
        self.fees = Decimal(0)
        self.error: str | None = None

    def on_start(self) -> None:
        for inst in self.specs.values():
            self.subscribe_quote_ticks(inst.id)

    def account(self) -> Any:
        return self.cache.account_for_venue(VENUE)

    def fee_reservation(self) -> Decimal:
        # Nautilus CASH locks native purchase cost; reserve the bounded quote fee
        # additionally in the deterministic risk gate before admitting another order.
        return sum((order.leaves_qty.as_decimal() * order.price.as_decimal() * FEE
                    for order in self.cache.orders_open()
                    if order.side == OrderSide.BUY), Decimal(0))

    def on_quote_tick(self, tick: Any) -> None:
        sequence = (tick.ts_event - BASE_NS) // 1_000_000_000
        if sequence < 1 or self.error is not None:
            return
        event = self.events[sequence - 1]
        symbol, kind = event["instrument"], event["kind"]
        inst = self.specs[symbol]
        try:
            if kind == "tick":
                self.marks[symbol] = Decimal(event["price"])
                return
            if kind == "cancel":
                existing = self.cache.order(ClientOrderId(event["order_id"]))
                if existing is None or existing.instrument_id != inst.id or not existing.is_open:
                    raise ValueError("SPOT open order not found")
                self.cancel_order(existing)
                if existing.status.name != "CANCELED":
                    raise ValueError("SPOT native cancellation failed")
                return
            qty = Decimal(event["quantity"])
            buy = event["side"] == "buy"
            side = OrderSide.BUY if buy else OrderSide.SELL
            limit = event["order_type"] == "LIMIT"
            bound = Decimal(event["limit_price"]) if limit else (
                self.marks[symbol] + Decimal(SPECS[symbol]["tick"]) * (1 if buy else -1)
            )
            account = self.account()
            if qty * bound > 100_000:
                raise ValueError("SPOT notional cap")
            if buy:
                available = account.balance_free(USDT).as_decimal() - self.fee_reservation()
                if qty * bound * (1 + FEE) > available:
                    raise ValueError("SPOT insufficient cash balance")
            elif qty > account.balance_free(inst.base_currency).as_decimal():
                raise ValueError("SPOT sell exceeds free inventory balance; short forbidden")
            key = ClientOrderId(f"S{sequence:06}")
            if limit:
                order = self.order_factory.limit(inst.id, side, inst.make_qty(qty), inst.make_price(bound),
                                                 client_order_id=key, tags=[VERSION])
            else:
                order = self.order_factory.market(inst.id, side, inst.make_qty(qty),
                                                  client_order_id=key, tags=[VERSION])
            self.submit_order(order)
            if order.status.name not in {"FILLED", "ACCEPTED", "PARTIALLY_FILLED"}:
                raise ValueError("SPOT native order rejected")
            if not limit and order.status.name != "FILLED":
                raise ValueError("SPOT native market order incomplete")
        except Exception as exc:
            self.error = str(exc)
            raise

    def on_order_filled(self, event: Any) -> None:
        if event.commission.currency != USDT:
            raise ValueError("SPOT unqualified native fee currency")
        self.fees += event.commission.as_decimal()
        self.fills.append({
            "event_seq": (event.ts_event - BASE_NS) // 1_000_000_000,
            "order_id": str(event.client_order_id), "instrument": self.symbols[str(event.instrument_id)],
            "side": event.order_side.name, "role": event.order_side.name.lower(),
            "price": str(event.last_px), "quantity": str(event.last_qty),
            "fee": money(event.commission.as_decimal()), "fee_currency": "USDT",
            "clock": "SPOT_REPLAY_V2",
        })

    def snapshot(self) -> dict[str, Any]:
        account = self.account()
        balances = [{"asset_code": currency.code,
                     "total": money(account.balance_total(currency).as_decimal()),
                     "locked": money(account.balance_locked(currency).as_decimal()),
                     "free": money(account.balance_free(currency).as_decimal())}
                    for currency in (USDT, BTC, ETH)]
        totals = {row["asset_code"]: Decimal(row["total"]) for row in balances}
        expected_cash = SEED - self.fees
        expected_assets = {symbol: Decimal(0) for symbol in SPECS}
        for fill in self.fills:
            qty, px = Decimal(fill["quantity"]), Decimal(fill["price"])
            sign = 1 if fill["side"] == "BUY" else -1
            expected_cash -= sign * qty * px
            expected_assets[fill["instrument"]] += sign * qty
        adjustment = totals["USDT"] - expected_cash
        if abs(adjustment) > UNIT or any(
            abs(totals[symbol.split('-')[0]] - qty) > UNIT
            for symbol, qty in expected_assets.items()
        ) or any(value < 0 for value in totals.values()):
            raise ValueError("SPOT native cash/inventory reconciliation")
        fee_hold = self.fee_reservation()
        quote_locked = account.balance_locked(USDT).as_decimal()
        free = account.balance_free(USDT).as_decimal() - fee_hold
        if free < -UNIT:
            raise ValueError("SPOT reserved fees exceed free cash")
        positions, unrealized = [], Decimal(0)
        for native in self.cache.positions_open():
            symbol = self.symbols[str(native.instrument_id)]
            if not native.is_long:
                raise ValueError("SPOT native short position forbidden")
            unrealized += native.unrealized_pnl(self.specs[symbol].make_price(self.marks[symbol])).as_decimal()
            positions.append({"instrument": symbol, "side": "owned", "quantity": str(native.quantity),
                              "entry_price": str(native.avg_px_open), "mark": str(self.marks[symbol]),
                              "product": "SPOT"})
        equity = totals["USDT"] + sum((totals[symbol.split('-')[0]] * self.marks[symbol]
                                       for symbol in SPECS), Decimal(0))
        orders = [{"id": str(order.client_order_id), "instrument": self.symbols[str(order.instrument_id)],
                   "status": order.status.name, "role": order.side.name.lower(), "side": order.side.name,
                   "order_type": order.order_type.name, "quantity": str(order.quantity),
                   "filled_quantity": str(order.filled_qty), "remaining_quantity": str(order.leaves_qty),
                   "limit_price": str(order.price) if getattr(order, "price", None) is not None else None,
                   "reduce_only": False, "trigger": None}
                  for order in sorted(self.cache.orders(), key=lambda item: str(item.client_order_id))]
        return {"engine": "NautilusTrader 1.231.0 SimulatedExchange CASH", "mode": "PAPER",
                "assumptions": VERSION, "product": "SPOT", "balances": balances,
                "wallet_balance": money(totals["USDT"]), "available_balance": money(max(free, Decimal(0))),
                "reserved_margin": money(Decimal(0)), "reserved_order_cash": money(quote_locked + fee_hold),
                "reserved_order_fee": money(fee_hold), "deficit": money(Decimal(0)),
                "equity": money(equity), "unrealized_pnl": money(unrealized),
                "closed_net_pnl": None, "realized_gross": None,
                "realized_wallet_delta": money(totals["USDT"] - SEED),
                "fees": money(self.fees), "native_rounding_adjustment": money(adjustment),
                "positions": sorted(positions, key=lambda row: row["instrument"]),
                "orders": orders, "fills": self.fills,
                "marks": {symbol: str(mark) for symbol, mark in self.marks.items()},
                "fill_policy": "SPOT_L1_SPREAD_FULL_LIQUIDITY_V2", "fee_currency": "USDT",
                "bid_offset_ticks": -1, "ask_offset_ticks": 1, "extra_slippage_ticks": 0}


def replay(events: list[dict[str, Any]]) -> dict[str, Any]:
    if version("nautilus_trader") != "1.231.0":
        raise ValueError("exact Nautilus version required")
    for event in events:
        validate_spot_event(event)
    specs = instruments()
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True), run_analysis=False))
    try:
        engine.add_venue(VENUE, oms_type=OmsType.NETTING, account_type=AccountType.CASH,
                         base_currency=None, starting_balances=[Money(SEED, USDT), Money(0, BTC), Money(0, ETH)],
                         default_leverage=Decimal(1), allow_cash_borrowing=False,
                         fill_model=FillModel(prob_slippage=0.0, random_seed=42),
                         use_message_queue=False, use_reduce_only=True)
        for inst in specs.values():
            engine.add_instrument(inst)
        strategy = SpotStrategy(events, specs)
        engine.add_strategy(strategy)
        marks = {symbol: spec["seed"] for symbol, spec in SPECS.items()}

        def quote(symbol: str, stamp: int) -> Any:
            inst = specs[symbol]
            mark, spread = Decimal(marks[symbol]), Decimal(SPECS[symbol]["tick"])
            return QuoteTick(inst.id, inst.make_price(mark - spread), inst.make_price(mark + spread),
                             inst.make_qty(Decimal(1000)), inst.make_qty(Decimal(1000)), stamp, stamp)

        data = [quote(symbol, BASE_NS + index + 1) for index, symbol in enumerate(specs)]
        for index, event in enumerate(events, 1):
            if event["kind"] == "tick":
                marks[event["instrument"]] = event["price"]
            data.append(quote(event["instrument"], BASE_NS + index * 1_000_000_000))
        engine.add_data(data)
        engine.run(streaming=True)
        if strategy.error:
            raise ValueError(strategy.error)
        return strategy.snapshot()
    finally:
        engine.dispose()
