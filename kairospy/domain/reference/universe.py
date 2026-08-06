from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .identity import AssetId, ExchangeId, MarketTypeId, SourceSymbol
from .model import InstrumentType, MarketDefinition, MarketStatus


@dataclass(frozen=True, slots=True)
class UniverseQuery:
    """Selection criteria for deriving a named market universe."""

    venue: ExchangeId | str | None = None
    market: MarketTypeId | str | None = None
    status: MarketStatus | str | None = MarketStatus.ACTIVE
    instrument_type: InstrumentType | str | None = None
    base_asset_id: AssetId | str | None = None
    quote_asset_id: AssetId | str | None = None
    symbols: tuple[SourceSymbol | str, ...] = ()
    underlying_instrument_id: str | None = None
    expiry_from: datetime | None = None
    expiry_to: datetime | None = None
    strike_min: Decimal | str | int | float | None = None
    strike_max: Decimal | str | int | float | None = None
    option_right: str | None = None
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class Universe:
    """Named set of markets selected from a reference catalog at one as-of time."""

    name: str
    markets: tuple[MarketDefinition, ...]
    selected_at: datetime

    @property
    def market_ids(self) -> tuple[str, ...]:
        return tuple(str(item.market_id) for item in self.markets)

    @property
    def source_symbols(self) -> tuple[str, ...]:
        return tuple(str(item.source_symbol) for item in self.markets)


__all__ = ["Universe", "UniverseQuery"]
