from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from kairospy.domain.reference.catalog import ReferenceCatalog
from kairospy.domain.reference.model import InstrumentDefinition, LifecycleEvent, LifecycleEventType, ListingDefinition, MarketDefinition, MarketStatus

from kairospy.application.usecases.reference.domain.serde import (
    asset_to_primitive,
    entity_to_primitive,
    instrument_to_primitive,
    financial_product_to_primitive,
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
    underlying: str | None = None,
) -> ReferenceCatalogTransition:
    previous_markets = tuple(
        item
        for item in catalog.list_markets(at=as_of, venue=venue, market=market, active_only=True)
        if _in_scope_market(catalog, item, as_of=as_of, underlying=underlying)
    )
    current_markets = tuple(
        item
        for item in snapshot.list_markets(at=as_of, venue=venue, market=market)
        if _in_scope_market(snapshot, item, as_of=as_of, underlying=underlying)
    )
    _reject_unexpected_empty_snapshot(catalog, previous_markets, current_markets, as_of=as_of)
    events = _classify_terminal_events(
        ReferenceCatalog.diff_markets(previous_markets, current_markets, event_time=as_of),
        catalog,
        previous_markets,
        as_of=as_of,
    )

    _merge_static_definitions(catalog, snapshot, as_of=as_of)
    for listing in snapshot.listings():
        if _in_scope_listing(listing, snapshot, as_of=as_of, venue=venue, market=market, underlying=underlying):
            _merge_listing(catalog, listing, as_of=as_of)
    for current in current_markets:
        _merge_market(catalog, current, as_of=as_of)

    current_ids = {str(item.market_id) for item in current_markets}
    for previous in previous_markets:
        if str(previous.market_id) in current_ids:
            continue
        terminal_status = _terminal_status(catalog, previous, as_of=as_of)
        terminal_market = replace(previous, status=terminal_status, effective_from=as_of, effective_to=None)
        catalog.supersede_market(terminal_market, as_of)
        previous_listing = catalog.maybe_get_listing(previous.listing_id, as_of)
        if previous_listing is not None:
            catalog.supersede_listing(
                replace(previous_listing, status=terminal_status, effective_from=as_of, effective_to=None),
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
    for product in snapshot.financial_products():
        existing = catalog.maybe_get_financial_product(product.product_id, as_of)
        if existing is None:
            catalog.add_financial_product(product)
        elif _versionless(financial_product_to_primitive(existing)) != _versionless(financial_product_to_primitive(product)):
            catalog.supersede_financial_product(product, as_of)


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
    underlying: str | None,
) -> bool:
    if venue is not None and str(listing.venue) != str(venue):
        return False
    return any(
        item.listing_id == listing.listing_id
        and _in_scope_market(snapshot, item, as_of=as_of, underlying=underlying)
        for item in snapshot.list_markets(at=as_of, venue=venue, market=market)
    )


def _in_scope_market(
    catalog: ReferenceCatalog,
    market: MarketDefinition,
    *,
    as_of: datetime,
    underlying: str | None,
) -> bool:
    if underlying is None:
        return True
    instrument = catalog.maybe_get_instrument(market.instrument_id, as_of)
    return instrument is not None and _underlying_matches(instrument, underlying)


def _underlying_matches(instrument: InstrumentDefinition, underlying: str) -> bool:
    expected = str(underlying).strip().casefold()
    if not expected:
        return True
    value = str(instrument.underlying_instrument_id or "").casefold()
    return value == expected or value.rsplit(":", 1)[-1] == expected


def _terminal_status(catalog: ReferenceCatalog, market: MarketDefinition, *, as_of: datetime) -> MarketStatus:
    instrument = catalog.maybe_get_instrument(market.instrument_id, as_of)
    if instrument is not None and instrument.expiry is not None and instrument.expiry <= as_of:
        return MarketStatus.EXPIRED
    return MarketStatus.DELISTED


def _classify_terminal_events(
    events: tuple[LifecycleEvent, ...],
    catalog: ReferenceCatalog,
    previous_markets: tuple[MarketDefinition, ...],
    *,
    as_of: datetime,
) -> tuple[LifecycleEvent, ...]:
    previous_by_id = {str(item.market_id): item for item in previous_markets}
    classified: list[LifecycleEvent] = []
    for event in events:
        market = previous_by_id.get(str(event.market_id))
        if event.event_type is not LifecycleEventType.DELISTED or market is None:
            classified.append(event)
            continue
        status = _terminal_status(catalog, market, as_of=as_of)
        if status is MarketStatus.EXPIRED:
            classified.append(replace(event, event_type=LifecycleEventType.EXPIRED, current={"status": status.value}))
        else:
            classified.append(event)
    return tuple(classified)


def _reject_unexpected_empty_snapshot(
    catalog: ReferenceCatalog,
    previous_markets: tuple[MarketDefinition, ...],
    current_markets: tuple[MarketDefinition, ...],
    *,
    as_of: datetime,
) -> None:
    """Keep the last catalog when a live scope suddenly becomes empty.

    Empty is a valid terminal state only when the scope had no active markets
    or all its contracts had already expired. This check runs before the
    transition mutates the catalog, so a rejected refresh is side-effect free.
    """
    if current_markets or not previous_markets:
        return
    live = tuple(
        item
        for item in previous_markets
        if (
            (instrument := catalog.maybe_get_instrument(item.instrument_id, as_of)) is None
            or instrument.expiry is None
            or instrument.expiry > as_of
        )
    )
    if live:
        raise ValueError(
            "reference provider returned an empty snapshot for an active scope; "
            f"refusing to mark {len(live)} active markets as delisted"
        )


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
