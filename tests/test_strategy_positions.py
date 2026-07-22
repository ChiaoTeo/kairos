from decimal import Decimal
import unittest

from kairospy.identity import InstrumentId
from kairospy.risk.strategy_positions import StrategyPositionBook


class StrategyPositionBookTest(unittest.TestCase):
    def test_virtual_positions_net_without_losing_strategy_ownership(self):
        book=StrategyPositionBook();btc=InstrumentId("BTC")
        book.apply("trend",btc,Decimal("2"));book.apply("carry",btc,Decimal("-1"))
        net=book.netted_positions()[0]
        self.assertEqual(net.account_quantity,1);self.assertEqual(len(net.allocations),2)
        self.assertEqual(book.reconcile({btc:Decimal("1")}),())
        self.assertTrue(book.reconcile({btc:Decimal("2")}))


if __name__=="__main__":unittest.main()
