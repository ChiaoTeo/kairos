from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Iterable

from kairospy.core.market import Bar, MarketEvent
from kairospy.core.reference import (
    MarketRef,
)

from kairospy.infrastructure.integrations.payloads.ccxt_market import (
    ccxt_market_type,
    ccxt_ohlcv_bar,
    ccxt_ohlcv_update,
    ccxt_order_book_update,
    ccxt_ticker_update,
    ccxt_trade_update,
    ephemeral_market_ref,
)
from kairospy.infrastructure.integrations.drivers import CcxtDriver
from kairospy.infrastructure.integrations.payloads.types import IntegrationParams, RawPayload, RawPayloadRows, RawPayloadStream


@dataclass(frozen=True, slots=True)
class HyperliquidMarketDataConnector:
    driver: CcxtDriver = field(default_factory=CcxtDriver)
    name: str = "hyperliquid"
    exchange_id: str = "hyperliquid"

    def fetch_markets(
        self,
        *,
        params: IntegrationParams | None = None,
    ) -> RawPayloadRows:
        return self.driver.fetch_markets(self.exchange_id, params=params)

    def fetch_bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
        adapter_options: IntegrationParams | None = None,
    ) -> Iterable[Bar]:
        ccxt_symbol = _ccxt_symbol(symbol)
        market_ref = _market_ref(self.exchange_id, ccxt_symbol, adapter_options)
        rows = self.driver.fetch_ohlcv(
            self.exchange_id,
            ccxt_symbol,
            timeframe=timeframe,
            since=since,
            until=until,
            limit=limit,
            params=adapter_options,
        )
        return (ccxt_ohlcv_bar(row, market=market_ref, timeframe=timeframe) for row in rows)

    def fetch_ohlcv_updates(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
        params: IntegrationParams | None = None,
    ) -> Iterable[MarketEvent]:
        ccxt_symbol = _ccxt_symbol(symbol)
        market_ref = _market_ref(self.exchange_id, ccxt_symbol, params)
        rows = self.driver.fetch_ohlcv(
            self.exchange_id,
            ccxt_symbol,
            timeframe=timeframe,
            since=since,
            until=until,
            limit=limit,
            params=params,
        )
        return (ccxt_ohlcv_update(row, market=market_ref, timeframe=timeframe) for row in rows)

    def watch_ticker(
        self,
        symbol: str,
        *,
        params: IntegrationParams | None = None,
    ) -> RawPayloadStream:
        ccxt_symbol = _ccxt_symbol(symbol)
        return self.driver.watch_ticker(self.exchange_id, ccxt_symbol, params=params)

    def watch_ticker_updates(
        self,
        symbol: str,
        *,
        params: IntegrationParams | None = None,
    ) -> AsyncIterator[MarketEvent]:
        ccxt_symbol = _ccxt_symbol(symbol)
        return _ticker_updates(
            self.driver.watch_ticker(self.exchange_id, ccxt_symbol, params=params),
            _market_ref(self.exchange_id, ccxt_symbol, params),
        )

    def fetch_quote(
        self,
        market: MarketRef,
        *,
        params: IntegrationParams | None = None,
    ) -> RawPayload:
        ccxt_symbol = _ccxt_symbol(market.source_symbol)
        return self.driver.fetch_ticker(self.exchange_id, ccxt_symbol, params=params)

    def fetch_quote_update(
        self,
        market: MarketRef,
        *,
        params: IntegrationParams | None = None,
    ) -> MarketEvent:
        ccxt_symbol = _ccxt_symbol(market.source_symbol)
        raw = self.driver.fetch_ticker(self.exchange_id, ccxt_symbol, params=params)
        return ccxt_ticker_update(raw, market=_market_ref(self.exchange_id, ccxt_symbol, params))

    def watch_order_book(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> RawPayloadStream:
        ccxt_symbol = _ccxt_symbol(symbol)
        return self.driver.watch_order_book(self.exchange_id, ccxt_symbol, limit=limit, params=params)

    def watch_order_book_updates(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> AsyncIterator[MarketEvent]:
        ccxt_symbol = _ccxt_symbol(symbol)
        return _order_book_updates(
            self.driver.watch_order_book(self.exchange_id, ccxt_symbol, limit=limit, params=params),
            _market_ref(self.exchange_id, ccxt_symbol, params),
        )

    def watch_trades(
        self,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: IntegrationParams | None = None,
    ) -> RawPayloadStream:
        ccxt_symbol = _ccxt_symbol(symbol)
        return self.driver.watch_trades(self.exchange_id, ccxt_symbol, since=since, limit=limit, params=params)

    def watch_trades_updates(
        self,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: IntegrationParams | None = None,
    ) -> AsyncIterator[MarketEvent]:
        ccxt_symbol = _ccxt_symbol(symbol)
        return _trade_updates(
            self.driver.watch_trades(self.exchange_id, ccxt_symbol, since=since, limit=limit, params=params),
            _market_ref(self.exchange_id, ccxt_symbol, params),
        )

def _ccxt_symbol(symbol: object) -> str:
    value = str(symbol).strip()
    if "/" in value:
        return value
    return f"{value.upper()}/USDC:USDC"


def _market_ref(exchange_id: str, symbol: object, params: RawPayload | None) -> MarketRef:
    return ephemeral_market_ref(venue=exchange_id, market=ccxt_market_type(exchange_id, params), source_symbol=str(symbol))


async def _ticker_updates(events, market):
    async for event in events:
        yield ccxt_ticker_update(event, market=market)


async def _order_book_updates(events, market):
    async for event in events:
        yield ccxt_order_book_update(event, market=market)


async def _trade_updates(events, market):
    async for event in events:
        yield ccxt_trade_update(event, market=market)


Hyperliquid = HyperliquidMarketDataConnector


__all__ = ["Hyperliquid", "HyperliquidMarketDataConnector"]
