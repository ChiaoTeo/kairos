from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from kairospy.data.snapshots.market_snapshot_storage import MarketSnapshotStorageDriver
from kairospy.identity import AssetId, InstrumentId, VenueId
from kairospy.market.types import Quote
from kairospy.reference.contracts import ContractType, FutureSpec, ProductType
from kairospy.reference import ReferenceCatalog
from tests.reference_support import publish_test_instrument
from kairospy.research.capture.normalized_series import NormalizedSeriesCaptureService
from kairospy.research.capture.series import SeriesCaptureSpec


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class Provider:
    def snapshot(self, definitions):
        return tuple(Quote(item.instrument_id, Decimal("10"), Decimal("11"), Decimal("1"), Decimal("1"), NOW) for item in definitions)


class NormalizedSeriesReferenceTests(unittest.TestCase):
    def test_current_future_produces_contract_metadata(self) -> None:
        instrument = InstrumentId("future:BTC:202609")
        catalog = ReferenceCatalog()
        definition = publish_test_instrument(
            catalog, instrument, ProductType.FUTURE, "BTC Future",
            FutureSpec(AssetId("BTC"), AssetId("USDT"), NOW + timedelta(days=30), Decimal("1"), ContractType.LINEAR, "index"),
            AssetId("USDT"), VenueId("test"), "BTC-FUT", NOW,
        )
        with tempfile.TemporaryDirectory() as directory:
            times = iter((NOW, NOW + timedelta(seconds=1)))
            service = NormalizedSeriesCaptureService(MarketSnapshotStorageDriver(Path(directory)), wait=lambda _: None, now=lambda: next(times))
            dataset = service.capture(Provider(), catalog, (definition,), SeriesCaptureSpec("dataset", 2, 1), source="test", market_data_type="quote")
        self.assertEqual(dataset.contracts[0].instrument_id, instrument)


if __name__ == "__main__":
    unittest.main()
