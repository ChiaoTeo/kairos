from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from kairospy.domain.reference import MarketDefinition, MarketRef, MarketResolver, ReferenceCatalog


@dataclass(frozen=True, slots=True)
class ReferenceResolutionService:
    catalog: ReferenceCatalog | None = None
    default_venue: str | None = None
    default_market: str | None = None

    def resolver(self, *, as_of: datetime | None = None) -> MarketResolver:
        if self.catalog is None:
            return MarketResolver(default_venue=self.default_venue, default_market=self.default_market)
        resolved_as_of = as_of or datetime.now(timezone.utc)
        return MarketResolver(
            self.catalog,
            as_of=resolved_as_of,
            default_venue=self.default_venue,
            default_market=self.default_market,
        )

    def resolve(
        self,
        value: object | MarketRef,
        *,
        venue: str | None = None,
        market: str | None = None,
        as_of: datetime | None = None,
    ) -> MarketRef:
        return self.resolver(as_of=as_of).resolve(value, venue=venue, market=market)

    def resolve_definition(
        self,
        source_symbol: str,
        *,
        venue: str | None = None,
        market: str | None = None,
        as_of: datetime | None = None,
    ) -> MarketDefinition:
        if self.catalog is None:
            raise RuntimeError("catalog-backed reference resolution is required")
        resolved_venue = venue or self.default_venue
        if resolved_venue is None:
            raise ValueError("venue is required")
        return self.catalog.resolve_market(
            source_symbol,
            venue=resolved_venue,
            market=market or self.default_market,
            at=as_of or datetime.now(timezone.utc),
        )

    def broker_symbol(self, value: object | MarketRef, *, as_of: datetime | None = None) -> str:
        return self.resolver(as_of=as_of).broker_symbol(value)


__all__ = ["ReferenceResolutionService"]
