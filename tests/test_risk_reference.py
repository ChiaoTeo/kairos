from __future__ import annotations

from kairospy.trading.identity import InstitutionId

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from kairospy.accounting.portfolio import PortfolioSnapshot, Position
from kairospy.trading.identity import AccountKey, AccountType, AssetId, InstrumentId, VenueId
from kairospy.trading.product import ContractType, PerpetualSpec, ProductType
from kairospy.reference import (
    BrokerId, EconomicProduct, ExecutionRoute, InstrumentDefinition,
    InstrumentLifecycle, InstrumentReference, ListingDefinition, ListingId,
    ProductId, ReferenceCatalog, ReferenceRole, ReferenceTarget, RouteId,
    TradingRules,
)
from kairospy.risk.view import build_risk_view


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class RiskReferenceTests(unittest.TestCase):
    def test_risk_dimensions_come_from_reference_graph_and_route(self) -> None:
        catalog = ReferenceCatalog()
        instrument_id = InstrumentId("crypto:binance:perpetual:BTCUSDT")
        product_id = ProductId("product:perpetual:BTC")
        listing_id = ListingId("listing:binance-usdm:BTCUSDT")
        account = AccountKey(InstitutionId("binance"), "main", AccountType.DERIVATIVES)
        spec = PerpetualSpec(AssetId("BTC"), AssetId("USDT"), "btc-index", Decimal("1"), ContractType.LINEAR, 28800)
        catalog.products.add(EconomicProduct(product_id, ProductType.PERPETUAL, "BTC perpetual", NOW, currency=AssetId("USDT")))
        catalog.instruments.add(InstrumentDefinition(instrument_id, product_id, ProductType.PERPETUAL, spec, InstrumentLifecycle(), NOW))
        catalog.listings.add(ListingDefinition(listing_id, instrument_id, VenueId("binance-usdm"), "BTCUSDT", AssetId("USDT"), TradingRules(Decimal("0.1"), Decimal("0.001"), Decimal("0.001")), NOW))
        catalog.routes.add(ExecutionRoute(RouteId("route:binance:BTCUSDT"), BrokerId("binance"), account, listing_id, NOW))
        catalog.add_reference(InstrumentReference(instrument_id, ReferenceRole.ECONOMIC_UNDERLYING, ReferenceTarget(asset_id=AssetId("BTC")), NOW))
        snapshot = PortfolioSnapshot(NOW, AssetId("USD"), (), (
            Position(account, instrument_id, Decimal("2"), Decimal("50000"), Decimal("51000"), Decimal("102000"), Decimal("2000"), Decimal("0"), AssetId("USDT")),
        ), Decimal("120000"), "complete", (), ())
        view = build_risk_view(snapshot, catalog, unit_greeks={instrument_id: (Decimal("1"), Decimal("0"), Decimal("0"), Decimal("0"))})
        dimensions = {(item.dimension, item.key) for item in view.exposures}
        self.assertIn(("asset", "BTC"), dimensions)
        self.assertIn(("venue", "binance-usdm"), dimensions)
        self.assertIn(("broker", "binance"), dimensions)
        self.assertIn(("product_family", product_id.value), dimensions)


if __name__ == "__main__":
    unittest.main()
