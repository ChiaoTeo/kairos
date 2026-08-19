from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from kairospy.domain_types import DataEvent

from .models import PortfolioSnapshot


@dataclass(frozen=True, slots=True)
class PortfolioUpdatedEvent(DataEvent[PortfolioSnapshot]):
    kind: Literal["portfolio_updated"] = field(init=False, default="portfolio_updated")


@dataclass(frozen=True, slots=True)
class PortfolioBecameStaleEvent(DataEvent[PortfolioSnapshot]):
    kind: Literal["portfolio_became_stale"] = field(
        init=False, default="portfolio_became_stale"
    )


@dataclass(frozen=True, slots=True)
class PortfolioRecoveredEvent(DataEvent[PortfolioSnapshot]):
    kind: Literal["portfolio_recovered"] = field(
        init=False, default="portfolio_recovered"
    )


PortfolioEvent: TypeAlias = (
    PortfolioUpdatedEvent | PortfolioBecameStaleEvent | PortfolioRecoveredEvent
)
