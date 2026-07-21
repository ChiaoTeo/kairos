from __future__ import annotations

from kairospy.trading.identity import InstitutionId

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from kairospy.ports import Environment, OrderAck, OrderRequest
from kairospy.trading.capability import ExecutionCapabilities, OrderType
from kairospy.trading.execution import TradeSide
from kairospy.trading.identity import AccountKey, AccountType, AssetId, InstrumentId, VenueId
from kairospy.trading.order import ExecutionInstructions, TimeInForce
from kairospy.trading.product import EquitySpec, ProductType
from kairospy.execution.router import ExecutionRouter
from kairospy.reference import (
    BrokerId, EconomicProduct, ExecutionRoute, InstrumentDefinition,
    InstrumentLifecycle, ListingDefinition, ListingId, ProductId,
    ReferenceCatalog, RouteId, TradingRules,
)


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class ExecutionGateway:
    institution_id = InstitutionId("ibkr")
    venue_id = VenueId("xnas")
    environment = Environment.PAPER
    capabilities = ExecutionCapabilities(frozenset({OrderType.LIMIT}), frozenset({ProductType.EQUITY}))

    def __init__(self):
        self.requests = []

    def place_order(self, request):
        self.requests.append(request)
        return OrderAck(
            request.internal_order_id, request.client_order_id, request.strategy_id,
            request.intent_id, request.correlation_id, "venue-order-1", NOW,
        )

    def cancel_order(self, account, venue_order_id):
        pass


class ExecutionReferenceTests(unittest.TestCase):
    def test_router_resolves_account_route_to_listing_venue(self) -> None:
        catalog = ReferenceCatalog()
        instrument_id = InstrumentId("equity:us:AAPL")
        product_id = ProductId("product:equity:us:AAPL")
        listing_id = ListingId("listing:xnas:AAPL")
        account = AccountKey(InstitutionId("ibkr"), "paper", AccountType.SECURITIES_MARGIN)
        catalog.products.add(EconomicProduct(product_id, ProductType.EQUITY, "Apple", NOW, currency=AssetId("USD")))
        catalog.instruments.add(InstrumentDefinition(instrument_id, product_id, ProductType.EQUITY, EquitySpec("NASDAQ", "US", AssetId("USD")), InstrumentLifecycle(listed_at=NOW), NOW))
        catalog.listings.add(ListingDefinition(listing_id, instrument_id, VenueId("xnas"), "AAPL", AssetId("USD"), TradingRules(Decimal("0.01"), Decimal("1"), Decimal("1")), NOW))
        catalog.routes.add(ExecutionRoute(RouteId("route:ibkr:paper:AAPL"), BrokerId("ibkr"), account, listing_id, NOW, broker_contract_id="265598"))
        gateway = ExecutionGateway()
        router = ExecutionRouter(catalog, (gateway,))
        request = OrderRequest("internal-1", "client-1", "strategy", "intent", "correlation", account, instrument_id, TradeSide.BUY, Decimal("2"), ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("200.00")))
        ack = router.submit(request, NOW)
        self.assertEqual(ack.venue_order_id, "venue-order-1")
        self.assertEqual(gateway.requests, [request])

    def test_router_enforces_current_listing_rules(self) -> None:
        catalog = ReferenceCatalog()
        instrument_id = InstrumentId("equity:us:AAPL")
        product_id = ProductId("product:equity:us:AAPL")
        listing_id = ListingId("listing:xnas:AAPL")
        account = AccountKey(InstitutionId("ibkr"), "paper", AccountType.SECURITIES_MARGIN)
        catalog.products.add(EconomicProduct(product_id, ProductType.EQUITY, "Apple", NOW))
        catalog.instruments.add(InstrumentDefinition(instrument_id, product_id, ProductType.EQUITY, EquitySpec("NASDAQ", "US", AssetId("USD")), InstrumentLifecycle(), NOW))
        catalog.listings.add(ListingDefinition(listing_id, instrument_id, VenueId("xnas"), "AAPL", AssetId("USD"), TradingRules(Decimal("0.01"), Decimal("1"), Decimal("1")), NOW))
        catalog.routes.add(ExecutionRoute(RouteId("route"), BrokerId("ibkr"), account, listing_id, NOW))
        router = ExecutionRouter(catalog, (ExecutionGateway(),))
        request = OrderRequest("internal-2", "client-2", "strategy", "intent", "correlation", account, instrument_id, TradeSide.BUY, Decimal("0.5"), ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("200")))
        with self.assertRaises(ValueError):
            router.submit(request, NOW)


if __name__ == "__main__":
    unittest.main()
