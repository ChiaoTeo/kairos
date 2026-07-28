from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from kairospy.core.reference.catalog import ReferenceCatalog
from kairospy.core.reference.model import LifecycleEvent, ListingDefinition, MarketDefinition, MarketStatus
from .serde import (
    asset_to_primitive,
    entity_to_primitive,
    instrument_to_primitive,
    listing_to_primitive,
    market_to_primitive,
)


@dataclass(frozen=True, slots=True)
class ReferenceCatalogTransition:
    catalog: ReferenceCatalog
    events: tuple[LifecycleEvent, ...]
    previous_markets: tuple[MarketDefinition, ...]
    current_markets: tuple[MarketDefinition, ...]


def apply_catalog_snapshot(
    catalog: ReferenceCatalog,
    snapshot: ReferenceCatalog,
    *,
    as_of: datetime,
    venue: str | None = None,
    market: str | None = None,
) -> ReferenceCatalogTransition:
    previous_markets = catalog.list_markets(at=as_of, venue=venue, market=market, active_only=True)
    current_markets = snapshot.list_markets(at=as_of, venue=venue, market=market)
    events = ReferenceCatalog.diff_markets(previous_markets, current_markets, event_time=as_of)

    _merge_static_definitions(catalog, snapshot, as_of=as_of)
    for listing in snapshot.listings():
        if _in_scope_listing(listing, snapshot, as_of=as_of, venue=venue, market=market):
            _merge_listing(catalog, listing, as_of=as_of)
    for current in current_markets:
        _merge_market(catalog, current, as_of=as_of)

    current_ids = {str(item.market_id) for item in current_markets}
    for previous in previous_markets:
        if str(previous.market_id) in current_ids:
            continue
        delisted_market = replace(previous, status=MarketStatus.DELISTED, effective_from=as_of, effective_to=None)
        catalog.supersede_market(delisted_market, as_of)
        previous_listing = catalog.maybe_get_listing(previous.listing_id, as_of)
        if previous_listing is not None:
            catalog.supersede_listing(
                replace(previous_listing, status=MarketStatus.DELISTED, effective_from=as_of, effective_to=None),
                as_of,
            )
    return ReferenceCatalogTransition(catalog, events, previous_markets, current_markets)


def _merge_static_definitions(catalog: ReferenceCatalog, snapshot: ReferenceCatalog, *, as_of: datetime) -> None:
    for entity in snapshot.entities():
        existing = catalog.maybe_get_entity(str(entity.entity_id), as_of)
        if existing is None:
            catalog.add_entity(entity)
        elif _versionless(entity_to_primitive(existing)) != _versionless(entity_to_primitive(entity)):
            catalog.supersede_entity(entity, as_of)
    for asset in snapshot.assets():
        existing = catalog.maybe_get_asset(asset.asset_id, as_of)
        if existing is None:
            catalog.add_asset(asset)
        elif _versionless(asset_to_primitive(existing)) != _versionless(asset_to_primitive(asset)):
            catalog.supersede_asset(asset, as_of)
    for instrument in snapshot.instruments():
        existing = catalog.maybe_get_instrument(instrument.instrument_id, as_of)
        if existing is None:
            catalog.add_instrument(instrument)
        elif _versionless(instrument_to_primitive(existing)) != _versionless(instrument_to_primitive(instrument)):
            catalog.supersede_instrument(instrument, as_of)


def _merge_listing(catalog: ReferenceCatalog, listing: ListingDefinition, *, as_of: datetime) -> None:
    existing = catalog.maybe_get_listing(listing.listing_id, as_of)
    if existing is None:
        catalog.add_listing(listing)
        return
    if _listing_signature(existing) != _listing_signature(listing):
        catalog.supersede_listing(listing, as_of)


def _merge_market(catalog: ReferenceCatalog, market: MarketDefinition, *, as_of: datetime) -> None:
    existing = catalog.maybe_get_market(market.market_id, as_of)
    if existing is None:
        catalog.add_market(market)
        return
    if _market_signature(existing) != _market_signature(market):
        catalog.supersede_market(market, as_of)


def _in_scope_listing(
    listing: ListingDefinition,
    snapshot: ReferenceCatalog,
    *,
    as_of: datetime,
    venue: str | None,
    market: str | None,
) -> bool:
    if venue is not None and listing.venue != venue:
        return False
    return any(item.listing_id == listing.listing_id for item in snapshot.list_markets(at=as_of, venue=venue, market=market))


def _listing_signature(listing: ListingDefinition) -> Mapping[str, object]:
    return _versionless(listing_to_primitive(listing))


def _market_signature(market: MarketDefinition) -> Mapping[str, object]:
    return _versionless(market_to_primitive(market))


def _versionless(value: Mapping[str, object]) -> Mapping[str, object]:
    primitive = dict(value)
    primitive.pop("effective_from", None)
    primitive.pop("effective_to", None)
    return primitive


__all__ = ["ReferenceCatalogTransition", "apply_catalog_snapshot"]
