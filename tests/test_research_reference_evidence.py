from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from kairos.domain.identity import AssetId, InstrumentId, VenueId
from kairos.domain.product import CryptoSpotSpec, ProductType
from kairos.reference import (
    EconomicProduct, InstrumentDefinition, InstrumentLifecycle,
    ListingDefinition, ListingId, ProductId, ReferenceCatalog, TradingRules,
    VenueDefinition, VenueType,
)
from kairos.research_platform.snapshot import build_reference_evidence


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class ResearchReferenceEvidenceTests(unittest.TestCase):
    def test_reference_evidence_hash_is_deterministic_and_changes_with_listing_version(self) -> None:
        catalog = ReferenceCatalog(); instrument = InstrumentId("crypto:spot:BTCUSDT"); product = ProductId("product:BTCUSDT")
        catalog.products.add(EconomicProduct(product, ProductType.CRYPTO_SPOT, "BTC/USDT", NOW))
        catalog.instruments.add(InstrumentDefinition(instrument, product, ProductType.CRYPTO_SPOT, CryptoSpotSpec(AssetId("BTC"), AssetId("USDT")), InstrumentLifecycle(), NOW))
        catalog.venues.add(VenueDefinition(VenueId("binance"), VenueType.CRYPTO_EXCHANGE, "Binance", "UTC", NOW))
        catalog.listings.add(ListingDefinition(ListingId("listing:binance:BTCUSDT"), instrument, VenueId("binance"), "BTCUSDT", AssetId("USDT"), TradingRules(Decimal("0.1"), Decimal("0.001"), Decimal("0.001")), NOW))
        first = build_reference_evidence(catalog, (instrument,), NOW)
        second = build_reference_evidence(catalog, (instrument,), NOW)
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.listing_ids, ("listing:binance:BTCUSDT",))


if __name__ == "__main__":
    unittest.main()
