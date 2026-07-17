from __future__ import annotations

from trading.domain.identity import InstitutionId

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from trading.accounting.conversion import AssetConversionGraph
from trading.accounting.ledger import LedgerService
from trading.accounting.portfolio import Portfolio
from trading.domain.corporate_action import CashDividendEvent, SplitEvent
from trading.domain.execution import TradeExecution, TradeSide
from trading.domain.identity import AccountKey, AccountType, AssetId, InstrumentId, VenueId
from trading.domain.ledger import Ledger, LedgerBook
from trading.domain.product import EquitySpec, ExerciseStyle, ListedOptionSpec, OptionRight, ProductType, SettlementSession, SettlementType
from trading.products.equity.corporate_actions import CorporateActionService
from trading.products.listed_option.lifecycle import OptionLifecycleService, PhysicalOptionEvent, PhysicalOptionEventType
from trading.risk.covered_call import validate_covered_call
from trading.execution.strategy_planner import plan_strategy_intent
from trading.domain.capability import OrderType
from trading.domain.order import ExecutionInstructions, TimeInForce
from trading.strategies.covered_call import CoveredCallStrategy
from trading.strategies.protective_put import ProtectivePutStrategy
from trading.reference import ReferenceCatalog
from tests.reference_support import publish_test_instrument


NOW = datetime(2025, 1, 2, 15, 30, tzinfo=timezone.utc)


class EquityOptionTests(unittest.TestCase):
    def setUp(self):
        self.account = AccountKey(InstitutionId("ibkr"), "paper", AccountType.SECURITIES_MARGIN)
        self.equity_id = InstrumentId("equity:us:aapl")
        self.option_id = InstrumentId("option:aapl:20250221:105:c")
        self.catalog = ReferenceCatalog()
        self.equity = publish_test_instrument(
            self.catalog, self.equity_id, ProductType.EQUITY, "AAPL",
            EquitySpec("NASDAQ", "US", AssetId("USD")), AssetId("USD"), VenueId("ibkr"), "AAPL",
            NOW - timedelta(days=1),
        )
        expiry = NOW + timedelta(days=50)
        self.option = publish_test_instrument(
            self.catalog, self.option_id, ProductType.LISTED_OPTION, "AAPL 105C",
            ListedOptionSpec(self.equity_id, expiry, Decimal("105"), OptionRight.CALL, ExerciseStyle.AMERICAN, SettlementType.PHYSICAL, SettlementSession.PM, Decimal("100"), expiry),
            AssetId("USD"), VenueId("ibkr"), "AAPL  250221C00105000", NOW - timedelta(days=1),
        )
        self.ledger = Ledger(); self.service = LedgerService(self.ledger, self.catalog)
        self.service.deposit(self.account, AssetId("USD"), Decimal("20000"), NOW, "initial")

    def trade(self, instrument_id, side, quantity, price, fee, seconds):
        self.service.trade(TradeExecution(uuid4(), NOW + timedelta(seconds=seconds), self.account, instrument_id, side, Decimal(quantity), Decimal(price), AssetId("USD"), Decimal(fee), f"order-{seconds}"))

    def test_covered_call_assignment_dividend_and_cash_reconcile(self):
        self.trade(self.equity_id, TradeSide.BUY, "100", "100", "1", 1)
        strategy = CoveredCallStrategy(self.equity_id, self.option_id)
        intents = strategy.intents(Decimal("100"), Decimal("0"))
        self.assertEqual(len(intents), 1)
        validate_covered_call(intents[0], self.account, self.ledger, self.catalog, NOW + timedelta(seconds=2))
        self.trade(self.option_id, TradeSide.SELL, "1", "2", "0.65", 2)
        CorporateActionService(self.service).apply_dividend(self.account, CashDividendEvent(uuid4(), self.equity_id, NOW, NOW + timedelta(seconds=3), AssetId("USD"), Decimal("1")))
        OptionLifecycleService(self.service).apply(PhysicalOptionEvent(uuid4(), PhysicalOptionEventType.ASSIGNMENT, self.account, self.option_id, Decimal("1"), NOW + timedelta(seconds=4), Decimal("110")))
        self.assertEqual(self.ledger.book_balance(self.account, LedgerBook.CASH, AssetId("USD")), Decimal("20798.35"))
        graph = AssetConversionGraph()
        snapshot = Portfolio(self.ledger, self.catalog, AssetId("USD")).snapshot(NOW + timedelta(seconds=5), {}, graph)
        self.assertFalse(snapshot.positions)
        self.assertEqual(snapshot.net_asset_value, Decimal("20798.35"))

    def test_naked_call_is_rejected(self):
        intent = CoveredCallStrategy(self.equity_id, self.option_id).intents(Decimal("100"), Decimal("0"))[0]
        with self.assertRaisesRegex(ValueError, "naked call"):
            validate_covered_call(intent, self.account, self.ledger, self.catalog, NOW)

    def test_covered_call_strategy_intent_plans_a_venue_independent_option_order(self):
        self.service.deposit(self.account, AssetId("USD"), Decimal("20000"), NOW, "capital")
        self.service.trade(TradeExecution(uuid4(), NOW + timedelta(seconds=1), self.account, self.equity.instrument_id, TradeSide.BUY, Decimal("100"), Decimal("100"), AssetId("USD"), Decimal("0"), "stock"))
        intent = CoveredCallStrategy(self.equity.instrument_id, self.option.instrument_id).intents(Decimal("100"), Decimal("0"))[0]
        validate_covered_call(intent, self.account, self.ledger, self.catalog, NOW + timedelta(seconds=2))
        plan = plan_strategy_intent(
            intent, accounts={self.option.instrument_id: self.account}, current_positions={},
            instructions={self.option.instrument_id: ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("2"))},
        )
        self.assertEqual(plan.orders[0].instrument_id, self.option.instrument_id)
        self.assertEqual(plan.orders[0].side, TradeSide.SELL)
        self.assertEqual(plan.orders[0].intent_id, str(intent.intent_id))

    def test_protective_put_strategy_buys_put_and_exercise_delivers_stock_at_strike(self):
        put = publish_test_instrument(
            self.catalog, InstrumentId("option:aapl:put"), ProductType.LISTED_OPTION, "AAPL PUT",
            ListedOptionSpec(self.equity.instrument_id, self.option.contract_spec.expiry, Decimal("95"), OptionRight.PUT, ExerciseStyle.AMERICAN, SettlementType.PHYSICAL, SettlementSession.PM, Decimal("100"), self.option.contract_spec.expiry),
            AssetId("USD"), VenueId("ibkr"), "AAPL PUT", self.option.effective_from,
        )
        self.service.deposit(self.account, AssetId("USD"), Decimal("20000"), NOW, "protective-capital")
        self.service.trade(TradeExecution(uuid4(), NOW + timedelta(seconds=1), self.account, self.equity.instrument_id, TradeSide.BUY, Decimal("100"), Decimal("100"), AssetId("USD"), Decimal("0"), "stock"))
        intent = ProtectivePutStrategy(self.equity.instrument_id, put.instrument_id).intents(Decimal("100"), Decimal("0"))[0]
        plan = plan_strategy_intent(intent, accounts={put.instrument_id: self.account}, current_positions={}, instructions={put.instrument_id: ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("2"))})
        self.assertEqual(plan.orders[0].side, TradeSide.BUY)
        self.service.trade(TradeExecution(uuid4(), NOW + timedelta(seconds=2), self.account, put.instrument_id, TradeSide.BUY, Decimal("1"), Decimal("2"), AssetId("USD"), Decimal("0"), "put"))
        OptionLifecycleService(self.service).apply(PhysicalOptionEvent(uuid4(), PhysicalOptionEventType.EXERCISE, self.account, put.instrument_id, Decimal("1"), NOW + timedelta(seconds=3), Decimal("90")))
        self.assertEqual(self.ledger.book_balance(self.account, LedgerBook.POSITION, AssetId(f"POSITION:{self.equity.instrument_id.value}")), Decimal("0"))

    def test_option_expiration_otm_leaves_stock_position(self):
        self.trade(self.equity_id, TradeSide.BUY, "100", "100", "1", 1)
        self.trade(self.option_id, TradeSide.SELL, "1", "2", "0.65", 2)
        OptionLifecycleService(self.service).apply(PhysicalOptionEvent(uuid4(), PhysicalOptionEventType.EXPIRATION, self.account, self.option_id, Decimal("1"), NOW + timedelta(seconds=3)))
        snapshot = Portfolio(self.ledger, self.catalog, AssetId("USD")).snapshot(NOW + timedelta(seconds=4), {self.equity_id: Decimal("103")}, AssetConversionGraph())
        self.assertEqual(len(snapshot.positions), 1)
        self.assertEqual(snapshot.positions[0].instrument_id, self.equity_id)
        self.assertEqual(snapshot.positions[0].quantity, Decimal("100"))

    def test_split_preserves_economic_value_and_adjusts_cost(self):
        self.trade(self.equity_id, TradeSide.BUY, "100", "100", "1", 1)
        CorporateActionService(self.service).apply_split(self.account, SplitEvent(uuid4(), self.equity_id, NOW + timedelta(seconds=2), Decimal("2")))
        snapshot = Portfolio(self.ledger, self.catalog, AssetId("USD")).snapshot(NOW + timedelta(seconds=3), {self.equity_id: Decimal("50")}, AssetConversionGraph())
        position = snapshot.positions[0]
        self.assertEqual(position.quantity, Decimal("200"))
        self.assertEqual(position.average_price, Decimal("50"))
        self.assertEqual(position.market_value_reporting, Decimal("10000"))


if __name__ == "__main__": unittest.main()
