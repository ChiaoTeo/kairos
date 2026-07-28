from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from kairospy.application.service.domains.reference.builders import (
    ReferenceSnapshot,
    catalog_from_market_rows,
    market_definitions_from_rows,
)

from .protocols import ReferenceDataClient


@dataclass(frozen=True, slots=True)
class InstrumentReferenceSnapshotProvider:
    provider: ReferenceDataClient

    def reference_snapshot(
        self,
        *,
        as_of: datetime,
        params: Mapping[str, object] | None = None,
    ) -> ReferenceSnapshot:
        catalog = catalog_from_market_rows(tuple(self.provider.fetch_markets(params=params)), effective_from=as_of)
        return ReferenceSnapshot(catalog, as_of)


__all__ = [
    "InstrumentReferenceSnapshotProvider",
    "ReferenceSnapshot",
    "catalog_from_market_rows",
    "market_definitions_from_rows",
]
