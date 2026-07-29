from __future__ import annotations

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.views import ViewFieldSchema, ViewSchema

from .models import StrategyRunView


class RuntimeSystemViewState:
    key = "system.strategy"
    schema = ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("strategy_id", "strategy identity", "run identity", "runtime"),
            ViewFieldSchema("event_count", "consumed runtime event count", "runtime sequence", "runtime"),
            ViewFieldSchema("runtime_event_count", "consumed runtime event count", "runtime sequence", "runtime"),
            ViewFieldSchema("last_event_time", "latest runtime event time", "event time", "runtime event source"),
            ViewFieldSchema("last_domain", "latest runtime event domain", "event time", "runtime event source"),
            ViewFieldSchema("last_kind", "latest runtime event kind", "event time", "runtime event source"),
            ViewFieldSchema("status", "runtime status", "runtime time", "runtime"),
        ),
        mutability="runtime_writable",
        evidence="strategy runtime loop view state",
    )

    def __init__(self, *, strategy_id: str) -> None:
        self.strategy_id = strategy_id
        self._event_count = 0
        self._last_event: RuntimeEnvelope | None = None
        self.status = "initialized"

    def on_event(self, event: RuntimeEnvelope) -> None:
        self._event_count += 1
        self._last_event = event
        self.status = "running"

    def view(self) -> StrategyRunView:
        return StrategyRunView(
            strategy_id=self.strategy_id,
            event_count=self._event_count,
            runtime_event_count=self._event_count,
            last_event_time=None if self._last_event is None else self._last_event.time,
            last_domain=None if self._last_event is None else str(self._last_event.domain),
            last_kind=None if self._last_event is None else self._last_event.kind,
            status=self.status,
        )


__all__ = ["RuntimeSystemViewState"]
