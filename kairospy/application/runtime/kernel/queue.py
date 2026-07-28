from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from datetime import datetime

from ..model import RuntimeDataEnvelope, RuntimeMode, system_data_envelope


class RuntimeQueue:
    def __init__(
        self,
        mode: RuntimeMode | str | None,
        source_events: Iterable[RuntimeDataEnvelope] = (),
        *,
        pre_events: Iterable[RuntimeDataEnvelope] = (),
        started_at: datetime | None = None,
    ) -> None:
        events = tuple(source_events)
        if mode is not None and started_at is None and events:
            started_at = events[0].time
        prefix: tuple[RuntimeDataEnvelope, ...] = ()
        if mode is not None and started_at is not None:
            runtime_mode = mode if isinstance(mode, RuntimeMode) else RuntimeMode(str(mode))
            prefix = (
                system_data_envelope(
                    f"runtime.mode.{runtime_mode.value}.started",
                    sequence=1,
                    time=started_at,
                    payload={"mode": runtime_mode.value},
                    stream="system.runtime",
                ),
            )
        self._events: deque[RuntimeDataEnvelope] = deque((*prefix, *tuple(pre_events), *events))

    @classmethod
    def pending(cls, events: Iterable[RuntimeDataEnvelope]) -> "RuntimeQueue":
        return cls(None, events)

    def next(self) -> RuntimeDataEnvelope | None:
        if not self._events:
            return None
        return self._events.popleft()

    def extend(self, events: Iterable[RuntimeDataEnvelope]) -> None:
        self._events.extend(events)

    def events(self) -> Iterable[RuntimeDataEnvelope]:
        while True:
            event = self.next()
            if event is None:
                break
            yield event


__all__ = ["RuntimeQueue"]
