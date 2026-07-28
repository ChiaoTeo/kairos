from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import AsyncIterable, AsyncIterator, Iterable, Mapping

from kairospy.application.context import DataView
from kairospy.core.market import (
    Bar,
    MarketEvent,
    MarketObservation,
    MarketSubject,
    OrderBookSnapshot,
    PriceLevel,
    Quote,
    RateObservation,
    TradePrint,
)
from kairospy.application.runtime.model import RuntimeDataEnvelope
from kairospy.application.runtime.source import close_async_iterator


@dataclass(frozen=True, slots=True)
class IterableEventSource:
    stream: str
    rows: tuple[Mapping[str, object], ...]

    def __init__(self, stream: str, rows: Iterable[Mapping[str, object]]) -> None:
        if not stream.strip():
            raise ValueError("event source stream is required")
        object.__setattr__(self, "stream", stream)
        object.__setattr__(self, "rows", tuple(dict(row) for row in rows))

    def events(self) -> Iterable[RuntimeDataEnvelope]:
        for index, row in enumerate(self.rows, start=1):
            yield runtime_envelope_from_row(row, sequence=index, stream=self.stream)


@dataclass(frozen=True, slots=True)
class DataViewEventSource:
    view: DataView
    stream: str | None = None

    def events(self) -> Iterable[RuntimeDataEnvelope]:
        return IterableEventSource(
            self.stream or self.view.name,
            self.view.rows(),
        ).events()


@dataclass(frozen=True, slots=True)
class AsyncDataViewEventSource:
    view: DataView
    stream: str | None = None

    async def events(self) -> AsyncIterator[RuntimeDataEnvelope]:
        stream = self.stream or self.view.binding.stream or self.view.name
        index = 0
        rows = self.view.events()
        try:
            async for row in rows:
                index += 1
                yield runtime_envelope_from_row(row, sequence=index, stream=stream)
        finally:
            await close_async_iterator(rows)


@dataclass(frozen=True, slots=True)
class AsyncIterableEventSource:
    stream: str
    rows: AsyncIterable[Mapping[str, object]]
    limit: int | None = None

    def __post_init__(self) -> None:
        if not self.stream.strip():
            raise ValueError("event source stream is required")
        if self.limit is not None and self.limit < 0:
            raise ValueError("event source limit cannot be negative")

    async def events(self) -> AsyncIterator[RuntimeDataEnvelope]:
        index = 0
        rows = self.rows.__aiter__()
        try:
            async for row in rows:
                if self.limit is not None and index >= self.limit:
                    break
                index += 1
                yield runtime_envelope_from_row(row, sequence=index, stream=self.stream)
        finally:
            await close_async_iterator(rows)


__all__ = [
    "AsyncDataViewEventSource",
    "AsyncIterableEventSource",
    "DataViewEventSource",
    "IterableEventSource",
    "runtime_envelope_from_row",
]


def runtime_envelope_from_row(
    row: Mapping[str, object],
    *,
    sequence: int,
    stream: str,
) -> RuntimeDataEnvelope:
    if "time" not in row:
        raise ValueError("event rows require a time field")
    event_time = parse_event_time(row["time"])
    kind = str(row.get("kind") or "event")
    domain = str(row.get("domain") or "market")
    return RuntimeDataEnvelope(
        domain,
        kind,
        event_time,
        sequence,
        (_market_event_from_row(row, event_time=event_time, sequence=sequence, stream=stream) or row)
        if domain == "market"
        else row,
        stream=stream,
        source=str(row.get("source") or stream),
        metadata=_row_metadata(row),
    )


def _market_event_from_row(
    row: Mapping[str, object],
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
        observation = MarketObservation(
            MarketSubject(subject_type, subject_id),
            kind,
            event_time,
            _row_payload(row),
            available_at=event_time,
            source=stream,
            sequence=sequence,
        )
        return MarketEvent(
            MarketSubject(subject_type, subject_id),
            event_time,
            observation,
            source=stream,
            available_at=event_time,
            sequence=sequence,
            metadata=_row_metadata(row),
        )
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


def _subject(row: Mapping[str, object]) -> tuple[str, str] | None:
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
    row: Mapping[str, object],
    *,
    subject_id: str,
    event_time: datetime,
    kind: str,
    stream: str,
) -> Bar | Quote | OrderBookSnapshot | TradePrint | RateObservation | None:
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
    if row.get("rate") is not None:
        return RateObservation(
            rate_id=subject_id,
            time=event_time,
            rate=Decimal(str(row["rate"])),
            source=source,
            basis=str(row.get("basis") or kind),
            market_id=market_id,
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
    metadata: dict[str, object] = {}
    for key in ("venue", "market", "source_symbol", "timeframe", "id", "trade_id", "nonce"):
        if row.get(key) is not None:
            metadata[key] = row[key]
    return metadata


def _row_payload(row: Mapping[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in row.items()
        if key not in {"domain", "kind", "time", "subject_type", "subject_id"}
    }


def parse_event_time(value: object) -> datetime:
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
