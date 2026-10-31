"""Synthetic local inputs, actual Nautilus v1 matching/accounting; no live results."""

from decimal import Decimal as D

import pytest


def replay(events):
    from multi_exchange_trader.paper.native import replay

    return replay(events)


def event(kind, **fields):
    return {"kind": kind, "instrument": "BTC-PAPER", **fields}


def test_native_long_partial_full_cash_fees_and_replay():
    events = [
        event("enter", side="long", quantity="0.100", leverage=10),
        event("tick", price="61000.0"),
        event("close", quantity="0.040"),
        event("tick", price="59000.0"),
        event("close", quantity="0.060"),
    ]
    state = replay(events)
    assert state["engine"] == "NautilusTrader 1.231.0 SimulatedExchange"
    assert state["wallet_balance"] == "9975.18800000"
    assert state["fees"] == "4.79200000"
    assert state["closed_net_pnl"] == "-24.81200000"
    assert state["positions"] == [] and len(state["fills"]) == 3
    assert [f["price"] for f in state["fills"]] == ["60000.1", "60999.9", "58999.9"]
    assert replay(events) == state


def test_native_short_profit_and_adverse_slippage():
    state = replay(
        [
            event("enter", side="short", quantity="0.100", leverage=20),
            event("tick", price="59000.0"),
            event("close", quantity="0.100"),
        ]
    )
    assert [f["price"] for f in state["fills"]] == ["59999.9", "59000.1"]
    assert state["wallet_balance"] == "10095.22000000"
    assert state["closed_net_pnl"] == "95.22000000"


def test_native_oco_gap_take_cancels_stop_and_no_second_exit():
    state = replay(
        [
            event("enter", side="long", quantity="0.100", leverage=10),
            event("protect", sl="59000.0", tp="61000.0"),
            event("tick", price="61500.0"),
            event("tick", price="58000.0"),
        ]
    )
    assert len(state["fills"]) == 2 and state["positions"] == []
    assert state["wallet_balance"] == "10145.12000000"
    assert any(o["role"] == "sl" and o["status"] == "CANCELED" for o in state["orders"])


def test_native_protection_replace_cancel_partial_reduce_only():
    events = [
        event("enter", side="long", quantity="0.100", leverage=10),
        event("protect", sl="59000.0", tp="62000.0"),
        event("protect", sl="58000.0", tp="61000.0"),
        event("close", quantity="0.040"),
        event("tick", price="61500.0"),
    ]
    state = replay(events)
    assert sum(D(f["quantity"]) for f in state["fills"] if f["side"] == "SELL") == D("0.100")
    assert state["positions"] == []
    canceled = replay(
        events[:2] + [event("protect", sl=None, tp=None), event("tick", price="62500.0")]
    )
    assert len(canceled["fills"]) == 1 and len(canceled["positions"]) == 1


def test_native_liquidation_gap_keeps_negative_cash_and_forbids_more_entry():
    state = replay(
        [event("enter", side="long", quantity="1.000", leverage=10), event("tick", price="1.0")]
    )
    assert D(state["wallet_balance"]) < 0 and state["positions"] == []
    assert any(f["role"] == "liquidation" for f in state["fills"])
    assert D(state["deficit"]) == -D(state["wallet_balance"])
    with pytest.raises(ValueError, match="margin"):
        replay(
            [
                event("enter", side="long", quantity="1.000", leverage=10),
                event("tick", price="1.0"),
                event("enter", side="long", quantity="0.001", leverage=1),
            ]
        )


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1", "0", "0.0001", 0.1, True, "1e-3"])
def test_invalid_quantities_rejected_before_native_order(bad):
    with pytest.raises(ValueError):
        replay([event("enter", side="long", quantity=bad, leverage=10)])


def test_isolated_reservation_partial_and_account_invariants():
    for qty in ["0.001", "0.003", "0.100"]:
        events = [event("enter", side="long", quantity=qty, leverage=3)]
        for mark in ["59990.0", "60010.0", "60000.0"]:
            s = replay(events + [event("tick", price=mark)])
            assert D(s["available_balance"]) + D(s["reserved_margin"]) - D(s["deficit"]) == D(
                s["wallet_balance"]
            )
            assert D(s["wallet_balance"]) + D(s["unrealized_pnl"]) == D(s["equity"])
            assert D(s["fees"]) >= 0


@pytest.mark.parametrize(
    "side,sl,tp,target,role",
    [
        ("long", "59000.0", "61000.0", "58000.0", "sl"),
        ("short", "61000.0", "59000.0", "58000.0", "tp"),
        ("short", "61000.0", "59000.0", "61500.0", "sl"),
    ],
)
def test_native_long_short_conditional_roles_and_no_reverse(side, sl, tp, target, role):
    s = replay(
        [
            event("enter", side=side, quantity="0.100", leverage=10),
            event("protect", sl=sl, tp=tp),
            event("tick", price=target),
            event("tick", price=target),
        ]
    )
    assert len(s["fills"]) == 2 and s["fills"][-1]["role"] == role and s["positions"] == []


@pytest.mark.parametrize("lev", [0, 21, True, 1.5])
def test_leverage_invalid_and_margin_excess(lev):
    with pytest.raises(ValueError):
        replay([event("enter", side="long", quantity="0.100", leverage=lev)])
    with pytest.raises(ValueError, match="margin"):
        replay([event("enter", side="long", quantity="1.000", leverage=1)])


def test_no_outbound_from_actual_native_replay(monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("PAPER must not connect outbound")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    assert len(replay([event("enter", side="long", quantity="0.001", leverage=1)])["fills"]) == 1


def test_liquidation_boundary_at_adjacent_price_ticks_and_rounding():
    # Hand boundary:0.1*m−5700.0095 <=0.00054*m =>m<=57309.5666599...
    entry = event("enter", side="long", quantity="0.100", leverage=20)
    safe = replay([entry, event("tick", price="57309.6")])
    gone = replay([entry, event("tick", price="57309.5")])
    assert len(safe["positions"]) == 1 and gone["positions"] == []
    thirds = replay([event("enter", side="long", quantity="0.001", leverage=3)])
    assert thirds["reserved_margin"] == "20.00003334"  # 60.0001/3 rounded up,8dp.


def test_flat_reopen_short_after_long_native_account_history():
    events = [
        event("enter", side="long", quantity="0.100", leverage=10),
        event("tick", price="61000.0"),
        event("close", quantity="0.100"),
        event("enter", side="short", quantity="0.100", leverage=5),
        event("tick", price="60000.0"),
        event("close", quantity="0.100"),
    ]
    s = replay(events)
    assert len(s["fills"]) == 4 and s["positions"] == []
    # Long gross99.98;short gross99.98;fees2.400004+2.439996+2.439996+2.400004=9.68.
    assert s["wallet_balance"] == "10190.28000000" and s["closed_net_pnl"] == "190.28000000"
