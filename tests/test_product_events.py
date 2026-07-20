from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from kairos.accounting.conversion import AssetConversionGraph, ConversionRate
from kairos.accounting.ledger import LedgerService
from kairos.accounting.portfolio import Portfolio
from kairos.domain.corporate_action import (
    CorporateActionType, DelistingEvent, InstrumentExchangeEvent, StockDividendEvent, SymbolChangeEvent,
)
from kairos.domain.derivative_event import DerivativeEventType, DerivativePositionEvent
from kairos.domain.event import EventEnvelope
from kairos.domain.execution import TradeExecution, TradeSide
from kairos.domain.identity import AccountKey, AccountType, AssetId, InstitutionId, InstrumentId, VenueId
from kairos.domain.ledger import Ledger, LedgerBook
from kairos.domain.market_data import IndexPrice, VolatilitySurfacePoint
from kairos.domain.market_state import MarketState, apply_market_event
from kairos.domain.product import (
    ContractType, EquitySpec, ExerciseStyle, FutureSpec, ListedOptionSpec, OptionRight,
    ProductType, SettlementSession, SettlementType,
)
from kairos.products.equity.corporate_actions import CorporateActionService
from kairos.products.future.settlement import DerivativeLifecycleService
from kairos.products.listed_option.lifecycle import OptionLifecycleService
from kairos.risk.margin import CryptoCrossMarginPolicy, CryptoIsolatedMarginPolicy, CryptoSpotPolicy
from kairos.reference import ReferenceCatalog
from kairos.reference.contracts import InstrumentDefinition
from tests.reference_support import publish_test_instrument


NOW = datetime(2026, 7, 14, 8, tzinfo=timezone.utc)
VENUE = VenueId("test")
ACCOUNT = AccountKey(InstitutionId("xnas"), "main", AccountType.SECURITIES_MARGIN)


def equity(catalog: ReferenceCatalog, instrument_id: str, symbol: str) -> InstrumentDefinition:
    return publish_test_instrument(
        catalog, InstrumentId(instrument_id), ProductType.EQUITY, symbol,
        EquitySpec("NASDAQ", "US", AssetId("USD")), AssetId("USD"), VENUE, symbol,
        datetime(2020, 1, 1, tzinfo=timezone.utc),
    )


class ProductEventTests(unittest.TestCase):
    def test_transfers_locked_balance_borrow_interest_and_stablecoin_depeg(self) -> None:
        ledger, catalog = Ledger(), ReferenceCatalog()
        service = LedgerService(ledger, catalog)
        source = AccountKey(InstitutionId("xnas"), "source", AccountType.CRYPTO_SPOT)
        destination = AccountKey(InstitutionId("xnas"), "destination", AccountType.DERIVATIVES)
        service.deposit(source, AssetId("USDT"), Decimal("100"), NOW, "deposit")
        service.transfer(source, destination, AssetId("USDT"), Decimal("30"), NOW + timedelta(seconds=1), "margin-transfer")
        service.reclassify_balance(source, AssetId("USDT"), Decimal("20"), LedgerBook.CASH, LedgerBook.LOCKED, NOW + timedelta(seconds=2), "lock")
        service.borrow_interest(source, AssetId("USDT"), Decimal("1"), NOW + timedelta(seconds=3), "borrow")
        service.withdrawal(destination, AssetId("USDT"), Decimal("5"), NOW + timedelta(seconds=4), "withdraw")
        service.borrow_asset(source, AssetId("BTC"), Decimal("0.1"), NOW + timedelta(seconds=5), "short-borrow")
        graph = AssetConversionGraph()
        graph.update(ConversionRate(AssetId("USDT"), AssetId("USD"), Decimal("0.95"), NOW + timedelta(seconds=5), "depeg-fixture"))
        snapshot = Portfolio(ledger, catalog, AssetId("USD")).snapshot(NOW + timedelta(seconds=5), {}, graph)
        source_balance = next(item for item in snapshot.balances if item.account == source and item.asset == AssetId("USDT"))
        self.assertEqual(source_balance.total, Decimal("69"))
        self.assertEqual(source_balance.locked, Decimal("20"))
        btc = next(item for item in snapshot.balances if item.account == source and item.asset == AssetId("BTC"))
        self.assertEqual(btc.total, Decimal("0"))
        self.assertEqual(btc.borrowed, Decimal("0.1"))
        self.assertEqual(snapshot.net_asset_value, Decimal("89.30"))

    def test_stock_dividend_spinoff_merger_symbol_change_and_delisting_are_auditable(self) -> None:
        catalog, ledger = ReferenceCatalog(), Ledger()
        source, target = equity(catalog, "equity:aaa", "AAA"), equity(catalog, "equity:bbb", "BBB")
        merger_target = equity(catalog, "equity:ccc", "CCC")
        service = LedgerService(ledger, catalog)
        actions = CorporateActionService(service)
        service.deposit(ACCOUNT, AssetId("USD"), Decimal("20000"), NOW, "capital")
        service.trade(TradeExecution(uuid4(), NOW + timedelta(seconds=1), ACCOUNT, source.instrument_id, TradeSide.BUY, Decimal("100"), Decimal("100"), AssetId("USD"), Decimal("0"), "buy"))
        actions.apply_stock_dividend(ACCOUNT, StockDividendEvent(uuid4(), source.instrument_id, NOW + timedelta(seconds=2), Decimal("0.10")))
        actions.apply_exchange(ACCOUNT, InstrumentExchangeEvent(uuid4(), CorporateActionType.SPINOFF, source.instrument_id, target.instrument_id, NOW + timedelta(seconds=3), Decimal("0.20")))
        self.assertEqual(Decimal("110") * Decimal("80") + Decimal("22") * Decimal("100"), Decimal("11000"))
        actions.apply_exchange(ACCOUNT, InstrumentExchangeEvent(uuid4(), CorporateActionType.MERGER, source.instrument_id, merger_target.instrument_id, NOW + timedelta(seconds=4), Decimal("0.50")))
        self.assertEqual(ledger.book_balance(ACCOUNT, LedgerBook.POSITION, AssetId("POSITION:equity:aaa")), Decimal("0"))
        self.assertEqual(ledger.book_balance(ACCOUNT, LedgerBook.POSITION, AssetId("POSITION:equity:bbb")), Decimal("22"))
        self.assertEqual(ledger.book_balance(ACCOUNT, LedgerBook.POSITION, AssetId("POSITION:equity:ccc")), Decimal("55"))
        self.assertEqual(Decimal("22") * Decimal("100") + Decimal("55") * Decimal("160"), Decimal("11000"))
        graph = AssetConversionGraph(); graph.update(ConversionRate(AssetId("USD"), AssetId("USD"), Decimal("1"), NOW + timedelta(seconds=4), "identity"))
        portfolio = Portfolio(ledger, catalog, AssetId("USD")).snapshot(NOW + timedelta(seconds=4), {target.instrument_id: Decimal("100"), merger_target.instrument_id: Decimal("160")}, graph)
        self.assertEqual(portfolio.positions[0].instrument_id, target.instrument_id)
        self.assertEqual(portfolio.positions[0].quantity, Decimal("22"))
        change_at = NOW + timedelta(seconds=5)
        actions.apply_symbol_change(SymbolChangeEvent(uuid4(), target.instrument_id, change_at, "NEW", "NEW"))
        self.assertEqual(catalog.instruments.get(target.instrument_id, change_at).display_name, "NEW")
        actions.apply_delisting(DelistingEvent(uuid4(), target.instrument_id, NOW + timedelta(seconds=6), "merger complete"))
        self.assertEqual(catalog.active_listings(target.instrument_id, NOW + timedelta(seconds=6)), ())
        self.assertTrue(any(entry.entry_type.value == "corporate_action" for entry in ledger.entries))

    def test_adjusted_option_expiration_threshold_physically_delivers_shares(self) -> None:
        catalog, ledger = ReferenceCatalog(), Ledger()
        stock = equity(catalog, "equity:aaa", "AAA")
        expiry = NOW + timedelta(days=1)
        option = publish_test_instrument(
            catalog, InstrumentId("option:aaa:call"), ProductType.LISTED_OPTION, "AAA1",
            ListedOptionSpec(stock.instrument_id, expiry, Decimal("100"), OptionRight.CALL, ExerciseStyle.AMERICAN, SettlementType.PHYSICAL, SettlementSession.PM, Decimal("100"), expiry),
            AssetId("USD"), VENUE, "AAA1", datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        service = LedgerService(ledger, catalog)
        lifecycle = OptionLifecycleService(service)
        adjust_at = NOW + timedelta(seconds=1)
        lifecycle.adjust_contract(option.instrument_id, adjust_at, strike=Decimal("90"), multiplier=Decimal("50"), symbol="AAA-ADJ")
        service.deposit(ACCOUNT, AssetId("USD"), Decimal("10000"), NOW, "capital")
        service.trade(TradeExecution(uuid4(), NOW + timedelta(seconds=2), ACCOUNT, option.instrument_id, TradeSide.BUY, Decimal("1"), Decimal("2"), AssetId("USD"), Decimal("1"), "option-buy"))
        lifecycle.expire(ACCOUNT, option.instrument_id, Decimal("100"), NOW + timedelta(seconds=3))
        self.assertEqual(ledger.book_balance(ACCOUNT, LedgerBook.POSITION, AssetId("POSITION:option:aaa:call")), Decimal("0"))
        self.assertEqual(ledger.book_balance(ACCOUNT, LedgerBook.POSITION, AssetId("POSITION:equity:aaa")), Decimal("50"))
        self.assertEqual(ledger.book_balance(ACCOUNT, LedgerBook.CASH, AssetId("USD")), Decimal("5399"))

    def test_future_expiry_liquidation_and_adl_close_positions_through_same_ledger(self) -> None:
        catalog, ledger = ReferenceCatalog(), Ledger()
        future = publish_test_instrument(
            catalog, InstrumentId("future:btc"), ProductType.FUTURE, "BTC-FUT",
            FutureSpec(AssetId("BTC"), AssetId("USDT"), NOW + timedelta(days=1), Decimal("1"), ContractType.LINEAR, "BTCUSDT"),
            AssetId("USDT"), VENUE, "BTC-FUT", datetime(2020, 1, 1, tzinfo=timezone.utc),
            price_increment=Decimal("0.1"),
        )
        service = LedgerService(ledger, catalog)
        lifecycle = DerivativeLifecycleService(service)
        service.deposit(ACCOUNT, AssetId("USDT"), Decimal("1000"), NOW, "margin")
        service.trade(TradeExecution(uuid4(), NOW + timedelta(seconds=1), ACCOUNT, future.instrument_id, TradeSide.BUY, Decimal("3"), Decimal("100"), AssetId("USDT"), Decimal("0"), "open"))
        lifecycle.apply(DerivativePositionEvent(uuid4(), DerivativeEventType.POSITION_LIQUIDATED, ACCOUNT, future.instrument_id, Decimal("1"), Decimal("90"), AssetId("USDT"), NOW + timedelta(seconds=2), "margin breach"))
        lifecycle.apply(DerivativePositionEvent(uuid4(), DerivativeEventType.AUTO_DELEVERAGED, ACCOUNT, future.instrument_id, Decimal("1"), Decimal("105"), AssetId("USDT"), NOW + timedelta(seconds=3), "venue adl"))
        lifecycle.apply(DerivativePositionEvent(uuid4(), DerivativeEventType.CONTRACT_EXPIRED, ACCOUNT, future.instrument_id, Decimal("1"), Decimal("110"), AssetId("USDT"), NOW + timedelta(seconds=4), "final settlement"))
        self.assertEqual(ledger.book_balance(ACCOUNT, LedgerBook.POSITION, AssetId("POSITION:future:btc")), Decimal("0"))
        self.assertEqual(ledger.book_balance(ACCOUNT, LedgerBook.CASH, AssetId("USDT")), Decimal("1005"))

    def test_normalized_index_and_vol_surface_events_and_margin_policies(self) -> None:
        state = MarketState()
        index = IndexPrice(InstrumentId("perp:btc"), Decimal("50000"), NOW)
        vol = VolatilitySurfacePoint(InstrumentId("asset:btc"), NOW + timedelta(days=30), Decimal("60000"), Decimal("0.55"), Decimal("0.25"), NOW, "fixture")
        for sequence, payload in enumerate((index, vol), 1):
            apply_market_event(state, EventEnvelope(uuid4(), NOW, NOW, payload, "fixture", sequence))
        self.assertEqual(len(state.normalized), 2)
        with self.assertRaises(ValueError):
            CryptoSpotPolicy().calculate(equity=Decimal("100"), quantity=Decimal("1"), price=Decimal("10"), direction=-1)
        self.assertGreater(CryptoCrossMarginPolicy().calculate(equity=Decimal("1000"), quantity=Decimal("1"), price=Decimal("100"), leverage=Decimal("2")).available_after, 0)
        with self.assertRaises(ValueError):
            CryptoIsolatedMarginPolicy().calculate(equity=Decimal("10"), quantity=Decimal("1"), price=Decimal("100"), leverage=Decimal("2"))


if __name__ == "__main__":
    unittest.main()
