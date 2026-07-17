from __future__ import annotations

import unittest

from trading.application import (
    RunModeComposition, backtest_composition, historical_simulation_composition,
    live_composition, live_paper_composition, research_composition,
)
from trading.data.models import RunMode
from trading.market_data import CapturePolicy


class RunModeCompositionTests(unittest.TestCase):
    def test_all_promotion_modes_have_explicit_replaceable_dependencies(self) -> None:
        values = (
            research_composition(), backtest_composition(), historical_simulation_composition(),
            live_paper_composition("binance"), live_composition("binance", "binance-live"),
        )

        self.assertEqual([item.mode for item in values], [
            RunMode.RESEARCH, RunMode.BACKTEST, RunMode.HISTORICAL_SIMULATION,
            RunMode.LIVE_PAPER, RunMode.LIVE,
        ])
        self.assertEqual(len({item.composition_hash for item in values}), len(values))
        self.assertEqual(live_paper_composition("binance").composition_hash,
                         live_paper_composition("binance").composition_hash)

    def test_live_modes_fail_without_capture_or_persistence(self) -> None:
        with self.assertRaisesRegex(ValueError, "capture"):
            RunModeComposition(
                RunMode.LIVE_PAPER, "live", "system", "simulated", "runtime-store",
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


if __name__ == "__main__":
    unittest.main()
