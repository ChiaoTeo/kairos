from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from kairos.domain.identity import InstrumentId
from kairos.domain.market_data import Bar
from kairos.strategies.sma_cross_research_backtest import BarSeries, SmaCrossConfig, backtest_sma_cross


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
INSTRUMENT = InstrumentId("fixture:asset")


def dataset(closes: tuple[str, ...], opens: tuple[str, ...] | None = None) -> BarSeries:
    opens = opens or closes
    bars = tuple(Bar(
        INSTRUMENT, NOW + timedelta(hours=index), NOW + timedelta(hours=index + 1),
        Decimal(opens[index]), max(Decimal(opens[index]), Decimal(close)),
        min(Decimal(opens[index]), Decimal(close)), Decimal(close), Decimal("1"),
    ) for index, close in enumerate(closes))
    return BarSeries("fixture", bars)


class SmaCrossTests(unittest.TestCase):
    def test_signal_at_close_fills_only_at_next_open(self) -> None:
        result = backtest_sma_cross(
            dataset(("1", "2", "3", "4"), ("1", "2", "10", "4")),
            SmaCrossConfig(1, 2, Decimal("100"), Decimal("0")),
        )

        self.assertEqual(result.trades[0].side, "buy")
        self.assertEqual(result.trades[0].timestamp, NOW + timedelta(hours=2))
        self.assertEqual(result.trades[0].price, Decimal("10"))
        self.assertEqual(result.trades[-1].reason, "end_of_data")
        self.assertEqual(result.metrics["final_equity"], Decimal("40"))

    def test_fees_are_charged_on_both_sides(self) -> None:
        result = backtest_sma_cross(
            dataset(("1", "2", "2", "2")),
            SmaCrossConfig(1, 2, Decimal("100"), Decimal("100")),
        )

        self.assertEqual(len(result.trades), 2)
        self.assertGreater(result.metrics["commissions"], Decimal("1.9"))
        self.assertLess(result.metrics["final_equity"], Decimal("100"))

    def test_configuration_and_history_length_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            SmaCrossConfig(20, 10)
        with self.assertRaisesRegex(ValueError, "more bars"):
            backtest_sma_cross(dataset(("1", "2")), SmaCrossConfig(1, 2))


if __name__ == "__main__":
    unittest.main()
