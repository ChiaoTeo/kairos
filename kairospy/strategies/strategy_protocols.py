from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, TYPE_CHECKING

from kairospy.domain.identity import InstrumentId
from kairospy.domain.intent import Intent
from kairospy.domain.order import Fill, Order

if TYPE_CHECKING:
    from kairospy.backtest.feed import MarketSnapshot
    from kairospy.backtest.portfolio import PortfolioSnapshot
    from kairospy.reference.catalog import ReferenceCatalog
    from kairospy.pricing.option_valuation import ValuationSnapshot
    from kairospy.study_platform.features import FeatureSnapshot
    from kairospy.volatility.contracts import SurfaceSnapshot
    from kairospy.features.runtime import FactorSnapshot
    from kairospy.execution.intent_status import IntentExecutionView, IntentScope


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    timestamp: str
    action: str
    reason: str
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Read-only application context supplied to a strategy implementation.

    This belongs to the strategy/application layer rather than the domain kernel:
    it composes catalog, portfolio, valuation, feature, and market projections.
    """

    market: "MarketSnapshot"
    portfolio: "PortfolioSnapshot"
    working_orders: tuple[Order, ...]
    catalog: "ReferenceCatalog"
    valuation: "ValuationSnapshot | None" = None
    surface: "SurfaceSnapshot | None" = None
    features: "FeatureSnapshot | None" = None
    approved_capital: Decimal | None = None
    risk_state: tuple[tuple[str, str], ...] = ()
    strategy_positions: tuple[tuple[InstrumentId, Decimal], ...] = ()
    factor_snapshots: tuple["FactorSnapshot", ...] = ()
    intent_executions: tuple["IntentExecutionView", ...] = ()

    @property
    def now(self):
        return self.market.timestamp

    def factor(self, factor_id: str) -> "FactorSnapshot":
        matches = [item for item in self.factor_snapshots if item.factor_id == factor_id]
        if len(matches) != 1:
            raise LookupError(f"strategy context requires exactly one factor snapshot: {factor_id}")
        return matches[0]

    def intent_execution(self, intent_id) -> "IntentExecutionView | None":
        return next((item for item in self.intent_executions if item.intent_id == intent_id), None)

    def active_intent(self, scope: "IntentScope | str") -> "IntentExecutionView | None":
        key = scope if isinstance(scope, str) else scope.key
        return next((item for item in self.intent_executions if item.scope.key == key), None)


class Strategy(Protocol):
    @property
    def strategy_id(self) -> str: ...

    @property
    def decisions(self) -> tuple[StrategyDecision, ...]: ...

    def on_start(self, context: StrategyContext) -> tuple[Intent, ...]: ...

    def on_market(self, context: StrategyContext) -> tuple[Intent, ...]: ...

    def on_fill(self, fill: Fill, context: StrategyContext) -> tuple[Intent, ...]: ...

    def on_end(self, context: StrategyContext) -> tuple[Intent, ...]: ...
