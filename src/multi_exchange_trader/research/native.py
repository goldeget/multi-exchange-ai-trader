"""Exact v1 native single-instrument CASH or PAPER-margin OHLC path research."""

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
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId, Symbol, Venue  # type: ignore[import-not-found]
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money, Price, Quantity  # type: ignore[import-not-found]
from nautilus_trader.trading.strategy import Strategy  # type: ignore[import-not-found]

from ..paper.contract import FEE, MMR, SEED, UNIT, money, reserve
from ..paper.native import instruments
from .data import validate_dataset
from .strategy import config_check, signal


class ResearchStrategy(Strategy):  # type: ignore[misc]
    def __init__(
        self,
        dataset: dict[str, Any],
        config: dict[str, Any],
        start: int,
        end: int,
        inst: Any,
        venue: Any,
    ) -> None:
        super().__init__(StrategyConfig(strategy_id="RESEARCH-001"))
        self.dataset, self.settings, self.segment_start, self.segment_end = (
            dataset,
            config,
            start,
            end,
        )
        self.inst, self.venue = inst, venue
        self.spot = dataset["market"] == "spot"
        self.base = BTC if dataset["symbol"].startswith("BTC") else ETH
        self.mark = Decimal(dataset["bars"][start]["open"])
        self.curve: list[dict[str, Any]] = []
        self.fills: list[dict[str, Any]] = []
        self.trades: list[dict[str, Any]] = []
        self.lot: dict[str, Any] | None = None
        self.roles: dict[str, str] = {}
        self.phase = "open"
        self.index = start
        self.error: str | None = None
        self.halted: str | None = None
        self.fees = Decimal(0)
        self.gross = Decimal(0)

    def on_start(self) -> None:
        self.subscribe_quote_ticks(self.inst.id)

    def balance(self, currency: Any) -> Decimal:
        value = self.cache.account_for_venue(self.venue).balance_total(currency)
        return value.as_decimal() if value is not None else Decimal(0)

    def equity(self) -> Decimal:
        cash = self.balance(USDT)
        if self.spot:
            return cash + self.balance(self.base) * self.mark
        return cash + sum(
            (
                p.unrealized_pnl(self.inst.make_price(self.mark)).as_decimal()
                for p in self.cache.positions_open(instrument_id=self.inst.id)
            ),
            Decimal(0),
        )

    def market(self, side: Any, quantity: Decimal, role: str) -> None:
        cid = ClientOrderId(f"R{self.index:04}-{self.phase}-{role}")
        self.roles[str(cid)] = role
        order = self.order_factory.market(
            self.inst.id,
            side,
            self.inst.make_qty(quantity),
            reduce_only=role != "enter",
            client_order_id=cid,
            tags=["RESEARCH", self.dataset["market"], role],
        )
        self.submit_order(order)
        if order.status.name != "FILLED":
            raise ValueError("native research order not fully filled")

    def close(self, role: str = "close") -> None:
        if self.lot:
            self.market(
                OrderSide.SELL if self.lot["side"] == "BUY" else OrderSide.BUY,
                self.lot["quantity"],
                role,
            )

    def act(self, target: str) -> None:
        current = "flat" if not self.lot else ("long" if self.lot["side"] == "BUY" else "short")
        if current == target:
            return
        self.close()
        if target == "flat" or self.halted:
            return
        if target == "short" and self.spot:
            raise ValueError("spot cannot short")
        qty = Decimal(self.settings["quantity"])
        tick = self.inst.price_increment.as_decimal()
        price = self.mark + (tick if target == "long" else -tick)
        required = (
            qty * price * (1 + FEE) if self.spot else reserve(qty, price, 10) + qty * price * FEE
        )
        if required > self.balance(USDT) or qty * price > 100000:
            self.halted = "insufficient_cash_or_margin"
            return
        if not self.spot:
            self.cache.account_for_venue(self.venue).set_leverage(self.inst.id, Decimal(10))
        self.market(OrderSide.BUY if target == "long" else OrderSide.SELL, qty, "enter")

    def on_quote_tick(self, tick: Any) -> None:
        if self.error:
            return
        try:
            ms = tick.ts_event // 1_000_000
            self.index = (ms - self.dataset["start_ms"]) // self.dataset["interval_ms"]
            remainder = (ms - self.dataset["start_ms"]) % self.dataset["interval_ms"]
            step = self.dataset["interval_ms"]
            self.phase = {0: "open", step // 3: "low", 2 * step // 3: "high", step - 1: "close"}[
                remainder
            ]
            self.mark = tick.bid_price.as_decimal()
            if self.lot and not self.spot:
                p = self.cache.positions_open(instrument_id=self.inst.id)[0]
                if reserve(self.lot["quantity"], self.lot["entry"], 10) + p.unrealized_pnl(
                    self.inst.make_price(self.mark)
                ).as_decimal() <= self.lot["quantity"] * self.mark * (MMR + FEE):
                    self.close("liquidation")
            if self.phase == "open":
                target = (
                    "long"
                    if self.settings["strategy"] == "BUY_HOLD_V1"
                    else signal(
                        self.dataset["bars"][: self.index],
                        self.settings["fast"],
                        self.settings["slow"],
                        self.dataset["market"],
                    )
                )
                self.act(target)
            if self.phase == "close" and self.index == self.segment_end - 1:
                self.close("end_close")
            self.curve.append(
                {
                    "time_ms": ms,
                    "bar_index": self.index,
                    "phase": self.phase,
                    "equity": money(self.equity()),
                    "cash": money(self.balance(USDT)),
                    "mark": str(self.mark),
                }
            )
        except Exception as exc:
            self.error = str(exc)
            raise

    def on_order_filled(self, event: Any) -> None:
        if event.commission.currency != USDT:
            raise ValueError("native fee currency mismatch")
        fee, qty, price = (
            event.commission.as_decimal(),
            event.last_qty.as_decimal(),
            event.last_px.as_decimal(),
        )
        self.fees += fee
        role = self.roles[str(event.client_order_id)]
        side = event.order_side.name
        record = {
            "order_id": str(event.client_order_id),
            "bar_index": self.index,
            "phase": self.phase,
            "time_ms": event.ts_event // 1_000_000,
            "side": side,
            "role": role,
            "price": str(event.last_px),
            "quantity": str(event.last_qty),
            "fee": money(fee),
        }
        self.fills.append(record)
        if role == "enter":
            if self.lot:
                raise ValueError("native unexpected second entry")
            self.lot = {
                "entry": price,
                "quantity": qty,
                "side": side,
                "fee": fee,
                "bar_index": self.index,
            }
        else:
            if not self.lot or qty != self.lot["quantity"]:
                raise ValueError("native closing quantity reconciliation")
            gross = (price - self.lot["entry"]) * qty * (1 if self.lot["side"] == "BUY" else -1)
            self.gross += gross
            self.trades.append(
                {
                    "entry_bar": self.lot["bar_index"],
                    "exit_bar": self.index,
                    "side": self.lot["side"],
                    "quantity": str(qty),
                    "gross_pnl": money(gross),
                    "fees": money(fee + self.lot["fee"]),
                    "net_pnl": money(gross - fee - self.lot["fee"]),
                    "exit_role": role,
                }
            )
            self.lot = None


def run_segment(
    dataset: dict[str, Any], config: dict[str, Any], start: int, end: int
) -> dict[str, Any]:
    validate_dataset(dataset)
    config_check(config, dataset["symbol"])
    if (
        version("nautilus_trader") != "1.231.0"
        or type(start) is not int
        or type(end) is not int
        or not 0 <= start < end <= len(dataset["bars"])
    ):
        raise ValueError("native version/segment bounds")
    spot = dataset["market"] == "spot"
    venue = Venue("SPOT_RESEARCH" if spot else "PAPER")
    if spot:
        inst = CurrencyPair(
            instrument_id=InstrumentId.from_str(f"{dataset['symbol'][:-4]}-USDT.SPOT_RESEARCH"),
            raw_symbol=Symbol(dataset["symbol"]),
            base_currency=BTC if dataset["symbol"] == "BTCUSDT" else ETH,
            quote_currency=USDT,
            price_precision=2,
            size_precision=3 if dataset["symbol"] == "BTCUSDT" else 2,
            price_increment=Price.from_str("0.01"),
            size_increment=Quantity.from_str("0.001" if dataset["symbol"] == "BTCUSDT" else "0.01"),
            maker_fee=FEE,
            taker_fee=FEE,
            ts_event=0,
            ts_init=0,
        )
    else:
        inst = instruments()[dataset["symbol"]]
    engine = BacktestEngine(
        BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True), run_analysis=False)
    )
    try:
        engine.add_venue(
            venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH if spot else AccountType.MARGIN,
            base_currency=None if spot else USDT,
            starting_balances=[Money(SEED, USDT)],
            default_leverage=Decimal(1) if spot else Decimal(10),
            fill_model=FillModel(prob_slippage=1.0, random_seed=42),
            use_message_queue=False,
            use_reduce_only=True,
        )
        engine.add_instrument(inst)
        strategy = ResearchStrategy(dataset, config, start, end, inst, venue)
        engine.add_strategy(strategy)
        quotes = []
        step = dataset["interval_ms"]
        for bar in dataset["bars"][start:end]:
            for key, offset in [
                ("open", 0),
                ("low", step // 3),
                ("high", 2 * step // 3),
                ("close", step - 1),
            ]:
                price = inst.make_price(Decimal(bar[key]))
                ts = (bar["time_ms"] + offset) * 1_000_000
                quotes.append(
                    QuoteTick(
                        inst.id,
                        price,
                        price,
                        inst.make_qty(Decimal(1000)),
                        inst.make_qty(Decimal(1000)),
                        ts,
                        ts,
                    )
                )
        engine.add_data(quotes)
        engine.run(streaming=True)
        if strategy.error:
            raise ValueError(strategy.error)
        final = strategy.equity()
        adjustment = final - (SEED + strategy.gross - strategy.fees)
        if (
            strategy.lot
            or abs(adjustment) > UNIT
            or strategy.cache.positions_open(instrument_id=inst.id)
        ):
            raise ValueError("native final account/position reconciliation")
        return {
            "market": dataset["market"],
            "symbol": dataset["symbol"],
            "synthetic": dataset["synthetic"],
            "portfolio_scope": "independent_single_instrument",
            "account_type": "CASH" if spot else "MARGIN",
            "start": start,
            "end": end,
            "config": config,
            "initial_equity": money(SEED),
            "final_equity": money(final),
            "net_pnl": money(final - SEED),
            "gross_pnl": money(strategy.gross),
            "fees": money(strategy.fees),
            "rounding_adjustment": money(adjustment),
            "inventory": money(strategy.balance(strategy.base)) if spot else "0.00000000",
            "curve": strategy.curve,
            "fills": strategy.fills,
            "trades": strategy.trades,
            "native_fill_count": len(strategy.fills),
            "halted": strategy.halted,
            "path_assumption": "O-L-H-C inferred path, not observed intrabar sequence",
            "forced_end_close": True,
        }
    finally:
        engine.dispose()
