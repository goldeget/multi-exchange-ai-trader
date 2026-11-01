"""Linux-only qualification through the same Nautilus CASH engine and seccomp worker."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest


def order(side="buy", order_type="MARKET", quantity="0.010", **extra):
    return {"kind": "order", "instrument": "BTC-PAPER", "side": side,
            "order_type": order_type, "quantity": quantity, **extra}


def tick(price="59000.0", sequence=1):
    return {"kind": "tick", "instrument": "BTC-PAPER", "price": price,
            "timestamp": (datetime.now(UTC) - timedelta(seconds=2) + timedelta(microseconds=sequence)).isoformat(),
            "sequence": sequence, "source": "LOCAL_SPOT_SCENARIO_V2"}


def test_cash_market_inventory_fee_currency_partial_sale_and_restart(tmp_path):
    from multi_exchange_trader.paper.spot_store import SpotPaperStore

    path = tmp_path / "spot.sqlite3"
    store = SpotPaperStore(path)
    seed = store.command("spot", "seed", {"kind": "create"})
    assert seed["product"] == "SPOT"
    assert seed["wallet_balance"] == "10000.00000000"
    bought = store.command("spot", "buy", order())
    balances = {row["asset_code"]: row for row in bought["balances"]}
    assert Decimal(balances["BTC"]["total"]) == Decimal("0.010")
    assert Decimal(balances["USDT"]["total"]) < Decimal(9400)
    assert bought["fills"][0]["fee_currency"] == "USDT"
    sold = store.command("spot", "sell", order("sell", quantity="0.004"))
    balances = {row["asset_code"]: row for row in sold["balances"]}
    assert Decimal(balances["BTC"]["total"]) == Decimal("0.006")
    assert SpotPaperStore(path).state("spot") == sold
    with pytest.raises(ValueError, match="inventory|balance"):
        store.command("spot", "oversell", order("sell", quantity="0.007"))
    assert store.state("spot") == sold
    assert store.audit("spot")["funding_entries"] == 1


def test_native_limit_holds_cash_until_fill_or_cancel_and_cannot_double_spend(tmp_path):
    from multi_exchange_trader.paper.spot_store import SpotPaperStore

    store = SpotPaperStore(tmp_path / "spot.sqlite3")
    store.command("spot", "seed", {"kind": "create"})
    waiting = store.command("spot", "limit", order(order_type="LIMIT", quantity="0.100", limit_price="59000.0"))
    assert waiting["fills"] == []
    assert waiting["orders"][0]["status"] == "ACCEPTED"
    assert Decimal(waiting["reserved_order_cash"]) >= Decimal(5900)
    assert Decimal(waiting["available_balance"]) < Decimal(4100)
    with pytest.raises(ValueError, match="balance|notional"):
        store.command("spot", "double", order(order_type="LIMIT", quantity="0.100", limit_price="59000.0"))
    cancelled = store.command("spot", "cancel", {"kind": "cancel", "instrument": "BTC-PAPER",
                                                "order_id": waiting["orders"][0]["id"]})
    assert cancelled["orders"][0]["status"] == "CANCELED"
    assert cancelled["available_balance"] == "10000.00000000"
    waiting = store.command("spot", "limit2", order(order_type="LIMIT", limit_price="59000.0"))
    filled = store.command("spot", "quote", tick("58000.0"))
    assert len(filled["fills"]) == 1
    assert filled["orders"][-1]["status"] == "FILLED"
    assert Decimal(filled["fills"][0]["price"]) <= Decimal("59000.0")
    assert Decimal(filled["reserved_order_cash"]) == 0


def test_spot_metadata_never_opens_as_v1_and_sell_reserves_inventory(tmp_path):
    from multi_exchange_trader.paper.spot_store import SpotPaperStore
    from multi_exchange_trader.paper.store import PaperStore

    path = tmp_path / "spot.sqlite3"
    store = SpotPaperStore(path)
    store.command("spot", "seed", {"kind": "create"})
    with pytest.raises(ValueError, match="schema|assumptions"):
        PaperStore(path)
    store.command("spot", "buy", order())
    waiting = store.command("spot", "sell-limit", order("sell", "LIMIT", "0.006", limit_price="61000.0"))
    btc = next(row for row in waiting["balances"] if row["asset_code"] == "BTC")
    assert Decimal(btc["locked"]) == Decimal("0.006")
    assert Decimal(btc["free"]) == Decimal("0.004")
    with pytest.raises(ValueError, match="inventory|balance"):
        store.command("spot", "oversell", order("sell", quantity="0.005"))
