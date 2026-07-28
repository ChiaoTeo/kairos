from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Iterable, Mapping

from kairospy.data import DataSink
from kairospy.core.market import MarketUpdate
from kairospy.core.reference import (
    MarketDefinition,
    MarketRef,
    ReferenceCatalog,
)
from kairospy.service.domains.reference.builders import (
    catalog_from_market_rows,
    market_definitions_from_rows,
)

from kairospy.integrations.payloads.ccxt_market import (
    ccxt_market_type,
    ccxt_ohlcv_record,
    ccxt_ohlcv_update,
    ccxt_order_book_record,
    ccxt_order_book_update,
    ccxt_ticker_record,
    ccxt_ticker_update,
    ccxt_trade_record,
    ccxt_trade_update,
    ephemeral_market_ref,
)
from kairospy.integrations.drivers import CcxtDriver


@dataclass(frozen=True, slots=True)
class HyperliquidMarketDataConnector:
    driver: CcxtDriver = field(default_factory=CcxtDriver)
    name: str = "hyperliquid"
    exchange_id: str = "hyperliquid"

    def fetch_markets(
        self,
        *,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        return self.driver.fetch_markets(self.exchange_id, params=params)

    def fetch_market_definitions(
        self,
        *,
        as_of: datetime | None = None,
        params: Mapping[str, object] | None = None,
    ) -> tuple[MarketDefinition, ...]:
        effective_from = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        return market_definitions_from_rows(self.fetch_markets(params=params), effective_from=effective_from)

    def fetch_reference_catalog(
        self,
        *,
        as_of: datetime | None = None,
        params: Mapping[str, object] | None = None,
    ) -> ReferenceCatalog:
        effective_from = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        return catalog_from_market_rows(self.fetch_markets(params=params), effective_from=effective_from)

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
        return (ccxt_ohlcv_record(row, market=market_ref, timeframe=timeframe) for row in rows)

    def fetch_ohlcv_updates(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[MarketUpdate]:
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
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        ccxt_symbol = _ccxt_symbol(symbol)
        return _ticker_records(
            self.driver.watch_ticker(self.exchange_id, ccxt_symbol, params=params),
            _market_ref(self.exchange_id, ccxt_symbol, params),
        )

    def watch_ticker_updates(
        self,
        symbol: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[MarketUpdate]:
        ccxt_symbol = _ccxt_symbol(symbol)
        return _ticker_updates(
            self.driver.watch_ticker(self.exchange_id, ccxt_symbol, params=params),
            _market_ref(self.exchange_id, ccxt_symbol, params),
        )

    def fetch_quote(
        self,
        market: MarketRef,
        *,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        ccxt_symbol = _ccxt_symbol(market.source_symbol)
        raw = self.driver.fetch_ticker(self.exchange_id, ccxt_symbol, params=params)
        return ccxt_ticker_record(raw, market=_market_ref(self.exchange_id, ccxt_symbol, params))

    def fetch_quote_update(
        self,
        market: MarketRef,
        *,
        params: Mapping[str, object] | None = None,
    ) -> MarketUpdate:
        ccxt_symbol = _ccxt_symbol(market.source_symbol)
        raw = self.driver.fetch_ticker(self.exchange_id, ccxt_symbol, params=params)
        return ccxt_ticker_update(raw, market=_market_ref(self.exchange_id, ccxt_symbol, params))

    def watch_order_book(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        ccxt_symbol = _ccxt_symbol(symbol)
        return _order_book_records(
            self.driver.watch_order_book(self.exchange_id, ccxt_symbol, limit=limit, params=params),
            _market_ref(self.exchange_id, ccxt_symbol, params),
        )

    def watch_order_book_updates(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[MarketUpdate]:
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
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        ccxt_symbol = _ccxt_symbol(symbol)
        return _trade_records(
            self.driver.watch_trades(self.exchange_id, ccxt_symbol, since=since, limit=limit, params=params),
            _market_ref(self.exchange_id, ccxt_symbol, params),
        )

    def watch_trades_updates(
        self,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[MarketUpdate]:
        ccxt_symbol = _ccxt_symbol(symbol)
        return _trade_updates(
            self.driver.watch_trades(self.exchange_id, ccxt_symbol, since=since, limit=limit, params=params),
            _market_ref(self.exchange_id, ccxt_symbol, params),
        )

    async def persist_ticker(
        self,
        symbol: str,
        sink: DataSink,
        *,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> int:
        return await sink.consume(self.watch_ticker(symbol, params=params), limit=limit)

    async def persist_order_book(
        self,
        symbol: str,
        sink: DataSink,
        *,
        book_limit: int | None = None,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> int:
        return await sink.consume(self.watch_order_book(symbol, limit=book_limit, params=params), limit=limit)

    async def persist_trades(
        self,
        symbol: str,
        sink: DataSink,
        *,
        since: object | None = None,
        trade_limit: int = 50,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> int:
        return await sink.consume(
            self.watch_trades(symbol, since=since, limit=trade_limit, params=params),
            limit=limit,
        )


def _ccxt_symbol(symbol: str) -> str:
    value = symbol.strip()
    if "/" in value:
        return value
    return f"{value.upper()}/USDC:USDC"


def _market_ref(exchange_id: str, symbol: str, params: Mapping[str, object] | None) -> MarketRef:
    return ephemeral_market_ref(venue=exchange_id, market=ccxt_market_type(exchange_id, params), source_symbol=symbol)


async def _ticker_records(events, market):
    async for event in events:
        yield ccxt_ticker_record(event, market=market)


async def _ticker_updates(events, market):
    async for event in events:
        yield ccxt_ticker_update(event, market=market)


async def _order_book_records(events, market):
    async for event in events:
        yield ccxt_order_book_record(event, market=market)


async def _order_book_updates(events, market):
    async for event in events:
        yield ccxt_order_book_update(event, market=market)


async def _trade_records(events, market):
    async for event in events:
        yield ccxt_trade_record(event, market=market)


async def _trade_updates(events, market):
    async for event in events:
        yield ccxt_trade_update(event, market=market)


Hyperliquid = HyperliquidMarketDataConnector


__all__ = ["Hyperliquid", "HyperliquidMarketDataConnector"]
