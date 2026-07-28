from __future__ import annotations

from dataclasses import dataclass

from kairospy.core.market import Quote, RateObservation
from kairospy.core.reference import MarketRef, MarketResolver

from .store import MarketState
from .views import MarketBarSummary, MarketBookSummary, MarketTradeSummary


@dataclass(frozen=True, slots=True)
class MarketAccess:
    resolver: MarketResolver
    state: MarketState

    def latest_quote(
        self,
        instrument: object | MarketRef,
        *,
        venue: str | None = None,
        market: str | None = None,
    ) -> Quote | None:
        resolved = self.resolver.resolve(instrument, venue=venue, market=market)
        return self.state.latest_quote(resolved.market_key)

    def latest_rate(self, rate_id: object) -> RateObservation | None:
        return self.state.latest_rate(str(rate_id))

    def latest_book(
        self,
        instrument: object | MarketRef,
        *,
        venue: str | None = None,
        market: str | None = None,
    ) -> MarketBookSummary | None:
        resolved = self.resolver.resolve(instrument, venue=venue, market=market)
        return self.state.latest_book(resolved.market_key)

    def latest_bar(
        self,
        instrument: object | MarketRef,
        *,
        venue: str | None = None,
        market: str | None = None,
        timeframe: str | None = None,
    ) -> MarketBarSummary | None:
        resolved = self.resolver.resolve(instrument, venue=venue, market=market)
        return self.state.latest_bar(resolved.market_key, timeframe=timeframe)

    def latest_trade(
        self,
        instrument: object | MarketRef,
        *,
        venue: str | None = None,
        market: str | None = None,
    ) -> MarketTradeSummary | None:
        resolved = self.resolver.resolve(instrument, venue=venue, market=market)
        return self.state.latest_trade(resolved.market_key)

    def latest_funding(
        self,
        instrument: object | MarketRef,
        *,
        venue: str | None = None,
        market: str | None = None,
    ) -> RateObservation | None:
        resolved = self.resolver.resolve(instrument, venue=venue, market=market)
        return self.state.latest_rate(resolved.market_id)

__all__ = ["MarketAccess"]
