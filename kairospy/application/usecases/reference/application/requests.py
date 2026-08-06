"""Business request types for reference application use cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NotRequired, TypedDict
from collections.abc import Mapping

from kairospy.domain.reference import ReferenceCatalog


class ReferenceDriverName(StrEnum):
    ccxt = "ccxt"
    massive = "massive"


class ReferenceExchangeName(StrEnum):
    binance = "binance"
    hyperliquid = "hyperliquid"
    okx = "okx"
    okex = "okex"


class ReferenceMarketRow(TypedDict, total=False):
    """Normalized row shape accepted by the reference snapshot adapter."""

    market: str
    venue: str
    source_symbol: str
    base: NotRequired[str | None]
    quote: NotRequired[str | None]
    ticker: NotRequired[str | None]
    underlying_instrument_id: NotRequired[str | None]
    expiry: NotRequired[str | datetime | None]
    strike_price: NotRequired[str | int | float | None]
    contract_type: NotRequired[str | None]
    shares_per_contract: NotRequired[str | int | float | None]
    active: NotRequired[bool | str | None]
    status: NotRequired[str | None]
    metadata: NotRequired[Mapping[str, object]]
    raw: NotRequired[Mapping[str, object]]


@dataclass(frozen=True, slots=True)
class ReferenceCatalogRequest:
    """Request a provider snapshot for one explicitly defined scope.

    ``underlying`` is part of the snapshot scope. A provider may therefore
    return a complete catalog for one underlying without claiming that every
    other underlying is absent from the venue.
    """

    as_of: datetime
    market: str | None = None
    underlying: str | None = None


@dataclass(frozen=True, slots=True)
class ReferenceLifecycleRequest:
    ticker: str
    start: datetime
    end: datetime
    catalog: ReferenceCatalog
    venue: str | None = None


@dataclass(frozen=True, slots=True)
class ReferenceDelistRequest:
    catalog: ReferenceCatalog
    market: str


@dataclass(frozen=True, slots=True)
class ReferenceRefreshRequest:
    as_of: datetime
    venue: str | None = None
    market: str | None = None
    asset_class: str | None = None
    include_delist_schedule: bool = False


@dataclass(frozen=True, slots=True)
class ReferenceLifecycleSyncRequest:
    ticker: str
    start: datetime
    end: datetime
    venue: str | None = None


__all__ = [
    "ReferenceCatalogRequest",
    "ReferenceDelistRequest",
    "ReferenceDriverName",
    "ReferenceExchangeName",
    "ReferenceLifecycleRequest",
    "ReferenceLifecycleSyncRequest",
    "ReferenceMarketRow",
    "ReferenceRefreshRequest",
]
