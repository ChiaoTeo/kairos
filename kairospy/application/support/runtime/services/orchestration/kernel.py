from __future__ import annotations

from kairospy.application.support.runtime.application.dispatch.dispatcher import RuntimeDispatcherPort
from kairospy.application.support.runtime.services.orchestration.session import RuntimeSession
from kairospy.application.support.runtime.services.orchestration.state import (
    RuntimeFrame,
    RuntimeResult,
    RuntimeStores,
    RuntimeCycle,
    Callback,
)


class RuntimeKernel:
    def __init__(
        self,
        dispatcher_factory: object,
        *,
        program_id: str,
        system_call: object | None = None,
        stores: RuntimeStores | None = None,
        reference: object | None = None,
    ) -> None:
        if not program_id.strip():
            raise ValueError("program_id is required")
        self.program_id = program_id
        self.stores = stores or RuntimeStores()
        self.state = dict(self.stores.program_state)
        self.views = self.stores.views
        self.system_call = system_call
        factory = getattr(dispatcher_factory, "__call__", None)
        if not callable(factory):
            raise TypeError("runtime dispatcher factory must be callable")
        self.dispatcher: RuntimeDispatcherPort = factory(
            state=self.state,
            system_call=self.system_call,
            views=self.views,
            reference=reference,
        )

    def start(self) -> RuntimeSession:
        frame = RuntimeFrame()
        self.dispatcher.start(frame)
        return RuntimeSession(self.dispatcher, self.views, frame, self.system_call)

__all__ = [
    "RuntimeKernel",
    "RuntimeFrame",
    "RuntimeResult",
    "RuntimeStores",
    "RuntimeCycle",
    "RuntimeSession",
    "Callback",
]
