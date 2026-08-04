"""Account Actor-owned projections."""

from __future__ import annotations

from datetime import datetime

from kairospy.application.support.messaging import Message
from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.usecases.account.application.projectors import AccountProjector, EquityProjector, FundingProjector
from kairospy.application.usecases.execution.application.execution_projector import ExecutionProjector
from kairospy.application.usecases.execution.application.order_projector import OrderProjector
from kairospy.application.usecases.intent.application.projection import IntentProjector
from kairospy.domain.intent import IntentJournal


class AccountActorProjectors:
    def __init__(self, *, strategy_id: str, intents: IntentJournal, account: object | None = None, execution: object | None = None, risk: object | None = None) -> None:
        self.account = None if account is None else AccountProjector(account)  # type: ignore[arg-type]
        self.funding = _funding(account)
        self.equity = _equity(account)
        self.execution = None if execution is None or not getattr(execution, "has_projection", False) or not getattr(execution, "has_updates", False) else ExecutionProjector(execution)  # type: ignore[arg-type]
        self.order = None if execution is None or not getattr(execution, "has_projection", False) else OrderProjector(execution)  # type: ignore[arg-type]
        self.intent = IntentProjector(strategy_id=strategy_id, intents=intents)

    def _all(self) -> tuple[object, ...]:
        return tuple(item for item in (self.account, self.funding, self.equity, self.execution, self.order, self.intent) if item is not None)

    def on_event(self, event: Message) -> None:
        for projector in self._all():
            handler = getattr(projector, "on_event", None)
            if callable(handler):
                handler(event)

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        for projector in self._all():
            handler = getattr(projector, "on_intents", None)
            if callable(handler):
                handler(intents, context, hook)

    def register_views(self, views: ViewStore) -> None:
        for projector in self._all():
            projector.register_views(views)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        for projector in self._all():
            projector.publish_views(views, as_of=as_of)


def _funding(account: object | None) -> FundingProjector | None:
    if account is None or getattr(account, "projection", None) is None:
        return None
    environment = getattr(getattr(account, "account", None), "environment", None)
    if getattr(environment, "value", str(environment)) != "backtest":
        return None
    return FundingProjector(service=account)  # type: ignore[arg-type]


def _equity(account: object | None) -> EquityProjector | None:
    return None if account is None or getattr(account, "projection", None) is None else EquityProjector(service=account)  # type: ignore[arg-type]


__all__ = ["AccountActorProjectors"]
