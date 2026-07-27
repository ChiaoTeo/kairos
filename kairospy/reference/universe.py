from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .catalog import ReferenceCatalog
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


class UniverseSelector:
    def __init__(self, catalog: ReferenceCatalog) -> None:
        self.catalog = catalog

    def select(self, name: str, *, at: datetime, query: UniverseQuery | None = None) -> Universe:
        options = query or UniverseQuery()
        markets = list(
            self.catalog.list_markets(
                at=at,
                venue=options.venue,
                market=options.market,
                status=options.status,
                active_only=False,
            )
        )
        if options.instrument_type is not None:
            expected_type = (
                options.instrument_type
                if isinstance(options.instrument_type, InstrumentType)
                else InstrumentType(str(options.instrument_type))
            )
            markets = [
                market
                for market in markets
                if self.catalog.get_instrument(market.instrument_id, at).instrument_type is expected_type
            ]
        if options.base_asset_id is not None:
            expected_base = str(options.base_asset_id)
            markets = [
                market
                for market in markets
                if str(self.catalog.get_instrument(market.instrument_id, at).base_asset_id) == expected_base
            ]
        if options.quote_asset_id is not None:
            expected_quote = str(options.quote_asset_id)
            markets = [
                market
                for market in markets
                if str(self.catalog.get_instrument(market.instrument_id, at).quote_asset_id) == expected_quote
            ]
        if options.symbols:
            symbols = {item.casefold() for item in options.symbols}
            markets = [market for market in markets if market.source_symbol.casefold() in symbols]
        markets = sorted(markets, key=lambda item: str(item.market_id))
        if options.limit is not None:
            markets = markets[: options.limit]
        return Universe(name, tuple(markets), at)


__all__ = ["Universe", "UniverseQuery", "UniverseSelector"]
