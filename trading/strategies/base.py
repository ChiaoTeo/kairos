from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, TYPE_CHECKING

from trading.domain.identity import InstrumentId
from trading.domain.intent import Intent
from trading.domain.order import Fill, Order

if TYPE_CHECKING:
    from trading.backtest.feed import MarketSlice
    from trading.backtest.portfolio import PortfolioSnapshot
    from trading.reference.catalog import ReferenceCatalog
    from trading.pricing.service import ValuationSnapshot
    from trading.research.features import FeatureSnapshot
    from trading.volatility.models import SurfaceSnapshot


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

    market: "MarketSlice"
    portfolio: "PortfolioSnapshot"
    working_orders: tuple[Order, ...]
    catalog: "ReferenceCatalog"
    valuation: "ValuationSnapshot | None" = None
    surface: "SurfaceSnapshot | None" = None
    features: "FeatureSnapshot | None" = None
    approved_capital: Decimal | None = None
    risk_state: tuple[tuple[str, str], ...] = ()
    strategy_positions: tuple[tuple[InstrumentId, Decimal], ...] = ()

    @property
    def now(self):
        return self.market.timestamp


class Strategy(Protocol):
    @property
    def strategy_id(self) -> str: ...

    @property
    def decisions(self) -> tuple[StrategyDecision, ...]: ...

    def on_start(self, context: StrategyContext) -> tuple[Intent, ...]: ...

    def on_market(self, context: StrategyContext) -> tuple[Intent, ...]: ...

    def on_fill(self, fill: Fill, context: StrategyContext) -> tuple[Intent, ...]: ...

    def on_end(self, context: StrategyContext) -> tuple[Intent, ...]: ...
