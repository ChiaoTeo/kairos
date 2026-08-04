"""Contracts consumed by the market usecase."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from collections.abc import AsyncIterable, Iterable, Mapping
from pathlib import Path

from kairospy.application.usecases.market.domain.datasets import MarketPartition
from kairospy.application.usecases.market.domain.specs import MarketDataSpec
from kairospy.application.usecases.market.application.resolver import ResolvedMarketData
from kairospy.application.usecases.market.domain.subscriptions import DataSubscription, MarketDataSubscriptionSpec


class MarketHistoricalSource(Protocol):
    """Fetches already-translated historical observations for one market spec."""

    def fetch(self, spec: MarketDataSpec) -> Iterable[object]:
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

    def read(self, spec: MarketDataSpec, *, columns: Iterable[str] | None = None) -> list[dict[str, object]]:
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
        client: object,
        *,
        mode: str = "append",
        options: Mapping[str, object] | None = None,
    ) -> Path:
        ...

    def persist_historical(
        self,
        spec: MarketDataSpec,
        observations: Iterable[object],
        *,
        mode: str = "append",
    ) -> Path:
        ...

    async def persist(
        self,
        spec: MarketDataSpec,
        events: AsyncIterable[Mapping[str, object]],
        *,
        limit: int | None = None,
    ) -> int:
        ...

    def ensure(
        self,
        spec: MarketDataSpec,
        client: object | None = None,
        *,
        mode: str = "append",
        options: Mapping[str, object] | None = None,
    ) -> ResolvedMarketData:
        ...


__all__ = [
    "MarketDataReader",
    "MarketDataWriter",
    "MarketHistoricalSource",
    "MarketSubscriptionState",
]
