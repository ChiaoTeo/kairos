from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .identity import AssetId, InstrumentId, VenueId
from .product import ProductSpec, ProductType


@dataclass(frozen=True, slots=True)
class VenueListing:
    venue_id: VenueId
    external_id: str
    symbol: str
    price_tick: Decimal
    quantity_step: Decimal
    minimum_quantity: Decimal
    minimum_notional: Decimal | None = None
    listed_at: datetime | None = None
    delisted_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.price_tick <= 0 or self.quantity_step <= 0 or self.minimum_quantity <= 0:
            raise ValueError("listing tick and quantity rules must be positive")

    def active_at(self, timestamp: datetime) -> bool:
        return (self.listed_at is None or timestamp >= self.listed_at) and (
            self.delisted_at is None or timestamp < self.delisted_at
        )


@dataclass(frozen=True, slots=True)
class InstrumentDefinition:
    instrument_id: InstrumentId
    product_type: ProductType
    symbol: str
    base_asset: AssetId | None
    quote_asset: AssetId
    product_spec: ProductSpec
    listings: tuple[VenueListing, ...]
    effective_from: datetime
    effective_to: datetime | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.effective_from.tzinfo is None or self.effective_to is not None and self.effective_to.tzinfo is None:
            raise ValueError("instrument effective timestamps must be timezone-aware")
        if not self.symbol.strip():
            raise ValueError("instrument symbol cannot be empty")
        if not self.listings:
            raise ValueError("instrument requires at least one venue listing")

    def active_at(self, timestamp: datetime) -> bool:
        return timestamp >= self.effective_from and (self.effective_to is None or timestamp < self.effective_to)

    def listing(self, venue_id: VenueId, timestamp: datetime | None = None) -> VenueListing:
        candidates = [item for item in self.listings if item.venue_id == venue_id]
        if timestamp is not None:
            candidates = [item for item in candidates if item.active_at(timestamp)]
        if len(candidates) != 1:
            raise LookupError(f"expected one listing for {self.instrument_id} on {venue_id}, got {len(candidates)}")
        return candidates[0]


@dataclass(frozen=True, slots=True)
class OptionChain:
    underlying_id: InstrumentId
    venue_id: VenueId
    exchange: str
    trading_class: str
    multiplier: Decimal
    expirations: tuple[date, ...]
    strikes: tuple[Decimal, ...]
