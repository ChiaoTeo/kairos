from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from kairospy.identity import AssetId
from kairospy.reference.contracts import OptionRight
from kairospy.market import DayCount, RateCurve, RateNode, cost_of_carry_forward, parity_forward, zero_rate
from kairospy.analytics.pricing import PricingInput, PricingModel, SolverStatus, black_scholes, black76, implied_volatility


class PricingTests(unittest.TestCase):
    def test_black_scholes_matches_published_reference_values(self) -> None:
        # Hull-style benchmark: S=42, K=40, r=10%, sigma=20%, T=0.5.
        inputs = PricingInput(Decimal("42"), Decimal("40"), Decimal("0.5"), Decimal("0.10"), Decimal("0.20"), OptionRight.CALL)
        result = black_scholes(inputs)
        self.assertAlmostEqual(float(result.price), 4.7594, places=4)
        self.assertAlmostEqual(float(result.delta), 0.7791, places=4)
        self.assertGreater(result.gamma, 0)
        self.assertGreater(result.vega, 0)

    def test_put_call_parity_and_black76(self) -> None:
        call_input = PricingInput(Decimal("100"), Decimal("100"), Decimal("1"), Decimal("0.05"), Decimal("0.20"), OptionRight.CALL)
        put_input = PricingInput(Decimal("100"), Decimal("100"), Decimal("1"), Decimal("0.05"), Decimal("0.20"), OptionRight.PUT)
        call, put = black_scholes(call_input), black_scholes(put_input)
        parity = call.price - put.price
        expected = Decimal("100") - Decimal("100") * Decimal(str(__import__("math").exp(-0.05)))
        self.assertAlmostEqual(float(parity), float(expected), places=10)
        forward_call = black76(call_input)
        self.assertGreater(forward_call.price, 0)
        self.assertGreater(forward_call.vega, 0)

    def test_implied_vol_round_trip_and_diagnostics(self) -> None:
        inputs = PricingInput(Decimal("100"), Decimal("105"), Decimal("0.75"), Decimal("0.03"), Decimal("0.32"), OptionRight.PUT, Decimal("0.01"))
        market_price = black_scholes(inputs).price
        solved = implied_volatility(market_price, inputs, PricingModel.BLACK_SCHOLES)
        self.assertEqual(solved.status, SolverStatus.CONVERGED)
        self.assertAlmostEqual(float(solved.volatility), 0.32, places=7)
        invalid = implied_volatility(Decimal("200"), inputs, PricingModel.BLACK_SCHOLES)
        self.assertEqual(invalid.status, SolverStatus.PRICE_OUT_OF_BOUNDS)
        self.assertIsNone(invalid.volatility)

    def test_rate_curve_and_forward_estimators(self) -> None:
        curve = RateCurve(
            datetime(2026, 7, 14, tzinfo=timezone.utc), AssetId("USD"),
            (RateNode(Decimal("0.25"), Decimal("0.04")), RateNode(Decimal("1"), Decimal("0.05"))),
            DayCount.ACT_365, "fixture",
        )
        self.assertEqual(zero_rate(curve, Decimal("0.625")), Decimal("0.045"))
        forward = cost_of_carry_forward(Decimal("100"), Decimal("1"), Decimal("0.05"), Decimal("0.02"))
        self.assertAlmostEqual(float(forward), 103.04545, places=5)
        recovered = parity_forward(Decimal("10"), Decimal("7"), Decimal("100"), Decimal("1"), Decimal("0.05"))
        self.assertAlmostEqual(float(recovered), 103.15381, places=5)


if __name__ == "__main__":
    unittest.main()
