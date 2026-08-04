from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from kairospy.domain.reference import Asset, AssetId, AssetType, EntityId, LifecycleEvent
from kairospy.application.usecases.reference.protocol import ReferenceStore

from .catalogs import ReferenceCatalogService
from .provider import EquityProviderRefreshService, ReferenceDataRefreshService
from .refresh import ReferenceRefreshResult, ReferenceRefreshService


@dataclass(frozen=True, slots=True)
class ReferenceProviderRefreshResult:
    refresh: ReferenceRefreshResult
    scheduled_events: tuple[LifecycleEvent, ...] = ()


ReferenceSourceRefreshResult = ReferenceProviderRefreshResult


@dataclass(slots=True)
class ReferenceRefreshWorkflow:
    """Private refresh workflow used by the reference application facade."""

    store: ReferenceStore

    def exchange(self, source: object, **kwargs: object) -> ReferenceSourceRefreshResult:
        return refresh_exchange_reference(self.store, source, **kwargs)  # type: ignore[arg-type]

    def exchange_with_delist_schedule(self, source: object, **kwargs: object) -> ReferenceSourceRefreshResult:
        return refresh_exchange_reference_with_delist_schedule(self.store, source, **kwargs)  # type: ignore[arg-type]

    def provider(self, source: object, **kwargs: object) -> ReferenceSourceRefreshResult:
        return refresh_provider_reference(self.store, source, **kwargs)  # type: ignore[arg-type]

    def equity(self, source: object, **kwargs: object) -> ReferenceProviderRefreshResult:
        return refresh_equity_provider(self.store, source, **kwargs)  # type: ignore[arg-type]

    def lifecycle_events(self, source: object, **kwargs: object) -> tuple[LifecycleEvent, ...]:
        return sync_lifecycle_events(self.store, source, **kwargs)  # type: ignore[arg-type]


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
    provider: object,
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
    exchange: object,
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
    provider: object,
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


def refresh_exchange_reference_with_delist_schedule(
    store: ReferenceStore,
    exchange: object,
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
    provider: object,
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
    provider: object,
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
    store: object,
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
