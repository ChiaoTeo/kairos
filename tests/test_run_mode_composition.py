from __future__ import annotations

import unittest

from kairos.application import (
    RunModeComposition, backtest_composition, historical_simulation_composition,
    live_composition, paper_trading_composition, research_composition,
)
from kairos.data.contracts import RunMode
from kairos.market_data import CapturePolicy


class RunModeCompositionTests(unittest.TestCase):
    def test_all_promotion_modes_have_explicit_replaceable_dependencies(self) -> None:
        values = (
            research_composition(), backtest_composition(), historical_simulation_composition(),
            paper_trading_composition("binance"), live_composition("binance", "binance-live"),
        )

        self.assertEqual([item.mode for item in values], [
            RunMode.RESEARCH, RunMode.BACKTEST, RunMode.HISTORICAL_SIMULATION,
            RunMode.PAPER_TRADING, RunMode.LIVE,
        ])
        self.assertEqual(len({item.composition_hash for item in values}), len(values))
        self.assertEqual(paper_trading_composition("binance").composition_hash,
                         paper_trading_composition("binance").composition_hash)

    def test_live_modes_fail_without_capture_or_persistence(self) -> None:
        with self.assertRaisesRegex(ValueError, "capture"):
            RunModeComposition(
                RunMode.PAPER_TRADING, "live", "system", "simulated", "runtime-store",
                "paper", CapturePolicy.NONE,
            )
        with self.assertRaisesRegex(ValueError, "persistence"):
            RunModeComposition(
                RunMode.LIVE, "live", "system", "venue", "none", "live",
                CapturePolicy.RAW_AND_CANONICAL,
            )

    def test_backtest_cannot_silently_use_wall_clock(self) -> None:
        with self.assertRaisesRegex(ValueError, "replay clock"):
            RunModeComposition(
                RunMode.BACKTEST, "release", "system", "fill-model", "artifact",
                "backtest", CapturePolicy.NONE,
            )

    def test_declaration_binds_real_components_and_executes(self) -> None:
        declaration=backtest_composition();calls=[]
        executable=declaration.bind(event_source=object(),clock=object(),execution_driver=object(),
            persistence=object(),safety_policy=object(),runner=lambda:calls.append("ran") or {"passed":True})
        self.assertEqual(executable.run(),{"passed":True});self.assertEqual(calls,["ran"])
        self.assertEqual(executable.composition_hash,declaration.composition_hash)


if __name__ == "__main__":
    unittest.main()
