"""Typed integration capabilities consumed by the Market usecase."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from kairospy.application.usecases.market.domain.specs import MarketDataSpec
from kairospy.domain.market import Bar, MarketEvent, MarketSelector, Quote
from kairospy.domain.reference import MarketRef


@dataclass(frozen=True, slots=True)
class MarketStreamConnectionRequest:
    """Business request for one market-stream connection.

    This request selects a market API family.  It does not select a symbol
    subscription; subscriptions are created after the connection exists.
    """

    market: MarketRef
    connection_id: str | None = None
    provider: str | None = None
    adapter: str | None = None
    credential: str | None = None


@dataclass(frozen=True, slots=True)
class MarketFeedSubscriptionRequest:
    market: MarketRef
    selector: MarketSelector
    identity: str
    params: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.identity.strip():
            raise ValueError("market feed subscription identity is required")
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


class MarketFeedSubscription(Protocol):
    subscription_id: str

    def events(self) -> AsyncIterator[MarketEvent]:
        ...


class MarketStreamConnection(Protocol):
    async def subscribe(self, request: MarketFeedSubscriptionRequest) -> MarketFeedSubscription:
        ...

    async def unsubscribe(self, subscription_id: str) -> None:
        ...


class MarketIntegrationRuntime(Protocol):
    """Runtime-owned factory and lifecycle port for Market connections."""

    def create_stream(self, request: MarketStreamConnectionRequest) -> MarketStreamConnection:
        ...

    def create_data(self, request: "MarketDataConnectionRequest") -> "MarketDataConnection":
        ...

    def resolve_stream(self, connection_id: str) -> MarketStreamConnection | None:
        ...

    def remove(self, connection_id: str) -> None:
        ...

    def reconnect(self, connection_id: str) -> MarketStreamConnection:
        ...


@dataclass(frozen=True, slots=True)
class MarketDataConnectionRequest:
    spec: MarketDataSpec
    connection_id: str | None = None
    params: Mapping[str, object] = field(default_factory=dict)


class MarketDataConnection(Protocol):
    def latest_quote(self, symbol: str) -> Quote | None:
        ...

    def bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
    ) -> Iterable[Bar]:
        ...

    def funding_rates(
        self,
        symbol: str,
        *,
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
        adapter_options: Mapping[str, object] | None = None,
    ) -> Iterable[object]:
        ...


__all__ = [
    "MarketFeedSubscription",
    "MarketFeedSubscriptionRequest",
    "MarketStreamConnectionRequest",
    "MarketDataConnection",
    "MarketDataConnectionRequest",
    "MarketIntegrationRuntime",
    "MarketStreamConnection",
]
