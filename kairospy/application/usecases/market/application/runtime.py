"""Runtime-facing assembly API for the market usecase.

The implementations live under ``market.services``.  Composition may use
these narrow constructors, while generic runtime code only receives the
resulting event source and the market application capability.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from typing import Protocol

from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.usecases.market.application.feed import MarketFeedResolver, MarketStreamConnection, StopSignal
from kairospy.application.usecases.market.application.integration import MarketIntegrationRuntime
from kairospy.application.usecases.market.protocol import MarketHistoricalClient
from kairospy.application.usecases.market.protocol import MarketDataStore
from kairospy.application.usecases.market.application.resolver import MarketDataResolver
from kairospy.application.usecases.market.services.replay import HistoricalClientFactory, ReplayMarketDataPolicy
from kairospy.application.actor.support.connections import ConnectionManager
from kairospy.domain.market import MarketEvent
from kairospy.application.support.messaging import Message
from kairospy.application.usecases.market.services.runtime import (
    BacktestMarketDataService,
    LiveMarketDataService,
    PaperMarketDataService,
    ReplayMarketDataRuntimeService,
    RuntimeIterableMarketEventSource,
    RuntimeMarketDataServiceView,
)
from kairospy.application.usecases.market.domain.specs import MarketDataSpec


class MarketRuntimeSource(Protocol):
    def events(self) -> AsyncIterator[MarketEvent | Message]:
        ...


def build_live_market(
    source: MarketRuntimeSource | None = None,
    *,
    feed: MarketStreamConnection | None = None,
    feed_resolver: MarketFeedResolver | None = None,
    source_name: str = "live",
    connections: ConnectionManager | None = None,
    stream_connections: Mapping[str, MarketStreamConnection] | None = None,
    market_service: MarketApplication | None = None,
    integration_runtime: MarketIntegrationRuntime | None = None,
    warmup_specs: Iterable[MarketDataSpec] = (),
    warmup_client_factory: Callable[[MarketDataSpec], MarketHistoricalClient] | None = None,
) -> LiveMarketDataService:
    return LiveMarketDataService(
        source,
        feed=feed,
        feed_resolver=feed_resolver,
        source_name=source_name,
        connections=connections,
        stream_connections=stream_connections,
        market_service=market_service,
        integration_runtime=integration_runtime,
        warmup_specs=warmup_specs,
        warmup_client_factory=warmup_client_factory,
    )


def build_paper_market(
    source: MarketRuntimeSource | None = None,
    *,
    feed: MarketStreamConnection | None = None,
    feed_resolver: MarketFeedResolver | None = None,
    source_name: str = "paper",
    connections: ConnectionManager | None = None,
    stream_connections: Mapping[str, MarketStreamConnection] | None = None,
    market_service: MarketApplication | None = None,
    integration_runtime: MarketIntegrationRuntime | None = None,
    warmup_specs: Iterable[MarketDataSpec] = (),
    warmup_client_factory: Callable[[MarketDataSpec], MarketHistoricalClient] | None = None,
) -> PaperMarketDataService:
    return PaperMarketDataService(
        source,
        feed=feed,
        feed_resolver=feed_resolver,
        source_name=source_name,
        connections=connections,
        stream_connections=stream_connections,
        market_service=market_service,
        integration_runtime=integration_runtime,
        warmup_specs=warmup_specs,
        warmup_client_factory=warmup_client_factory,
    )


def build_backtest_market(
    store: MarketDataStore,
    *,
    resolver: MarketDataResolver | None = None,
    policy: ReplayMarketDataPolicy | None = None,
    historical_client: MarketHistoricalClient | None = None,
    historical_client_factory: HistoricalClientFactory | None = None,
) -> BacktestMarketDataService:
    return BacktestMarketDataService(
        store,
        resolver=resolver,
        policy=policy,
        historical_client=historical_client,
        historical_client_factory=historical_client_factory,
    )


def build_replay_market(
    store: MarketDataStore,
    *,
    resolver: MarketDataResolver | None = None,
    policy: ReplayMarketDataPolicy | None = None,
    historical_client: MarketHistoricalClient | None = None,
    historical_client_factory: HistoricalClientFactory | None = None,
) -> ReplayMarketDataRuntimeService:
    return ReplayMarketDataRuntimeService(
        store,
        resolver=resolver,
        policy=policy,
        historical_client=historical_client,
        historical_client_factory=historical_client_factory,
    )


def build_market_runtime(
    source: MarketRuntimeSource | None = None,
    *,
    feed: MarketStreamConnection | None = None,
    feed_resolver: MarketFeedResolver | None = None,
    source_name: str,
    mode_label: str = "streaming",
    connections: ConnectionManager | None = None,
    stream_connections: Mapping[str, MarketStreamConnection] | None = None,
    market_service: MarketApplication | None = None,
    integration_runtime: MarketIntegrationRuntime | None = None,
    warmup_specs: Iterable[MarketDataSpec] = (),
    warmup_client_factory: HistoricalClientFactory | None = None,
) -> "MarketRuntimeService":
    """Build the generic streaming adapter for market-usecase hosts."""
    from kairospy.application.usecases.market.services.runtime import MarketRuntimeService

    return MarketRuntimeService(
        source,
        feed=feed,
        feed_resolver=feed_resolver,
        source_name=source_name,
        mode_label=mode_label,
        connections=connections,
        stream_connections=stream_connections,
        market_service=market_service,
        integration_runtime=integration_runtime,
        warmup_specs=warmup_specs,
        warmup_client_factory=warmup_client_factory,
    )


__all__ = [
    "BacktestMarketDataService",
    "LiveMarketDataService",
    "PaperMarketDataService",
    "ReplayMarketDataRuntimeService",
    "MarketRuntimeSource",
    "RuntimeIterableMarketEventSource",
    "RuntimeMarketDataServiceView",
    "build_backtest_market",
    "build_live_market",
    "build_market_runtime",
    "build_paper_market",
    "build_replay_market",
]
