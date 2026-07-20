from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from kairospy.connectors.binance.funding_ingestion import BinanceDurableFundingBackfill
from kairospy.application.clock import FixedClock
from tests.test_runtime_store import request


NOW = datetime(2026, 7, 17, 16, 0, tzinfo=timezone.utc)


class FundingSettlementClient:
    def __init__(self): self.windows = []
    def funding_history(self, account, start, end):
        self.windows.append((start, end)); return ()


class Ingestion:
    def __init__(self): self.calls = 0
    def ingest_funding_history(self, payments, *, source):
        self.calls += 1; return len(payments)


class BinanceFundingBackfillTests(unittest.TestCase):
    def test_periodic_backfill_uses_initial_window_then_overlap_and_is_supervisor_compatible(self) -> None:
        clock = FixedClock(NOW); funding_client = FundingSettlementClient(); ingestion = Ingestion()
        service = BinanceDurableFundingBackfill(
            request().account, funding_client, ingestion, clock=clock,
            initial_lookback=timedelta(days=2), overlap=timedelta(hours=1),
        )  # type: ignore[arg-type]
        first = service.start()
        self.assertEqual((first.start, first.end), (NOW - timedelta(days=2), NOW))
        clock.set(NOW + timedelta(hours=2))
        second = service.backfill()
        self.assertEqual(second.start, NOW - timedelta(hours=1))
        self.assertEqual((first.complete, second.complete, ingestion.calls), (True, True, 2))
        service.stop()


if __name__ == "__main__":
    unittest.main()
