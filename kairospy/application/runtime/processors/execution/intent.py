from __future__ import annotations

from kairospy.application.protocol import RuntimeEnvelope
from kairospy.application.service.runtime import RuntimeExecutionService
from kairospy.core.intent import TradeIntent


class TradingIntentProcessor:
    def __init__(self, service: RuntimeExecutionService) -> None:
        self.service = service

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        for intent in intents:
            if isinstance(intent, TradeIntent):
                self.service.execute_intent(intent, context, hook=hook)


__all__ = ["TradingIntentProcessor"]
