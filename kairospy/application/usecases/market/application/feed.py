"""Market feed application capability: subscriptions and live events."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Awaitable, Protocol

from kairospy.application.usecases.market.domain.planning import MarketFeedWatchPlan
from kairospy.application.usecases.market.domain.subscriptions import DataSubscription, MarketDataSubscriptionSpec
from kairospy.application.usecases.market.application.integration import (
    MarketFeedSubscription,
    MarketFeedSubscriptionRequest,
    MarketStreamConnectionRequest,
    MarketStreamConnection,
    MarketIntegrationRuntime,
)
from kairospy.domain.market import MarketEvent, MarketSelector


class MarketFeedResolver(Protocol):
    def resolve_market_feed(self, spec: MarketDataSubscriptionSpec) -> MarketStreamConnection | None:
        ...


class MarketFeedSubscriptionState(Protocol):
    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription: ...
    def unsubscribe(self, subscription: DataSubscription | str) -> None: ...
    def subscriptions(self) -> tuple[DataSubscription, ...]: ...
    def feed_watches(self, subscription: DataSubscription) -> tuple[MarketFeedWatchPlan, ...]: ...


class _StopRequested(Exception):
    pass


class MarketFeedApplicationService:
    """Coordinates market subscription state with live event sources.

    Connection lifecycle remains owned by System's connection scope. This
    service only creates remote subscriptions, reads canonical MarketEvents,
    and closes the remote subscriptions when the feed stops.
    """

    def __init__(
        self,
        subscriptions: MarketFeedSubscriptionState,
        *,
        feed: MarketStreamConnection | None = None,
        feed_resolver: MarketFeedResolver | None = None,
        stream_connections: Mapping[str, MarketStreamConnection] | None = None,
        integration_runtime: MarketIntegrationRuntime | None = None,
        stop_signal: Callable[[], bool] | "StopSignal" | None = None,
    ) -> None:
        self.subscriptions = subscriptions
        self.feed = feed
        self.feed_resolver = feed_resolver
        self.stream_connections = dict(stream_connections or {})
        self.integration_runtime = integration_runtime
        self.stop_signal = stop_signal

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        return self.subscriptions.subscribe(spec)

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        self.subscriptions.unsubscribe(subscription)

    def list_subscriptions(self) -> tuple[DataSubscription, ...]:
        return self.subscriptions.subscriptions()

    async def events(self) -> AsyncIterator[MarketEvent]:
        if not (self.stream_connections or self.feed is not None or self.feed_resolver is not None or self.integration_runtime is not None):
            raise RuntimeError("market feed has no integration connection")
        if not self.list_subscriptions() and not bool(getattr(self.subscriptions, "has_subscription_intents", lambda: False)()):
            return

        active: dict[str, tuple[MarketStreamConnection, MarketFeedSubscription, AsyncIterator[MarketEvent], asyncio.Task[MarketEvent]]] = {}

        async def sync_subscriptions() -> None:
            desired: dict[str, tuple[MarketFeedWatchPlan, MarketStreamConnection, str]] = {}
            for subscription in self.list_subscriptions():
                for plan in self.subscriptions.feed_watches(subscription):
                    connection = self._connection_for(plan)
                    if connection is None:
                        raise RuntimeError(f"no market stream connection for {plan.market.venue}")
                    desired[plan.key] = (plan, connection, subscription.key)

            for key, (plan, connection, identity) in desired.items():
                if key in active:
                    continue
                remote = await self._await_stop_aware(
                    connection.subscribe(
                        MarketFeedSubscriptionRequest(
                            market=plan.market,
                            selector=plan.selector,
                            identity=identity,
                            params=plan.params,
                        )
                    )
                )
                iterator = remote.events().__aiter__()
                active[key] = (connection, remote, iterator, asyncio.create_task(iterator.__anext__()))

            for key in tuple(active):
                if key in desired:
                    continue
                connection, remote, _iterator, task = active.pop(key)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await connection.unsubscribe(remote.subscription_id)

        try:
            await sync_subscriptions()
            while not self._should_stop():
                tasks = {item[3]: (key, item[2]) for key, item in active.items()}
                if not tasks:
                    await asyncio.sleep(0.5)
                    await sync_subscriptions()
                    continue
                done, _ = await asyncio.wait(tasks, timeout=0.5, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    key, iterator = tasks[task]
                    try:
                        event = task.result()
                    except StopAsyncIteration:
                        stopped = active.pop(key, None)
                        if stopped is not None:
                            await stopped[0].unsubscribe(stopped[1].subscription_id)
                        continue
                    if not isinstance(event, MarketEvent):
                        raise TypeError("market feed connections must emit MarketEvent")
                    yield event
                    if key in active:
                        connection, remote, _old_iterator, _old_task = active[key]
                        active[key] = (connection, remote, iterator, asyncio.create_task(iterator.__anext__()))
                await sync_subscriptions()
        except _StopRequested:
            return
        finally:
            for connection, remote, _iterator, task in active.values():
                task.cancel()
            if active:
                await asyncio.gather(*(item[3] for item in active.values()), return_exceptions=True)
            for connection, remote, _iterator, _task in active.values():
                await connection.unsubscribe(remote.subscription_id)

    def _connection_for(self, plan: MarketFeedWatchPlan) -> MarketStreamConnection | None:
        runtime = self.integration_runtime
        if runtime is not None:
            request = MarketStreamConnectionRequest(
                market=plan.market,
                connection_id=str(plan.params["connection_id"]) if plan.params.get("connection_id") is not None else None,
                provider=str(plan.params["provider"]) if plan.params.get("provider") is not None else None,
                credential=str(plan.params["credential"]) if plan.params.get("credential") is not None else None,
            )
            connection_id = plan.params.get("connection_id")
            if connection_id is not None:
                resolved = runtime.resolve_stream(str(connection_id))
                if resolved is not None:
                    return resolved
            return runtime.create_stream(request)
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

        candidates = [
            connection
            for connection in self.stream_connections.values()
            if _connection_matches_venue(connection, plan.market.venue)
        ]
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

    async def _await_stop_aware(self, awaitable: Awaitable[MarketFeedSubscription]) -> MarketFeedSubscription:
        task = asyncio.ensure_future(awaitable)
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

    def _should_stop(self) -> bool:
        if self.stop_signal is None:
            return False
        if callable(self.stop_signal):
            return bool(self.stop_signal())
        should_stop = getattr(self.stop_signal, "should_stop", None)
        if not callable(should_stop):
            raise TypeError("market feed stop signal must define should_stop()")
        return bool(should_stop())


class StopSignal(Protocol):
    def should_stop(self) -> bool:
        ...


def _connection_matches_venue(connection: MarketStreamConnection, venue: str) -> bool:
    identity = getattr(connection, "identity", None)
    if identity is not None:
        return any(str(participant.id) == str(venue) for participant in identity.participants)
    return str(getattr(connection, "venue", "")) == str(venue)


__all__ = [
    "MarketFeedApplicationService",
    "MarketStreamConnection",
    "MarketFeedResolver",
    "MarketFeedSubscription",
    "MarketFeedSubscriptionRequest",
]
