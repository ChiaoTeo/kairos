from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from kairos import __version__
from kairos.backtest.engine import BacktestEngine
from kairos.backtest.feed import MarketReplayDataset, MarketSnapshot, build_manifest
from kairos.backtest.synthetic_scenarios import _put, build_synthetic_backtest_dataset
from kairos.backtest.result import BacktestConfig
from kairos.domain.market_data import Quote
from kairos.pricing import PricingInput, black76
from kairos.study_platform.snapshot import InstrumentSnapshot
from kairos.risk.limits import RiskLimits
from kairos.strategies.bull_put_spread import BullPutSpreadConfig, BullPutSpreadStrategy


def internally_priceable_spxw_dataset() -> MarketReplayDataset:
    base = build_synthetic_backtest_dataset()
    underlying = base.definitions[0]
    existing = base.definitions[1:]
    catalog = base.reference_catalog()
    expiry_date = existing[0].contract_spec.expiry.date()
    options = tuple(sorted((*existing, _put(catalog, expiry_date, "6100", "105")), key=lambda item: item.contract_spec.strike))
    spot = Decimal("6000")
    slices = []
    for sequence, market in enumerate(base.slices):
        snapshots = []
        for definition in options:
            spec = definition.contract_spec
            maturity = Decimal(str((spec.expiry - market.timestamp).total_seconds())) / Decimal("31557600")
            volatility = Decimal("0.45") + (Decimal("6000") - spec.strike) / Decimal("10000")
            price = black76(PricingInput(spot, spec.strike, maturity, Decimal("0"), volatility, spec.right)).price
            bid = max(Decimal("0.01"), price - Decimal("0.05"))
            ask = price + Decimal("0.05")
            quote = Quote(definition.instrument_id, bid, ask, Decimal("100"), Decimal("100"), market.timestamp)
            snapshots.append(InstrumentSnapshot(definition.instrument_id, quote, market.timestamp, None, None, None, None))
        slices.append(MarketSnapshot(
            market.timestamp, tuple(snapshots), ((underlying.instrument_id, spot),), (), Decimal("0"),
            sequence, tuple(item.instrument_id for item in options),
        ))
    definitions = (underlying, *options)
    contracts = (*base.contracts, replace(base.contracts[0], instrument_id=options[-1].instrument_id))
    manifest = build_manifest(
        "spxw-internal-golden", tuple(slices), tuple(contracts), definitions,
        sampling_seconds=60, source="synthetic.internal-pricing-reference", market_data_type="synthetic",
        code_version=__version__, split="test", synthetic=True,
        products=catalog.products.values(), references=catalog.all_references(), settlements=catalog.settlements.values(),
    )
    return MarketReplayDataset(
        manifest, tuple(slices), tuple(contracts), definitions, catalog.products.values(),
        catalog.all_references(), catalog.settlements.values(),
    )


class OptionsStudyEndToEndTests(unittest.TestCase):
    def test_market_to_surface_strategy_backtest_and_risk_is_deterministic(self) -> None:
        dataset = internally_priceable_spxw_dataset()
        config = BacktestConfig(dataset.manifest.start, dataset.manifest.end, minimum_data_coverage=Decimal("0"))
        strategy_config = BullPutSpreadConfig(
            target_short_delta=Decimal("-0.35"), width=Decimal("50"), min_credit=Decimal("0.01"),
        )
        first = BacktestEngine(dataset, config, BullPutSpreadStrategy(strategy_config), RiskLimits()).run()
        replay = BacktestEngine(dataset, config, BullPutSpreadStrategy(strategy_config), RiskLimits()).run()
        self.assertEqual(first.run_id, replay.run_id)
        self.assertEqual(first.metrics, replay.metrics)
        self.assertTrue(first.intents)
        self.assertTrue(first.fills)
        self.assertTrue(any("delta_source=internal_surface" in item.reason for item in first.strategy_decisions))
        self.assertEqual(first.metrics["internal_valuation_coverage"], Decimal("1"))
        self.assertEqual(first.metrics["surface_calibration_rate"], Decimal("1"))
        self.assertGreater(first.metrics["scenario_observations"], 0)


if __name__ == "__main__":
    unittest.main()
