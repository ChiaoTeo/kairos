from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

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


ExchangeIdRead = NewType("ExchangeIdRead", str)
AssetIdRead = NewType("AssetIdRead", str)
CurrencyRead = NewType("CurrencyRead", str)
InstrumentIdRead = NewType("InstrumentIdRead", str)
ListingIdRead = NewType("ListingIdRead", str)
MarketIdRead = NewType("MarketIdRead", str)
SymbolRead = NewType("SymbolRead", str)


__all__ = [
    "AssetId",
    "AssetIdRead",
    "Currency",
    "CurrencyRead",
    "ExchangeId",
    "ExchangeIdRead",
    "InstrumentId",
    "InstrumentIdRead",
    "ListingId",
    "ListingIdRead",
    "MarketId",
    "MarketIdRead",
    "Symbol",
    "SymbolRead",
]
