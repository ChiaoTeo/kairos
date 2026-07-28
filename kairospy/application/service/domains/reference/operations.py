from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from kairospy.core.reference import LifecycleEvent
from .store import ReferenceStore
from kairospy.infrastructure.integrations.protocols import ReferenceDataClient

from .provider import EquityProviderRefreshService, ReferenceDataRefreshService
from .refresh import ReferenceRefreshResult, ReferenceRefreshService


@dataclass(frozen=True, slots=True)
class ReferenceProviderRefreshResult:
    refresh: ReferenceRefreshResult
    scheduled_events: tuple[LifecycleEvent, ...] = ()


def refresh_instrument_provider(
    store: ReferenceStore,
    provider: ReferenceDataClient,
    *,
    as_of: datetime,
    venue: str | None = None,
    market: str | None = None,
    params: Mapping[str, object] | None = None,
) -> ReferenceProviderRefreshResult:
    refresh = ReferenceDataRefreshService(ReferenceRefreshService(store)).refresh(
        provider,
        as_of=as_of,
        venue=venue,
        market=market,
        params=params,
    )
    return ReferenceProviderRefreshResult(refresh)


def refresh_instrument_provider_with_delist_schedule(
    store: ReferenceStore,
    provider: ReferenceDataClient,
    *,
    as_of: datetime,
    venue: str,
    market: str,
    params: Mapping[str, object] | None = None,
    include_delist_schedule: bool = True,
) -> ReferenceProviderRefreshResult:
    result = refresh_instrument_provider(
        store,
        provider,
        as_of=as_of,
        venue=venue,
        market=market,
        params=params,
    )
    schedule_events: tuple[LifecycleEvent, ...] = ()
    if include_delist_schedule and hasattr(provider, "fetch_delist_events"):
        schedule_events = tuple(provider.fetch_delist_events(catalog=result.refresh.catalog, market=market))
        store.append_events(schedule_events)
    return ReferenceProviderRefreshResult(result.refresh, schedule_events)


def refresh_equity_provider(
    store: ReferenceStore,
    provider: ReferenceDataClient,
    *,
    as_of: datetime,
    venue: str | None = None,
    params: Mapping[str, object] | None = None,
) -> ReferenceProviderRefreshResult:
    refresh = EquityProviderRefreshService(ReferenceRefreshService(store)).refresh(
        provider,
        as_of=as_of,
        venue=venue,
        params=params,
    )
    return ReferenceProviderRefreshResult(refresh)


def sync_lifecycle_events(
    store: ReferenceStore,
    provider: object,
    *,
    ticker: str,
    start: datetime,
    end: datetime,
    venue: str | None = None,
) -> tuple[LifecycleEvent, ...]:
    if not hasattr(provider, "fetch_lifecycle_events"):
        raise ValueError("provider does not support lifecycle events")
    catalog = store.load_catalog()
    events = tuple(provider.fetch_lifecycle_events(ticker, start=start, end=end, catalog=catalog, venue=venue))
    store.append_events(events)
    return events


__all__ = [
    "ReferenceProviderRefreshResult",
    "refresh_equity_provider",
    "refresh_instrument_provider",
    "refresh_instrument_provider_with_delist_schedule",
    "sync_lifecycle_events",
]
