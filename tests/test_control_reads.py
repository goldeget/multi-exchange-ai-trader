"""Read model composition keeps independent ledgers and isolates component failures."""

import unittest


class FakePaper:
    def scenarios(self):
        return ["healthy", "broken"]

    def state(self, scenario):
        if scenario == "broken":
            raise ValueError("secret-error-do-not-publish")
        return {"scenario_id": scenario, "equity": "10000.00000000"}


class FakeResearch:
    def runs(self):
        return [{"run_id": "independent", "status": "RUNNING",
                 "native": {"equity": "10000.00000000"}}]


class OverviewContracts(unittest.TestCase):
    def test_same_scenario_name_in_spot_and_perpetual_keeps_product_identity(self):
        from multi_exchange_trader.read_models import read_overview

        value = read_overview(FakePaper(), FakeResearch(), spot=FakePaper())
        self.assertEqual(len(value["ledgers"]), 2)
        self.assertEqual({row["product"] for row in value["ledgers"]}, {"SPOT", "SYNTHETIC_PERPETUAL"})
        self.assertNotIn("equity", value)

    def test_one_broken_ledger_preserves_other_independent_scopes(self):
        from multi_exchange_trader.read_models import read_overview

        value = read_overview(FakePaper(), FakeResearch())
        self.assertEqual(len(value["ledgers"]), 1)
        self.assertEqual(value["ledgers"][0]["id"], "healthy")
        self.assertEqual(value["automation"]["runs"][0]["run_id"], "independent")
        self.assertNotIn("equity", value)
        self.assertNotIn("secret-error", str(value))
        self.assertEqual(value["errors"][0]["code"], "PAPER_READ_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
