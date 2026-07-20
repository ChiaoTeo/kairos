from datetime import datetime, timedelta, timezone
import unittest

from kairos.connectors.deribit.trade_history import normalize_deribit_trades
from kairos.features.volatility import build_term_skew_panel


class CryptoOptionStudyTest(unittest.TestCase):
    def test_deribit_trade_normalization_converts_iv_percent_to_absolute(self):
        rows = normalize_deribit_trades([{"timestamp": 1705276800075, "iv": 53.3, "price": 0.036, "amount": 0.1,
            "direction": "sell", "instrument_name": "BTC-23FEB24-46000-C", "index_price": 41701.94,
            "trade_id": "x", "mark_price": 0.0366, "tick_direction": 2}])
        self.assertEqual(rows[0]["option_right"], "call")
        self.assertAlmostEqual(rows[0]["trade_iv"], 0.533)
        self.assertEqual(rows[0]["expiry"], "2024-02-23T08:00:00Z")

    def test_fixed_maturity_skew_uses_total_variance_interpolation(self):
        as_of = datetime(2024, 1, 1, tzinfo=timezone.utc)
        quotes = []
        for dte, base in ((20, 0.50), (40, 0.60)):
            expiry = (as_of + timedelta(days=dte)).isoformat().replace("+00:00", "Z")
            for right, delta, spread in (("call", .50, 0), ("put", -.50, 0), ("call", .25, -.01),
                                          ("put", -.25, .03), ("call", .10, .01), ("put", -.10, .08)):
                quotes.append({"period_start": as_of.isoformat().replace("+00:00", "Z"), "expiry": expiry,
                               "option_right": right, "mark_iv": str(base+spread), "vendor_delta": str(delta)})
        row = build_term_skew_panel(quotes, (30,))[0]
        self.assertGreater(float(row["put_skew25_30d"]), 0)
        self.assertLess(float(row["rr25_30d"]), 0)
        expected_atm = ((0.50**2*(20/365)*.5 + 0.60**2*(40/365)*.5)/(30/365))**.5
        self.assertAlmostEqual(float(row["atm_iv_30d"]), expected_atm)
