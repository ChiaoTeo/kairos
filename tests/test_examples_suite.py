from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).parents[1]


def run_example(path: str):
    completed = subprocess.run(
        [sys.executable, path], cwd=ROOT, check=True, capture_output=True, text=True,
    )
    return json.loads(completed.stdout)


class ExamplesSuiteTests(unittest.TestCase):
    def test_governed_sma_fixture_runs_batch_and_canonical_replay(self):
        result = run_example("examples/backtest/governed_sma.py")
        self.assertTrue(result["batch_equals_canonical_replay"])
        self.assertGreater(result["bars"], 15)
        self.assertEqual(len(result["audit_hash"]), 64)

    def test_strategy_capture_replay_example_is_deterministic(self):
        first = run_example("examples/replay/live_vs_replay_strategy.py")
        second = run_example("examples/replay/live_vs_replay_strategy.py")
        self.assertTrue(first["live_equals_replay"])
        self.assertEqual(first["audit_hash"], second["audit_hash"])
        self.assertEqual(first["decision_hash"], second["decision_hash"])
        self.assertEqual(first["intent_hash"], second["intent_hash"])

    def test_run_mode_example_exposes_all_five_compositions(self):
        result = run_example("examples/runtime/run_modes.py")
        self.assertEqual(len(result["modes"]), 5)
        self.assertEqual({item["mode"] for item in result["modes"]}, {
            "research", "backtest", "historical-simulation", "live-paper", "live",
        })
        self.assertTrue(all(len(item["composition_hash"]) == 64 for item in result["modes"]))

    def test_reference_adapter_passes_language_boundary_vectors(self):
        result = run_example("examples/adapters/reference_adapter/verify_contract.py")
        self.assertTrue(result["passed"])
        self.assertGreaterEqual(result["vectors"], 1)


if __name__ == "__main__":
    unittest.main()
