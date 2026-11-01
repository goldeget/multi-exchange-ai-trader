"""Pure SPOT-PAPER-v2 validation, with no engine, account or network access."""

import pytest


def order(**changes):
    return {"kind": "order", "instrument": "BTC-PAPER", "side": "buy",
            "quantity": "0.001", "order_type": "LIMIT", "limit_price": "59000.0"} | changes


def test_spot_contract_accepts_cash_buy_sell_and_rejects_margin_or_precision():
    from multi_exchange_trader.paper.spot_contract import validate_spot_event

    validate_spot_event(order())
    validate_spot_event(order(side="sell"))
    for changed in ({"leverage": 2}, {"side": "short"}, {"quantity": "0.0001"},
                    {"limit_price": "59000.01"}, {"order_type": "STOP"}):
        with pytest.raises((ValueError, TypeError)):
            validate_spot_event(order(**changed))
    with pytest.raises(ValueError):
        validate_spot_event({"kind": "enter", "instrument": "BTC-PAPER", "side": "long",
                             "quantity": "0.001", "leverage": 1})


def test_market_order_rejects_limit_price_and_floating_point_quantity():
    from multi_exchange_trader.paper.spot_contract import validate_spot_event

    body = order(order_type="MARKET")
    body.pop("limit_price")
    validate_spot_event(body)
    with pytest.raises((ValueError, TypeError)):
        validate_spot_event(body | {"limit_price": "59000.0"})
    with pytest.raises((ValueError, TypeError)):
        validate_spot_event(body | {"quantity": 0.001})


def test_event_capacity_keeps_one_cancellation_slot_for_each_resting_order():
    from multi_exchange_trader.paper.spot_store import SpotPaperStore

    store = object.__new__(SpotPaperStore)
    state = {"orders": [{"status": "ACCEPTED"}, {"status": "ACCEPTED"}]}
    with pytest.raises(ValueError, match="cancellation"):
        store.admission(498, state, order())
    store.admission(498, state, {"kind": "cancel"})
    store.admission(499, {"orders": [{"status": "ACCEPTED"}]}, {"kind": "cancel"})
