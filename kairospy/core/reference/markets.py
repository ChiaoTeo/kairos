from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .catalog import ReferenceCatalog
from .identity import reference_slug
from .model import MarketDefinition


@dataclass(frozen=True, slots=True)
class SymbolRef:
    symbol: str
    venue: str | None = None
    market: str | None = None

    @classmethod
    def parse(
        cls,
        value: object,
        *,
        venue: str | None = None,
        market: str | None = None,
    ) -> "SymbolRef":
        text = str(value).strip()
        if not text:
            raise ValueError("symbol reference cannot be empty")
        parts = text.split(":")
        if len(parts) >= 3:
            parsed_venue, parsed_market = parts[0], parts[1]
            symbol = ":".join(parts[2:])
            return cls(symbol.strip(), parsed_venue.strip() or venue, parsed_market.strip() or market)
        return cls(text, venue, market)


@dataclass(frozen=True, slots=True)
class MarketRef:
    market_id: str
    instrument_id: str
    market_key: str
    venue: str
    market: str
    source_symbol: str

    @classmethod
    def from_definition(cls, market: MarketDefinition) -> "MarketRef":
        return cls(
            market_id=str(market.market_id),
            instrument_id=str(market.instrument_id),
            market_key=_market_key(market.venue, market.market, market.source_symbol),
            venue=market.venue,
            market=market.market,
            source_symbol=market.source_symbol,
        )

    @classmethod
    def ephemeral(cls, *, venue: str, market: str, source_symbol: str) -> "MarketRef":
        venue = _required_text(venue, "venue")
        market = _required_text(market, "market")
        source_symbol = _required_text(source_symbol, "source_symbol")
        base, quote = _split_symbol(source_symbol)
        instrument_id = (
            f"instrument:{reference_slug(market)}:{reference_slug(base)}:{reference_slug(quote)}"
            if base and quote
            else f"instrument:{reference_slug(market)}:{reference_slug(venue)}:{reference_slug(source_symbol)}"
        )
        return cls(
            market_id=f"market:{reference_slug(venue)}:{reference_slug(market)}:{reference_slug(source_symbol)}",
            instrument_id=instrument_id,
            market_key=_market_key(venue, market, source_symbol),
            venue=venue,
            market=market,
            source_symbol=source_symbol,
        )

    def identity_fields(self) -> dict[str, object]:
        return {
            "market_id": self.market_id,
            "instrument_id": self.instrument_id,
            "market_key": self.market_key,
            "venue": self.venue,
            "market": self.market,
            "source_symbol": self.source_symbol,
        }


class MarketResolver:
    def __init__(
        self,
        catalog: ReferenceCatalog | None = None,
        *,
        as_of: datetime | None = None,
        default_venue: str | None = None,
        default_market: str | None = None,
    ) -> None:
        self.catalog = catalog
        self.as_of = as_of or datetime.now(timezone.utc)
        self.default_venue = default_venue
        self.default_market = default_market
        self._by_key: dict[str, MarketRef] = {}
        self._aliases: dict[tuple[str | None, str | None, str], str] = {}
        if catalog is not None:
            for market in catalog.list_markets(at=self.as_of):
                self.add(MarketRef.from_definition(market))

    def add(self, market: MarketRef, *aliases: object) -> MarketRef:
        self._by_key[market.market_key] = market
        keys = {
            self._key(market.source_symbol, venue=market.venue, market=market.market),
            self._key(market.source_symbol, venue=None, market=None),
            self._key(market.market_key, venue=None, market=None),
            self._key(market.market_id, venue=None, market=None),
            self._key(market.instrument_id, venue=None, market=None),
        }
        for alias in aliases:
            keys.add(self._key(alias, venue=market.venue, market=market.market))
            keys.add(self._key(alias, venue=None, market=None))
        for key in keys:
            self._aliases[key] = market.market_key
        return market

    def resolve(
        self,
        value: object | MarketRef,
        *,
        venue: str | None = None,
        market: str | None = None,
    ) -> MarketRef:
        if isinstance(value, MarketRef):
            return value
        if venue is None and market is None:
            raw_key = self._key(value, venue=None, market=None)
            raw_market_key = self._aliases.get(raw_key)
            if raw_market_key is not None:
                return self._by_key[raw_market_key]
        ref = SymbolRef.parse(
            value,
            venue=venue or self.default_venue,
            market=market or self.default_market,
        )
        key = self._key(ref.symbol, venue=ref.venue, market=ref.market)
        fallback_key = self._key(ref.symbol, venue=None, market=None)
        market_key = self._aliases.get(key)
        if market_key is None and venue is None and market is None:
            market_key = self._aliases.get(fallback_key)
        if market_key is not None:
            return self._by_key[market_key]
        if self.catalog is not None and ref.venue and ref.market:
            definition = self.catalog.resolve_market(ref.symbol, venue=ref.venue, market=ref.market, at=self.as_of)
            return self.add(MarketRef.from_definition(definition))
        if not ref.venue or not ref.market:
            raise KeyError(f"unknown market reference: {ref.symbol}")
        return self.add(MarketRef.ephemeral(venue=ref.venue, market=ref.market, source_symbol=ref.symbol))

    def broker_symbol(self, value: object | MarketRef) -> str:
        return self.resolve(value).source_symbol

    def snapshot(self) -> dict[str, object]:
        return {
            key: market.identity_fields()
            for key, market in sorted(self._by_key.items())
        }

    @staticmethod
    def _key(value: object, *, venue: str | None, market: str | None) -> tuple[str | None, str | None, str]:
        return (
            venue.strip().lower() if venue else None,
            market.strip().lower() if market else None,
            str(value).strip().lower(),
        )


def _market_key(venue: object, market: object, source_symbol: object) -> str:
    return f"{reference_slug(venue)}_{reference_slug(market)}_{reference_slug(source_symbol)}"


def _required_text(value: object, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} cannot be empty")
    return text


def _split_symbol(symbol: str) -> tuple[str | None, str | None]:
    for separator in ("/", "-", "_"):
        if separator in symbol:
            left, right = symbol.split(separator, 1)
            return left.strip() or None, right.strip() or None
    return symbol.strip() or None, None


__all__ = ["MarketRef", "MarketResolver", "SymbolRef"]
