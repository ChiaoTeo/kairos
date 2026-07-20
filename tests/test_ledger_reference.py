from __future__ import annotations

from kairospy.domain.identity import InstitutionId

from datetime import datetime, timezone
from decimal import Decimal
import unittest
from uuid import uuid4

from kairospy.accounting.ledger import LedgerService
from kairospy.domain.execution import TradeExecution, TradeSide
from kairospy.domain.identity import AccountKey, AccountType, AssetId, InstrumentId, VenueId
from kairospy.domain.ledger import Ledger, LedgerBook
from kairospy.domain.product import CryptoSpotSpec, ProductType
from kairospy.reference import EconomicProduct, InstrumentDefinition, InstrumentLifecycle, ProductId, ReferenceCatalog


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class LedgerReferenceTests(unittest.TestCase):
    def test_spot_trade_posts_cash_from_current_contract_spec(self) -> None:
        catalog = ReferenceCatalog()
        instrument = InstrumentId("crypto:binance:spot:BTCUSDT")
        product = ProductId("product:BTCUSDT")
        catalog.products.add(EconomicProduct(product, ProductType.CRYPTO_SPOT, "BTC/USDT", NOW, currency=AssetId("USDT")))
        catalog.instruments.add(InstrumentDefinition(instrument, product, ProductType.CRYPTO_SPOT, CryptoSpotSpec(AssetId("BTC"), AssetId("USDT")), InstrumentLifecycle(), NOW))
        ledger = Ledger(); service = LedgerService(ledger, catalog)
        account = AccountKey(InstitutionId("binance"), "spot", AccountType.CRYPTO_SPOT)
        service.trade(TradeExecution(uuid4(), NOW, account, instrument, TradeSide.BUY, Decimal("0.1"), Decimal("50000"), AssetId("USDT"), Decimal("5"), "order"))
        self.assertEqual(ledger.book_balance(account, LedgerBook.CASH, AssetId("USDT")), Decimal("-5005"))
        self.assertEqual(ledger.book_balance(account, LedgerBook.POSITION, AssetId(f"POSITION:{instrument.value}")), Decimal("0.1"))


if __name__ == "__main__":
    unittest.main()
