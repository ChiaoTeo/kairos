from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

try:
    import numpy as np
    import pandas as pd
except ImportError:
    np = pd = None

if pd is not None:
    from studies.spxw_put_skew.analysis import (
        data_quality_report, frozen_parameter_validation, predictability_report,
        risk_decomposition, robustness_sensitivity, strategy_comparison, surface_observations,
    )
    from tests.test_options_study_end_to_end import internally_priceable_spxw_dataset


def study_panel():
    rows = []
    start = datetime(2020, 1, 1, 15, 30, tzinfo=timezone.utc)
    for index in range(180):
        rank = (index % 20) / 20
        pnl = -20 + rank * 100 + (5 if index % 3 else -10)
        rows.append({
            "timestamp": start + timedelta(days=index), "sample": "development" if index < 108 else "validation" if index < 144 else "test",
            "skew_rank": rank, "atm_iv_rank": (index % 10) / 10, "spot_trend": 0.01 if index % 2 else -0.01,
            "high_skew": rank >= 0.8,
            "strategy_pnl": pnl, "short_strike": 5900.0, "long_strike": 5850.0, "multiplier": 100.0, "entry_credit": 2.0,
            "strategy_pnl_delay_2": pnl - 1.0,
            "put25_atm_skew": 0.02 + rank * 0.08, "forward_skew_change": 0.02 - rank * 0.05,
            "forward_spot_return": -0.01 + rank * 0.02, "forward_realized_vol": 0.15 + rank * 0.2,
            "forward_max_drawdown": -0.04 + rank * 0.02, "spread_pnl": pnl - 3,
            "atm_iv": 0.20 + rank * 0.1, "spot": 4000 + index,
            "net_delta": 8.0, "net_gamma": -0.01, "net_theta": 20.0, "net_vega": -50.0,
            "holding_slices": 390, "exit_reason": "profit_target" if pnl > 0 else "stop_loss",
        })
    return pd.DataFrame(rows)


@unittest.skipIf(pd is None, "install notebook optional dependencies")
class SpxwStudyAnalysisTests(unittest.TestCase):
    def test_quality_and_surface_reports_use_internal_valuation(self) -> None:
        dataset = internally_priceable_spxw_dataset()
        summary, detail = data_quality_report(dataset)
        surface = surface_observations(dataset)
        self.assertEqual(len(detail), len(dataset.slices))
        self.assertIn("iv_solver_success_rate", detail)
        self.assertTrue((detail["iv_solver_success_rate"] == 1).all())
        self.assertTrue(len(surface) > 0)
        self.assertIn("total_variance", surface)

    def test_predictability_strategy_and_frozen_validation(self) -> None:
        panel = study_panel()
        correlations, quintiles, regressions = predictability_report(panel)
        self.assertFalse(correlations.empty)
        self.assertEqual(len(quintiles), 5)
        self.assertEqual(len(regressions), 5)
        comparison, trades = strategy_comparison(panel)
        self.assertEqual(set(comparison["strategy"]), {
            "no_trade", "daily_spread", "high_skew", "high_skew_vol_filter", "high_skew_trend_filter",
        })
        self.assertGreater(len(trades["daily_spread"]), len(trades["high_skew"]))
        grid, frozen = frozen_parameter_validation(panel)
        self.assertFalse(grid.empty)
        self.assertFalse(frozen.empty)
        self.assertGreater(frozen.iloc[0]["test_trades"], 0)
        sensitivity = robustness_sensitivity(panel)
        self.assertEqual(set(sensitivity["case"]), {
            "base", "double_commission", "extra_slippage_0.05", "entry_delay_2_slices", "combined_stress",
        })
        self.assertLess(sensitivity.set_index("case").loc["combined_stress", "mean_pnl"], sensitivity.set_index("case").loc["base", "mean_pnl"])

    def test_risk_decomposition_reconciles_to_strategy_pnl(self) -> None:
        panel = study_panel()
        mask = panel["skew_rank"] >= 0.8
        summary, trades, by_year = risk_decomposition(panel, mask)
        self.assertFalse(summary.empty)
        self.assertFalse(by_year.empty)
        explained = trades["delta_pnl"] + trades["gamma_pnl"] + trades["theta_pnl"] + trades["vega_pnl"] + trades["residual_pnl"]
        np.testing.assert_allclose(explained, trades["strategy_pnl"])
        self.assertLessEqual(summary.iloc[0]["expected_shortfall_95"], summary.iloc[0]["mean_pnl"])


if __name__ == "__main__":
    unittest.main()
