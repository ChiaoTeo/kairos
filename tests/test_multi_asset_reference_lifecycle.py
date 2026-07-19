from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from examples.strategy.multi_asset_reference_lifecycle import run


class MultiAssetReferenceLifecycleTests(unittest.TestCase):
    def test_formal_releases_run_strategy_runtime_and_ledger_scenarios(self):
        with tempfile.TemporaryDirectory() as directory:result=run(Path(directory))
        self.assertTrue(result["protective_put_release_complete"])
        for value in result["strategies"].values():
            self.assertTrue(value["release_complete"]);self.assertTrue(value["economic_replay_equal"])
            self.assertTrue(value["stress_is_worse"]);self.assertGreater(value["ledger_transactions"],0)


if __name__=="__main__":unittest.main()
