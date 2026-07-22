from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Sequence

from .contracts import EconomicIntent, StrategySpec
from .intents import Intent
from .protocols import Context, Strategy


class StrategyRuntime:
    """Adapts user strategy hooks into runtime-collected intents."""

    def __init__(self, strategy: Strategy) -> None:
        self.strategy = strategy

    def intents_on_start(self, context: Context) -> tuple[Intent, ...]:
        return tuple(self.strategy.on_start(context))

    def intents_on_market(self, context: Context) -> tuple[Intent, ...]:
        return tuple(self.strategy.on_market(context))

    def intents_on_fill(self, fill, context: Context) -> tuple[Intent, ...]:
        return tuple(self.strategy.on_fill(fill, context))

    def intents_on_end(self, context: Context) -> tuple[Intent, ...]:
        return tuple(self.strategy.on_end(context))


class GovernedStrategyRuntime(StrategyRuntime):
    """Adds the versioned EconomicIntent contract to StrategyRuntime."""

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
        super().__init__(strategy)
        self.spec = spec
        self.execution_policy_id = execution_policy_id
        self.decision_validity = decision_validity

    def wrap_intents(
        self,
        intents: Sequence[Intent],
        context: Context,
        *,
        approved_equity: Decimal | None = None,
    ) -> EconomicIntent | None:
        if not intents:
            return None
        values = tuple(intents)
        capital = approved_equity if approved_equity is not None else context.budget.approved_capital
        if capital is None or capital <= 0:
            raise ValueError("governed strategy runtime requires approved capital")
        budget = capital * self.spec.risk_budget_fraction
        return EconomicIntent.create(
            strategy=self.spec,
            decision_time=context.now,
            valid_until=context.now + self.decision_validity,
            intents=values,
            risk_budget=budget,
            urgency="normal",
            execution_policy_id=self.execution_policy_id,
            feature_snapshot_hash=_context_feature_hash(context),
            diagnostics=(("market_sequence", context.market.sequence),),
        )


def _context_feature_hash(context: Context) -> str:
    return context.features.feature_hash
