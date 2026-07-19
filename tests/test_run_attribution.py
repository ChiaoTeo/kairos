from __future__ import annotations

import asyncio
from decimal import Decimal
import unittest

from trading.application import build_run_attribution
from trading.product_workflow import _governed_run,fixture_sma_bars


class RunAttributionTests(unittest.TestCase):
    def test_signal_portfolio_and_execution_layers_are_separate(self):
        result=asyncio.run(_governed_run("fixture",fixture_sma_bars(),5,15,Decimal("100000")))
        value=build_run_attribution(result,starting_equity=Decimal("100000"),ending_equity=Decimal("101000"),
            orders=5,fills=4,fees=Decimal("12"),slippage=Decimal("3"))
        self.assertEqual(value.portfolio.total_pnl,Decimal("1000"));self.assertEqual(value.execution.fees,Decimal("12"))
        self.assertEqual(value.signal.economic_intents,len(result.economic_intents));self.assertTrue(value.limitations)


if __name__=="__main__":unittest.main()
