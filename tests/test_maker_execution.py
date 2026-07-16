from datetime import datetime,timedelta,timezone
from decimal import Decimal
from uuid import uuid4
import unittest

from trading.backtest.maker import (
    BookEventType,FifoMakerFillModel,HybridAction,HybridExecutionStateMachine,
    IncrementalBookEvent,MakerOrderState,
)
from trading.domain.capability import TimeInForce
from trading.domain.execution import TradeSide
from trading.domain.identity import InstrumentId
from trading.execution.policy import ExecutionMode,ExecutionPolicy,PartialFillPolicy


class MakerExecutionTest(unittest.TestCase):
    def test_fifo_model_does_not_fill_on_touch_and_consumes_queue_first(self):
        now=datetime.now(timezone.utc);instrument=InstrumentId("BTC")
        order=MakerOrderState(uuid4(),instrument,TradeSide.BUY,Decimal("100"),Decimal("2"),now,now,Decimal("3"))
        model=FifoMakerFillModel()
        first=model.apply(order,IncrementalBookEvent(1,now,instrument,TradeSide.BUY,Decimal("100"),Decimal("2"),BookEventType.TRADE))
        self.assertEqual(first.fill_quantity,0);self.assertEqual(first.order.queue_ahead,1)
        second=model.apply(first.order,IncrementalBookEvent(2,now,instrument,TradeSide.BUY,Decimal("100"),Decimal("2"),BookEventType.TRADE))
        self.assertEqual(second.fill_quantity,1);self.assertEqual(second.order.filled_quantity,1)

    def test_hybrid_crosses_only_after_timeout(self):
        now=datetime.now(timezone.utc);instrument=InstrumentId("BTC")
        order=MakerOrderState(uuid4(),instrument,TradeSide.BUY,Decimal("100"),Decimal("2"),now,now,Decimal("0"),Decimal("1"))
        policy=ExecutionPolicy("hybrid","1",ExecutionMode.HYBRID,TimeInForce.GTC,Decimal("10"),maker_timeout_ms=2000,
            queue_model="fifo",partial_fill_policy=PartialFillPolicy.CROSS_REMAINDER)
        machine=HybridExecutionStateMachine()
        self.assertEqual(machine.decide(order,now+timedelta(seconds=1),policy).action,HybridAction.WAIT)
        decision=machine.decide(order,now+timedelta(seconds=3),policy)
        self.assertEqual(decision.action,HybridAction.CROSS_REMAINDER);self.assertEqual(decision.remaining_quantity,1)


if __name__=="__main__":unittest.main()
