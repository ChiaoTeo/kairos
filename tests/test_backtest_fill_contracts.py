from __future__ import annotations

from kairospy.trading.identity import InstitutionId

import unittest
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from kairospy.backtest.execution import ExecutionPlanner, combo_quote
from kairospy.backtest.clock import BacktestClock
from kairospy.backtest.fill import FillModelType, FixedCommissionModel, ListedOptionComboFillModel
from kairospy.backtest.synthetic_scenarios import build_synthetic_backtest_dataset
from kairospy.backtest.portfolio import BacktestPortfolio
from kairospy.trading.execution import TradeSide
from kairospy.trading.identity import AccountKey, AccountType, VenueId
from kairospy.trading.intent import LegIntent, OpenStructureIntent
from kairospy.trading.market_data import Quote
from kairospy.trading.order import Fill, LegFill, OrderStatus, TimeInForce
from kairospy.trading.product import ListedOptionSpec
from kairospy.risk.engine import RiskDecisionType, RiskEngine
from kairospy.risk.limits import RiskLimits
from kairospy.storage.codec import from_primitive, to_primitive


class BacktestFillContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = build_synthetic_backtest_dataset()
        self.first, self.second = self.dataset.slices[:2]
        self.catalog = self.dataset.reference_catalog()
        by_strike = {
            definition.contract_spec.strike: definition.instrument_id
            for definition in self.dataset.definitions
            if isinstance(definition.contract_spec, ListedOptionSpec)
        }
        self.short = by_strike[Decimal("6000")]
        self.long = by_strike[Decimal("5950")]
        self.legs = (LegIntent(self.short, TradeSide.SELL), LegIntent(self.long, TradeSide.BUY))
        self.intent = OpenStructureIntent("test", self.legs, 1, Decimal("0.50"), TimeInForce.DAY, "test")
        self.account = AccountKey(InstitutionId("backtest"), "unit", AccountType.SECURITIES_MARGIN)

    def test_models_serialize_and_order_state_machine_rejects_illegal_transition(self) -> None:
        self.assertEqual(from_primitive(to_primitive(self.intent), OpenStructureIntent), self.intent)
        order = ExecutionPlanner(self.catalog).plan(self.intent, self.first.timestamp)
        self.assertEqual(order.status, OrderStatus.CREATED)
        working = order.transition(OrderStatus.WORKING)
        with self.assertRaises(ValueError):
            working.transition(OrderStatus.CREATED)
        filled = working.transition(OrderStatus.FILLED, filled_quantity=1)
        with self.assertRaises(ValueError):
            filled.transition(OrderStatus.CANCELLED)
        clock = BacktestClock()
        clock.advance(self.second.timestamp)
        with self.assertRaises(ValueError):
            clock.advance(self.first.timestamp)

    def test_combo_quote_and_no_same_slice_fill(self) -> None:
        quote = combo_quote(self.legs, self.first, 1)
        self.assertEqual(quote.natural, Decimal("2.8"))
        self.assertEqual(quote.midpoint, Decimal("3.0"))
        order = ExecutionPlanner(self.catalog).plan(self.intent, self.first.timestamp).transition(OrderStatus.WORKING)
        model = ListedOptionComboFillModel(FillModelType.CONSERVATIVE, FixedCommissionModel(), self.catalog)
        attempt = model.attempt(order, self.first)
        self.assertIsNone(attempt.fill)
        self.assertEqual(attempt.reason, "not_yet_eligible")
        attempt = model.attempt(attempt.order, self.second)
        self.assertIsNotNone(attempt.fill)
        self.assertEqual(attempt.fill.net_price, Decimal("2.8"))

    def test_stress_is_worse_and_commission_is_nonzero(self) -> None:
        order = ExecutionPlanner(self.catalog).plan(self.intent, self.first.timestamp).transition(OrderStatus.WORKING)
        conservative = ListedOptionComboFillModel(FillModelType.CONSERVATIVE, FixedCommissionModel(), self.catalog).attempt(order, self.second).fill
        stress = ListedOptionComboFillModel(FillModelType.STRESS, FixedCommissionModel(), self.catalog).attempt(order, self.second).fill
        midpoint = ListedOptionComboFillModel(FillModelType.MIDPOINT, FixedCommissionModel(), self.catalog).attempt(order, self.second).fill
        self.assertLess(stress.net_price, conservative.net_price)
        self.assertGreater(midpoint.net_price, conservative.net_price)
        self.assertGreater(conservative.commission, 0)
        self.assertGreater(stress.slippage, 0)

    def test_ioc_expires_on_first_unsuccessful_eligible_attempt(self) -> None:
        intent = OpenStructureIntent("test", self.legs, 1, Decimal("9"), TimeInForce.IOC, "unreachable")
        order = ExecutionPlanner(self.catalog).plan(intent, self.first.timestamp).transition(OrderStatus.WORKING)
        attempt = ListedOptionComboFillModel(FillModelType.CONSERVATIVE, FixedCommissionModel(), self.catalog).attempt(order, self.second)
        self.assertIsNone(attempt.fill)
        self.assertEqual(attempt.order.status, OrderStatus.EXPIRED)

    def test_portfolio_credit_spread_cash_pnl_and_greeks_reconcile(self) -> None:
        portfolio = BacktestPortfolio(Decimal("100000"), self.catalog, self.account)
        structure_id = uuid4()
        opening = Fill(
            uuid4(), uuid4(), uuid4(), "test", structure_id, self.second.timestamp,
            (LegFill(self.short, TradeSide.SELL, 1, Decimal("5")), LegFill(self.long, TradeSide.BUY, 1, Decimal("2.2"))),
            Decimal("2.8"), 1, Decimal("1.36"), Decimal("0"), False,
        )
        portfolio.apply_fill(opening)
        self.assertEqual(portfolio.cash, Decimal("100278.64"))
        snapshot = portfolio.snapshot(self.second)
        self.assertEqual(snapshot.max_theoretical_risk, Decimal("4720.0"))
        self.assertEqual(snapshot.greeks_coverage, Decimal("1"))
        closing = Fill(
            uuid4(), uuid4(), uuid4(), "test", structure_id, self.dataset.slices[3].timestamp,
            (LegFill(self.short, TradeSide.BUY, 1, Decimal("1.7")), LegFill(self.long, TradeSide.SELL, 1, Decimal("1"))),
            Decimal("-0.7"), 1, Decimal("1.36"), Decimal("0"), True,
        )
        portfolio.apply_fill(closing)
        final = portfolio.snapshot(self.dataset.slices[3])
        self.assertEqual(portfolio.cash, Decimal("100207.28"))
        self.assertFalse(final.open_structures)
        self.assertTrue(all(position.quantity == 0 for position in portfolio.positions.values()))
        self.assertEqual(final.realized_pnl, Decimal("210.0"))
        with self.assertRaises(ValueError):
            portfolio.apply_fill(closing)

    def test_single_sided_quote_uses_directional_fallback_and_is_counted(self) -> None:
        from dataclasses import replace
        from kairospy.capture.snapshot import InstrumentSnapshot
        portfolio = BacktestPortfolio(Decimal("100000"), self.catalog, self.account)
        structure_id = uuid4()
        portfolio.apply_fill(Fill(uuid4(), uuid4(), uuid4(), "test", structure_id, self.second.timestamp, (LegFill(self.long, TradeSide.BUY, 1, Decimal("2.2")),), Decimal("-2.2"), 1, Decimal("1"), Decimal("0"), False))
        items = tuple(
            replace(item, quote=Quote(item.instrument_id, Decimal("2"), None, Decimal("10"), None, self.second.timestamp)) if item.instrument_id == self.long else item
            for item in self.second.instruments
        )
        market = replace(self.second, instruments=items)
        snapshot = portfolio.snapshot(market)
        position = snapshot.positions[0]
        self.assertEqual(position.mark_mid, Decimal("2"))
        self.assertEqual(position.mark_source, "fallback_bid")
        self.assertEqual(snapshot.fallback_price_count, 1)

    def test_debit_spread_maximum_risk_is_debit_not_width(self) -> None:
        portfolio = BacktestPortfolio(Decimal("100000"), self.catalog, self.account)
        structure_id = uuid4()
        portfolio.apply_fill(Fill(
            uuid4(), uuid4(), uuid4(), "test", structure_id, self.second.timestamp,
            (LegFill(self.short, TradeSide.BUY, 1, Decimal("5.2")), LegFill(self.long, TradeSide.SELL, 1, Decimal("2"))),
            Decimal("-3.2"), 1, Decimal("1.36"), Decimal("0"), False,
        ))
        self.assertEqual(portfolio.snapshot(self.second).max_theoretical_risk, Decimal("320.0"))

    def test_risk_approves_resizes_and_rejects_naked_option(self) -> None:
        portfolio = BacktestPortfolio(Decimal("100000"), self.catalog, self.account).snapshot(self.first)
        approved, decision = RiskEngine(RiskLimits(), self.catalog).evaluate(self.intent, portfolio, self.first)
        self.assertIsNotNone(approved)
        self.assertEqual(decision.decision, RiskDecisionType.APPROVED)
        strict = RiskLimits(max_loss_per_trade=Decimal("5000"), max_risk_fraction=Decimal("0.05"))
        bigger = OpenStructureIntent("test", self.legs, 2, Decimal("0.5"), TimeInForce.DAY, "resize")
        resized, decision = RiskEngine(strict, self.catalog).evaluate(bigger, portfolio, self.first)
        self.assertEqual(decision.decision, RiskDecisionType.RESIZED)
        self.assertEqual(resized.quantity, 1)
        naked = OpenStructureIntent("test", (LegIntent(self.short, TradeSide.SELL),), 1, Decimal("1"), TimeInForce.DAY, "bad")
        rejected, decision = RiskEngine(RiskLimits(), self.catalog).evaluate(naked, portfolio, self.first)
        self.assertIsNone(rejected)
        self.assertEqual(decision.rule, "naked_option")
        greek_limited = RiskLimits(max_abs_delta=Decimal("5"))
        rejected, decision = RiskEngine(greek_limited, self.catalog).evaluate(self.intent, portfolio, self.first)
        self.assertIsNone(rejected)
        self.assertEqual(decision.rule, "projected_delta")


if __name__ == "__main__":
    unittest.main()
