from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
import unittest

from kairospy.execution.intent_coordinator import IntentCoordinator
from kairospy.execution.ports import OrderAck, OrderRequest
from kairospy.execution.command import OrderCommand, OutboxRecord, OutboxStatus
from kairospy.execution.events import TradeExecution, TradeSide
from kairospy.execution.fills import Fill as BacktestFill, LegFill
from kairospy.execution.order_state import DurableOrderRecord, DurableOrderStatus
from kairospy.execution.orders import Order, OrderLeg, OrderStatus, OrderType, TimeInForce
from kairospy.identity import AccountRef, AccountType, AssetId, InstitutionId, InstrumentId
from kairospy.strategy.intents import TargetExposureIntent, TargetPositionIntent
from kairospy.execution.orders import ExecutionInstructions
from kairospy.execution.intent_status import IntentExecutionTracker, IntentStatus, intent_scope
from kairospy.reference.contracts import ProductType
from kairospy.strategy.contracts import StrategyLifecycle, StrategySpec
from kairospy.execution.strategy_planner import plan_strategy_intent
from kairospy.strategy.protocols import Context
from kairospy.strategy.runtime import GovernedStrategyRuntime, StrategyRuntime
from kairospy.strategy.views import BudgetView, IntentView, MarketView, OrderView, PortfolioView


NOW = datetime(2026, 7, 19, tzinfo=timezone.utc)
INSTRUMENT = InstrumentId("BTC")
ACCOUNT = AccountRef(InstitutionId("sim"), "primary", AccountType.CRYPTO_SPOT)
INSTRUCTIONS = {INSTRUMENT: ExecutionInstructions(OrderType.MARKET, TimeInForce.IOC)}


class IntentExecutionTests(unittest.TestCase):
    def test_strategy_runtime_is_the_single_hook_adapter_for_intents(self):
        strategy = _StaticIntentStrategy()
        runtime = StrategyRuntime(strategy)
        context = _context()

        self.assertEqual(runtime.intents_on_start(context), ())
        self.assertEqual(runtime.intents_on_market(context), (strategy.intent,))
        self.assertEqual(runtime.intents_on_fill(SimpleNamespace(), context), ())
        self.assertEqual(runtime.intents_on_end(context), ())

    def test_intent_coordinator_publishes_strategy_intents_idempotently(self):
        strategy = _StaticIntentStrategy()
        runtime = GovernedStrategyRuntime(strategy, _strategy_spec(strategy.strategy_id), execution_policy_id="test-policy")
        tracker = IntentExecutionTracker()
        coordinator = IntentCoordinator(runtime, tracker)
        context = Context(
            MarketView(NOW, 1, (INSTRUMENT,), available_instruments=(INSTRUMENT,)),
            PortfolioView(timestamp=NOW),
            budget=BudgetView(approved_capital=Decimal("1000")),
        )

        first = coordinator.publish(runtime.intents_on_market(context), context)
        second = coordinator.publish(runtime.intents_on_market(context), context)

        self.assertIsNotNone(first)
        assert first is not None
        self.assertIsNone(second)
        self.assertEqual(first.intents, (strategy.intent,))
        self.assertEqual(len(coordinator.views), 1)
        self.assertEqual(coordinator.execution(strategy.intent.intent_id).status, IntentStatus.PENDING)

    def test_intent_coordinator_owns_execution_progress_updates(self):
        strategy = _StaticIntentStrategy()
        runtime = GovernedStrategyRuntime(strategy, _strategy_spec(strategy.strategy_id), execution_policy_id="test-policy")
        coordinator = IntentCoordinator(runtime)
        context = Context(
            MarketView(NOW, 1, (INSTRUMENT,), available_instruments=(INSTRUMENT,)),
            PortfolioView(timestamp=NOW),
            budget=BudgetView(approved_capital=Decimal("1000")),
        )
        coordinator.publish(runtime.intents_on_market(context), context)

        updated = coordinator.mark_satisfied(strategy.intent, filled_quantity=Decimal("1"))

        self.assertEqual(updated.status, IntentStatus.SATISFIED)
        self.assertEqual(coordinator.execution(strategy.intent.intent_id), updated)

    def test_intent_coordinator_marks_blocked_progress_with_reason(self):
        strategy = _StaticIntentStrategy()
        runtime = GovernedStrategyRuntime(strategy, _strategy_spec(strategy.strategy_id), execution_policy_id="test-policy")
        coordinator = IntentCoordinator(runtime)
        coordinator.publish(runtime.intents_on_market(_context()), _context())

        blocked = coordinator.mark_blocked(strategy.intent, reason="approved capital is not positive")

        self.assertEqual(blocked.status, IntentStatus.BLOCKED)
        self.assertEqual(blocked.last_error, "approved capital is not positive")
        self.assertEqual(coordinator.execution(strategy.intent.intent_id), blocked)
        with self.assertRaises(ValueError):
            coordinator.mark_blocked(strategy.intent, reason=" ")

    def test_intent_coordinator_can_track_progress_without_governed_runtime(self):
        strategy = _StaticIntentStrategy()
        coordinator = IntentCoordinator()

        published = coordinator.publish_progress((strategy.intent,))

        self.assertEqual(len(published), 1)
        self.assertEqual(published[0].status, IntentStatus.PENDING)
        with self.assertRaisesRegex(ValueError, "strategy runtime"):
            coordinator.publish((strategy.intent,), _context())

    def test_new_target_replaces_the_current_view_for_the_same_scope(self):
        tracker = IntentExecutionTracker()
        first = TargetExposureIntent(uuid4(), "sma", INSTRUMENT, Decimal("1"), "long")
        second = TargetExposureIntent(uuid4(), "sma", INSTRUMENT, Decimal("0"), "flat")
        tracker.publish(first)
        tracker.publish(second)

        self.assertEqual(len(tracker.views), 1)
        self.assertEqual(tracker.active(intent_scope(first)).intent_id, second.intent_id)

    def test_context_exposes_normalized_intent_progress_view(self):
        intent = TargetPositionIntent(uuid4(), "sma", INSTRUMENT, Decimal("10"), "cross")
        tracker = IntentExecutionTracker()
        tracker.publish(intent)
        view = tracker.refresh_target(
            intent, current_quantity=Decimal("4"), working_quantity=Decimal("3"),
            filled_quantity=Decimal("4"), attempt_count=1, last_attempt_at=NOW,
        )
        context = Context(
            MarketView(NOW, 1, (INSTRUMENT,), available_instruments=(INSTRUMENT,)),
            PortfolioView(timestamp=NOW),
            intents=IntentView.from_executions(tracker.views),
        )
        progress = context.intent_execution(intent.intent_id)
        active = context.active_intent(intent_scope(intent))

        self.assertEqual(view.status, IntentStatus.PARTIALLY_SATISFIED)
        self.assertEqual(view.remaining_quantity, Decimal("3"))
        self.assertIsNot(progress, view)
        self.assertEqual(progress.intent_id, view.intent_id)
        self.assertEqual(progress.status, IntentStatus.PARTIALLY_SATISFIED.value)
        self.assertEqual(progress.remaining_quantity, Decimal("3"))
        self.assertEqual(active, progress)

    def test_context_views_project_durable_execution_evidence_without_services(self):
        intent = TargetPositionIntent(uuid4(), "sma", INSTRUMENT, Decimal("10"), "cross")
        request = OrderRequest(
            "internal-1", "client-1", "sma", str(intent.intent_id), "correlation-1", ACCOUNT,
            INSTRUMENT, TradeSide.BUY, Decimal("4"), INSTRUCTIONS[INSTRUMENT],
        )
        ack = OrderAck(
            request.internal_order_id, request.client_order_id, request.strategy_id, request.intent_id,
            request.correlation_id, "venue-1", NOW,
        )
        order = DurableOrderRecord(request, DurableOrderStatus.PARTIALLY_FILLED, NOW, NOW, ack)
        command = OutboxRecord(
            OrderCommand("submit:client-1", request, NOW),
            OutboxStatus.COMPLETED,
            NOW,
            attempts=1,
        )
        execution = TradeExecution(
            uuid4(), NOW, ACCOUNT, INSTRUMENT, TradeSide.BUY, Decimal("2"),
            Decimal("10"), AssetId("USD"), Decimal("0"), request.client_order_id,
        )
        execution_record = SimpleNamespace(order=order, execution=execution, occurred_at=execution.timestamp)

        order_view = OrderView.from_execution_state(orders=(order,), outbox_records=(command,))
        intent_view = IntentView.from_executions(
            (), orders=(order,), outbox_records=(command,), execution_records=(execution_record,),
        )

        order_summary = order_view.working[0]
        progress = intent_view.executions[0]
        self.assertEqual(order_view.commands[0].command_id, "submit:client-1")
        self.assertEqual(order_summary.client_order_id, "client-1")
        self.assertEqual(order_summary.venue_order_id, "venue-1")
        self.assertEqual(order_summary.command_status, OutboxStatus.COMPLETED.value)
        self.assertEqual(order_summary.status, DurableOrderStatus.PARTIALLY_FILLED.value)
        self.assertNotEqual(order_view.state_hash, "none")
        self.assertEqual(progress.intent_id, str(intent.intent_id))
        self.assertEqual(progress.command_ids, ("submit:client-1",))
        self.assertEqual(progress.order_states, (("client-1", DurableOrderStatus.PARTIALLY_FILLED.value),))
        self.assertEqual(progress.filled_quantity, Decimal("2"))
        self.assertEqual(progress.execution_event_count, 1)
        self.assertEqual(progress.status, IntentStatus.PARTIALLY_SATISFIED.value)
        self.assertNotEqual(intent_view.state_hash, "none")

    def test_intent_coordinator_projects_durable_execution_evidence(self):
        strategy = _StaticIntentStrategy()
        runtime = GovernedStrategyRuntime(strategy, _strategy_spec(strategy.strategy_id), execution_policy_id="test-policy")
        coordinator = IntentCoordinator(runtime)
        coordinator.publish(runtime.intents_on_market(_context()), _context())
        request = OrderRequest(
            "internal-1", "client-1", strategy.strategy_id, str(strategy.intent.intent_id), "correlation-1", ACCOUNT,
            INSTRUMENT, TradeSide.BUY, Decimal("1"), INSTRUCTIONS[INSTRUMENT],
        )
        command = OutboxRecord(
            OrderCommand("submit:client-1", request, NOW),
            OutboxStatus.COMPLETED,
            NOW,
            attempts=1,
        )
        order = DurableOrderRecord(request, DurableOrderStatus.FILLED, NOW, NOW)
        execution = TradeExecution(
            uuid4(), NOW, ACCOUNT, INSTRUMENT, TradeSide.BUY, Decimal("1"),
            Decimal("10"), AssetId("USD"), Decimal("0"), request.client_order_id,
        )
        execution_record = SimpleNamespace(order=order, execution=execution, occurred_at=execution.timestamp)

        intent_view = coordinator.intent_view(
            orders=(order,),
            outbox_records=(command,),
            execution_records=(execution_record,),
        )

        progress = intent_view.execution(strategy.intent.intent_id)
        assert progress is not None
        self.assertEqual(progress.command_ids, ("submit:client-1",))
        self.assertEqual(progress.order_states, (("client-1", DurableOrderStatus.FILLED.value),))
        self.assertEqual(progress.filled_quantity, Decimal("1"))
        self.assertEqual(progress.execution_event_count, 1)

    def test_intent_view_projects_backtest_order_and_fill_evidence(self):
        strategy = _StaticIntentStrategy()
        order_id = uuid4()
        structure_id = uuid4()
        order = Order(
            order_id,
            strategy.intent.intent_id,
            strategy.strategy_id,
            structure_id,
            (OrderLeg(INSTRUMENT, TradeSide.BUY),),
            3,
            None,
            TimeInForce.DAY,
            NOW,
            NOW,
            NOW,
            False,
            OrderStatus.PARTIALLY_FILLED,
            filled_quantity=1,
        )
        fill = BacktestFill(
            uuid4(),
            order_id,
            strategy.intent.intent_id,
            strategy.strategy_id,
            structure_id,
            NOW,
            (LegFill(INSTRUMENT, TradeSide.BUY, 1, Decimal("10")),),
            Decimal("10"),
            1,
            Decimal("0"),
            Decimal("0"),
            False,
        )
        coordinator = IntentCoordinator()
        coordinator.publish_progress((strategy.intent,))

        intent_view = coordinator.intent_view(orders=(order,), execution_records=(fill,))

        progress = intent_view.execution(strategy.intent.intent_id)
        assert progress is not None
        self.assertEqual(progress.status, IntentStatus.PARTIALLY_SATISFIED.value)
        self.assertEqual(progress.order_states, ((str(order_id), OrderStatus.PARTIALLY_FILLED.value),))
        self.assertEqual(progress.working_quantity, Decimal("3"))
        self.assertEqual(progress.filled_quantity, Decimal("1"))
        self.assertEqual(progress.execution_event_count, 1)

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


class _StaticIntentStrategy:
    strategy_id = "sma"

    def __init__(self) -> None:
        self.intent = TargetPositionIntent(uuid4(), self.strategy_id, INSTRUMENT, Decimal("1"), "cross")

    @property
    def decisions(self):
        return ()

    def on_start(self, context):
        return ()

    def on_market(self, context):
        return (self.intent,)

    def on_fill(self, fill, context):
        return ()

    def on_end(self, context):
        return ()


def _strategy_spec(strategy_id: str) -> StrategySpec:
    return StrategySpec(
        strategy_id,
        "1.0.0",
        StrategyLifecycle.DRAFT,
        (ProductType.CRYPTO_SPOT,),
        (),
        ("momentum",),
        ("price",),
        (("instrument", INSTRUMENT.value),),
        ("sma",),
        (),
        (),
        (),
        (),
        (),
        Decimal("0.1"),
        ("bars",),
        ("market_orders",),
        "evidence-hash",
    )


def _context() -> Context:
    return Context(
        MarketView(NOW, 1, (INSTRUMENT,), available_instruments=(INSTRUMENT,)),
        PortfolioView(timestamp=NOW),
        budget=BudgetView(approved_capital=Decimal("1000")),
    )


if __name__ == "__main__":
    unittest.main()
