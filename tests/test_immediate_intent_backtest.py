from decimal import Decimal
import unittest

from examples.backtest.governed_sma import canonical_events, fixture_bars
from kairos.application import run_immediate_target_backtest
from kairos.execution.intent_status import IntentStatus
from kairos.features import SmaFactorConfig, SmaFactorRuntime
from kairos.market_data import IterableEventSource
from kairos.strategies import GovernedStrategyRuntime, SmaCrossStrategy, SmaCrossStrategyConfig
from kairos.strategies.specs import sma_strategy_spec
from kairos.strategies.sma_cross_research_backtest import SmaCrossConfig


class ImmediateIntentBacktestTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_strategy_executes_intents_synchronously(self):
        bars = fixture_bars()
        instrument = bars[0].instrument_id
        config = SmaCrossConfig(5, 15, Decimal("100000"), Decimal("10"))
        spec, policy = sma_strategy_spec(config)

        result = await run_immediate_target_backtest(
            source=IterableEventSource(tuple(canonical_events(bars))),
            factor_runtime=SmaFactorRuntime(SmaFactorConfig(5, 15), input_identity="fixture:sma-bars-v1"),
            strategy_runtime=GovernedStrategyRuntime(
                SmaCrossStrategy(SmaCrossStrategyConfig(instrument)), spec,
                execution_policy_id=policy.policy_id,
            ),
            instrument_id=instrument, catalog=object(), initial_cash=config.initial_cash,
            fee_bps=config.fee_bps,
        )

        self.assertGreater(len(result.strategy_run.economic_intents), 0)
        self.assertGreater(len(result.trades), 0)
        self.assertGreater(result.final_portfolio.equity, 0)
        self.assertTrue(result.intent_executions)
        self.assertTrue(all(item.status is IntentStatus.SATISFIED for item in result.intent_executions))


if __name__ == "__main__":
    unittest.main()
