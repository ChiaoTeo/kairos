from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from tempfile import TemporaryDirectory
import unittest

from kairospy.trading.identity import InstrumentId
from kairospy.market_data import MarketEventEnvelope, MarketEventType, ParquetMarketEventRepository, validate_events


NOW = datetime(2026, 7, 15, 14, 30, tzinfo=timezone.utc)


def quote(at=NOW, order=0, bid="1", ask="2"):
    return MarketEventEnvelope(InstrumentId("option:us:SPXW"), at, at, NOW + timedelta(hours=1), "massive", "options.quotes", "O:SPXW",
        MarketEventType.QUOTE, order, {"bid": Decimal(bid), "ask": Decimal(ask)})


class MarketEventRepositoryTests(unittest.TestCase):
    def test_quality_gate_rejects_crossed_quote(self):
        report = validate_events((quote(bid="3", ask="2"),))
        self.assertFalse(report.publishable)
        self.assertEqual(report.issues[0].code, "crossed_quote")

    def test_parquet_round_trip_and_right_open_boundary(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow optional dependency is not installed")
        with TemporaryDirectory() as temporary:
            repository = ParquetMarketEventRepository(temporary)
            repository.write_batch("test.v1", (quote(), quote(NOW + timedelta(seconds=1), 1)), lineage={"source": {"provider": "massive"}})
            values = list(repository.scan("test.v1", NOW, NOW + timedelta(seconds=1)))
            self.assertEqual(len(values), 1)
            self.assertEqual(values[0].payload["bid"], "1")

    def test_raw_and_corrected_views_are_explicit(self):
        trade_time = NOW
        original = MarketEventEnvelope(InstrumentId("option:us:SPXW"), trade_time, trade_time, NOW + timedelta(hours=1), "massive", "options.trades", "O:SPXW", MarketEventType.TRADE, 0, {"trade_id": "t1", "price": Decimal("1"), "size": Decimal("1")})
        corrected = MarketEventEnvelope(InstrumentId("option:us:SPXW"), trade_time, trade_time + timedelta(microseconds=1), NOW + timedelta(hours=1), "massive", "options.trades", "O:SPXW", MarketEventType.TRADE, 1, {"trade_id": "t1", "price": Decimal("2"), "size": Decimal("1")}, flags=("correction",))
        with TemporaryDirectory() as temporary:
            repository = ParquetMarketEventRepository(temporary)
            repository.write_batch("trades.v1", (original, corrected), lineage={"source": {"provider": "massive"}})
            raw = list(repository.scan("trades.v1", NOW, NOW + timedelta(seconds=1), view="raw-as-received"))
            final = list(repository.scan("trades.v1", NOW, NOW + timedelta(seconds=1), view="corrected-final"))
            self.assertEqual(len(raw), 2)
            self.assertEqual(len(final), 1)
            self.assertEqual(final[0].payload["price"], "2")

    def test_same_batch_is_content_addressed_and_idempotent(self):
        with TemporaryDirectory() as temporary:
            repository = ParquetMarketEventRepository(temporary)
            first = repository.write_batch("idempotent.v1", (quote(),), lineage={"source": {"provider": "massive"}})
            second = repository.write_batch("idempotent.v1", (quote(),), lineage={"source": {"provider": "massive"}})
            self.assertEqual(first["dataset_sha256"], second["dataset_sha256"])
            self.assertEqual(first["files"], second["files"])
            self.assertEqual(len(list((repository.root / "dataset=idempotent.v1").rglob("*.parquet"))), 1)
            with self.assertRaises(ValueError):
                repository.write_batch("idempotent.v1", (quote(NOW + timedelta(seconds=1), 1),), lineage={"source": {"provider": "massive"}})


if __name__ == "__main__":
    unittest.main()
