from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.application.runtime.protocol.lines import RuntimeEventLine, close_event_line
from kairospy.application.runtime.services import DataSubscription, MarketDataService, MarketDataSubscriptionSpec
from kairospy.core.views import ViewFieldSchema, ViewSchema
from kairospy.infrastructure.data import DataStore

from kairospy.application.service.domain.market import HistoricalMarketDataClient, MarketDataOperationsService, MarketDataResolver, MarketDataSpec
from kairospy.application.service.domain.market.sources import IterableMarketEventSource


@dataclass(frozen=True, slots=True)
class MarketDataServiceView:
    source: str
    subscription_count: int = 0
    subscriptions: tuple[DataSubscription, ...] = ()


class BacktestMarketDataService(MarketDataOperationsService, MarketDataService):
    key = "market.service"
    schema = ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("source", "market service backing source", "runtime state", "market data service"),
            ViewFieldSchema("subscription_count", "active subscription count", "runtime state", "market data service"),
            ViewFieldSchema("subscriptions", "active subscription specs", "runtime state", "market data service"),
        ),
        mutability="runtime_writable",
        evidence="runtime market data service",
    )

    def __init__(
        self,
        store: DataStore,
        *,
        resolver: MarketDataResolver | None = None,
        source: RuntimeEventLine | None = None,
    ) -> None:
        super().__init__(store, resolver=resolver)
        self.source = source
        self._subscriptions: dict[str, DataSubscription] = {}

    def source_from_store(self, spec: MarketDataSpec) -> IterableMarketEventSource:
        resolved = self.resolve(spec)
        return IterableMarketEventSource(resolved.stream_name, self.read(spec))

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        if self.source is None:
            return
        events = self.source.events()
        try:
            async for event in events:
                yield event
        finally:
            await close_event_line(events)

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        subscription = DataSubscription(spec.key, spec)
        self._subscriptions[subscription.key] = subscription
        return subscription

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        key = subscription if isinstance(subscription, str) else subscription.key
        self._subscriptions.pop(key, None)

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return tuple(self._subscriptions[key] for key in sorted(self._subscriptions))

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def view(self) -> MarketDataServiceView:
        subscriptions = self.subscriptions()
        return MarketDataServiceView(
            source=type(self.store).__name__,
            subscription_count=len(subscriptions),
            subscriptions=subscriptions,
        )


__all__ = [
    "HistoricalMarketDataClient",
    "BacktestMarketDataService",
    "MarketDataServiceView",
]
