"""Real v1 Nautilus matching/accounting over bounded local quote/command replay."""

from decimal import ROUND_HALF_EVEN, Decimal
from importlib.metadata import version
from typing import Any

from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine  # type: ignore[import-not-found]
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import LoggingConfig, StrategyConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.currencies import BTC, ETH, USDT
from nautilus_trader.model.data import QuoteTick  # type: ignore[import-not-found]
from nautilus_trader.model.enums import (
    AccountType,
    ContingencyType,
    OmsType,
    OrderSide,
    TriggerType,
)
from nautilus_trader.model.identifiers import (  # type: ignore[import-not-found]
    ClientOrderId,
    InstrumentId,
    OrderListId,
    Symbol,
    Venue,
)
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Money, Price, Quantity  # type: ignore[import-not-found]
from nautilus_trader.model.orders import MarketIfTouchedOrder, OrderList, StopMarketOrder
from nautilus_trader.trading.strategy import Strategy  # type: ignore[import-not-found]

from .contract import FEE, MMR, SEED, SPECS, UNIT, leverage, money, reserve, validate_event

PAPER = Venue("PAPER")
BASE_NS = 1767225600000000000  # Separate deterministic PAPER execution clock; not exchange time.


def instruments(*, fee: Decimal = FEE) -> dict[str, Any]:
    return {
        symbol: CryptoPerpetual(
            instrument_id=InstrumentId.from_str(f"{symbol.split('-')[0]}-USDT-PERP.PAPER"),
            raw_symbol=Symbol(symbol),
            base_currency=BTC if symbol == "BTC-PAPER" else ETH,
            quote_currency=USDT,
            settlement_currency=USDT,
            is_inverse=False,
            price_precision=1 if symbol == "BTC-PAPER" else 2,
            price_increment=Price.from_str(s["tick"]),
            size_precision=3 if symbol == "BTC-PAPER" else 2,
            size_increment=Quantity.from_str(s["lot"]),
            min_quantity=Quantity.from_str(s["lot"]),
            max_quantity=Quantity.from_str(s["max_qty"]),
            margin_init=Decimal(1),
            margin_maint=MMR,
            maker_fee=fee,
            taker_fee=fee,
            ts_event=0,
            ts_init=0,
        )
        for symbol, s in SPECS.items()
    }


class PaperStrategy(Strategy):  # type: ignore[misc]
    def __init__(self, events: list[dict[str, Any]], specs: dict[str, Any]) -> None:
        super().__init__(StrategyConfig(strategy_id="PAPER-001"))
        self.events = events
        self.specs = specs
        self.symbols = {str(i.id): s for s, i in specs.items()}
        self.marks = {s: Decimal(v["seed"]) for s, v in SPECS.items()}
        self.roles: dict[str, str] = {}
        self.fills: list[dict[str, Any]] = []
        self.lots: dict[str, dict[str, Any]] = {}
        self.closed_net = Decimal(0)
        self.gross = Decimal(0)
        self.fees = Decimal(0)
        self.error: str | None = None
        self.pending_leverage: dict[str, int] = {}

    def on_start(self) -> None:
        for inst in self.specs.values():
            self.subscribe_quote_ticks(inst.id)

    def cash(self) -> Decimal:
        return Decimal(self.cache.account_for_venue(PAPER).balance_total(USDT).as_decimal())

    def reserved(self) -> Decimal:
        return sum(
            (reserve(x["qty"], x["entry"], x["leverage"]) for x in self.lots.values()), Decimal(0)
        )

    def market(self, symbol: str, side: Any, qty: Decimal, role: str, seq: int) -> None:
        inst = self.specs[symbol]
        cid = ClientOrderId(f"P{seq:06}-{role}")
        self.roles[str(cid)] = role
        order = self.order_factory.market(
            inst.id,
            side,
            inst.make_qty(qty),
            reduce_only=role != "enter",
            client_order_id=cid,
            tags=["PAPER", role],
        )
        self.submit_order(order)
        if order.status.name != "FILLED":
            raise ValueError("native order rejected or not fully filled")

    def protection(self, symbol: str, e: dict[str, Any], seq: int) -> None:
        inst = self.specs[symbol]
        self.cancel_all_orders(inst.id)
        lot = self.lots.get(symbol)
        if not lot:
            if e.get("sl") is not None or e.get("tp") is not None:
                raise ValueError("position required")
            return
        mark = self.marks[symbol]
        long = lot["side"] == "BUY"
        for key in ("sl", "tp"):
            if e.get(key) is None:
                continue
            p = Decimal(e[key])
            correct = p < mark if (key == "sl") == long else p > mark
            if not correct:
                raise ValueError("protective trigger must be on correct side of mark")
        fields = [k for k in ("sl", "tp") if e.get(k) is not None]
        if not fields:
            return
        ids = {k: ClientOrderId(f"P{seq:06}-{k}") for k in fields}
        group = OrderListId(f"P{seq:06}-OCO")
        orders = []
        for key in fields:
            cid = ids[key]
            self.roles[str(cid)] = key
            cls = StopMarketOrder if key == "sl" else MarketIfTouchedOrder
            orders.append(
                cls(
                    trader_id=self.trader_id,
                    strategy_id=self.id,
                    instrument_id=inst.id,
                    client_order_id=cid,
                    order_side=OrderSide.SELL if long else OrderSide.BUY,
                    quantity=inst.make_qty(lot["qty"]),
                    trigger_price=inst.make_price(Decimal(e[key])),
                    trigger_type=TriggerType.DEFAULT,
                    init_id=UUID4(),
                    ts_init=self.clock.timestamp_ns(),
                    reduce_only=True,
                    contingency_type=ContingencyType.OCO
                    if len(fields) == 2
                    else ContingencyType.NO_CONTINGENCY,
                    order_list_id=group,
                    linked_order_ids=[v for k, v in ids.items() if k != key] or None,
                    tags=["PAPER", key],
                )
            )
        self.submit_order_list(OrderList(group, orders))
        if any(o.status.name not in ("ACCEPTED", "TRIGGERED") for o in orders):
            raise ValueError("native protection rejected")

    def on_quote_tick(self, tick: Any) -> None:
        if self.error:
            return
        seq = (tick.ts_event - BASE_NS) // 1000000000
        if seq < 1:
            return
        e = self.events[seq - 1]
        symbol = e["instrument"]
        inst = self.specs[symbol]
        try:
            if e["kind"] == "tick":
                self.marks[symbol] = Decimal(e["price"])
            if e["kind"] == "enter":
                if symbol in self.lots:
                    raise ValueError("position already open; close before another entry")
                lev = leverage(e["leverage"])
                qty = Decimal(e["quantity"])
                long = e["side"] == "long"
                fill = self.marks[symbol] + Decimal(SPECS[symbol]["tick"]) * (1 if long else -1)
                if (
                    qty * fill > 100000
                    or reserve(qty, fill, lev) + qty * fill * FEE > self.cash() - self.reserved()
                ):
                    raise ValueError("insufficient margin or notional cap")
                self.pending_leverage[symbol] = lev
                self.cache.account_for_venue(PAPER).set_leverage(inst.id, Decimal(lev))
                self.market(symbol, OrderSide.BUY if long else OrderSide.SELL, qty, "enter", seq)
            elif e["kind"] == "close":
                lot = self.lots.get(symbol)
                qty = Decimal(e["quantity"])
                if not lot or qty > lot["qty"]:
                    raise ValueError("reduce-only quantity exceeds position")
                if qty == lot["qty"]:
                    self.cancel_all_orders(inst.id)
                self.market(
                    symbol,
                    OrderSide.SELL if lot["side"] == "BUY" else OrderSide.BUY,
                    qty,
                    "close",
                    seq,
                )
            elif e["kind"] == "protect":
                self.protection(symbol, e, seq)
            if e["kind"] == "tick" and symbol in self.lots:
                lot = self.lots[symbol]
                positions = self.cache.positions_open(instrument_id=inst.id)
                if positions:
                    upnl = (
                        positions[0]
                        .unrealized_pnl(inst.make_price(self.marks[symbol]))
                        .as_decimal()
                    )
                    if reserve(lot["qty"], lot["entry"], lot["leverage"]) + upnl <= lot[
                        "qty"
                    ] * self.marks[symbol] * (MMR + FEE):
                        self.cancel_all_orders(inst.id)
                        self.market(
                            symbol,
                            OrderSide.SELL if lot["side"] == "BUY" else OrderSide.BUY,
                            lot["qty"],
                            "liquidation",
                            seq,
                        )
        except Exception as exc:
            self.error = str(exc)
            raise

    def on_order_filled(self, e: Any) -> None:
        symbol = self.symbols[str(e.instrument_id)]
        role = self.roles[str(e.client_order_id)]
        px = e.last_px.as_decimal()
        qty = e.last_qty.as_decimal()
        fee = e.commission.as_decimal()
        side = e.order_side.name
        self.fees += fee
        if role == "enter":
            self.lots[symbol] = {
                "qty": qty,
                "entry": px,
                "fee_left": fee,
                "side": side,
                "leverage": self.pending_leverage[symbol],
            }
        else:
            lot = self.lots[symbol]
            if qty > lot["qty"]:
                raise ValueError("native reduce-only reversal")
            allocated = (lot["fee_left"] * qty / lot["qty"]).quantize(
                UNIT, rounding=ROUND_HALF_EVEN
            )
            gross = (px - lot["entry"]) * qty * (1 if lot["side"] == "BUY" else -1)
            self.gross += gross
            self.closed_net += gross - fee - allocated
            lot["fee_left"] -= allocated
            lot["qty"] -= qty
            if lot["qty"] == 0:
                del self.lots[symbol]
        self.fills.append(
            {
                "event_seq": (e.ts_event - BASE_NS) // 1000000000,
                "order_id": str(e.client_order_id),
                "instrument": symbol,
                "side": side,
                "role": role,
                "price": str(e.last_px),
                "quantity": str(e.last_qty),
                "fee": money(fee),
                "clock": "PAPER_REPLAY_V1",
            }
        )

    def snapshot(self) -> dict[str, Any]:
        positions = []
        unreal = Decimal(0)
        for p in self.cache.positions_open():
            symbol = self.symbols[str(p.instrument_id)]
            lot = self.lots[symbol]
            qty = p.quantity.as_decimal()
            if qty != lot["qty"]:
                raise ValueError("native position reconciliation failed")
            upnl = p.unrealized_pnl(self.specs[symbol].make_price(self.marks[symbol])).as_decimal()
            unreal += upnl
            positions.append(
                {
                    "instrument": symbol,
                    "side": "long" if lot["side"] == "BUY" else "short",
                    "quantity": str(p.quantity),
                    "entry_price": str(lot["entry"]),
                    "leverage": lot["leverage"],
                    "reserved_margin": money(reserve(qty, lot["entry"], lot["leverage"])),
                    "unrealized_pnl": money(upnl),
                    "mark": str(self.marks[symbol]),
                }
            )
        cash = self.cash()
        reserved = self.reserved()
        expected = SEED + self.gross - self.fees
        adjustment = cash - expected
        if abs(adjustment) > UNIT:
            raise ValueError("native cash reconciliation failed")
        orders = [
            {
                "id": str(o.client_order_id),
                "instrument": self.symbols[str(o.instrument_id)],
                "role": self.roles.get(str(o.client_order_id), "unknown"),
                "status": o.status.name,
                "quantity": str(o.quantity),
                "trigger": str(o.trigger_price)
                if getattr(o, "trigger_price", None) is not None
                else None,
                "reduce_only": o.is_reduce_only,
            }
            for o in self.cache.orders()
        ]
        return {
            "engine": "NautilusTrader 1.231.0 SimulatedExchange",
            "mode": "PAPER",
            "assumptions": "PAPER-v1",
            "wallet_balance": money(cash),
            "reserved_margin": money(reserved),
            "available_balance": money(max(cash - reserved, Decimal(0))),
            "deficit": money(max(reserved - cash, Decimal(0))),
            "unrealized_pnl": money(unreal),
            "equity": money(cash + unreal),
            "closed_net_pnl": money(self.closed_net),
            "realized_wallet_delta": money(cash - SEED),
            "realized_gross": money(self.gross),
            "fees": money(self.fees),
            "native_rounding_adjustment": money(adjustment),
            "positions": sorted(positions, key=lambda p: p["instrument"]),
            "orders": orders,
            "fills": self.fills,
            "marks": {s: str(m) for s, m in self.marks.items()},
        }


def replay(events: list[dict[str, Any]]) -> dict[str, Any]:
    if version("nautilus_trader") != "1.231.0":
        raise ValueError("exact Nautilus version required")
    for event in events:
        validate_event(event)
    specs = instruments()
    engine = BacktestEngine(
        BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True), run_analysis=False)
    )
    try:
        engine.add_venue(
            PAPER,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=USDT,
            starting_balances=[Money(SEED, USDT)],
            default_leverage=Decimal(10),
            fill_model=FillModel(prob_slippage=1.0, random_seed=42),
            use_message_queue=False,
            use_reduce_only=True,
        )
        for inst in specs.values():
            engine.add_instrument(inst)
        strategy = PaperStrategy(events, specs)
        engine.add_strategy(strategy)
        marks = {s: v["seed"] for s, v in SPECS.items()}
        data = []

        def quote(symbol: str, ts: int) -> Any:
            inst = specs[symbol]
            return QuoteTick(
                inst.id,
                inst.make_price(Decimal(marks[symbol])),
                inst.make_price(Decimal(marks[symbol])),
                inst.make_qty(Decimal(1000)),
                inst.make_qty(Decimal(1000)),
                ts,
                ts,
            )

        for n, s in enumerate(specs):
            data.append(quote(s, BASE_NS + n + 1))
        for n, e in enumerate(events, 1):
            if e["kind"] == "tick":
                marks[e["instrument"]] = e["price"]
            data.append(quote(e["instrument"], BASE_NS + n * 1000000000))
        engine.add_data(data)
        engine.run(streaming=True)
        if strategy.error:
            raise ValueError(strategy.error)
        return strategy.snapshot()
    finally:
        engine.dispose()
