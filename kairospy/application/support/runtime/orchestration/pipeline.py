from __future__ import annotations

from kairospy.application.support.runtime.events import RuntimeEnvelope
from kairospy.application.support.runtime.processors.system import RuntimeProcessors
from kairospy.core.views import ViewStore


class RuntimeProjectionPipeline:
    def __init__(
        self,
        *,
        views: ViewStore,
        processors: RuntimeProcessors,
    ) -> None:
        self.views = views
        self.processors = processors
        self.processors.register_views(self.views)

    def publish(self) -> None:
        self.processors.publish_views(self.views)

    def on_event(self, event: RuntimeEnvelope) -> None:
        self.processors.on_event(event)
        self.processors.publish_views(self.views, as_of=event.time)

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        if not intents and not tuple(getattr(context, "emitted_traces", ()) or ()):
            return
        self.processors.on_intents(intents, context, hook)
        self.processors.publish_views(self.views, as_of=getattr(context, "now", None))


__all__ = ["RuntimeProjectionPipeline"]
