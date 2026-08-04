"""Market Actor-owned projections."""

from __future__ import annotations

from datetime import datetime

from kairospy.application.support.messaging import Message
from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.usecases.market.application.projector import MarketProjector
from kairospy.application.usecases.reference.application.projector import ReferenceProjector


class MarketActorProjectors:
    def __init__(self, *, market: object | None = None, reference: object | None = None) -> None:
        self.market = None if market is None else MarketProjector(market)  # type: ignore[arg-type]
        self.reference = None if reference is None else ReferenceProjector(reference)

    def on_event(self, event: Message) -> None:
        for projector in (self.market, self.reference):
            if projector is not None:
                projector.on_event(event)

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        for projector in (self.market, self.reference):
            if projector is not None:
                projector.on_intents(intents, context, hook)

    def register_views(self, views: ViewStore) -> None:
        for projector in (self.market, self.reference):
            if projector is not None:
                projector.register_views(views)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        for projector in (self.market, self.reference):
            if projector is not None:
                projector.publish_views(views, as_of=as_of)

__all__ = ["MarketActorProjectors"]
