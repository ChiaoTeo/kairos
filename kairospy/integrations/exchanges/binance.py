from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Iterable, Mapping

from kairospy.data import DataSink

from kairospy.integrations.binance_lifecycle import delist_schedule_events
from kairospy.integrations.ccxt.market_data import (
    ccxt_market_type,
    ccxt_ohlcv_record,
    ccxt_order_book_record,
    ccxt_ticker_record,
    ccxt_trade_record,
    ephemeral_market_ref,
)
from kairospy.integrations.instruments import catalog_from_market_rows, market_definitions_from_rows
from kairospy.integrations.drivers import BinanceReferenceDriver, CcxtDriver
from kairospy.core.reference import LifecycleEvent, MarketDefinition, MarketRef, ReferenceCatalog


@dataclass(frozen=True, slots=True)
class Binance:
    driver: CcxtDriver = field(default_factory=CcxtDriver)
    reference_driver: BinanceReferenceDriver = field(default_factory=BinanceReferenceDriver)
    name: str = "binance"
    exchange_id: str = "binance"

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

    def fetch_delist_schedule(
        self,
        *,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        return self.reference_driver.fetch_delist_schedule(params=params)

    def fetch_delist_events(
        self,
        *,
        catalog: ReferenceCatalog | None = None,
        market: str = "spot",
        params: Mapping[str, object] | None = None,
    ) -> tuple[LifecycleEvent, ...]:
        return delist_schedule_events(
            self.fetch_delist_schedule(params=params),
            catalog=catalog,
            venue=self.exchange_id,
            market=market,
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
        market_ref = _market_ref(self.exchange_id, symbol, params)
        rows = self.driver.fetch_ohlcv(
            self.exchange_id,
            symbol,
            timeframe=timeframe,
            since=since,
            until=until,
            limit=limit,
            params=params,
        )
        return (ccxt_ohlcv_record(row, market=market_ref, timeframe=timeframe) for row in rows)

    def watch_ticker(
        self,
        symbol: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        return _ticker_records(
            self.driver.watch_ticker(self.exchange_id, symbol, params=params),
            _market_ref(self.exchange_id, symbol, params),
        )

    def watch_order_book(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        return _order_book_records(
            self.driver.watch_order_book(self.exchange_id, symbol, limit=limit, params=params),
            _market_ref(self.exchange_id, symbol, params),
        )

    def watch_trades(
        self,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        return _trade_records(
            self.driver.watch_trades(self.exchange_id, symbol, since=since, limit=limit, params=params),
            _market_ref(self.exchange_id, symbol, params),
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


def _market_ref(exchange_id: str, symbol: str, params: Mapping[str, object] | None) -> MarketRef:
    return ephemeral_market_ref(venue=exchange_id, market=ccxt_market_type(exchange_id, params), source_symbol=symbol)


async def _ticker_records(events, market):
    async for event in events:
        yield ccxt_ticker_record(event, market=market)


async def _order_book_records(events, market):
    async for event in events:
        yield ccxt_order_book_record(event, market=market)


async def _trade_records(events, market):
    async for event in events:
        yield ccxt_trade_record(event, market=market)


__all__ = ["Binance"]
