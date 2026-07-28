from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.application.runtime.protocol.lines import RuntimeEventLine, close_event_line
from kairospy.application.runtime.services import DataSubscription, MarketDataService, MarketDataSubscriptionSpec
from kairospy.core.views import ViewFieldSchema, ViewSchema


@dataclass(frozen=True, slots=True)
class PaperMarketDataServiceView:
    source: str
    subscription_count: int = 0
    subscriptions: tuple[DataSubscription, ...] = ()


class PaperMarketDataService(MarketDataService):
    key = "market.service"
    schema = ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("source", "paper market data source", "runtime state", "paper market data service"),
            ViewFieldSchema("subscription_count", "active subscription count", "runtime state", "paper market data service"),
            ViewFieldSchema("subscriptions", "active subscription specs", "runtime state", "paper market data service"),
        ),
        mutability="runtime_writable",
        evidence="runtime paper market data service",
    )

    def __init__(self, source: RuntimeEventLine, *, source_name: str = "paper") -> None:
        self.source = source
        self.source_name = source_name
        self._subscriptions: dict[str, DataSubscription] = {}

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
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

    def view(self) -> PaperMarketDataServiceView:
        subscriptions = self.subscriptions()
        return PaperMarketDataServiceView(self.source_name, len(subscriptions), subscriptions)


__all__ = ["PaperMarketDataService", "PaperMarketDataServiceView"]
