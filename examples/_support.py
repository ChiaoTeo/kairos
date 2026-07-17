from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from trading.domain.intent import TargetPositionIntent
from trading.strategies import StrategyContext, StrategyDecision


@dataclass(frozen=True, slots=True)
class ExamplePortfolioView:
    cash: Decimal = Decimal("100000")
    positions: tuple = ()


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

    def on_start(self, context: StrategyContext):
        return ()

    def on_market(self, context: StrategyContext):
        quote = context.market.instruments[0].quote
        if quote is None or quote.bid is None or quote.ask is None:
            return ()
        midpoint = (quote.bid + quote.ask) / Decimal("2")
        target = Decimal("1") if midpoint < self.threshold else Decimal("0")
        action = "long" if target else "flat"
        self._decisions.append(StrategyDecision(
            context.now.isoformat(), action, f"midpoint={midpoint}",
            (quote.instrument_id.value,),
        ))
        return (TargetPositionIntent(
            uuid5(
                NAMESPACE_URL,
                f"{self.strategy_id}:{context.now.isoformat()}:{quote.instrument_id.value}:{target}",
            ),
            self.strategy_id, quote.instrument_id, target,
            f"{action} because midpoint={midpoint} threshold={self.threshold}",
        ),)

    def on_fill(self, fill, context: StrategyContext):
        return ()

    def on_end(self, context: StrategyContext):
        return ()


def example_context(market) -> StrategyContext:
    return StrategyContext(
        market, ExamplePortfolioView(), (), ExampleCatalogView(),
        approved_capital=Decimal("100000"),
    )
