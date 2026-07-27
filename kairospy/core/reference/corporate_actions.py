from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from .catalog import ReferenceCatalog
from .identity import InstrumentId, ListingId, MarketId
from .model import LifecycleEvent, LifecycleEventType


class CorporateActionService:
    def __init__(self, catalog: ReferenceCatalog) -> None:
        self.catalog = catalog

    def apply_symbol_change(
        self,
        *,
        listing_id: ListingId | str,
        market_id: MarketId | str,
        new_symbol: str,
        effective_at: datetime,
    ) -> LifecycleEvent:
        listing = self.catalog.get_listing(listing_id, effective_at)
        market = self.catalog.get_market(market_id, effective_at)
        old_symbol = listing.trading_symbol
        self.catalog.supersede_listing(replace(listing, trading_symbol=new_symbol, effective_from=effective_at), effective_at)
        self.catalog.supersede_market(replace(market, source_symbol=new_symbol, effective_from=effective_at), effective_at)
        return LifecycleEvent(
            LifecycleEventType.SYMBOL_CHANGED,
            effective_at,
            instrument_id=listing.instrument_id,
            listing_id=listing.listing_id,
            market_id=market.market_id,
            venue=listing.venue,
            source_symbol=new_symbol,
            previous={"symbol": old_symbol},
            current={"symbol": new_symbol},
        )

    def split(
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
        return LifecycleEvent(
            LifecycleEventType.SPLIT,
            effective_at,
            instrument_id=instrument.instrument_id,
            current={"ratio": str(value)},
        )

    def dividend(
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
        return LifecycleEvent(
            LifecycleEventType.DIVIDEND,
            ex_date,
            instrument_id=instrument.instrument_id,
            current={
                "amount_per_share": str(amount),
                "currency": currency,
                "pay_date": pay_date.isoformat(),
            },
        )


__all__ = ["CorporateActionService"]
