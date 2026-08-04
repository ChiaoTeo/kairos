"""A small in-process message routing table."""

from __future__ import annotations

import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable

from kairospy.application.support.messaging import Message


TopologyHandler = Callable[[Message], object | Awaitable[object]]


class MessageTopology:
    """Route messages to registered handlers in registration order.

    The topology is not another broker or subscription mechanism. It is the
    single-consumer dispatch table run after a host has removed one message
    from the Bus subscription. A route may be an exact topic, ``domain.*``
    or ``*``.
    """

    def __init__(self) -> None:
        self._routes: dict[str, list[TopologyHandler]] = defaultdict(list)

    def register(self, pattern: str, handler: TopologyHandler) -> None:
        if not pattern.strip():
            raise ValueError("message topology pattern is required")
        if not callable(handler):
            raise TypeError("message topology handler must be callable")
        if handler not in self._routes[pattern]:
            self._routes[pattern].append(handler)

    async def dispatch(self, message: Message) -> None:
        for pattern in self._patterns_for(message.topic):
            for handler in tuple(self._routes.get(pattern, ())):
                result = handler(message)
                if inspect.isawaitable(result):
                    await result

    def dispatch_sync(self, message: Message) -> None:
        """Dispatch a synchronous System step without creating a task."""
        for pattern in self._patterns_for(message.topic):
            for handler in tuple(self._routes.get(pattern, ())):
                result = handler(message)
                if inspect.isawaitable(result):
                    raise RuntimeError("async message topology handler requires process_async")

    def handlers(self, pattern: str) -> tuple[TopologyHandler, ...]:
        return tuple(self._routes.get(pattern, ()))

    @staticmethod
    def _patterns_for(topic: str) -> tuple[str, ...]:
        domain = topic.split(".", 1)[0]
        patterns = [topic]
        if domain != topic:
            patterns.append(f"{domain}.*")
        patterns.append("*")
        return tuple(patterns)


__all__ = ["MessageTopology", "TopologyHandler"]
