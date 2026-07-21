from __future__ import annotations

from kairospy.trading.identity import InstitutionId

import unittest
from decimal import Decimal
from uuid import uuid4

from kairospy.backtest.synthetic_scenarios import SyntheticScenario, build_synthetic_backtest_dataset
from kairospy.backtest.portfolio import BacktestPortfolio
from kairospy.backtest.settlement import due_settlements, intrinsic_value
from kairospy.trading.execution import TradeSide
from kairospy.trading.identity import AccountKey, AccountType, VenueId
from kairospy.trading.order import Fill, LegFill
from kairospy.trading.product import ListedOptionSpec, OptionRight


class SettlementTests(unittest.TestCase):
    def test_intrinsic_value_for_call_and_put(self) -> None:
        self.assertEqual(intrinsic_value(OptionRight.CALL, Decimal("100"), Decimal("110")), Decimal("10"))
        self.assertEqual(intrinsic_value(OptionRight.PUT, Decimal("100"), Decimal("90")), Decimal("10"))
        self.assertEqual(intrinsic_value(OptionRight.PUT, Decimal("100"), Decimal("110")), Decimal("0"))

    def test_spread_settlement_scenarios_are_hand_reconcilable(self) -> None:
        expectations = {
            SyntheticScenario.EXPIRY_ALL_OTM: Decimal("100278.64"),
            SyntheticScenario.EXPIRY_SHORT_ITM: Decimal("97778.64"),
            SyntheticScenario.EXPIRY_BOTH_ITM: Decimal("95278.64"),
        }
        for scenario, expected_cash in expectations.items():
            with self.subTest(scenario=scenario):
                dataset = build_synthetic_backtest_dataset(scenario)
                catalog = dataset.reference_catalog()
                by_strike = {definition.contract_spec.strike: definition.instrument_id for definition in dataset.definitions if isinstance(definition.contract_spec, ListedOptionSpec)}
                short, long = by_strike[Decimal("6000")], by_strike[Decimal("5950")]
                portfolio = BacktestPortfolio(Decimal("100000"), catalog, AccountKey(InstitutionId("backtest"), scenario.value, AccountType.SECURITIES_MARGIN))
                structure_id = uuid4()
                fill = Fill(
                    uuid4(), uuid4(), uuid4(), "test", structure_id, dataset.slices[1].timestamp,
                    (LegFill(short, TradeSide.SELL, 1, Decimal("5")), LegFill(long, TradeSide.BUY, 1, Decimal("2.2"))),
                    Decimal("2.8"), 1, Decimal("1.36"), Decimal("0"), False,
                )
                portfolio.apply_fill(fill)
                settlements = due_settlements(portfolio, dataset.contracts, dataset.slices[-1].timestamp)
                self.assertEqual(len(settlements), 2)
                for settlement in settlements:
                    portfolio.apply_settlement(settlement)
                self.assertEqual(portfolio.cash, expected_cash)
                self.assertFalse(portfolio.structures)
                self.assertTrue(all(position.quantity == 0 for position in portfolio.positions.values()))

    def test_missing_official_settlement_fails(self) -> None:
        dataset = build_synthetic_backtest_dataset(SyntheticScenario.EXPIRY_ALL_OTM)
        # The explicit missing-settlement failure is exercised by removing the official value.
        from dataclasses import replace
        contracts = tuple(replace(item, official_settlement=None) for item in dataset.contracts)
        catalog = dataset.reference_catalog()
        by_strike = {definition.contract_spec.strike: definition.instrument_id for definition in dataset.definitions if isinstance(definition.contract_spec, ListedOptionSpec)}
        short, long = by_strike[Decimal("6000")], by_strike[Decimal("5950")]
        portfolio = BacktestPortfolio(Decimal("100000"), catalog, AccountKey(InstitutionId("backtest"), "missing", AccountType.SECURITIES_MARGIN))
        structure_id = uuid4()
        portfolio.apply_fill(Fill(uuid4(), uuid4(), uuid4(), "test", structure_id, dataset.slices[1].timestamp, (LegFill(short, TradeSide.SELL, 1, Decimal("5")), LegFill(long, TradeSide.BUY, 1, Decimal("2.2"))), Decimal("2.8"), 1, Decimal("1"), Decimal("0"), False))
        with self.assertRaisesRegex(ValueError, "missing official settlement"):
            due_settlements(portfolio, contracts, dataset.slices[-1].timestamp)
        unconfirmed = tuple(replace(item, settlement_confirmed=False) for item in dataset.contracts)
        with self.assertRaisesRegex(ValueError, "unconfirmed settlement metadata"):
            due_settlements(portfolio, unconfirmed, dataset.slices[-1].timestamp)


if __name__ == "__main__":
    unittest.main()
