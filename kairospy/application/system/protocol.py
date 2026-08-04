"""Contracts owned by the system control plane."""

from __future__ import annotations

from typing import Protocol

from kairospy.application.support.launch.application.runtime import LaunchRuntimeSession
from kairospy.application.support.messaging import MessageInbox, Message
from kairospy.application.support.runtime.application.interaction import SystemCallResult
from kairospy.application.support.runtime.domain.commands import RuntimeCommand
from kairospy.application.support.runtime.services.orchestration.state import RuntimeCycle


class SystemBusinessRuntime(Protocol):
    """Runtime-facing capabilities of one composed business runtime.

    The control plane receives capabilities, never the business service
    graph.  Implementations may compose any number of usecases behind this
    boundary.  Each method represents a system task delegated to business;
    the control plane does not interpret the task's business meaning.
    """

    def attach(self, *, views: object) -> None: ...

    def message_inbox(self) -> MessageInbox | None: ...

    def bind_runtime(self, runtime: LaunchRuntimeSession) -> None: ...

    @property
    def intents(self) -> object: ...

    async def close(self) -> None: ...

    async def start_actors(self) -> None: ...

    async def stop_actors(self) -> None: ...

    @property
    def has_finite_actors(self) -> bool: ...

    async def wait_for_finite_actors(self) -> None: ...

    def process(self, event: Message) -> tuple[RuntimeCycle, ...]: ...

    async def process_async(self, event: Message) -> tuple[RuntimeCycle, ...]: ...

    def call(self, command: RuntimeCommand) -> SystemCallResult: ...

    def detach(self) -> None: ...


class SystemBusinessFactory(Protocol):
    """Factory contract used by system resources to create its coordinator."""

    def start(self, **kwargs: object) -> SystemBusinessRuntime: ...


__all__ = ["SystemBusinessFactory", "SystemBusinessRuntime"]
