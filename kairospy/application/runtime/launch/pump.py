from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime

from kairospy.application.modes import RuntimeMode
from kairospy.application.protocol import RuntimeEnvelope, RuntimeEventLine, close_event_line, system_envelope


@dataclass(frozen=True, slots=True)
class RuntimeEnvelopePump(RuntimeEventLine):
    source: RuntimeEventLine
    mode: RuntimeMode | str
    pre_events: tuple[RuntimeEnvelope, ...] = ()
    started_at: datetime | None = None

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        source_events = self.source.events()
        try:
            first = await anext(source_events, None)
            started_at = self.started_at or (None if first is None else first.time)
            if started_at is not None:
                yield system_envelope(
                    f"runtime.mode.{RuntimeMode(self.mode).value}.started",
                    time=started_at,
                    sequence=1,
                    payload={"mode": RuntimeMode(self.mode).value},
                )
            for event in self.pre_events:
                yield event
            if first is not None:
                yield first
            async for event in source_events:
                yield event
        finally:
            await close_event_line(source_events)


__all__ = ["RuntimeEnvelopePump"]
