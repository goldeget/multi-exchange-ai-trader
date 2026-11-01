"""Pure control contracts; these tests never start the native worker or an exchange."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest


def test_capabilities_do_not_confuse_account_registration_with_order_permission():
    from multi_exchange_trader.capabilities import capabilities

    value = capabilities(account_registered=True)
    assert value["live_order_enabled"] is False
    assert value["account_readonly"]["bybit"] is True
    assert value["manual_paper"]["spot_limit"]["supported"] is False
    assert value["analysis"] == {"sources": ["RULE"], "llm_enabled": False}


def test_budget_parallel_reservations_survive_restart_and_settle_idempotently(tmp_path):
    from multi_exchange_trader.ai.budget import BudgetBook

    path = tmp_path / "ai.sqlite3"
    book = BudgetBook(path)
    book.configure(0, "8.00")

    def reserve(index):
        try:
            book.reserve(f"request-{index}", "3.00")
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(reserve, range(4))) == 2
    restarted = BudgetBook(path)
    summary = restarted.summary()
    assert Decimal(summary["reserved"]) == Decimal(6)
    assert Decimal(summary["remaining"]) == Decimal(2)
    with restarted.connect() as db:
        key = db.execute("SELECT request_id FROM reservations LIMIT 1").fetchone()[0]
    restarted.uncertain(key)
    assert Decimal(restarted.summary()["uncertain"]) == Decimal(3)
    restarted.settle(key, "0.25")
    restarted.settle(key, "0.25")
    assert Decimal(restarted.summary()["used"]) == Decimal("0.25")
    with pytest.raises(ValueError, match="conflict"):
        restarted.settle(key, "0.50")
    with pytest.raises(ValueError):
        restarted.configure(0, "8.00")


def test_budget_unknown_usage_keeps_reservation_and_cannot_lower_below_exposure(tmp_path):
    from multi_exchange_trader.ai.budget import BudgetBook

    book = BudgetBook(tmp_path / "ai.sqlite3")
    book.configure(0, "1.00")
    book.reserve("timeout", "0.80")
    book.uncertain("timeout")
    with pytest.raises(ValueError):
        book.configure(1, "0.50")
    with pytest.raises(ValueError):
        book.settle("timeout", "0.81")
    assert Decimal(book.summary()["reserved"]) == Decimal("0.80")
    assert book.settings()["mode"] == "OFFLINE_TEST"
    assert book.settings()["provider_status"] == "NOT_CONFIGURED"


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "0.000000001", 1.0, True])
def test_budget_refuses_invalid_money(tmp_path, amount):
    from multi_exchange_trader.ai.budget import BudgetBook

    with pytest.raises(ValueError):
        BudgetBook(tmp_path / "ai.sqlite3").configure(0, amount)
