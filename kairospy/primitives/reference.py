from __future__ import annotations

from dataclasses import dataclass

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


__all__ = [
    "ExchangeId",
    "InstrumentId",
    "ListingId",
    "MarketId",
]
