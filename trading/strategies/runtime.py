from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
import hashlib
import json

from trading.domain.strategy_contract import EconomicIntent, StrategySpec

from .base import Strategy, StrategyContext


class GovernedStrategyRuntime:
    """Adapts a Strategy Model to the versioned EconomicIntent contract."""

    def __init__(self, strategy: Strategy, spec: StrategySpec, *, execution_policy_id: str,
                 decision_validity: timedelta = timedelta(minutes=5)) -> None:
        if strategy.strategy_id != spec.strategy_id:
            raise ValueError("strategy implementation and spec identity differ")
        self.strategy = strategy; self.spec = spec; self.execution_policy_id = execution_policy_id
        self.decision_validity = decision_validity
        if decision_validity.total_seconds() <= 0: raise ValueError("decision validity must be positive")

    def on_market(self, context: StrategyContext, *, approved_equity: Decimal | None = None) -> EconomicIntent | None:
        intents = tuple(self.strategy.on_market(context))
        if not intents: return None
        capital=approved_equity if approved_equity is not None else context.approved_capital
        if capital is None or capital<=0:raise ValueError("governed strategy runtime requires approved capital")
        budget = capital * self.spec.risk_budget_fraction
        return EconomicIntent.create(strategy=self.spec, decision_time=context.now,
            valid_until=context.now+self.decision_validity, intents=intents, risk_budget=budget,
            urgency="normal", execution_policy_id=self.execution_policy_id,
            feature_snapshot_hash=_snapshot_hash(context.features),
            diagnostics=(("market_sequence", context.market.sequence),))


def _snapshot_hash(snapshot) -> str:
    if snapshot is None: return "none"
    payload=asdict(snapshot)
    encoded=json.dumps(payload,sort_keys=True,default=str,separators=(",",":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
