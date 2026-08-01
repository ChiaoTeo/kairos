from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

from kairospy.core.market import Bar, RateObservation


class BarHistoryPort(Protocol):
    def fetch_bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
        adapter_options: Mapping[str, object] | None = None,
    ) -> Iterable[Bar]:
        ...


class FundingRateHistoryPort(Protocol):
    def fetch_funding_rates(
        self,
        symbol: str,
        *,
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
        adapter_options: Mapping[str, object] | None = None,
    ) -> Iterable[RateObservation]:
        ...


class HistoricalMarketDataPort(BarHistoryPort, FundingRateHistoryPort, Protocol):
    pass


__all__ = ["BarHistoryPort", "FundingRateHistoryPort", "HistoricalMarketDataPort"]
