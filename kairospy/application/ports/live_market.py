from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Protocol

from kairospy.core.market import MarketEvent
from kairospy.core.reference import MarketRef


class MarketStreamGateway(Protocol):
    def watch_ticker_updates(self, symbol: str, *, market: MarketRef, params: Mapping[str, object] | None = None) -> AsyncIterator[MarketEvent]:
        ...

    def watch_order_book_updates(
        self,
        symbol: str,
        *,
        market: MarketRef,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[MarketEvent]:
        ...

    def watch_trades_updates(
        self,
        symbol: str,
        *,
        market: MarketRef,
        since: object | None = None,
        limit: int = 50,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[MarketEvent]:
        ...

    def watch_option_greeks_updates(self, symbol: str, *, market: MarketRef, params: Mapping[str, object] | None = None) -> AsyncIterator[MarketEvent]:
        ...


__all__ = ["MarketStreamGateway"]
