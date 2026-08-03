from __future__ import annotations

from datetime import datetime
from typing import Protocol

from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.application.support.runtime.application.views import ViewStore


class RuntimeProjector(Protocol):
    """Port consumed by runtime to install and drive read-model projectors.

    Runtime owns the publication lifecycle. A projector owns the meaning of
    the view it produces and may be supplied by a usecase or composition
    root. Runtime must not inspect the projector's business type.
    """

    def on_event(self, event: RuntimeEnvelope) -> None: ...

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None: ...

    def register_views(self, views: ViewStore) -> None: ...

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None: ...


__all__ = ["RuntimeProjector"]
