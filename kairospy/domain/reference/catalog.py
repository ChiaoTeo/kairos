from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import TypeVar

from .identity import AssetId, FinancialProductId, InstrumentId, ListingId, MarketId
from .model import (
    Asset,
    FinancialProductDefinition,
    Entity,
    InstrumentDefinition,
    FinancialProductStatus,
    FinancialProductType,
    LifecycleEvent,
    LifecycleEventType,
    ListingDefinition,
    MarketDefinition,
    MarketStatus,
)


T = TypeVar("T")


class ReferenceCatalog:
    def __init__(
        self,
        *,
        entities: Iterable[Entity] = (),
        assets: Iterable[Asset] = (),
        instruments: Iterable[InstrumentDefinition] = (),
        listings: Iterable[ListingDefinition] = (),
        markets: Iterable[MarketDefinition] = (),
        financial_products: Iterable[FinancialProductDefinition] = (),
    ) -> None:
        self._entities: dict[str, list[Entity]] = {}
        self._assets: dict[str, list[Asset]] = {}
        self._instruments: dict[str, list[InstrumentDefinition]] = {}
        self._listings: dict[str, list[ListingDefinition]] = {}
        self._markets: dict[str, list[MarketDefinition]] = {}
        self._financial_products: dict[str, list[FinancialProductDefinition]] = {}
        for item in entities:
            self.add_entity(item)
        for item in assets:
            self.add_asset(item)
        for item in instruments:
            self.add_instrument(item)
        for item in listings:
            self.add_listing(item)
        for item in markets:
            self.add_market(item)
        for item in financial_products:
            self.add_financial_product(item)

    def add_entity(self, entity: Entity) -> Entity:
        _add_version(self._entities, str(entity.entity_id), entity)
        return entity

    def add_asset(self, asset: Asset) -> Asset:
        _add_version(self._assets, str(asset.asset_id), asset)
        return asset

    def add_instrument(self, instrument: InstrumentDefinition) -> InstrumentDefinition:
        _add_version(self._instruments, str(instrument.instrument_id), instrument)
        return instrument

    def add_listing(self, listing: ListingDefinition) -> ListingDefinition:
        _add_version(self._listings, str(listing.listing_id), listing)
        return listing

    def add_market(self, market: MarketDefinition) -> MarketDefinition:
        _add_version(self._markets, str(market.market_id), market)
        return market

    def add_financial_product(self, product: FinancialProductDefinition) -> FinancialProductDefinition:
        _add_version(self._financial_products, str(product.product_id), product)
        return product

    def get_entity(self, entity_id: str, at: datetime) -> Entity:
        return _get_active(self._entities, str(entity_id), at)

    def get_asset(self, asset_id: AssetId | str, at: datetime) -> Asset:
        return _get_active(self._assets, str(asset_id), at)

    def get_instrument(self, instrument_id: InstrumentId | str, at: datetime) -> InstrumentDefinition:
        return _get_active(self._instruments, str(instrument_id), at)

    def get_listing(self, listing_id: ListingId | str, at: datetime) -> ListingDefinition:
        return _get_active(self._listings, str(listing_id), at)

    def get_market(self, market_id: MarketId | str, at: datetime) -> MarketDefinition:
        return _get_active(self._markets, str(market_id), at)

    def get_financial_product(self, product_id: FinancialProductId | str, at: datetime) -> FinancialProductDefinition:
        return _get_active(self._financial_products, str(product_id), at)

    def maybe_get_entity(self, entity_id: str, at: datetime) -> Entity | None:
        return _maybe_get_active(self._entities, str(entity_id), at)

    def maybe_get_asset(self, asset_id: AssetId | str, at: datetime) -> Asset | None:
        return _maybe_get_active(self._assets, str(asset_id), at)

    def maybe_get_instrument(self, instrument_id: InstrumentId | str, at: datetime) -> InstrumentDefinition | None:
        return _maybe_get_active(self._instruments, str(instrument_id), at)

    def maybe_get_listing(self, listing_id: ListingId | str, at: datetime) -> ListingDefinition | None:
        return _maybe_get_active(self._listings, str(listing_id), at)

    def maybe_get_market(self, market_id: MarketId | str, at: datetime) -> MarketDefinition | None:
        return _maybe_get_active(self._markets, str(market_id), at)

    def maybe_get_financial_product(self, product_id: FinancialProductId | str, at: datetime) -> FinancialProductDefinition | None:
        return _maybe_get_active(self._financial_products, str(product_id), at)

    def list_financial_products(
        self,
        *,
        at: datetime,
        product_type: FinancialProductType | str | None = None,
        asset_id: AssetId | str | None = None,
        status: FinancialProductStatus | str | None = None,
    ) -> tuple[FinancialProductDefinition, ...]:
        values = _active_values(self._financial_products, at)
        if product_type is not None:
            expected = getattr(product_type, "value", str(product_type))
            values = [item for item in values if item.product_type.value == expected]
        if asset_id is not None:
            values = [item for item in values if str(item.asset_id) == str(asset_id)]
        if status is not None:
            expected = getattr(status, "value", str(status))
            values = [item for item in values if item.status.value == expected]
        return tuple(sorted(values, key=lambda item: str(item.product_id)))

    def active_listings(
        self,
        instrument_id: InstrumentId | str | None = None,
        at: datetime | None = None,
        *,
        venue: str | None = None,
    ) -> tuple[ListingDefinition, ...]:
        if at is None:
            raise ValueError("active_listings requires an as-of time")
        values = _active_values(self._listings, at)
        if instrument_id is not None:
            values = [item for item in values if str(item.instrument_id) == str(instrument_id)]
        if venue is not None:
            values = [item for item in values if str(item.venue) == str(venue)]
        return tuple(sorted(values, key=lambda item: str(item.listing_id)))

    def list_markets(
        self,
        *,
        at: datetime,
        venue: str | None = None,
        market: str | None = None,
        status: MarketStatus | str | None = None,
        active_only: bool = False,
    ) -> tuple[MarketDefinition, ...]:
        values = _active_values(self._markets, at)
        if venue is not None:
            values = [item for item in values if str(item.venue) == str(venue)]
        if market is not None:
            values = [item for item in values if str(item.market) == str(market)]
        if status is not None:
            expected = status if isinstance(status, MarketStatus) else MarketStatus(str(status))
            values = [item for item in values if item.status is expected]
        if active_only:
            values = [item for item in values if item.status is MarketStatus.ACTIVE]
        return tuple(sorted(values, key=lambda item: str(item.market_id)))

    def resolve_market(
        self,
        source_symbol: str,
        *,
        venue: str,
        market: str | None = None,
        at: datetime,
    ) -> MarketDefinition:
        candidates = [
            item for item in self.list_markets(at=at, venue=venue, market=market)
            if str(item.source_symbol).casefold() == source_symbol.casefold()
        ]
        if not candidates:
            raise KeyError(f"unknown market: {venue}:{market or '*'}:{source_symbol}")
        if len(candidates) > 1:
            raise KeyError(f"ambiguous market: {venue}:{market or '*'}:{source_symbol}")
        return candidates[0]

    def supersede_listing(self, listing: ListingDefinition, effective_at: datetime) -> None:
        current = self.get_listing(listing.listing_id, effective_at)
        self._end_version(self._listings, str(listing.listing_id), current, effective_at)
        self.add_listing(replace(listing, effective_from=effective_at))

    def supersede_entity(self, entity: Entity, effective_at: datetime) -> None:
        current = self.get_entity(entity.entity_id, effective_at)
        self._end_version(self._entities, str(entity.entity_id), current, effective_at)
        self.add_entity(replace(entity, effective_from=effective_at))

    def supersede_asset(self, asset: Asset, effective_at: datetime) -> None:
        current = self.get_asset(asset.asset_id, effective_at)
        self._end_version(self._assets, str(asset.asset_id), current, effective_at)
        self.add_asset(replace(asset, effective_from=effective_at))

    def supersede_instrument(self, instrument: InstrumentDefinition, effective_at: datetime) -> None:
        current = self.get_instrument(instrument.instrument_id, effective_at)
        self._end_version(self._instruments, str(instrument.instrument_id), current, effective_at)
        self.add_instrument(replace(instrument, effective_from=effective_at))

    def supersede_market(self, market: MarketDefinition, effective_at: datetime) -> None:
        current = self.get_market(market.market_id, effective_at)
        self._end_version(self._markets, str(market.market_id), current, effective_at)
        self.add_market(replace(market, effective_from=effective_at))

    def supersede_financial_product(self, product: FinancialProductDefinition, effective_at: datetime) -> None:
        current = self.get_financial_product(product.product_id, effective_at)
        self._end_version(self._financial_products, str(product.product_id), current, effective_at)
        self.add_financial_product(replace(product, effective_from=effective_at))

    def snapshot(self, *, at: datetime) -> Mapping[str, object]:
        return {
            "assets": {str(item.asset_id): item for item in _active_values(self._assets, at)},
            "instruments": {str(item.instrument_id): item for item in _active_values(self._instruments, at)},
            "listings": {str(item.listing_id): item for item in _active_values(self._listings, at)},
            "markets": {str(item.market_id): item for item in _active_values(self._markets, at)},
            "financial_products": {str(item.product_id): item for item in _active_values(self._financial_products, at)},
        }

    def entities(self) -> tuple[Entity, ...]:
        return tuple(item for versions in self._entities.values() for item in versions)

    def assets(self) -> tuple[Asset, ...]:
        return tuple(item for versions in self._assets.values() for item in versions)

    def instruments(self) -> tuple[InstrumentDefinition, ...]:
        return tuple(item for versions in self._instruments.values() for item in versions)

    def listings(self) -> tuple[ListingDefinition, ...]:
        return tuple(item for versions in self._listings.values() for item in versions)

    def markets(self) -> tuple[MarketDefinition, ...]:
        return tuple(item for versions in self._markets.values() for item in versions)

    def financial_products(self) -> tuple[FinancialProductDefinition, ...]:
        return tuple(item for versions in self._financial_products.values() for item in versions)

    @staticmethod
    def diff_markets(
        previous: Iterable[MarketDefinition],
        current: Iterable[MarketDefinition],
        *,
        event_time: datetime,
    ) -> tuple[LifecycleEvent, ...]:
        before = {str(item.market_id): item for item in previous}
        after = {str(item.market_id): item for item in current}
        events: list[LifecycleEvent] = []
        for market_id in sorted(after.keys() - before.keys()):
            item = after[market_id]
            events.append(_market_event(LifecycleEventType.LISTED, item, event_time=event_time))
        for market_id in sorted(before.keys() - after.keys()):
            item = before[market_id]
            events.append(_market_event(LifecycleEventType.DELISTED, item, event_time=event_time))
        for market_id in sorted(before.keys() & after.keys()):
            old, new = before[market_id], after[market_id]
            if str(old.source_symbol) != str(new.source_symbol):
                events.append(
                    _market_event(
                        LifecycleEventType.SYMBOL_CHANGED,
                        new,
                        event_time=event_time,
                        previous={"symbol": str(old.source_symbol)},
                        current={"symbol": str(new.source_symbol)},
                    )
                )
            if old.status != new.status:
                events.append(
                    _market_event(
                        LifecycleEventType.STATUS_CHANGED,
                        new,
                        event_time=event_time,
                        previous={"status": old.status.value},
                        current={"status": new.status.value},
                    )
                )
        return tuple(events)

    @staticmethod
    def _end_version(store: dict[str, list[T]], key: str, current: T, effective_at: datetime) -> None:
        versions = store[key]
        versions[versions.index(current)] = replace(current, effective_to=effective_at)  # type: ignore[arg-type]


def _add_version(store: dict[str, list[T]], key: str, value: T) -> None:
    versions = store.setdefault(key, [])
    start = getattr(value, "effective_from")
    end = getattr(value, "effective_to")
    if any(_overlaps(start, end, getattr(item, "effective_from"), getattr(item, "effective_to")) for item in versions):
        raise ValueError(f"overlapping reference versions for {key}")
    versions.append(value)
    versions.sort(key=lambda item: getattr(item, "effective_from"))


def _get_active(store: dict[str, list[T]], key: str, at: datetime) -> T:
    value = _maybe_get_active(store, key, at)
    if value is not None:
        return value
    raise KeyError(f"no active reference for {key} at {at.isoformat()}")


def _maybe_get_active(store: dict[str, list[T]], key: str, at: datetime) -> T | None:
    for item in store.get(key, ()):
        if item.active_at(at):  # type: ignore[attr-defined]
            return item
    return None


def _active_values(store: dict[str, list[T]], at: datetime) -> list[T]:
    return [item for versions in store.values() for item in versions if item.active_at(at)]  # type: ignore[attr-defined]


def _overlaps(left_start: datetime, left_end: datetime | None, right_start: datetime, right_end: datetime | None) -> bool:
    return left_start < (right_end or datetime.max.replace(tzinfo=left_start.tzinfo)) and right_start < (
        left_end or datetime.max.replace(tzinfo=right_start.tzinfo)
    )


def _market_event(
    event_type: LifecycleEventType,
    market: MarketDefinition,
    *,
    event_time: datetime,
    previous: Mapping[str, object] | None = None,
    current: Mapping[str, object] | None = None,
) -> LifecycleEvent:
    return LifecycleEvent(
        event_type,
        event_time,
        instrument_id=market.instrument_id,
        listing_id=market.listing_id,
        market_id=market.market_id,
        venue=market.venue,
        source_symbol=market.source_symbol,
        previous=previous or {},
        current=current or {},
    )


__all__ = ["ReferenceCatalog"]
