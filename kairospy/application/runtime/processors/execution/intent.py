from __future__ import annotations

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.intent import TradeIntent


class TradingIntentProcessor:
    def __init__(self, executor: object) -> None:
        self.executor = executor

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        execute_intent = getattr(self.executor, "execute_intent")
        for intent in intents:
            if isinstance(intent, TradeIntent):
                execute_intent(intent, context, hook=hook)


__all__ = ["TradingIntentProcessor"]
