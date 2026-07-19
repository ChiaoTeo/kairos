from __future__ import annotations

from decimal import Decimal
import unittest

from examples.backtest.governed_sma import canonical_events, fixture_bars
from trading.application import GovernedStrategyRunLoop
from trading.features import SmaFactorConfig, SmaFactorRuntime
from trading.market_data import IterableEventSource
from trading.strategies import GovernedStrategyRuntime, SmaCrossStrategy, SmaCrossStrategyConfig, StrategyContext
from trading.strategies.specs import builtin_strategy_specs


class StrategyRunLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_events_produce_deterministic_factor_decision_and_intent_hashes(self) -> None:
        bars = fixture_bars()
        events = tuple(canonical_events(bars))
        instrument = bars[0].instrument_id

        async def run_once():
            spec, policy = next(item for item in builtin_strategy_specs() if item[0].strategy_id == "sma-cross-v1")
            strategy = SmaCrossStrategy(SmaCrossStrategyConfig(instrument))
            runtime = GovernedStrategyRuntime(strategy, spec, execution_policy_id=policy.policy_id)
            return await GovernedStrategyRunLoop(
                IterableEventSource(events),
                SmaFactorRuntime(SmaFactorConfig(5, 15), input_identity="fixture:sma-bars-v1"),
                runtime,
                lambda market: StrategyContext(market, object(), (), object()),
                approved_capital=Decimal("10000"),
            ).run()

        first = await run_once()
        second = await run_once()

        self.assertEqual(first, second)
        self.assertEqual(len(first.factor_snapshots), len(bars))
        self.assertGreater(len(first.economic_intents), 0)
        self.assertTrue(all(len(value) == 64 for value in (
            first.factor_hash, first.decision_hash, first.intent_hash, first.audit_hash,
        )))


if __name__ == "__main__":
    unittest.main()
