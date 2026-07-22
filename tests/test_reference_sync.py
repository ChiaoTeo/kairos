from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from kairospy.integrations.ports import ReferenceDataRequest
from kairospy.identity import AssetId, InstrumentId, VenueId
from kairospy.reference.contracts import CryptoSpotSpec, EquitySpec, ProductType
from kairospy.reference import (
    AssetDefinition, AssetType, ListingDefinition, ListingId, MappingTargetType,
    ProviderId, ProviderSymbolMapping, ReferenceCatalog, TradingRules,
    VenueDefinition, VenueType,
)
from kairospy.reference.factory import publish_instrument
from kairospy.reference.sync import ReferenceSyncService


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class ReferenceDataClient:
    def __init__(self, catalog):
        self.catalog = catalog

    def sync(self, request):
        return self.catalog


class ReferenceSyncTests(unittest.TestCase):
    def test_binance_sync_publishes_real_listing_idempotently(self) -> None:
        published = ReferenceCatalog(); instrument_id = InstrumentId("crypto:binance:spot:BTCUSDT")
        publish_instrument(
            published, instrument_id=instrument_id, instrument_type=ProductType.CRYPTO_SPOT, display_name="BTCUSDT",
            contract_spec=CryptoSpotSpec(AssetId("BTC"), AssetId("USDT")), trading_currency=AssetId("USDT"),
            listings=(ListingDefinition(
                ListingId("listing:binance:BTCUSDT"), instrument_id, VenueId("binance"), "BTCUSDT", AssetId("USDT"),
                TradingRules(Decimal("0.1"), Decimal("0.001"), Decimal("0.001")), NOW,
            ),), effective_from=NOW,
            asset_definitions=(
                AssetDefinition(AssetId("BTC"), AssetType.CRYPTO, "Bitcoin", NOW, decimals=8),
                AssetDefinition(AssetId("USDT"), AssetType.CRYPTO, "Tether USD", NOW, decimals=6),
            ),
            venue_definitions=(VenueDefinition(VenueId("binance"), VenueType.CRYPTO_EXCHANGE, "Binance", "UTC", NOW),),
        )
        catalog = ReferenceCatalog(); service = ReferenceSyncService(catalog)
        request = ReferenceDataRequest(ProductType.CRYPTO_SPOT, ("BTCUSDT",))
        first = service.sync(ReferenceDataClient(published), request)
        second = service.sync(ReferenceDataClient(published), request)
        self.assertEqual((first.instruments_added, first.listings_added), (1, 1))
        self.assertEqual((second.instruments_added, second.listings_added), (0, 0))
        self.assertEqual(catalog.active_listings(instrument_id, NOW)[0].venue_id, VenueId("binance"))

    def test_ibkr_identifier_is_provider_mapping_but_explicit_primary_exchange_is_listing(self) -> None:
        published = ReferenceCatalog(); instrument_id = InstrumentId("equity:us:AAPL")
        publish_instrument(
            published, instrument_id=instrument_id, instrument_type=ProductType.EQUITY, display_name="AAPL",
            contract_spec=EquitySpec("XNAS", "US", AssetId("USD")), trading_currency=AssetId("USD"),
            listings=(ListingDefinition(
                ListingId("listing:xnas:AAPL"), instrument_id, VenueId("xnas"), "AAPL", AssetId("USD"),
                TradingRules(Decimal("0.01"), Decimal("1"), Decimal("1")), NOW,
            ),), effective_from=NOW,
            asset_definitions=(AssetDefinition(AssetId("USD"), AssetType.FIAT, "US Dollar", NOW, decimals=2),),
            venue_definitions=(VenueDefinition(VenueId("xnas"), VenueType.EXCHANGE, "Nasdaq", "America/New_York", NOW, mic="XNAS"),),
        )
        published.add_mapping(ProviderSymbolMapping(
            ProviderId("ibkr"), "conid", "265598", MappingTargetType.INSTRUMENT, instrument_id.value, NOW,
        ))
        catalog = ReferenceCatalog(); ReferenceSyncService(catalog).sync(ReferenceDataClient(published), ReferenceDataRequest(ProductType.EQUITY, ("AAPL",)))
        self.assertEqual(catalog.active_listings(instrument_id, NOW)[0].venue_id, VenueId("xnas"))
        self.assertEqual(catalog.mappings()[0].external_id, "265598")


if __name__ == "__main__":
    unittest.main()
