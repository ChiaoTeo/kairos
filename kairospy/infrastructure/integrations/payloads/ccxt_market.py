from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from kairospy.core.market import Bar, MarketEvent, MarketSubject, OrderBookSnapshot, PriceLevel, Quote, RateObservation, TradePrint
from kairospy.application.service.domain.market.records import (
    BarRecord,
    OrderBookRecord,
    QuoteRecord,
    RateRecord,
    TradeRecord,
    bar_record,
    event_time,
    funding_rate_record,
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
    ticker = _normalized_ticker(raw)
    return Quote(
        instrument_id=market.instrument_id,
        market_id=market.market_id,
        market_key=market.market_key,
        time=_market_time(ticker, fallback_to_now=True),
        bid=_optional_decimal(ticker.get("bid")),
        ask=_optional_decimal(ticker.get("ask")),
        source=market.venue,
    )


def ccxt_order_book_snapshot(raw: Mapping[str, object], *, market: MarketRef) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        instrument_id=market.instrument_id,
        market_id=market.market_id,
        market_key=market.market_key,
        time=_market_time(raw),
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
        time=_market_time(raw),
        trade_id=None if raw.get("id") is None else str(raw.get("id")),
        side=None if raw.get("side") is None else str(raw.get("side")),
        price=_optional_decimal(raw.get("price")),
        size=_optional_decimal(raw.get("amount")),
        cost=_optional_decimal(raw.get("cost")),
        source=market.venue,
    )


def ccxt_funding_rate_observation(raw: Mapping[str, object], *, market: MarketRef) -> RateObservation:
    row = _normalized_funding_rate(raw)
    return RateObservation(
        rate_id=str(market.market_id),
        time=_market_time(row),
        rate=_decimal(row["rate"]),
        source=market.venue,
        tenor=None if row.get("tenor") is None else str(row["tenor"]),
        basis="funding_rate",
        market_id=market.market_id,
        instrument_id=market.instrument_id,
        mark_price=_optional_decimal(row.get("markPrice")),
    )


def ccxt_ohlcv_record(values: list[Any] | tuple[Any, ...], *, market: MarketRef, timeframe: str) -> BarRecord:
    return bar_record(
        ccxt_ohlcv_bar(values, market=market, timeframe=timeframe),
        venue=market.venue,
        market=market.market,
        source_symbol=market.source_symbol,
    )


def ccxt_ohlcv_update(values: list[Any] | tuple[Any, ...], *, market: MarketRef, timeframe: str) -> MarketEvent:
    bar = ccxt_ohlcv_bar(values, market=market, timeframe=timeframe)
    return MarketEvent(
        MarketSubject("instrument", bar.instrument_id),
        bar.time,
        bar,
        source=market.venue,
        available_at=bar.time,
        metadata=_market_metadata(market, raw=tuple(values)),
    )


def ccxt_ticker_record(raw: Mapping[str, object], *, market: MarketRef) -> QuoteRecord:
    return ticker_record(venue=market.venue, market=market.market, instrument=market, ticker=_normalized_ticker(raw))


def ccxt_ticker_update(raw: Mapping[str, object], *, market: MarketRef) -> MarketEvent:
    quote = ccxt_ticker_quote(raw, market=market)
    return MarketEvent(
        MarketSubject("instrument", quote.instrument_id),
        quote.time,
        quote,
        source=market.venue,
        available_at=quote.time,
        metadata=_market_metadata(market, raw=dict(raw), last=_optional_decimal(raw.get("last"))),
    )


def ccxt_order_book_record(raw: Mapping[str, object], *, market: MarketRef) -> OrderBookRecord:
    return order_book_record(
        ccxt_order_book_snapshot(raw, market=market),
        venue=market.venue,
        market=market.market,
        source_symbol=market.source_symbol,
    )


def ccxt_order_book_update(raw: Mapping[str, object], *, market: MarketRef) -> MarketEvent:
    book = ccxt_order_book_snapshot(raw, market=market)
    return MarketEvent(
        MarketSubject("instrument", book.instrument_id),
        book.time,
        book,
        source=market.venue,
        available_at=book.time,
        metadata=_market_metadata(
            market,
            raw=dict(raw),
            bids=tuple((level.price, level.size) for level in book.bids),
            asks=tuple((level.price, level.size) for level in book.asks),
            nonce=book.nonce,
        ),
    )


def ccxt_trade_record(raw: Mapping[str, object], *, market: MarketRef) -> TradeRecord:
    return trade_print_record(
        ccxt_trade_print(raw, market=market),
        venue=market.venue,
        market=market.market,
        source_symbol=market.source_symbol,
    )


def ccxt_trade_update(raw: Mapping[str, object], *, market: MarketRef) -> MarketEvent:
    trade = ccxt_trade_print(raw, market=market)
    return MarketEvent(
        MarketSubject("instrument", trade.instrument_id),
        trade.time,
        trade,
        source=market.venue,
        available_at=trade.time,
        metadata=_market_metadata(market, raw=dict(raw), id=trade.trade_id),
    )


def ccxt_funding_rate_record(raw: Mapping[str, object], *, market: MarketRef) -> RateRecord:
    return funding_rate_record(
        ccxt_funding_rate_observation(raw, market=market),
        venue=market.venue,
        market=market.market,
        source_symbol=market.source_symbol,
    )


def ccxt_funding_rate_update(raw: Mapping[str, object], *, market: MarketRef) -> MarketEvent:
    rate = ccxt_funding_rate_observation(raw, market=market)
    return MarketEvent(
        MarketSubject("market", market.market_id),
        rate.time,
        rate,
        source=market.venue,
        available_at=rate.time,
        metadata=_market_metadata(market, raw=dict(raw), mark_price=rate.mark_price),
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


def _market_time(raw: Mapping[str, object], *, fallback_to_now: bool = False) -> datetime:
    value = raw.get("timestamp")
    if value is not None:
        return event_time(value)
    value = raw.get("datetime") or raw.get("time") or raw.get("transactTime")
    if isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    if fallback_to_now:
        return datetime.now(timezone.utc)
    return event_time(None)


def _normalized_ticker(raw: Mapping[str, object]) -> Mapping[str, object]:
    value = dict(raw)
    price = _first_not_none(value.get("last"), value.get("close"), value.get("markPrice"), value.get("indexPrice"), _info_price(value))
    if value.get("bid") is None and price is not None:
        value["bid"] = price
    if value.get("ask") is None and price is not None:
        value["ask"] = price
    if value.get("timestamp") is None:
        value["timestamp"] = int(_market_time(value, fallback_to_now=True).timestamp() * 1000)
    return value


def _normalized_funding_rate(raw: Mapping[str, object]) -> Mapping[str, object]:
    value = dict(raw)
    info = value.get("info") if isinstance(value.get("info"), Mapping) else {}
    rate = _first_not_none(value.get("fundingRate"), value.get("rate"), info.get("fundingRate"))
    timestamp = _first_not_none(value.get("timestamp"), value.get("fundingTimestamp"), info.get("fundingTime"), info.get("time"))
    mark_price = _first_not_none(value.get("markPrice"), value.get("mark_price"), info.get("markPrice"))
    if rate is None:
        raise ValueError(f"funding rate row is missing rate: {raw!r}")
    if timestamp is None:
        raise ValueError(f"funding rate row is missing timestamp: {raw!r}")
    value["rate"] = rate
    value["timestamp"] = timestamp
    if mark_price is not None:
        value["markPrice"] = mark_price
    value.setdefault("tenor", "8h")
    return value


def _info_price(raw: Mapping[str, object]) -> object | None:
    info = raw.get("info")
    return info.get("price") if isinstance(info, Mapping) else None


def _first_not_none(*values: object) -> object | None:
    for value in values:
        if value is not None:
            return value
    return None


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
    "ccxt_funding_rate_observation",
    "ccxt_funding_rate_record",
    "ccxt_funding_rate_update",
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
