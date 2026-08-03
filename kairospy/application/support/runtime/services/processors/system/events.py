from __future__ import annotations

from typing import Mapping

from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.application.support.runtime.application.views import SYSTEM_EVENTS_SCHEMA, SystemEventView, SystemViewKeys


class SystemEventViewState:
    key = SystemViewKeys.events
    schema = SYSTEM_EVENTS_SCHEMA

    def __init__(self) -> None:
        self._event_count = 0
        self._last_event: RuntimeEnvelope | None = None

    def on_event(self, event: RuntimeEnvelope) -> None:
        if event.domain == "system":
            self._event_count += 1
            self._last_event = event

    def view(self) -> SystemEventView:
        if self._last_event is None:
            return SystemEventView(event_count=self._event_count)
        return SystemEventView(
            event_count=self._event_count,
            last_name=self._last_event.kind,
            last_event_time=self._last_event.time,
            last_payload=_payload_dict(self._last_event.payload),
        )


def _payload_dict(payload: object) -> dict[str, object]:
    if isinstance(payload, Mapping):
        return dict(payload)
    return {"type": type(payload).__name__}


__all__ = ["SystemEventViewState"]
