from __future__ import annotations

import unittest
from decimal import Decimal

from kairospy.identity import InstrumentId
from kairospy.reference.contracts import OptionRight
from kairospy.analytics.pricing import PricingInput, PricingModel
from kairospy.risk import RevaluationPosition, Scenario, ScenarioEngine, explain_scenario, historical_var_es, standard_scenario_grid


class RiskAnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.position = RevaluationPosition(
            InstrumentId("option:spx:put"), Decimal("-2"), Decimal("100"),
            PricingInput(Decimal("6000"), Decimal("5700"), Decimal("0.1"), Decimal("0.04"), Decimal("0.25"), OptionRight.PUT),
            PricingModel.BLACK_76, "put-spread", "account-1",
        )

    def test_full_revaluation_supports_spot_vol_skew_term_rate_and_time(self) -> None:
        scenario = Scenario(
            "combined", spot_shock=Decimal("-0.10"), volatility_shock=Decimal("0.05"),
            skew_twist=Decimal("-0.10"), term_twist=Decimal("0.02"),
            rate_shock=Decimal("0.01"), time_advance_days=Decimal("1"),
        )
        result = ScenarioEngine().evaluate((self.position,), scenario)
        self.assertLess(result.pnl, 0)  # short put loses under a down/vol-up shock
        self.assertEqual(result.pnl_by_structure[0][0], "put-spread")
        self.assertEqual(result.pnl_by_account[0][0], "account-1")
        explain = explain_scenario(self.position, scenario, result)
        self.assertEqual(explain.total_pnl, result.pnl)
        self.assertEqual(explain.total_pnl, explain.delta + explain.gamma + explain.theta + explain.vega + explain.rho + explain.residual)

    def test_scenario_grid_and_historical_tail_risk(self) -> None:
        results = tuple(ScenarioEngine().evaluate((self.position,), item) for item in standard_scenario_grid())
        self.assertEqual(len(results), 20)
        risk = historical_var_es(tuple(item.pnl for item in results), Decimal("0.95"))
        self.assertEqual(risk.observation_count, 20)
        self.assertGreaterEqual(risk.expected_shortfall, risk.value_at_risk)
        self.assertEqual(risk.worst_pnl, min(item.pnl for item in results))

    def test_invalid_scenario_and_var_inputs_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ScenarioEngine().evaluate((self.position,), Scenario("invalid", spot_shock=Decimal("-1")))
        with self.assertRaises(ValueError):
            historical_var_es(())


if __name__ == "__main__":
    unittest.main()
