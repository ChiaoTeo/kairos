from __future__ import annotations

from kairos.domain.identity import InstitutionId

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from uuid import uuid4

from kairos.accounting.ledger import LedgerService
from kairos.domain.corporate_action import DelistingEvent, SymbolChangeEvent
from kairos.domain.identity import AccountKey, AccountType, AssetId, InstrumentId, VenueId
from kairos.domain.ledger import Ledger
from kairos.domain.product import ContractType, EquitySpec, PerpetualSpec, ProductType
from kairos.products.equity.corporate_actions import CorporateActionService
from kairos.products.perpetual.funding import FundingEngine
from kairos.reference import (
    EconomicProduct, InstrumentDefinition, InstrumentLifecycle,
    ListingDefinition, ListingId, ProductId, ReferenceCatalog, TradingRules,
    VenueDefinition, VenueType,
)


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class ProductLifecycleReferenceTests(unittest.TestCase):
    def test_symbol_change_and_delisting_version_only_the_listing(self) -> None:
        catalog = ReferenceCatalog(); instrument = InstrumentId("equity:us:OLD"); product = ProductId("product:equity:OLD")
        catalog.products.add(EconomicProduct(product, ProductType.EQUITY, "Company", NOW, currency=AssetId("USD")))
        catalog.instruments.add(InstrumentDefinition(instrument, product, ProductType.EQUITY, EquitySpec("NASDAQ", "US", AssetId("USD")), InstrumentLifecycle(), NOW, display_name="OLD"))
        catalog.venues.add(VenueDefinition(VenueId("xnas"), VenueType.EXCHANGE, "Nasdaq", "America/New_York", NOW, mic="XNAS"))
        listing_id = ListingId("listing:xnas:OLD")
        catalog.listings.add(ListingDefinition(listing_id, instrument, VenueId("xnas"), "OLD", AssetId("USD"), TradingRules(Decimal("0.01"), Decimal("1"), Decimal("1")), NOW))
        service = CorporateActionService(LedgerService(Ledger(), catalog))
        changed = NOW + timedelta(days=1); delisted = NOW + timedelta(days=2)
        service.apply_symbol_change(SymbolChangeEvent(uuid4(), instrument, changed, "NEW", "NEW"))
        self.assertEqual(catalog.instruments.get(instrument, changed).display_name, "NEW")
        self.assertEqual(catalog.listings.get(listing_id, changed).trading_symbol, "NEW")
        service.apply_delisting(DelistingEvent(uuid4(), instrument, delisted, "test"))
        self.assertEqual(catalog.active_listings(instrument, delisted), ())
        self.assertEqual(catalog.instruments.get(instrument, delisted).instrument_id, instrument)

    def test_funding_engine_reads_current_perpetual_spec(self) -> None:
        catalog = ReferenceCatalog(); instrument = InstrumentId("crypto:perp:BTCUSDT"); product = ProductId("product:perp:BTC")
        spec = PerpetualSpec(AssetId("BTC"), AssetId("USDT"), "index", Decimal("1"), ContractType.LINEAR, 28800)
        catalog.products.add(EconomicProduct(product, ProductType.PERPETUAL, "BTC perp", NOW, currency=AssetId("USDT")))
        catalog.instruments.add(InstrumentDefinition(instrument, product, ProductType.PERPETUAL, spec, InstrumentLifecycle(), NOW))
        ledger = Ledger(); account = AccountKey(InstitutionId("binance"), "main", AccountType.DERIVATIVES)
        payment = FundingEngine(LedgerService(ledger, catalog)).apply(account, instrument, Decimal("2"), Decimal("50000"), Decimal("0.0001"), NOW)
        self.assertEqual(payment.amount, Decimal("-10.0000"))


if __name__ == "__main__":
    unittest.main()
