from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from kairospy.core.market import (
    FIELD_BAR_CLOSE,
    FIELD_BAR_HIGH,
    FIELD_BAR_LOW,
    FIELD_BAR_OPEN,
    FIELD_BAR_VOLUME,
    FIELD_BOOK_ASK1,
    FIELD_BOOK_ASK_DEPTH,
    FIELD_BOOK_BID1,
    FIELD_BOOK_BID_DEPTH,
    FIELD_QUOTE_ASK,
    FIELD_QUOTE_ASK_SIZE,
    FIELD_QUOTE_BID,
    FIELD_QUOTE_BID_SIZE,
    FIELD_TRADE_COST,
    FIELD_TRADE_PRICE,
    FIELD_TRADE_SIDE,
    FIELD_TRADE_SIZE,
    Bar,
    MarketUpdate,
    OrderBookSnapshot,
    PriceLevel,
    Quote,
    TradePrint,
)
from kairospy.service.domains.market.records import (
    bar_record,
    event_time,
    order_book_record,
    ticker_record,
    trade_print_record,
)
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


def ccxt_ohlcv_update(values: list[Any] | tuple[Any, ...], *, market: MarketRef, timeframe: str) -> MarketUpdate:
    bar = ccxt_ohlcv_bar(values, market=market, timeframe=timeframe)
    return MarketUpdate(
        "instrument",
        bar.instrument_id,
        bar.time,
        _clean_fields({
            FIELD_BAR_OPEN: bar.open,
            FIELD_BAR_HIGH: bar.high,
            FIELD_BAR_LOW: bar.low,
            FIELD_BAR_CLOSE: bar.close,
            FIELD_BAR_VOLUME: bar.volume,
        }),
        source=market.venue,
        kind="ohlcv",
        available_at=bar.time,
        market_id=bar.market_id,
        market_key=bar.market_key,
        interval=timeframe,
        metadata=_market_metadata(market, raw=tuple(values)),
    )


def ccxt_ticker_record(raw: Mapping[str, object], *, market: MarketRef) -> dict[str, object]:
    return ticker_record(venue=market.venue, market=market.market, instrument=market, ticker=raw)


def ccxt_ticker_update(raw: Mapping[str, object], *, market: MarketRef) -> MarketUpdate:
    quote = ccxt_ticker_quote(raw, market=market)
    return MarketUpdate(
        "instrument",
        quote.instrument_id,
        quote.time,
        _clean_fields({
            FIELD_QUOTE_BID: quote.bid,
            FIELD_QUOTE_ASK: quote.ask,
        }),
        source=market.venue,
        kind="ticker",
        available_at=quote.time,
        market_id=quote.market_id,
        market_key=quote.market_key,
        metadata=_market_metadata(market, raw=dict(raw), last=_optional_decimal(raw.get("last"))),
    )


def ccxt_order_book_record(raw: Mapping[str, object], *, market: MarketRef) -> dict[str, object]:
    return order_book_record(
        ccxt_order_book_snapshot(raw, market=market),
        venue=market.venue,
        market=market.market,
        source_symbol=market.source_symbol,
    )


def ccxt_order_book_update(raw: Mapping[str, object], *, market: MarketRef) -> MarketUpdate:
    book = ccxt_order_book_snapshot(raw, market=market)
    bid1 = book.bid1
    ask1 = book.ask1
    return MarketUpdate(
        "instrument",
        book.instrument_id,
        book.time,
        _clean_fields({
            FIELD_BOOK_BID1: None if bid1 is None else bid1.price,
            FIELD_BOOK_ASK1: None if ask1 is None else ask1.price,
            FIELD_QUOTE_BID_SIZE: None if bid1 is None else bid1.size,
            FIELD_QUOTE_ASK_SIZE: None if ask1 is None else ask1.size,
            FIELD_BOOK_BID_DEPTH: len(book.bids),
            FIELD_BOOK_ASK_DEPTH: len(book.asks),
        }),
        source=market.venue,
        kind="orderbook",
        available_at=book.time,
        market_id=book.market_id,
        market_key=book.market_key,
        metadata=_market_metadata(
            market,
            raw=dict(raw),
            bids=tuple((level.price, level.size) for level in book.bids),
            asks=tuple((level.price, level.size) for level in book.asks),
            nonce=book.nonce,
        ),
    )


def ccxt_trade_record(raw: Mapping[str, object], *, market: MarketRef) -> dict[str, object]:
    return trade_print_record(
        ccxt_trade_print(raw, market=market),
        venue=market.venue,
        market=market.market,
        source_symbol=market.source_symbol,
    )


def ccxt_trade_update(raw: Mapping[str, object], *, market: MarketRef) -> MarketUpdate:
    trade = ccxt_trade_print(raw, market=market)
    return MarketUpdate(
        "instrument",
        trade.instrument_id,
        trade.time,
        _clean_fields({
            FIELD_TRADE_PRICE: trade.price,
            FIELD_TRADE_SIZE: trade.size,
            FIELD_TRADE_SIDE: trade.side,
            FIELD_TRADE_COST: trade.cost,
        }),
        source=market.venue,
        kind="trade",
        available_at=trade.time,
        market_id=trade.market_id,
        market_key=trade.market_key,
        metadata=_market_metadata(market, raw=dict(raw), id=trade.trade_id),
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


def _clean_fields(values: Mapping[str, object | None]) -> dict[str, object]:
    return {key: value for key, value in values.items() if value is not None}


def _market_metadata(market: MarketRef, **values: object) -> dict[str, object]:
    metadata = {
        "venue": market.venue,
        "market": market.market,
        "source_symbol": market.source_symbol,
    }
    metadata.update({key: value for key, value in values.items() if value is not None})
    return metadata


__all__ = [
    "ccxt_ohlcv_bar",
    "ccxt_ohlcv_record",
    "ccxt_ohlcv_update",
    "ccxt_order_book_record",
    "ccxt_order_book_snapshot",
    "ccxt_order_book_update",
    "ccxt_market_type",
    "ccxt_ticker_quote",
    "ccxt_ticker_record",
    "ccxt_ticker_update",
    "ccxt_trade_print",
    "ccxt_trade_record",
    "ccxt_trade_update",
    "ephemeral_market_ref",
]
