from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

from kairospy.core.market import MarketEvent
from kairospy.core.reference import MarketRef

from kairospy.infrastructure.integrations.payloads.ccxt_market import (
    ccxt_order_book_update,
    ccxt_option_greeks_update,
    ccxt_ticker_update,
    ccxt_trade_update,
)
from kairospy.infrastructure.integrations.protocols import RawMarketDataGateway


class MarketStreamAdapter:
    """Translate a raw integration feed into the application market contract."""

    def __init__(self, feed: RawMarketDataGateway) -> None:
        self.feed = feed

    async def watch_ticker_updates(self, symbol: str, *, market: MarketRef, params: Mapping[str, object] | None = None) -> AsyncIterator[MarketEvent]:
        async for raw in self.feed.watch_ticker(symbol, params=params):
            yield ccxt_ticker_update(raw, market=market)

    async def watch_order_book_updates(
        self,
        symbol: str,
        *,
        market: MarketRef,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[MarketEvent]:
        async for raw in self.feed.watch_order_book(symbol, limit=limit, params=params):
            yield ccxt_order_book_update(raw, market=market)

    async def watch_trades_updates(
        self,
        symbol: str,
        *,
        market: MarketRef,
        since: object | None = None,
        limit: int = 50,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[MarketEvent]:
        async for raw in self.feed.watch_trades(symbol, since=since, limit=limit, params=params):
            yield ccxt_trade_update(raw, market=market)

    async def watch_option_greeks_updates(self, symbol: str, *, market: MarketRef, params: Mapping[str, object] | None = None) -> AsyncIterator[MarketEvent]:
        async for raw in self.feed.watch_option_greeks(symbol, params=params):
            yield ccxt_option_greeks_update(raw, market=market)


__all__ = ["MarketStreamAdapter"]
