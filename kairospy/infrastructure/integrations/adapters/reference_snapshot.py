from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.application.usecases.reference.builders import (
    ReferenceSnapshot,
    catalog_from_equity_rows,
    catalog_from_market_rows,
    market_definitions_from_rows,
)
from kairospy.infrastructure.integrations.payloads.types import IntegrationParams
from kairospy.infrastructure.integrations.protocols import RawReferenceGateway


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


@dataclass(frozen=True, slots=True)
class InstrumentReferenceSnapshotProvider:
    provider: RawReferenceGateway

    def reference_snapshot(
        self,
        *,
        as_of: datetime,
        params: IntegrationParams | None = None,
    ) -> ReferenceSnapshot:
        catalog = catalog_from_market_rows(tuple(self.provider.fetch_markets(params=params)), effective_from=as_of)
        return ReferenceSnapshot(catalog, as_of)


__all__ = [
    "EquityReferenceSnapshotProvider",
    "InstrumentReferenceSnapshotProvider",
    "ReferenceSnapshot",
    "catalog_from_equity_rows",
    "catalog_from_market_rows",
    "market_definitions_from_rows",
]
