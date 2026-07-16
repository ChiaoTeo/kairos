import unittest

from trading.domain.strategy_contract import StrategyLifecycle
from trading.strategies.promotion import evaluate_promotion_artifacts


class StrategyPromotionGateTest(unittest.TestCase):
    def test_l3_proxy_cannot_promote_to_live_but_supported_signal_can_promote_research(self):
        proxy={"state":{"maximum_level":3,"signal_status":"EXPLORATORY","strategy_status":"TRADE_PROXY_ONLY"},"out_of_sample":"time_oos"}
        self.assertFalse(evaluate_promotion_artifacts(StrategyLifecycle.LIVE_LIMITED,(proxy,)).passed)
        signal={"state":{"maximum_level":2,"signal_status":"SUPPORTED"}}
        self.assertTrue(evaluate_promotion_artifacts(StrategyLifecycle.RESEARCH_VALIDATED,(signal,)).passed)


if __name__=="__main__":unittest.main()
