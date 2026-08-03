from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from kairospy.domain.reference.catalog import ReferenceCatalog
from kairospy.domain.reference.identity import InstrumentId, MarketId
from kairospy.domain.reference.model import LifecycleEvent, LifecycleEventType

from .catalogs import ReferenceCatalogService


class ReferenceLifecycleService:
    def __init__(self, catalog: ReferenceCatalog | ReferenceCatalogService) -> None:
        self._catalog_service = catalog if isinstance(catalog, ReferenceCatalogService) else None
        self.catalog = catalog.catalog() if isinstance(catalog, ReferenceCatalogService) else catalog

    def change_symbol(
        self,
        *,
        market_id: MarketId | str,
        new_symbol: str,
        effective_at: datetime,
    ) -> LifecycleEvent:
        market = self.catalog.get_market(market_id, effective_at)
        listing = self.catalog.get_listing(market.listing_id, effective_at)
        old_symbol = str(listing.trading_symbol)
        self.catalog.supersede_listing(replace(listing, trading_symbol=new_symbol, effective_from=effective_at), effective_at)
        self.catalog.supersede_market(replace(market, source_symbol=new_symbol, effective_from=effective_at), effective_at)
        return self._record(
            LifecycleEvent(
                LifecycleEventType.SYMBOL_CHANGED,
                effective_at,
                instrument_id=market.instrument_id,
                listing_id=market.listing_id,
                market_id=market.market_id,
                venue=market.venue,
                source_symbol=new_symbol,
                previous={"symbol": old_symbol},
                current={"symbol": new_symbol},
            )
        )

    def record_split(
        self,
        *,
        instrument_id: InstrumentId | str,
        effective_at: datetime,
        ratio: Decimal | str | int,
    ) -> LifecycleEvent:
        instrument = self.catalog.get_instrument(instrument_id, effective_at)
        value = Decimal(str(ratio))
        if value <= 0 or value == 1:
            raise ValueError("split ratio must be positive and not one")
        return self._record(
            LifecycleEvent(
                LifecycleEventType.SPLIT,
                effective_at,
                instrument_id=instrument.instrument_id,
                current={"ratio": str(value)},
            )
        )

    def record_dividend(
        self,
        *,
        instrument_id: InstrumentId | str,
        ex_date: datetime,
        pay_date: datetime,
        amount_per_share: Decimal | str | int,
        currency: str = "USD",
    ) -> LifecycleEvent:
        instrument = self.catalog.get_instrument(instrument_id, ex_date)
        amount = Decimal(str(amount_per_share))
        if amount <= 0:
            raise ValueError("dividend amount must be positive")
        return self._record(
            LifecycleEvent(
                LifecycleEventType.DIVIDEND,
                ex_date,
                instrument_id=instrument.instrument_id,
                current={
                    "amount_per_share": str(amount),
                    "currency": currency,
                    "pay_date": pay_date.isoformat(),
                },
            )
        )

    def _record(self, event: LifecycleEvent) -> LifecycleEvent:
        if self._catalog_service is not None:
            self._catalog_service.save_catalog(self.catalog)
            self._catalog_service.append_events((event,))
        return event


__all__ = ["ReferenceLifecycleService"]
