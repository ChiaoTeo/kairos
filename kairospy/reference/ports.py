from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from kairospy.identity import InstrumentId, VenueId
from kairospy.reference.catalog import ReferenceCatalog
from kairospy.reference.contracts import ProductType, ReferenceCapabilities

if TYPE_CHECKING:
    from kairospy.products.equity.corporate_actions import CashDividendEvent, SplitEvent


@dataclass(frozen=True, slots=True)
class ReferenceDataRequest:
    product_type: ProductType
    symbols: tuple[str, ...]


class ReferenceDataPort(Protocol):
    venue_id: VenueId
    capabilities: ReferenceCapabilities

    def sync(self, request: ReferenceDataRequest) -> ReferenceCatalog: ...


class CorporateActionPort(Protocol):
    venue_id: VenueId

    def corporate_actions(
        self,
        instruments: tuple[InstrumentId, ...],
        start: datetime,
        end: datetime,
    ) -> tuple[CashDividendEvent | SplitEvent, ...]: ...


__all__ = ["CorporateActionPort", "ReferenceDataPort", "ReferenceDataRequest"]
