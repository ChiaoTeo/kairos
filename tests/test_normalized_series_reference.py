from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from trading.data.market_slice_storage import MarketSliceStorageDriver
from trading.domain.identity import AssetId, InstrumentId, VenueId
from trading.domain.market_data import Quote
from trading.domain.product import ContractType, FutureSpec, ProductType
from trading.reference import ReferenceCatalog
from tests.reference_support import publish_test_instrument
from trading.research.normalized_series import NormalizedSeriesCaptureService
from trading.research.series import SeriesCaptureSpec


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
            service = NormalizedSeriesCaptureService(MarketSliceStorageDriver(Path(directory)), wait=lambda _: None, now=lambda: next(times))
            dataset = service.capture(Provider(), catalog, (definition,), SeriesCaptureSpec("dataset", 2, 1), source="test", market_data_type="quote")
        self.assertEqual(dataset.contracts[0].instrument_id, instrument)


if __name__ == "__main__":
    unittest.main()
