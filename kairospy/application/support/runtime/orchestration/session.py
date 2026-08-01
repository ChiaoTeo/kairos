from __future__ import annotations

from kairospy.application.support.runtime.dispatch.dispatcher import RuntimeDispatcher
from kairospy.application.support.runtime.orchestration.pipeline import RuntimeProjectionPipeline
from kairospy.application.support.runtime.orchestration.state import RuntimeFrame, RuntimeLaunchResult, RuntimeStep
from kairospy.application.support.runtime.events import RuntimeEnvelope
from kairospy.application.support.runtime.lines import RuntimeEventLine, close_event_line
from kairospy.core.views import ViewStore


class RuntimeSession:
    def __init__(self, dispatcher: RuntimeDispatcher, pipeline: RuntimeProjectionPipeline, frame: RuntimeFrame) -> None:
        self.dispatcher = dispatcher
        self.pipeline = pipeline
        self.frame = frame

    def process(self, event: RuntimeEnvelope) -> tuple[RuntimeStep, ...]:
        self.pipeline.on_event(event)
        steps = [RuntimeStep("event", as_of=event.time, event=event, views=_view_snapshot(self.pipeline.views))]
        self.dispatcher.process(self.frame, event)
        hook = self.frame.callbacks[-1].hook if self.frame.callbacks else ""
        intents = tuple(self.dispatcher.context.emitted_intents)
        traces = tuple(self.dispatcher.context.emitted_traces)
        self.pipeline.on_intents(intents, self.dispatcher.context, hook)
        if intents or traces:
            steps.append(
                RuntimeStep(
                    "decision",
                    as_of=getattr(self.dispatcher.context, "now", None),
                    event=event,
                    intents=intents,
                    traces=traces,
                    context=self.dispatcher.context,
                    hook=hook,
                    views=_view_snapshot(self.pipeline.views),
                )
            )
        return tuple(steps)

    def finish(self) -> RuntimeLaunchResult:
        return self.dispatcher.finish(self.frame)

    async def run(self, source: RuntimeEventLine) -> RuntimeLaunchResult:
        events = source.events()
        try:
            async for event in events:
                self.process(event)
        finally:
            await close_event_line(events)
        return self.finish()


__all__ = ["RuntimeSession"]


def _view_snapshot(views: ViewStore) -> ViewStore:
    return ViewStore(views.registry, views.envelopes().values())
