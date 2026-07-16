from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from trading.backtest.feed import DatasetRepository
from trading.domain.identity import AssetId, InstrumentId, VenueId
from trading.domain.instrument import InstrumentDefinition, VenueListing
from trading.domain.market_data import Quote
from trading.domain.product import CryptoSpotSpec, EquitySpec, ProductType
from trading.research.normalized_series import NormalizedSeriesCaptureService
from trading.research.series import SeriesCaptureSpec


NOW = datetime(2026, 7, 14, 8, tzinfo=timezone.utc)


class FakeNormalizedProvider:
    def snapshot(self, definitions):
        return tuple(Quote(item.instrument_id, Decimal("99"), Decimal("101"), Decimal("10"), Decimal("10"), NOW) for item in definitions)


class NormalizedSeriesTests(unittest.TestCase):
    def test_stock_and_crypto_series_do_not_require_option_underlying_fields(self) -> None:
        venue = VenueId("fixture")
        stock = InstrumentDefinition(
            InstrumentId("equity:aapl"), ProductType.EQUITY, "AAPL", AssetId("AAPL"), AssetId("USD"),
            EquitySpec("NASDAQ", "US", AssetId("USD")),
            (VenueListing(venue, "AAPL", "AAPL", Decimal("0.01"), Decimal("1"), Decimal("1")),),
            datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        spot = InstrumentDefinition(
            InstrumentId("crypto:spot:btcusdt"), ProductType.CRYPTO_SPOT, "BTCUSDT", AssetId("BTC"), AssetId("USDT"),
            CryptoSpotSpec(AssetId("BTC"), AssetId("USDT"), Decimal("10")),
            (VenueListing(venue, "BTCUSDT", "BTCUSDT", Decimal("0.1"), Decimal("0.001"), Decimal("0.001")),),
            datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        times = iter((NOW, NOW.replace(minute=1)))
        with tempfile.TemporaryDirectory() as directory:
            repository = DatasetRepository(directory)
            dataset = NormalizedSeriesCaptureService(repository, wait=lambda _: None, now=lambda: next(times)).capture(
                FakeNormalizedProvider(), (stock, spot), SeriesCaptureSpec("mixed-series", 2, 60),
                source="fixture", market_data_type="normalized",
            )
            self.assertEqual(dataset.contracts, ())
            self.assertTrue(all(item.reference_prices == () for item in dataset.slices))
            self.assertEqual(dataset.manifest.contract_coverage, Decimal("1"))
            self.assertEqual(repository.load("mixed-series"), dataset)


if __name__ == "__main__":
    unittest.main()
