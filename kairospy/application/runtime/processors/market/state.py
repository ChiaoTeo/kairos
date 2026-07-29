from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.views import ViewSchema, ViewStore

from .store import MarketStore


@dataclass(frozen=True, slots=True)
class MarketViewState:
    market: MarketStore

    @property
    def schemas(self) -> tuple[ViewSchema, ...]:
        return self.market.schemas

    def on_event(self, event: RuntimeEnvelope) -> None:
        self.market.apply_envelope(event)

    def publish(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        views.put_runtime("market.subscriptions", self.market.subscriptions_view(), as_of=as_of, available_time=as_of)
        views.put_runtime("market.quotes", self.market.quotes_view(), as_of=as_of, available_time=as_of)
        views.put_runtime("market.rates", self.market.rates_view(), as_of=as_of, available_time=as_of)
        views.put_runtime("market.books", self.market.books_view(), as_of=as_of, available_time=as_of)
        views.put_runtime("market.bars", self.market.bars_view(), as_of=as_of, available_time=as_of)
        views.put_runtime("market.trades", self.market.trades_view(), as_of=as_of, available_time=as_of)
        views.put_runtime("market.observations", self.market.observations_view(), as_of=as_of, available_time=as_of)
        views.put_runtime("market.fields", self.market.fields_view(), as_of=as_of, available_time=as_of)


__all__ = ["MarketViewState"]
