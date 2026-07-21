from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
import unittest

from kairospy.ports import OrderRequest
from kairospy.trading.capability import OrderType, TimeInForce
from kairospy.trading.execution import TradeSide
from kairospy.trading.identity import AccountKey, AccountType, InstitutionId, InstrumentId
from kairospy.trading.intent import TargetExposureIntent, TargetPositionIntent
from kairospy.trading.order import ExecutionInstructions
from kairospy.execution.intent_status import IntentExecutionTracker, IntentStatus, intent_scope
from kairospy.execution.strategy_planner import plan_strategy_intent
from kairospy.strategy.protocols import StrategyContext


NOW = datetime(2026, 7, 19, tzinfo=timezone.utc)
INSTRUMENT = InstrumentId("BTC")
ACCOUNT = AccountKey(InstitutionId("sim"), "primary", AccountType.CRYPTO_SPOT)
INSTRUCTIONS = {INSTRUMENT: ExecutionInstructions(OrderType.MARKET, TimeInForce.IOC)}


class IntentExecutionTests(unittest.TestCase):
    def test_new_target_replaces_the_current_view_for_the_same_scope(self):
        tracker = IntentExecutionTracker()
        first = TargetExposureIntent(uuid4(), "sma", INSTRUMENT, Decimal("1"), "long")
        second = TargetExposureIntent(uuid4(), "sma", INSTRUMENT, Decimal("0"), "flat")
        tracker.publish(first)
        tracker.publish(second)

        self.assertEqual(len(tracker.views), 1)
        self.assertEqual(tracker.active(intent_scope(first)).intent_id, second.intent_id)

    def test_strategy_context_exposes_normalized_intent_progress(self):
        intent = TargetPositionIntent(uuid4(), "sma", INSTRUMENT, Decimal("10"), "cross")
        tracker = IntentExecutionTracker()
        tracker.publish(intent)
        view = tracker.refresh_target(
            intent, current_quantity=Decimal("4"), working_quantity=Decimal("3"),
            filled_quantity=Decimal("4"), attempt_count=1, last_attempt_at=NOW,
        )
        context = StrategyContext(object(), object(), (), object(), intent_executions=tracker.views)

        self.assertEqual(view.status, IntentStatus.PARTIALLY_SATISFIED)
        self.assertEqual(view.remaining_quantity, Decimal("3"))
        self.assertEqual(context.intent_execution(intent.intent_id), view)
        self.assertEqual(context.active_intent(intent_scope(intent)), view)

    def test_target_planner_subtracts_same_strategy_working_orders(self):
        intent = TargetPositionIntent(uuid4(), "sma", INSTRUMENT, Decimal("10"), "cross")
        working = OrderRequest(
            "internal", "client", "sma", "old-intent", "correlation", ACCOUNT,
            INSTRUMENT, TradeSide.BUY, Decimal("6"), INSTRUCTIONS[INSTRUMENT],
        )
        plan = plan_strategy_intent(
            intent, accounts={INSTRUMENT: ACCOUNT}, current_positions={INSTRUMENT: Decimal("4")},
            instructions=INSTRUCTIONS, working_orders=(working,),
        )
        self.assertEqual(plan.orders, ())

    def test_new_attempt_gets_a_new_idempotency_key(self):
        intent = TargetPositionIntent(uuid4(), "sma", INSTRUMENT, Decimal("10"), "cross")
        first = plan_strategy_intent(
            intent, accounts={INSTRUMENT: ACCOUNT}, current_positions={},
            instructions=INSTRUCTIONS, attempt=1,
        ).orders[0]
        second = plan_strategy_intent(
            intent, accounts={INSTRUMENT: ACCOUNT}, current_positions={INSTRUMENT: Decimal("4")},
            instructions=INSTRUCTIONS, attempt=2,
        ).orders[0]

        self.assertNotEqual(first.client_order_id, second.client_order_id)
        self.assertEqual(first.quantity, Decimal("10"))
        self.assertEqual(second.quantity, Decimal("6"))


if __name__ == "__main__":
    unittest.main()
