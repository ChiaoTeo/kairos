from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol

from kairospy.context import DataView

from .events import MarketEvent, RuntimeEvent, parse_event_time


class EventSource(Protocol):
    def events(self) -> Iterable[RuntimeEvent]:
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

    def events(self) -> Iterable[MarketEvent]:
        for index, row in enumerate(self.rows, start=1):
            if "time" not in row:
                raise ValueError("event rows require a time field")
            yield MarketEvent(
                stream=self.stream,
                sequence=index,
                time=parse_event_time(row["time"]),
                payload=row,
            )


@dataclass(frozen=True, slots=True)
class DataViewEventSource:
    view: DataView
    stream: str | None = None

    def events(self) -> Iterable[MarketEvent]:
        return IterableEventSource(
            self.stream or self.view.name,
            self.view.rows(),
        ).events()


__all__ = ["DataViewEventSource", "EventSource", "IterableEventSource"]
