from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from kairospy.strategy import BudgetView, Context, PortfolioView, ReferenceView, StrategyDecision
from kairospy.strategy.intents import TargetPositionIntent


@dataclass(frozen=True, slots=True)
class ExampleCatalogView:
    name: str = "example-catalog"


class MidpointTargetStrategy:
    """Small deterministic Strategy used to demonstrate live/replay parity."""

    strategy_id = "example-midpoint-target-v1"

    def __init__(self, threshold: Decimal) -> None:
        self.threshold = threshold
        self._decisions: list[StrategyDecision] = []

    @property
    def decisions(self) -> tuple[StrategyDecision, ...]:
        return tuple(self._decisions)

    def on_start(self, context: Context):
        return ()

    def on_market(self, context: Context):
        if not context.market.reference_prices:
            return ()
        instrument_id, midpoint = context.market.reference_prices[0]
        target = Decimal("1") if midpoint < self.threshold else Decimal("0")
        action = "long" if target else "flat"
        self._decisions.append(StrategyDecision(
            context.now.isoformat(), action, f"midpoint={midpoint}",
            (instrument_id.value,),
        ))
        return (TargetPositionIntent(
            uuid5(
                NAMESPACE_URL,
                f"{self.strategy_id}:{context.now.isoformat()}:{instrument_id.value}:{target}",
            ),
            self.strategy_id, instrument_id, target,
            f"{action} because midpoint={midpoint} threshold={self.threshold}",
        ),)

    def on_fill(self, fill, context: Context):
        return ()

    def on_end(self, context: Context):
        return ()


def example_context(market) -> Context:
    return Context(
        market,
        PortfolioView(timestamp=market.timestamp, cash=Decimal("100000")),
        reference=ReferenceView(catalog_hash=ExampleCatalogView().name),
        budget=BudgetView(approved_capital=Decimal("100000")),
    )
