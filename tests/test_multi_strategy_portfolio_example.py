import unittest

from examples.strategy.multi_strategy_portfolio import run


class MultiStrategyPortfolioExampleTests(unittest.TestCase):
    def test_allocation_virtual_ownership_and_netting(self):
        result=run();self.assertEqual(result["account_net_quantity"],"75")
        self.assertTrue(result["virtual_ownership_preserved"]);self.assertEqual(len(result["strategy_positions"]),2)
        self.assertEqual(result["allocation_decisions"],["resized","resized"])


if __name__=="__main__":unittest.main()
