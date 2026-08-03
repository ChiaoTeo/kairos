from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Callable
from dataclasses import dataclass
from typing import Mapping

from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.application.usecases.market.services.ingestion import MarketIngestionService
from kairospy.application.usecases.market.services.replay import HistoricalClientFactory, ReplayMarketDataPolicy
from kairospy.application.usecases.market.services.resolver import MarketDataResolver
from kairospy.application.usecases.market.services.sources import IterableMarketEventSource
from kairospy.application.usecases.market.services.service import MarketDataService
from kairospy.application.usecases.market.domain.datasets import MarketPartition
from kairospy.application.usecases.market.domain.specs import MarketDataSpec
from kairospy.application.usecases.market.domain.subscriptions import DataSubscription, MarketDataSubscriptionSpec

from .common import RuntimeMarketDataServiceView


@dataclass(frozen=True, slots=True)
class RuntimeIterableMarketEventSource:
    source: IterableMarketEventSource

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        for sequence, row in enumerate(self.source.rows, start=1):
            yield runtime_envelope_from_row(row, sequence=sequence, stream=self.source.stream)


class ReplayMarketDataRuntimeService:
    def __init__(
        self,
        store: object,
        *,
        resolver: MarketDataResolver | None = None,
        policy: ReplayMarketDataPolicy | None = None,
        historical_client: object | None = None,
        historical_client_factory: HistoricalClientFactory | None = None,
    ) -> None:
        self.market_data = MarketDataService(
            store,
            resolver=resolver,
            policy=policy,
            historical_client=historical_client,
            historical_client_factory=historical_client_factory,
        )
        self._stop_signal: Callable[[], bool] | None = None

    def set_stop_signal(self, stop_signal: Callable[[], bool] | None) -> None:
        self._stop_signal = stop_signal

    @property
    def store(self) -> object:
        return self.market_data.store

    @property
    def resolver(self) -> MarketDataResolver:
        return self.market_data._operations().resolver

    def set_historical_client_factory(
        self,
        factory: HistoricalClientFactory | None,
    ) -> None:
        self.market_data.set_historical_client_factory(factory)

    def resolve(self, spec: MarketDataSpec) -> object:
        return self.market_data.resolve(spec)

    def read(self, spec: MarketDataSpec) -> list[dict[str, object]]:
        return self.market_data.read(spec)

    def download(
        self,
        spec: MarketDataSpec,
        client: object,
        *,
        mode: str = "append",
        params: Mapping[str, object] | None = None,
    ) -> object:
        return self.market_data.download(spec, client, mode=mode, params=params)

    def partition_for(self, resolved: object) -> MarketPartition:
        return self.market_data.partition_for(resolved)  # type: ignore[arg-type]

    def source_from_store(self, spec: MarketDataSpec) -> RuntimeIterableMarketEventSource:
        resolved = self.market_data.resolve(spec)
        return RuntimeIterableMarketEventSource(IterableMarketEventSource(resolved.stream_name, self.market_data.read(spec)))

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        return self.market_data.subscribe(spec)

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        self.market_data.unsubscribe(subscription)

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return self.market_data.subscriptions()

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        if not self.subscriptions():
            raise RuntimeError("backtest strategy did not subscribe to market data")
        rows = self.market_data.rows_for_subscriptions(self.subscriptions())
        for sequence, row in enumerate(rows, start=1):
            if self._stop_signal is not None and self._stop_signal():
                return
            yield runtime_envelope_from_row(row, sequence=sequence, stream=str(row.get("source") or "backtest"))

    def view(self) -> RuntimeMarketDataServiceView:
        subscriptions = self.subscriptions()
        return RuntimeMarketDataServiceView(
            source=type(self.store).__name__,
            subscription_count=len(subscriptions),
            subscriptions=subscriptions,
        )


def runtime_envelope_from_row(row: Mapping[str, object], *, sequence: int, stream: str) -> RuntimeEnvelope:
    if "time" not in row:
        raise ValueError("event rows require a time field")
    event_time = MarketIngestionService().event_time(row["time"])
    kind = str(row.get("kind") or "event")
    domain = str(row.get("domain") or "market")
    payload = MarketIngestionService().event_from_row(dict(row), sequence=sequence, stream=stream) if domain == "market" else dict(row)
    return RuntimeEnvelope(domain, kind, event_time, sequence, payload or dict(row))


__all__ = [
    "ReplayMarketDataRuntimeService",
    "RuntimeIterableMarketEventSource",
    "RuntimeMarketDataServiceView",
    "runtime_envelope_from_row",
]
