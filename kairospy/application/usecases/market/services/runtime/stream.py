from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from datetime import datetime, timezone
from typing import Mapping

from kairospy.application.support.messaging import Message
from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.usecases.market.application.data import DataSubscription, MarketDataSubscriptionSpec
from kairospy.application.usecases.market.application.feed import MarketStreamConnection
from kairospy.application.actor.support.connections import ConnectionManager
from kairospy.domain.market import MarketEvent
from kairospy.domain.market import MarketSubject, Bar
from kairospy.domain.reference import MarketResolver
from kairospy.application.usecases.market.domain.specs import MarketDataSpec
from kairospy.application.usecases.market.application.feed import MarketFeedApplicationService

from kairospy.application.usecases.market.services.runtime.view import RuntimeMarketDataServiceView


class MarketRuntimeService:
    def __init__(
        self,
        source: object | None = None,
        *,
        feed: MarketStreamConnection | None = None,
        feed_resolver: object | None = None,
        source_name: str,
        mode_label: str = "streaming",
        connections: ConnectionManager | None = None,
        stream_connections: Mapping[str, MarketStreamConnection] | None = None,
        market_service: MarketApplication | None = None,
        integration_runtime: object | None = None,
        warmup_specs: Iterable[MarketDataSpec] = (),
        warmup_client_factory: object | None = None,
    ) -> None:
        if source is None and feed is None and feed_resolver is None and not stream_connections and integration_runtime is None:
            raise ValueError(f"{mode_label} market data service requires a runtime source or integration feed")
        self.source = source
        self.feed = feed
        self.feed_resolver = feed_resolver
        self.source_name = source_name
        self.mode_label = mode_label
        self.connections = connections
        self.stream_connections = dict(stream_connections or {})
        self.market_service = market_service
        self.integration_runtime = integration_runtime
        self.warmup_specs = tuple(warmup_specs)
        self.warmup_client_factory = warmup_client_factory
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

    def set_market_service(self, market_service: MarketApplication) -> None:
        if self.market_service is not None and self.market_service is not market_service:
            raise RuntimeError("market service is already bound to this system source")
        self.market_service = market_service
        market_service.attach_feed(
            MarketFeedApplicationService(
                market_service.subscriptions,
                feed=self.feed,
                feed_resolver=self.feed_resolver,
                stream_connections=self.stream_connections,
                integration_runtime=self.integration_runtime,
                stop_signal=self._stop_signal,
            )
        )

    def clear_market_service(self) -> None:
        self.market_service = None

    def warmup_events(
        self,
        *,
        stop_requested: Callable[[], bool] | None = None,
        progress: Callable[[int, int, MarketDataSpec, str], None] | None = None,
        failure: Callable[[MarketDataSpec, Exception], None] | None = None,
    ) -> tuple[Message, ...]:
        """Load historical bars before the strategy runtime starts.

        Warmup is deliberately synchronous: the Market Actor invokes it before
        ``StrategyRuntimeSession.start()``, so ``on_start`` sees a complete
        initial projection.  Live updates still arrive through the normal
        Market Actor event loop afterwards.
        """
        messages: list[Message] = []
        sequence = 0
        failed = 0
        specs = tuple(self.warmup_specs or self._subscription_warmup_specs())
        total = len(specs)
        for index, spec in enumerate(specs, start=1):
            if stop_requested is not None and stop_requested():
                if progress is not None:
                    progress(index, total, spec, "stopped")
                break
            if progress is not None:
                progress(index, total, spec, "checking")
            try:
                client = self._warmup_client(spec)
                if client is None:
                    raise RuntimeError(f"market warmup has no historical client for {spec.symbol}")
                cached = getattr(self.market_service, "ensure_bars", None)
                values = cached(spec, client) if callable(cached) and getattr(self.market_service, "has_historical_store", False) else self._fetch_bars(spec, client)
                ref = MarketResolver(default_venue=spec.venue, default_market=spec.market).resolve(
                    spec.symbol, venue=spec.venue, market=spec.market
                )
                values = tuple(values)
                for value in values:
                    if not isinstance(value, Bar):
                        raise TypeError("market warmup historical client must return domain Bar values")
                    sequence += 1
                    event = MarketEvent(
                        subject=MarketSubject("market", ref.market_id),
                        observed_at=value.time,
                        available_at=value.time,
                        value=value,
                        source="historical",
                        sequence=sequence,
                    )
                    messages.append(self._message(event.kind, event))
                if progress is not None:
                    progress(index, total, spec, "ready" if values else "empty")
            except Exception as error:
                failed += 1
                if failure is not None:
                    failure(spec, error)
                if progress is not None:
                    progress(index, total, spec, "failed")
                # One unavailable contract must not prevent the strategy from
                # starting with the rest of its view and the live stream.
                continue
        if progress is not None and failed and specs:
            progress(total, total, specs[-1], f"degraded failed={failed}")
        return tuple(messages)

    def _fetch_bars(self, spec: MarketDataSpec, client: object) -> Iterable[Bar]:
        fetch = getattr(client, "bars", None)
        if not callable(fetch):
            raise TypeError("market warmup historical client must provide bars()")
        return fetch(
            spec.symbol,
            timeframe=spec.timeframe or "1m",
            since=spec.start,
            until=spec.end,
            limit=spec.limit or 1000,
        )

    def _subscription_warmup_specs(self) -> tuple[MarketDataSpec, ...]:
        """Translate strategy-declared historical params into market specs."""
        service = self.market_service
        if service is None:
            return ()
        subscriptions = getattr(getattr(service, "subscriptions", None), "subscriptions", None)
        if not callable(subscriptions):
            return ()
        specs: list[MarketDataSpec] = []
        for subscription in subscriptions():
            subscription_spec = subscription.spec
            params = subscription_spec.params
            start = params.get("history_start", params.get("warmup_start"))
            end = params.get("history_end", params.get("warmup_end"))
            if start is None or end is None:
                continue
            for selector in subscription_spec.selectors:
                model = getattr(selector, "model", selector)
                model_name = getattr(model, "__name__", "")
                if model_name == "Bar":
                    timeframe = getattr(selector, "interval", None) or "1m"
                elif model_name == "Quote" and params.get("history_timeframe") is not None:
                    timeframe = str(params["history_timeframe"])
                else:
                    continue
                specs.append(MarketDataSpec(
                    symbol=str(subscription_spec.market.source_symbol),
                    kind="ohlcv",
                    venue=str(subscription_spec.market.venue),
                    market=str(subscription_spec.market.market),
                    timeframe=timeframe,
                    start=start,
                    end=end,
                    limit=int(params.get("history_limit", params.get("warmup_limit", 1000))),
                ))
        return tuple(specs)

    def _warmup_client(self, spec: MarketDataSpec) -> object | None:
        factory = self.warmup_client_factory
        if callable(factory):
            return factory(spec)
        if factory is not None:
            return factory
        runtime = self.integration_runtime
        if runtime is None:
            return None
        create_data = getattr(runtime, "create_data", None)
        if not callable(create_data):
            return None
        from kairospy.application.usecases.market.application.integration import MarketDataConnectionRequest

        return create_data(MarketDataConnectionRequest(spec))

    async def events(self) -> AsyncIterator[Message]:
        if (self.stream_connections or self.feed is not None or self.feed_resolver is not None or self.integration_runtime is not None) and self.subscriptions():
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
            close = getattr(events, "aclose", None)
            if close is not None:
                await close()

    def view(self) -> RuntimeMarketDataServiceView:
        subscriptions = self.subscriptions()
        return RuntimeMarketDataServiceView(self.source_name, len(subscriptions), subscriptions)

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        return self._require_market_service().subscriptions.subscribe(spec)

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        self._require_market_service().subscriptions.unsubscribe(subscription)

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return self._require_market_service().subscriptions.subscriptions()

    async def _feed_events(self) -> AsyncIterator[Message]:
        if self.stream_connections or self.feed is not None or self.feed_resolver is not None or self.integration_runtime is not None:
            async for event in self._connection_events():
                yield event
            return
        raise RuntimeError("market stream connections are not configured")

    async def _connection_events(self) -> AsyncIterator[Message]:
        market_service = self._require_market_service()
        feed = MarketFeedApplicationService(
            market_service.subscriptions,
            feed=self.feed,
            feed_resolver=self.feed_resolver,
            stream_connections=self.stream_connections,
            integration_runtime=self.integration_runtime,
            stop_signal=self._stop_signal,
        )
        async for event in feed.events():
            yield self._message(event.kind, event)

    def _should_stop(self) -> bool:
        if self._stop_signal is None:
            return False
        if callable(self._stop_signal):
            return bool(self._stop_signal())
        should_stop = getattr(self._stop_signal, "should_stop", None)
        if not callable(should_stop):
            raise TypeError("runtime stop signal must define should_stop()")
        return bool(should_stop())

    def _require_market_service(self) -> MarketApplication:
        if self.market_service is None:
            raise RuntimeError("market source is not bound to a MarketApplication")
        return self.market_service

    def _message(self, kind: str, event: MarketEvent) -> Message:
        self._sequence += 1
        time = event.available_at or event.observed_at
        if time.tzinfo is None:
            time = datetime.now(timezone.utc)
        return Message(topic=f"market.{kind}", payload=event, published_at=time, producer=self.source_name, producer_sequence=self._sequence)

__all__ = ["MarketRuntimeService"]
