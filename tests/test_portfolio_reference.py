from __future__ import annotations

from trading.domain.identity import InstitutionId

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from uuid import uuid4

from trading.accounting.conversion import AssetConversionGraph, ConversionRate
from trading.accounting.portfolio import Portfolio
from trading.domain.identity import AccountKey, AccountType, AssetId, InstrumentId, VenueId
from trading.domain.ledger import Ledger, LedgerBook, LedgerEntry, LedgerEntryType, LedgerTransaction
from trading.domain.product import CryptoSpotSpec, ProductType
from trading.reference import EconomicProduct, InstrumentDefinition, InstrumentLifecycle, ProductId, ReferenceCatalog


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class PortfolioReferenceTests(unittest.TestCase):
    def test_portfolio_values_positions_from_current_contract_spec(self) -> None:
        catalog = ReferenceCatalog()
        instrument_id = InstrumentId("crypto:spot:BTCUSDT")
        product_id = ProductId("product:crypto:BTCUSDT")
        catalog.products.add(EconomicProduct(product_id, ProductType.CRYPTO_SPOT, "BTC/USDT", NOW, currency=AssetId("USDT")))
        catalog.instruments.add(InstrumentDefinition(instrument_id, product_id, ProductType.CRYPTO_SPOT, CryptoSpotSpec(AssetId("BTC"), AssetId("USDT")), InstrumentLifecycle(), NOW))
        account = AccountKey(InstitutionId("binance"), "spot", AccountType.CRYPTO_SPOT)
        transaction_id = uuid4()
        position_asset = AssetId(f"POSITION:{instrument_id.value}")
        entries = (
            LedgerEntry(uuid4(), transaction_id, NOW, account, LedgerBook.POSITION, position_asset, Decimal("0.1"), LedgerEntryType.TRADE_POSITION, "trade", instrument_id, Decimal("50000")),
            LedgerEntry(uuid4(), transaction_id, NOW, account, LedgerBook.CLEARING, position_asset, Decimal("-0.1"), LedgerEntryType.TRADE_POSITION, "trade", instrument_id, Decimal("50000")),
        )
        ledger = Ledger(); ledger.post(LedgerTransaction(transaction_id, NOW, "trade", entries))
        graph = AssetConversionGraph(); graph.update(ConversionRate(AssetId("USDT"), AssetId("USD"), Decimal("1"), NOW, "test"))
        snapshot = Portfolio(ledger, catalog, AssetId("USD")).snapshot(NOW + timedelta(seconds=1), {instrument_id: Decimal("51000")}, graph)
        self.assertEqual(snapshot.positions[0].quantity, Decimal("0.1"))
        self.assertEqual(snapshot.positions[0].unrealized_pnl_reporting, Decimal("100.0"))


if __name__ == "__main__":
    unittest.main()
