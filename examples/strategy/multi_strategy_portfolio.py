"""Demonstrate capital allocation, virtual ownership and account-level netting."""

from __future__ import annotations

from datetime import datetime,timedelta,timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from trading.domain.identity import InstrumentId
from trading.domain.intent import TargetPositionIntent
from trading.domain.strategy_contract import EconomicIntent
from trading.risk.portfolio_governance import PortfolioAllocator,StrategyAllocation
from trading.risk.strategy_positions import StrategyPositionBook
from trading.strategies.specs import builtin_strategy_specs


def run()->dict[str,object]:
    now=datetime(2026,7,17,tzinfo=timezone.utc);instrument=InstrumentId("equity:aapl")
    specs={spec.strategy_id:spec for spec,_ in builtin_strategy_specs()}
    requested=[]
    for strategy_id,target in (("covered-call-v1",Decimal("100")),("protective-put-v1",Decimal("-25"))):
        intent=TargetPositionIntent(uuid4(),strategy_id,instrument,target,"portfolio acceptance")
        requested.append(EconomicIntent.create(strategy=specs[strategy_id],decision_time=now,valid_until=now+timedelta(minutes=5),
            intents=(intent,),risk_budget=Decimal("1000"),urgency="normal",execution_policy_id="portfolio-demo",
            feature_snapshot_hash="none"))
    allocator=PortfolioAllocator((StrategyAllocation("covered-call-v1",Decimal("800")),
        StrategyAllocation("protective-put-v1",Decimal("600"))),portfolio_risk_limit=Decimal("1200"))
    decisions=[];committed=Decimal("0")
    for intent in requested:
        decision=allocator.approve(intent,committed_risk=committed);decisions.append(decision);committed+=decision.approved_risk_budget
    book=StrategyPositionBook();book.apply("covered-call-v1",instrument,Decimal("100"));book.apply("protective-put-v1",instrument,Decimal("-25"))
    net=book.netted_positions()[0]
    return {"allocation_decisions":[item.decision.value for item in decisions],
        "approved_budgets":[str(item.approved_risk_budget) for item in decisions],
        "strategy_positions":[{"strategy_id":item.strategy_id,"quantity":str(item.quantity)} for item in net.allocations],
        "account_net_quantity":str(net.account_quantity),"virtual_ownership_preserved":len(net.allocations)==2}


def main():print(json.dumps(run(),indent=2,sort_keys=True))


if __name__=="__main__":main()
