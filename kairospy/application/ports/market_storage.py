from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from kairospy.core.market import Bar, RateObservation
from kairospy.core.reference import MarketRef


@dataclass(frozen=True, slots=True)
class MarketPartition:
    time_grain: str = "none"
    path_fields: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_partitioned(self) -> bool:
        return self.time_grain != "none" or bool(self.path_fields)


class MarketDatasetStore(Protocol):
    def read_rows(
        self,
        dataset: object,
        *,
        start: object | None = None,
        end: object | None = None,
        columns: Iterable[str] | None = None,
        limit: int | None = None,
        partition: MarketPartition | None = None,
    ) -> list[Mapping[str, object]]:
        ...

    def write(
        self,
        dataset: object,
        rows: Iterable[Mapping[str, object]],
        *,
        mode: str = "append",
        partition: MarketPartition | None = None,
    ) -> Path:
        ...

    def write_bars(
        self,
        dataset: object,
        bars: Iterable[Bar],
        *,
        market: MarketRef,
        mode: str = "append",
        partition: MarketPartition | None = None,
    ) -> Path:
        ...

    def write_funding_rates(
        self,
        dataset: object,
        rates: Iterable[RateObservation],
        *,
        market: MarketRef,
        mode: str = "append",
        partition: MarketPartition | None = None,
    ) -> Path:
        ...

    def consume(
        self,
        dataset: object,
        events: AsyncIterable[Mapping[str, object]],
        *,
        partition: MarketPartition | None = None,
        limit: int | None = None,
    ) -> int:
        ...


__all__ = ["MarketDatasetStore", "MarketPartition"]
