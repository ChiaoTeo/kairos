"""Shared market-selection value objects.

These types are deliberately independent of the reference usecase.  A
reference catalog can produce them and a market subscription can consume
them without either business module depending on the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.domain.reference import (
    AssetId,
    ExchangeId,
    InstrumentType,
    MarketRef,
    MarketStatus,
    MarketTypeId,
)


@dataclass(frozen=True, slots=True)
class MarketSelectionQuery:
    venue: ExchangeId | str | None = None
    market: MarketTypeId | str | None = None
    status: MarketStatus | str | None = MarketStatus.ACTIVE
    instrument_type: InstrumentType | str | None = None
    base_asset_id: AssetId | str | None = None
    quote_asset_id: AssetId | str | None = None
    symbols: tuple[str, ...] = ()
    limit: int | None = None
    as_of: datetime | None = None

    def __post_init__(self) -> None:
        if self.limit is not None and self.limit < 0:
            raise ValueError("market selection limit cannot be negative")
        object.__setattr__(self, "symbols", tuple(str(item).strip() for item in self.symbols if str(item).strip()))


@dataclass(frozen=True, slots=True)
class MarketSelection:
    markets: tuple[MarketRef, ...]
    as_of: datetime
    query: MarketSelectionQuery

    @property
    def market_keys(self) -> tuple[str, ...]:
        return tuple(item.market_key for item in self.markets)

    def __bool__(self) -> bool:
        return bool(self.markets)


__all__ = ["MarketSelection", "MarketSelectionQuery"]
