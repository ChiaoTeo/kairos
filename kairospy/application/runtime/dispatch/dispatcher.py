from __future__ import annotations

from kairospy.application.runtime.dispatch.context import RuntimeContext
from kairospy.application.runtime.orchestration.state import RuntimeFrame, RuntimeRunResult, Callback
from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.application.strategy import Strategy
from kairospy.core.intent import Intent, IntentJournal


class RuntimeDispatcher:
    def __init__(
        self,
        *,
        strategy: Strategy,
        intents: IntentJournal,
        context: RuntimeContext,
    ) -> None:
        self.strategy = strategy
        self.intents = intents
        self.context = context

    def start(self, frame: RuntimeFrame) -> None:
        self._call(frame, "on_start", self.context.bind(None))

    def process(self, frame: RuntimeFrame, event: RuntimeEnvelope) -> None:
        self._ensure_active(frame)
        frame.event_count += 1
        frame.last_event = event
        context = self.context.bind(event)
        hook = hook_for(event)
        if hook == "on_intent":
            if not isinstance(event.payload, Intent):
                raise TypeError("intent runtime envelope payload must implement Intent")
            self._call(frame, hook, context, event.payload, event=event)
            return
        self._call(frame, hook, context, event, event=event)

    def finish(self, frame: RuntimeFrame) -> RuntimeRunResult:
        self._ensure_active(frame)
        self._call(frame, "on_end", self.context.bind(frame.last_event))
        frame.finished = True
        return RuntimeRunResult(
            strategy_id=self.strategy.strategy_id,
            event_count=frame.event_count,
            callbacks=tuple(frame.callbacks),
            intent_count=len(self.intents.list(strategy_id=self.strategy.strategy_id)),
            last_event=frame.last_event,
        )

    def _call(
        self,
        frame: RuntimeFrame,
        hook: str,
        context: RuntimeContext,
        *args: object,
        event: RuntimeEnvelope | None = None,
    ) -> None:
        returned = getattr(self.strategy, hook)(context, *args)
        if returned is not None:
            raise TypeError(f"{hook} must return None; emit intents with context.intent()")
        frame.callbacks.append(
            Callback(
                hook,
                None if event is None else event.sequence,
                tuple(intent.intent_id for intent in context.emitted_intents),
            )
        )

    @staticmethod
    def _ensure_active(frame: RuntimeFrame) -> None:
        if frame.finished:
            raise RuntimeError("runtime session is already finished")


def hook_for(event: RuntimeEnvelope) -> str:
    if event.domain == "clock":
        return "on_clock"
    if event.domain == "system":
        return "on_system"
    if event.domain == "intent":
        return "on_intent"
    return "on_data"


__all__ = ["RuntimeDispatcher", "hook_for"]
