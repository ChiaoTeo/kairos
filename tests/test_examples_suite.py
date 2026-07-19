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
        self.assertEqual(result["factor_snapshots"], result["bars"])
        self.assertGreater(result["economic_intents"], 0)
        self.assertGreater(result["immediate_intent_trades"], 0)
        self.assertTrue(result["all_current_intents_satisfied"])
        self.assertTrue(all(len(result[name]) == 64 for name in (
            "factor_hash", "decision_hash", "intent_hash", "strategy_run_audit_hash",
        )))

    def test_sma_research_candidate_and_factor_release_lifecycle(self):
        result = run_example("examples/research/sma_factor_lifecycle.py")
        self.assertTrue(result["sandbox_workspace"])
        self.assertTrue(result["frozen_candidate"])
        self.assertTrue(result["factor_release"])
        self.assertTrue(result["batch_replay_equal"])
        self.assertEqual(len(result["factor_hash"]), 64)

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

    def test_sma_historical_simulation_runs_durable_execution_and_restart(self):
        result = run_example("examples/runtime/sma_historical_simulation.py")
        self.assertGreater(result["orders"], 0)
        self.assertGreater(result["fills"], 0)
        self.assertTrue(result["restart_ready"])
        self.assertTrue(result["runtime_database_exists"])

    def test_sma_live_paper_capture_and_offline_replay(self):
        result=run_example("examples/runtime/sma_paper_session.py")
        self.assertEqual(result["mode"],"live-paper");self.assertGreater(result["fills"],0)
        self.assertTrue(result["restart_ready"]);self.assertTrue(result["capture_replay_passed"])

    def test_complex_option_strategy_binds_research_factor_to_executable_strategy(self):
        result=run_example("examples/strategy/bull_put_spread_lifecycle.py")
        self.assertEqual(result["research_evidence"],"TRADE_PROXY_ONLY")
        self.assertTrue(result["formal_strategy_consumed_factor"]);self.assertTrue(result["replay_equal"])

    def test_multi_asset_reference_strategies_are_complete_releases(self):
        result=run_example("examples/strategy/multi_asset_reference_lifecycle.py")
        self.assertTrue(result["protective_put_release_complete"])
        self.assertTrue(all(item["economic_replay_equal"] for item in result["strategies"].values()))

    def test_audited_manual_order_example(self):
        result=run_example("examples/operations/manual_order.py")
        self.assertTrue(result["accepted"]);self.assertTrue(result["actor_recorded"]);self.assertTrue(result["reason_recorded"])

    def test_sma_backtest_and_historical_simulation_match_before_execution_boundary(self):
        backtest = run_example("examples/backtest/governed_sma.py")
        simulation = run_example("examples/runtime/sma_historical_simulation.py")
        for name in ("factor_hash", "decision_hash", "intent_hash"):
            self.assertEqual(backtest[name], simulation[name])

    def test_reference_adapter_passes_language_boundary_vectors(self):
        result = run_example("examples/adapters/reference_adapter/verify_contract.py")
        self.assertTrue(result["passed"])
        self.assertGreaterEqual(result["vectors"], 1)


if __name__ == "__main__":
    unittest.main()
