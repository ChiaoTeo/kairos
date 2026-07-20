from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from kairos.backtest.engine import BacktestEngine
from kairos.backtest.synthetic_scenarios import build_synthetic_backtest_dataset
from kairos.backtest.result import BacktestConfig
from kairos.pricing import SolverStatus, OptionValuationService
from kairos.research.snapshot import InstrumentSnapshot
from kairos.risk.limits import RiskLimits
from kairos.research.features import FeatureEngine, build_features
from kairos.strategies.bull_put_spread import BullPutSpreadStrategy


class InternalValuationTests(unittest.TestCase):
    def test_missing_vendor_greeks_are_replaced_by_internal_values(self) -> None:
        dataset = build_synthetic_backtest_dataset()
        catalog = dataset.reference_catalog()
        raw = dataset.slices[0]
        without_vendor = replace(
            raw,
            instruments=tuple(replace(item, greeks=None, greeks_time=None) for item in raw.instruments),
            reference_prices=((dataset.definitions[0].instrument_id, raw.reference_prices[0][1]),),
        )
        # Use an arbitrage-consistent spot for this valuation contract test.
        without_vendor = replace(without_vendor, reference_prices=((raw.reference_prices[0][0], raw.reference_prices[0][1] + 50),))
        valued, snapshot = OptionValuationService(catalog).value(without_vendor)
        self.assertTrue(all(item.greeks is not None for item in valued.instruments))
        self.assertTrue(all(item.implied_vol.status is SolverStatus.CONVERGED for item in snapshot.instruments))
        self.assertIsNotNone(snapshot.surface)

    def test_static_arbitrage_price_is_rejected_with_diagnostic(self) -> None:
        dataset = build_synthetic_backtest_dataset()
        catalog = dataset.reference_catalog()
        market = replace(dataset.slices[0], instruments=tuple(replace(item, greeks=None, greeks_time=None) for item in dataset.slices[0].instruments))
        valued, snapshot = OptionValuationService(catalog).value(market)
        self.assertTrue(any("price_out_of_bounds" in failure for failure in snapshot.failures))
        self.assertTrue(any(item.greeks is None for item in valued.instruments))

    def test_feature_engine_uses_only_accumulated_history(self) -> None:
        dataset = build_synthetic_backtest_dataset()
        catalog = dataset.reference_catalog()
        valuation_engine, features = OptionValuationService(catalog), FeatureEngine()
        first = features.update(valuation_engine.value(dataset.slices[0])[1])
        second = features.update(valuation_engine.value(dataset.slices[1])[1])
        self.assertEqual(first.iv_rank, Decimal("0.5"))
        self.assertIsNotNone(second.iv_percentile)
        self.assertIsNotNone(first.put_skew)

    def test_offline_and_incremental_feature_paths_have_exact_parity(self) -> None:
        dataset = build_synthetic_backtest_dataset(); catalog = dataset.reference_catalog()
        valuations = tuple(OptionValuationService(catalog).value(item)[1] for item in dataset.slices)
        engine = FeatureEngine(); online = tuple(engine.update(item) for item in valuations)
        history, offline = [], []
        for valuation in valuations:
            value = build_features(valuation, tuple(history)); offline.append(value)
            if value.average_implied_vol is not None:
                history.append(value.average_implied_vol)
        self.assertEqual(tuple(offline), online)

    def test_strategy_backtest_runs_without_vendor_greeks(self) -> None:
        dataset = build_synthetic_backtest_dataset()
        slices = tuple(replace(market, instruments=tuple(replace(item, greeks=None, greeks_time=None) for item in market.instruments)) for market in dataset.slices)
        dataset = replace(dataset, slices=slices)
        config = BacktestConfig(dataset.manifest.start, dataset.manifest.end, minimum_data_coverage=0)
        result = BacktestEngine(dataset, config, BullPutSpreadStrategy(), RiskLimits()).run()
        self.assertTrue(result.intents)
        self.assertTrue(any(decision.action == "open" for decision in result.strategy_decisions))
        self.assertTrue(any("delta_source=internal" in decision.reason for decision in result.strategy_decisions))
        self.assertGreater(result.metrics["scenario_observations"], 0)
        self.assertIn("scenario_expected_shortfall_95", result.metrics)
        self.assertGreater(result.metrics["internal_valuation_coverage"], 0)
        self.assertIn("surface_calibration_rate", result.metrics)


if __name__ == "__main__":
    unittest.main()
