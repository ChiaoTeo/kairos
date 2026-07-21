from __future__ import annotations

from kairospy.trading.identity import InstitutionId

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from kairospy.accounting.conversion import AssetConversionGraph, ConversionRate
from kairospy.accounting.ledger import LedgerService
from kairospy.accounting.portfolio import Portfolio
from kairospy.trading.execution import FundingPayment, TradeExecution, TradeSide
from kairospy.trading.identity import AccountKey, AccountType, AssetId, InstrumentId, VenueId
from kairospy.trading.ledger import Ledger, LedgerBook, LedgerEntry, LedgerEntryType, LedgerTransaction
from kairospy.trading.product import ContractType, CryptoSpotSpec, PerpetualSpec, ProductType
from kairospy.products.calculators import PositionCalculatorRegistry
from kairospy.risk.margin import CryptoCrossMarginPolicy
from kairospy.risk.view import build_risk_view
from kairospy.reference import ReferenceCatalog
from tests.reference_support import publish_test_instrument


NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


def definition(catalog, instrument_id, product_type, spec, base, quote):
    return publish_test_instrument(
        catalog, InstrumentId(instrument_id), product_type, instrument_id, spec, AssetId(quote),
        VenueId("binance"), instrument_id, NOW, quantity_increment=Decimal("0.001"), minimum_quantity=Decimal("0.001"),
    )


class LedgerPortfolioTests(unittest.TestCase):
    def setUp(self):
        self.account = AccountKey(InstitutionId("binance"), "paper", AccountType.CRYPTO_SPOT)
        self.catalog = ReferenceCatalog()
        self.spot = definition(self.catalog, "BTC-USDT", ProductType.CRYPTO_SPOT, CryptoSpotSpec(AssetId("BTC"), AssetId("USDT"), Decimal("10")), "BTC", "USDT")
        self.linear = definition(self.catalog, "BTC-USDT-PERP", ProductType.PERPETUAL, PerpetualSpec(AssetId("BTC"), AssetId("USDT"), "BTCUSDT", Decimal("1"), ContractType.LINEAR, 28800), "BTC", "USDT")
        self.inverse = definition(self.catalog, "BTC-USD-INVERSE", ProductType.PERPETUAL, PerpetualSpec(AssetId("BTC"), AssetId("BTC"), "BTCUSD", Decimal("100"), ContractType.INVERSE, 28800), "BTC", "USD")
        self.quanto = definition(self.catalog, "ETH-USD-QUANTO", ProductType.PERPETUAL, PerpetualSpec(AssetId("ETH"), AssetId("USD"), "ETHUSD", Decimal("1"), ContractType.QUANTO, 28800, Decimal("0.01")), "ETH", "USD")
        self.ledger = Ledger()
        self.service = LedgerService(self.ledger, self.catalog)

    def test_unbalanced_transaction_and_duplicate_are_rejected(self):
        transaction_id = uuid4()
        entry = LedgerEntry(uuid4(), transaction_id, NOW, self.account, LedgerBook.CASH, AssetId("USD"), Decimal("1"), LedgerEntryType.DEPOSIT, "x")
        with self.assertRaises(ValueError):
            LedgerTransaction(transaction_id, NOW, "x", (entry, entry))
        self.service.deposit(self.account, AssetId("USDT"), Decimal("10000"), NOW, "initial")
        with self.assertRaises(ValueError):
            self.service.deposit(self.account, AssetId("USDT"), Decimal("10000"), NOW, "initial")

    def test_spot_trade_fee_and_multi_asset_portfolio_rebuild(self):
        self.service.deposit(self.account, AssetId("USDT"), Decimal("10000"), NOW, "initial")
        self.service.trade(TradeExecution(uuid4(), NOW + timedelta(seconds=1), self.account, self.spot.instrument_id, TradeSide.BUY, Decimal("0.1"), Decimal("50000"), AssetId("USDT"), Decimal("5"), "order-1"))
        graph = AssetConversionGraph()
        graph.update(ConversionRate(AssetId("USDT"), AssetId("USD"), Decimal("1"), NOW + timedelta(seconds=2), "synthetic"))
        snapshot = Portfolio(self.ledger, self.catalog, AssetId("USD")).snapshot(NOW + timedelta(seconds=2), {self.spot.instrument_id: Decimal("51000")}, graph)
        self.assertEqual(snapshot.status, "complete")
        self.assertEqual(snapshot.positions[0].quantity, Decimal("0.1"))
        self.assertEqual(snapshot.positions[0].unrealized_pnl_reporting, Decimal("100.0"))
        usdt = next(item for item in snapshot.balances if item.asset == AssetId("USDT"))
        self.assertEqual(usdt.total, Decimal("4995.0"))
        self.assertEqual(snapshot.net_asset_value, Decimal("10095.0"))

    def test_missing_conversion_marks_portfolio_partial(self):
        self.service.deposit(self.account, AssetId("BTC"), Decimal("1"), NOW, "btc")
        snapshot = Portfolio(self.ledger, self.catalog, AssetId("USD")).snapshot(NOW, {}, AssetConversionGraph())
        self.assertEqual(snapshot.status, "partial")
        self.assertTrue(snapshot.unpriced_assets)
        graph = AssetConversionGraph()
        graph.update(ConversionRate(AssetId("BTC"), AssetId("USD"), Decimal("50000"), NOW - timedelta(hours=1), "stale"))
        stale = Portfolio(self.ledger, self.catalog, AssetId("USD")).snapshot(NOW, {}, graph, max_conversion_age=timedelta(minutes=5))
        self.assertEqual(stale.status, "partial")

    def test_linear_inverse_pnl_and_funding(self):
        registry = PositionCalculatorRegistry()
        linear = registry.for_definition(self.linear)
        inverse = registry.for_definition(self.inverse)
        quanto = registry.for_definition(self.quanto)
        self.assertEqual(linear.unrealized_pnl(self.linear, Decimal("2"), Decimal("51000"), Decimal("50000")), Decimal("2000"))
        expected_inverse = Decimal("10") * Decimal("100") * (Decimal("1") / Decimal("50000") - Decimal("1") / Decimal("40000"))
        self.assertEqual(inverse.unrealized_pnl(self.inverse, Decimal("10"), Decimal("40000"), Decimal("50000")), expected_inverse)
        self.assertEqual(quanto.unrealized_pnl(self.quanto, Decimal("2"), Decimal("3100"), Decimal("3000")), Decimal("2"))
        self.service.deposit(self.account, AssetId("USDT"), Decimal("1000"), NOW, "margin")
        self.service.funding(FundingPayment(uuid4(), NOW + timedelta(seconds=1), self.account, self.linear.instrument_id, AssetId("USDT"), Decimal("5"), Decimal("0.0001"), Decimal("50000")))
        self.assertEqual(self.ledger.book_balance(self.account, LedgerBook.CASH, AssetId("USDT")), Decimal("1005"))

    def test_unified_risk_view_attributes_exposure_greeks_margin_and_liquidation(self):
        self.service.deposit(self.account, AssetId("USDT"), Decimal("10000"), NOW, "initial")
        self.service.trade(TradeExecution(uuid4(), NOW + timedelta(seconds=1), self.account, self.linear.instrument_id, TradeSide.BUY, Decimal("1"), Decimal("50000"), AssetId("USDT"), Decimal("0"), "open"))
        graph = AssetConversionGraph(); graph.update(ConversionRate(AssetId("USDT"), AssetId("USD"), Decimal("1"), NOW + timedelta(seconds=2), "synthetic"))
        snapshot = Portfolio(self.ledger, self.catalog, AssetId("USD")).snapshot(NOW + timedelta(seconds=2), {self.linear.instrument_id: Decimal("51000")}, graph)
        margin = CryptoCrossMarginPolicy().calculate(equity=Decimal("10000"), quantity=Decimal("1"), price=Decimal("51000"), leverage=Decimal("10"))
        view = build_risk_view(snapshot, self.catalog, unit_greeks={self.linear.instrument_id: (Decimal("1"), Decimal("0"), Decimal("0"), Decimal("0"))}, margins={self.account: margin}, liquidation_prices={self.linear.instrument_id: Decimal("45500")})
        self.assertEqual(view.delta, Decimal("1"))
        self.assertEqual(view.margin_usage, Decimal("5100"))
        self.assertGreater(view.minimum_liquidation_distance, 0)
        self.assertTrue(any(item.dimension == "product" and item.key == "perpetual" for item in view.exposures))


if __name__ == "__main__": unittest.main()
