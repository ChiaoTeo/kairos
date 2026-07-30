from __future__ import annotations

from kairospy.core.market import (
    BarWindow,
    MarketViewExchange,
    MarketViewMarketType,
    MarketViewReader,
    MarketViewSubject,
    OrderBookWindow,
    OptionGreeksWindow,
    QuoteWindow,
    RateWindow,
    TradeWindow,
)
from kairospy.core.views import ViewStore


class StrategyViews:
    def __init__(self, views: ViewStore) -> None:
        self._views = views
        self.market = StrategyMarketViews(views)

    def get(self, key: str, default: object = None) -> object:
        return self._views.get(key, default)

    def require(self, key: str) -> object:
        return self._views.require(key)


class StrategyMarketViews:
    def __init__(self, views: ViewStore) -> None:
        self._reader = MarketViewReader(views)

    def quotes(
        self,
        subject: MarketViewSubject,
        *,
        exchange: MarketViewExchange | None = None,
        market_type: MarketViewMarketType | None = None,
    ) -> QuoteWindow:
        return self._reader.quotes(subject, exchange=exchange, market_type=market_type)

    def trades(
        self,
        subject: MarketViewSubject,
        *,
        exchange: MarketViewExchange | None = None,
        market_type: MarketViewMarketType | None = None,
    ) -> TradeWindow:
        return self._reader.trades(subject, exchange=exchange, market_type=market_type)

    def bars(
        self,
        subject: MarketViewSubject,
        *,
        timeframe: str | None = None,
        exchange: MarketViewExchange | None = None,
        market_type: MarketViewMarketType | None = None,
    ) -> BarWindow:
        return self._reader.bars(subject, timeframe=timeframe, exchange=exchange, market_type=market_type)

    def rates(
        self,
        subject: MarketViewSubject,
        *,
        basis: str | None = None,
        exchange: MarketViewExchange | None = None,
        market_type: MarketViewMarketType | None = None,
    ) -> RateWindow:
        return self._reader.rates(subject, basis=basis, exchange=exchange, market_type=market_type)

    def option_greeks(
        self,
        subject: MarketViewSubject,
        *,
        exchange: MarketViewExchange | None = None,
        market_type: MarketViewMarketType | None = None,
    ) -> OptionGreeksWindow:
        return self._reader.option_greeks(subject, exchange=exchange, market_type=market_type)

    def orderbooks(
        self,
        subject: MarketViewSubject,
        *,
        exchange: MarketViewExchange | None = None,
        market_type: MarketViewMarketType | None = None,
    ) -> OrderBookWindow:
        return self._reader.orderbooks(subject, exchange=exchange, market_type=market_type)


__all__ = ["StrategyMarketViews", "StrategyViews"]
