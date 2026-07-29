from __future__ import annotations

from kairospy.application.runtime.dispatch.dispatcher import RuntimeDispatcher
from kairospy.application.runtime.orchestration.pipeline import RuntimeServicePipeline
from kairospy.application.runtime.orchestration.state import RuntimeFrame, RuntimeRunResult
from kairospy.application.runtime.protocol import RuntimeEnvelope, RuntimeEventLine, close_event_line


class RuntimeSession:
    def __init__(self, dispatcher: RuntimeDispatcher, services: RuntimeServicePipeline, frame: RuntimeFrame) -> None:
        self.dispatcher = dispatcher
        self.services = services
        self.frame = frame

    def process(self, event: RuntimeEnvelope) -> None:
        self.services.on_event(event)
        self.dispatcher.process(self.frame, event)
        hook = self.frame.callbacks[-1].hook if self.frame.callbacks else ""
        self.services.on_intents(tuple(self.dispatcher.context.emitted_intents), self.dispatcher.context, hook)

    def finish(self) -> RuntimeRunResult:
        return self.dispatcher.finish(self.frame)

    async def run(self, source: RuntimeEventLine) -> RuntimeRunResult:
        events = source.events()
        try:
            async for event in events:
                self.process(event)
        finally:
            await close_event_line(events)
        return self.finish()


__all__ = ["RuntimeSession"]
