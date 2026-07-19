from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from examples.strategy.bull_put_spread_lifecycle import run


class BullPutSpreadLifecycleTests(unittest.TestCase):
    def test_skew_factor_is_bound_to_formal_strategy_and_executable_replay(self):
        with tempfile.TemporaryDirectory() as directory:result=run(Path(directory))
        self.assertTrue(result["study_candidate"]);self.assertEqual(result["research_evidence"],"TRADE_PROXY_ONLY")
        self.assertTrue(result["synthetic_mechanics_only"]);self.assertEqual(result["strategy_version"],"1.2.0")
        self.assertEqual(result["factor_spec_hash"].__len__(),64);self.assertGreater(result["conservative_fills"],0)
        self.assertTrue(result["formal_strategy_consumed_factor"]);self.assertTrue(result["replay_equal"])


if __name__=="__main__":unittest.main()
