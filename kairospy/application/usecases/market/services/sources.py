from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping

from kairospy.domain.market import (
    Bar,
    MarketEvent,
    MarketObservation,
    MarketSubject,
    OptionGreeks,
    OrderBookSnapshot,
    PriceLevel,
    Quote,
    RateObservation,
    TradePrint,
)
from kairospy.application.usecases.market.application.requests import MarketDataRow, MarketTime


@dataclass(frozen=True, slots=True)
class IterableMarketEventSource:
    stream: str
    rows: tuple[MarketDataRow, ...]

    def __init__(self, stream: str, rows: Iterable[MarketDataRow]) -> None:
        if not stream.strip():
            raise ValueError("event source stream is required")
        object.__setattr__(self, "stream", stream)
        object.__setattr__(self, "rows", tuple(dict(row) for row in rows))

    async def events(self) -> AsyncIterator[MarketEvent]:
        for index, row in enumerate(self.rows, start=1):
            event = market_event_from_row(row, sequence=index, stream=self.stream)
            if event is not None:
                yield event


@dataclass(frozen=True, slots=True)
class AsyncIterableMarketEventSource:
    stream: str
    rows: AsyncIterable[MarketDataRow]
    limit: int | None = None

    def __post_init__(self) -> None:
        if not self.stream.strip():
            raise ValueError("event source stream is required")
        if self.limit is not None and self.limit < 0:
            raise ValueError("event source limit cannot be negative")

    async def events(self) -> AsyncIterator[MarketEvent]:
        index = 0
        rows = self.rows.__aiter__()
        async for row in rows:
            if self.limit is not None and index >= self.limit:
                break
            index += 1
            event = market_event_from_row(row, sequence=index, stream=self.stream)
            if event is not None:
                yield event


def market_event_from_row(row: MarketDataRow, *, sequence: int, stream: str) -> MarketEvent | None:
    if "time" not in row:
        raise ValueError("event rows require a time field")
    event_time = parse_event_time(row["time"])
    domain = str(row.get("domain") or "market")
    if domain != "market":
        return None
    return _market_event_from_row(row, event_time=event_time, sequence=sequence, stream=stream)


def _market_event_from_row(
    row: MarketDataRow,
    *,
    event_time: datetime,
    sequence: int,
    stream: str,
) -> MarketEvent | None:
    subject = _subject(row)
    if subject is None:
        return None
    subject_type, subject_id = subject
    kind = str(row.get("kind") or "fields")
    if subject_type not in {"instrument", "market", "rate"}:
        value = MarketObservation(
            MarketSubject(subject_type, subject_id),
            kind,
            event_time,
            _row_payload(row),
            available_at=event_time,
            source=stream,
            sequence=sequence,
        )
    else:
        value = _market_value_from_row(row, subject_id=subject_id, event_time=event_time, kind=kind, stream=stream)
    if value is None:
        return None
    return MarketEvent(
        MarketSubject(subject_type, subject_id),
        event_time,
        value,
        source=stream,
        available_at=event_time,
        sequence=sequence,
        metadata=_row_metadata(row),
    )


def _subject(row: MarketDataRow) -> tuple[str, str] | None:
    if row.get("subject_type") is not None and row.get("subject_id") is not None:
        return str(row["subject_type"]), str(row["subject_id"])
    kind = str(row.get("kind") or "")
    if row.get("market_id") is not None and kind == "funding_rate":
        return "market", str(row["market_id"])
    if row.get("rate_id") is not None:
        return "rate", str(row["rate_id"])
    if row.get("instrument_id") is not None:
        return "instrument", str(row["instrument_id"])
    if row.get("market_id") is not None:
        return "market", str(row["market_id"])
    return None


def _market_value_from_row(
    row: MarketDataRow,
    *,
    subject_id: str,
    event_time: datetime,
    kind: str,
    stream: str,
) -> Bar | Quote | OrderBookSnapshot | TradePrint | RateObservation | OptionGreeks | None:
    market_id = None if row.get("market_id") is None else str(row["market_id"])
    market_key = None if row.get("market_key") is None else str(row["market_key"])
    source = str(row.get("source") or stream)
    if kind in {"bar", "ohlcv"} or any(row.get(key) is not None for key in ("open", "high", "low", "close", "volume")):
        return Bar(
            instrument_id=subject_id,
            market_id=market_id,
            market_key=market_key,
            time=event_time,
            timeframe=str(row.get("timeframe") or row.get("interval") or "event"),
            open=_optional_decimal(row.get("open")),
            high=_optional_decimal(row.get("high")),
            low=_optional_decimal(row.get("low")),
            close=_optional_decimal(row.get("close")),
            volume=_optional_decimal(row.get("volume")),
            source=source,
        )
    if kind == "orderbook" or row.get("bids") is not None or row.get("asks") is not None:
        return OrderBookSnapshot(
            instrument_id=subject_id,
            market_id=market_id,
            market_key=market_key,
            time=event_time,
            bids=_levels(row.get("bids")),
            asks=_levels(row.get("asks")),
            nonce=row.get("nonce"),
            source=source,
        )
    if kind in {"trade", "trades"} or row.get("price") is not None:
        return TradePrint(
            instrument_id=subject_id,
            market_id=market_id,
            market_key=market_key,
            time=event_time,
            trade_id=None if (row.get("trade_id") or row.get("id")) is None else str(row.get("trade_id") or row.get("id")),
            side=None if row.get("side") is None else str(row["side"]),
            price=_optional_decimal(row.get("price")),
            size=_optional_decimal(row.get("size") or row.get("amount")),
            cost=_optional_decimal(row.get("cost")),
            source=source,
        )
    if kind in {"quote", "ticker"} or any(row.get(key) is not None for key in ("bid", "bid1", "ask", "ask1")):
        return Quote(
            instrument_id=subject_id,
            market_id=market_id,
            market_key=market_key,
            time=event_time,
            bid=_optional_decimal(row.get("bid") or row.get("bid1")),
            ask=_optional_decimal(row.get("ask") or row.get("ask1")),
            bid_size=_optional_decimal(row.get("bid_size") or row.get("bid1_size")),
            ask_size=_optional_decimal(row.get("ask_size") or row.get("ask1_size")),
            source=source,
            basis=str(row.get("basis") or "ticker"),
        )
    if kind in {"option_greeks", "greeks"} or any(row.get(key) is not None for key in ("delta", "gamma", "theta", "vega", "rho", "implied_volatility")):
        return OptionGreeks(
            instrument_id=subject_id,
            market_id=market_id,
            market_key=market_key,
            time=event_time,
            delta=_optional_decimal(row.get("delta")),
            gamma=_optional_decimal(row.get("gamma")),
            theta=_optional_decimal(row.get("theta")),
            vega=_optional_decimal(row.get("vega")),
            rho=_optional_decimal(row.get("rho")),
            implied_volatility=_optional_decimal(row.get("implied_volatility") or row.get("impliedVolatility")),
            mark_price=_optional_decimal(row.get("mark_price") or row.get("markPrice")),
            underlying_price=_optional_decimal(row.get("underlying_price") or row.get("underlyingPrice")),
            source=source,
            basis=str(row.get("basis") or "greeks"),
        )
    if row.get("rate") is not None:
        return RateObservation(
            rate_id=subject_id,
            time=event_time,
            rate=Decimal(str(row["rate"])),
            source=source,
            basis=str(row.get("basis") or kind),
            market_id=market_id,
            instrument_id=None if row.get("instrument_id") is None else str(row["instrument_id"]),
            mark_price=_optional_decimal(row.get("mark_price") or row.get("price")),
        )
    return None


def _levels(value: object) -> tuple[PriceLevel, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    levels: list[PriceLevel] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        levels.append(PriceLevel(Decimal(str(item[0])), Decimal(str(item[1]))))
    return tuple(levels)


def _optional_decimal(value: object | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _row_metadata(row: Mapping[str, object]) -> dict[str, object]:
    return {
        key: row[key]
        for key in ("venue", "market", "source_symbol", "timeframe", "id", "trade_id", "nonce")
        if row.get(key) is not None
    }


def _row_payload(row: Mapping[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in row.items()
        if key not in {"domain", "kind", "time", "subject_type", "subject_id"}
    }


def parse_event_time(value: MarketTime) -> datetime:
    if isinstance(value, datetime):
        event_time = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        event_time = datetime.fromisoformat(text)
    if event_time.tzinfo is None:
        raise ValueError("event time must be timezone-aware")
    return event_time


__all__ = [
    "AsyncIterableMarketEventSource",
    "IterableMarketEventSource",
    "market_event_from_row",
    "parse_event_time",
]
