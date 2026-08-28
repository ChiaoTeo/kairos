from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NewType, TypeAlias

from ._text import TextValue


@dataclass(frozen=True, slots=True)
class ExchangeId(TextValue):
    """Canonical Reference exchange identity."""


@dataclass(frozen=True, slots=True)
class InstrumentId(TextValue):
    """Canonical economic instrument identity."""


@dataclass(frozen=True, slots=True)
class ListingId(TextValue):
    """Canonical venue listing identity."""


@dataclass(frozen=True, slots=True)
class MarketId(TextValue):
    """Canonical observable or tradable market identity."""


@dataclass(frozen=True, slots=True)
class AssetId(TextValue):
    """Canonical Reference asset identity."""


@dataclass(frozen=True, slots=True)
class Currency(TextValue):
    """Canonical currency code or identity."""


@dataclass(frozen=True, slots=True)
class Symbol(TextValue):
    """Canonical Reference symbol."""


@dataclass(frozen=True, slots=True)
class IssuerId(TextValue):
    """Canonical issuer identity."""


@dataclass(frozen=True, slots=True)
class MarketSegmentId(TextValue):
    """Canonical Reference market-segment identity."""


@dataclass(frozen=True, slots=True)
class TradingSessionId(TextValue):
    """Canonical trading-session identity."""


@dataclass(frozen=True, slots=True)
class TradingCalendarId(TextValue):
    """Canonical trading-calendar identity."""


@dataclass(frozen=True, slots=True)
class ReferenceSourceId(TextValue):
    """Canonical Reference source binding identity."""


ExchangeIdRead = NewType("ExchangeIdRead", str)
AssetIdRead = NewType("AssetIdRead", str)
CurrencyRead = NewType("CurrencyRead", str)
InstrumentIdRead = NewType("InstrumentIdRead", str)
ListingIdRead = NewType("ListingIdRead", str)
MarketIdRead = NewType("MarketIdRead", str)
SymbolRead = NewType("SymbolRead", str)
IssuerIdRead = NewType("IssuerIdRead", str)
MarketSegmentIdRead = NewType("MarketSegmentIdRead", str)
TradingSessionIdRead = NewType("TradingSessionIdRead", str)
TradingCalendarIdRead = NewType("TradingCalendarIdRead", str)
ReferenceSourceIdRead = NewType("ReferenceSourceIdRead", str)

# Closed Reference vocabulary is carried by native ``str`` values at runtime;
# Literal aliases preserve the Rust enum distinctions without allocating a
# parallel Python enum object for every read.
AssetClass: TypeAlias = Literal["fiat", "crypto", "equity", "unknown"]
InstrumentKind: TypeAlias = Literal[
    "equity", "spot", "perpetual", "future", "option", "index", "unknown"
]
ReferenceStatus: TypeAlias = Literal[
    "draft",
    "active",
    "trading",
    "suspended",
    "delisted",
    "inactive",
    "retired",
    "expired",
    "unknown",
]


__all__ = [
    "AssetId",
    "AssetIdRead",
    "AssetClass",
    "Currency",
    "CurrencyRead",
    "ExchangeId",
    "ExchangeIdRead",
    "InstrumentId",
    "InstrumentIdRead",
    "InstrumentKind",
    "IssuerId",
    "IssuerIdRead",
    "ListingId",
    "ListingIdRead",
    "MarketId",
    "MarketIdRead",
    "MarketSegmentId",
    "MarketSegmentIdRead",
    "ReferenceSourceId",
    "ReferenceSourceIdRead",
    "ReferenceStatus",
    "Symbol",
    "SymbolRead",
    "TradingCalendarId",
    "TradingCalendarIdRead",
    "TradingSessionId",
    "TradingSessionIdRead",
]
