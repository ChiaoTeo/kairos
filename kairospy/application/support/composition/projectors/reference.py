from __future__ import annotations

from datetime import datetime

from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.application.usecases.reference.application.runtime import RuntimeReferenceService
from kairospy.application.usecases.reference.application.projections import ReferenceProjectionService


class ReferenceProjector:
    def __init__(self, service: RuntimeReferenceService) -> None:
        self.service = service

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        return None

    def register_views(self, views: ViewStore) -> None:
        self._projection().register_views(views)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        self._projection().publish_views(views, as_of=as_of)

    def _projection(self) -> ReferenceProjectionService:
        return ReferenceProjectionService(self.service.catalog(), self.service.lifecycle_events())


__all__ = ["ReferenceProjector"]
