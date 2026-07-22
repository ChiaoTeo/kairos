from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from kairospy.identity import InstrumentId
from kairospy.market import OptionMarketObservation, blocking_issues, validate_option_observation


NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


class MarketDataQualityTests(unittest.TestCase):
    def test_valid_quote_and_wide_spread_warning(self) -> None:
        valid = OptionMarketObservation(InstrumentId("option:test"), NOW, Decimal("9"), Decimal("11"), Decimal("10"), Decimal("10"), "fixture")
        self.assertEqual(validate_option_observation(valid, NOW), ())
        wide = OptionMarketObservation(InstrumentId("option:test"), NOW, Decimal("0"), Decimal("2"), None, None, "fixture")
        issues = validate_option_observation(wide, NOW, max_relative_spread=Decimal("0.5"))
        self.assertEqual(issues[0].code, "wide_spread")
        self.assertEqual(blocking_issues(issues), ())

    def test_crossed_stale_and_future_quotes_are_blocking(self) -> None:
        crossed = OptionMarketObservation(InstrumentId("option:test"), NOW - timedelta(seconds=10), Decimal("11"), Decimal("9"), None, None, "fixture")
        issues = validate_option_observation(crossed, NOW)
        self.assertEqual({item.code for item in blocking_issues(issues)}, {"crossed_quote", "stale_quote"})
        future = OptionMarketObservation(InstrumentId("option:test"), NOW + timedelta(seconds=1), Decimal("9"), Decimal("11"), None, None, "fixture")
        self.assertEqual(blocking_issues(validate_option_observation(future, NOW))[0].code, "future_event")


if __name__ == "__main__":
    unittest.main()
