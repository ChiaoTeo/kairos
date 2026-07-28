from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import AsyncIterator

from ..model import RuntimeDataEnvelope, RuntimeMode, system_data_envelope
from ..source import AsyncEventSource, EventSource, close_async_iterator


@dataclass(frozen=True, slots=True)
class RuntimeEnvelopePump:
    mode: RuntimeMode
    pre_events: tuple[RuntimeDataEnvelope, ...] = ()
    started_at: object = None

    def events(self, source: EventSource) -> Iterable[RuntimeDataEnvelope]:
        events = iter(source.events())
        first = next(events, None)
        start_time = self.started_at if self.started_at is not None else None if first is None else first.time
        yield from self._prefix(start_time)
        if first is not None:
            yield first
        yield from events

    async def async_events(self, source: AsyncEventSource) -> AsyncIterator[RuntimeDataEnvelope]:
        events = source.events()
        try:
            first = await anext(events, None)
            start_time = self.started_at if self.started_at is not None else None if first is None else first.time
            for event in self._prefix(start_time):
                yield event
            if first is not None:
                yield first
            async for event in events:
                yield event
        finally:
            await close_async_iterator(events)

    def _prefix(self, start_time: object) -> Iterator[RuntimeDataEnvelope]:
        if start_time is not None:
            yield system_data_envelope(
                f"runtime.mode.{self.mode.value}.started",
                sequence=1,
                time=start_time,
                payload={"mode": self.mode.value},
                stream="system.runtime",
            )
        yield from self.pre_events


__all__ = ["RuntimeEnvelopePump"]
