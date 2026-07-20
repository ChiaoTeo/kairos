from datetime import date, timedelta
import math
import unittest

from studies.btc_options_vrp.study import analyze, prepare_study_panel
from kairospy.features.volatility import build_iv_rv_panel


class BtcOptionsVrpTest(unittest.TestCase):
    def test_panel_is_time_split_and_threshold_is_frozen(self):
        start = date(2020, 1, 1)
        days = [start + timedelta(days=i) for i in range(500)]
        spot = {day: 10_000 * math.exp(i * 0.001 + math.sin(i / 7) * 0.02) for i, day in enumerate(days)}
        dvol = {day: 70 + math.sin(i / 20) * 10 for i, day in enumerate(days)}
        spot_rows = [{"period_start": day.isoformat() + "T00:00:00Z", "close": spot[day]} for day in days]
        dvol_rows = [{"period_start": day.isoformat() + "T00:00:00Z", "close": dvol[day]} for day in days]
        rows = build_iv_rv_panel(spot_rows, dvol_rows)
        rows, threshold, development, high_test = prepare_study_panel(rows)
        result = analyze(rows, threshold, development, high_test, seed=1)

        self.assertEqual(len(rows), 500)
        self.assertLess(development[-1]["period_start"], next(row["period_start"] for row in rows if row["sample"] == "test"))
        self.assertTrue(rows[0]["period_start"].endswith("Z"))
        self.assertLess(rows[0]["period_start"], rows[0]["period_end"])
        self.assertGreater(result["data"]["test_observations"], 100)
        self.assertEqual(result["pre_registered_rule"]["frozen_threshold_vol_points"], threshold)
