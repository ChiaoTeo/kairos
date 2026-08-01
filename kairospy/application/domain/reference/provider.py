from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from kairospy.application.ports.reference_catalog import ReferenceCatalogSource

from .refresh import ReferenceRefreshResult, ReferenceRefreshService


@dataclass(frozen=True, slots=True)
class ReferenceDataRefreshService:
    refresh_service: ReferenceRefreshService

    def refresh(
        self,
        provider: ReferenceCatalogSource,
        *,
        as_of: datetime,
        venue: str | None = None,
        market: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> ReferenceRefreshResult:
        product = _reference_product(market=market, params=params)
        snapshot = provider.fetch_catalog(as_of=as_of, market=market, params=params)
        return self.refresh_service.refresh_snapshot(
            snapshot,
            as_of=as_of,
            venue=venue,
            market=market or _market_from_product(product),
        )


InstrumentProviderRefreshService = ReferenceDataRefreshService


@dataclass(frozen=True, slots=True)
class EquityProviderRefreshService:
    refresh_service: ReferenceRefreshService

    def refresh(
        self,
        provider: ReferenceCatalogSource,
        *,
        as_of: datetime,
        venue: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> ReferenceRefreshResult:
        snapshot = provider.fetch_catalog(as_of=as_of, market="equity", params=params)
        return self.refresh_service.refresh_snapshot(snapshot, as_of=as_of, venue=venue, market="equity")


def _reference_product(*, market: str | None, params: Mapping[str, object] | None) -> object | None:
    if market is not None:
        return market
    if params is None:
        return None
    for key in ("product", "asset_class", "market", "type"):
        if key in params:
            return params[key]
    return None


def _market_from_product(product: object | None) -> str | None:
    text = str(product or "").strip().lower()
    if text in {"equity", "stock", "stocks"}:
        return "equity"
    return None


__all__ = ["EquityProviderRefreshService", "InstrumentProviderRefreshService", "ReferenceDataRefreshService"]
