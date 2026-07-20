from __future__ import annotations

from datetime import datetime,timezone
from decimal import Decimal
import unittest

from kairos.backtest.feed import MarketSnapshot
from kairos.domain.identity import InstrumentId
from kairos.domain.market_data import Quote
from kairos.study_platform.snapshot import InstrumentSnapshot
from kairos.strategies import GovernedStrategyRuntime,StrategyContext
from kairos.strategies.cash_and_carry import CashAndCarryConfig,CashAndCarryStrategy
from kairos.strategies.covered_call import CoveredCallStrategy
from kairos.strategies.protective_put import ProtectivePutStrategy
from kairos.strategies.specs import builtin_strategy_specs


NOW=datetime(2026,7,17,tzinfo=timezone.utc)
EQUITY=InstrumentId("equity:aapl");CALL=InstrumentId("option:aapl:call");PUT=InstrumentId("option:aapl:put")
SPOT=InstrumentId("crypto:spot:btc");PERP=InstrumentId("crypto:perp:btc")


def context(instruments,positions=()):
    snapshots=tuple(InstrumentSnapshot(i,Quote(i,Decimal(b),Decimal(a),Decimal("10"),Decimal("10"),NOW),NOW,None,None,None,None)
        for i,b,a in instruments)
    return StrategyContext(MarketSnapshot(NOW,snapshots,sequence=1),object(),(),object(),approved_capital=Decimal("10000"),
        strategy_positions=positions)


def runtime(strategy):
    spec,policy=next(item for item in builtin_strategy_specs() if item[0].strategy_id==strategy.strategy_id)
    return GovernedStrategyRuntime(strategy,spec,execution_policy_id=policy.policy_id)


class MultiAssetStrategyRuntimeTests(unittest.TestCase):
    def test_covered_and_protective_models_use_formal_strategy_protocol(self):
        covered=runtime(CoveredCallStrategy(EQUITY,CALL));first=covered.on_market(context(((EQUITY,"99","101"),(CALL,"2","2.1"))))
        second=covered.on_market(context(((EQUITY,"99","101"),(CALL,"2","2.1")),((EQUITY,Decimal("100")),)))
        self.assertEqual(type(first.intents[0]).__name__,"TargetPositionIntent")
        self.assertEqual(type(second.intents[0]).__name__,"CoveredCallIntent")
        protective=runtime(ProtectivePutStrategy(EQUITY,PUT));value=protective.on_market(
            context(((EQUITY,"99","101"),(PUT,"1","1.1")),((EQUITY,Decimal("100")),)))
        self.assertEqual(type(value.intents[0]).__name__,"ProtectivePutIntent")

    def test_carry_model_consumes_market_context_and_emits_economic_intent(self):
        strategy=CashAndCarryStrategy(SPOT,PERP,CashAndCarryConfig(minimum_annualized_basis=Decimal("0.001")))
        intent=runtime(strategy).on_market(context(((SPOT,"49999","50001"),(PERP,"50100","50102"))))
        self.assertEqual(type(intent.intents[0]).__name__,"CashAndCarryIntent")
        self.assertEqual(intent.strategy_id,"spot-perpetual-carry-v1")


if __name__=="__main__":unittest.main()
