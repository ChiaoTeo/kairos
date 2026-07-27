from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from kairospy.reference import ReferenceRefreshResult, ReferenceRefreshService

from .equities import catalog_from_equity_rows
from .instruments import catalog_from_market_rows
from .protocols import InstrumentProvider


@dataclass(frozen=True, slots=True)
class InstrumentProviderRefreshService:
    refresh_service: ReferenceRefreshService

    def refresh(
        self,
        provider: InstrumentProvider,
        *,
        as_of: datetime,
        venue: str | None = None,
        market: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> ReferenceRefreshResult:
        snapshot = catalog_from_market_rows(provider.fetch_markets(params=params), effective_from=as_of)
        return self.refresh_service.refresh_snapshot(snapshot, as_of=as_of, venue=venue, market=market)


@dataclass(frozen=True, slots=True)
class EquityProviderRefreshService:
    refresh_service: ReferenceRefreshService

    def refresh(
        self,
        provider: InstrumentProvider,
        *,
        as_of: datetime,
        venue: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> ReferenceRefreshResult:
        snapshot = catalog_from_equity_rows(provider.fetch_markets(params=params), effective_from=as_of)
        return self.refresh_service.refresh_snapshot(snapshot, as_of=as_of, venue=venue, market="equity")


__all__ = ["EquityProviderRefreshService", "InstrumentProviderRefreshService"]
