from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from kairospy.application.usecases.reference.application.requests import ReferenceCatalogRequest
from kairospy.application.usecases.reference.application.results import ReferenceRefreshResult
from kairospy.application.usecases.reference.protocol import ReferenceCatalogSource

from .refresh import ReferenceRefreshService


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
        snapshot = provider.catalog(
            ReferenceCatalogRequest(
                as_of=as_of,
                market=market,
                underlying=_underlying(params),
            )
        )
        return self.refresh_service.refresh_snapshot(
            snapshot,
            as_of=as_of,
            venue=venue,
            market=market or _market_from_product(product),
            underlying=_underlying(params),
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
        snapshot = provider.catalog(
            ReferenceCatalogRequest(as_of=as_of, market="equity", underlying=_underlying(params))
        )
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


def _underlying(params: Mapping[str, object] | None) -> str | None:
    if params is None:
        return None
    value = params.get("underlying")
    return value if isinstance(value, str) and value.strip() else None


def _market_from_product(product: object | None) -> str | None:
    text = str(product or "").strip().lower()
    if text in {"equity", "stock", "stocks"}:
        return "equity"
    return None


__all__ = ["EquityProviderRefreshService", "InstrumentProviderRefreshService", "ReferenceDataRefreshService"]
