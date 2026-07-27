from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterable, AsyncIterator, Iterable, Mapping, Protocol

from kairospy.context import DataView
from kairospy.core.market import MarketUpdate
from kairospy.core.market import (
    FIELD_BAR_CLOSE,
    FIELD_BAR_HIGH,
    FIELD_BAR_LOW,
    FIELD_BAR_OPEN,
    FIELD_BAR_VOLUME,
    FIELD_BOOK_ASK1,
    FIELD_BOOK_BID1,
    FIELD_FUNDING_RATE,
    FIELD_QUOTE_ASK,
    FIELD_QUOTE_ASK_SIZE,
    FIELD_QUOTE_BID,
    FIELD_QUOTE_BID_SIZE,
    FIELD_TRADE_COST,
    FIELD_TRADE_PRICE,
    FIELD_TRADE_SIDE,
    FIELD_TRADE_SIZE,
)

from .data import RuntimeDataEnvelope


class EventSource(Protocol):
    def events(self) -> Iterable[RuntimeDataEnvelope]:
        ...


class AsyncEventSource(Protocol):
    def events(self) -> AsyncIterator[RuntimeDataEnvelope]:
        ...


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


async def close_async_iterator(iterator: object) -> None:
    close = getattr(iterator, "aclose", None)
    if not callable(close):
        close = getattr(iterator, "close", None)
    if not callable(close):
        return
    result = close()
    if hasattr(result, "__await__"):
        await result


__all__ = [
    "AsyncDataViewEventSource",
    "AsyncEventSource",
    "AsyncIterableEventSource",
    "DataViewEventSource",
    "EventSource",
    "IterableEventSource",
    "close_async_iterator",
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
        (_market_update_from_row(row, event_time=event_time, sequence=sequence, stream=stream) or row)
        if domain == "market"
        else row,
        stream=stream,
        source=str(row.get("source") or stream),
        metadata=_row_metadata(row),
    )


def _market_update_from_row(
    row: Mapping[str, object],
    *,
    event_time: datetime,
    sequence: int,
    stream: str,
) -> MarketUpdate | None:
    subject = _subject(row)
    if subject is None:
        return None
    subject_type, subject_id = subject
    kind = str(row.get("kind") or "fields")
    fields = _market_fields(row, kind=kind)
    if not fields:
        return None
    return MarketUpdate(
        subject_type,
        subject_id,
        event_time,
        fields,
        source=stream,
        kind=kind,
        available_at=event_time,
        sequence=sequence,
        market_id=None if row.get("market_id") is None else str(row["market_id"]),
        market_key=None if row.get("market_key") is None else str(row["market_key"]),
        interval=None if row.get("timeframe") is None else str(row["timeframe"]),
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


def _market_fields(row: Mapping[str, object], *, kind: str) -> dict[str, object]:
    fields: dict[str, object] = {}
    _copy_field(row, fields, "bid", FIELD_QUOTE_BID)
    _copy_field(row, fields, "bid1", FIELD_QUOTE_BID)
    _copy_field(row, fields, "ask", FIELD_QUOTE_ASK)
    _copy_field(row, fields, "ask1", FIELD_QUOTE_ASK)
    _copy_field(row, fields, "bid_size", FIELD_QUOTE_BID_SIZE)
    _copy_field(row, fields, "bid1_size", FIELD_QUOTE_BID_SIZE)
    _copy_field(row, fields, "ask_size", FIELD_QUOTE_ASK_SIZE)
    _copy_field(row, fields, "ask1_size", FIELD_QUOTE_ASK_SIZE)
    _copy_field(row, fields, "open", FIELD_BAR_OPEN)
    _copy_field(row, fields, "high", FIELD_BAR_HIGH)
    _copy_field(row, fields, "low", FIELD_BAR_LOW)
    _copy_field(row, fields, "close", FIELD_BAR_CLOSE)
    _copy_field(row, fields, "volume", FIELD_BAR_VOLUME)
    _copy_field(row, fields, "price", FIELD_TRADE_PRICE)
    _copy_field(row, fields, "size", FIELD_TRADE_SIZE)
    _copy_field(row, fields, "amount", FIELD_TRADE_SIZE)
    _copy_field(row, fields, "side", FIELD_TRADE_SIDE)
    _copy_field(row, fields, "cost", FIELD_TRADE_COST)
    _copy_levels(row, fields)
    if kind == "funding_rate":
        _copy_field(row, fields, "rate", FIELD_FUNDING_RATE)
    elif row.get("rate") is not None:
        fields["interest_rate.rate"] = row["rate"]
    elif row.get("value") is not None:
        fields[f"{kind}.value"] = row["value"]
    return fields


def _copy_field(row: Mapping[str, object], fields: dict[str, object], source: str, target: str) -> None:
    if row.get(source) is not None:
        fields[target] = row[source]


def _copy_levels(row: Mapping[str, object], fields: dict[str, object]) -> None:
    bids = row.get("bids")
    asks = row.get("asks")
    if isinstance(bids, (list, tuple)) and bids:
        first = bids[0]
        if isinstance(first, (list, tuple)) and len(first) >= 2:
            fields[FIELD_BOOK_BID1] = first[0]
            fields[FIELD_QUOTE_BID_SIZE] = first[1]
    if isinstance(asks, (list, tuple)) and asks:
        first = asks[0]
        if isinstance(first, (list, tuple)) and len(first) >= 2:
            fields[FIELD_BOOK_ASK1] = first[0]
            fields[FIELD_QUOTE_ASK_SIZE] = first[1]


def _row_metadata(row: Mapping[str, object]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for key in ("venue", "market", "source_symbol", "timeframe", "id", "trade_id", "nonce"):
        if row.get(key) is not None:
            metadata[key] = row[key]
    return metadata


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
