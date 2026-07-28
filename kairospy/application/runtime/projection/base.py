from __future__ import annotations

from datetime import datetime
from typing import Protocol

from kairospy.core.views import ViewSchema, ViewStore

from ..model import RuntimeDataEnvelope


class RuntimeComponent(Protocol):
    """Runtime projection component that owns one view."""

    @property
    def key(self) -> str:
        ...

    @property
    def schema(self) -> ViewSchema:
        ...

    def on_event(self, event: RuntimeDataEnvelope) -> None:
        ...

    def view(self) -> object:
        ...


class RuntimeProjection(Protocol):
    def register(self, views: ViewStore) -> None:
        ...

    def on_event(self, event: RuntimeDataEnvelope) -> None:
        ...

    def publish(
        self,
        views: ViewStore,
        *,
        as_of: datetime | None,
        event_count: int,
        runtime_event_count: int,
        last_event: RuntimeDataEnvelope | None,
        last_runtime_event: RuntimeDataEnvelope | None,
        status: str,
    ) -> datetime | None:
        ...


__all__ = ["RuntimeComponent", "RuntimeProjection"]
