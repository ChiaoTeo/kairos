from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.core.views import ViewSchema, ViewStore

from .store import MarketStore


@dataclass(frozen=True, slots=True)
class MarketViewPublisher:
    store: MarketStore

    @property
    def schemas(self) -> tuple[ViewSchema, ...]:
        return self.store.schemas

    def register(self, views: ViewStore) -> None:
        for schema in self.schemas:
            views.register(schema)

    def publish(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        views.put_runtime("market.subscriptions", self.store.subscriptions_view(), as_of=as_of, available_time=as_of)
        views.put_runtime("market.quotes", self.store.quotes_view(), as_of=as_of, available_time=as_of)
        views.put_runtime("market.rates", self.store.rates_view(), as_of=as_of, available_time=as_of)
        views.put_runtime("market.books", self.store.books_view(), as_of=as_of, available_time=as_of)
        views.put_runtime("market.bars", self.store.bars_view(), as_of=as_of, available_time=as_of)
        views.put_runtime("market.trades", self.store.trades_view(), as_of=as_of, available_time=as_of)
        views.put_runtime("market.observations", self.store.observations_view(), as_of=as_of, available_time=as_of)
        views.put_runtime("market.fields", self.store.fields_view(), as_of=as_of, available_time=as_of)

__all__ = ["MarketViewPublisher"]
