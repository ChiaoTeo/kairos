from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.core.views import ViewSchema, ViewStore

from ...model import RuntimeDataEnvelope
from .publisher import MarketViewPublisher
from .store import MarketStore


@dataclass(frozen=True, slots=True)
class MarketProjection:
    market: MarketStore

    @property
    def publisher(self) -> MarketViewPublisher:
        return MarketViewPublisher(self.market)

    @property
    def schemas(self) -> tuple[ViewSchema, ...]:
        return self.publisher.schemas

    def register(self, views: ViewStore) -> None:
        self.publisher.register(views)

    def on_event(self, event: RuntimeDataEnvelope) -> None:
        if event.domain == "market":
            self.market.apply_envelope(event)

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
        self.publisher.publish(views, as_of=as_of)
        return as_of


__all__ = ["MarketProjection"]
