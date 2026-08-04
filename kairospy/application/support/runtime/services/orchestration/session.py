from __future__ import annotations

from kairospy.application.support.runtime.application.dispatch.dispatcher import RuntimeDispatcher
from kairospy.application.support.runtime.services.orchestration.state import RuntimeCycle, RuntimeFrame, RuntimeResult
from kairospy.application.support.messaging import Message
from kairospy.application.support.runtime.application.views import ViewStore


class RuntimeSession:
    def __init__(
        self,
        dispatcher: RuntimeDispatcher,
        views: ViewStore,
        frame: RuntimeFrame,
        system_call: object | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.views = views
        self.frame = frame
        self.system_call = system_call

    @property
    def is_finished(self) -> bool:
        return self.frame.finished
    def observe(self, event: Message) -> RuntimeCycle:
        """Record an external event without deciding whether strategy runs."""
        return RuntimeCycle(as_of=event.time, event=event, views=_view_snapshot(self.views))

    def process(self, event: Message, *, hook: str | None = None) -> RuntimeCycle:
        """Run one strategy cycle selected by the owning System."""
        output = self.dispatcher.process(self.frame, event, hook=hook)
        return RuntimeCycle(
            as_of=event.time,
            event=event,
            dispatched=output is not None,
            hook=hook if output is not None else None,
            output=output,
            views=_view_snapshot(self.views),
        )

    def finish(self) -> RuntimeResult:
        return self.dispatcher.finish(self.frame)

    def stop(self) -> None:
        self.frame.finished = True

__all__ = ["RuntimeSession"]


def _view_snapshot(views: ViewStore) -> ViewStore:
    return ViewStore(views.registry, views.envelopes().values())
