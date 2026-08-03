from __future__ import annotations

import csv
import asyncio
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol

from kairospy.application.support.runtime.domain.events import RuntimeEnvelope


class RuntimeDataSource(Protocol):
    source_id: str

    def events(self) -> AsyncIterator[RuntimeEnvelope]:
        ...


@dataclass(frozen=True, slots=True)
class DataObservation:
    subject_type: str
    subject_id: str
    kind: str
    observed_at: datetime
    available_at: datetime
    value: Mapping[str, object]
    source: str
    metadata: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("data observation observed_at must be timezone-aware")
        if self.available_at.tzinfo is None:
            raise ValueError("data observation available_at must be timezone-aware")
        object.__setattr__(self, "value", MappingProxyType(dict(self.value)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class ClockTick:
    at: datetime
    source: str
    name: str = "tick"
    metadata: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        if self.at.tzinfo is None:
            raise ValueError("clock tick time must be timezone-aware")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


class IterableEventSource:
    is_finite = True

    def __init__(self, source_id: str, events: Iterable[RuntimeEnvelope]) -> None:
        if not source_id.strip():
            raise ValueError("source_id is required")
        self.source_id = source_id
        self._events = tuple(sorted(events, key=lambda event: (event.time, event.sequence)))

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        for event in self._events:
            yield event


class AsyncEventSource:
    def __init__(self, source_id: str, events: AsyncIterable[RuntimeEnvelope], *, limit: int | None = None) -> None:
        if not source_id.strip():
            raise ValueError("source_id is required")
        if limit is not None and limit < 0:
            raise ValueError("limit cannot be negative")
        self.source_id = source_id
        self._events = events
        self.limit = limit
        self.is_finite = limit is not None

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        count = 0
        async for event in self._events:
            if self.limit is not None and count >= self.limit:
                break
            count += 1
            yield event


class ClockEventSource:
    is_finite = True

    def __init__(
        self,
        source_id: str,
        ticks: Iterable[datetime | str],
        *,
        kind: str = "tick",
        metadata: Mapping[str, object] | None = None,
        default_timezone: tzinfo | None = None,
    ) -> None:
        if not source_id.strip():
            raise ValueError("source_id is required")
        if not kind.strip():
            raise ValueError("clock kind is required")
        self.source_id = source_id
        self.kind = kind
        self.metadata = MappingProxyType(dict(metadata or {}))
        self.default_timezone = default_timezone
        self._ticks = tuple(sorted(_coerce_time(tick, default_timezone=self.default_timezone) for tick in ticks))

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        for sequence, at in enumerate(self._ticks, start=1):
            yield RuntimeEnvelope("clock", self.kind, at, sequence, ClockTick(at, self.source_id, self.kind, self.metadata))


class IntervalClockSource:
    is_finite = True

    def __init__(
        self,
        source_id: str,
        *,
        start: datetime | str,
        end: datetime | str,
        every: timedelta | str | int | float,
        kind: str = "tick",
        metadata: Mapping[str, object] | None = None,
        default_timezone: tzinfo | None = None,
    ) -> None:
        if not source_id.strip():
            raise ValueError("source_id is required")
        if not kind.strip():
            raise ValueError("clock kind is required")
        self.source_id = source_id
        self.default_timezone = default_timezone
        self.start = _coerce_time(start, default_timezone=self.default_timezone)
        self.end = _coerce_time(end, default_timezone=self.default_timezone)
        self.every = _coerce_duration(every)
        self.kind = kind
        self.metadata = MappingProxyType(dict(metadata or {}))
        if self.end < self.start:
            raise ValueError("clock interval end must be greater than or equal to start")
        if self.every.total_seconds() <= 0:
            raise ValueError("clock interval must be positive")

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        sequence = 1
        at = self.start
        while at <= self.end:
            yield RuntimeEnvelope("clock", self.kind, at, sequence, ClockTick(at, self.source_id, self.kind, self.metadata))
            sequence += 1
            at += self.every


class RealtimeClockSource:
    def __init__(
        self,
        source_id: str,
        *,
        every: timedelta | str | int | float,
        limit: int | None = None,
        start_immediately: bool = False,
        kind: str = "tick",
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        if not source_id.strip():
            raise ValueError("source_id is required")
        if not kind.strip():
            raise ValueError("clock kind is required")
        if limit is not None and limit < 0:
            raise ValueError("clock limit cannot be negative")
        self.source_id = source_id
        self.every = _coerce_duration(every)
        self.limit = limit
        self.start_immediately = start_immediately
        self.kind = kind
        self.metadata = MappingProxyType(dict(metadata or {}))
        self.is_finite = limit is not None
        if self.every.total_seconds() <= 0:
            raise ValueError("clock interval must be positive")

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        sequence = 1
        while self.limit is None or sequence <= self.limit:
            if sequence > 1 or not self.start_immediately:
                await asyncio.sleep(self.every.total_seconds())
            at = datetime.now(timezone.utc)
            yield RuntimeEnvelope("clock", self.kind, at, sequence, ClockTick(at, self.source_id, self.kind, self.metadata))
            sequence += 1


class CsvEventSource:
    is_finite = True

    def __init__(
        self,
        path: str | Path,
        *,
        source_id: str | None = None,
        domain: str = "data",
        kind: str,
        time_field: str = "time",
        observed_at_field: str | None = None,
        available_at_field: str | None = None,
        subject_type: str = "data",
        subject_id: str | None = None,
        subject_id_field: str | None = None,
        value_fields: Iterable[str] | None = None,
        metadata_fields: Iterable[str] = (),
        default_timezone: tzinfo | None = None,
    ) -> None:
        self.path = Path(path).expanduser()
        self.source_id = source_id or self.path.stem
        self.domain = domain
        self.kind = kind
        self.time_field = time_field
        self.observed_at_field = observed_at_field
        self.available_at_field = available_at_field
        self.subject_type = subject_type
        self.subject_id = subject_id
        self.subject_id_field = subject_id_field
        self.value_fields = None if value_fields is None else tuple(value_fields)
        self.metadata_fields = tuple(metadata_fields)
        self.default_timezone = default_timezone
        if not self.kind.strip():
            raise ValueError("kind is required")

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        rows = self._rows()
        for sequence, row in enumerate(sorted(rows, key=lambda item: item["_event_time"]), start=1):
            event_time = row["_event_time"]
            payload = DataObservation(
                subject_type=self.subject_type,
                subject_id=self._subject_id(row),
                kind=self.kind,
                observed_at=self._row_time(row, self.observed_at_field) or event_time,
                available_at=self._row_time(row, self.available_at_field) or event_time,
                value=self._value(row),
                source=self.source_id,
                metadata={field: row[field] for field in self.metadata_fields if field in row},
            )
            yield RuntimeEnvelope(self.domain, self.kind, event_time, sequence, payload)

    def _rows(self) -> tuple[dict[str, object], ...]:
        with self.path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = []
            for index, row in enumerate(reader, start=2):
                if self.time_field not in row or not str(row[self.time_field] or "").strip():
                    raise ValueError(f"CSV event row {index} requires {self.time_field!r}: {self.path}")
                item: dict[str, object] = {key: _coerce(value) for key, value in row.items()}
                item["_event_time"] = _coerce_time(item[self.time_field], default_timezone=self.default_timezone)
                rows.append(item)
            return tuple(rows)

    def _row_time(self, row: Mapping[str, object], field: str | None) -> datetime | None:
        if field is None or row.get(field) in {None, ""}:
            return None
        return _coerce_time(row[field], default_timezone=self.default_timezone)

    def _subject_id(self, row: Mapping[str, object]) -> str:
        if self.subject_id is not None:
            return self.subject_id
        if self.subject_id_field is not None and row.get(self.subject_id_field) not in {None, ""}:
            return str(row[self.subject_id_field])
        return self.source_id

    def _value(self, row: Mapping[str, object]) -> Mapping[str, object]:
        excluded = {
            "_event_time",
            self.time_field,
            self.observed_at_field,
            self.available_at_field,
            self.subject_id_field,
            *self.metadata_fields,
        }
        if self.value_fields is not None:
            return {field: row[field] for field in self.value_fields if field in row}
        return {key: value for key, value in row.items() if key not in excluded and value not in {None, ""}}


def _coerce(value: object) -> object:
    if value is None:
        return None
    text = str(value)
    if text == "":
        return ""
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        if "." not in text:
            return int(text)
        return float(text)
    except ValueError:
        return text


def _coerce_time(value: object, *, default_timezone: tzinfo | None = None) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None and default_timezone is not None:
            return value.replace(tzinfo=default_timezone)
        if value.tzinfo is None:
            raise ValueError("runtime time must be timezone-aware")
        return value
    text = str(value).strip()
    try:
        return _parse_timezone_aware_time(text)
    except ValueError:
        if default_timezone is None:
            raise
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        return parsed
    return parsed.replace(tzinfo=default_timezone)


def _parse_timezone_aware_time(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("runtime time must be timezone-aware")
    return parsed


def _coerce_duration(value: timedelta | str | int | float) -> timedelta:
    if isinstance(value, timedelta):
        return value
    if isinstance(value, (int, float)):
        return timedelta(seconds=float(value))
    text = value.strip().lower()
    if not text:
        raise ValueError("duration is required")
    units = {
        "ms": 0.001,
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
    }
    for suffix, multiplier in units.items():
        if text.endswith(suffix):
            return timedelta(seconds=float(text[: -len(suffix)]) * multiplier)
    return timedelta(seconds=float(text))


__all__ = [
    "AsyncEventSource",
    "ClockEventSource",
    "ClockTick",
    "CsvEventSource",
    "DataObservation",
    "IntervalClockSource",
    "IterableEventSource",
    "RealtimeClockSource",
    "RuntimeDataSource",
]
