from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Protocol

from kairospy.domain.market import Bar, MarketEvent, MarketSelector, Quote
from kairospy.domain.reference import MarketRef, ReferenceCatalog
from kairospy.infrastructure.integrations.application.connections import IntegrationConnection


@dataclass(frozen=True, slots=True)
class ConnectionMarketSubscriptionRequest:
    market: MarketRef
    selector: MarketSelector
    identity: str
    params: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.identity.strip():
            raise ValueError("market subscription identity is required")
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


class MarketDataConnection(IntegrationConnection, Protocol):
    """Business methods exposed by a market-data request connection."""
    def latest_quote(self, symbol: str) -> Quote | None: ...

    def bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 1000,
    ) -> Iterable[Bar]: ...



class ReferenceDataConnection(IntegrationConnection, Protocol):
    def catalog(self, *, as_of: datetime, market: str | None = None) -> ReferenceCatalog: ...


class MarketStreamConnection(IntegrationConnection, Protocol):
    async def subscribe(self, request: ConnectionMarketSubscriptionRequest) -> "MarketStreamSubscription": ...
    async def unsubscribe(self, subscription_id: str) -> None: ...


class MarketStreamSubscription(Protocol):
    subscription_id: str

    def events(self) -> AsyncIterator[MarketEvent]: ...


__all__ = ["ConnectionMarketSubscriptionRequest", "MarketDataConnection", "ReferenceDataConnection", "MarketStreamConnection", "MarketStreamSubscription"]
