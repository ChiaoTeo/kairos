from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from kairospy.application.service.domain.reference.builders import ReferenceSnapshot, catalog_from_equity_rows

from .protocols import ReferenceDataClient


@dataclass(frozen=True, slots=True)
class EquityReferenceSnapshotProvider:
    provider: ReferenceDataClient

    def reference_snapshot(
        self,
        *,
        as_of: datetime,
        params: Mapping[str, object] | None = None,
    ) -> ReferenceSnapshot:
        catalog = catalog_from_equity_rows(self.provider.fetch_markets(params=params), effective_from=as_of)
        return ReferenceSnapshot(catalog, as_of)


__all__ = ["EquityReferenceSnapshotProvider", "catalog_from_equity_rows"]
