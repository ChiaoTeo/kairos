from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from kairospy.domain.views import ViewFieldSchema, ViewSchema

from .catalog import ReferenceCatalog
from .identity import AssetId, InstrumentId, ListingId, MarketId, reference_slug
from .markets import MarketRef
from .model import Asset, InstrumentDefinition, LifecycleEvent, ListingDefinition, MarketDefinition, MarketStatus


class ReferenceViewKeys:
    catalog = "reference.catalog"
    markets = "reference.markets"
    lifecycle_events = "reference.lifecycle_events"
    market_prefix = "reference.market"

    @staticmethod
    def market(market_key: object) -> str:
        return f"{ReferenceViewKeys.market_prefix}.{reference_slug(market_key)}"


class ReferenceViewSource(Protocol):
    def get(self, key: str, default: object = None) -> object:
        ...


@dataclass(frozen=True, slots=True)
class ReferenceCatalogSummaryView:
    entity_count: int = 0
    asset_count: int = 0
    instrument_count: int = 0
    listing_count: int = 0
    market_count: int = 0
    active_market_count: int = 0
    lifecycle_event_count: int = 0
    as_of: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReferenceMarketSummary:
    key: str
    market_id: MarketId | str
    instrument_id: InstrumentId | str
    listing_id: ListingId | str
    market_key: str
    venue: str
    market: str
    source_symbol: str
    status: str
    price_tick: Decimal | None = None
    amount_tick: Decimal | None = None
    price_precision: int | None = None
    amount_precision: int | None = None
    min_amount: Decimal | None = None
    min_notional: Decimal | None = None
    contract_size: Decimal | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReferenceMarketsView:
    total_count: int = 0
    active_count: int = 0
    markets: tuple[ReferenceMarketSummary, ...] = ()
    as_of: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReferenceLifecycleEventSummary:
    event_type: str
    event_time: datetime
    instrument_id: InstrumentId | str | None = None
    listing_id: ListingId | str | None = None
    market_id: MarketId | str | None = None
    venue: str = ""
    source_symbol: str = ""


@dataclass(frozen=True, slots=True)
class ReferenceLifecycleEventsView:
    total_count: int = 0
    latest: ReferenceLifecycleEventSummary | None = None
    events: tuple[ReferenceLifecycleEventSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class ReferenceResolvedMarketView:
    ref: MarketRef
    definition: MarketDefinition
    instrument: InstrumentDefinition | None = None
    listing: ListingDefinition | None = None
    base_asset: Asset | None = None
    quote_asset: Asset | None = None
    as_of: datetime | None = None


REFERENCE_CATALOG_SCHEMA = ViewSchema(
    ReferenceViewKeys.catalog,
    "reference",
    fields=(
        ViewFieldSchema("entity_count", "known reference entity count", "runtime state", "reference port"),
        ViewFieldSchema("asset_count", "known reference asset count", "runtime state", "reference port"),
        ViewFieldSchema("instrument_count", "known reference instrument count", "runtime state", "reference port"),
        ViewFieldSchema("listing_count", "known reference listing count", "runtime state", "reference port"),
        ViewFieldSchema("market_count", "known reference market count", "runtime state", "reference port"),
        ViewFieldSchema("active_market_count", "active reference market count", "as-of reference state", "reference catalog"),
        ViewFieldSchema("lifecycle_event_count", "known reference lifecycle event count", "runtime state", "reference port"),
        ViewFieldSchema("as_of", "reference catalog as-of time", "as-of", "runtime publish time"),
    ),
    mutability="runtime_writable",
    evidence="runtime reference catalog view state",
)

REFERENCE_MARKETS_SCHEMA = ViewSchema(
    ReferenceViewKeys.markets,
    "reference",
    fields=(
        ViewFieldSchema("total_count", "known active market summary count", "as-of reference state", "reference catalog"),
        ViewFieldSchema("active_count", "active trading market summary count", "as-of reference state", "reference catalog"),
        ViewFieldSchema("markets", "active market summaries", "as-of reference state", "reference catalog"),
        ViewFieldSchema("as_of", "reference market index as-of time", "as-of", "runtime publish time"),
    ),
    mutability="runtime_writable",
    evidence="runtime reference market index",
)

REFERENCE_LIFECYCLE_EVENTS_SCHEMA = ViewSchema(
    ReferenceViewKeys.lifecycle_events,
    "reference",
    fields=(
        ViewFieldSchema("total_count", "known lifecycle event count", "runtime state", "reference port"),
        ViewFieldSchema("latest", "latest lifecycle event summary", "event time", "reference lifecycle log"),
        ViewFieldSchema("events", "recent lifecycle event summaries", "event time", "reference lifecycle log"),
    ),
    mutability="runtime_writable",
    evidence="runtime reference lifecycle event index",
)


def reference_market_schema(key: str) -> ViewSchema:
    return ViewSchema(
        key,
        "reference",
        fields=(
            ViewFieldSchema("ref", "resolved runtime market reference", "as-of reference state", "reference catalog"),
            ViewFieldSchema("definition", "market definition", "as-of reference state", "reference catalog"),
            ViewFieldSchema("instrument", "instrument definition", "as-of reference state", "reference catalog"),
            ViewFieldSchema("listing", "listing definition", "as-of reference state", "reference catalog"),
            ViewFieldSchema("base_asset", "base asset definition", "as-of reference state", "reference catalog"),
            ViewFieldSchema("quote_asset", "quote asset definition", "as-of reference state", "reference catalog"),
            ViewFieldSchema("as_of", "resolved market as-of time", "as-of", "runtime publish time"),
        ),
        mutability="runtime_writable",
        evidence="runtime reference market resolution",
    )


@dataclass(frozen=True, slots=True)
class ReferenceViewReader:
    source: ReferenceViewSource

    def catalog(self) -> ReferenceCatalogSummaryView:
        value = self.source.get(ReferenceViewKeys.catalog, None)
        return value if isinstance(value, ReferenceCatalogSummaryView) else ReferenceCatalogSummaryView()

    def markets(
        self,
        *,
        venue: object | None = None,
        market: object | None = None,
        status: MarketStatus | str | None = None,
        active_only: bool = False,
    ) -> ReferenceMarketsView:
        value = self.source.get(ReferenceViewKeys.markets, None)
        if not isinstance(value, ReferenceMarketsView):
            return ReferenceMarketsView()
        markets = value.markets
        if venue is not None:
            markets = tuple(item for item in markets if item.venue.casefold() == str(venue).casefold())
        if market is not None:
            markets = tuple(item for item in markets if item.market.casefold() == str(market).casefold())
        if status is not None:
            status_text = status.value if isinstance(status, MarketStatus) else str(status)
            markets = tuple(item for item in markets if item.status == status_text)
        if active_only:
            markets = tuple(item for item in markets if item.status == MarketStatus.ACTIVE.value)
        return ReferenceMarketsView(
            total_count=len(markets),
            active_count=sum(1 for item in markets if item.status == MarketStatus.ACTIVE.value),
            markets=markets,
            as_of=value.as_of,
        )

    def resolve(
        self,
        subject: object | MarketRef,
        *,
        venue: object | None = None,
        market: object | None = None,
    ) -> MarketRef:
        return self.market(subject, venue=venue, market=market).ref

    def market(
        self,
        subject: object | MarketRef,
        *,
        venue: object | None = None,
        market: object | None = None,
    ) -> ReferenceResolvedMarketView:
        if isinstance(subject, MarketRef):
            value = self.source.get(ReferenceViewKeys.market(subject.market_key), None)
            if isinstance(value, ReferenceResolvedMarketView):
                return value
        summary = _match_market(self.markets(), subject, venue=venue, market=market)
        if summary is None:
            raise KeyError(f"unknown reference market: {subject}")
        value = self.source.get(summary.key, None)
        if not isinstance(value, ReferenceResolvedMarketView):
            raise KeyError(f"reference market view has no value: {summary.key}")
        return value

    def lifecycle_events(self) -> ReferenceLifecycleEventsView:
        value = self.source.get(ReferenceViewKeys.lifecycle_events, None)
        return value if isinstance(value, ReferenceLifecycleEventsView) else ReferenceLifecycleEventsView()


def reference_catalog_view(
    catalog: ReferenceCatalog,
    *,
    lifecycle_events: tuple[LifecycleEvent, ...] = (),
    as_of: datetime | None = None,
) -> ReferenceCatalogSummaryView:
    active_markets = () if as_of is None else catalog.list_markets(at=as_of, active_only=True)
    return ReferenceCatalogSummaryView(
        entity_count=len(catalog.entities()),
        asset_count=len(catalog.assets()),
        instrument_count=len(catalog.instruments()),
        listing_count=len(catalog.listings()),
        market_count=len(catalog.markets()),
        active_market_count=len(active_markets),
        lifecycle_event_count=len(lifecycle_events),
        as_of=as_of,
    )


def reference_markets_view(catalog: ReferenceCatalog, *, as_of: datetime) -> ReferenceMarketsView:
    markets = tuple(_market_summary(item, as_of=as_of) for item in catalog.list_markets(at=as_of))
    return ReferenceMarketsView(
        total_count=len(markets),
        active_count=sum(1 for item in markets if item.status == MarketStatus.ACTIVE.value),
        markets=markets,
        as_of=as_of,
    )


def reference_market_view(catalog: ReferenceCatalog, market: MarketDefinition, *, as_of: datetime) -> ReferenceResolvedMarketView:
    instrument = catalog.maybe_get_instrument(market.instrument_id, as_of)
    listing = catalog.maybe_get_listing(market.listing_id, as_of)
    base_asset = None if instrument is None or instrument.base_asset_id is None else catalog.maybe_get_asset(instrument.base_asset_id, as_of)
    quote_asset = None if instrument is None or instrument.quote_asset_id is None else catalog.maybe_get_asset(instrument.quote_asset_id, as_of)
    return ReferenceResolvedMarketView(
        ref=MarketRef.from_definition(market),
        definition=market,
        instrument=instrument,
        listing=listing,
        base_asset=base_asset,
        quote_asset=quote_asset,
        as_of=as_of,
    )


def reference_lifecycle_events_view(events: tuple[LifecycleEvent, ...], *, limit: int = 100) -> ReferenceLifecycleEventsView:
    items = tuple(_lifecycle_event_summary(item) for item in sorted(events, key=lambda item: item.event_time)[-limit:])
    return ReferenceLifecycleEventsView(total_count=len(events), latest=items[-1] if items else None, events=items)


def _market_summary(market: MarketDefinition, *, as_of: datetime) -> ReferenceMarketSummary:
    ref = MarketRef.from_definition(market)
    return ReferenceMarketSummary(
        key=ReferenceViewKeys.market(ref.market_key),
        market_id=market.market_id,
        instrument_id=market.instrument_id,
        listing_id=market.listing_id,
        market_key=ref.market_key,
        venue=str(market.venue),
        market=str(market.market),
        source_symbol=str(market.source_symbol),
        status=market.status.value,
        price_tick=market.price_tick,
        amount_tick=market.amount_tick,
        price_precision=market.price_precision,
        amount_precision=market.amount_precision,
        min_amount=market.min_amount,
        min_notional=market.min_notional,
        contract_size=market.contract_size,
        effective_from=market.effective_from,
        effective_to=market.effective_to,
    )


def _lifecycle_event_summary(event: LifecycleEvent) -> ReferenceLifecycleEventSummary:
    return ReferenceLifecycleEventSummary(
        event_type=event.event_type.value,
        event_time=event.event_time,
        instrument_id=event.instrument_id,
        listing_id=event.listing_id,
        market_id=event.market_id,
        venue="" if event.venue is None else str(event.venue),
        source_symbol="" if event.source_symbol is None else str(event.source_symbol),
    )


def _match_market(
    markets: ReferenceMarketsView,
    subject: object,
    *,
    venue: object | None,
    market: object | None,
) -> ReferenceMarketSummary | None:
    subject_text = str(subject).casefold()
    matches = tuple(
        item
        for item in markets.markets
        if subject_text
        in {
            item.market_key.casefold(),
            str(item.market_id).casefold(),
            str(item.instrument_id).casefold(),
            item.source_symbol.casefold(),
        }
        and (venue is None or item.venue.casefold() == str(venue).casefold())
        and (market is None or item.market.casefold() == str(market).casefold())
    )
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise KeyError(f"ambiguous reference market: {subject}")
    return None


__all__ = [
    "REFERENCE_CATALOG_SCHEMA",
    "REFERENCE_LIFECYCLE_EVENTS_SCHEMA",
    "REFERENCE_MARKETS_SCHEMA",
    "ReferenceCatalogSummaryView",
    "ReferenceLifecycleEventSummary",
    "ReferenceLifecycleEventsView",
    "ReferenceMarketSummary",
    "ReferenceMarketsView",
    "ReferenceResolvedMarketView",
    "ReferenceViewKeys",
    "ReferenceViewReader",
    "ReferenceViewSource",
    "reference_catalog_view",
    "reference_lifecycle_events_view",
    "reference_market_schema",
    "reference_market_view",
    "reference_markets_view",
]
