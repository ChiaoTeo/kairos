from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.application.domain.reference.builders import ReferenceSnapshot, catalog_from_equity_rows

from .protocols import RawReferenceGateway
from kairospy.infrastructure.integrations.types import IntegrationParams


@dataclass(frozen=True, slots=True)
class EquityReferenceSnapshotProvider:
    provider: RawReferenceGateway

    def reference_snapshot(
        self,
        *,
        as_of: datetime,
        params: IntegrationParams | None = None,
    ) -> ReferenceSnapshot:
        catalog = catalog_from_equity_rows(self.provider.fetch_markets(params=params), effective_from=as_of)
        return ReferenceSnapshot(catalog, as_of)


__all__ = ["EquityReferenceSnapshotProvider", "catalog_from_equity_rows"]
