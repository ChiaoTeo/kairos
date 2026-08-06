"""Shared market-selection value objects.

These types are deliberately independent of the reference usecase.  A
reference catalog can produce them and a market subscription can consume
them without either business module depending on the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

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
    underlying_instrument_id: str | None = None
    expiry_from: datetime | None = None
    expiry_to: datetime | None = None
    strike_min: Decimal | str | int | float | None = None
    strike_max: Decimal | str | int | float | None = None
    option_right: str | None = None
    limit: int | None = None
    as_of: datetime | None = None

    def __post_init__(self) -> None:
        if self.limit is not None and self.limit < 0:
            raise ValueError("market selection limit cannot be negative")
        if self.expiry_from is not None and self.expiry_to is not None and self.expiry_from > self.expiry_to:
            raise ValueError("market selection expiry range is invalid")
        if self.strike_min is not None and self.strike_max is not None and Decimal(str(self.strike_min)) > Decimal(str(self.strike_max)):
            raise ValueError("market selection strike range is invalid")
        object.__setattr__(self, "symbols", tuple(str(item).strip() for item in self.symbols if str(item).strip()))
        object.__setattr__(self, "strike_min", None if self.strike_min is None else Decimal(str(self.strike_min)))
        object.__setattr__(self, "strike_max", None if self.strike_max is None else Decimal(str(self.strike_max)))
        object.__setattr__(self, "option_right", None if self.option_right is None else str(self.option_right).strip().lower())


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
