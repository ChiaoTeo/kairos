from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Iterable, Mapping

import requests

from kairospy.infrastructure.data import DataSink
from kairospy.core.market import MarketEvent, MarketSubject, OrderBookSyncGap, OrderBookSynchronizer
from kairospy.core.reference import (
    LifecycleEvent,
    MarketDefinition,
    MarketRef,
    ReferenceCatalog,
)
from kairospy.application.service.domain.reference.builders import (
    catalog_from_market_rows,
    market_definitions_from_rows,
)

from kairospy.infrastructure.integrations.connectors.exchange.binance.reference import delist_schedule_events
from kairospy.infrastructure.integrations.payloads.ccxt_market import (
    ccxt_market_type,
    ccxt_order_book_delta,
    ccxt_ohlcv_record,
    ccxt_ohlcv_update,
    ccxt_funding_rate_record,
    ccxt_option_greeks_update,
    ccxt_order_book_record,
    ccxt_order_book_snapshot,
    ccxt_order_book_update,
    ccxt_ticker_record,
    ccxt_ticker_update,
    ccxt_trade_record,
    ccxt_trade_update,
    ephemeral_market_ref,
)
from kairospy.infrastructure.integrations.drivers import BinanceReferenceDriver, CcxtDriver


@dataclass(frozen=True, slots=True)
class BinanceMarketDataConnector:
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

    def fetch_ohlcv_updates(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[MarketEvent]:
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
        return (ccxt_ohlcv_update(row, market=market_ref, timeframe=timeframe) for row in rows)

    def fetch_funding_rate(
        self,
        symbol: str,
        *,
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        funding_params = {"type": "swap", **dict(params or {})}
        market_ref = _market_ref(self.exchange_id, symbol, funding_params)
        rows = self.driver.fetch_funding_rate(
            self.exchange_id,
            symbol,
            since=since,
            until=until,
            limit=limit,
            params=funding_params,
        )
        return (ccxt_funding_rate_record(row, market=market_ref) for row in rows)

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

    def watch_ticker_updates(
        self,
        symbol: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[MarketEvent]:
        return _ticker_updates(
            self.driver.watch_ticker(self.exchange_id, symbol, params=params),
            _market_ref(self.exchange_id, symbol, params),
        )

    def watch_option_greeks(
        self,
        symbol: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        option_params = {"type": "option", **dict(params or {})}
        return self.driver.watch_option_greeks(self.exchange_id, symbol, params=option_params)

    def watch_option_greeks_updates(
        self,
        symbol: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[MarketEvent]:
        option_params = {"type": "option", **dict(params or {})}
        return _option_greeks_updates(
            self.driver.watch_option_greeks(self.exchange_id, symbol, params=option_params),
            _market_ref(self.exchange_id, symbol, option_params),
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

    def watch_order_book_updates(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[MarketEvent]:
        if str((params or {}).get("derivation") or "").strip().lower() == "local_l2":
            return _local_l2_order_book_updates(self.driver, self.exchange_id, symbol, limit, params, _market_ref(self.exchange_id, symbol, params))
        return _order_book_updates(
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

    def watch_trades_updates(
        self,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[MarketEvent]:
        return _trade_updates(
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


def _market_ref(exchange_id: str, symbol: object, params: Mapping[str, object] | None) -> MarketRef:
    return ephemeral_market_ref(venue=exchange_id, market=ccxt_market_type(exchange_id, params), source_symbol=str(symbol))


async def _ticker_records(events, market):
    async for event in events:
        yield ccxt_ticker_record(event, market=market)


async def _ticker_updates(events, market):
    async for event in events:
        yield ccxt_ticker_update(event, market=market)


async def _option_greeks_updates(events, market):
    async for event in events:
        yield ccxt_option_greeks_update(event, market=market)


async def _order_book_records(events, market):
    async for event in events:
        yield ccxt_order_book_record(event, market=market)


async def _order_book_updates(events, market):
    async for event in events:
        yield ccxt_order_book_update(event, market=market)


async def _local_l2_order_book_updates(driver, exchange_id, symbol, limit, params, market):
    options = dict(params or {})
    snapshot_limit = _orderbook_snapshot_limit(limit, options)
    while True:
        stream = driver.watch_binance_depth_diffs(symbol, params=options).__aiter__()
        try:
            first_raw = await stream.__anext__()
            first_delta = ccxt_order_book_delta(first_raw, market=market)
            snapshot_raw = _fetch_binance_depth_snapshot(symbol, limit=snapshot_limit, params=options)
            snapshot_raw["derivation"] = "local_l2"
            synchronizer = OrderBookSynchronizer(ccxt_order_book_snapshot(snapshot_raw, market=market, fallback_to_now=True))
            first_nonce = _optional_int(first_delta.first_nonce)
            snapshot_nonce = _optional_int(synchronizer.current.nonce if synchronizer.current is not None else None)
            if first_nonce is not None and snapshot_nonce is not None and snapshot_nonce < first_nonce:
                continue
            yield _orderbook_event(synchronizer.current, market)
            try:
                applied = synchronizer.apply(first_delta)
            except OrderBookSyncGap:
                continue
            if applied.update_count:
                yield _orderbook_event(applied.book, market)
            async for raw in stream:
                try:
                    previous_count = synchronizer.update_count
                    applied = synchronizer.apply(ccxt_order_book_delta(raw, market=market))
                except OrderBookSyncGap:
                    break
                if applied.update_count > previous_count:
                    yield _orderbook_event(applied.book, market)
        finally:
            await _close_async_iterator(stream)


def _orderbook_event(book, market):
    return MarketEvent(
        subject=MarketSubject("instrument", book.instrument_id),
        observed_at=book.time,
        value=book,
        source=market.venue,
        available_at=book.time,
        metadata={"venue": market.venue, "market": market.market, "source_symbol": market.source_symbol, "derivation": "local_l2"},
    )


async def _close_async_iterator(iterator) -> None:
    close = getattr(iterator, "aclose", None)
    if callable(close):
        await close()


def _optional_int(value: object | None) -> int | None:
    if value is None:
        return None


def _fetch_binance_depth_snapshot(symbol: str, *, limit: int, params: Mapping[str, object]) -> dict[str, object]:
    url = _binance_depth_snapshot_url(params)
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(
            url,
            params={"symbol": _binance_rest_symbol(symbol), "limit": limit},
            timeout=float(params.get("snapshot_timeout_seconds") or 10.0),
        )
        response.raise_for_status()
        data = response.json()
        return dict(data) if isinstance(data, Mapping) else {}
    finally:
        session.close()


def _binance_depth_snapshot_url(params: Mapping[str, object]) -> str:
    market_type = str(params.get("market") or params.get("type") or "spot").strip().lower()
    if market_type in {"swap", "future", "futures", "perp", "perpetual"}:
        return "https://fapi.binance.com/fapi/v1/depth"
    return "https://api.binance.com/api/v3/depth"


def _orderbook_snapshot_limit(limit: object | None, params: Mapping[str, object]) -> int:
    configured = params.get("local_orderbook_limit")
    if configured is not None:
        return int(configured)
    if str(params.get("orderbook_depth") or "").strip().lower() == "full":
        return 5000
    return int(limit or 1000)


def _binance_rest_symbol(symbol: str) -> str:
    value = symbol.split(":", 1)[0]
    return "".join(part for part in value if part.isalnum()).upper()
    try:
        return int(value)
    except Exception:
        return None


async def _trade_records(events, market):
    async for event in events:
        yield ccxt_trade_record(event, market=market)


async def _trade_updates(events, market):
    async for event in events:
        yield ccxt_trade_update(event, market=market)


Binance = BinanceMarketDataConnector


__all__ = ["Binance", "BinanceMarketDataConnector"]
