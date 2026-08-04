from __future__ import annotations

from datetime import datetime

from kairospy.application.support.messaging import Message
from kairospy.application.support.runtime.application.views import ViewStore
from .budget import RiskApplication
from .events import RiskEventViewState
from .views import RISK_BUDGET_SCHEMA, RiskBudgetView


class RiskProjector:
    def __init__(self, application: RiskApplication | None = None) -> None:
        self.state = RiskEventViewState()
        self.application = application

    def on_event(self, event: Message) -> None:
        self.state.on_event(event)

    def register_views(self, views: ViewStore) -> None:
        if views.registry.get(self.state.schema.key) is None:
            views.register(self.state.schema)
        if views.registry.get(RISK_BUDGET_SCHEMA.key) is None:
            views.register(RISK_BUDGET_SCHEMA)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        views.put_runtime(self.state.key, self.state.view(), as_of=as_of, available_time=as_of)
        if self.application is not None:
            snapshot = self.application.snapshot(as_of=as_of)
            views.put_runtime(
                RISK_BUDGET_SCHEMA.key,
                RiskBudgetView(snapshot.budgets, snapshot.reservations, snapshot.as_of),
                as_of=as_of,
                available_time=as_of,
            )


__all__ = ["RiskProjector"]
