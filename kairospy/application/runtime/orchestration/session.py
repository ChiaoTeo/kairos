from __future__ import annotations

from kairospy.application.runtime.execution.engine import RuntimeEngine
from kairospy.application.runtime.orchestration.pipeline import RuntimeServicePipeline
from kairospy.application.runtime.orchestration.state import RuntimeFrame, RuntimeRunResult
from kairospy.application.runtime.protocol import RuntimeEnvelope, RuntimeEventLine, close_event_line


class RuntimeSession:
    def __init__(self, engine: RuntimeEngine, services: RuntimeServicePipeline, frame: RuntimeFrame) -> None:
        self.engine = engine
        self.services = services
        self.frame = frame

    def process(self, event: RuntimeEnvelope) -> None:
        self.services.on_event(event)
        self.engine.process(self.frame, event)
        hook = self.frame.callbacks[-1].hook if self.frame.callbacks else ""
        self.services.on_intents(tuple(self.engine.context.emitted_intents), self.engine.context, hook)

    def finish(self) -> RuntimeRunResult:
        return self.engine.finish(self.frame)

    async def run(self, source: RuntimeEventLine) -> RuntimeRunResult:
        events = source.events()
        try:
            async for event in events:
                self.process(event)
        finally:
            await close_event_line(events)
        return self.finish()


__all__ = ["RuntimeSession"]
