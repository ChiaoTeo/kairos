from __future__ import annotations

from datetime import datetime, timezone

from kairospy.domain.reference.catalog import ReferenceCatalog
from kairospy.domain.market.selection import MarketSelection, MarketSelectionQuery
from kairospy.domain.reference import MarketRef
from kairospy.domain.reference.model import InstrumentType
from kairospy.domain.reference.universe import Universe, UniverseQuery


class ReferenceUniverseService:
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
            markets = [market for market in markets if str(market.source_symbol).casefold() in symbols]
        markets = sorted(markets, key=lambda item: str(item.market_id))
        if options.limit is not None:
            markets = markets[: options.limit]
        return Universe(name, tuple(markets), at)

    def select_markets(self, query: MarketSelectionQuery) -> MarketSelection:
        as_of = query.as_of or datetime.now(timezone.utc)
        universe = self.select(
            "strategy-query",
            at=as_of,
            query=UniverseQuery(
                venue=query.venue,
                market=query.market,
                status=query.status,
                instrument_type=query.instrument_type,
                base_asset_id=query.base_asset_id,
                quote_asset_id=query.quote_asset_id,
                symbols=query.symbols,
                limit=query.limit,
            ),
        )
        return MarketSelection(
            markets=tuple(MarketRef.from_definition(item) for item in universe.markets),
            as_of=as_of,
            query=query,
        )


UniverseSelector = ReferenceUniverseService


__all__ = ["ReferenceUniverseService", "UniverseSelector"]
