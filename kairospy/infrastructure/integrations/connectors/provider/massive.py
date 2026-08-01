from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from kairospy.infrastructure.integrations.connectors.provider.massive_reference import massive_corporate_action_events
from kairospy.infrastructure.integrations.drivers import MassiveDriver
from kairospy.core.reference import ReferenceCatalog
from kairospy.core.reference.model import LifecycleEvent
from kairospy.infrastructure.integrations.types import IntegrationParams, RawPayloadRows, RawPayloadStream


@dataclass(frozen=True, slots=True)
class Massive:
    driver: MassiveDriver = field(default_factory=MassiveDriver)
    name: str = "massive"

    def fetch_markets(
        self,
        *,
        params: IntegrationParams | None = None,
    ) -> RawPayloadRows:
        return self.driver.fetch_markets(params=params)

    def fetch_splits(
        self,
        ticker: str,
        *,
        start: datetime,
        end: datetime,
        params: IntegrationParams | None = None,
    ) -> RawPayloadRows:
        return self.driver.fetch_splits(ticker, start=start, end=end, params=params)

    def fetch_dividends(
        self,
        ticker: str,
        *,
        start: datetime,
        end: datetime,
        params: IntegrationParams | None = None,
    ) -> RawPayloadRows:
        return self.driver.fetch_dividends(ticker, start=start, end=end, params=params)

    def fetch_ticker_events(
        self,
        ticker: str,
        *,
        params: IntegrationParams | None = None,
    ) -> RawPayloadRows:
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

    def watch_trades(
        self,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: IntegrationParams | None = None,
    ) -> RawPayloadStream:
        return self.driver.watch_trades(symbol, since=since, limit=limit, params=params)


__all__ = ["Massive"]
