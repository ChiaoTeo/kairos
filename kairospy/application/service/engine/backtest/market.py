from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.application.runtime.protocol.lines import RuntimeEventLine, close_event_line
from kairospy.application.runtime.services import DataSubscription, MarketDataService, MarketDataSubscriptionSpec
from kairospy.core.views import ViewFieldSchema, ViewSchema
from kairospy.infrastructure.data import DataSink, DataStore

from kairospy.application.service.domain.market import MarketDataResolver, MarketDataSpec, ResolvedMarketData
from kairospy.application.service.domain.market.sources import IterableMarketEventSource


class HistoricalMarketDataClient(Protocol):
    def fetch_ohlcv(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
    ) -> Iterable[Mapping[str, object]]:
        ...


@dataclass(frozen=True, slots=True)
class MarketDataServiceView:
    source: str
    subscription_count: int = 0
    subscriptions: tuple[DataSubscription, ...] = ()


class BacktestMarketDataService(MarketDataService):
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
        self.store = store
        self.resolver = resolver or MarketDataResolver()
        self.source = source
        self._subscriptions: dict[str, DataSubscription] = {}

    def resolve(self, spec: MarketDataSpec) -> ResolvedMarketData:
        return self.resolver.resolve(spec)

    def read(self, spec: MarketDataSpec, *, columns: Iterable[str] | None = None) -> list[dict[str, object]]:
        resolved = self.resolve(spec)
        return self.store.read_rows(
            resolved.dataset_id,
            start=spec.start,
            end=spec.end,
            columns=columns,
            limit=spec.limit,
        )

    def download(
        self,
        spec: MarketDataSpec,
        client: HistoricalMarketDataClient,
        *,
        mode: str = "append",
    ) -> Path:
        if spec.kind != "ohlcv":
            raise ValueError(f"unsupported historical data kind: {spec.kind}")
        resolved = self.resolve(spec)
        rows = client.fetch_ohlcv(
            resolved.market_ref.source_symbol,
            timeframe=spec.timeframe or "1m",
            since=spec.start,
            until=spec.end,
            limit=spec.limit or 1000,
        )
        return self.store.write(resolved.dataset_id, rows, mode=mode)

    def ensure(
        self,
        spec: MarketDataSpec,
        client: HistoricalMarketDataClient | None = None,
        *,
        mode: str = "append",
    ) -> ResolvedMarketData:
        resolved = self.resolve(spec)
        if self.store.read_rows(resolved.dataset_id, start=spec.start, end=spec.end, limit=1):
            return resolved
        if client is None:
            raise RuntimeError(f"dataset has no rows and no client was provided: {resolved.dataset_id}")
        self.download(spec, client, mode=mode)
        return resolved

    def sink(self, spec: MarketDataSpec) -> DataSink:
        return DataSink(self.store, self.resolve(spec).dataset_id)

    async def persist(
        self,
        spec: MarketDataSpec,
        events: AsyncIterable[Mapping[str, object]],
        *,
        limit: int | None = None,
    ) -> int:
        return await self.sink(spec).consume(events, limit=limit)

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
