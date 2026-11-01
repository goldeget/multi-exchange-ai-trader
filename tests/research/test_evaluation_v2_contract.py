"""Exact submitted evaluation settings, bounds and versions; no native dependency."""

import importlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from multi_exchange_trader.research.data import synthetic_dataset


class EvaluationConfigTests(unittest.TestCase):
    def setUp(self):
        self.data = synthetic_dataset()
        self.config = {
            "config_version": 2, "start_ms": self.data["start_ms"],
            "end_ms": self.data["end_ms"], "fast": 20, "slow": 60,
            "quantity": "0.010", "fee_rate": "0.001", "slippage_ticks": 2, "leverage": 3,
        }

    def validator(self):
        try:
            return importlib.import_module("multi_exchange_trader.research.configuration").evaluation_config
        except ImportError:
            self.fail("Versioned evaluation settings must be validated before native work")

    def test_submitted_parameters_are_not_ignored(self):
        normalized, start, end = self.validator()(self.config, self.data)
        self.assertEqual(normalized, self.config)
        self.assertEqual((start, end), (0, 240))

    def test_range_is_aligned_bounded_and_large_enough(self):
        valid = self.validator()
        for fields in (
            {"start_ms": self.data["start_ms"] - 60000},
            {"end_ms": self.data["end_ms"] + 60000},
            {"start_ms": self.data["start_ms"] + 1},
            {"end_ms": self.data["start_ms"] + 99 * 60000},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                valid({**self.config, **fields}, self.data)

    def test_unrecognized_cost_and_strategy_inputs_are_refused(self):
        valid = self.validator()
        for fields in (
            {"fast": 60}, {"slow": 101}, {"fee_rate": "NaN"}, {"fee_rate": "0.02"},
            {"slippage_ticks": True}, {"slippage_ticks": 21}, {"leverage": 0},
            {"config_version": True}, {"unknown": "ignored"}, {"quantity": "0.00001"},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                valid({**self.config, **fields}, self.data)

    def test_report_keeps_the_submitted_range_windows_and_costs(self):
        from multi_exchange_trader.research.evaluation import evaluate

        observed = []

        def run(_dataset, config, start, end):
            observed.append((config, start, end))
            return {"initial_equity": "10000", "curve": [{"equity": "10000"}],
                    "trades": [], "net_pnl": "0", "config": config}

        native_name = "multi_exchange_trader.research.native"
        with patch.dict(sys.modules, {native_name: SimpleNamespace(run_segment=run)}):
            try:
                report = evaluate(self.data, self.config)
            except TypeError:
                self.fail("The evaluation must accept and execute explicit V2 settings")
        self.assertEqual(report["schema"], "RESEARCH_REPORT_V2")
        self.assertEqual(report["evaluation_config"], self.config)
        self.assertEqual(report["selected_config"]["fast"], 20)
        self.assertEqual(report["selected_config"]["slow"], 60)
        self.assertEqual(report["assumptions"]["fee_rate"], "0.001")
        self.assertTrue(observed)
        self.assertTrue(all(c["fee_rate"] == "0.001" and c["slippage_ticks"] == 2
                            for c, _start, _end in observed))


if __name__ == "__main__":
    unittest.main()
