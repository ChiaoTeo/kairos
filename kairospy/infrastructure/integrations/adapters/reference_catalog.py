from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from dataclasses import dataclass

from kairospy.application.usecases.reference.builders import catalog_from_equity_rows, catalog_from_reference_rows
from kairospy.application.usecases.reference.source import ReferenceCatalogSource
from kairospy.core.reference import ReferenceCatalog

from kairospy.infrastructure.integrations.protocols import RawReferenceGateway


@dataclass(frozen=True, slots=True)
class ReferenceCatalogAdapter(ReferenceCatalogSource):
    """Translate vendor reference rows into the domain catalog contract."""

    source: RawReferenceGateway
    default_market: str | None = None

    def fetch_catalog(
        self,
        *,
        as_of: datetime,
        market: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> ReferenceCatalog:
        resolved_market = (market or self.default_market or "").strip().lower()
        rows = self.source.fetch_markets(params=params)
        if resolved_market in {"equity", "stock", "stocks"}:
            return catalog_from_equity_rows(rows, effective_from=as_of)
        return catalog_from_reference_rows(rows, effective_from=as_of, product=market)


__all__ = ["ReferenceCatalogAdapter"]
