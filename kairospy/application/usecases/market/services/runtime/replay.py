from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from kairospy.application.support.messaging import Message
from kairospy.application.usecases.market.services.runtime.view import RuntimeMarketDataServiceView
from kairospy.application.usecases.market.application.data import DataSubscription, MarketDataSpec, MarketDataSubscriptionSpec, MarketPartition
from kairospy.application.usecases.market.application.replay import MarketReplayApplicationService
from kairospy.application.usecases.market.application.sources import market_event_from_row, parse_event_time
from kairospy.application.usecases.market.application.requests import MarketDataRow, MarketOptions
from kairospy.application.usecases.market.application.resolver import ResolvedMarketData, MarketDataResolver
from kairospy.application.usecases.market.protocol import MarketDataStore, MarketHistoricalClient
from kairospy.application.usecases.market.services.replay import HistoricalClientFactory, ReplayMarketDataPolicy
from kairospy.application.usecases.market.application.component import MarketApplication


class ReplayRowSource(Protocol):
    stream: str
    rows: tuple[MarketDataRow, ...]


@dataclass(frozen=True, slots=True)
class RuntimeIterableMarketEventSource:
    source: ReplayRowSource

    async def events(self) -> AsyncIterator[Message]:
        rows = getattr(self.source, "rows")
        stream = str(getattr(self.source, "stream"))
        for sequence, row in enumerate(rows, start=1):
            yield message_from_row(row, sequence=sequence, stream=stream)


@dataclass(frozen=True, slots=True)
class _ReplayRows:
    stream: str
    rows: tuple[MarketDataRow, ...]


class ReplayMarketDataRuntimeService:
    """System-owned adapter that drives market replay from persistence."""

    def __init__(
        self,
        store: MarketDataStore,
        *,
        resolver: MarketDataResolver | None = None,
        policy: ReplayMarketDataPolicy | None = None,
        historical_client: MarketHistoricalClient | None = None,
        historical_client_factory: HistoricalClientFactory | None = None,
    ) -> None:
        self.source_name = type(store).__name__
        self.market_data = MarketReplayApplicationService(
            store,
            resolver=resolver,
            policy=policy,
            historical_client=historical_client,
            historical_client_factory=historical_client_factory,
        )
        self._stop_signal: Callable[[], bool] | None = None
        self._market_service: MarketApplication | None = None

    def set_market_service(self, market_service: MarketApplication) -> None:
        if self._market_service is not None and self._market_service is not market_service:
            raise RuntimeError("market service is already bound to this replay source")
        self._market_service = market_service

    def clear_market_service(self) -> None:
        self._market_service = None

    def set_stop_signal(self, stop_signal: Callable[[], bool] | None) -> None:
        self._stop_signal = stop_signal

    def set_historical_client_factory(self, factory: HistoricalClientFactory | None) -> None:
        self.market_data.set_historical_client_factory(factory)

    def resolve(self, spec: MarketDataSpec) -> ResolvedMarketData:
        return self.market_data.resolve(spec)

    def read(self, spec: MarketDataSpec) -> list[MarketDataRow]:
        return self.market_data.read(spec)

    def download(
        self,
        spec: MarketDataSpec,
        client: MarketHistoricalClient,
        *,
        mode: str = "append",
        params: MarketOptions | None = None,
    ) -> Path:
        return self.market_data.download(spec, client, mode=mode, options=params)

    def partition_for(self, resolved: ResolvedMarketData) -> MarketPartition:
        return self.market_data.partition_for(resolved)

    def source_from_store(self, spec: MarketDataSpec) -> RuntimeIterableMarketEventSource:
        resolved = self.market_data.resolve(spec)
        return RuntimeIterableMarketEventSource(
            _ReplayRows(resolved.stream_name, tuple(self.market_data.read(spec)))
        )

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        service = self._market_service
        if service is not None:
            return service.subscribe(spec)
        return self.market_data.subscribe(spec)

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        service = self._market_service
        if service is not None:
            service.unsubscribe(subscription)
            return
        self.market_data.unsubscribe(subscription)

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        service = self._market_service
        if service is not None:
            return service.subscriptions()
        return self.market_data.subscriptions()

    async def events(self) -> AsyncIterator[Message]:
        if not self.subscriptions():
            raise RuntimeError("backtest strategy did not subscribe to market data")
        rows = self.market_data.rows_for_subscriptions(self.subscriptions())
        for sequence, row in enumerate(rows, start=1):
            if self._stop_signal is not None and self._stop_signal():
                return
            yield message_from_row(row, sequence=sequence, stream=str(row.get("source") or "backtest"))

    def view(self) -> RuntimeMarketDataServiceView:
        subscriptions = self.subscriptions()
        return RuntimeMarketDataServiceView(
            source=self.source_name,
            subscription_count=len(subscriptions),
            subscriptions=subscriptions,
        )


def message_from_row(row: MarketDataRow, *, sequence: int, stream: str) -> Message:
    if "time" not in row:
        raise ValueError("event rows require a time field")
    event_time = parse_event_time(row["time"])
    kind = str(row.get("kind") or "event")
    domain = str(row.get("domain") or "market")
    payload = market_event_from_row(dict(row), sequence=sequence, stream=stream) if domain == "market" else dict(row)
    return Message(topic=f"{domain}.{kind}", payload=payload or dict(row), published_at=event_time, producer=stream, producer_sequence=sequence)


__all__ = [
    "ReplayMarketDataRuntimeService",
    "RuntimeIterableMarketEventSource",
    "RuntimeMarketDataServiceView",
    "message_from_row",
]
