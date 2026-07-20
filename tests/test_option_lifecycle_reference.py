from __future__ import annotations

from kairospy.domain.identity import InstitutionId

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from uuid import uuid4

from kairospy.accounting.ledger import LedgerService
from kairospy.domain.execution import TradeExecution, TradeSide
from kairospy.domain.identity import AccountKey, AccountType, AssetId, InstrumentId, VenueId
from kairospy.domain.ledger import Ledger, LedgerBook
from kairospy.domain.product import EquitySpec, ExerciseStyle, ListedOptionSpec, OptionRight, ProductType, SettlementSession, SettlementType
from kairospy.products.listed_option.lifecycle import OptionLifecycleService, PhysicalOptionEvent, PhysicalOptionEventType
from kairospy.reference import ReferenceCatalog
from tests.reference_support import publish_test_instrument


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class OptionLifecycleReferenceTests(unittest.TestCase):
    def _fixture(self):
        underlying = InstrumentId("equity:us:AAPL"); option = InstrumentId("option:us:AAPL:C200")
        catalog = ReferenceCatalog()
        publish_test_instrument(catalog, underlying, ProductType.EQUITY, "AAPL", EquitySpec("XNAS", "US", AssetId("USD")), AssetId("USD"), VenueId("xnas"), "AAPL", NOW)
        spec = ListedOptionSpec(underlying, NOW + timedelta(days=30), Decimal("200"), OptionRight.CALL, ExerciseStyle.AMERICAN, SettlementType.PHYSICAL, SettlementSession.PM, Decimal("100"), NOW + timedelta(days=30))
        publish_test_instrument(catalog, option, ProductType.LISTED_OPTION, "AAPL-C", spec, AssetId("USD"), VenueId("xnas"), "AAPL-C", NOW, NOW + timedelta(days=31))
        ledger = Ledger(); service = LedgerService(ledger, catalog)
        account = AccountKey(InstitutionId("ibkr"), "paper", AccountType.SECURITIES_MARGIN)
        return underlying, option, catalog, ledger, service, account

    def test_current_exercise_posts_underlying_instrument_and_strike_cash(self):
        underlying, option, catalog, ledger, service, account = self._fixture()
        service.trade(TradeExecution(uuid4(), NOW, account, option, TradeSide.BUY, Decimal("1"), Decimal("5"), AssetId("USD"), Decimal("0"), "buy-option"))
        OptionLifecycleService(service).apply(PhysicalOptionEvent(uuid4(), PhysicalOptionEventType.EXERCISE, account, option, Decimal("1"), NOW + timedelta(days=1), Decimal("210")))
        self.assertEqual(ledger.book_balance(account, LedgerBook.POSITION, AssetId(f"POSITION:{underlying.value}")), Decimal("100"))
        self.assertEqual(ledger.book_balance(account, LedgerBook.CASH, AssetId("USD")), Decimal("-20500"))

    def test_current_adjustment_versions_instrument_listing_and_deliverable(self):
        _, option, catalog, _, service, _ = self._fixture()
        effective = NOW + timedelta(days=1)
        OptionLifecycleService(service).adjust_contract(option, effective, strike=Decimal("100"), multiplier=Decimal("200"), symbol="AAPL-ADJ")
        definition = catalog.instruments.get(option, effective)
        listing = catalog.active_listings(option, effective)[0]
        terms = catalog.settlements.get(definition.settlement_terms_id, effective).terms
        self.assertEqual(definition.contract_spec.multiplier, Decimal("200"))
        self.assertEqual(listing.trading_symbol, "AAPL-ADJ")
        self.assertEqual(terms.deliverables[0].quantity, Decimal("200"))


if __name__ == "__main__":
    unittest.main()
