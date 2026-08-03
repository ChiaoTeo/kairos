"""Runtime-envelope adapters assembled by the composition root.

These adapters are deliberately outside runtime. They connect runtime events
to business application projections and can be replaced in tests or by a
different launch composition.
"""

from .execution import ExecutionProjector, ExecutionUpdateParser
from .account import AccountCurrentProjector, AccountProjector, EquityProjector, FundingProjector
from .execution_intent import TradingIntentProjector
from .market import MarketProjector
from .order import OrderProjector
from .reference import ReferenceProjector
from .runtime import RuntimeProjectorSet, runtime_projectors, runtime_services_for

__all__ = [
    "AccountCurrentProjector",
    "AccountProjector",
    "EquityProjector",
    "FundingProjector",
    "ExecutionProjector",
    "ExecutionUpdateParser",
    "MarketProjector",
    "OrderProjector",
    "ReferenceProjector",
    "TradingIntentProjector",
    "RuntimeProjectorSet",
    "runtime_projectors",
    "runtime_services_for",
]
