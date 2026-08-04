from __future__ import annotations

from datetime import datetime

from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.support.messaging import Message


class ReferenceProjector:
    """System-business projection adapter for reference views."""

    def __init__(self, reference: object) -> None:
        self.reference = reference

    def on_event(self, event: Message) -> None:
        return None

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        return None

    def register_views(self, views: ViewStore) -> None:
        self.reference.register_views(views)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        self.reference.publish_views(views, as_of=as_of)


__all__ = ["ReferenceProjector"]
