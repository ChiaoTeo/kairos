from __future__ import annotations

from typing import Mapping

from kairospy.application.support.messaging import Message
from kairospy.application.usecases.risk.application.views import RISK_EVENTS_SCHEMA, RiskEventView, RiskViewKeys


class RiskEventViewState:
    key = RiskViewKeys.events
    schema = RISK_EVENTS_SCHEMA

    def __init__(self) -> None:
        self._event_count = 0
        self._last_event: Message | None = None

    def on_event(self, event: Message) -> None:
        if event.domain == "system" and event.kind.startswith("risk."):
            self._event_count += 1
            self._last_event = event

    def view(self) -> RiskEventView:
        if self._last_event is None:
            return RiskEventView(event_count=self._event_count)
        return RiskEventView(
            event_count=self._event_count,
            last_name=self._last_event.kind,
            last_event_time=self._last_event.time,
            last_payload=_payload_dict(self._last_event.payload),
        )


def _payload_dict(payload: object) -> dict[str, object]:
    if isinstance(payload, Mapping):
        return dict(payload)
    return {"type": type(payload).__name__}


__all__ = ["RiskEventViewState"]
