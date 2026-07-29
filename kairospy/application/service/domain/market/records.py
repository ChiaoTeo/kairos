from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, TypeAlias, TypedDict

from kairospy.core.market import Bar, OrderBookSnapshot, PriceLevel, Quote, RateObservation, TradePrint
from kairospy.core.reference import MarketRef


MarketRecordValue: TypeAlias = str | int | float | Decimal | datetime | list[str] | list[list[str]] | None


class MarketRecord(TypedDict, total=False):
    time: str
    kind: str
    market_id: str | None
    instrument_id: str
    market_key: str | None
    venue: str
    market: str
    source_symbol: str


class QuoteRecord(MarketRecord, total=False):
    bid1: str | None
    ask1: str | None
    last: str | None
    base_volume: str | None
    quote_volume: str | None


class OrderBookRecord(MarketRecord, total=False):
    bid1: str | None
    bid1_size: str | None
    ask1: str | None
    ask1_size: str | None
    bid_depth: int
    ask_depth: int
    bids: list[list[str]]
    asks: list[list[str]]
    nonce: MarketRecordValue


class TradeRecord(MarketRecord, total=False):
    id: str | None
    side: str | None
    price: str | None
    size: str | None
    amount: str | None
    cost: str | None


class BarRecord(MarketRecord, total=False):
    timeframe: str
    open: str | None
    high: str | None
    low: str | None
    close: str | None
    volume: str | None


class RateRecord(MarketRecord, total=False):
    rate_id: str
    rate: str
    tenor: str | None
    basis: str
    mark_price: str | None


def ohlcv_record(
    *,
    venue: str,
    instrument: object,
    timeframe: str,
    values: list[Any] | tuple[Any, ...],
    market: str = "spot",
) -> BarRecord:
    market_ref = _market_ref(venue=venue, market=market, instrument=instrument)
    return bar_record(
        Bar(
            instrument_id=market_ref.instrument_id,
            market_id=market_ref.market_id,
            market_key=market_ref.market_key,
            time=event_time(values[0]),
            timeframe=timeframe,
            open=_decimal(values[1]),
            high=_decimal(values[2]),
            low=_decimal(values[3]),
            close=_decimal(values[4]),
            volume=_decimal(values[5]),
            source=venue,
        ),
        venue=market_ref.venue,
        market=market_ref.market,
        source_symbol=market_ref.source_symbol,
    )


def ticker_record(*, venue: str, instrument: object, ticker: Mapping[str, Any], market: str = "spot") -> QuoteRecord:
    market_ref = _market_ref(venue=venue, market=market, instrument=instrument)
    quote = Quote(
        instrument_id=market_ref.instrument_id,
        market_id=market_ref.market_id,
        market_key=market_ref.market_key,
        time=event_time(ticker.get("timestamp")),
        bid=_optional_decimal(ticker.get("bid")),
        ask=_optional_decimal(ticker.get("ask")),
        source=venue,
    )
    return {
        "time": quote.time.isoformat(),
        "kind": "ticker",
        **market_ref.identity_fields(),
        "bid1": _optional_decimal_text(quote.bid),
        "ask1": _optional_decimal_text(quote.ask),
        "last": _optional_decimal_text(ticker.get("last")),
        "base_volume": _optional_decimal_text(ticker.get("baseVolume")),
        "quote_volume": _optional_decimal_text(ticker.get("quoteVolume")),
    }


def orderbook_record(*, venue: str, instrument: object, book: Mapping[str, Any], market: str = "spot") -> OrderBookRecord:
    market_ref = _market_ref(venue=venue, market=market, instrument=instrument)
    bids = [level for value in book.get("bids", ()) if (level := _level(value)) is not None]
    asks = [level for value in book.get("asks", ()) if (level := _level(value)) is not None]
    if not bids:
        bid1 = _optional_decimal_from_keys(book, "bid1", "bid", "bid_price")
        bid1_size = _optional_decimal_from_keys(book, "bid1_size", "bid_size")
        if bid1 is not None and bid1_size is not None:
            bids = [PriceLevel(bid1, bid1_size)]
    if not asks:
        ask1 = _optional_decimal_from_keys(book, "ask1", "ask", "ask_price")
        ask1_size = _optional_decimal_from_keys(book, "ask1_size", "ask_size")
        if ask1 is not None and ask1_size is not None:
            asks = [PriceLevel(ask1, ask1_size)]
    return order_book_record(
        OrderBookSnapshot(
            instrument_id=market_ref.instrument_id,
            market_id=market_ref.market_id,
            market_key=market_ref.market_key,
            time=event_time(book.get("timestamp")),
            bids=tuple(bids),
            asks=tuple(asks),
            nonce=book.get("nonce"),
            source=venue,
        ),
        venue=market_ref.venue,
        market=market_ref.market,
        source_symbol=market_ref.source_symbol,
        preserve_flat_only=not bool(book.get("bids")) and not bool(book.get("asks")),
    )


def order_book_record(
    book: OrderBookSnapshot,
    *,
    venue: str,
    market: str,
    source_symbol: str,
    preserve_flat_only: bool = False,
) -> OrderBookRecord:
    bid1 = book.bid1
    ask1 = book.ask1
    return {
        "time": book.time.isoformat(),
        "kind": "orderbook",
        **_identity_fields(book.market_id, book.instrument_id, book.market_key, venue, market, source_symbol),
        "bid1": _optional_decimal_text(None if bid1 is None else bid1.price),
        "bid1_size": _optional_decimal_text(None if bid1 is None else bid1.size),
        "ask1": _optional_decimal_text(None if ask1 is None else ask1.price),
        "ask1_size": _optional_decimal_text(None if ask1 is None else ask1.size),
        "bid_depth": len(book.bids),
        "ask_depth": len(book.asks),
        "bids": [] if preserve_flat_only else [_level_record(level) for level in book.bids],
        "asks": [] if preserve_flat_only else [_level_record(level) for level in book.asks],
        "nonce": book.nonce,
    }


def trade_record(*, venue: str, instrument: object, trade: Mapping[str, Any], market: str = "spot") -> TradeRecord:
    market_ref = _market_ref(venue=venue, market=market, instrument=instrument)
    return trade_print_record(
        TradePrint(
            instrument_id=market_ref.instrument_id,
            market_id=market_ref.market_id,
            market_key=market_ref.market_key,
            time=event_time(trade.get("timestamp")),
            trade_id=None if trade.get("id") is None else str(trade.get("id")),
            side=None if trade.get("side") is None else str(trade.get("side")),
            price=_optional_decimal(trade.get("price")),
            size=_optional_decimal(trade.get("amount")),
            cost=_optional_decimal(trade.get("cost")),
            source=venue,
        ),
        venue=market_ref.venue,
        market=market_ref.market,
        source_symbol=market_ref.source_symbol,
    )


def trade_print_record(trade: TradePrint, *, venue: str, market: str, source_symbol: str) -> TradeRecord:
    size = _optional_decimal_text(trade.size)
    return {
        "time": trade.time.isoformat(),
        "kind": "trade",
        **_identity_fields(trade.market_id, trade.instrument_id, trade.market_key, venue, market, source_symbol),
        "id": trade.trade_id,
        "side": trade.side,
        "price": _optional_decimal_text(trade.price),
        "size": size,
        "amount": size,
        "cost": _optional_decimal_text(trade.cost),
    }


def bar_record(bar: Bar, *, venue: str, market: str, source_symbol: str) -> BarRecord:
    return {
        "time": bar.time.isoformat(),
        "kind": "ohlcv",
        **_identity_fields(bar.market_id, bar.instrument_id, bar.market_key, venue, market, source_symbol),
        "timeframe": bar.timeframe,
        "open": _optional_decimal_text(bar.open),
        "high": _optional_decimal_text(bar.high),
        "low": _optional_decimal_text(bar.low),
        "close": _optional_decimal_text(bar.close),
        "volume": _optional_decimal_text(bar.volume),
    }


def funding_rate_record(rate: RateObservation, *, venue: str, market: str, source_symbol: str) -> RateRecord:
    return {
        "time": rate.time.isoformat(),
        "kind": "funding_rate",
        **_identity_fields(
            None if rate.market_id is None else str(rate.market_id),
            _instrument_id(venue, market, source_symbol) if rate.instrument_id is None else str(rate.instrument_id),
            None,
            venue,
            market,
            source_symbol,
        ),
        "rate_id": rate.rate_id,
        "rate": _decimal_text(rate.rate),
        "tenor": rate.tenor,
        "basis": rate.basis or "funding_rate",
        "mark_price": _optional_decimal_text(rate.mark_price),
    }


def event_time(value: object) -> datetime:
    if value is None:
        raise ValueError("market record timestamp is required")
    return datetime.fromtimestamp(int(value) / 1000, timezone.utc)


def iso_time(value: object) -> str:
    return event_time(value).isoformat()


def _market_ref(*, venue: str, market: str, instrument: object) -> MarketRef:
    if isinstance(instrument, MarketRef):
        return instrument
    if hasattr(instrument, "identity_fields"):
        fields = instrument.identity_fields()
        return MarketRef(
            market_id=str(fields["market_id"]),
            instrument_id=str(fields["instrument_id"]),
            market_key=str(fields["market_key"]),
            venue=str(fields["venue"]),
            market=str(fields["market"]),
            source_symbol=str(fields["source_symbol"]),
        )
    return MarketRef.ephemeral(venue=venue, market=market, source_symbol=str(instrument))


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _decimal_text(value: object) -> str:
    return str(_decimal(value))


def _optional_decimal(value: object | None) -> Decimal | None:
    return None if value is None else _decimal(value)


def _optional_decimal_text(value: object) -> str | None:
    return None if value is None else _decimal_text(value)


def _optional_decimal_from_keys(row: Mapping[str, Any], *keys: str) -> Decimal | None:
    for key in keys:
        if row.get(key) is not None:
            return _decimal(row[key])
    return None


def _level(value: object) -> PriceLevel | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    return PriceLevel(_decimal(value[0]), _decimal(value[1]))


def _level_record(level: PriceLevel) -> list[str]:
    return [_decimal_text(level.price), _decimal_text(level.size)]


def _identity_fields(
    market_id: str | None,
    instrument_id: str,
    market_key: str | None,
    venue: str,
    market: str,
    source_symbol: str,
) -> dict[str, object]:
    return {
        "market_id": market_id,
        "instrument_id": instrument_id,
        "market_key": market_key,
        "venue": venue,
        "market": market,
        "source_symbol": source_symbol,
    }


def _instrument_id(venue: str, market: str, source_symbol: str) -> str:
    return str(MarketRef.ephemeral(venue=venue, market=market, source_symbol=source_symbol).instrument_id)


__all__ = [
    "BarRecord",
    "MarketRecord",
    "MarketRecordValue",
    "OrderBookRecord",
    "QuoteRecord",
    "RateRecord",
    "TradeRecord",
    "bar_record",
    "event_time",
    "funding_rate_record",
    "iso_time",
    "ohlcv_record",
    "order_book_record",
    "orderbook_record",
    "ticker_record",
    "trade_print_record",
    "trade_record",
]
