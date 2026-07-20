from __future__ import annotations

from kairos.domain.identity import InstitutionId

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from kairos.domain.identity import AccountKey, AccountType, AssetId, InstrumentId, VenueId
from kairos.domain.product import ContractType, PerpetualSpec, ProductType, SettlementSession
from kairos.reference import (
    AssetDefinition, AssetType, BenchmarkDefinition, BenchmarkId, BenchmarkType, BrokerId,
    ContractSeries, EconomicProduct, ExecutionRoute, InstrumentDefinition,
    InstrumentLifecycle, InstrumentReference, ListingDefinition, ListingId,
    MappingTargetType, ProductId, ProviderId, ProviderSymbolMapping,
    ReferenceCatalog, ReferenceRole, ReferenceTarget, RouteId, SeriesId,
    SettlementMethod, SettlementTerms, TradingRules,
    VenueDefinition, VenueType,
)


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class ReferenceCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = ReferenceCatalog()
        self.catalog.assets.add(AssetDefinition(AssetId("USDT"), AssetType.CRYPTO, "Tether USD", NOW, decimals=6))
        self.product = EconomicProduct(ProductId("product:btc-perp"), ProductType.PERPETUAL, "BTC perpetual", NOW, currency=AssetId("USDT"))
        self.instrument = InstrumentDefinition(
            InstrumentId("crypto:binance:perpetual:BTCUSDT"), self.product.product_id,
            ProductType.PERPETUAL, PerpetualSpec(AssetId("BTC"), AssetId("USDT"), "BTCUSDT", Decimal("1"), ContractType.LINEAR, 28800), InstrumentLifecycle(listed_at=NOW), NOW,
        )
        self.listing = ListingDefinition(
            ListingId("listing:binance-usdm:BTCUSDT"), self.instrument.instrument_id,
            VenueId("binance-usdm"), "BTCUSDT", AssetId("USDT"),
            TradingRules(Decimal("0.1"), Decimal("0.001"), Decimal("0.001")), NOW,
        )
        self.catalog.products.add(self.product)
        self.catalog.instruments.add(self.instrument)
        self.catalog.venues.add(VenueDefinition(VenueId("binance-usdm"), VenueType.CRYPTO_EXCHANGE, "Binance USD-M", "UTC", NOW))
        self.catalog.listings.add(self.listing)

    def test_listing_and_provider_are_independent_and_point_in_time(self) -> None:
        mapping = ProviderSymbolMapping(
            ProviderId("massive"), "crypto", "X:BTCUSDT", MappingTargetType.INSTRUMENT,
            self.instrument.instrument_id.value, NOW,
        )
        self.catalog.add_mapping(mapping)
        self.assertEqual(self.catalog.active_listings(self.instrument.instrument_id, NOW), (self.listing,))
        self.assertEqual(self.catalog.resolve_provider_symbol(ProviderId("massive"), "crypto", "X:BTCUSDT", NOW), mapping)
        with self.assertRaises(ValueError):
            self.catalog.add_mapping(mapping)

    def test_reference_graph_and_integrity(self) -> None:
        benchmark = BenchmarkDefinition(BenchmarkId("benchmark:binance:btcusdt-index"), BenchmarkType.INDEX, "BTCUSDT index", AssetId("USDT"), NOW)
        self.catalog.benchmarks.add(benchmark)
        reference = InstrumentReference(
            self.instrument.instrument_id, ReferenceRole.SETTLEMENT_BENCHMARK,
            ReferenceTarget(benchmark_id=benchmark.benchmark_id), NOW,
        )
        self.catalog.add_reference(reference)
        self.assertEqual(self.catalog.references(self.instrument.instrument_id, ReferenceRole.SETTLEMENT_BENCHMARK, NOW), (reference,))
        self.assertEqual(self.catalog.validate_integrity(NOW), ())

    def test_execution_route_resolves_through_listing(self) -> None:
        account = AccountKey(InstitutionId("binance"), "main", AccountType.DERIVATIVES)
        route = ExecutionRoute(RouteId("route:binance:main:btcusdt"), BrokerId("binance"), account, self.listing.listing_id, NOW)
        self.catalog.routes.add(route)
        self.assertEqual(self.catalog.resolve_execution_route(account, self.instrument.instrument_id, NOW), route)

    def test_settlement_terms_reject_ambiguous_contracts(self) -> None:
        benchmark = BenchmarkId("benchmark:spx:settlement")
        cash = SettlementTerms(SettlementMethod.CASH, SettlementSession.AM, AssetId("USD"), benchmark)
        self.assertEqual(cash.benchmark_id, benchmark)
        with self.assertRaises(ValueError):
            SettlementTerms(SettlementMethod.CASH, SettlementSession.AM, AssetId("USD"))
        with self.assertRaises(ValueError):
            ReferenceTarget(asset_id=AssetId("BTC"), instrument_id=self.instrument.instrument_id)

    def test_instrument_type_and_contract_spec_are_one_tagged_contract(self) -> None:
        with self.assertRaises(TypeError):
            InstrumentDefinition(
                InstrumentId("invalid"), self.product.product_id, ProductType.EQUITY,
                self.instrument.contract_spec, InstrumentLifecycle(), NOW,
            )

    def test_integrity_reports_series_mapping_currency_and_route_mismatches(self) -> None:
        missing_product = ProductId("product:missing")
        self.catalog.series.add(ContractSeries(SeriesId("series:orphan"), missing_product, NOW))
        self.catalog.add_mapping(ProviderSymbolMapping(
            ProviderId("vendor"), "symbols", "UNKNOWN", MappingTargetType.INSTRUMENT,
            "instrument:missing", NOW,
        ))
        account = AccountKey(InstitutionId("ibkr"), "paper", AccountType.DERIVATIVES)
        self.catalog.routes.add(ExecutionRoute(
            RouteId("route:mismatch"), BrokerId("binance"), account, self.listing.listing_id, NOW,
        ))
        issues = self.catalog.validate_integrity(NOW)
        self.assertTrue(any(item.startswith("series_missing_product:") for item in issues))
        self.assertTrue(any(item.startswith("mapping_missing_target:") for item in issues))
        self.assertTrue(any(item.startswith("route_broker_account_mismatch:") for item in issues))


if __name__ == "__main__":
    unittest.main()
