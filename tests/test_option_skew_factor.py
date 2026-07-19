from __future__ import annotations

import unittest
from decimal import Decimal

from trading.backtest.mock import make_mock_dataset
from trading.features import FactorQuality,OptionSkewFactorConfig,OptionSkewFactorRuntime
from trading.pricing import ValuationService
from trading.backtest.engine import BacktestEngine
from trading.backtest.result import BacktestConfig
from trading.strategies.bull_put_spread import BullPutSpreadConfig,BullPutSpreadStrategy


class OptionSkewFactorTests(unittest.TestCase):
    def test_point_in_time_skew_rank_has_explicit_warmup_and_state_restore(self):
        dataset=make_mock_dataset();catalog=dataset.reference_catalog();valuation=ValuationService(catalog,max_quote_age_seconds=120)
        runtime=OptionSkewFactorRuntime(catalog,OptionSkewFactorConfig(minimum_rank_history=1),input_identity=dataset.manifest.dataset_id)
        values=[]
        for market in dataset.slices:
            valued,snapshot=valuation.value(market);values.append(runtime.update_market(valued,snapshot))
        self.assertEqual(values[0].quality,FactorQuality.WARMING_UP);self.assertIsNone(values[0].get("skew_rank"))
        self.assertEqual(values[1].quality,FactorQuality.READY);self.assertIsNotNone(values[1].get("skew_rank"))
        restored=OptionSkewFactorRuntime(catalog,OptionSkewFactorConfig(minimum_rank_history=1),input_identity=dataset.manifest.dataset_id)
        restored.restore(runtime.dump_state());self.assertEqual(restored.dump_state(),runtime.dump_state())

    def test_formal_bull_put_strategy_consumes_governed_skew_rank(self):
        dataset=make_mock_dataset();catalog=dataset.reference_catalog()
        factor=OptionSkewFactorRuntime(catalog,OptionSkewFactorConfig(minimum_rank_history=0),input_identity=dataset.manifest.dataset_id)
        config=BullPutSpreadConfig(signal_factor_id="spxw-put-skew",minimum_skew_rank=Decimal("0.5"))
        result=BacktestEngine(dataset,BacktestConfig(dataset.manifest.start,dataset.manifest.end),
            BullPutSpreadStrategy(config),factor_runtimes=(factor,)).run()
        self.assertTrue(result.intents);self.assertTrue(any(item.action=="open" for item in result.strategy_decisions))


if __name__=="__main__":unittest.main()
