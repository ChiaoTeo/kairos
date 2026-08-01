from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from kairospy.core.reference import Asset, AssetId, AssetType, EntityId, LifecycleEvent
from kairospy.core.reference.identity import reference_slug
from kairospy.application.usecases.reference.store import ReferenceStore
from kairospy.application.usecases.reference.source import ReferenceCatalogSource

from .provider import EquityProviderRefreshService, ReferenceDataRefreshService
from .refresh import ReferenceRefreshResult, ReferenceRefreshService


@dataclass(frozen=True, slots=True)
class ReferenceProviderRefreshResult:
    refresh: ReferenceRefreshResult
    scheduled_events: tuple[LifecycleEvent, ...] = ()


ReferenceSourceRefreshResult = ReferenceProviderRefreshResult


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
    catalog = store.load_catalog()
    resolved_asset_type = asset_type if isinstance(asset_type, AssetType) else AssetType(str(asset_type))
    resolved_asset_id = AssetId(str(asset_id)) if asset_id is not None else _asset_id(resolved_asset_type, symbol)
    asset = Asset(
        resolved_asset_id,
        resolved_asset_type,
        _required_text(symbol, "symbol"),
        name=_optional_text(name),
        issuer_id=None if issuer_id is None else EntityId(str(issuer_id)),
        effective_from=effective_from,
        metadata=metadata or {},
    )
    current = catalog.maybe_get_asset(resolved_asset_id, effective_from)
    if current is not None:
        if not replace_existing:
            raise ValueError(f"asset already exists at {effective_from.isoformat()}: {resolved_asset_id}")
        catalog.supersede_asset(asset, effective_from)
    else:
        catalog.add_asset(asset)
    store.save_catalog(catalog)
    return asset


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
    provider: ReferenceCatalogSource,
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
    exchange: ReferenceCatalogSource,
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


def _asset_id(asset_type: AssetType, symbol: str) -> AssetId:
    return AssetId(f"asset:{asset_type.value}:{reference_slug(_required_text(symbol, 'symbol'))}")


def _required_text(value: object, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "ReferenceProviderRefreshResult",
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
