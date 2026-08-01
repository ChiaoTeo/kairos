from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass

from kairospy.core.market import MarketEvent
from kairospy.core.reference import MarketRef
from kairospy.infrastructure.integrations.adapters.market_stream import MarketStreamAdapter
from kairospy.infrastructure.integrations.services.resolver import DEFAULT_INTEGRATION_RESOLVER, IntegrationResolver


@dataclass(frozen=True, slots=True)
class MarketIntegrationApplicationService:
    """Concrete market integration service exposed to application composition."""

    resolver: IntegrationResolver = DEFAULT_INTEGRATION_RESOLVER
    venue: str | None = None
    market: str | None = None
    credential: str | None = None
    mode_label: str = "runtime"
    error_type: type[Exception] = ValueError

    async def watch_ticker_updates(
        self,
        symbol: str,
        *,
        market: MarketRef,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[MarketEvent]:
        async for item in self._adapter(market).watch_ticker_updates(symbol, market=market, params=params):
            yield item

    async def watch_order_book_updates(
        self,
        symbol: str,
        *,
        market: MarketRef,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[MarketEvent]:
        async for item in self._adapter(market).watch_order_book_updates(symbol, market=market, limit=limit, params=params):
            yield item

    async def watch_trades_updates(
        self,
        symbol: str,
        *,
        market: MarketRef,
        since: object | None = None,
        limit: int = 50,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[MarketEvent]:
        async for item in self._adapter(market).watch_trades_updates(symbol, market=market, since=since, limit=limit, params=params):
            yield item

    async def watch_option_greeks_updates(
        self,
        symbol: str,
        *,
        market: MarketRef,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[MarketEvent]:
        async for item in self._adapter(market).watch_option_greeks_updates(symbol, market=market, params=params):
            yield item

    def _adapter(self, market: MarketRef) -> MarketStreamAdapter:
        resolved_venue = self.venue or str(market.venue)
        if self.market is None:
            feed = self.resolver.market_feed(resolved_venue, mode_label=self.mode_label, error_type=self.error_type)
        else:
            feed = self.resolver.market_feed_for_market(
                resolved_venue,
                self.market,
                credential=self.credential,
                mode_label=self.mode_label,
                error_type=self.error_type,
            )
        return MarketStreamAdapter(feed)


__all__ = ["MarketIntegrationApplicationService"]
