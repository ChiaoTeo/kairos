from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from trading.data.market_slice_storage import MarketSliceStorageDriver
from trading.domain.identity import AssetId, InstrumentId, VenueId
from trading.domain.market_data import Quote
from trading.domain.product import CryptoSpotSpec, EquitySpec, ProductType
from trading.research.normalized_series import NormalizedSeriesCaptureService
from trading.research.series import SeriesCaptureSpec
from trading.reference import ReferenceCatalog
from tests.reference_support import publish_test_instrument


NOW = datetime(2026, 7, 14, 8, tzinfo=timezone.utc)


class FakeNormalizedProvider:
    def snapshot(self, definitions):
        return tuple(Quote(item.instrument_id, Decimal("99"), Decimal("101"), Decimal("10"), Decimal("10"), NOW) for item in definitions)


class NormalizedSeriesTests(unittest.TestCase):
    def test_stock_and_crypto_series_do_not_require_option_underlying_fields(self) -> None:
        venue = VenueId("fixture")
        catalog = ReferenceCatalog()
        stock = publish_test_instrument(
            catalog, InstrumentId("equity:aapl"), ProductType.EQUITY, "AAPL",
            EquitySpec("NASDAQ", "US", AssetId("USD")), AssetId("USD"), venue, "AAPL",
            datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        spot = publish_test_instrument(
            catalog, InstrumentId("crypto:spot:btcusdt"), ProductType.CRYPTO_SPOT, "BTCUSDT",
            CryptoSpotSpec(AssetId("BTC"), AssetId("USDT"), Decimal("10")), AssetId("USDT"), venue, "BTCUSDT",
            datetime(2020, 1, 1, tzinfo=timezone.utc), price_increment=Decimal("0.1"),
            quantity_increment=Decimal("0.001"), minimum_quantity=Decimal("0.001"),
        )
        times = iter((NOW, NOW.replace(minute=1)))
        with tempfile.TemporaryDirectory() as directory:
            repository = MarketSliceStorageDriver(directory)
            dataset = NormalizedSeriesCaptureService(repository, wait=lambda _: None, now=lambda: next(times)).capture(
                FakeNormalizedProvider(), catalog, (stock, spot), SeriesCaptureSpec("mixed-series", 2, 60),
                source="fixture", market_data_type="normalized",
            )
            self.assertEqual(dataset.contracts, ())
            self.assertTrue(all(item.reference_prices == () for item in dataset.slices))
            self.assertEqual(dataset.manifest.contract_coverage, Decimal("1"))
            self.assertEqual(repository.load("mixed-series"), dataset)


if __name__ == "__main__":
    unittest.main()
