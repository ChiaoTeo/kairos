from __future__ import annotations

from collections.abc import AsyncIterator

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.application.runtime.protocol.lines import RuntimeEventLine, close_event_line
from kairospy.application.runtime.ports import MarketDataPort
from kairospy.application.service.domain.market import MarketDataOperationsService, MarketDataResolver, MarketDataSpec
from kairospy.application.service.domain.market.sources import IterableMarketEventSource
from kairospy.infrastructure.data import DataStore

from .common import MarketSubscriptionState, RuntimeMarketDataServiceView


class ReplayMarketDataService(MarketDataOperationsService, MarketSubscriptionState, MarketDataPort):
    def __init__(
        self,
        store: DataStore,
        *,
        resolver: MarketDataResolver | None = None,
        source: RuntimeEventLine | None = None,
    ) -> None:
        MarketDataOperationsService.__init__(self, store, resolver=resolver)
        MarketSubscriptionState.__init__(self)
        self.source = source

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

    def view(self) -> RuntimeMarketDataServiceView:
        subscriptions = self.subscriptions()
        return RuntimeMarketDataServiceView(
            source=type(self.store).__name__,
            subscription_count=len(subscriptions),
            subscriptions=subscriptions,
        )


__all__ = ["ReplayMarketDataService", "RuntimeMarketDataServiceView"]
