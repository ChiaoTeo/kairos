from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

from examples.runtime.sma_historical_simulation import run


class SmaHistoricalSimulationTests(unittest.TestCase):
    def test_durable_simulation_orders_fills_ledger_and_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = asyncio.run(run(Path(directory)))

        self.assertEqual(result["mode"], "historical-simulation")
        self.assertGreater(result["orders"], 0)
        self.assertGreater(result["fills"], 0)
        self.assertTrue(result["restart_ready"])
        self.assertTrue(result["runtime_database_exists"])
        self.assertTrue(all(len(result[name]) == 64 for name in (
            "factor_hash", "decision_hash", "intent_hash",
        )))


if __name__ == "__main__":
    unittest.main()
