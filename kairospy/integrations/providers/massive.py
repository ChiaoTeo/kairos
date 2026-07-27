from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Iterable, Mapping

from kairospy.integrations.massive_lifecycle import massive_corporate_action_events
from kairospy.integrations.drivers import MassiveDriver
from kairospy.core.reference import ReferenceCatalog
from kairospy.core.reference.model import LifecycleEvent


@dataclass(frozen=True, slots=True)
class Massive:
    driver: MassiveDriver = field(default_factory=MassiveDriver)
    name: str = "massive"

    def fetch_markets(
        self,
        *,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        return self.driver.fetch_markets(params=params)

    def fetch_splits(
        self,
        ticker: str,
        *,
        start: datetime,
        end: datetime,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        return self.driver.fetch_splits(ticker, start=start, end=end, params=params)

    def fetch_dividends(
        self,
        ticker: str,
        *,
        start: datetime,
        end: datetime,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        return self.driver.fetch_dividends(ticker, start=start, end=end, params=params)

    def fetch_ticker_events(
        self,
        ticker: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        return self.driver.fetch_ticker_events(ticker, params=params)

    def fetch_lifecycle_events(
        self,
        ticker: str,
        *,
        start: datetime,
        end: datetime,
        catalog: ReferenceCatalog,
        venue: str | None = None,
    ) -> tuple[LifecycleEvent, ...]:
        return massive_corporate_action_events(
            splits=self.fetch_splits(ticker, start=start, end=end),
            dividends=self.fetch_dividends(ticker, start=start, end=end),
            ticker_events=self.fetch_ticker_events(ticker),
            catalog=catalog,
            ticker=ticker,
            venue=venue,
        )

    def fetch_ohlcv(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        return self.driver.fetch_ohlcv(
            symbol,
            timeframe=timeframe,
            since=since,
            until=until,
            limit=limit,
            params=params,
        )

    def watch_trades(
        self,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        return self.driver.watch_trades(symbol, since=since, limit=limit, params=params)


__all__ = ["Massive"]
