from kairos.domain.identity import InstitutionId

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
import unittest

from kairos.domain.capability import TimeInForce
from kairos.domain.identity import InstrumentId
from kairos.domain.intent import TargetPositionIntent
from kairos.domain.product import ProductType
from kairos.domain.strategy_contract import EconomicIntent, StrategyLifecycle, StrategySpec
from kairos.execution.planner import LeggingPolicy
from kairos.execution.policy import ExecutionMode, ExecutionPolicy, PartialFillPolicy
from kairos.execution.strategy_planner import plan_economic_intent
from kairos.domain.order import ExecutionInstructions
from kairos.domain.capability import OrderType
from kairos.risk.portfolio_governance import (
    AllocationDecisionType, PortfolioAllocator, StrategyAllocation,
)


def spec():
    return StrategySpec(
        "btc_condor", "1.0.0", StrategyLifecycle.DRAFT, (ProductType.CRYPTO_OPTION,),
        ("short_volatility",), ("variance_risk_premium",), ("gamma", "jump"),
        (("underlying", "BTC"),), ("skew", "atm_iv"), (("threshold", .8),),
        (("structure", "iron_condor"),), ("high_skew",), ("hold_7d",), ("daily",),
        Decimal(".02"), ("synchronous_quotes",), ("combo_orders",), "research-hash",
    )


class StrategyGovernanceTest(unittest.TestCase):
    def test_strategy_promotion_cannot_skip_evidence_stage(self):
        strategy = spec()
        with self.assertRaises(ValueError):
            strategy.promote(StrategyLifecycle.LIVE_APPROVED)
        promoted = strategy.promote(StrategyLifecycle.STUDY_VALIDATED)
        self.assertEqual(promoted.lifecycle, StrategyLifecycle.STUDY_VALIDATED)
        self.assertEqual(promoted.spec_hash, strategy.spec_hash)

    def test_legacy_research_validated_maps_to_study_validated(self):
        self.assertIs(StrategyLifecycle("RESEARCH_VALIDATED"), StrategyLifecycle.STUDY_VALIDATED)

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

    def test_economic_planner_enforces_policy_identity_and_validity(self):
        strategy=spec();now=datetime.now(timezone.utc);instrument=InstrumentId("BTC")
        target=TargetPositionIntent(uuid4(),strategy.strategy_id,instrument,Decimal("1"),"test")
        intent=EconomicIntent.create(strategy=strategy,decision_time=now,valid_until=now+timedelta(minutes=5),
            intents=(target,),risk_budget=Decimal("1000"),urgency="normal",execution_policy_id="taker-v1",feature_snapshot_hash="x")
        policy=ExecutionPolicy("taker-v1","1",ExecutionMode.TAKER,TimeInForce.IOC,Decimal("10"))
        instructions={instrument:ExecutionInstructions(OrderType.MARKET,TimeInForce.IOC)}
        from kairos.domain.identity import AccountKey,AccountType,VenueId
        account=AccountKey(InstitutionId("sim"),"a",AccountType.CRYPTO_SPOT)
        plan=plan_economic_intent(intent,policy=policy,accounts={instrument:account},current_positions={},instructions=instructions,now=now)
        self.assertEqual(plan.strategy_spec_hash,strategy.spec_hash)
        with self.assertRaises(ValueError):
            plan_economic_intent(intent,policy=policy,accounts={instrument:account},current_positions={},instructions=instructions,now=now+timedelta(hours=1))


if __name__ == "__main__":
    unittest.main()
