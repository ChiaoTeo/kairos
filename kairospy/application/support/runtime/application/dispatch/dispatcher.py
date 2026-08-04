from __future__ import annotations

from typing import Protocol

from kairospy.application.support.runtime.services.orchestration.state import RuntimeFrame, RuntimeResult
from kairospy.application.support.messaging import Message


class RuntimeDispatcherPort(Protocol):
    """Generic runtime callback port implemented by a business usecase."""

    context: object

    def start(self, frame: RuntimeFrame) -> None: ...
    def process(self, frame: RuntimeFrame, event: Message, *, hook: str | None) -> object | None: ...
    def finish(self, frame: RuntimeFrame) -> RuntimeResult: ...


RuntimeDispatcher = RuntimeDispatcherPort

__all__ = ["RuntimeDispatcher", "RuntimeDispatcherPort"]
