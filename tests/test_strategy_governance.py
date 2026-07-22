from kairospy.identity import InstitutionId

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
import unittest

from kairospy.execution.orders import TimeInForce
from kairospy.identity import InstrumentId
from kairospy.strategy.intents import TargetPositionIntent
from kairospy.reference.contracts import ProductType
from kairospy.strategy.contracts import EconomicIntent, StrategyLifecycle, StrategySpec
from kairospy.strategy.stop_policy import StopAction, StopPolicy, StopReason, StopRule
from kairospy.governance.stop_resolver import resolve_stop_policy
from kairospy.execution.planner import LeggingPolicy
from kairospy.execution.policy import ExecutionMode, ExecutionPolicy, PartialFillPolicy
from kairospy.execution.strategy_planner import plan_economic_intent
from kairospy.execution.orders import ExecutionInstructions
from kairospy.execution.orders import OrderType
from kairospy.risk.portfolio_governance import (
    AllocationDecisionType, PortfolioAllocator, StrategyAllocation,
)
from kairospy.risk.engine import RiskDecision, RiskDecisionType
from kairospy.risk.limits import RiskLimits
from kairospy.strategy.views import BudgetView


def spec():
    return StrategySpec(
        "btc_condor", "1.0.0", StrategyLifecycle.DRAFT, (ProductType.CRYPTO_OPTION,),
        ("short_volatility",), ("variance_risk_premium",), ("gamma", "jump"),
        (("underlying", "BTC"),), ("skew", "atm_iv"), (("threshold", .8),),
        (("structure", "iron_condor"),), ("high_skew",), ("hold_7d",), ("daily",),
        Decimal(".02"), ("synchronous_quotes",), ("combo_orders",), "evidence-hash",
    )


class StrategyGovernanceTest(unittest.TestCase):
    def test_strategy_promotion_cannot_skip_evidence_stage(self):
        strategy = spec()
        with self.assertRaises(ValueError):
            strategy.promote(StrategyLifecycle.LIVE_APPROVED)
        promoted = strategy.promote(StrategyLifecycle.RESEARCH_VALIDATED)
        self.assertEqual(promoted.lifecycle, StrategyLifecycle.RESEARCH_VALIDATED)
        self.assertEqual(promoted.spec_hash, strategy.spec_hash)

    def test_legacy_lifecycle_alias_is_not_public(self):
        with self.assertRaises(ValueError):
            StrategyLifecycle("STUDY_VALIDATED")

    def test_strategy_declares_stop_policy_but_system_keeps_risk_floor(self):
        strategy = spec()
        self.assertEqual(
            strategy.default_stop_policy.action_for(StopReason.MANUAL),
            StopAction.CANCEL_ORDERS,
        )
        loose = replace(
            strategy,
            default_stop_policy=StopPolicy((
                StopRule(StopReason.RISK_BREACH, StopAction.KEEP_POSITIONS),
                StopRule(StopReason.EMERGENCY, StopAction.FLATTEN),
            )),
        )

        risk_decision = resolve_stop_policy(loose, StopReason.RISK_BREACH)
        self.assertEqual(risk_decision.requested_action, StopAction.KEEP_POSITIONS)
        self.assertEqual(risk_decision.action, StopAction.REDUCE_ONLY)
        self.assertTrue(risk_decision.requires_reduce_only)

        emergency = resolve_stop_policy(loose, StopReason.EMERGENCY)
        self.assertEqual(emergency.requested_action, StopAction.FLATTEN)
        self.assertEqual(emergency.action, StopAction.REDUCE_ONLY)
        approved_emergency = resolve_stop_policy(loose, StopReason.EMERGENCY, allow_flatten=True)
        self.assertEqual(approved_emergency.action, StopAction.FLATTEN)

    def test_economic_intent_preserves_strategy_and_evidence_hashes(self):
        strategy = spec(); now = datetime.now(timezone.utc)
        target = TargetPositionIntent(uuid4(), strategy.strategy_id, InstrumentId("BTC"), Decimal("1"), "test")
        intent = EconomicIntent.create(strategy=strategy, decision_time=now, valid_until=now+timedelta(minutes=5),
            intents=(target,), risk_budget=Decimal("2000"), urgency="normal", execution_policy_id="taker-v1",
            feature_snapshot_hash="feature-hash")
        self.assertEqual(intent.strategy_spec_hash, strategy.spec_hash)
        self.assertEqual(intent.intents, (target,))
        self.assertEqual(intent.atomicity_preference,"atomic")

    def test_economic_intent_decision_id_is_replay_deterministic(self):
        strategy = spec(); now = datetime(2026, 7, 17, tzinfo=timezone.utc)
        target = TargetPositionIntent(uuid4(), strategy.strategy_id, InstrumentId("BTC"), Decimal("1"), "test")
        arguments = dict(
            strategy=strategy, decision_time=now, valid_until=now+timedelta(minutes=5),
            intents=(target,), risk_budget=Decimal("2000"), urgency="normal",
            execution_policy_id="taker-v1", feature_snapshot_hash="feature-hash",
        )
        first = EconomicIntent.create(**arguments)
        second = EconomicIntent.create(**arguments)
        self.assertEqual(first, second)
        self.assertEqual(first.decision_id, second.decision_id)

    def test_execution_policy_enforces_maker_and_legging_requirements(self):
        with self.assertRaises(ValueError):
            ExecutionPolicy("maker", "1", ExecutionMode.MAKER, TimeInForce.GTC, Decimal("5"))
        policy = ExecutionPolicy("hybrid", "1", ExecutionMode.HYBRID, TimeInForce.GTC, Decimal("10"),
            maker_timeout_ms=2000, queue_model="fifo", partial_fill_policy=PartialFillPolicy.HEDGE_IMMEDIATELY,
            legging_policy=LeggingPolicy.SEQUENTIAL, maximum_naked_legs=1)
        self.assertIn("queue_reconstructable", policy.required_data_capabilities)

    def test_portfolio_allocator_resizes_without_changing_structure(self):
        strategy = spec(); now = datetime.now(timezone.utc)
        target = TargetPositionIntent(uuid4(), strategy.strategy_id, InstrumentId("BTC"), Decimal("1"), "test")
        intent = EconomicIntent.create(strategy=strategy, decision_time=now, valid_until=now+timedelta(minutes=5),
            intents=(target,), risk_budget=Decimal("2000"), urgency="normal", execution_policy_id="taker-v1",
            feature_snapshot_hash="feature-hash")
        decision = PortfolioAllocator((StrategyAllocation("btc_condor", Decimal("1500")),), Decimal("10000")).approve(intent)
        self.assertEqual(decision.decision, AllocationDecisionType.RESIZED)
        self.assertEqual(decision.approved_risk_budget, Decimal("1500"))
        self.assertEqual(intent.intents, (target,))

    def test_budget_view_projects_risk_allocation_and_governance_evidence(self):
        strategy = spec(); now = datetime(2026, 7, 19, tzinfo=timezone.utc)
        target = TargetPositionIntent(uuid4(), strategy.strategy_id, InstrumentId("BTC"), Decimal("1"), "test")
        intent = EconomicIntent.create(strategy=strategy, decision_time=now, valid_until=now+timedelta(minutes=5),
            intents=(target,), risk_budget=Decimal("2000"), urgency="normal", execution_policy_id="taker-v1",
            feature_snapshot_hash="feature-hash")
        allocation = PortfolioAllocator((StrategyAllocation("btc_condor", Decimal("1500")),), Decimal("10000")).approve(intent)
        risk_decision = RiskDecision(
            uuid4(), target.intent_id, RiskDecisionType.APPROVED, "all",
            "all pre-trade checks passed", 1, 1,
        )

        view = BudgetView.from_evidence(
            as_of=now,
            allocation_decisions=(allocation,),
            risk_decisions=(risk_decision,),
            risk_limits=RiskLimits(min_remaining_cash=Decimal("100")),
            runtime_state={"status": "reduce_only", "reason": "operator intervention"},
            committed_capital=Decimal("500"),
        )

        self.assertEqual(view.approved_capital, Decimal("1500"))
        self.assertEqual(view.remaining_capital, Decimal("1000"))
        self.assertEqual(view.decision_count, 2)
        self.assertEqual(view.approved_count, 1)
        self.assertEqual(view.resized_count, 1)
        self.assertEqual(view.rejected_count, 0)
        self.assertTrue(view.reduce_only)
        self.assertEqual(view.blocked_reason, "operator intervention")
        self.assertIn(("risk:all", "approved:all pre-trade checks passed"), view.risk_state)
        self.assertIn(("allocation:btc_condor", "resized:risk budget reduced by allocation gate"), view.risk_state)
        self.assertNotEqual(view.risk_decision_hash, "none")
        self.assertNotEqual(view.allocation_hash, "none")
        self.assertNotEqual(view.limit_hash, "none")
        self.assertNotEqual(view.governance_hash, "none")
        self.assertNotEqual(view.state_hash, "none")

    def test_economic_planner_enforces_policy_identity_and_validity(self):
        strategy=spec();now=datetime.now(timezone.utc);instrument=InstrumentId("BTC")
        target=TargetPositionIntent(uuid4(),strategy.strategy_id,instrument,Decimal("1"),"test")
        intent=EconomicIntent.create(strategy=strategy,decision_time=now,valid_until=now+timedelta(minutes=5),
            intents=(target,),risk_budget=Decimal("1000"),urgency="normal",execution_policy_id="taker-v1",feature_snapshot_hash="x")
        policy=ExecutionPolicy("taker-v1","1",ExecutionMode.TAKER,TimeInForce.IOC,Decimal("10"))
        instructions={instrument:ExecutionInstructions(OrderType.MARKET,TimeInForce.IOC)}
        from kairospy.identity import AccountRef,AccountType,VenueId
        account=AccountRef(InstitutionId("sim"),"a",AccountType.CRYPTO_SPOT)
        plan=plan_economic_intent(intent,policy=policy,accounts={instrument:account},current_positions={},instructions=instructions,now=now)
        self.assertEqual(plan.strategy_spec_hash,strategy.spec_hash)
        with self.assertRaises(ValueError):
            plan_economic_intent(intent,policy=policy,accounts={instrument:account},current_positions={},instructions=instructions,now=now+timedelta(hours=1))


if __name__ == "__main__":
    unittest.main()
