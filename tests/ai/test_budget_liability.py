"""Observed charges remain durable liabilities, never renewed spending permission."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, localcontext

import httpx
import pytest
from fastapi import FastAPI

from multi_exchange_trader.ai.budget import BudgetBook


@pytest.mark.parametrize("actual,remaining", [("0.95", "0.05000000"), ("1.20", "0.00000000"),
                                           ("150.123456789", "0.00000000")])
def test_overrun_is_persisted_and_blocks_new_reservations_after_restart(tmp_path, actual, remaining):
    path = tmp_path / "budget.sqlite3"
    book = BudgetBook(path)
    book.configure(0, "1.00")
    book.reserve("paid", "0.80")
    settled = book.settle("paid", actual)
    assert Decimal(settled["actual"]) == Decimal(actual)
    restarted = BudgetBook(path)
    summary = restarted.summary()
    assert summary["used"] == {"0.95": "0.95000000", "1.20": "1.20000000",
                               "150.123456789": "150.12345679"}[actual]
    assert summary["remaining"] == remaining
    assert summary["reserved"] == "0.00000000"
    assert summary["overrun_hold"] is True
    assert summary["overrun_count"] == 1
    assert summary["overrun_liability"] == summary["used"]
    assert restarted.settle("paid", actual) == settled
    assert restarted.reserve("paid", "0.80") == settled
    with pytest.raises(ValueError, match="conflict"):
        restarted.settle("paid", "0.96")
    with pytest.raises(ValueError, match="overrun"):
        restarted.reserve("new", "0.01")
    assert restarted.settings()["mode"] == "OFFLINE_TEST"
    assert restarted.settings()["llm_enabled"] is False
    with pytest.raises(ValueError):
        restarted.configure(1, "101")


def test_hold_survives_month_boundary_and_larger_configured_budget(tmp_path):
    path = tmp_path / "budget.sqlite3"
    book = BudgetBook(path)
    book.configure(0, "1")
    book.reserve("paid", "0.80")
    book.settle("paid", "0.95")
    with book.connect() as db:
        db.execute("UPDATE reservations SET period='2000-01' WHERE request_id='paid'")
        db.commit()
    restarted = BudgetBook(path)
    restarted.configure(1, "100")
    summary = restarted.summary()
    assert summary["used"] == "0.00000000"
    assert summary["remaining"] == "100.00000000"
    assert summary["overrun_liability"] == "0.95000000"
    assert summary["overrun_hold"] is True
    with pytest.raises(ValueError, match="overrun"):
        restarted.reserve("new-month", "0.01")


def test_unknown_charge_retains_full_maximum_across_restart(tmp_path):
    path = tmp_path / "budget.sqlite3"
    book = BudgetBook(path)
    book.configure(0, "1")
    book.reserve("unknown", "0.80")
    book.uncertain("unknown")
    summary = BudgetBook(path).summary()
    assert summary["reserved"] == "0.80000000"
    assert summary["uncertain"] == "0.80000000"
    assert summary["remaining"] == "0.20000000"
    assert summary["overrun_hold"] is False


def test_unit_rounding_and_totals_ignore_ambient_decimal_precision(tmp_path):
    book = BudgetBook(tmp_path / "budget.sqlite3")
    with localcontext() as context:
        context.prec = 2
        book.configure(0, "1.12345678")
        book.reserve("first", "0.50000000")
        book.reserve("second", "0.50000000")
        book.settle("first", "0.123456780000000000000000000001")
        book.settle("second", "0.000000000000000000000000000001")
        summary = book.summary()
    assert summary["used"] == "0.12345680"
    assert summary["remaining"] == "0.99999998"


def test_distinct_exact_charges_conflict_even_if_they_round_to_same_unit(tmp_path):
    book = BudgetBook(tmp_path / "budget.sqlite3")
    book.configure(0, "1")
    book.reserve("paid", "0.80")
    settled = book.settle("paid", "0.8000000001")
    assert book.settle("paid", "0.8000000001000") == settled
    with pytest.raises(ValueError, match="conflict"):
        book.settle("paid", "0.8000000002")
    assert book.summary()["overrun_hold"] is True


def test_old_schema_and_large_settled_liability_remain_readable(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE ai_settings (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
                                      monthly_limit TEXT NOT NULL);
            INSERT INTO ai_settings VALUES (1,7,'1.00000000');
            CREATE TABLE reservations (request_id TEXT PRIMARY KEY, period TEXT NOT NULL,
                maximum TEXT NOT NULL, actual TEXT, status TEXT NOT NULL, updated_at TEXT NOT NULL);
            INSERT INTO reservations VALUES ('old', '2000-01', '0.80000000', '150.25',
                                              'settled', '2000-01-01T00:00:00+00:00');
        """)
    for _ in range(2):
        book = BudgetBook(path)
        assert book.settings()["revision"] == 7
        assert book.reconcile("old")["actual"] == "150.25"
        assert book.summary()["overrun_liability"] == "150.25000000"
        with pytest.raises(ValueError, match="overrun"):
            book.reserve("new", "0.01")


def test_sqlite_failure_does_not_report_or_partially_persist_settlement(tmp_path):
    path = tmp_path / "budget.sqlite3"
    book = BudgetBook(path)
    book.configure(0, "1")
    book.reserve("paid", "0.80")
    with book.connect() as db:
        db.execute("""CREATE TRIGGER deny_settlement BEFORE UPDATE OF actual ON reservations
                      BEGIN SELECT RAISE(ABORT, 'test storage failure'); END""")
        db.commit()
    with pytest.raises(sqlite3.DatabaseError):
        book.settle("paid", "1.20")
    persisted = BudgetBook(path).reconcile("paid")
    assert persisted["actual"] is None
    assert persisted["status"] == "reserved"
    assert book.summary()["reserved"] == "0.80000000"


def test_parallel_new_reservations_remain_atomic(tmp_path):
    book = BudgetBook(tmp_path / "budget.sqlite3")
    book.configure(0, "1")

    def reserve(index):
        try:
            book.reserve(f"parallel-{index}", "0.60")
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(reserve, range(4))) == 1
    assert book.summary()["reserved"] == "0.60000000"


def test_largest_supported_exact_actual_is_not_capped_or_rounded_down(tmp_path):
    path = tmp_path / "budget.sqlite3"
    book = BudgetBook(path)
    book.configure(0, "1")
    book.reserve("paid", "0.80")
    actual = "9" * 64 + "." + "9" * 64
    with localcontext() as context:
        context.prec = 2
        book.settle("paid", actual)
        summary = book.summary()
    assert book.reconcile("paid")["actual"] == actual
    assert summary["used"] == "1" + "0" * 64 + ".00000000"
    assert BudgetBook(path).summary() == summary


def test_zero_settlement_releases_only_its_reservation_and_does_not_clear_hold(tmp_path):
    book = BudgetBook(tmp_path / "budget.sqlite3")
    book.configure(0, "1")
    book.reserve("overrun", "0.40")
    book.reserve("unused", "0.40")
    book.settle("overrun", "0.41")
    book.settle("unused", "0")
    summary = book.summary()
    assert summary["reserved"] == "0.00000000"
    assert summary["used"] == "0.41000000"
    assert summary["overrun_hold"] is True
    with pytest.raises(ValueError, match="overrun"):
        book.reserve("new", "0.01")


async def test_budget_api_exposes_liability_and_hold_without_enabling_generation(tmp_path):
    from multi_exchange_trader.controls import install

    app = FastAPI()
    book = BudgetBook(tmp_path / "budget.sqlite3")
    app.state.ai_budget = book
    book.configure(0, "1")
    book.reserve("paid", "0.80")
    book.settle("paid", "1.20")
    install(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as client:
        response = await client.get("/api/v1/ai/budget")
        assert response.status_code == 200
        payload = response.json()
        assert payload["used"] == "1.20000000"
        assert payload["remaining"] == "0.00000000"
        assert payload["overrun_hold"] is True
        assert payload["provider_balance"] is None
        settings = (await client.get("/api/v1/settings/ai")).json()
        assert settings["mode"] == "OFFLINE_TEST"
        assert settings["llm_enabled"] is False


@pytest.mark.parametrize("actual", ["NaN", "Infinity", "-1", "1e3", "", " 1", "01", "١", True,
                                    1.0, None, "1" * 65, "0." + "1" * 65])
def test_unsupported_actual_evidence_cannot_release_reservation(tmp_path, actual):
    book = BudgetBook(tmp_path / "budget.sqlite3")
    book.configure(0, "1")
    book.reserve("paid", "0.80")
    with pytest.raises(ValueError):
        book.settle("paid", actual)
    assert book.reconcile("paid")["status"] == "reserved"
    assert book.summary()["reserved"] == "0.80000000"
