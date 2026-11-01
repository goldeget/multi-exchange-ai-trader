"""Dependency-free contract smoke checks, also collected by pytest."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path


class ControlContracts(unittest.TestCase):
    def test_parallel_budget_reservations_cannot_exceed_limit(self):
        from multi_exchange_trader.ai.budget import BudgetBook

        with tempfile.TemporaryDirectory() as folder:
            book = BudgetBook(Path(folder) / "ai.sqlite3")
            book.configure(0, "1.00")

            def attempt(index):
                try:
                    book.reserve(f"request-{index}", "0.40")
                    return True
                except ValueError:
                    return False

            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(sum(pool.map(attempt, range(4))), 2)
            self.assertEqual(book.summary()["remaining"], "0.20000000")

    def test_review_binding_survives_lost_result_and_expiry(self):
        """Controlled ledger double qualifies the sidecar only, never the native engine."""
        from multi_exchange_trader.paper.order_preview import OrderReviews

        class LedgerDouble:
            def __init__(self):
                self.results = {}
                self.lose_response = True

            def preview_command(self, scenario, command):
                state = {"revision": 1, "fees": "0", "available_balance": "10000", "reserved_margin": "0"}
                return state, state | {"fees": "0.01"}

            def command(self, scenario, key, command, **checks):
                if key not in self.results:
                    self.results[key] = {"revision": 2}
                if self.lose_response:
                    self.lose_response = False
                    raise TimeoutError("simulated loss after commit")
                return self.results[key]

        with tempfile.TemporaryDirectory() as folder:
            ledger = LedgerDouble()
            path = Path(folder) / "reviews.sqlite3"
            reviews = OrderReviews(path, ledger)
            preview = reviews.preview({"scenario_id": "manual", "product": "SYNTHETIC_PERPETUAL",
                "order_type": "MARKET", "config_revision": "PAPER-v1", "command": {
                    "kind": "enter", "instrument": "BTC-PAPER", "quantity": "0.001",
                    "side": "long", "leverage": 3}})
            with self.assertRaises(TimeoutError):
                reviews.confirm(preview["preview_id"], "same-request")
            self.assertEqual(reviews.unresolved_count(), 1)
            restarted = OrderReviews(path, ledger, clock=lambda: datetime.now(UTC) + timedelta(hours=1))
            result = restarted.confirm(preview["preview_id"], "same-request")
            self.assertEqual(result["status"], "confirmed")
            self.assertEqual(len(ledger.results), 1)
            self.assertEqual(restarted.unresolved_count(), 0)
            with self.assertRaisesRegex(ValueError, "idempotency"):
                restarted.confirm(preview["preview_id"], "new-request")

    def test_unqualified_preview_refused_before_ledger_access(self):
        from multi_exchange_trader.paper.order_preview import OrderReviews

        with tempfile.TemporaryDirectory() as folder:
            reviews = OrderReviews(Path(folder) / "reviews.sqlite3", None)
            with self.assertRaises(ValueError):
                reviews.preview({"scenario_id": "manual", "product": "SPOT",
                                 "order_type": "LIMIT", "config_revision": "PAPER-v1",
                                 "command": {"kind": "spot_limit"}})

    def test_capability_source_and_execution_are_separate(self):
        from multi_exchange_trader.capabilities import capabilities

        result = capabilities(account_registered=True)
        self.assertFalse(result["live_order_enabled"])
        self.assertTrue(result["account_readonly"]["bybit"])
        self.assertFalse(result["manual_paper"]["spot_limit"]["supported"])

    def test_native_runtime_readiness_is_distinct_from_supported_contract(self):
        from multi_exchange_trader.capabilities import capabilities

        result = capabilities(native_runtime_available=False)
        self.assertTrue(result["manual_paper"]["synthetic_perpetual"]["enter"])
        self.assertFalse(result["manual_paper"]["available"])
        self.assertEqual(result["manual_paper"]["unavailable_reason"], "native_sandbox_unavailable")

    def test_budget_reservation_survives_restart(self):
        from multi_exchange_trader.ai.budget import BudgetBook

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "budget.sqlite3"
            book = BudgetBook(path)
            book.configure(0, "1.00")
            book.reserve("request", "0.50")
            book.uncertain("request")
            restarted = BudgetBook(path)
            self.assertEqual(restarted.summary()["reserved"], "0.50000000")
            self.assertEqual(restarted.summary()["uncertain"], "0.50000000")
            result = restarted.settle("request", "0.10")
            self.assertEqual(restarted.settle("request", "0.10"), result)
            self.assertEqual(restarted.summary()["used"], "0.10000000")


if __name__ == "__main__":
    unittest.main()
