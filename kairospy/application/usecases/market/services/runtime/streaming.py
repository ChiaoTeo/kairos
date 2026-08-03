from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from typing import Mapping

from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.application.support.runtime.domain.lines import RuntimeEventLine, close_event_line
from kairospy.application.usecases.market.services.service import MarketDataService
from kairospy.application.usecases.market.domain.planning import MarketFeedWatchPlan, MarketStreamPlanningService
from kairospy.application.usecases.market.domain.subscriptions import DataSubscription, MarketDataSubscriptionSpec, MarketSubscriptionService
from kairospy.infrastructure.integrations.application.market import ConnectionMarketSubscriptionRequest, MarketStreamConnection
from kairospy.application.support.runtime.domain.connections import ConnectionManager
from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.domain.market import MarketEvent

from .common import RuntimeMarketDataServiceView


class _StopRequested(Exception):
    pass


class StreamingMarketDataService:
    def __init__(
        self,
        source: RuntimeEventLine | None = None,
        *,
        feed: MarketStreamConnection | None = None,
        feed_resolver: object | None = None,
        source_name: str,
        mode_label: str = "streaming",
        connections: ConnectionManager | None = None,
        stream_connections: Mapping[str, MarketStreamConnection] | None = None,
        subscriptions: MarketSubscriptionService | None = None,
        planning: MarketStreamPlanningService | None = None,
    ) -> None:
        if source is None and feed is None and feed_resolver is None and not stream_connections:
            raise ValueError(f"{mode_label} market data service requires a runtime source or integration feed")
        self.source = source
        self.feed = feed
        self.feed_resolver = feed_resolver
        self.source_name = source_name
        self.mode_label = mode_label
        self.connections = connections
        self.stream_connections = dict(stream_connections or {})
        self.market_data = MarketDataService(subscriptions=subscriptions, planning=planning)
        self._sequence = 0
        self._stop_signal: object | None = None
        if self.feed is not None and self.connections is not None:
            self.connections.register(f"{self.mode_label}.market_feed.default", self.feed, role="market_feed")

    def set_stop_signal(self, stop_signal: Callable[[], bool] | object | None) -> None:
        self._stop_signal = stop_signal

    def set_connection_manager(self, connections: ConnectionManager | None) -> None:
        self.connections = connections
        if self.feed is not None and self.connections is not None:
            self.feed = self.connections.register(f"{self.mode_label}.market_feed.default", self.feed, role="market_feed")

    def set_feed_resolver(self, feed_resolver: object | None) -> None:
        self.feed_resolver = feed_resolver

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        if (self.stream_connections or self.feed is not None or self.feed_resolver is not None) and self.subscriptions():
            async for event in self._feed_events():
                yield event
            return
        if self.source is None:
            return
        events = self.source.events()
        try:
            async for event in events:
                if self._should_stop():
                    return
                yield event
        finally:
            await close_event_line(events)

    def view(self) -> RuntimeMarketDataServiceView:
        subscriptions = self.subscriptions()
        return RuntimeMarketDataServiceView(self.source_name, len(subscriptions), subscriptions)

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        return self.market_data.subscribe(spec)

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        self.market_data.unsubscribe(subscription)

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return self.market_data.subscriptions()

    async def _feed_events(self) -> AsyncIterator[RuntimeEnvelope]:
        if self.stream_connections or self.feed is not None or self.feed_resolver is not None:
            async for event in self._connection_events():
                yield event
            return
        raise RuntimeError("market stream connections are not configured")

    async def _connection_events(self) -> AsyncIterator[RuntimeEnvelope]:
        subscriptions = []
        iterators: list[tuple[object, str]] = []
        for subscription in self.subscriptions():
            for plan in self.market_data.feed_watches(subscription):
                connection = self._connection_for(plan)
                if connection is None:
                    raise RuntimeError(f"no market stream connection for {plan.market.venue}")
                try:
                    remote = await self._await_stop_aware(
                        connection.subscribe(
                            ConnectionMarketSubscriptionRequest(
                                market=plan.market,
                                selector=plan.selector,
                                identity=subscription.key,
                                params=plan.params,
                            )
                        )
                    )
                except _StopRequested:
                    return
                subscriptions.append((connection, remote))
                iterators.append((remote.events().__aiter__(), plan.kind))
        tasks = {asyncio.create_task(iterator.__anext__()): (iterator, kind) for iterator, kind in iterators}
        try:
            while tasks and not self._should_stop():
                done, _ = await asyncio.wait(
                    tasks,
                    timeout=0.5,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    iterator, kind = tasks.pop(task)
                    try:
                        event = task.result()
                        yield self._envelope(kind, event) if isinstance(event, MarketEvent) else event
                    except StopAsyncIteration:
                        continue
                    tasks[asyncio.create_task(iterator.__anext__())] = (iterator, kind)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks.keys(), return_exceptions=True)
            for iterator, _kind in iterators:
                close_iterator = getattr(iterator, "aclose", None)
                if callable(close_iterator):
                    await close_iterator()
            for connection, subscription in subscriptions:
                await connection.unsubscribe(subscription.subscription_id)

    async def _await_stop_aware(self, awaitable: object) -> object:
        task = asyncio.ensure_future(awaitable)  # type: ignore[arg-type]
        try:
            while not task.done():
                if self._should_stop():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise _StopRequested
                await asyncio.wait({task}, timeout=0.5)
            return task.result()
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

    def _connection_for(self, plan: MarketFeedWatchPlan) -> MarketStreamConnection | None:
        requested = plan.params.get("connection_id")
        if requested is not None:
            connection = self.stream_connections.get(str(requested))
            if connection is not None:
                return connection
            if self.feed_resolver is not None:
                return self.feed_resolver.resolve_market_feed(
                    MarketDataSubscriptionSpec(plan.market, (plan.selector,), params=plan.params)
                )
            return None
        candidates = [connection for connection in self.stream_connections.values() if _connection_matches_venue(connection, plan.market.venue)]
        if not candidates and self.feed is not None:
            return self.feed
        if len(candidates) == 1:
            return candidates[0]
        if self.feed_resolver is not None and not candidates:
            return self.feed_resolver.resolve_market_feed(
                MarketDataSubscriptionSpec(plan.market, (plan.selector,), params=plan.params)
            )
        if not candidates:
            return None
        raise RuntimeError(f"multiple market stream connections require connection_id: venue={plan.market.venue}")

    def _should_stop(self) -> bool:
        if self._stop_signal is None:
            return False
        if callable(self._stop_signal):
            return bool(self._stop_signal())
        should_stop = getattr(self._stop_signal, "should_stop", None)
        if not callable(should_stop):
            raise TypeError("runtime stop signal must define should_stop()")
        return bool(should_stop())

    def _envelope(self, kind: str, event: MarketEvent) -> RuntimeEnvelope:
        self._sequence += 1
        time = event.available_at or event.observed_at
        if time.tzinfo is None:
            time = datetime.now(timezone.utc)
        return RuntimeEnvelope("market", kind, time, self._sequence, event)

__all__ = ["StreamingMarketDataService"]


def _connection_matches_venue(connection: object, venue: object) -> bool:
    identity = getattr(connection, "identity", None)
    if identity is not None:
        return any(str(participant.id) == str(venue) for participant in identity.participants)
    return str(getattr(connection, "venue", "")) == str(venue)
