from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.core.reference import (
    LifecycleEvent,
    MarketDefinition,
    ReferenceCatalog,
    apply_catalog_snapshot,
)
from .store import ReferenceStore


@dataclass(frozen=True, slots=True)
class ReferenceRefreshResult:
    catalog: ReferenceCatalog
    events: tuple[LifecycleEvent, ...]
    previous_markets: tuple[MarketDefinition, ...]
    current_markets: tuple[MarketDefinition, ...]


@dataclass(slots=True)
class ReferenceRefreshService:
    store: ReferenceStore

    def refresh_snapshot(
        self,
        snapshot: ReferenceCatalog,
        *,
        as_of: datetime,
        venue: str | None = None,
        market: str | None = None,
    ) -> ReferenceRefreshResult:
        catalog = self.store.load_catalog()
        transition = apply_catalog_snapshot(
            catalog,
            snapshot,
            as_of=as_of,
            venue=venue,
            market=market,
        )
        result = ReferenceRefreshResult(
            transition.catalog,
            transition.events,
            transition.previous_markets,
            transition.current_markets,
        )
        self.store.save_catalog(result.catalog)
        self.store.append_events(result.events)
        return result


__all__ = ["ReferenceRefreshResult", "ReferenceRefreshService"]
