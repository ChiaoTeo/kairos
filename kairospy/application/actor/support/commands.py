from __future__ import annotations

from typing import Protocol

from kairospy.application.support.messaging import Message
from kairospy.application.support.runtime.application.interaction import SystemCallDecision, SystemCallResult
from kairospy.application.support.runtime.domain.commands import CommandHandle, RuntimeCommand, RuntimeCommandStatus


class ActorCommandHandler(Protocol):
    def call(self, command: RuntimeCommand) -> SystemCallResult: ...

    def apply_event(self, event: Message) -> None: ...


class ActorCommandRouter:
    """Route opaque system commands to actors that understand their kinds."""

    def __init__(self) -> None:
        self._handlers: dict[str, ActorCommandHandler] = {}

    def register(self, *kinds: str, handler: ActorCommandHandler) -> None:
        for kind in kinds:
            if not kind.strip():
                raise ValueError("actor command kind is required")
            self._handlers[kind] = handler

    def call(self, command: RuntimeCommand) -> SystemCallResult:
        handler = self._handlers.get(command.kind)
        if handler is None:
            handle = CommandHandle(command.command_id, command.kind)
            handle._reject(f"unsupported runtime command: {command.kind}")
            return SystemCallResult(
                request_id=command.command_id,
                decision=SystemCallDecision.REJECTED,
                handle=handle,
                result=handle.result,
                error=handle.error,
            )
        return handler.call(command)

    def apply_event(self, event: Message) -> None:
        seen: set[int] = set()
        for handler in self._handlers.values():
            marker = id(handler)
            if marker in seen:
                continue
            seen.add(marker)
            handler.apply_event(event)


__all__ = ["ActorCommandHandler", "ActorCommandRouter"]
