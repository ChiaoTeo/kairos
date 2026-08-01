from __future__ import annotations

from kairospy.application.support.runtime.events import RuntimeEnvelope
from kairospy.application.support.runtime.services.application import RuntimeExecutionService
from kairospy.core.intent import TradeIntent


class TradingIntentProcessor:
    def __init__(self, service: RuntimeExecutionService) -> None:
        self.service = service

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        for intent in intents:
            if isinstance(intent, TradeIntent):
                self.service.submit_intent(intent, context)


__all__ = ["TradingIntentProcessor"]
