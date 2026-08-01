from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
import requests
from typing import AsyncIterator, Iterable, Mapping

from kairospy.core.market import Bar, MarketEvent
from kairospy.core.reference import MarketDefinition, MarketRef, ReferenceCatalog
from kairospy.infrastructure.persistence.market_data.ingest import DataSink
from kairospy.infrastructure.integrations.credentials import credential_value
from kairospy.infrastructure.integrations.drivers import CcxtDriver
from kairospy.infrastructure.integrations.payloads.ccxt_market import (
    ccxt_market_type,
    ccxt_ohlcv_bar,
    ccxt_ohlcv_update,
    ccxt_order_book_record,
    ccxt_order_book_update,
    ccxt_ticker_record,
    ccxt_ticker_update,
    ccxt_trade_record,
    ccxt_trade_update,
    ephemeral_market_ref,
)
from kairospy.application.domain.reference.builders import catalog_from_market_rows, market_definitions_from_rows
from kairospy.infrastructure.integrations.types import IntegrationParams, OrderBookRecordStream, QuoteRecordStream, RawPayload, RawPayloadRows, RawPayloadStream, TradeRecordStream


@dataclass(frozen=True, slots=True)
class OkxMarketDataConnector:
    driver: CcxtDriver = field(default_factory=lambda: CcxtDriver(_okx_exchange, _okx_async_exchange))
    name: str = "okx"
    exchange_id: str = "okx"

    def fetch_markets(self, *, params: IntegrationParams | None = None) -> RawPayloadRows:
        return self.driver.fetch_markets(self.exchange_id, params=params)

    def fetch_market_definitions(
        self,
        *,
        as_of: datetime | None = None,
        params: IntegrationParams | None = None,
    ) -> tuple[MarketDefinition, ...]:
        effective_from = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        return market_definitions_from_rows(self.fetch_markets(params=params), effective_from=effective_from)

    def fetch_reference_catalog(
        self,
        *,
        as_of: datetime | None = None,
        params: IntegrationParams | None = None,
    ) -> ReferenceCatalog:
        effective_from = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        return catalog_from_market_rows(self.fetch_markets(params=params), effective_from=effective_from)

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
        market_ref = _market_ref(self.exchange_id, symbol, adapter_options)
        rows = self.driver.fetch_ohlcv(
            self.exchange_id,
            symbol,
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

    def watch_ticker(
        self,
        symbol: str,
        *,
        params: IntegrationParams | None = None,
    ) -> QuoteRecordStream:
        return _ticker_records(
            _poll_ticker(self.driver, self.exchange_id, symbol, params=params),
            _market_ref(self.exchange_id, symbol, params),
        )

    def watch_ticker_updates(
        self,
        symbol: str,
        *,
        params: IntegrationParams | None = None,
    ) -> AsyncIterator[MarketEvent]:
        return _ticker_updates(
            _poll_ticker(self.driver, self.exchange_id, symbol, params=params),
            _market_ref(self.exchange_id, symbol, params),
        )

    def fetch_quote(self, market: MarketRef, *, params: IntegrationParams | None = None) -> RawPayload:
        symbol = str(market.source_symbol)
        raw = _fetch_okx_ticker(symbol)
        return ccxt_ticker_record(raw, market=_market_ref(self.exchange_id, symbol, params))

    def fetch_quote_update(self, market: MarketRef, *, params: IntegrationParams | None = None) -> MarketEvent:
        symbol = str(market.source_symbol)
        raw = _fetch_okx_ticker(symbol)
        return ccxt_ticker_update(raw, market=_market_ref(self.exchange_id, symbol, params))

    def watch_order_book(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> OrderBookRecordStream:
        return _order_book_records(
            self.driver.watch_order_book(self.exchange_id, symbol, limit=limit, params=params),
            _market_ref(self.exchange_id, symbol, params),
        )

    def watch_order_book_updates(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> AsyncIterator[MarketEvent]:
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
        params: IntegrationParams | None = None,
    ) -> TradeRecordStream:
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
        params: IntegrationParams | None = None,
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
        params: IntegrationParams | None = None,
    ) -> int:
        return await sink.consume(self.watch_ticker(symbol, params=params), limit=limit)

    async def persist_order_book(
        self,
        symbol: str,
        sink: DataSink,
        *,
        book_limit: int | None = None,
        limit: int | None = None,
        params: IntegrationParams | None = None,
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
        params: IntegrationParams | None = None,
    ) -> int:
        return await sink.consume(self.watch_trades(symbol, since=since, limit=trade_limit, params=params), limit=limit)


def _market_ref(exchange_id: str, symbol: object, params: RawPayload | None) -> MarketRef:
    return ephemeral_market_ref(venue=exchange_id, market=ccxt_market_type(exchange_id, params), source_symbol=str(symbol))


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


async def _poll_ticker(driver: CcxtDriver, exchange_id: str, symbol: str, *, params: RawPayload | None):
    options = dict(params or {})
    poll_seconds = float(options.get("poll_seconds", 1.0))
    max_events = options.get("max_events")
    remaining = int(max_events) if max_events is not None else None
    while remaining is None or remaining > 0:
        yield await asyncio.to_thread(_fetch_okx_ticker, symbol)
        if remaining is not None:
            remaining -= 1
            if remaining <= 0:
                return
        if poll_seconds > 0:
            await asyncio.sleep(poll_seconds)


def _fetch_okx_ticker(symbol: str) -> dict[str, object]:
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            response = requests.get(
                "https://www.okx.com/api/v5/market/ticker",
                params={"instId": _okx_inst_id(symbol)},
                proxies=_requests_proxies(),
                timeout=10,
            )
            break
        except requests.RequestException as error:
            last_error = error
            if attempt == 4:
                raise
            import time

            time.sleep(0.5 * (attempt + 1))
    else:
        raise RuntimeError("unreachable OKX ticker retry state") from last_error
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("data") if isinstance(payload, Mapping) else None
    if not rows:
        raise RuntimeError(f"OKX ticker response has no data: {payload!r}")
    row = dict(rows[0])
    return {
        "symbol": symbol,
        "timestamp": int(row["ts"]),
        "bid": row.get("bidPx") or None,
        "ask": row.get("askPx") or None,
        "last": row.get("last") or None,
        "baseVolume": row.get("vol24h") or None,
        "quoteVolume": row.get("volCcy24h") or None,
        "info": row,
    }


def _okx_inst_id(symbol: str) -> str:
    value = symbol.strip()
    if "-" in value:
        return value
    if "/" in value:
        base, quote = value.split("/", 1)
        return f"{base}-{quote.split(':', 1)[0]}"
    return value


def _requests_proxies() -> dict[str, str] | None:
    https = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    http = os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY") or https
    if https is None and http is None:
        return None
    proxies: dict[str, str] = {}
    if http is not None:
        proxies["http"] = http
    if https is not None:
        proxies["https"] = https
    return proxies


Okx = OkxMarketDataConnector


__all__ = ["Okx", "OkxMarketDataConnector"]


def okx_ccxt_driver(credential: str | None = None) -> CcxtDriver:
    return CcxtDriver(
        lambda exchange_id: _okx_exchange(exchange_id, credential=credential),
        lambda exchange_id: _okx_async_exchange(exchange_id, credential=credential),
    )


def _okx_config(credential: str | None = None) -> dict[str, object]:
    proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    config: dict[str, object] = {"enableRateLimit": True, "options": {"fetchMarkets": {"types": ["spot"]}}}
    api_key = _credential_env(credential, "API_KEY", "OKX_API_KEY", "OKEX_API_KEY")
    secret = _credential_env(credential, "SECRET", "OKX_SECRET", "OKEX_SECRET")
    password = _credential_env(credential, "PASSWORD", "OKX_PASSWORD", "OKEX_PASSWORD", "OKX_PASSPHRASE")
    if api_key:
        config["apiKey"] = api_key
    if secret:
        config["secret"] = secret
    if password:
        config["password"] = password
    if proxy:
        config["proxies"] = {"http": os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY") or proxy, "https": proxy}
        config["aiohttp_proxy"] = proxy
    return config


def _credential_env(credential: str | None, suffix: str, *fallbacks: str) -> str | None:
    return credential_value(credential, suffix, *fallbacks)


def _okx_exchange(exchange_id: str, *, credential: str | None = None):
    try:
        import ccxt
    except ImportError as error:
        raise RuntimeError("ccxt driver requires ccxt") from error
    return ccxt.okx(_okx_config(credential))


def _okx_async_exchange(exchange_id: str, *, credential: str | None = None):
    try:
        import ccxt.pro as ccxt_pro
    except ImportError:
        ccxt_pro = None
    if ccxt_pro is not None:
        return ccxt_pro.okx(_okx_config(credential))
    try:
        import ccxt.async_support as ccxt_async
    except ImportError as error:
        raise RuntimeError("ccxt live driver requires ccxt.pro or ccxt async_support") from error
    return ccxt_async.okx(_okx_config(credential))
