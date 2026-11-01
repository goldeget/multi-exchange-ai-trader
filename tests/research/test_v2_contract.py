"""Versioned run settings; these tests require no native engine or network."""

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from multi_exchange_trader.research.automation import default_auto_config, validate_config
from multi_exchange_trader.research.data import synthetic_dataset


class VersionedConfigTests(unittest.TestCase):
    def setUp(self):
        self.dataset = synthetic_dataset()

    def v2(self):
        try:
            return default_auto_config(self.dataset["dataset_id"], version=2)
        except TypeError:
            self.fail("A new V2 run must accept explicit versioned settings")

    def test_existing_config_keeps_its_exact_v1_shape(self):
        config = default_auto_config(self.dataset["dataset_id"])
        self.assertNotIn("config_version", config)
        self.assertNotIn("leverage", config)
        validate_config(config, self.dataset)

    def test_v2_leverage_is_explicit_and_validated(self):
        config = self.v2()
        self.assertEqual(config["config_version"], 2)
        self.assertEqual(config["leverage"], 3)
        validate_config(config, self.dataset)
        for invalid in (True, 0, 21, "3", 3.5):
            with self.subTest(leverage=invalid), self.assertRaises(ValueError):
                validate_config({**config, "leverage": invalid}, self.dataset)

    def test_config_version_cannot_silently_change_v1_semantics(self):
        self.v2()
        original = default_auto_config(self.dataset["dataset_id"])
        for fields in ({"leverage": 3}, {"config_version": 2}, {"config_version": True}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                validate_config({**original, **fields}, self.dataset)
        with self.assertRaises(ValueError):
            default_auto_config(self.dataset["dataset_id"], version=99)

    def test_configured_leverage_reaches_the_existing_native_event_path(self):
        from multi_exchange_trader.research.automation import advance

        state = self.state("RUNNING")
        with patch("multi_exchange_trader.research.automation.sandbox_replay", self.account):
            result = advance(state, self.dataset)
        entries = [event for event in result["native_events"] if event["kind"] == "enter"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["leverage"], 3)

    def test_entry_pause_advances_prices_without_generating_an_entry(self):
        from multi_exchange_trader.research.automation import advance

        state = self.state("ENTRY_PAUSED")
        with patch("multi_exchange_trader.research.automation.sandbox_replay", self.account):
            result = advance(state, self.dataset)
        self.assertEqual(result["cursor"], 11)
        self.assertEqual([e["kind"] for e in result["native_events"]], ["tick"] * 4)
        self.assertEqual(result["status"], "ENTRY_PAUSED")

    def test_entry_paused_run_is_still_scheduled_and_stops_at_source_end(self):
        from multi_exchange_trader.research.automation import advance
        from multi_exchange_trader.research.scheduler import _overdue

        state = self.state("ENTRY_PAUSED")
        state["config"]["bar_limit"] = 11
        state["next_due_at"] = "2026-01-01T00:00:00+00:00"
        self.assertTrue(_overdue(state, datetime(2026, 1, 2, tzinfo=UTC)))
        with patch("multi_exchange_trader.research.automation.sandbox_replay", self.account):
            result = advance(state, self.dataset)
        self.assertEqual(result["status"], "STOPPED")

    def test_durable_entry_pause_and_explicit_restart_keep_original_config(self):
        from multi_exchange_trader.research.data import checksum
        from multi_exchange_trader.research.storage import ResearchStore

        with tempfile.TemporaryDirectory() as directory, patch(
            "multi_exchange_trader.research.storage.sandbox_replay", self.account
        ), patch("multi_exchange_trader.research.automation.sandbox_replay", self.account):
            path = Path(directory)
            store = ResearchStore(path / "state.sqlite3", path / "data")
            config = self.v2()
            state = store.create("v2", "create", config)
            self.assertEqual(state.get("config_hash"), checksum(config))
            store.command("v2", "start", {"action": "resume"})
            try:
                paused = store.command("v2", "entrypause", {"action": "pause_entries"})
            except ValueError:
                self.fail("V2 entry-only pause must be a durable, scheduled command")
            self.assertEqual(paused["status"], "ENTRY_PAUSED")
            result = store.step("v2", 0, now=datetime.now(UTC) + timedelta(days=1))
            self.assertEqual(result["cursor"], 1)
            restarted = ResearchStore(store.path, store.data_root)
            recovered = restarted.state("v2")
            self.assertEqual(recovered["status"], "PAUSED_RECOVERY")
            self.assertTrue(recovered["entry_paused"])
            resumed = restarted.command("v2", "resume-process", {"action": "resume"})
            self.assertEqual(resumed["status"], "ENTRY_PAUSED")
            entered = restarted.command("v2", "resume-entries", {"action": "resume_entries"})
            self.assertEqual(entered["status"], "RUNNING")
            self.assertEqual(entered["config_hash"], state["config_hash"])
            self.assertEqual(restarted.command("v2", "resume-entries", {"action": "resume_entries"}), entered)

    def state(self, status):
        return {
            "config": self.v2(), "cursor": 10, "status": status,
            "native_events": [], "native": self.account([]), "high_water": "10000",
            "loss_streak": 0, "last_closed_net": "0", "processed_closed_trades": 0,
        }

    @staticmethod
    def account(_events):
        # Domain contract only: actual native fills/reconciliation have separate Linux tests.
        return {"equity": "10000", "positions": [], "fills": [], "closed_net_pnl": "0"}


if __name__ == "__main__":
    unittest.main()
