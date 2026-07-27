from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from kairospy.core.market import Bar, OrderBookSnapshot, PriceLevel, Quote, TradePrint
from kairospy.core.market.records import bar_record, event_time, order_book_record, ticker_record, trade_print_record
from kairospy.core.reference import MarketRef


def ccxt_ohlcv_bar(values: list[Any] | tuple[Any, ...], *, market: MarketRef, timeframe: str) -> Bar:
    return Bar(
        instrument_id=market.instrument_id,
        market_id=market.market_id,
        market_key=market.market_key,
        time=event_time(values[0]),
        timeframe=timeframe,
        open=_decimal(values[1]),
        high=_decimal(values[2]),
        low=_decimal(values[3]),
        close=_decimal(values[4]),
        volume=_decimal(values[5]),
        source=market.venue,
    )


def ccxt_ticker_quote(raw: Mapping[str, object], *, market: MarketRef) -> Quote:
    return Quote(
        instrument_id=market.instrument_id,
        market_id=market.market_id,
        market_key=market.market_key,
        time=event_time(raw.get("timestamp")),
        bid=_optional_decimal(raw.get("bid")),
        ask=_optional_decimal(raw.get("ask")),
        source=market.venue,
    )


def ccxt_order_book_snapshot(raw: Mapping[str, object], *, market: MarketRef) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        instrument_id=market.instrument_id,
        market_id=market.market_id,
        market_key=market.market_key,
        time=event_time(raw.get("timestamp")),
        bids=_levels(raw.get("bids")),
        asks=_levels(raw.get("asks")),
        nonce=raw.get("nonce"),
        source=market.venue,
    )


def ccxt_trade_print(raw: Mapping[str, object], *, market: MarketRef) -> TradePrint:
    return TradePrint(
        instrument_id=market.instrument_id,
        market_id=market.market_id,
        market_key=market.market_key,
        time=event_time(raw.get("timestamp")),
        trade_id=None if raw.get("id") is None else str(raw.get("id")),
        side=None if raw.get("side") is None else str(raw.get("side")),
        price=_optional_decimal(raw.get("price")),
        size=_optional_decimal(raw.get("amount")),
        cost=_optional_decimal(raw.get("cost")),
        source=market.venue,
    )


def ccxt_ohlcv_record(values: list[Any] | tuple[Any, ...], *, market: MarketRef, timeframe: str) -> dict[str, object]:
    return bar_record(
        ccxt_ohlcv_bar(values, market=market, timeframe=timeframe),
        venue=market.venue,
        market=market.market,
        source_symbol=market.source_symbol,
    )


def ccxt_ticker_record(raw: Mapping[str, object], *, market: MarketRef) -> dict[str, object]:
    return ticker_record(venue=market.venue, market=market.market, instrument=market, ticker=raw)


def ccxt_order_book_record(raw: Mapping[str, object], *, market: MarketRef) -> dict[str, object]:
    return order_book_record(
        ccxt_order_book_snapshot(raw, market=market),
        venue=market.venue,
        market=market.market,
        source_symbol=market.source_symbol,
    )


def ccxt_trade_record(raw: Mapping[str, object], *, market: MarketRef) -> dict[str, object]:
    return trade_print_record(
        ccxt_trade_print(raw, market=market),
        venue=market.venue,
        market=market.market,
        source_symbol=market.source_symbol,
    )


def ephemeral_market_ref(*, venue: str, market: str, source_symbol: str) -> MarketRef:
    return MarketRef.ephemeral(venue=venue, market=market, source_symbol=source_symbol)


def ccxt_market_type(exchange_id: str, params: Mapping[str, object] | None = None) -> str:
    options = params or {}
    if options.get("market") is not None:
        return str(options["market"])
    if options.get("type") is not None:
        return str(options["type"])
    if exchange_id == "hyperliquid":
        return "derivative"
    return "spot"


def _levels(value: object) -> tuple[PriceLevel, ...]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return ()
    levels: list[PriceLevel] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        levels.append(PriceLevel(_decimal(item[0]), _decimal(item[1])))
    return tuple(levels)


def _optional_decimal(value: object | None) -> Decimal | None:
    return None if value is None else _decimal(value)


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


__all__ = [
    "ccxt_ohlcv_bar",
    "ccxt_ohlcv_record",
    "ccxt_order_book_record",
    "ccxt_order_book_snapshot",
    "ccxt_market_type",
    "ccxt_ticker_quote",
    "ccxt_ticker_record",
    "ccxt_trade_print",
    "ccxt_trade_record",
    "ephemeral_market_ref",
]
