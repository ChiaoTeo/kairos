from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .catalog import ReferenceCatalog
from .identity import ExchangeId, InstrumentId, MarketId, MarketTypeId, SourceSymbol, reference_slug
from .model import MarketDefinition


@dataclass(frozen=True, slots=True)
class SymbolRef:
    """User-facing symbol reference before catalog resolution.

    It accepts compact symbols such as BTC/USDT and scoped symbols such as
    binance:spot:BTC/USDT. It becomes tradable only after resolving to MarketRef.
    """

    symbol: SourceSymbol | str
    venue: ExchangeId | str | None = None
    market: MarketTypeId | str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _id(self.symbol, SourceSymbol, "symbol"))
        object.__setattr__(self, "venue", None if self.venue is None else _id(self.venue, ExchangeId, "venue"))
        object.__setattr__(self, "market", None if self.market is None else _id(self.market, MarketTypeId, "market"))

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
    """Resolved runtime market identity.

    Runtime services use this compact view after catalog lookup. It carries
    stable reference IDs plus the venue-native symbol needed by adapters.
    """

    market_id: MarketId | str
    instrument_id: InstrumentId | str
    market_key: str
    venue: ExchangeId | str
    market: MarketTypeId | str
    source_symbol: SourceSymbol | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "market_id", _id(self.market_id, MarketId, "market_id"))
        object.__setattr__(self, "instrument_id", _id(self.instrument_id, InstrumentId, "instrument_id"))
        object.__setattr__(self, "venue", _id(self.venue, ExchangeId, "venue"))
        object.__setattr__(self, "market", _id(self.market, MarketTypeId, "market"))
        object.__setattr__(self, "source_symbol", _id(self.source_symbol, SourceSymbol, "source_symbol"))

    @classmethod
    def from_definition(cls, market: MarketDefinition) -> "MarketRef":
        return cls(
            market_id=market.market_id,
            instrument_id=market.instrument_id,
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
            market_id=MarketId(f"market:{reference_slug(venue)}:{reference_slug(market)}:{reference_slug(source_symbol)}"),
            instrument_id=InstrumentId(instrument_id),
            market_key=_market_key(venue, market, source_symbol),
            venue=venue,
            market=market,
            source_symbol=source_symbol,
        )

    def identity_fields(self) -> dict[str, object]:
        return {
            "market_id": str(self.market_id),
            "instrument_id": str(self.instrument_id),
            "market_key": self.market_key,
            "venue": str(self.venue),
            "exchange_id": str(self.exchange_id),
            "market": str(self.market),
            "market_type": str(self.market_type),
            "source_symbol": str(self.source_symbol),
        }

    @property
    def exchange_id(self) -> ExchangeId:
        return self.venue

    @property
    def market_type(self) -> MarketTypeId:
        return self.market


class MarketResolver:
    def __init__(
        self,
        catalog: ReferenceCatalog | None = None,
        *,
        as_of: datetime | None = None,
        default_venue: str | None = None,
        default_market: str | None = None,
    ) -> None:
        if catalog is not None and as_of is None:
            raise ValueError("catalog-backed market resolver requires as_of")
        self.catalog = catalog
        self.as_of = as_of
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
            if self.as_of is None:
                raise RuntimeError("catalog-backed market resolver has no as_of")
            definition = self.catalog.resolve_market(str(ref.symbol), venue=str(ref.venue), market=str(ref.market), at=self.as_of)
            return self.add(MarketRef.from_definition(definition))
        if not ref.venue or not ref.market:
            raise KeyError(f"unknown market reference: {ref.symbol}")
        return self.add(MarketRef.ephemeral(venue=str(ref.venue), market=str(ref.market), source_symbol=str(ref.symbol)))

    def broker_symbol(self, value: object | MarketRef) -> str:
        return str(self.resolve(value).source_symbol)

    def snapshot(self) -> dict[str, object]:
        return {
            key: market.identity_fields()
            for key, market in sorted(self._by_key.items())
        }

    @staticmethod
    def _key(value: object, *, venue: object | None, market: object | None) -> tuple[str | None, str | None, str]:
        return (
            str(venue).strip().lower() if venue else None,
            str(market).strip().lower() if market else None,
            str(value).strip().lower(),
        )


def _market_key(venue: object, market: object, source_symbol: object) -> str:
    return f"{reference_slug(venue)}_{reference_slug(market)}_{reference_slug(source_symbol)}"


def _required_text(value: object, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} cannot be empty")
    return text


def _id(value, id_type, label: str):
    if isinstance(value, id_type):
        return value
    return id_type(_required_text(value, label))


def _split_symbol(symbol: str) -> tuple[str | None, str | None]:
    for separator in ("/", "-", "_"):
        if separator in symbol:
            left, right = symbol.split(separator, 1)
            return left.strip() or None, right.strip() or None
    return symbol.strip() or None, None


__all__ = ["MarketRef", "MarketResolver", "SymbolRef"]
