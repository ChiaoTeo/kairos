from datetime import date
import unittest

from studies.btc_deribit_iron_condor.study import _build_trade, _signals, _target_expiry


class BtcIronCondorTest(unittest.TestCase):
    def test_target_expiry_breaks_equal_distance_tie_deterministically(self):
        book = {
            "later": {"expiry": date(2026, 1, 9)},
            "earlier": {"expiry": date(2026, 1, 7)},
        }
        self.assertEqual(_target_expiry(book, date(2026, 1, 1), 7), date(2026, 1, 7))

    def test_iv_fear_threshold_adapts_using_only_trailing_history(self):
        def row(day, iv, skew=.10):
            value = {"period_start": f"2026-01-{day:02d}T00:00:00Z"}
            for maturity in (7, 14, 30):
                value[f"atm_iv_{maturity}d"] = iv; value[f"put_skew25_{maturity}d"] = skew
            return value
        thresholds = {maturity: {"skew": .05, "atm_iv": .80} for maturity in (7, 14, 30)}
        signals = _signals([row(3, .55)], thresholds, [row(1, .50), row(2, .51)])
        self.assertEqual(len(signals[(7, "high_skew_high_iv")]), 1)

    def test_delta_neutral_skewed_condor_sizes_call_side(self):
        expiry = date(2026, 8, 1)
        def leg(name, right, strike, delta, buy, sell):
            return {"instrument": name, "expiry": expiry, "right": right, "strike": strike,
                    "delta": delta, "buy": buy, "sell": sell}
        entry = {
            "lp": leg("lp", "put", 80000, -.10, 500, 490), "sp": leg("sp", "put", 90000, -.25, 1100, 1090),
            "sc": leg("sc", "call", 110000, .15, 900, 890), "lc": leg("lc", "call", 120000, .05, 400, 390),
        }
        exit_ = {key: {**value, "buy": value["buy"] / 2, "sell": value["sell"] / 2} for key, value in entry.items()}
        trade = _build_trade(date(2026, 7, 1), date(2026, 7, 8), 30, "fear_cooling",
                             "delta_neutral_skewed", expiry, entry, exit_, 5)
        self.assertIsNotNone(trade)
        self.assertAlmostEqual(trade["call_quantity"], 1.5)
        self.assertAlmostEqual(trade["initial_delta_btc"], 0.0, places=7)
        self.assertGreater(trade["pnl_usd"], 0)


if __name__ == "__main__":
    unittest.main()
