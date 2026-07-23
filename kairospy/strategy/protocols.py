from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, TYPE_CHECKING

from .intents import Intent
from .views import (
    BudgetView,
    FeatureValue,
    FeatureView,
    IntentProgressView,
    IntentView,
    MarketView,
    OrderView,
    PortfolioView,
    ReferenceView,
    ViewSchema,
    context_view_schemas,
    view_hash,
)

if TYPE_CHECKING:
    from kairospy.execution.fills import Fill


@dataclass(frozen=True, slots=True)
class Context:
    market: MarketView
    portfolio: PortfolioView
    features: FeatureView = field(default_factory=FeatureView.empty)
    reference: ReferenceView = field(default_factory=ReferenceView.empty)
    orders: OrderView = field(default_factory=OrderView.empty)
    intents: IntentView = field(default_factory=IntentView.empty)
    budget: BudgetView = field(default_factory=BudgetView.empty)

    @property
    def now(self):
        return self.market.timestamp

    @classmethod
    def view_schemas(cls) -> tuple[ViewSchema, ...]:
        return context_view_schemas()

    @property
    def view_hashes(self) -> Mapping[str, str]:
        return {
            "market": self.market.view_hash,
            "portfolio": self.portfolio.view_hash,
            "features": self.features.view_hash,
            "reference": self.reference.view_hash,
            "orders": self.orders.view_hash,
            "intents": self.intents.view_hash,
            "budget": self.budget.view_hash,
        }

    @property
    def context_hash(self) -> str:
        return view_hash(self.view_hashes)

    def factor(self, factor_id: str) -> FeatureValue:
        return self.features.factor(factor_id)

    def intent_execution(self, intent_id) -> IntentProgressView | None:
        return self.intents.execution(intent_id)

    def active_intent(self, scope: Any) -> IntentProgressView | None:
        return self.intents.active(scope)


class Strategy(Protocol):
    """User strategy contract: read Context, emit strategy intents.

    Intent is the single strategy output contract. Executable work and audit
    reasons both flow through Intent and are wrapped into EconomicIntent by the
    governed runtime.
    """

    @property
    def strategy_id(self) -> str: ...

    def on_start(self, context: Context) -> Sequence[Intent]: ...

    def on_market(self, context: Context) -> Sequence[Intent]: ...

    def on_fill(self, fill: "Fill", context: Context) -> Sequence[Intent]: ...

    def on_end(self, context: Context) -> Sequence[Intent]: ...
