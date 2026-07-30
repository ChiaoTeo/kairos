from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.market import MarketViewKeys
from kairospy.core.views import ViewFieldSchema, ViewSchema, ViewStore

from .projection import MarketProjectionState


@dataclass(frozen=True, slots=True)
class MarketViewState:
    projection: MarketProjectionState

    @property
    def schemas(self) -> tuple[ViewSchema, ...]:
        return self.projection.schemas

    def on_event(self, event: RuntimeEnvelope) -> None:
        self.projection.apply_envelope(event)

    def publish(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        views.put_runtime(MarketViewKeys.subscriptions, self.projection.subscriptions_view(), as_of=as_of, available_time=as_of)
        for key, kind, window in self.projection.window_views():
            schema = _window_schema(key, kind)
            if views.registry.get(key) is None:
                views.register(schema)
            views.put_runtime(key, window, as_of=as_of, available_time=as_of)
        views.put_runtime(MarketViewKeys.windows, self.projection.windows_view(), as_of=as_of, available_time=as_of)


__all__ = ["MarketViewState"]


def _window_schema(key: str, kind: str) -> ViewSchema:
    return ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("subject_type", "market window subject type", "runtime state", "market event window state"),
            ViewFieldSchema("subject_id", "market window subject identity", "runtime state", "market event window state"),
            ViewFieldSchema("items", f"{kind} window items", "event time", "market event window state"),
            ViewFieldSchema("event_count", f"{kind} event count", "runtime sequence", "market event window state"),
            ViewFieldSchema("updated_at", f"latest {kind} event time", "event time", "market event window state"),
        ),
        mutability="runtime_writable",
        evidence=f"runtime market {kind} window state",
    )
