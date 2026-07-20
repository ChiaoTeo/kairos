from __future__ import annotations

import unittest

from kairos.backtest.reference_scenarios import run_reference_scenario


class ReferenceScenarioTests(unittest.TestCase):
    def test_covered_call_and_spot_perp_are_deterministic_and_stress_is_worse(self) -> None:
        for strategy in ("covered-call", "spot-perp-carry"):
            with self.subTest(strategy=strategy):
                conservative = run_reference_scenario(strategy, "conservative")
                replay = run_reference_scenario(strategy, "conservative")
                stress = run_reference_scenario(strategy, "stress")
                self.assertEqual(conservative, replay)
                self.assertLess(stress.final_cash, conservative.final_cash)
                self.assertNotEqual(stress.audit_hash, conservative.audit_hash)
                self.assertGreater(conservative.ledger_transactions, 0)


if __name__ == "__main__":
    unittest.main()
