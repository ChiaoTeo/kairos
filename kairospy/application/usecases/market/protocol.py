"""Contracts consumed by the market usecase."""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
from typing import Protocol
from pathlib import Path

from kairospy.application.usecases.market.application.requests import MarketDataRow, MarketWarmupStatus
from kairospy.application.usecases.market.domain.datasets import MarketPartition
from kairospy.application.usecases.market.domain.specs import MarketDataSpec, MarketOptions, MarketTime
from kairospy.application.usecases.market.application.resolver import ResolvedMarketData
from kairospy.application.usecases.market.domain.subscriptions import DataSubscription, MarketDataSubscriptionSpec
from kairospy.domain.market import Bar, MarketEvent, RateObservation
from kairospy.domain.reference import MarketRef


class MarketDataStore(Protocol):
    """Minimal persistence capability consumed by the market usecase."""

    def read_rows(
        self,
        dataset: str,
        *,
        start: MarketTime | None = None,
        end: MarketTime | None = None,
        columns: Iterable[str] | None = None,
        limit: int | None = None,
        partition: MarketPartition | None = None,
    ) -> list[MarketDataRow]:
        ...

    def write_bars(
        self,
        dataset: str,
        bars: Iterable[Bar],
        *,
        market: MarketRef,
        mode: str = "append",
        partition: MarketPartition | None = None,
    ) -> Path:
        ...

    def write_funding_rates(
        self,
        dataset: str,
        rates: Iterable[RateObservation],
        *,
        market: MarketRef,
        mode: str = "append",
        partition: MarketPartition | None = None,
    ) -> Path:
        ...

    def write(
        self,
        dataset: str,
        rows: Iterable[MarketDataRow],
        *,
        partition: MarketPartition | None = None,
    ) -> Path:
        ...

    def read_metadata(self, key: str) -> MarketWarmupStatus | None:
        ...

    def write_metadata(self, key: str, value: MarketWarmupStatus) -> None:
        ...


class MarketHistoricalSource(Protocol):
    """Fetches already-translated historical observations for one market spec."""

    def fetch(self, spec: MarketDataSpec) -> Iterable[Bar | RateObservation]:
        """Return market observations without exposing a vendor client."""


class MarketSubscriptionState(Protocol):
    """State port consumed by the market subscription application service."""

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        ...

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        ...

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        ...


class MarketDataReader(Protocol):
    """Read-side port for normalized market datasets."""

    def resolve(self, spec: MarketDataSpec) -> ResolvedMarketData:
        ...

    def read(self, spec: MarketDataSpec, *, columns: Iterable[str] | None = None) -> list[MarketDataRow]:
        ...

    def partition_for(self, resolved: ResolvedMarketData) -> MarketPartition:
        ...

    def partition_for_spec(self, spec: MarketDataSpec) -> MarketPartition:
        ...


class MarketDataWriter(Protocol):
    """Write-side port for normalized market datasets."""

    def download(
        self,
        spec: MarketDataSpec,
        client: "MarketHistoricalClient",
        *,
        mode: str = "append",
        options: MarketOptions | None = None,
    ) -> Path:
        ...

    def persist_historical(
        self,
        spec: MarketDataSpec,
        observations: Iterable[Bar | RateObservation],
        *,
        mode: str = "append",
    ) -> Path:
        ...

    async def persist(
        self,
        spec: MarketDataSpec,
        events: AsyncIterable[MarketEvent],
        *,
        limit: int | None = None,
    ) -> int:
        ...

    def ensure(
        self,
        spec: MarketDataSpec,
        client: "MarketHistoricalClient | None" = None,
        *,
        mode: str = "append",
        options: MarketOptions | None = None,
    ) -> ResolvedMarketData:
        ...


class MarketHistoricalClient(Protocol):
    """Normalized historical market-data capability supplied by an adapter."""

    def bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: MarketTime | None = None,
        until: MarketTime | None = None,
        limit: int = 1000,
        adapter_options: MarketOptions | None = None,
    ) -> Iterable[Bar]:
        ...

    def funding_rates(
        self,
        symbol: str,
        *,
        since: MarketTime | None = None,
        until: MarketTime | None = None,
        limit: int = 1000,
        adapter_options: MarketOptions | None = None,
    ) -> Iterable[RateObservation]:
        ...


__all__ = [
    "MarketDataReader",
    "MarketDataStore",
    "MarketDataWriter",
    "MarketHistoricalClient",
    "MarketHistoricalSource",
    "MarketSubscriptionState",
]
