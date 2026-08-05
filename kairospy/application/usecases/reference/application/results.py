"""Business result types returned by reference application use cases."""

from __future__ import annotations

from dataclasses import dataclass

from kairospy.domain.reference import LifecycleEvent, MarketDefinition, ReferenceCatalog


@dataclass(frozen=True, slots=True)
class ReferenceRefreshResult:
    catalog: ReferenceCatalog
    events: tuple[LifecycleEvent, ...]
    previous_markets: tuple[MarketDefinition, ...]
    current_markets: tuple[MarketDefinition, ...]


@dataclass(frozen=True, slots=True)
class ReferenceProviderRefreshResult:
    refresh: ReferenceRefreshResult
    scheduled_events: tuple[LifecycleEvent, ...] = ()


ReferenceSourceRefreshResult = ReferenceProviderRefreshResult

__all__ = ["ReferenceProviderRefreshResult", "ReferenceRefreshResult", "ReferenceSourceRefreshResult"]
