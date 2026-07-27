from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Iterable, Mapping

from kairospy.data import DataSink
from kairospy.reference import MarketRef

from kairospy.integrations.instruments import catalog_from_market_rows, market_definitions_from_rows
from kairospy.integrations.drivers import CcxtDriver
from kairospy.reference import MarketDefinition, ReferenceCatalog


@dataclass(frozen=True, slots=True)
class Hyperliquid:
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
        return self.driver.fetch_ohlcv(
            self.exchange_id,
            _ccxt_symbol(symbol),
            timeframe=timeframe,
            since=since,
            until=until,
            limit=limit,
            params=params,
        )

    def watch_ticker(
        self,
        symbol: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        return self.driver.watch_ticker(self.exchange_id, _ccxt_symbol(symbol), params=params)

    def fetch_quote(
        self,
        market: MarketRef,
        *,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        return self.driver.fetch_ticker(self.exchange_id, _ccxt_symbol(market.source_symbol), params=params)

    def watch_order_book(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        return self.driver.watch_order_book(self.exchange_id, _ccxt_symbol(symbol), limit=limit, params=params)

    def watch_trades(
        self,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        return self.driver.watch_trades(self.exchange_id, _ccxt_symbol(symbol), since=since, limit=limit, params=params)

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
