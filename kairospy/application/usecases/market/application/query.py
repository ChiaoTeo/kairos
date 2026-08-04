"""Market query capability exposed to system application code."""

from __future__ import annotations

from collections.abc import Iterable

from kairospy.application.usecases.market.application.resolver import ResolvedMarketData
from kairospy.application.usecases.market.domain.datasets import MarketPartition
from kairospy.application.usecases.market.domain.specs import MarketDataSpec
from kairospy.application.usecases.market.protocol import MarketDataReader


class MarketDataQueryApplicationService:
    """Reads normalized market data without exposing persistence details."""

    def __init__(self, reader: MarketDataReader) -> None:
        self._reader = reader

    def resolve(self, spec: MarketDataSpec) -> ResolvedMarketData:
        return self._reader.resolve(spec)

    def read(self, spec: MarketDataSpec, *, columns: Iterable[str] | None = None) -> list[dict[str, object]]:
        return self._reader.read(spec, columns=columns)

    def partition_for(self, resolved: ResolvedMarketData) -> MarketPartition:
        return self._reader.partition_for(resolved)

    def partition_for_spec(self, spec: MarketDataSpec) -> MarketPartition:
        return self._reader.partition_for_spec(spec)


__all__ = ["MarketDataQueryApplicationService"]
