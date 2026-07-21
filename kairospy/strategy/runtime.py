from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import timedelta
from decimal import Decimal
import hashlib
import json

from kairospy.trading.strategy_contract import EconomicIntent, StrategySpec

from .protocols import Strategy, StrategyContext


class GovernedStrategyRuntime:
    """Adapts user strategy code to the versioned EconomicIntent contract."""

    def __init__(
        self,
        strategy: Strategy,
        spec: StrategySpec,
        *,
        execution_policy_id: str,
        decision_validity: timedelta = timedelta(minutes=5),
    ) -> None:
        if strategy.strategy_id != spec.strategy_id:
            raise ValueError("strategy implementation and spec identity differ")
        if decision_validity.total_seconds() <= 0:
            raise ValueError("decision validity must be positive")
        self.strategy = strategy
        self.spec = spec
        self.execution_policy_id = execution_policy_id
        self.decision_validity = decision_validity

    def on_start(self, context: StrategyContext, *, approved_equity: Decimal | None = None) -> EconomicIntent | None:
        return self._wrap(tuple(self.strategy.on_start(context)), context, approved_equity)

    def on_market(self, context: StrategyContext, *, approved_equity: Decimal | None = None) -> EconomicIntent | None:
        return self._wrap(tuple(self.strategy.on_market(context)), context, approved_equity)

    def on_fill(self, fill, context: StrategyContext, *, approved_equity: Decimal | None = None) -> EconomicIntent | None:
        return self._wrap(tuple(self.strategy.on_fill(fill, context)), context, approved_equity)

    def on_end(self, context: StrategyContext, *, approved_equity: Decimal | None = None) -> EconomicIntent | None:
        return self._wrap(tuple(self.strategy.on_end(context)), context, approved_equity)

    def _wrap(self, intents, context, approved_equity):
        if not intents:
            return None
        capital = approved_equity if approved_equity is not None else context.approved_capital
        if capital is None or capital <= 0:
            raise ValueError("governed strategy runtime requires approved capital")
        budget = capital * self.spec.risk_budget_fraction
        return EconomicIntent.create(
            strategy=self.spec,
            decision_time=context.now,
            valid_until=context.now + self.decision_validity,
            intents=intents,
            risk_budget=budget,
            urgency="normal",
            execution_policy_id=self.execution_policy_id,
            feature_snapshot_hash=_context_feature_hash(context),
            diagnostics=(("market_sequence", context.market.sequence),),
        )


def _snapshot_hash(snapshot) -> str:
    if snapshot is None:
        return "none"
    payload = _snapshot_payload(snapshot)
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _context_feature_hash(context: StrategyContext) -> str:
    if context.factor_snapshots:
        return _snapshot_hash(context.factor_snapshots)
    return _snapshot_hash(context.features)


def _snapshot_payload(value):
    if is_dataclass(value):
        return _snapshot_payload(asdict(value))
    if isinstance(value, dict):
        return {str(key): _snapshot_payload(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_snapshot_payload(item) for item in value]
    return value
