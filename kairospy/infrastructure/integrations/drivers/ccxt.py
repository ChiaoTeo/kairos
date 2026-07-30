from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Iterable, Mapping


SyncExchangeFactory = Callable[[str], Any]
AsyncExchangeFactory = Callable[[str], Any]


@dataclass(frozen=True, slots=True)
class CcxtDriver:
    exchange_factory: SyncExchangeFactory | None = None
    async_exchange_factory: AsyncExchangeFactory | None = None
    require_websocket: bool = False

    def fetch_markets(
        self,
        exchange_id: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        options = dict(params or {})
        market = _market_type(exchange_id, options)
        exchange = (self.exchange_factory or _default_exchange)(exchange_id)
        try:
            load_markets = getattr(exchange, "load_markets", None)
            if callable(load_markets):
                try:
                    loaded = load_markets(params=_exchange_params(options))
                except TypeError:
                    loaded = load_markets()
                markets = loaded if isinstance(loaded, Mapping) else getattr(exchange, "markets", None)
            else:
                markets = getattr(exchange, "markets", None)
            if markets is None:
                raise RuntimeError(f"ccxt exchange {exchange_id} does not expose markets")
            rows = markets.values() if isinstance(markets, Mapping) else markets
            return tuple(_market_record(exchange_id, market, item) for item in rows)
        finally:
            close = getattr(exchange, "close", None)
            if callable(close):
                close()

    def fetch_ohlcv(
        self,
        exchange_id: str,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        symbol = _symbol_text(symbol)
        options = dict(params or {})
        max_pages = int(options.pop("max_pages", 1 if until is None else 1000))
        since_ms = _optional_millis(since)
        until_ms = _optional_millis(until)
        exchange = (self.exchange_factory or _default_exchange)(exchange_id)
        try:
            _configure_exchange_market(exchange, exchange_id, options)
            pages = 0
            while pages < max_pages:
                rows = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=limit, params=_exchange_params(options))
                if not rows:
                    break
                pages += 1
                last_time = None
                yielded = 0
                for item in rows:
                    event_time = int(item[0])
                    last_time = event_time
                    if until_ms is not None and event_time >= until_ms:
                        return
                    yield tuple(item)
                    yielded += 1
                if until_ms is None or yielded == 0 or last_time is None:
                    break
                since_ms = last_time + 1
                if len(rows) < limit:
                    break
        finally:
            close = getattr(exchange, "close", None)
            if callable(close):
                close()

    def fetch_funding_rate(
        self,
        exchange_id: str,
        symbol: str,
        *,
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        symbol = _symbol_text(symbol)
        options = dict(params or {})
        max_pages = int(options.pop("max_pages", 1 if until is None else 1000))
        since_ms = _optional_millis(since)
        until_ms = _optional_millis(until)
        exchange = (self.exchange_factory or _default_exchange)(exchange_id)
        try:
            _configure_exchange_market(exchange, exchange_id, options)
            method = getattr(exchange, "fetch_funding_rate_history", None)
            if not callable(method):
                method = getattr(exchange, "fetchFundingRateHistory", None)
            if not callable(method):
                raise NotImplementedError(f"ccxt exchange {exchange_id} does not expose funding rate history")
            pages = 0
            while pages < max_pages:
                rows = method(symbol, since=since_ms, limit=limit, params=_exchange_params(options))
                if not rows:
                    break
                pages += 1
                last_time = None
                yielded = 0
                for item in rows:
                    row = dict(item)
                    event_time = _funding_timestamp(row)
                    if event_time is None:
                        continue
                    last_time = event_time
                    if until_ms is not None and event_time >= until_ms:
                        return
                    yield row
                    yielded += 1
                if until_ms is None or yielded == 0 or last_time is None:
                    break
                since_ms = last_time + 1
                if len(rows) < limit:
                    break
        finally:
            close = getattr(exchange, "close", None)
            if callable(close):
                close()

    def fetch_ticker(
        self,
        exchange_id: str,
        symbol: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        symbol = _symbol_text(symbol)
        options = dict(params or {})
        exchange = (self.exchange_factory or _default_exchange)(exchange_id)
        try:
            return dict(exchange.fetch_ticker(symbol, params=_exchange_params(options)))
        finally:
            close = getattr(exchange, "close", None)
            if callable(close):
                close()

    def fetch_order_book(
        self,
        exchange_id: str,
        symbol: str,
        *,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        symbol = _symbol_text(symbol)
        options = dict(params or {})
        exchange = (self.exchange_factory or _default_exchange)(exchange_id)
        try:
            _configure_exchange_market(exchange, exchange_id, options)
            return dict(exchange.fetch_order_book(symbol, limit=limit, params=_exchange_params(options)))
        finally:
            close = getattr(exchange, "close", None)
            if callable(close):
                close()

    def watch_ticker(
        self,
        exchange_id: str,
        symbol: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        return self._poll(exchange_id, "ticker", _symbol_text(symbol), dict(params or {}))

    def watch_order_book(
        self,
        exchange_id: str,
        symbol: str,
        *,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        options = dict(params or {})
        if limit is not None:
            options["limit"] = limit
        return self._poll(exchange_id, "orderbook", _symbol_text(symbol), options)

    def watch_binance_depth_diffs(
        self,
        symbol: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        return _watch_binance_depth_diffs(_symbol_text(symbol), dict(params or {}))

    def watch_trades(
        self,
        exchange_id: str,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        options = dict(params or {})
        options.setdefault("since", since)
        options.setdefault("limit", limit)
        return self._poll(exchange_id, "trades", _symbol_text(symbol), options)

    def watch_option_greeks(
        self,
        exchange_id: str,
        symbol: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        return self._poll(exchange_id, "option_greeks", _symbol_text(symbol), dict(params or {}))

    def create_order(
        self,
        exchange_id: str,
        symbol: str,
        *,
        side: str,
        type: str,
        amount: object,
        price: object | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        symbol = _symbol_text(symbol)
        exchange = (self.exchange_factory or _default_exchange)(exchange_id)
        try:
            return exchange.create_order(symbol, type, side, amount, price, params or {})
        finally:
            close = getattr(exchange, "close", None)
            if callable(close):
                close()

    def cancel_order(
        self,
        exchange_id: str,
        id: str,
        *,
        symbol: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        symbol = _optional_symbol_text(symbol)
        exchange = (self.exchange_factory or _default_exchange)(exchange_id)
        try:
            return exchange.cancel_order(id, symbol, params or {})
        finally:
            close = getattr(exchange, "close", None)
            if callable(close):
                close()

    def fetch_balance(self, exchange_id: str, *, params: Mapping[str, object] | None = None) -> Mapping[str, object]:
        exchange = (self.exchange_factory or _default_exchange)(exchange_id)
        try:
            return exchange.fetch_balance(params or {})
        finally:
            close = getattr(exchange, "close", None)
            if callable(close):
                close()

    def fetch_open_orders(
        self,
        exchange_id: str,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        symbol = _optional_symbol_text(symbol)
        exchange = (self.exchange_factory or _default_exchange)(exchange_id)
        try:
            return tuple(
                exchange.fetch_open_orders(
                    symbol,
                    since=_optional_millis(since),
                    limit=limit,
                    params=params or {},
                )
            )
        finally:
            close = getattr(exchange, "close", None)
            if callable(close):
                close()

    def fetch_closed_orders(
        self,
        exchange_id: str,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        symbol = _optional_symbol_text(symbol)
        exchange = (self.exchange_factory or _default_exchange)(exchange_id)
        try:
            options = params or {}
            method = getattr(exchange, "fetch_closed_orders", None)
            if callable(method):
                return tuple(
                    method(
                        symbol,
                        since=_optional_millis(since),
                        limit=limit,
                        params=options,
                    )
                )
            method = getattr(exchange, "fetch_orders", None)
            if callable(method):
                rows = method(
                    symbol,
                    since=_optional_millis(since),
                    limit=limit,
                    params=options,
                )
                return tuple(row for row in rows if row.get("status") not in {"open", "new"})
            raise NotImplementedError(f"ccxt exchange {exchange_id} does not expose historical orders")
        finally:
            close = getattr(exchange, "close", None)
            if callable(close):
                close()

    def watch_balance(
        self,
        exchange_id: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        return self._poll_account(exchange_id, "balance", None, dict(params or {}))

    def watch_orders(
        self,
        exchange_id: str,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        options = dict(params or {})
        options.setdefault("since", since)
        options.setdefault("limit", limit)
        return self._poll_account(exchange_id, "orders", _optional_symbol_text(symbol), options)

    def watch_my_trades(
        self,
        exchange_id: str,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        options = dict(params or {})
        options.setdefault("since", since)
        options.setdefault("limit", limit)
        return self._poll_account(exchange_id, "my_trades", _optional_symbol_text(symbol), options)

    async def _poll(
        self,
        exchange_id: str,
        source: str,
        symbol: str,
        params: Mapping[str, object],
    ) -> AsyncIterator[Mapping[str, object]]:
        poll_seconds = float(params.get("poll_seconds", 1.0))
        max_events = params.get("max_events")
        remaining = int(max_events) if max_events is not None else None
        loop = asyncio.get_running_loop()
        previous_exception_handler = loop.get_exception_handler()
        loop.set_exception_handler(_ignore_cancelled_loop_exception(previous_exception_handler))
        exchange = None
        try:
            exchange = (self.async_exchange_factory or _default_async_exchange)(exchange_id)
            while remaining is None or remaining > 0:
                async for event in _fetch_live(
                    exchange_id,
                    source,
                    exchange,
                    symbol,
                    params,
                    require_websocket=self.require_websocket or bool(params.get("require_ws")),
                ):
                    yield event
                    if remaining is not None:
                        remaining -= 1
                        if remaining <= 0:
                            return
                if poll_seconds > 0:
                    await asyncio.sleep(poll_seconds)
        finally:
            if exchange is not None:
                close = getattr(exchange, "close", None)
                if callable(close):
                    result = close()
                    if hasattr(result, "__await__"):
                        await result
            loop.set_exception_handler(previous_exception_handler)

    async def _poll_account(
        self,
        exchange_id: str,
        source: str,
        symbol: str | None,
        params: Mapping[str, object],
    ) -> AsyncIterator[Mapping[str, object]]:
        poll_seconds = float(params.get("poll_seconds", 1.0))
        max_events = params.get("max_events")
        remaining = int(max_events) if max_events is not None else None
        loop = asyncio.get_running_loop()
        previous_exception_handler = loop.get_exception_handler()
        loop.set_exception_handler(_ignore_cancelled_loop_exception(previous_exception_handler))
        exchange = None
        try:
            exchange = (self.async_exchange_factory or _default_async_exchange)(exchange_id)
            while remaining is None or remaining > 0:
                async for event in _fetch_account_live(source, exchange, symbol, params):
                    yield event
                    if remaining is not None:
                        remaining -= 1
                        if remaining <= 0:
                            return
                if poll_seconds > 0:
                    await asyncio.sleep(poll_seconds)
        finally:
            if exchange is not None:
                close = getattr(exchange, "close", None)
                if callable(close):
                    result = close()
                    if hasattr(result, "__await__"):
                        await result
            loop.set_exception_handler(previous_exception_handler)


async def _fetch_live(
    exchange_id: str,
    source: str,
    exchange: Any,
    symbol: str,
    params: Mapping[str, object],
    *,
    require_websocket: bool = False,
) -> AsyncIterator[dict[str, object]]:
    if source == "ticker":
        try:
            ticker = await _watch_ticker(exchange, symbol, params)
        except _WsUnavailable:
            if require_websocket:
                raise
            ticker = await exchange.fetch_ticker(symbol)
        yield dict(ticker)
        return
    if source == "orderbook":
        limit = params.get("limit")
        book_limit = int(limit) if limit is not None else None
        try:
            book = await _watch_order_book(exchange, symbol, book_limit, params)
        except _WsUnavailable:
            if require_websocket:
                raise
            book = await exchange.fetch_order_book(symbol, limit=book_limit)
        yield dict(book)
        return
    if source == "trades":
        limit = int(params.get("limit", 50))
        since = _optional_millis(params.get("since"))
        try:
            trades = await _watch_trades(exchange, symbol, since, limit, params)
        except _WsUnavailable:
            if require_websocket:
                raise
            trades = await exchange.fetch_trades(symbol, since=since, limit=limit)
        for trade in trades:
            yield dict(trade)
        return
    if source == "option_greeks":
        try:
            greeks = await _watch_option_greeks(exchange, symbol, params)
        except _WsUnavailable:
            if require_websocket:
                raise
            greeks = await _fetch_option_greeks(exchange, symbol, params)
        yield dict(greeks)
        return
    raise KeyError(f"ccxt live source is not supported: {source}")


async def _fetch_account_live(
    source: str,
    exchange: Any,
    symbol: str | None,
    params: Mapping[str, object],
) -> AsyncIterator[Mapping[str, object]]:
    if source == "balance":
        watch = getattr(exchange, "watch_balance", None)
        if not callable(watch):
            raise _WsUnavailable("watch_balance is not supported by this ccxt exchange")
        await _ensure_markets_loaded(exchange)
        yield await watch(params=_exchange_params(params))
        return
    if source == "orders":
        watch = getattr(exchange, "watch_orders", None)
        if not callable(watch):
            raise _WsUnavailable("watch_orders is not supported by this ccxt exchange")
        await _ensure_markets_loaded(exchange)
        since = _optional_millis(params.get("since"))
        limit = params.get("limit")
        orders = await watch(symbol, since=since, limit=None if limit is None else int(limit), params=_exchange_params(params))
        for order in orders if isinstance(orders, list) else (orders,):
            yield dict(order)
        return
    if source == "my_trades":
        watch = getattr(exchange, "watch_my_trades", None)
        if not callable(watch):
            raise _WsUnavailable("watch_my_trades is not supported by this ccxt exchange")
        await _ensure_markets_loaded(exchange)
        since = _optional_millis(params.get("since"))
        limit = params.get("limit")
        trades = await watch(symbol, since=since, limit=None if limit is None else int(limit), params=_exchange_params(params))
        for trade in trades if isinstance(trades, list) else (trades,):
            yield dict(trade)
        return
    raise KeyError(f"ccxt account live source is not supported: {source}")


class _WsUnavailable(Exception):
    pass


def _ignore_cancelled_loop_exception(previous_handler: object) -> Callable[[asyncio.AbstractEventLoop, dict[str, Any]], None]:
    def handle(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        if isinstance(context.get("exception"), asyncio.CancelledError):
            return
        if callable(previous_handler):
            previous_handler(loop, context)
            return
        loop.default_exception_handler(context)

    return handle


def _symbol_text(symbol: object) -> str:
    value = str(symbol).strip()
    if not value:
        raise ValueError("ccxt symbol cannot be empty")
    return value


def _optional_symbol_text(symbol: object | None) -> str | None:
    return None if symbol is None else _symbol_text(symbol)


async def _watch_ticker(exchange: Any, symbol: str, params: Mapping[str, object]) -> Mapping[str, Any]:
    watch = getattr(exchange, "watch_ticker", None)
    if not callable(watch):
        raise _WsUnavailable()
    try:
        await _ensure_markets_loaded(exchange)
        return await watch(symbol, _exchange_params(params))
    except Exception as error:
        if _is_not_supported(error):
            raise _WsUnavailable() from error
        raise


async def _watch_order_book(
    exchange: Any,
    symbol: str,
    limit: int | None,
    params: Mapping[str, object],
) -> Mapping[str, Any]:
    watch = getattr(exchange, "watch_order_book", None)
    if not callable(watch):
        raise _WsUnavailable()
    try:
        await _ensure_markets_loaded(exchange)
        return await watch(symbol, limit=limit, params=_exchange_params(params))
    except Exception as error:
        if _is_not_supported(error):
            raise _WsUnavailable() from error
        raise


async def _watch_trades(
    exchange: Any,
    symbol: str,
    since: int | None,
    limit: int,
    params: Mapping[str, object],
) -> Iterable[Mapping[str, Any]]:
    watch = getattr(exchange, "watch_trades", None)
    if not callable(watch):
        raise _WsUnavailable()
    try:
        await _ensure_markets_loaded(exchange)
        return await watch(symbol, since=since, limit=limit, params=_exchange_params(params))
    except Exception as error:
        if _is_not_supported(error):
            raise _WsUnavailable() from error
        raise


async def _watch_option_greeks(exchange: Any, symbol: str, params: Mapping[str, object]) -> Mapping[str, Any]:
    watch = getattr(exchange, "watch_greeks", None) or getattr(exchange, "watchGreeks", None)
    if not callable(watch):
        raise _WsUnavailable()
    try:
        await _ensure_markets_loaded(exchange)
        result = await watch(symbol, params=_exchange_params(params))
        return _single_symbol_row(result, symbol)
    except Exception as error:
        if _is_not_supported(error):
            raise _WsUnavailable() from error
        raise


async def _fetch_option_greeks(exchange: Any, symbol: str, params: Mapping[str, object]) -> Mapping[str, Any]:
    fetch = getattr(exchange, "fetch_greeks", None) or getattr(exchange, "fetchGreeks", None)
    if not callable(fetch):
        raise _WsUnavailable("option greeks are not supported by this ccxt exchange")
    await _ensure_markets_loaded(exchange)
    result = await fetch(symbol, params=_exchange_params(params))
    return _single_symbol_row(result, symbol)


def _single_symbol_row(result: object, symbol: str) -> Mapping[str, Any]:
    if isinstance(result, Mapping):
        if symbol in result and isinstance(result[symbol], Mapping):
            return dict(result[symbol])
        if "symbol" in result or "delta" in result or "gamma" in result or "markIV" in result:
            return dict(result)
        for value in result.values():
            if isinstance(value, Mapping):
                return dict(value)
    if isinstance(result, (list, tuple)):
        for value in result:
            if isinstance(value, Mapping) and str(value.get("symbol") or "").strip() in {"", symbol}:
                return dict(value)
        for value in result:
            if isinstance(value, Mapping):
                return dict(value)
    raise TypeError(f"ccxt greeks response has no row for {symbol}: {result!r}")


async def _ensure_markets_loaded(exchange: Any) -> None:
    if getattr(exchange, "markets", object()) is not None:
        return
    load_markets = getattr(exchange, "load_markets", None)
    if not callable(load_markets):
        return
    result = load_markets()
    if hasattr(result, "__await__"):
        await result


def _default_exchange(exchange_id: str) -> Any:
    try:
        import ccxt
    except ImportError as error:
        raise RuntimeError("ccxt driver requires ccxt") from error
    try:
        exchange_type = getattr(ccxt, _normalized_exchange_id(exchange_id))
    except AttributeError as error:
        raise ValueError(f"unsupported ccxt exchange id: {exchange_id}") from error
    return exchange_type({"enableRateLimit": True})


def _default_async_exchange(exchange_id: str) -> Any:
    try:
        import ccxt.pro as ccxt_pro
    except ImportError:
        ccxt_pro = None
    if ccxt_pro is not None:
        try:
            exchange_type = getattr(ccxt_pro, _normalized_exchange_id(exchange_id))
        except AttributeError:
            pass
        else:
            return exchange_type({"enableRateLimit": True})
    try:
        import ccxt.async_support as ccxt_async
    except ImportError as error:
        raise RuntimeError("ccxt live driver requires ccxt.pro or ccxt async_support") from error
    try:
        exchange_type = getattr(ccxt_async, _normalized_exchange_id(exchange_id))
    except AttributeError as error:
        raise ValueError(f"unsupported ccxt exchange id: {exchange_id}") from error
    return exchange_type({"enableRateLimit": True})


def _normalized_exchange_id(value: str) -> str:
    return {"okex": "okx"}.get(value.strip().lower(), value.strip().lower())


def _market_type(exchange_id: str, params: Mapping[str, object]) -> str:
    if params.get("market") is not None:
        return str(params["market"])
    if params.get("type") is not None:
        return str(params["type"])
    if exchange_id == "hyperliquid":
        return "derivative"
    return "spot"


def _configure_exchange_market(exchange: Any, exchange_id: str, params: Mapping[str, object]) -> None:
    market_type = _ccxt_market_type(exchange_id, _market_type(exchange_id, params))
    options = getattr(exchange, "options", None)
    if isinstance(options, dict) and market_type is not None:
        options.setdefault("defaultType", market_type)
        options["defaultType"] = market_type


def _ccxt_market_type(exchange_id: str, market_type: str) -> str | None:
    normalized = market_type.strip().lower()
    if exchange_id == "binance" and normalized in {"swap", "perp", "perpetual", "future", "futures"}:
        return "future"
    if normalized in {"swap", "perp", "perpetual"}:
        return "swap"
    if normalized == "futures":
        return "future"
    return normalized or None


def _market_record(exchange_id: str, default_market: str, market: object) -> Mapping[str, object]:
    row = dict(market) if isinstance(market, Mapping) else {"symbol": str(market)}
    symbol = str(row.get("symbol") or row.get("id") or "").strip()
    if not symbol:
        raise ValueError(f"ccxt market row is missing symbol: {row!r}")
    market_type = str(row.get("type") or _market_from_flags(row) or default_market)
    precision = row.get("precision") if isinstance(row.get("precision"), Mapping) else {}
    limits = row.get("limits") if isinstance(row.get("limits"), Mapping) else {}
    amount_limits = limits.get("amount") if isinstance(limits.get("amount"), Mapping) else {}
    cost_limits = limits.get("cost") if isinstance(limits.get("cost"), Mapping) else {}
    return {
        "venue": exchange_id,
        "market": market_type,
        "source_symbol": symbol,
        "venue_instrument_id": row.get("id"),
        "base": row.get("base"),
        "quote": row.get("quote"),
        "active": row.get("active"),
        "status": row.get("status"),
        "price_precision": precision.get("price") if isinstance(precision, Mapping) else None,
        "amount_precision": precision.get("amount") if isinstance(precision, Mapping) else None,
        "min_amount": amount_limits.get("min") if isinstance(amount_limits, Mapping) else None,
        "min_notional": cost_limits.get("min") if isinstance(cost_limits, Mapping) else None,
        "contract_size": row.get("contractSize"),
        "raw": row,
    }


def _market_from_flags(row: Mapping[str, object]) -> str | None:
    if row.get("spot") is True:
        return "spot"
    if row.get("swap") is True:
        return "perpetual"
    if row.get("future") is True:
        return "future"
    if row.get("option") is True:
        return "option"
    return None


def _optional_millis(value: object | None) -> int | None:
    return None if value is None else _millis(value)


def _millis(value: object) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
    else:
        raise ValueError(f"time must be datetime, ISO-8601 text, or milliseconds: {value!r}")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"time must be timezone-aware: {value!r}")
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def _funding_timestamp(row: Mapping[str, object]) -> int | None:
    value = row.get("timestamp") or row.get("fundingTimestamp")
    if value is not None:
        return int(value)
    info = row.get("info")
    if isinstance(info, Mapping):
        for key in ("fundingTime", "fundingTimestamp", "time"):
            if info.get(key) is not None:
                return int(info[key])
    return None


def _exchange_params(params: Mapping[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in params.items()
        if key
        not in {
            "market",
            "type",
            "poll_seconds",
            "max_events",
            "since",
            "limit",
            "require_ws",
            "derivation",
            "orderbook_speed",
            "orderbook_url",
            "local_orderbook_limit",
            "orderbook_depth",
            "snapshot_timeout_seconds",
        }
    }


def _is_not_supported(error: Exception) -> bool:
    return error.__class__.__name__ in {"NotSupported", "NotImplementedError"}


async def _watch_binance_depth_diffs(symbol: str, params: Mapping[str, object]) -> AsyncIterator[Mapping[str, object]]:
    try:
        import websockets
    except ImportError as error:
        raise RuntimeError("Binance local L2 orderbook requires websockets") from error
    speed = str(params.get("orderbook_speed") or "100ms").strip()
    stream_name = f"{_binance_stream_symbol(symbol)}@depth" + ("" if speed in {"", "1000ms"} else f"@{speed}")
    url = str(params.get("orderbook_url") or _binance_depth_url(params, stream_name))
    try:
        connection = websockets.connect(url, proxy=None)
    except TypeError:
        connection = websockets.connect(url)
    async with connection as websocket:
        async for message in websocket:
            raw = json.loads(message)
            if isinstance(raw, Mapping) and isinstance(raw.get("data"), Mapping):
                raw = raw["data"]
            if isinstance(raw, Mapping) and raw.get("e") == "depthUpdate":
                yield dict(raw)


def _binance_depth_url(params: Mapping[str, object], stream_name: str) -> str:
    market_type = _market_type("binance", params).lower()
    if market_type in {"swap", "future", "futures", "perp", "perpetual"}:
        return f"wss://fstream.binance.com/ws/{stream_name}"
    return f"wss://stream.binance.com:9443/ws/{stream_name}"


def _binance_stream_symbol(symbol: str) -> str:
    value = symbol.split(":", 1)[0]
    return "".join(part for part in value if part.isalnum()).lower()
