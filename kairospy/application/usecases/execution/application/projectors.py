from __future__ import annotations

from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.support.messaging import Message
from kairospy.application.usecases.execution.application.runtime import RuntimeExecutionService
from kairospy.domain.intent import TradeIntent


class TradingIntentProjector:
    def __init__(self, service: RuntimeExecutionService) -> None:
        self.service = service

    def on_event(self, event: Message) -> None:
        return None

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        for intent in intents:
            if isinstance(intent, TradeIntent):
                self.service.execute_intent(intent, context)

    def register_views(self, views: ViewStore) -> None:
        return None

    def publish_views(self, views: ViewStore, *, as_of: object | None = None) -> None:
        return None


__all__ = ["TradingIntentProjector"]
