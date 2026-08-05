from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from kairospy.domain.reference import Asset, AssetId, AssetType, EntityId, LifecycleEvent
from kairospy.application.usecases.reference.application.requests import (
    ReferenceDelistRequest,
    ReferenceLifecycleSyncRequest,
    ReferenceRefreshRequest,
)
from kairospy.application.usecases.reference.application.results import ReferenceProviderRefreshResult, ReferenceRefreshResult, ReferenceSourceRefreshResult
from kairospy.application.usecases.reference.protocol import (
    ReferenceCatalogDelistSource,
    ReferenceCatalogSource,
    ReferenceDelistScheduleSource,
    ReferenceLifecycleRequest,
    ReferenceLifecycleSource,
    ReferenceStore,
)

from .catalogs import ReferenceCatalogService
from .provider import EquityProviderRefreshService, ReferenceDataRefreshService
from .refresh import ReferenceRefreshService


@dataclass(slots=True)
class ReferenceRefreshWorkflow:
    """Private refresh workflow used by the reference application facade."""

    store: ReferenceStore

    def exchange(self, source: ReferenceCatalogSource, request: ReferenceRefreshRequest) -> ReferenceSourceRefreshResult:
        return refresh_exchange_reference(
            self.store,
            source,
            as_of=request.as_of,
            venue=_required_venue(request),
            market=request.market,
        )

    def exchange_with_delist_schedule(self, source: ReferenceCatalogDelistSource, request: ReferenceRefreshRequest) -> ReferenceSourceRefreshResult:
        return refresh_exchange_reference_with_delist_schedule(
            self.store,
            source,
            as_of=request.as_of,
            venue=_required_venue(request),
            market=_required_market(request),
            include_delist_schedule=request.include_delist_schedule,
        )

    def provider(self, source: ReferenceCatalogSource, request: ReferenceRefreshRequest) -> ReferenceSourceRefreshResult:
        return refresh_provider_reference(
            self.store,
            source,
            as_of=request.as_of,
            venue=request.venue,
            market=request.market,
            params={"asset_class": request.asset_class} if request.asset_class else None,
        )

    def equity(self, source: ReferenceCatalogSource, request: ReferenceRefreshRequest) -> ReferenceProviderRefreshResult:
        return refresh_equity_provider(
            self.store,
            source,
            as_of=request.as_of,
            venue=request.venue,
            params={"asset_class": request.asset_class} if request.asset_class else None,
        )

    def lifecycle_events(self, source: ReferenceLifecycleSource, request: ReferenceLifecycleSyncRequest) -> tuple[LifecycleEvent, ...]:
        return sync_lifecycle_events(
            self.store,
            source,
            ticker=request.ticker,
            start=request.start,
            end=request.end,
            venue=request.venue,
        )


def _required_venue(request: ReferenceRefreshRequest) -> str:
    if request.venue is None or not request.venue.strip():
        raise ValueError("reference refresh requires a venue")
    return request.venue


def _required_market(request: ReferenceRefreshRequest) -> str:
    if request.market is None or not request.market.strip():
        raise ValueError("reference refresh with delist schedule requires a market")
    return request.market


def add_asset(
    store: ReferenceStore,
    *,
    symbol: str,
    asset_type: AssetType | str,
    effective_from: datetime,
    asset_id: AssetId | str | None = None,
    name: str | None = None,
    issuer_id: EntityId | str | None = None,
    metadata: Mapping[str, object] | None = None,
    replace_existing: bool = False,
) -> Asset:
    return ReferenceCatalogService(store).add_asset(
        symbol=symbol,
        asset_type=asset_type,
        effective_from=effective_from,
        asset_id=asset_id,
        name=name,
        issuer_id=issuer_id,
        metadata=metadata,
        replace_existing=replace_existing,
    )


def refresh_instrument_provider(
    store: ReferenceStore,
    provider: ReferenceCatalogSource,
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


def refresh_exchange_reference(
    store: ReferenceStore,
    exchange: ReferenceCatalogSource,
    *,
    as_of: datetime,
    venue: str,
    market: str | None = None,
    params: Mapping[str, object] | None = None,
) -> ReferenceSourceRefreshResult:
    return refresh_instrument_provider(
        store,
        exchange,
        as_of=as_of,
        venue=venue,
        market=market,
        params=params,
    )


def refresh_instrument_provider_with_delist_schedule(
    store: ReferenceStore,
    provider: ReferenceCatalogDelistSource,
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
    if include_delist_schedule:
        schedule_events = tuple(
            provider.fetch_delist_events(ReferenceDelistRequest(catalog=result.refresh.catalog, market=market))
        )
        store.append_events(schedule_events)
    return ReferenceProviderRefreshResult(result.refresh, schedule_events)


def refresh_exchange_reference_with_delist_schedule(
    store: ReferenceStore,
    exchange: ReferenceCatalogDelistSource,
    *,
    as_of: datetime,
    venue: str,
    market: str,
    params: Mapping[str, object] | None = None,
    include_delist_schedule: bool = True,
) -> ReferenceSourceRefreshResult:
    return refresh_instrument_provider_with_delist_schedule(
        store,
        exchange,
        as_of=as_of,
        venue=venue,
        market=market,
        params=params,
        include_delist_schedule=include_delist_schedule,
    )


def refresh_equity_provider(
    store: ReferenceStore,
    provider: ReferenceCatalogSource,
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


def refresh_provider_reference(
    store: ReferenceStore,
    provider: ReferenceCatalogSource,
    *,
    as_of: datetime,
    venue: str | None = None,
    params: Mapping[str, object] | None = None,
) -> ReferenceSourceRefreshResult:
    return refresh_equity_provider(
        store,
        provider,
        as_of=as_of,
        venue=venue,
        params=params,
    )


def sync_lifecycle_events(
    store: ReferenceStore,
    provider: ReferenceLifecycleSource,
    *,
    ticker: str,
    start: datetime,
    end: datetime,
    venue: str | None = None,
) -> tuple[LifecycleEvent, ...]:
    catalog = store.load_catalog()
    events = tuple(
        provider.fetch_lifecycle_events(
            ReferenceLifecycleRequest(ticker=ticker, start=start, end=end, catalog=catalog, venue=venue)
        )
    )
    store.append_events(events)
    return events


__all__ = [
    "ReferenceProviderRefreshResult",
    "ReferenceRefreshWorkflow",
    "ReferenceSourceRefreshResult",
    "add_asset",
    "refresh_exchange_reference",
    "refresh_exchange_reference_with_delist_schedule",
    "refresh_equity_provider",
    "refresh_instrument_provider",
    "refresh_instrument_provider_with_delist_schedule",
    "refresh_provider_reference",
    "sync_lifecycle_events",
]
