from __future__ import annotations

from datetime import datetime, timezone
import unittest

from trading.domain.capability import MarketDataCapabilities, MarketDataKind
from trading.domain.identity import AssetId, InstrumentId
from trading.domain.product import CryptoSpotSpec, ProductType
from trading.market_data.subscriptions import MarketDataRequirement, SubscriptionPlanner
from trading.reference import (
    EconomicProduct, InstrumentDefinition, InstrumentLifecycle,
    MappingTargetType, ProductId, ProviderId, ProviderSymbolMapping,
    ReferenceCatalog,
)


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class MarketDataReferenceTests(unittest.TestCase):
    def test_provider_mapping_is_used_instead_of_trading_listing_symbol(self) -> None:
        catalog = ReferenceCatalog(); instrument = InstrumentId("crypto:spot:BTCUSDT"); product = ProductId("product:BTCUSDT")
        catalog.products.add(EconomicProduct(product, ProductType.CRYPTO_SPOT, "BTC/USDT", NOW))
        catalog.instruments.add(InstrumentDefinition(instrument, product, ProductType.CRYPTO_SPOT, CryptoSpotSpec(AssetId("BTC"), AssetId("USDT")), InstrumentLifecycle(), NOW))
        catalog.add_mapping(ProviderSymbolMapping(ProviderId("vendor"), "realtime", "X.BTC-USDT", MappingTargetType.INSTRUMENT, instrument.value, NOW))
        capabilities = MarketDataCapabilities(frozenset({MarketDataKind.QUOTE}), frozenset({ProductType.CRYPTO_SPOT}))
        requirement = MarketDataRequirement("strategy", ProviderId("vendor"), (instrument,), (MarketDataKind.QUOTE,), source_namespace="realtime")
        plan = SubscriptionPlanner(catalog, {ProviderId("vendor"): capabilities}).build((requirement,), NOW)
        self.assertEqual(plan.subscriptions[0].key.symbol, "X.BTC-USDT")
        self.assertEqual(plan.subscriptions[0].key.provider_id, ProviderId("vendor"))


if __name__ == "__main__":
    unittest.main()
