from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .identity import AssetId
from .model import InstrumentType, MarketDefinition, MarketStatus


@dataclass(frozen=True, slots=True)
class UniverseQuery:
    venue: str | None = None
    market: str | None = None
    status: MarketStatus | str | None = MarketStatus.ACTIVE
    instrument_type: InstrumentType | str | None = None
    base_asset_id: AssetId | str | None = None
    quote_asset_id: AssetId | str | None = None
    symbols: tuple[str, ...] = ()
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class Universe:
    name: str
    markets: tuple[MarketDefinition, ...]
    selected_at: datetime

    @property
    def market_ids(self) -> tuple[str, ...]:
        return tuple(str(item.market_id) for item in self.markets)

    @property
    def source_symbols(self) -> tuple[str, ...]:
        return tuple(item.source_symbol for item in self.markets)


__all__ = ["Universe", "UniverseQuery"]
