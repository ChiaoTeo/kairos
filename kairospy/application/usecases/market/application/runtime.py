"""Runtime-facing assembly API for the market usecase.

The implementations live under ``market.services``.  Composition may use
these narrow constructors, while generic runtime code only receives the
resulting event source and the market application capability.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.usecases.market.application.feed import MarketStreamConnection
from kairospy.application.usecases.market.services.runtime import (
    BacktestMarketDataService,
    LiveMarketDataService,
    PaperMarketDataService,
    ReplayMarketDataRuntimeService,
    RuntimeIterableMarketEventSource,
    RuntimeMarketDataServiceView,
)
from kairospy.application.usecases.market.domain.specs import MarketDataSpec


def build_live_market(
    source: object | None = None,
    *,
    feed: MarketStreamConnection | None = None,
    feed_resolver: object | None = None,
    source_name: str = "live",
    connections: object | None = None,
    stream_connections: Mapping[str, MarketStreamConnection] | None = None,
    market_service: MarketApplication | None = None,
    integration_runtime: object | None = None,
    warmup_specs: Iterable[MarketDataSpec] = (),
    warmup_client_factory: object | None = None,
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
    source: object | None = None,
    *,
    feed: MarketStreamConnection | None = None,
    feed_resolver: object | None = None,
    source_name: str = "paper",
    connections: object | None = None,
    stream_connections: Mapping[str, MarketStreamConnection] | None = None,
    market_service: MarketApplication | None = None,
    integration_runtime: object | None = None,
    warmup_specs: Iterable[MarketDataSpec] = (),
    warmup_client_factory: object | None = None,
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


def build_backtest_market(*args: object, **kwargs: object) -> BacktestMarketDataService:
    return BacktestMarketDataService(*args, **kwargs)


def build_replay_market(*args: object, **kwargs: object) -> ReplayMarketDataRuntimeService:
    return ReplayMarketDataRuntimeService(*args, **kwargs)


def build_market_runtime(*args: object, **kwargs: object):
    """Build the generic streaming adapter for market-usecase hosts."""
    from kairospy.application.usecases.market.services.runtime import MarketRuntimeService

    return MarketRuntimeService(*args, **kwargs)


__all__ = [
    "BacktestMarketDataService",
    "LiveMarketDataService",
    "PaperMarketDataService",
    "ReplayMarketDataRuntimeService",
    "RuntimeIterableMarketEventSource",
    "RuntimeMarketDataServiceView",
    "build_backtest_market",
    "build_live_market",
    "build_market_runtime",
    "build_paper_market",
    "build_replay_market",
]
