from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from examples.lifecycle.full_product_acceptance import run


class FullProductAcceptanceTests(unittest.TestCase):
    def test_all_documented_scenarios_run_in_one_product_journey(self):
        with tempfile.TemporaryDirectory() as directory:result=run(Path(directory))
        self.assertTrue(result["passed"]);self.assertTrue(all(result["scenarios"].values()))
        self.assertTrue(result["sma_execution_boundary_parity"]);self.assertTrue(result["multi_asset_releases"])
        self.assertTrue(result["multi_strategy_portfolio"])


if __name__=="__main__":unittest.main()
