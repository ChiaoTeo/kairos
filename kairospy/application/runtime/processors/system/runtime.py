from __future__ import annotations

from kairospy.application.protocol import RuntimeEnvelope
from kairospy.application.views import STRATEGY_LAUNCH_SCHEMA, StrategyLaunchView, SystemViewKeys


class RuntimeSystemViewState:
    key = SystemViewKeys.strategy
    schema = STRATEGY_LAUNCH_SCHEMA

    def __init__(self, *, strategy_id: str) -> None:
        self.strategy_id = strategy_id
        self._event_count = 0
        self._last_event: RuntimeEnvelope | None = None
        self.status = "initialized"

    def on_event(self, event: RuntimeEnvelope) -> None:
        self._event_count += 1
        self._last_event = event
        self.status = "running"

    def view(self) -> StrategyLaunchView:
        return StrategyLaunchView(
            strategy_id=self.strategy_id,
            event_count=self._event_count,
            runtime_event_count=self._event_count,
            last_event_time=None if self._last_event is None else self._last_event.time,
            last_domain=None if self._last_event is None else str(self._last_event.domain),
            last_kind=None if self._last_event is None else self._last_event.kind,
            status=self.status,
        )


__all__ = ["RuntimeSystemViewState"]
